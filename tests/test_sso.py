"""Single sign-on against a fake OpenID provider: real RSA-signed ID tokens, discovery and JWKS served by a stub."""
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub import sso

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)

ISS = "https://idp.example"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "k1", "use": "sig", "alg": "RS256"}


class FakeIdP:
    def __init__(self):
        self.nonce, self.claims = "", {}

    def get(self, url, **kw):
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISS, "authorization_endpoint": f"{ISS}/auth", "token_endpoint": f"{ISS}/token",
                                             "jwks_uri": f"{ISS}/jwks"}, request=httpx.Request("GET", url))
        raise AssertionError(url)

    def post(self, url, data=None, **kw):
        assert url == f"{ISS}/token" and data["client_secret"] == "shh" and data["code_verifier"]
        now = int(time.time())
        tok = jwt.encode({"iss": ISS, "aud": "k10y", "sub": "u-1", "iat": now, "exp": now + 300, "nonce": self.nonce, **self.claims},
                         KEY, algorithm="RS256", headers={"kid": "k1"})
        return httpx.Response(200, json={"id_token": tok, "access_token": "at"})


@pytest.fixture()
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setattr(sso.httpx, "get", fake.get)
    monkeypatch.setattr(sso.httpx, "post", fake.post)
    monkeypatch.setattr(sso, "_discovery", {})

    class Keys:
        def __init__(self, *a, **k): pass
        def get_signing_key_from_jwt(self, token):
            return jwt.PyJWK(JWK)

    monkeypatch.setattr(jwt, "PyJWKClient", Keys)
    monkeypatch.setattr(sso, "_jwks", {})
    return fake


def sign_in(fake, claims):
    browser = TestClient(hub.app)
    start = browser.get("/api/hub/auth/sso/corp/start", follow_redirects=False)
    assert start.status_code == 302, start.text
    q = parse_qs(urlsplit(start.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["k10y"]
    fake.nonce, fake.claims = q["nonce"][0], claims
    return browser, browser.get(f"/api/hub/auth/sso/callback?code=c&state={q['state'][0]}")


def test_sso_creates_user_maps_groups_and_keeps_manual_roles(client, idp):
    ready_admin(client)
    roles = {r["name"]: r["id"] for r in client.get("/api/hub/roles").json()}
    body = {"id": "corp", "name": "Corp SSO", "issuer": ISS, "client_id": "k10y", "client_secret": "shh",
            "mappings": [{"group": "k8s-ops", "role_id": roles["Kubernetes operator"]}]}
    saved = client.put("/api/hub/settings/sso/corp", json=body, headers=CSRF)
    assert saved.status_code == 200, saved.text
    assert "shh" not in saved.text and saved.json()["secret_set"]
    assert TestClient(hub.app).get("/api/hub/auth/sso").json() == [{"id": "corp", "name": "Corp SSO"}]

    browser, done = sign_in(idp, {"preferred_username": "alice", "name": "Alice", "groups": ["k8s-ops", "other"]})
    assert done.status_code == 200 and "location.replace" in done.text
    me = browser.get("/api/hub/auth/me").json()
    assert me["user"]["username"] == "alice" and me["user"]["must_setup_2fa"] is False
    alice = next(u for u in client.get("/api/hub/users").json() if u["username"] == "alice")
    assert [(a["role"], a["source"]) for a in alice["assignments"]] == [("Kubernetes operator", "sso")]

    # A role given by hand stays; the SSO role follows the groups at the next sign-in.
    client.patch(f"/api/hub/users/{alice['id']}", json={"assignments": [{"role_id": roles["HAProxy viewer"]}]}, headers=CSRF)
    _, again = sign_in(idp, {"preferred_username": "alice", "groups": []})
    assert again.status_code == 200
    alice = next(u for u in client.get("/api/hub/users").json() if u["username"] == "alice")
    assert [(a["role"], a["source"]) for a in alice["assignments"]] == [("HAProxy viewer", "")]


def test_sso_rejects_wrong_state_and_bad_nonce(client, idp):
    ready_admin(client)
    client.put("/api/hub/settings/sso/corp", json={"id": "corp", "name": "Corp", "issuer": ISS, "client_id": "k10y", "client_secret": "shh"},
               headers=CSRF)
    assert TestClient(hub.app).get("/api/hub/auth/sso/callback?code=c&state=forged").status_code == 400
    browser = TestClient(hub.app)
    start = browser.get("/api/hub/auth/sso/corp/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    idp.nonce, idp.claims = "someone-elses", {"preferred_username": "mallory"}
    bad = browser.get(f"/api/hub/auth/sso/callback?code=c&state={state}")
    assert bad.status_code == 400 and "nonce" in bad.text
    assert browser.get("/api/hub/auth/me").status_code == 401
