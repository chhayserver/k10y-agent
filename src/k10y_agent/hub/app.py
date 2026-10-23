"""k10y hub: login, users, custom roles, clusters, audit and AI, backed by PostgreSQL.

The browser only talks to the hub. The hub holds each agent's token (encrypted) and
proxies API calls to the agents over HTTPS, checking the user's permissions first.
"""

import asyncio
import json
import logging
import ssl
import time
import secrets as pysecrets
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote, urlsplit

import httpx
from websockets.asyncio.client import connect as ws_connect
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import __version__, ai
from . import alerts, haproxy, haproxy_monitor
from ..config import Settings
from .db import (AuditEntry, Cluster, Database, Role, RoleAssignment, SessionRow, User, UserPreference, get_setting,
                 utcnow)
from .permissions import ALL_PERMISSIONS, BUILTIN_ROLES, CLUSTER_PERMISSIONS, GLOBAL_PERMISSIONS, required_permission
from .security import (Crypto, LoginLimiter, check_password_policy, hash_password, new_session_token, token_digest,
                       verify_password)

log = logging.getLogger("k10y.hub")
settings = Settings()
COOKIE = "k10y_session"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
AGENT_TIMEOUT = httpx.Timeout(30.0, connect=8.0)

app = FastAPI(title="k10y hub", version=__version__)
api = APIRouter(prefix="/api/hub")
state: Dict[str, Any] = {}
limiter = LoginLimiter()


# --- startup --------------------------------------------------------------------------

def init(database_url: Optional[str] = None, secret: Optional[str] = None, data_dir: Optional[str] = None) -> None:
    """Create tables, built-in roles and the first admin. Safe to call on every start."""
    url = database_url or settings.database_url
    if not url:
        raise RuntimeError("K10Y_DATABASE_URL is required in hub mode (e.g. postgresql://k10y:pw@postgres/k10y)")
    db = Database(url)
    db.create_all()
    state["db"] = db
    state["crypto"] = Crypto(secret if secret is not None else settings.secret_key, data_dir or settings.data_dir)
    with db.session() as s:
        for name, desc, perms in BUILTIN_ROLES:
            role = s.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
            if role is None:
                s.add(Role(name=name, description=desc, permissions=perms, builtin=True))
            elif name == "Admin":
                role.permissions = sorted(ALL_PERMISSIONS)  # Admin always has every permission
        s.commit()
        if not s.execute(select(func.count(User.id))).scalar():
            password = settings.admin_password or pysecrets.token_urlsafe(12)
            admin_role = s.execute(select(Role).where(Role.name == "Admin")).scalar_one()
            user = User(username=settings.admin_user, display_name="Administrator", password_hash=hash_password(password),
                        must_change_password=True)
            user.assignments.append(RoleAssignment(role=admin_role, cluster_id=None))
            s.add(user)
            s.commit()
            if settings.admin_password:
                log.warning("created admin user %r from K10Y_ADMIN_PASSWORD (change it after first login)", settings.admin_user)
            else:
                log.warning("=" * 70)
                log.warning("created admin user %r with password: %s", settings.admin_user, password)
                log.warning("you must change it at first login")
                log.warning("=" * 70)


@app.on_event("startup")
def _startup() -> None:
    if "db" not in state:
        init()
    if settings.alerts_enabled:
        alert_engine().start()
    haproxy_mon().start()
    if settings.syslog_port:
        try:
            rx = haproxy_monitor.SyslogReceiver(settings.syslog_port)
            rx.start()
            state["syslog"] = rx
        except OSError as e:
            log.warning("HAProxy log receiver disabled: cannot listen on udp/%s: %s", settings.syslog_port, e)


@app.on_event("shutdown")
def _shutdown() -> None:
    if "engine" in state:
        state["engine"].stop()
    if "haproxy_monitor" in state:
        state["haproxy_monitor"].stop()


def db_session():
    with state["db"].session() as s:
        yield s


def crypto() -> Crypto:
    return state["crypto"]


# --- auth -----------------------------------------------------------------------------

class Principal:
    def __init__(self, user: User, grants: Dict[Optional[int], Set[str]], session_id: Optional[int] = None):
        self.user = user
        self.grants = grants  # cluster_id (None = all clusters) -> permissions
        self.session_id = session_id

    def global_perms(self) -> Set[str]:
        return set(self.grants.get(None, set()))

    def cluster_perms(self, cluster_id: int) -> Set[str]:
        return {p for p in self.grants.get(None, set()) | self.grants.get(cluster_id, set()) if p in CLUSTER_PERMISSIONS}

    def can(self, perm: str, cluster_id: Optional[int] = None) -> bool:
        if perm in GLOBAL_PERMISSIONS or cluster_id is None:
            return perm in self.global_perms()
        return perm in self.cluster_perms(cluster_id)

    def visible_cluster_ids(self, all_ids: List[int]) -> List[int]:
        return [cid for cid in all_ids if "cluster.view" in self.cluster_perms(cid)]


def load_grants(s: Session, user: User) -> Dict[Optional[int], Set[str]]:
    grants: Dict[Optional[int], Set[str]] = {}
    for a in s.execute(select(RoleAssignment).where(RoleAssignment.user_id == user.id)).scalars():
        grants.setdefault(a.cluster_id, set()).update(a.role.permissions or [])
    return grants


def authenticate(s: Session, token: Optional[str]) -> Principal:
    """Session cookie -> Principal. Shared by HTTP routes and the pod-shell WebSocket."""
    if not token:
        raise HTTPException(status_code=401, detail="not logged in")
    row = s.execute(select(SessionRow).where(SessionRow.token_hash == token_digest(token))).scalar_one_or_none()
    now = utcnow()
    if row is None or _aware(row.expires_at) < now:
        raise HTTPException(status_code=401, detail="session expired")
    user = s.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="account disabled")
    # Sliding expiry and "last active", written at most once a minute.
    new_exp = now + timedelta(hours=settings.session_hours)
    if (new_exp - _aware(row.expires_at)).total_seconds() > 60:
        row.expires_at = new_exp
        row.last_seen_at = now
        s.commit()
    return Principal(user, load_grants(s, user), row.id)


