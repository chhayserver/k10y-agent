"""Single sign-on with OpenID Connect (Google, Microsoft Entra ID, Keycloak, Okta, Authentik, …).

Authorization code flow with PKCE, `state` (bound to the browser by a short cookie) and `nonce`. The ID token's
signature is checked against the provider's published keys, with its issuer, audience and expiry. Users are matched
by "<issuer>|<sub>", then by username, and can be created on first sign-in. Group mappings turn the user's groups
into role assignments; those are replaced at every sign-in, while roles given by hand stay.

Providers live in the Setting "sso": {"providers": [{id, name, issuer, client_id, secret (encrypted), scopes,
username_claim, groups_claim, auto_create, mappings: [{group, role_id, cluster_id, haproxy_id}]}]}.
"""

import base64
import hashlib
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Role, RoleAssignment, Setting, User

STATE_COOKIE = "k10y_sso"
STATE_TTL = 600
_pending: Dict[str, Dict[str, Any]] = {}            # state -> what the callback needs
_discovery: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_jwks: Dict[str, Any] = {}


class Mapping(BaseModel):
    group: str = Field(min_length=1, max_length=256)
    role_id: int
    cluster_id: Optional[int] = None
    haproxy_id: Optional[int] = None


class ProviderBody(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,30}$")
    name: str = Field(min_length=1, max_length=64)                  # the button: "Sign in with <name>"
    issuer: str = Field(pattern=r"^https?://\S+$", max_length=300)   # http only with K10Y_SSO_ALLOW_HTTP=true (tests)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: Optional[str] = Field(None, max_length=500)      # omit to keep the stored one
    scopes: str = Field("openid email profile", max_length=300)
    username_claim: str = Field("preferred_username", max_length=64)
    groups_claim: str = Field("groups", max_length=64)
    auto_create: bool = True
    enabled: bool = True
    mappings: List[Mapping] = Field(default_factory=list, max_length=100)


def load_providers(s: Session) -> List[Dict[str, Any]]:
    row = s.get(Setting, "sso")
    return list(((row.value or {}) if row else {}).get("providers") or [])


def save_providers(s: Session, providers: List[Dict[str, Any]]) -> None:
    row = s.get(Setting, "sso")
    if row:
        row.value = {"providers": providers}
    else:
        s.add(Setting(key="sso", value={"providers": providers}))
    s.commit()


def discover(issuer: str) -> Dict[str, Any]:
    hit = _discovery.get(issuer)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    r = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10)
    r.raise_for_status()
    doc = r.json()
    if doc.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise ValueError(f"the provider says its issuer is {doc.get('issuer')!r}, not {issuer!r}")
    _discovery[issuer] = (time.time(), doc)
    return doc


def verify_id_token(id_token: str, issuer: str, client_id: str, nonce: str, jwks_uri: str) -> Dict[str, Any]:
    import jwt
    client = _jwks.get(jwks_uri) or _jwks.setdefault(jwks_uri, jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600))
    key = client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(id_token, key.key, algorithms=["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"],
                        audience=client_id, issuer=issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=60)
    if not nonce or claims.get("nonce") != nonce:
        raise ValueError("the sign-in response doesn't match this browser (nonce)")
    return claims


def claim(claims: Dict[str, Any], path: str) -> Any:
    """A claim by name, also dotted (e.g. realm_access.roles for Keycloak)."""
    value: Any = claims
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def apply_mappings(s: Session, user: User, groups: List[str], mappings: List[Dict[str, Any]]) -> List[str]:
    """Replace the user's SSO role assignments with those their groups map to. Returns the role names given."""
    wanted = {(m["role_id"], m.get("cluster_id"), m.get("haproxy_id")) for m in mappings if m["group"] in groups}
    for a in list(user.assignments):
        if a.source == "sso":
            user.assignments.remove(a)
    s.flush()
    manual = {(a.role_id, a.cluster_id, a.haproxy_id) for a in user.assignments}
    given = []
    for role_id, cluster_id, haproxy_id in sorted(wanted, key=str):
        role = s.get(Role, role_id)
        if role is None or (role_id, cluster_id, haproxy_id) in manual:
            continue
        user.assignments.append(RoleAssignment(role=role, cluster_id=cluster_id, haproxy_id=haproxy_id, source="sso"))
        given.append(role.name)
    return given


