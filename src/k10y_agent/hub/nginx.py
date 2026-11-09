"""Nginx instances: each runs a k10y agent in Nginx mode (on the server, or the k10y/k10y-nginx image). The hub
keeps the agent's URL and token (encrypted) and passes reads through with the user's permissions checked."""

import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, utcnow

TIMEOUT = httpx.Timeout(20.0, connect=6.0)


class NginxInstance(Base):
    __tablename__ = "nginx_instances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(300))                 # the agent, e.g. https://web1:9443
    token_encrypted: Mapped[str] = mapped_column(Text)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    group_name: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InstanceBody(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    url: str = Field(min_length=3, max_length=300)
    token: Optional[str] = Field(None, max_length=512, description="Omit to keep the stored token.")
    verify_tls: bool = False
    group_name: str = Field("", max_length=64)


class TestBody(BaseModel):
    url: str = Field(min_length=3, max_length=300)
    token: Optional[str] = Field(None, max_length=512)
    verify_tls: bool = False
    instance_id: Optional[int] = None


def agent_url(url: str) -> str:
    """A bare host:port gets https:// (the agent serves HTTPS); the port defaults to 9443."""
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://", url):
        url = f"https://{url}"
    if not re.match(r"^https?://(\[[^\]]+\]|[^/:]+):\d+", url):
        url = re.sub(r"^(https?://(\[[^\]]+\]|[^/:]+))", r"\1:9443", url, count=1)
    return url


class AgentError(Exception):
    pass


def call(url: str, token: str, verify: bool, path: str, params: Optional[dict] = None) -> Any:
    try:
        r = httpx.get(f"{url}/api/v1/nginx/{path}", params=params, headers={"Authorization": f"Bearer {token}"},
                      verify=verify, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise AgentError(f"cannot reach the agent at {url}: {e}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        raise AgentError(f"agent answered {r.status_code}: {detail}")
    return r.json()


def build_router(db_session, require, audit, crypto) -> APIRouter:
    r = APIRouter(prefix="/api/hub/nginx")

    def view(i: NginxInstance) -> dict:
        return {"id": i.id, "name": i.name, "url": i.url, "verify_tls": i.verify_tls, "group_name": i.group_name,
                "created_at": i.created_at.isoformat() if i.created_at else None}

    def get(s: Session, nid: int) -> NginxInstance:
        i = s.get(NginxInstance, nid)
        if i is None:
            raise HTTPException(status_code=404, detail="Nginx instance not found")
        return i

    def agent(i: NginxInstance, path: str, params: Optional[dict] = None) -> Any:
        try:
            return call(i.url, crypto().decrypt(i.token_encrypted), i.verify_tls, path, params)
        except AgentError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @r.get("")
    def list_instances(p=Depends(require("nginx.view")), s: Session = Depends(db_session)) -> list:
        return [view(i) for i in s.execute(select(NginxInstance).order_by(NginxInstance.name)).scalars()
                if p.can("nginx.view", nginx_id=i.id)]

    @r.post("/test")
    def test_instance(body: TestBody, p=Depends(require("nginx.manage")), s: Session = Depends(db_session)) -> dict:
        if not (p.can("nginx.manage", nginx_id=body.instance_id) if body.instance_id else p.can("nginx.manage")):
            raise HTTPException(status_code=403, detail="missing permission: nginx.manage")
        token = body.token
        if not token and body.instance_id:
            token = crypto().decrypt(get(s, body.instance_id).token_encrypted)
        url = agent_url(body.url)
        try:
            info = call(url, token or "", body.verify_tls, "info")
        except AgentError as e:
            if url.startswith("https://") and "SSL" in str(e).upper() and body.verify_tls:
                raise HTTPException(status_code=400, detail=f"{e}. The agent's certificate is self-signed: turn off verification.")
            raise HTTPException(status_code=400, detail=str(e))
        return {**info, "url": url}

    @r.post("")
    def create_instance(body: InstanceBody, request: Request, p=Depends(require("nginx.manage")), s: Session = Depends(db_session)) -> dict:
        if not p.can("nginx.manage"):   # adding an instance is for roles on all of Nginx
            raise HTTPException(status_code=403, detail="missing permission: nginx.manage")
        if not body.token:
            raise HTTPException(status_code=400, detail="The agent's token is required.")
        if s.execute(select(NginxInstance).where(NginxInstance.name == body.name)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"There's already an Nginx instance called {body.name}.")
        i = NginxInstance(name=body.name, url=agent_url(body.url), token_encrypted=crypto().encrypt(body.token),
                          verify_tls=body.verify_tls, group_name=body.group_name)
        s.add(i)
        s.commit()
        audit(s, request, p, "nginx-create", i.name, params={"url": i.url})
        return view(i)

    @r.patch("/{nid}")
    def update_instance(nid: int, body: InstanceBody, request: Request, p=Depends(require("nginx.manage")),
                        s: Session = Depends(db_session)) -> dict:
        i = get(s, nid)
        i.name, i.url, i.verify_tls, i.group_name = body.name, agent_url(body.url), body.verify_tls, body.group_name
        if body.token:
            i.token_encrypted = crypto().encrypt(body.token)
        s.commit()
        audit(s, request, p, "nginx-update", i.name, params={"url": i.url, "token_changed": bool(body.token)})
        return view(i)

    @r.delete("/{nid}")
    def delete_instance(nid: int, request: Request, p=Depends(require("nginx.manage")), s: Session = Depends(db_session)) -> dict:
        i = get(s, nid)
        s.delete(i)
        s.commit()
        audit(s, request, p, "nginx-delete", i.name)
        return {"ok": True}

    # --- reads (nginx.view; the raw config needs nginx.configure) --------------------------------------
    @r.get("/{nid}/overview")
    def overview(nid: int, p=Depends(require("nginx.view")), s: Session = Depends(db_session)) -> dict:
        """Everything the Nginx pages show at once: the agent, its configuration model and the live summary."""
        i = get(s, nid)
        return {"instance": view(i), "info": agent(i, "info"), "config": agent(i, "config"), "summary": agent(i, "summary"),
                "health": agent(i, "upstreams/health")}

    for path, perm in (("stats", "nginx.view"), ("logs", "nginx.view"), ("errors", "nginx.view"), ("certificates", "nginx.view"),
                       ("upstreams/health", "nginx.view"), ("summary", "nginx.view"), ("config/raw", "nginx.configure")):
        def endpoint(nid: int, request: Request, p=Depends(require(perm)), s: Session = Depends(db_session), _path: str = path) -> Any:
            return agent(get(s, nid), _path, dict(request.query_params))
        r.add_api_route(f"/{{nid}}/{path}", endpoint, methods=["GET"], name=f"nginx_{path.replace('/', '_')}")

    return r


def targets(db_factory: Callable, decrypt: Callable[[str], str]) -> list:
    """(id, name, summary or exception) for every instance, for the alert engine."""
    out = []
    with db_factory() as s:
        rows = [(i.id, i.name, i.url, i.token_encrypted, i.verify_tls) for i in s.execute(select(NginxInstance)).scalars()]
    for nid, name, url, tok, verify in rows:
        try:
            out.append((nid, name, call(url, decrypt(tok), verify, "summary")))
        except Exception as e:
            out.append((nid, name, e))
    return out