def current(request: Request, s: Session = Depends(db_session)) -> Principal:
    p = authenticate(s, request.cookies.get(COOKIE))
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-k10y-csrf") != "1":
        raise HTTPException(status_code=403, detail="missing CSRF header")
    if p.user.must_change_password and request.url.path not in ("/api/hub/auth/me", "/api/hub/auth/password",
                                                                "/api/hub/auth/logout"):
        raise HTTPException(status_code=403, detail="password change required")
    return p


def _aware(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def require(perm: str):
    def dep(p: Principal = Depends(current)) -> Principal:
        if not p.can(perm):
            raise HTTPException(status_code=403, detail=f"missing permission: {perm}")
        return p
    return dep


def audit(s: Session, request: Request, p: Optional[Principal], action: str, target: str = "", *, ok: bool = True,
          message: str = "", params: Optional[dict] = None, cluster: Optional[Cluster] = None,
          dry_run: bool = False, username: str = "") -> None:
    s.add(AuditEntry(user_id=p.user.id if p else None, username=p.user.username if p else username,
                     cluster_id=cluster.id if cluster else None, cluster_name=cluster.name if cluster else "",
                     action=action, target=target, params=params or {}, dry_run=dry_run, ok=ok, message=message[:2000],
                     client_ip=request.client.host if request.client else ""))
    s.commit()


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def me_payload(s: Session, p: Principal) -> dict:
    clusters = s.execute(select(Cluster).order_by(Cluster.name)).scalars().all()
    return {
        "user": {"id": p.user.id, "username": p.user.username, "display_name": p.user.display_name,
                 "must_change_password": p.user.must_change_password},
        "global_permissions": sorted(p.global_perms()),
        "clusters": [{"id": c.id, "name": c.name, "permissions": sorted(p.cluster_perms(c.id))}
                     for c in clusters if "cluster.view" in p.cluster_perms(c.id)],
        "version": __version__,
    }


@api.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response, s: Session = Depends(db_session)) -> dict:
    ip = request.client.host if request.client else ""
    if limiter.blocked(body.username, ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Wait a few minutes and try again.")
    user = s.execute(select(User).where(func.lower(User.username) == body.username.lower())).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        limiter.failed(body.username, ip)
        audit(s, request, None, "login", body.username, ok=False, message="invalid username or password",
              username=body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    limiter.reset(body.username, ip)
    token = new_session_token()
    s.add(SessionRow(token_hash=token_digest(token), user_id=user.id,
                     expires_at=utcnow() + timedelta(hours=settings.session_hours),
                     ip=ip, user_agent=(request.headers.get("user-agent") or "")[:255], last_seen_at=utcnow()))
    user.last_login_at = utcnow()
    s.execute(delete(SessionRow).where(SessionRow.expires_at < utcnow()))
    s.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=request.url.scheme == "https", samesite="strict",
                        max_age=settings.session_hours * 3600, path="/")
    p = Principal(user, load_grants(s, user))
    audit(s, request, p, "login", user.username, message="logged in")
    return me_payload(s, p)


@api.post("/auth/logout")
def logout(request: Request, response: Response, p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    token = request.cookies.get(COOKIE, "")
    s.execute(delete(SessionRow).where(SessionRow.token_hash == token_digest(token)))
    s.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@api.get("/auth/me")
def me(p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    return me_payload(s, p)


@api.post("/auth/password")
def change_password(body: PasswordBody, request: Request, p: Principal = Depends(current),
                    s: Session = Depends(db_session)) -> dict:
    user = s.get(User, p.user.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    problem = check_password_policy(body.new_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Choose a different password.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out every other session.
    token = request.cookies.get(COOKIE, "")
    s.execute(delete(SessionRow).where(SessionRow.user_id == user.id, SessionRow.token_hash != token_digest(token)))
    s.commit()
    audit(s, request, p, "password-change", user.username)
    return me_payload(s, Principal(user, p.grants))


# --- personal preferences and sessions ------------------------------------------------

MAX_PREFS_BYTES = 16_000


@api.get("/auth/preferences")
def get_preferences(p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    row = s.get(UserPreference, p.user.id)
    return {"data": row.data if row else None}


@api.put("/auth/preferences")
async def put_preferences(request: Request, p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    raw = await request.body()
    if len(raw) > MAX_PREFS_BYTES:
        raise HTTPException(status_code=413, detail="preferences too large")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="preferences must be an object")
    row = s.get(UserPreference, p.user.id)
    if row is None:
        s.add(UserPreference(user_id=p.user.id, data=data))
    else:
        row.data = data
    s.commit()
    return {"ok": True}


def session_view(r: SessionRow, current_id: Optional[int]) -> dict:
    iso = lambda d: _aware(d).isoformat() if d else None
    return {"id": r.id, "created_at": iso(r.created_at), "last_seen_at": iso(r.last_seen_at or r.created_at),
            "expires_at": iso(r.expires_at), "ip": r.ip, "user_agent": r.user_agent, "current": r.id == current_id}


def _live_sessions(s: Session, user_id: int) -> List[SessionRow]:
    return [r for r in s.execute(select(SessionRow).where(SessionRow.user_id == user_id)
                                 .order_by(SessionRow.created_at.desc())).scalars() if _aware(r.expires_at) > utcnow()]


@api.get("/auth/sessions")
def my_sessions(p: Principal = Depends(current), s: Session = Depends(db_session)) -> list:
    return [session_view(r, p.session_id) for r in _live_sessions(s, p.user.id)]


@api.delete("/auth/sessions/{session_id}")
def end_my_session(session_id: int, request: Request, p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    row = s.get(SessionRow, session_id)
    if row is None or row.user_id != p.user.id:
        raise HTTPException(status_code=404, detail="session not found")
    s.delete(row)
    s.commit()
    audit(s, request, p, "session-revoke", p.user.username, message=f"signed out {row.ip or 'a device'}")
    return {"ok": True}


@api.post("/auth/sessions/revoke-others")
def end_other_sessions(request: Request, p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    n = s.execute(delete(SessionRow).where(SessionRow.user_id == p.user.id, SessionRow.id != p.session_id)).rowcount
    s.commit()
    audit(s, request, p, "session-revoke", p.user.username, message=f"signed out {n} other session(s)")
    return {"ok": True, "revoked": n}


# --- users and roles ------------------------------------------------------------------

class AssignmentBody(BaseModel):
    role_id: int
    cluster_id: Optional[int] = None


class UserBody(BaseModel):
    username: Optional[str] = Field(None, min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: Optional[str] = Field(None, max_length=128)
    password: Optional[str] = Field(None, max_length=256)
    is_active: Optional[bool] = None
    must_change_password: Optional[bool] = None
    assignments: Optional[List[AssignmentBody]] = None


class RoleBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=255)
    permissions: List[str]


def user_view(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "is_active": u.is_active,
            "must_change_password": u.must_change_password,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "sessions": getattr(u, "live_sessions", None), "last_seen_at": getattr(u, "last_seen_at", None),
            "assignments": [{"role_id": a.role_id, "role": a.role.name, "cluster_id": a.cluster_id} for a in u.assignments]}


def _admins_left(s: Session, excluding_user: Optional[int] = None) -> int:
    count = 0
    for u in s.execute(select(User).where(User.is_active.is_(True))).scalars():
        if u.id == excluding_user:
            continue
        if any(a.cluster_id is None and "users.manage" in (a.role.permissions or []) for a in u.assignments):
            count += 1
    return count


def _set_assignments(s: Session, user: User, items: List[AssignmentBody]) -> None:
    user.assignments.clear()
    s.flush()
    seen = set()
    for a in items:
        if (a.role_id, a.cluster_id) in seen:
            continue
        seen.add((a.role_id, a.cluster_id))
        role = s.get(Role, a.role_id)
        if role is None:
            raise HTTPException(status_code=400, detail=f"unknown role {a.role_id}")
        if a.cluster_id is not None and s.get(Cluster, a.cluster_id) is None:
            raise HTTPException(status_code=400, detail=f"unknown cluster {a.cluster_id}")
        user.assignments.append(RoleAssignment(role=role, cluster_id=a.cluster_id))


@api.get("/users")
def list_users(p: Principal = Depends(require("users.manage")), s: Session = Depends(db_session)) -> list:
    now, live = utcnow(), {}
    for r in s.execute(select(SessionRow)).scalars():
        if _aware(r.expires_at) > now:
            seen = _aware(r.last_seen_at or r.created_at)
            n, last = live.get(r.user_id, (0, None))
            live[r.user_id] = (n + 1, max(last, seen) if last else seen)
    out = []
    for u in s.execute(select(User).order_by(User.username)).scalars():
        u.live_sessions, seen = live.get(u.id, (0, None))
        u.last_seen_at = seen.isoformat() if seen else None
        out.append(user_view(u))
    return out


@api.post("/users")
def create_user(body: UserBody, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="username and password are required")
    problem = check_password_policy(body.password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if s.execute(select(User).where(func.lower(User.username) == body.username.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A user with that username already exists.")
    user = User(username=body.username, display_name=body.display_name or "", password_hash=hash_password(body.password),
                is_active=body.is_active if body.is_active is not None else True,
                must_change_password=body.must_change_password if body.must_change_password is not None else True)
    s.add(user)
    _set_assignments(s, user, body.assignments or [])
    s.commit()
    audit(s, request, p, "user-create", user.username, params={"roles": [a.role.name for a in user.assignments]})
    return user_view(user)


@api.patch("/users/{user_id}")
def update_user(user_id: int, body: UserBody, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    changes = []
    if body.display_name is not None:
        user.display_name = body.display_name
        changes.append("name")
    if body.password:
        problem = check_password_policy(body.password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        user.password_hash = hash_password(body.password)
        user.must_change_password = True if body.must_change_password is None else body.must_change_password
        s.execute(delete(SessionRow).where(SessionRow.user_id == user.id))
        changes.append("password reset")
    elif body.must_change_password is not None:
        user.must_change_password = body.must_change_password
    if body.is_active is not None and body.is_active != user.is_active:
        if not body.is_active and user.id == p.user.id:
            raise HTTPException(status_code=400, detail="You cannot disable your own account.")
        user.is_active = body.is_active
        if not body.is_active:
            s.execute(delete(SessionRow).where(SessionRow.user_id == user.id))
        changes.append("enabled" if body.is_active else "disabled")
    if body.assignments is not None:
        _set_assignments(s, user, body.assignments)
        changes.append("roles")
    s.flush()
    if _admins_left(s) == 0:
        s.rollback()
        raise HTTPException(status_code=400, detail="At least one active user must keep user management on all clusters.")
    s.commit()
    audit(s, request, p, "user-update", user.username, params={"changes": changes})
    return user_view(user)


@api.get("/users/{user_id}/sessions")
def user_sessions(user_id: int, p: Principal = Depends(require("users.manage")), s: Session = Depends(db_session)) -> list:
    if s.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    return [session_view(r, p.session_id) for r in _live_sessions(s, user_id)]


@api.post("/users/{user_id}/sessions/revoke")
def revoke_user_sessions(user_id: int, request: Request, p: Principal = Depends(require("users.manage")),
                         s: Session = Depends(db_session)) -> dict:
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    q = delete(SessionRow).where(SessionRow.user_id == user_id)
    if user_id == p.user.id:
        q = q.where(SessionRow.id != p.session_id)  # keep the admin's own current session
    n = s.execute(q).rowcount
    s.commit()
    audit(s, request, p, "session-revoke", user.username, message=f"signed out {n} session(s)")
    return {"ok": True, "revoked": n}


@api.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    user = s.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.id == p.user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if _admins_left(s, excluding_user=user.id) == 0:
        raise HTTPException(status_code=400, detail="This is the last user who can manage users.")
    name = user.username
    s.delete(user)
    s.commit()
    audit(s, request, p, "user-delete", name)
    return {"ok": True}


@api.get("/permissions")
def permission_catalog(p: Principal = Depends(current)) -> dict:
    return {"cluster": CLUSTER_PERMISSIONS, "global": GLOBAL_PERMISSIONS}


def role_view(r: Role, s: Session) -> dict:
    users = s.execute(select(func.count(RoleAssignment.id)).where(RoleAssignment.role_id == r.id)).scalar()
    return {"id": r.id, "name": r.name, "description": r.description, "permissions": sorted(r.permissions or []),
            "builtin": r.builtin, "locked": r.name == "Admin" and r.builtin, "assignments": users}


@api.get("/roles")
def list_roles(p: Principal = Depends(current), s: Session = Depends(db_session)) -> list:
    return [role_view(r, s) for r in s.execute(select(Role).order_by(Role.name)).scalars()]


def _check_perms(perms: List[str]) -> List[str]:
    unknown = [x for x in perms if x not in ALL_PERMISSIONS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown permissions: {', '.join(unknown)}")
    return sorted(set(perms))


@api.post("/roles")
def create_role(body: RoleBody, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    if s.execute(select(Role).where(func.lower(Role.name) == body.name.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A role with that name already exists.")
    role = Role(name=body.name, description=body.description, permissions=_check_perms(body.permissions))
    s.add(role)
    s.commit()
    audit(s, request, p, "role-create", role.name, params={"permissions": role.permissions})
    return role_view(role, s)


@api.patch("/roles/{role_id}")
def update_role(role_id: int, body: RoleBody, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    role = s.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    if role.builtin and role.name == "Admin":
        raise HTTPException(status_code=400, detail="The built-in Admin role cannot be changed.")
    other = s.execute(select(Role).where(func.lower(Role.name) == body.name.lower(), Role.id != role.id)).scalar_one_or_none()
    if other:
        raise HTTPException(status_code=409, detail="A role with that name already exists.")
    role.name, role.description, role.permissions = body.name, body.description, _check_perms(body.permissions)
    s.flush()
    if _admins_left(s) == 0:
        s.rollback()
        raise HTTPException(status_code=400, detail="This change would leave nobody able to manage users.")
    s.commit()
    audit(s, request, p, "role-update", role.name, params={"permissions": role.permissions})
    return role_view(role, s)


@api.delete("/roles/{role_id}")
def delete_role(role_id: int, request: Request, p: Principal = Depends(require("users.manage")),
                s: Session = Depends(db_session)) -> dict:
    role = s.get(Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="role not found")
    if role.builtin and role.name == "Admin":
        raise HTTPException(status_code=400, detail="The built-in Admin role cannot be deleted.")
    name = role.name
    s.delete(role)
    s.flush()
    if _admins_left(s) == 0:
        s.rollback()
        raise HTTPException(status_code=400, detail="This would leave nobody able to manage users.")
    s.commit()
    audit(s, request, p, "role-delete", name)
    return {"ok": True}


# --- clusters -------------------------------------------------------------------------

class ClusterBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=8, max_length=300, pattern=r"^https?://")
    token: Optional[str] = Field(None, max_length=512, description="Omit to keep the stored token.")
    verify_tls: bool = False


class ClusterTestBody(BaseModel):
    url: str = Field(min_length=8, max_length=300, pattern=r"^https?://")
    token: Optional[str] = Field(None, max_length=512)
    verify_tls: bool = False
    cluster_id: Optional[int] = None


def cluster_view(c: Cluster) -> dict:
    return {"id": c.id, "name": c.name, "url": c.url, "verify_tls": c.verify_tls,
            "created_at": c.created_at.isoformat() if c.created_at else None}


def agent_client(c: Cluster) -> httpx.Client:
    return httpx.Client(base_url=c.url.rstrip("/") + "/api/v1", verify=c.verify_tls, timeout=AGENT_TIMEOUT,
                        headers={"Authorization": f"Bearer {crypto().decrypt(c.token_encrypted)}"})


def _get_cluster(s: Session, cluster_id: int, p: Principal, perm: str = "cluster.view") -> Cluster:
    c = s.get(Cluster, cluster_id)
    if c is None or not p.can("cluster.view", cluster_id):
        raise HTTPException(status_code=404, detail="cluster not found")
    if not p.can(perm, cluster_id):
        raise HTTPException(status_code=403, detail=f"missing permission: {perm}")
    return c


@api.get("/clusters")
def list_clusters(p: Principal = Depends(current), s: Session = Depends(db_session)) -> list:
    out = []
    for c in s.execute(select(Cluster).order_by(Cluster.name)).scalars():
        if p.can("cluster.view", c.id) or p.can("clusters.manage"):
            out.append({**cluster_view(c), "permissions": sorted(p.cluster_perms(c.id))})
    return out


@api.post("/clusters/test")
def test_cluster(body: ClusterTestBody, p: Principal = Depends(require("clusters.manage")),
                 s: Session = Depends(db_session)) -> dict:
    token = body.token
    if not token and body.cluster_id:
        c = s.get(Cluster, body.cluster_id)
        token = crypto().decrypt(c.token_encrypted) if c else ""
    try:
        r = httpx.get(body.url.rstrip("/") + "/api/v1/info", headers={"Authorization": f"Bearer {token or ''}"},
                      verify=body.verify_tls, timeout=httpx.Timeout(10.0, connect=6.0))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach the agent: {e}")
    if r.status_code == 401:
        raise HTTPException(status_code=400, detail="The agent rejected this token.")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Agent answered HTTP {r.status_code}")
    return r.json()


@api.post("/clusters")
def create_cluster(body: ClusterBody, request: Request, p: Principal = Depends(require("clusters.manage")),
                   s: Session = Depends(db_session)) -> dict:
    if not body.token:
        raise HTTPException(status_code=400, detail="token is required")
    if s.execute(select(Cluster).where(func.lower(Cluster.name) == body.name.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A cluster with that name already exists.")
    c = Cluster(name=body.name, url=body.url.rstrip("/"), token_encrypted=crypto().encrypt(body.token),
                verify_tls=body.verify_tls)
    s.add(c)
    s.commit()
    audit(s, request, p, "cluster-create", c.name, params={"url": c.url}, cluster=c)
    return cluster_view(c)


@api.patch("/clusters/{cluster_id}")
def update_cluster(cluster_id: int, body: ClusterBody, request: Request, p: Principal = Depends(require("clusters.manage")),
                   s: Session = Depends(db_session)) -> dict:
    c = s.get(Cluster, cluster_id)
    if c is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    c.name, c.url, c.verify_tls = body.name, body.url.rstrip("/"), body.verify_tls
    if body.token:
        c.token_encrypted = crypto().encrypt(body.token)
    s.commit()
    audit(s, request, p, "cluster-update", c.name, params={"url": c.url, "token_changed": bool(body.token)}, cluster=c)
    return cluster_view(c)


@api.delete("/clusters/{cluster_id}")
def delete_cluster(cluster_id: int, request: Request, p: Principal = Depends(require("clusters.manage")),
                   s: Session = Depends(db_session)) -> dict:
    c = s.get(Cluster, cluster_id)
    if c is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    name = c.name
    s.delete(c)
    s.commit()
    audit(s, request, p, "cluster-delete", name)
    return {"ok": True}


# --- proxy to agents ------------------------------------------------------------------

def _describe_result(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return r.text[:300]
    if isinstance(d, dict):
        return str(d.get("message") or d.get("detail") or "")[:2000]
    return 


@api.api_route("/clusters/{cluster_id}/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(cluster_id: int, path: str, request: Request, p: Principal = Depends(current),
                s: Session = Depends(db_session)) -> Response:
    perm = required_permission(request.method, path)
    if perm is None:
        raise HTTPException(status_code=403, detail="This action is not available through the hub.")
    c = _get_cluster(s, cluster_id, p, perm)
    if path == "resource" and request.query_params.get("kind") == "Secret" and not p.can("secrets.view", cluster_id):
        raise HTTPException(status_code=403, detail="missing permission: secrets.view")
    body = await request.body()
    headers = {"Authorization": f"Bearer {crypto().decrypt(c.token_encrypted)}",
               "X-K10Y-Actor": f"hub:{p.user.username}"[:64]}
    if body:
        headers["Content-Type"] = "application/json"
    url = f"{c.url.rstrip('/')}/api/v1/{path}"
    def record(ok: bool, message: str) -> None:
        # Every control attempt is audited with the real user, including ones that never reached the agent.
        if request.method == "GET":
            return
        dry = request.query_params.get("dry_run") == "true"
        params: dict = {}
        if body:
            try:
                parsed = json.loads(body)
                dry = dry or bool(parsed.get("dry_run"))
                params = {k: v for k, v in parsed.items() if k not in ("yaml", "dry_run")}
                if "yaml" in parsed:
                    params["yaml_bytes"] = len(parsed["yaml"])
            except (ValueError, AttributeError):
                pass
        target = f"{request.method} {path}"
        if request.method == "PUT" and path == "resource" and body:
            try:
                b = json.loads(body)
                target = f"{b.get('kind', '')} {b['namespace'] + '/' if b.get('namespace') else ''}{b.get('name', '')}"
            except (ValueError, AttributeError, TypeError):
                pass
        audit(s, request, p, {"POST": path.split("/")[-1], "PUT": "edit"}.get(request.method, "delete"),
              target, ok=ok, message=message, params=params, cluster=c, dry_run=dry)

    if request.method == "GET" and path.endswith("/logs/stream"):
        return await _proxy_stream(c, url, request, headers)
    try:
        async with httpx.AsyncClient(verify=c.verify_tls, timeout=httpx.Timeout(120.0, connect=8.0)) as client:
            r = await client.request(request.method, url, params=request.query_params, content=body, headers=headers)
    except httpx.HTTPError as e:
        record(False, f"cannot reach agent: {e}")
        return JSONResponse(status_code=502, content={"detail": f"Cannot reach agent {c.name}: {e}"})
    if request.method != "GET":
        record(r.status_code < 400, _describe_result(r))
    passthrough = {k: v for k, v in r.headers.items() if k.lower() in ("content-type",)}
    return Response(content=r.content, status_code=r.status_code, headers=passthrough)


async def _proxy_stream(c: Cluster, url: str, request: Request, headers: Dict[str, str]) -> Response:
    """Pass a live log stream through chunk by chunk; closing the browser tab closes the agent stream."""
    client = httpx.AsyncClient(verify=c.verify_tls, timeout=httpx.Timeout(None, connect=8.0))
    try:
        r = await client.send(client.build_request("GET", url, params=request.query_params, headers=headers), stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        return JSONResponse(status_code=502, content={"detail": f"Cannot reach agent {c.name}: {e}"})
    if r.status_code >= 400:
        content = await r.aread()
        await r.aclose()
        await client.aclose()
        return Response(content=content, status_code=r.status_code, media_type=r.headers.get("content-type"))

    async def body():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            pass
        finally:
            await r.aclose()
            await client.aclose()
    return StreamingResponse(body(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- audit ----------------------------------------------------------------------------

@api.get("/audit")
def list_audit(p: Principal = Depends(current), s: Session = Depends(db_session), cluster_id: Optional[int] = None,
               limit: int = 300) -> list:
    q = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(min(max(limit, 1), 1000))
    all_ids = [c.id for c in s.execute(select(Cluster)).scalars()]
    allowed = [cid for cid in all_ids if p.can("audit.view", cid)]
    if cluster_id is not None:
        if cluster_id not in allowed:
            raise HTTPException(status_code=403, detail="missing permission: audit.view")
        q = q.where(AuditEntry.cluster_id == cluster_id)
    elif not p.can("audit.view"):
        # Users without global audit access see their clusters plus their own account events.
        q = q.where((AuditEntry.cluster_id.in_(allowed)) | (AuditEntry.user_id == p.user.id))
    return [{"id": a.id, "time": a.time.isoformat(), "username": a.username, "cluster": a.cluster_name,
             "cluster_id": a.cluster_id, "action": a.action, "target": a.target, "params": a.params,
             "dry_run": a.dry_run, "ok": a.ok, "message": a.message, "client_ip": a.client_ip}
            for a in s.execute(q).scalars()]


# --- AI (runs in the hub, keys in the database) ---------------------------------------

class HubAIStore(ai.AIConfigStore):
    """Same behaviour as the agent's AI store, but kept encrypted in the settings table."""

    KEY = "ai"

    def __init__(self, s: Session):
        super().__init__(lambda: None, lambda: None, {})
        self.s = s

    def _raw(self) -> Dict[str, str]:
        row = get_setting(self.s, self.KEY)
        if row is None or not row.secret_encrypted:
            return {}
        return json.loads(crypto().decrypt(row.secret_encrypted))

    def _write(self, data: Dict[str, Any]) -> None:
        flat = {"active_provider": data["active"]}
        for p_, slot in data["providers"].items():
            for f in self.FIELDS:
                flat[f"{p_}_{f}"] = slot.get(f, "")
        row = get_setting(self.s, self.KEY)
        from .db import Setting
        if row is None:
            row = Setting(key=self.KEY)
            self.s.add(row)
        row.secret_encrypted = crypto().encrypt(json.dumps(flat))
        row.value = {"active": data["active"]}
        self.s.commit()

    def clear(self, provider: str) -> None:
        data = self.load_all()
        data["providers"][provider]["api_key"] = ""
        if data["active"] == provider:
            data["active"] = next((x for x, sl in data["providers"].items() if sl["api_key"]), provider)
        self._write(data)


class AIConfigBody(BaseModel):
    provider: str = Field(pattern="^(gemini|openai)$")
    api_key: Optional[str] = Field(None, max_length=512)
    model: str = Field("", max_length=200)
    base_url: str = Field("", max_length=300)
    activate: bool = True


class AIProviderBody(BaseModel):
    provider: str = Field(pattern="^(gemini|openai)$")
    api_key: Optional[str] = Field(None, max_length=512)
    base_url: str = Field("", max_length=300)


class DiagnoseBody(BaseModel):
    provider: Optional[str] = Field(None, pattern="^(gemini|openai)$")
    scope: str = Field("cluster", pattern="^(cluster|namespace|object)$")
    namespace: Optional[str] = Field(None, max_length=253)
    kind: Optional[str] = Field(None, max_length=40)
    name: Optional[str] = Field(None, max_length=253)
    question: str = Field("", max_length=2000)


@api.get("/settings/ai")
def get_ai(p: Principal = Depends(current), s: Session = Depends(db_session)) -> dict:
    return ai.AIConfigStore.public(HubAIStore(s).load_all())


@api.post("/settings/ai")
def save_ai(body: AIConfigBody, request: Request, p: Principal = Depends(require("settings.manage")),
            s: Session = Depends(db_session)) -> dict:
    base = (body.base_url or ai.PROVIDERS[body.provider]["base_url"]).rstrip("/")
    if not base.startswith("https://"):
        raise HTTPException(status_code=400, detail="base URL must start with https://")
    HubAIStore(s).save(body.provider, body.api_key, body.model, base, body.activate)
    audit(s, request, p, "ai-config", body.provider, params={"model": body.model, "key_changed": body.api_key is not None})
    return ai.AIConfigStore.public(HubAIStore(s).load_all())


@api.post("/settings/ai/active")
def set_ai_active(body: AIProviderBody, request: Request, p: Principal = Depends(require("settings.manage")),
                  s: Session = Depends(db_session)) -> dict:
    try:
        HubAIStore(s).set_active(body.provider)
    except ai.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit(s, request, p, "ai-config", body.provider, message="active provider switched")
    return ai.AIConfigStore.public(HubAIStore(s).load_all())


@api.delete("/settings/ai/{provider}")
def clear_ai(provider: str, request: Request, p: Principal = Depends(require("settings.manage")),
             s: Session = Depends(db_session)) -> dict:
    if provider not in ai.PROVIDERS:
        raise HTTPException(status_code=404, detail="unknown provider")
    HubAIStore(s).clear(provider)
    audit(s, request, p, "ai-config", provider, message="key removed")
    return ai.AIConfigStore.public(HubAIStore(s).load_all())


@api.post("/settings/ai/models")
def ai_models(body: AIProviderBody, p: Principal = Depends(require("settings.manage")),
              s: Session = Depends(db_session)) -> dict:
    cfg = HubAIStore(s).load(body.provider)
    cfg["base_url"] = (body.base_url or ai.PROVIDERS[body.provider]["base_url"]).rstrip("/")
    if body.api_key:
        cfg["api_key"] = body.api_key
    try:
        return {"models": ai.list_models(cfg)}
    except ai.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e))


class RemoteClusterTools:
    """The AI's read-only tools, implemented over the agent's API instead of the Kubernetes API."""

    def __init__(self, client: httpx.Client):
        self.c = client

    def _get(self, path: str, **params: Any) -> Any:
        r = self.c.get(path, params={k: v for k, v in params.items() if v not in (None, "")})
        if r.status_code >= 400:
            return {"error": f"agent {r.status_code}: {_describe_result(r)}"}
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    def run(self, name: str, args: Dict[str, Any]) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return ai._clip({"error": f"unknown tool {name}"})
        try:
            return ai._clip(fn(**{k: v for k, v in (args or {}).items() if v not in (None, "")}))
        except (TypeError, httpx.HTTPError) as e:
            return ai._clip({"error": str(e)})

    def t_get_cluster_overview(self) -> Any:
        s = self._get("/summary")
        return {k: s.get(k) for k in ("health", "problems", "nodes", "pods", "deployments", "cpu", "memory",
                                      "kubernetes_version")} if isinstance(s, dict) else s

    def t_list_pods(self, namespace: Optional[str] = None, problems_only: bool = False) -> Any:
        keep = ("namespace", "name", "status", "phase", "ready_containers", "total_containers", "restarts",
                "last_termination_reasons", "problem", "node", "age_s")
        pods = self._get("/pods", namespace=namespace, problems_only=str(bool(problems_only)).lower())
        return [{k: x.get(k) for k in keep} for x in pods][:150] if isinstance(pods, list) else pods

    def t_list_workloads(self, namespace: str) -> Any:
        deps = self._get("/deployments", namespace=namespace)
        svcs = self._get("/services", namespace=namespace)
        return {"deployments": [{k: d.get(k) for k in ("name", "desired", "ready", "available", "containers", "revision")}
                                for d in deps] if isinstance(deps, list) else deps,
                "services": [{k: x.get(k) for k in ("name", "type", "selector", "ports")} for x in svcs]
                if isinstance(svcs, list) else svcs}

    def t_get_events(self, namespace: Optional[str] = None, object_name: Optional[str] = None,
                     warnings_only: bool = False) -> Any:
        ev = self._get("/events", namespace=namespace, warnings_only=str(bool(warnings_only)).lower(), limit=300)
        if not isinstance(ev, list):
            return ev
        if object_name:
            ev = [e for e in ev if e["object"].split("/", 1)[-1].startswith(object_name)]
        return [{k: e.get(k) for k in ("type", "reason", "object", "namespace", "message", "count", "age_s")} for e in ev[:40]]

    def t_describe(self, kind: str, name: str, namespace: Optional[str] = None) -> Any:
        if kind == "Secret":
            return {"error": "Secrets cannot be described"}
        d = self._get("/resource", kind=kind, name=name, namespace=namespace)
        if not isinstance(d, dict) or "object" not in d:
            return d
        obj = d["object"]
        (obj.get("metadata") or {}).pop("annotations", None)
        return {"object": obj, "related": d.get("related"), "pods": d.get("pods")}

    def t_get_pod_logs(self, namespace: str, name: str, container: Optional[str] = None, previous: bool = False,
                       tail: int = 80) -> Any:
        return self._get(f"/pods/{namespace}/{name}/logs", container=container, previous=str(bool(previous)).lower(),
                         tail=min(int(tail), 200))

    def t_get_deployment_history(self, namespace: str, name: str) -> Any:
        return self._get(f"/deployments/{namespace}/{name}/history")


@api.post("/clusters/{cluster_id}/ai/diagnose")
def diagnose(cluster_id: int, body: DiagnoseBody, request: Request, p: Principal = Depends(current),
             s: Session = Depends(db_session)) -> dict:
    c = _get_cluster(s, cluster_id, p, "ai.use")
    if body.scope == "namespace" and not body.namespace:
        raise HTTPException(status_code=400, detail="scope=namespace needs a namespace")
    if body.scope == "object" and not (body.kind and body.name):
        raise HTTPException(status_code=400, detail="scope=object needs kind and name")
    if body.scope == "cluster":
        task, target = "Check the whole cluster: find every current problem, explain root causes and propose fixes.", "cluster"
    elif body.scope == "namespace":
        task = f"Check namespace '{body.namespace}': find current problems, explain root causes and propose fixes."
        target = f"namespace/{body.namespace}"
    else:
        where = f" in namespace '{body.namespace}'" if body.namespace else ""
        task = f"Investigate {body.kind} '{body.name}'{where}: is anything wrong, why, and how to fix it?"
        target = f"{body.kind.lower()}/{body.namespace or ''}/{body.name}"
    if body.question:
        task += f"\n\nThe user also asks: {body.question}"
    cfg = HubAIStore(s).load(body.provider)
    with agent_client(c) as client:
        try:
            info = client.get("/info").json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach agent {c.name}: {e}")
        try:
            result = ai.diagnose(cfg, RemoteClusterTools(client), task, info.get("protected_namespaces") or [])
        except ai.ProviderError as e:
            audit(s, request, p, "ai-diagnose", target, ok=False, message=str(e), cluster=c)
            raise HTTPException(status_code=502, detail=str(e))
        except httpx.HTTPError as e:
            audit(s, request, p, "ai-diagnose", target, ok=False, message=str(e), cluster=c)
            raise HTTPException(status_code=504, detail=f"AI provider unreachable: {e}")
    audit(s, request, p, "ai-diagnose", target, cluster=c, params={"model": result["model"], "steps": len(result["steps"])},
          message=f"{len(result['issues'])} issue(s), {len(result['actions'])} proposed action(s), {result['seconds']}s")
    return {**result, "control_enabled": bool(info.get("control_enabled"))}


# --- pod shell (WebSocket) -------------------------------------------------------------

@app.websocket("/api/hub/clusters/{cluster_id}/exec/{namespace}/{pod}")
async def pod_shell(ws: WebSocket, cluster_id: int, namespace: str, pod: str, container: Optional[str] = None) -> None:
    """Browser <-> hub <-> agent terminal. Logged-in users with pods.exec only; every session is audited."""
    # Cross-site WebSocket hijacking guard: the page must come from this hub.
    origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
    if not origin or urlsplit(origin).netloc != host:
        await ws.close(code=4403, reason="origin not allowed")
        return
    with state["db"].session() as s:
        try:
            p = authenticate(s, ws.cookies.get(COOKIE))
        except HTTPException as e:
            await ws.close(code=4401, reason=str(e.detail))
            return
        c = s.get(Cluster, cluster_id)
        if c is None or not p.can("cluster.view", cluster_id) or p.user.must_change_password:
            await ws.close(code=4404, reason="cluster not found")
            return
        if not p.can("pods.exec", cluster_id):
            await ws.close(code=4403, reason="missing permission: pods.exec")
            return
        token, verify, base, username = crypto().decrypt(c.token_encrypted), c.verify_tls, c.url.rstrip("/"), p.user.username
        cluster_name, user_id = c.name, p.user.id
    target = f"pod/{namespace}/{pod}" + (f"/{container}" if container else "")
    client_ip = ws.client.host if ws.client else ""

    def record(ok: bool, message: str, params: Optional[dict] = None) -> None:
        with state["db"].session() as s2:
            s2.add(AuditEntry(user_id=user_id, username=username, cluster_id=cluster_id, cluster_name=cluster_name,
                              action="exec", target=target, params=params or {}, ok=ok, message=message[:2000],
                              client_ip=client_ip))
            s2.commit()

    await ws.accept()
    url = ("wss://" + base[len("https://"):] if base.startswith("https://") else "ws://" + base.split("://", 1)[-1]) + \
        f"/api/v1/pods/{quote(namespace)}/{quote(pod)}/exec" + (f"?container={quote(container)}" if container else "")
    ssl_ctx = None
    if url.startswith("wss://"):
        ssl_ctx = ssl.create_default_context()
        if not verify:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    try:
        agent_ws = await ws_connect(url, ssl=ssl_ctx, open_timeout=10, max_size=2 ** 22,
                                    additional_headers={"Authorization": f"Bearer {token}", "X-K10Y-Actor": f"hub:{username}"[:64]})
    except Exception as e:
        record(False, f"cannot open shell: {e}")
        await ws.send_text(f"\r\n\x1b[31mCannot open a shell: {e}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    record(True, "shell opened")

    async def to_agent() -> None:
        while True:
            await agent_ws.send(await ws.receive_text())

    async def to_browser() -> None:
        async for msg in agent_ws:
            await ws.send_text(msg if isinstance(msg, str) else msg.decode("utf-8", "replace"))

    tasks = [asyncio.create_task(to_agent()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        reason = agent_ws.close_reason if getattr(agent_ws, "close_reason", None) else ""
        await agent_ws.close()
        try:
            if reason:
                await ws.send_text(f"\r\n\x1b[33m{reason}\x1b[0m\r\n")
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
        record(True, "shell closed" + (f": {reason}" if reason else ""), {"seconds": int(time.monotonic() - started)})


# --- alerts ---------------------------------------------------------------------------

def _agent_get(c: Cluster, path: str) -> Any:
    with agent_client(c) as client:
        r = client.get(f"/{path}")
    if r.status_code >= 400:
        raise RuntimeError(f"agent {r.status_code}: {_describe_result(r)}")
    return r.json()


def _alert_diagnose(c: Cluster, resource: str) -> str:
    """Short AI diagnosis of a firing alert's object (kind/namespace/name), if an AI key is configured."""
    kind, ns, name = (resource.split("/", 2) + ["", ""])[:3]
    with state["db"].session() as s:
        cfg = HubAIStore(s).load()
    if not cfg.get("api_key"):
        return ""
    where = f" in namespace '{ns}'" if ns else ""
    with agent_client(c) as client:
        result = ai.diagnose(cfg, RemoteClusterTools(client), f"Investigate {kind} '{name}'{where}: what is wrong and "
                             "how to fix it? Keep the summary under 600 characters.", [])
    return result.get("summary", "")


def haproxy_mon() -> haproxy_monitor.HAProxyMonitor:
    if "haproxy_monitor" not in state:
        state["haproxy_monitor"] = haproxy_monitor.HAProxyMonitor(state["db"].session, lambda v: crypto().decrypt(v), _agent_get)
    return state["haproxy_monitor"]


def _haproxy_targets() -> list:
    m = state.get("haproxy_monitor")
    if m is None:
        return []
    with state["db"].session() as s:
        return [(i.id, i.name, m.latest.get(i.id)) for i in s.execute(select(haproxy.HAProxyInstance)).scalars()]


def alert_engine() -> alerts.AlertEngine:
    if "engine" not in state:
        state["engine"] = alerts.AlertEngine(state["db"].session, lambda v: crypto().decrypt(v), _agent_get,
                                             _alert_diagnose, lambda: settings.public_url, _haproxy_targets)
    return state["engine"]


class _EngineRef(dict):
    def get(self, key, default=None):
        return alert_engine() if key == "engine" and "db" in state else default

    def __getitem__(self, key):
        return alert_engine()


app.include_router(alerts.build_router(db_session, current, require, audit, crypto, _EngineRef()))
app.include_router(haproxy.build_router(db_session, require, audit, crypto, monitor=lambda: state.get("haproxy_monitor") or (haproxy_mon() if "db" in state else None),
                                        syslog=lambda: state.get("syslog"), can_cluster=lambda p, perm, cid: p.can(perm, cid)))


# --- web UI ---------------------------------------------------------------------------

@api.get("/health")
def hub_health() -> dict:
    return {"status": "ok", "version": __version__}


app.include_router(api)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Response:
    """Serve the built Vue app; unknown paths fall back to index.html (client-side routing)."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")
    target = (WEB_DIR / path).resolve()
    if path and WEB_DIR in target.parents and target.is_file():
        return FileResponse(target)
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"hub": "k10y", "version": __version__, "ui": "not built"})