def _pkce() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _page(text: str, status: int = 400) -> HTMLResponse:
    from html import escape
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>k10y sign-in</title><body style='font:15px system-ui;margin:48px'>"
                        f"<p>{escape(text)}</p><p><a href='/login'>Back to sign-in</a></p>", status_code=status)


def build_router(db_session, require, audit, crypto, start_session: Callable, public_url: Callable[[], str]) -> APIRouter:
    r = APIRouter(prefix="/api/hub")

    def provider_view(p: Dict[str, Any]) -> dict:
        return {k: v for k, v in p.items() if k != "secret"} | {"secret_set": bool(p.get("secret"))}

    def callback_url(request: Request) -> str:
        base = (public_url() or str(request.base_url)).rstrip("/")
        return f"{base}/api/hub/auth/sso/callback"

    # --- settings (settings.manage) ---------------------------------------------------
    @r.get("/settings/sso")
    def get_sso(request: Request, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        return {"providers": [provider_view(x) for x in load_providers(s)], "callback_url": callback_url(request)}

    @r.put("/settings/sso/{pid}")
    def put_provider(pid: str, body: ProviderBody, request: Request, p=Depends(require("settings.manage")),
                     s: Session = Depends(db_session)) -> dict:
        import os
        if body.id != pid:
            raise HTTPException(status_code=400, detail="id mismatch")
        if body.issuer.startswith("http://") and os.environ.get("K10Y_SSO_ALLOW_HTTP", "").lower() != "true":
            raise HTTPException(status_code=400, detail="The issuer must use https://.")
        providers = load_providers(s)
        old = next((x for x in providers if x["id"] == pid), None)
        if not body.client_secret and not (old and old.get("secret")):
            raise HTTPException(status_code=400, detail="The client secret is required.")
        for m in body.mappings:
            if s.get(Role, m.role_id) is None:
                raise HTTPException(status_code=400, detail=f"unknown role in the mapping for {m.group}")
        try:
            discover(body.issuer)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Can't read the provider's OpenID configuration at {body.issuer}: {e}")
        item = body.model_dump(exclude={"client_secret"})
        item["secret"] = crypto().encrypt(body.client_secret) if body.client_secret else old["secret"]
        save_providers(s, [x for x in providers if x["id"] != pid] + [item])
        audit(s, request, p, "sso-provider-save", body.name, params={"issuer": body.issuer, "mappings": len(body.mappings),
                                                                       "secret_changed": bool(body.client_secret)})
        return provider_view(item)

    @r.delete("/settings/sso/{pid}")
    def delete_provider(pid: str, request: Request, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        providers = load_providers(s)
        if not any(x["id"] == pid for x in providers):
            raise HTTPException(status_code=404, detail="provider not found")
        save_providers(s, [x for x in providers if x["id"] != pid])
        audit(s, request, p, "sso-provider-delete", pid)
        return {"ok": True}

    # --- sign-in ------------------------------------------------------------------------
    @r.get("/auth/sso")
    def sign_in_options(s: Session = Depends(db_session)) -> list:
        """Public: the buttons on the sign-in page."""
        return [{"id": x["id"], "name": x["name"]} for x in load_providers(s) if x.get("enabled", True)]

    @r.get("/auth/sso/{pid}/start")
    def start(pid: str, request: Request, s: Session = Depends(db_session)):
        prov = next((x for x in load_providers(s) if x["id"] == pid and x.get("enabled", True)), None)
        if prov is None:
            return _page("This sign-in option doesn't exist.", 404)
        try:
            doc = discover(prov["issuer"])
        except Exception as e:
            return _page(f"Can't reach {prov['name']}: {e}", 502)
        now = time.time()
        for k in [k for k, v in _pending.items() if v["t"] < now - STATE_TTL]:
            del _pending[k]
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = _pkce()
        _pending[state] = {"t": now, "pid": pid, "nonce": nonce, "verifier": verifier, "redirect": callback_url(request)}
        url = doc["authorization_endpoint"] + "?" + urlencode({
            "response_type": "code", "client_id": prov["client_id"], "redirect_uri": callback_url(request),
            "scope": prov.get("scopes") or "openid email profile", "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        resp = RedirectResponse(url, status_code=302)
        # Lax: the browser sends it back on the provider's top-level redirect to the callback, and nowhere else.
        resp.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/api/hub/auth/sso")
        return resp

    @r.get("/auth/sso/callback")
    def callback(request: Request, s: Session = Depends(db_session), code: str = "", state: str = "", error: str = "",
                 error_description: str = ""):
        if error:
            return _page(f"The provider didn't sign you in: {error_description or error}.")
        pending = _pending.pop(state, None)
        if not pending or request.cookies.get(STATE_COOKIE) != state or pending["t"] < time.time() - STATE_TTL:
            return _page("This sign-in link has expired or was started in another browser. Please try again.")
        prov = next((x for x in load_providers(s) if x["id"] == pending["pid"]), None)
        if prov is None or not code:
            return _page("Sign-in failed. Please try again.")
        try:
            doc = discover(prov["issuer"])
            tok = httpx.post(doc["token_endpoint"], timeout=15, headers={"Accept": "application/json"}, data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect"],
                "client_id": prov["client_id"], "client_secret": crypto().decrypt(prov["secret"]),
                "code_verifier": pending["verifier"]})
            if tok.status_code >= 400:
                raise ValueError(f"token endpoint answered {tok.status_code}: {tok.text[:200]}")
            tokens = tok.json()
            claims = verify_id_token(tokens.get("id_token") or "", doc["issuer"], prov["client_id"], pending["nonce"], doc["jwks_uri"])
            if claim(claims, prov.get("groups_claim") or "groups") is None and doc.get("userinfo_endpoint") and tokens.get("access_token"):
                info = httpx.get(doc["userinfo_endpoint"], headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=10)
                if info.status_code == 200 and info.json().get("sub") == claims["sub"]:
                    claims = {**info.json(), **claims}
        except Exception as e:
            audit(s, request, None, "login", prov["name"], ok=False, message=f"single sign-on failed: {e}"[:500], username="(sso)")
            return _page(f"Sign-in with {prov['name']} failed: {e}")

        subject = f"{claims['iss']}|{claims['sub']}"
        raw_name = str(claim(claims, prov.get("username_claim") or "preferred_username") or claims.get("email") or claims["sub"])
        username = "".join(ch for ch in raw_name if ch.isalnum() or ch in "._@-")[:64]
        groups_raw = claim(claims, prov.get("groups_claim") or "groups") or []
        groups = [str(g) for g in (groups_raw if isinstance(groups_raw, list) else [groups_raw])]
        user = s.execute(select(User).where(User.sso_subject == subject)).scalar_one_or_none()
        if user is None and username:
            user = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is not None and user.sso_subject and user.sso_subject != subject:
                return _page(f"The k10y account {username} belongs to another sign-in. Ask an administrator.", 403)
        if user is None:
            if not prov.get("auto_create", True):
                audit(s, request, None, "login", username, ok=False, message=f"no k10y account for this {prov['name']} user", username=username)
                return _page(f"There's no k10y account for {username}. Ask an administrator to add you.", 403)
            from .security import hash_password
            user = User(username=username, display_name=str(claims.get("name") or username)[:128],
                        password_hash=hash_password(secrets.token_urlsafe(32)), must_change_password=False)
            s.add(user)
            s.flush()
        if not user.is_active:
            return _page("Your k10y account is disabled.", 403)
        user.sso_provider, user.sso_subject = prov["id"], subject
        given = apply_mappings(s, user, groups, prov.get("mappings") or [])
        s.commit()
        resp = HTMLResponse("<!doctype html><meta charset=utf-8><title>k10y</title><script>location.replace('/')</script>"
                            "<noscript><a href='/'>Continue</a></noscript>")
        start_session(s, request, resp, user)
        resp.delete_cookie(STATE_COOKIE, path="/api/hub/auth/sso")
        audit(s, request, None, "login", user.username, username=user.username,
              message=f"signed in with {prov['name']}" + (f"; roles from groups: {', '.join(given)}" if given else ""))
        return resp

    return r
