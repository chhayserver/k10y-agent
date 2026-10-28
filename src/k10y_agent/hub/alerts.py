"""Alert rules, Telegram notifications and alert history (stored in PostgreSQL).

A background thread evaluates every enabled rule against every cluster it applies to.
A condition must hold for the rule's `for_minutes` before it fires; when it clears, a
resolved message is sent. Notifications go to the rule's channels (Telegram for now).
"""

import html
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, false, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, Cluster, utcnow

log = logging.getLogger("k10y.alerts")
TELEGRAM_API = "https://api.telegram.org"
EVAL_SECONDS = 30
RULE_TYPES = ("problems", "resource", "unreachable", "haproxy")
HAPROXY_CONDITIONS = ("server_down", "backend_down", "error_rate", "sessions", "cert_expiry", "unreachable")


class AlertChannel(Base):
    __tablename__ = "alert_channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32), default="telegram")
    config: Mapped[Any] = mapped_column(JSON, default=dict)          # non-secret, e.g. chat_id
    secret_encrypted: Mapped[str] = mapped_column(Text, default="")  # e.g. bot token
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    type: Mapped[str] = mapped_column(String(32))
    params: Mapped[Any] = mapped_column(JSON, default=dict)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # None = all clusters
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    for_minutes: Mapped[int] = mapped_column(Integer, default=2)
    repeat_minutes: Mapped[int] = mapped_column(Integer, default=0)            # 0 = no reminders
    notify_resolved: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_ids: Mapped[Any] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), default="")
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    cluster_name: Mapped[str] = mapped_column(String(64), default="")
    haproxy_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(300), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | firing | resolved
    title: Mapped[str] = mapped_column(String(300), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    resource: Mapped[str] = mapped_column(String(300), default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_error: Mapped[str] = mapped_column(Text, default="")
    ai_summary: Mapped[str] = mapped_column(Text, default="")


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- conditions: what is wrong right now, per rule type ------------------------------

Condition = Tuple[str, str, str, str]  # key, title, message, resource (kind/ns/name)


def evaluate_problems(summary: Dict[str, Any], params: Dict[str, Any]) -> List[Condition]:
    kinds = set(params.get("kinds") or [])
    include = set(params.get("namespaces") or [])
    exclude = set(params.get("exclude_namespaces") or [])
    out = []
    for p in summary.get("problems", []):
        ns = p.get("namespace")
        if kinds and p["kind"] not in kinds:
            continue
        if include and ns not in include:
            continue
        if ns and ns in exclude:
            continue
        where = f"{ns}/" if ns else ""
        out.append((f"problem/{p['kind']}/{ns or ''}/{p['name']}/{p['reason']}",
                    f"{p['kind']} {where}{p['name']}: {p['reason']}", p["reason"], f"{p['kind']}/{ns or ''}/{p['name']}"))
    return out


def evaluate_resource(summary: Dict[str, Any], nodes: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Condition]:
    metric = params.get("metric", "memory")          # cpu | memory
    scope = params.get("scope", "node")              # node | cluster
    threshold = float(params.get("threshold", 90))
    out = []
    if scope == "cluster":
        pct = (summary.get(metric) or {}).get("percent")
        if pct is not None and pct >= threshold:
            out.append((f"resource/cluster/{metric}", f"Cluster {metric} at {pct:.0f}% (threshold {threshold:.0f}%)",
                        f"Cluster-wide {metric} usage is {pct:.0f}%.", ""))
    else:
        for n in nodes:
            pct = n.get(f"{metric}_percent")
            if pct is not None and pct >= threshold:
                out.append((f"resource/node/{n['name']}/{metric}", f"Node {n['name']} {metric} at {pct:.0f}% (threshold {threshold:.0f}%)",
                            f"{metric.upper()} usage on node {n['name']} is {pct:.0f}%.", f"Node//{n['name']}"))
    return out


def evaluate_haproxy(latest: Optional[Dict[str, Any]], params: Dict[str, Any]) -> Optional[List[Condition]]:
    """Conditions for one HAProxy instance from the monitor's latest sample."""
    cond = params.get("condition", "server_down")
    if latest is None:
        return None                          # not sampled yet
    if cond == "unreachable":
        return [("hx/unreachable", "HAProxy Data Plane API unreachable", latest["error"], "")] if latest.get("error") else []
    if latest.get("error"):
        return None
    from .haproxy import _server_state      # local import: haproxy imports nothing from alerts
    stats = latest.get("stats") or []
    point = latest.get("point") or {}
    out: List[Condition] = []
    if cond in ("server_down", "backend_down"):
        servers: Dict[str, List[Tuple[str, str, bool]]] = {}
        for it in stats:
            if it.get("type") == "server":
                s = it.get("stats") or {}
                servers.setdefault(it.get("backend_name", ""), []).append((it["name"], _server_state(s.get("status")), bool(s.get("bck"))))
        for be, lst in servers.items():
            active = [x for x in lst if not x[2]]
            if cond == "backend_down":
                if active and not any(st in ("up", "nocheck") for _, st, _ in active):
                    out.append((f"hx/backend/{be}", f"Backend {be} has no server up", f"All {len(active)} servers of {be} are down or out of rotation.", f"backend/{be}"))
            else:
                for name, st, _ in lst:
                    if st == "down":
                        out.append((f"hx/server/{be}/{name}", f"Server {be}/{name} is down", "The health check is failing.", f"server/{be}/{name}"))
    elif cond == "error_rate":
        th, min_rps = float(params.get("threshold", 5)), float(params.get("min_rps", 1))
        for fe, v in (point.get("f") or {}).items():
            if v.get("req", 0) >= min_rps and v.get("e5_pct", 0) >= th:
                out.append((f"hx/5xx/{fe}", f"Frontend {fe}: {v['e5_pct']:.0f}% server errors", f"{v['e5_pct']:.1f}% of responses were 5xx at {v['req']:.1f} req/s (threshold {th:.0f}%).", f"frontend/{fe}"))
    elif cond == "sessions":
        th = float(params.get("threshold", 80))
        for fe, v in (point.get("f") or {}).items():
            if v.get("slim") and 100.0 * v.get("sess", 0) / v["slim"] >= th:
                pct = 100.0 * v["sess"] / v["slim"]
                out.append((f"hx/sessions/{fe}", f"Frontend {fe} at {pct:.0f}% of its session limit", f"{v['sess']} of {v['slim']} sessions in use.", f"frontend/{fe}"))
    elif cond == "cert_expiry":
        days = float(params.get("threshold", 14))
        for c in latest.get("certs") or []:
            exp = c.get("not_after")
            if not exp:
                continue
            left = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds() / 86400
            if left <= days:
                what = "has expired" if left < 0 else f"expires in {max(0, int(left))} days"
                out.append((f"hx/cert/{c.get('storage_name')}", f"Certificate {c.get('storage_name')} {what}", f"Valid until {exp}.", f"certificate/{c.get('storage_name')}"))
    return out


# --- notifications --------------------------------------------------------------------

def telegram_send(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    r = httpx.post(f"{TELEGRAM_API}/bot{token}/sendMessage", timeout=timeout,
                   json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
    if r.status_code >= 400:
        try:
            desc = r.json().get("description", "")
        except ValueError:
            desc = r.text[:200]
        raise RuntimeError(f"Telegram API {r.status_code}: {desc}")


def telegram_chats(token: str) -> List[Dict[str, Any]]:
    """Chats that recently messaged the bot (to find the chat ID)."""
    r = httpx.get(f"{TELEGRAM_API}/bot{token}/getUpdates", timeout=15.0)
    if r.status_code >= 400:
        raise RuntimeError(f"Telegram API {r.status_code}: {r.json().get('description', '') if r.content else ''}")
    chats: Dict[str, Dict[str, Any]] = {}
    for u in r.json().get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            title = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
            chats[str(chat["id"])] = {"id": str(chat["id"]), "type": chat.get("type", ""), "title": title}
    return list(chats.values())


ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


def format_message(ev: AlertEvent, rule: AlertRule, resolved: bool, public_url: str, reminder: bool = False) -> str:
    e = html.escape
    head = "✅ <b>Resolved</b>" if resolved else f"{ICON.get(ev.severity, '🟠')} <b>{'Still firing' if reminder else ev.severity.upper()}</b>"
    lines = [f"{head} · <b>{e(ev.cluster_name or 'hub')}</b>", e(ev.title)]
    if not resolved and ev.message and ev.message not in ev.title:
        lines.append(f"<i>{e(ev.message[:500])}</i>")
    since = _aware(ev.first_seen)
    lines.append(f"Rule: {e(rule.name)}")
    if resolved and ev.resolved_at and since:
        mins = max(1, int((_aware(ev.resolved_at) - since).total_seconds() // 60))
        lines.append(f"Lasted about {mins} min")
    elif since:
        lines.append(f"Since {since.strftime('%Y-%m-%d %H:%M UTC')}")
    if not resolved and ev.ai_summary:
        lines.append(f"\n🤖 <b>AI diagnosis</b>\n{e(ev.ai_summary[:1500])}")
    if public_url:
        lines.append(f'\n<a href="{e(public_url.rstrip("/"))}/overview">Open k10y</a>')
    return "\n".join(lines)


# --- engine ---------------------------------------------------------------------------

class AlertEngine:
    def __init__(self, db_factory: Callable[[], Session], decrypt: Callable[[str], str],
                 agent_get: Callable[[Cluster, str], Any], ai_diagnose: Optional[Callable[[Cluster, str], str]] = None,
                 public_url: Callable[[], str] = lambda: "",
                 haproxy_targets: Callable[[], List[Tuple[int, str, Optional[Dict[str, Any]]]]] = lambda: []):
        self.haproxy_targets = haproxy_targets
        self.db_factory = db_factory
        self.decrypt = decrypt
        self.agent_get = agent_get
        self.ai_diagnose = ai_diagnose
        self.public_url = public_url
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_run: Optional[datetime] = None
        self.last_error = ""

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="alerts", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(5)
        while not self._stop.is_set():
            try:
                self.run_once()
                self.last_error = ""
            except Exception as e:  # never let the loop die
                self.last_error = str(e)
                log.exception("alert evaluation failed")
            self._stop.wait(EVAL_SECONDS)

    def run_once(self, now: Optional[datetime] = None) -> None:
        now = now or utcnow()
        with self.db_factory() as s:
            rules = [r for r in s.execute(select(AlertRule).where(AlertRule.enabled.is_(True))).scalars()]
            if not rules:
                self.last_run = now
                return
            clusters = list(s.execute(select(Cluster)).scalars())
            cache: Dict[Tuple[int, str], Any] = {}

            def fetch(c: Cluster, path: str) -> Any:
                if (c.id, path) not in cache:
                    try:
                        cache[(c.id, path)] = self.agent_get(c, path)
                    except Exception as e:  # unreachable agents are a condition, not an error
                        cache[(c.id, path)] = e
                return cache[(c.id, path)]

            for rule in rules:
                if rule.type == "haproxy":
                    only = (rule.params or {}).get("instance_id")
                    for iid, name, latest in self.haproxy_targets():
                        if only and int(only) != iid:
                            continue
                        conds = evaluate_haproxy(latest, rule.params or {})
                        if conds is not None:
                            self._reconcile(s, rule, _Target(iid, f"haproxy/{name}", "haproxy"), conds, now)
                    continue
                for c in clusters:
                    if rule.cluster_id is not None and rule.cluster_id != c.id:
                        continue
                    conds = self._conditions(rule, c, fetch)
                    if conds is None:
                        continue  # data unavailable for this rule type; keep state as is
                    self._reconcile(s, rule, c, conds, now)
            s.commit()
        self.last_run = now

    def _conditions(self, rule: AlertRule, c: Cluster, fetch) -> Optional[List[Condition]]:
        summary = fetch(c, "summary")
        if rule.type == "unreachable":
            if isinstance(summary, Exception):
                return [(f"unreachable/{c.id}", f"Cluster {c.name} is unreachable", str(summary)[:300], "")]
            return []
        if isinstance(summary, Exception):
            return None
        if rule.type == "problems":
            return evaluate_problems(summary, rule.params or {})
        if rule.type == "resource":
            nodes = fetch(c, "nodes") if (rule.params or {}).get("scope", "node") == "node" else []
            if isinstance(nodes, Exception):
                return None
            return evaluate_resource(summary, nodes, rule.params or {})
        return []

    def _reconcile(self, s: Session, rule: AlertRule, c: Any, conds: List[Condition], now: datetime) -> None:
        is_hx = getattr(c, "kind", "cluster") == "haproxy"
        scope = AlertEvent.haproxy_id == c.id if is_hx else AlertEvent.cluster_id == c.id
        open_events = {e.key: e for e in s.execute(select(AlertEvent).where(
            AlertEvent.rule_id == rule.id, scope, AlertEvent.state.in_(("pending", "firing")))).scalars()}
        current = {k: (title, msg, res) for k, title, msg, res in conds}
        for key, (title, msg, res) in current.items():
            ev = open_events.get(key)
            if ev is None:
                ev = AlertEvent(rule_id=rule.id, rule_name=rule.name, cluster_id=None if is_hx else c.id,
                                haproxy_id=c.id if is_hx else None, cluster_name=c.name, key=key,
                                severity=rule.severity, state="pending", title=title, message=msg, resource=res,
                                first_seen=now, last_seen=now)
                s.add(ev)
                s.flush()
            ev.last_seen, ev.title, ev.message = now, title, msg
            held = (now - _aware(ev.first_seen)).total_seconds() / 60
            if ev.state == "pending" and held >= rule.for_minutes:
                ev.state, ev.fired_at = "firing", now
                if rule.ai_summary and self.ai_diagnose and res and not is_hx:
                    try:
                        ev.ai_summary = self.ai_diagnose(c, res)
                    except Exception as e:
                        ev.ai_summary = f"(AI diagnosis failed: {e})"
                self._notify(s, rule, ev, resolved=False)
            elif ev.state == "firing" and rule.repeat_minutes and ev.notified_at and \
                    (now - _aware(ev.notified_at)).total_seconds() / 60 >= rule.repeat_minutes:
                self._notify(s, rule, ev, resolved=False, reminder=True)
        for key, ev in open_events.items():
            if key in current:
                continue
            was_firing = ev.state == "firing"
            ev.state, ev.resolved_at = "resolved", now
            if was_firing and rule.notify_resolved:
                self._notify(s, rule, ev, resolved=True)

    def _notify(self, s: Session, rule: AlertRule, ev: AlertEvent, resolved: bool, reminder: bool = False) -> None:
        text = format_message(ev, rule, resolved, self.public_url(), reminder)
        errors = []
        for ch in s.execute(select(AlertChannel).where(AlertChannel.id.in_(rule.channel_ids or []),
                                                       AlertChannel.enabled.is_(True))).scalars():
            try:
                self.send(ch, text)
            except Exception as e:
                errors.append(f"{ch.name}: {e}")
        ev.notified_at = utcnow()
        ev.notify_error = "; ".join(errors)[:1000]

    def send(self, ch: AlertChannel, text: str) -> None:
        if ch.type == "telegram":
            telegram_send(self.decrypt(ch.secret_encrypted), str((ch.config or {}).get("chat_id", "")), text)
        else:
            raise RuntimeError(f"unsupported channel type {ch.type}")


class _Target:
    def __init__(self, id: int, name: str, kind: str):
        self.id, self.name, self.kind = id, name, kind


# --- API ------------------------------------------------------------------------------

class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str = Field("telegram", pattern="^telegram$")
    chat_id: str = Field(min_length=1, max_length=64)
    bot_token: Optional[str] = Field(None, max_length=200, description="Omit to keep the stored token.")
    enabled: bool = True


class ChatsBody(BaseModel):
    bot_token: Optional[str] = Field(None, max_length=200)
    channel_id: Optional[int] = None


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    type: str = Field(pattern="^(problems|resource|unreachable|haproxy)$")
    params: Dict[str, Any] = Field(default_factory=dict)
    cluster_id: Optional[int] = None
    severity: str = Field("warning", pattern="^(critical|warning|info)$")
    for_minutes: int = Field(2, ge=0, le=1440)
    repeat_minutes: int = Field(0, ge=0, le=10080)
    notify_resolved: bool = True
    ai_summary: bool = False
    channel_ids: List[int] = Field(default_factory=list)


def build_router(db_session, current, require, audit, crypto, engine_ref: Dict[str, AlertEngine]) -> APIRouter:
    r = APIRouter(prefix="/api/hub/alerts")

    def channel_view(ch: AlertChannel) -> dict:
        return {"id": ch.id, "name": ch.name, "type": ch.type, "chat_id": (ch.config or {}).get("chat_id", ""),
                "enabled": ch.enabled, "token_set": bool(ch.secret_encrypted)}

    def rule_view(x: AlertRule) -> dict:
        return {k: getattr(x, k) for k in ("id", "name", "enabled", "type", "params", "cluster_id", "severity", "for_minutes",
                                           "repeat_minutes", "notify_resolved", "ai_summary", "channel_ids")}

    def event_view(e: AlertEvent) -> dict:
        iso = lambda d: _aware(d).isoformat() if d else None
        return {"id": e.id, "rule_id": e.rule_id, "rule": e.rule_name, "cluster_id": e.cluster_id, "cluster": e.cluster_name,
                "severity": e.severity, "state": e.state, "title": e.title, "message": e.message, "resource": e.resource,
                "first_seen": iso(e.first_seen), "last_seen": iso(e.last_seen), "fired_at": iso(e.fired_at),
                "resolved_at": iso(e.resolved_at), "notified_at": iso(e.notified_at), "notify_error": e.notify_error,
                "ai_summary": e.ai_summary}

    # channels (settings.manage)
    @r.get("/channels")
    def list_channels(p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> list:
        return [channel_view(c) for c in s.execute(select(AlertChannel).order_by(AlertChannel.name)).scalars()]

    @r.get("/channels/names")
    def channel_names(p=Depends(current), s: Session = Depends(db_session)) -> list:
        return [{"id": c.id, "name": c.name, "type": c.type} for c in s.execute(select(AlertChannel)).scalars()]

    @r.post("/channels")
    def create_channel(body: ChannelBody, request: Request, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        if not body.bot_token:
            raise HTTPException(status_code=400, detail="bot token is required")
        ch = AlertChannel(name=body.name, type=body.type, config={"chat_id": body.chat_id}, enabled=body.enabled,
                          secret_encrypted=crypto().encrypt(body.bot_token))
        s.add(ch)
        s.commit()
        audit(s, request, p, "alert-channel-create", ch.name)
        return channel_view(ch)

    @r.patch("/channels/{cid}")
    def update_channel(cid: int, body: ChannelBody, request: Request, p=Depends(require("settings.manage")),
                       s: Session = Depends(db_session)) -> dict:
        ch = s.get(AlertChannel, cid)
        if ch is None:
            raise HTTPException(status_code=404, detail="channel not found")
        ch.name, ch.enabled, ch.config = body.name, body.enabled, {"chat_id": body.chat_id}
        if body.bot_token:
            ch.secret_encrypted = crypto().encrypt(body.bot_token)
        s.commit()
        audit(s, request, p, "alert-channel-update", ch.name, params={"token_changed": bool(body.bot_token)})
        return channel_view(ch)

    @r.delete("/channels/{cid}")
    def delete_channel(cid: int, request: Request, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        ch = s.get(AlertChannel, cid)
        if ch is None:
            raise HTTPException(status_code=404, detail="channel not found")
        s.delete(ch)
        s.commit()
        audit(s, request, p, "alert-channel-delete", ch.name)
        return {"ok": True}

    @r.post("/channels/{cid}/test")
    def test_channel(cid: int, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        ch = s.get(AlertChannel, cid)
        if ch is None:
            raise HTTPException(status_code=404, detail="channel not found")
        try:
            engine_ref["engine"].send(ch, f"✅ <b>k10y test message</b>\nChannel “{html.escape(ch.name)}” is working. Sent by {html.escape(p.user.username)}.")
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"ok": True, "message": "Test message sent."}

    @r.post("/telegram/chats")
    def find_chats(body: ChatsBody, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        token = body.bot_token
        if not token and body.channel_id:
            ch = s.get(AlertChannel, body.channel_id)
            token = crypto().decrypt(ch.secret_encrypted) if ch else ""
        if not token:
            raise HTTPException(status_code=400, detail="Enter the bot token first.")
        try:
            return {"chats": telegram_chats(token)}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    # rules (alerts.manage)
    @r.get("/rules")
    def list_rules(p=Depends(current), s: Session = Depends(db_session)) -> list:
        return [rule_view(x) for x in s.execute(select(AlertRule).order_by(AlertRule.name)).scalars()]

    def _validate(body: RuleBody, s: Session) -> None:
        if body.cluster_id is not None and s.get(Cluster, body.cluster_id) is None:
            raise HTTPException(status_code=400, detail="unknown cluster")
        if body.type == "haproxy":
            if body.params.get("condition") not in HAPROXY_CONDITIONS:
                raise HTTPException(status_code=400, detail=f"condition must be one of {', '.join(HAPROXY_CONDITIONS)}")
            if body.params.get("condition") in ("error_rate", "sessions", "cert_expiry"):
                try:
                    float(body.params.get("threshold"))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="threshold must be a number")
        if body.type == "resource":
            if body.params.get("metric") not in ("cpu", "memory") or body.params.get("scope") not in ("node", "cluster"):
                raise HTTPException(status_code=400, detail="resource rules need metric cpu|memory and scope node|cluster")
            try:
                t = float(body.params.get("threshold", 0))
            except (TypeError, ValueError):
                t = 0
            if not 1 <= t <= 100:
                raise HTTPException(status_code=400, detail="threshold must be 1–100 (%)")

    @r.post("/rules")
    def create_rule(body: RuleBody, request: Request, p=Depends(require("alerts.manage")), s: Session = Depends(db_session)) -> dict:
        _validate(body, s)
        rule = AlertRule(**body.model_dump())
        s.add(rule)
        s.commit()
        audit(s, request, p, "alert-rule-create", rule.name, params={"type": rule.type})
        return rule_view(rule)

    @r.patch("/rules/{rid}")
    def update_rule(rid: int, body: RuleBody, request: Request, p=Depends(require("alerts.manage")), s: Session = Depends(db_session)) -> dict:
        rule = s.get(AlertRule, rid)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        _validate(body, s)
        for k, v in body.model_dump().items():
            setattr(rule, k, v)
        s.commit()
        audit(s, request, p, "alert-rule-update", rule.name)
        return rule_view(rule)

    @r.delete("/rules/{rid}")
    def delete_rule(rid: int, request: Request, p=Depends(require("alerts.manage")), s: Session = Depends(db_session)) -> dict:
        rule = s.get(AlertRule, rid)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        for ev in s.execute(select(AlertEvent).where(AlertEvent.rule_id == rid, AlertEvent.state != "resolved")).scalars():
            ev.state, ev.resolved_at = "resolved", utcnow()
        s.delete(rule)
        s.commit()
        audit(s, request, p, "alert-rule-delete", rule.name)
        return {"ok": True}

    # events
    @r.get("/events")
    def list_events(p=Depends(current), s: Session = Depends(db_session), state: Optional[str] = None, limit: int = 300) -> dict:
        q = select(AlertEvent).order_by(AlertEvent.id.desc()).limit(min(max(limit, 1), 1000))
        if state:
            q = q.where(AlertEvent.state.in_(state.split(",")))
        visible = [c.id for c in s.execute(select(Cluster)).scalars() if p.can("cluster.view", c.id)]
        from .haproxy import HAProxyInstance      # local import: haproxy imports nothing from alerts
        hx_visible = [i.id for i in s.execute(select(HAProxyInstance)).scalars() if p.can("haproxy.view", haproxy_id=i.id)]
        q = q.where(or_(AlertEvent.cluster_id.in_(visible), AlertEvent.haproxy_id.in_(hx_visible)))
        events = [event_view(e) for e in s.execute(q).scalars()]
        eng = engine_ref.get("engine")
        return {"events": events, "engine": {"last_run": eng.last_run.isoformat() if eng and eng.last_run else None,
                                             "error": eng.last_error if eng else "", "interval_s": EVAL_SECONDS}}

    return r
