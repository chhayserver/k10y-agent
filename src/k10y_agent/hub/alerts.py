"""Alert rules, notifications and alert history (stored in PostgreSQL).

A background thread evaluates every enabled rule against every cluster it applies to.
A condition must hold for the rule's `for_minutes` before it fires; when it clears, a
resolved message is sent. Notifications go to the rule's channels: Telegram, Slack, email or a
webhook. An acknowledged alert sends no more reminders; a silence mutes matching alerts for a while.
"""

import hashlib
import hmac
import html
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
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
    acked_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    acked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertSilence(Base):
    """Mutes notifications of matching alerts until `until`. Empty fields match everything; a silence made from
    an alert has its rule, cluster or HAProxy and key, so it also covers that alert when it comes back."""
    __tablename__ = "alert_silences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    haproxy_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")    # what it mutes, for people
    reason: Mapped[str] = mapped_column(String(300), default="")
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def matches(self, ev: "AlertEvent", now: datetime) -> bool:
        return (_aware(self.until) > now and (self.rule_id is None or self.rule_id == ev.rule_id)
                and (self.key is None or self.key == ev.key)
                and (self.cluster_id is None or self.cluster_id == ev.cluster_id)
                and (self.haproxy_id is None or self.haproxy_id == ev.haproxy_id))


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


def html_to_slack(text: str) -> str:
    """Our Telegram HTML -> Slack mrkdwn. Entities stay as they are: Slack escapes &, < and > the same way."""
    text = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda m: f"<{html.unescape(m.group(1))}|{m.group(2)}>", text)
    return text.replace("<b>", "*").replace("</b>", "*").replace("<i>", "_").replace("</i>", "_")


def html_to_text(text: str) -> str:
    text = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda m: f"{m.group(2)}: {m.group(1)}", text)
    return html.unescape(re.sub(r"</?[bi]>", "", text))


def slack_send(webhook_url: str, text: str, timeout: float = 15.0) -> None:
    r = httpx.post(webhook_url, json={"text": html_to_slack(text)[:3900]}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Slack {r.status_code}: {r.text[:200]}")


def webhook_send(url: str, secret: str, payload: Dict[str, Any], timeout: float = 15.0) -> None:
    """POST JSON. With a secret, X-K10y-Signature: sha256=<HMAC-SHA256 of the body> lets the receiver check it's us."""
    body = json.dumps(payload, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "k10y-alerts"}
    if secret:
        headers["X-K10y-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = httpx.post(url, content=body, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"webhook {r.status_code}: {r.text[:200]}")


def email_send(cfg: Dict[str, Any], password: str, text: str, timeout: float = 20.0) -> None:
    plain = html_to_text(text)
    msg = EmailMessage()
    msg["Subject"] = "[k10y] " + plain.splitlines()[0][:150] if plain else "[k10y] alert"
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(cfg["recipients"])
    msg.set_content(plain)
    host, port, security = cfg["host"], int(cfg.get("port") or 587), cfg.get("security", "starttls")
    ctx = ssl.create_default_context()
    smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) if security == "ssl" else smtplib.SMTP(host, port, timeout=timeout)
    try:
        if security == "starttls":
            smtp.starttls(context=ctx)
        if cfg.get("username"):
            smtp.login(cfg["username"], password)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except smtplib.SMTPException:
            pass


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
        now = utcnow()
        if reminder and ev.acked_by:
            return  # someone is on it: no more reminders
        if any(x.matches(ev, now) for x in s.execute(select(AlertSilence).where(AlertSilence.until > now)).scalars()):
            ev.notified_at, ev.notify_error = now, ""   # silenced: kept in history, nothing sent
            return
        status = "resolved" if resolved else "reminder" if reminder else "firing"
        self.deliver(s, rule, ev, format_message(ev, rule, resolved, self.public_url(), reminder), status)
        ev.notified_at = now

    def deliver(self, s: Session, rule: AlertRule, ev: AlertEvent, text: str, status: str) -> None:
        errors = []
        payload = {"status": status, "rule": rule.name, "severity": ev.severity, "title": ev.title, "message": ev.message,
                   "cluster": ev.cluster_name, "resource": ev.resource, "key": ev.key, "event_id": ev.id,
                   "first_seen": _aware(ev.first_seen).isoformat() if ev.first_seen else None,
                   "acked_by": ev.acked_by, "url": self.public_url() or None, "text": html_to_text(text)}
        for ch in s.execute(select(AlertChannel).where(AlertChannel.id.in_(rule.channel_ids or []),
                                                       AlertChannel.enabled.is_(True))).scalars():
            try:
                self.send(ch, text, payload)
            except Exception as e:
                errors.append(f"{ch.name}: {e}")
        ev.notify_error = "; ".join(errors)[:1000]

    def send(self, ch: AlertChannel, text: str, payload: Optional[Dict[str, Any]] = None) -> None:
        cfg = ch.config or {}
        secret = self.decrypt(ch.secret_encrypted) if ch.secret_encrypted else ""
        if ch.type == "telegram":
            telegram_send(secret, str(cfg.get("chat_id", "")), text)
        elif ch.type == "slack":
            slack_send(secret, text)
        elif ch.type == "webhook":
            webhook_send(cfg["url"], secret, payload or {"status": "test", "text": html_to_text(text)})
        elif ch.type == "email":
            email_send(cfg, secret, text)
        else:
            raise RuntimeError(f"unsupported channel type {ch.type}")


class _Target:
    def __init__(self, id: int, name: str, kind: str):
        self.id, self.name, self.kind = id, name, kind


# --- API ------------------------------------------------------------------------------

class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str = Field("telegram", pattern="^(telegram|slack|webhook|email)$")
    enabled: bool = True
    chat_id: str = Field("", max_length=64)                      # telegram
    bot_token: Optional[str] = Field(None, max_length=200, description="Telegram bot token. Omit to keep the stored one.")
    # The secret of the other types: Slack incoming-webhook URL, webhook signing secret, SMTP password. Omit to keep it.
    secret: Optional[str] = Field(None, max_length=500)
    url: str = Field("", max_length=500)                          # webhook
    host: str = Field("", max_length=253)                         # email (SMTP)
    port: int = Field(587, ge=1, le=65535)
    security: str = Field("starttls", pattern="^(starttls|ssl|none)$")
    username: str = Field("", max_length=200)
    sender: str = Field("", max_length=200)
    recipients: List[str] = Field(default_factory=list, max_length=50)


EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def channel_config(body: ChannelBody, has_secret: bool) -> Dict[str, Any]:
    """Non-secret settings per type, checked. `has_secret`: a secret is given now or already stored."""
    if body.type == "telegram":
        if not body.chat_id:
            raise HTTPException(status_code=400, detail="Chat ID is required.")
        if not has_secret:
            raise HTTPException(status_code=400, detail="bot token is required")
        return {"chat_id": body.chat_id}
    if body.type == "slack":
        if not has_secret:
            raise HTTPException(status_code=400, detail="The Slack webhook URL is required.")
        if body.secret and not body.secret.startswith("https://hooks.slack.com/"):
            raise HTTPException(status_code=400, detail="A Slack incoming-webhook URL starts with https://hooks.slack.com/")
        return {}
    if body.type == "webhook":
        if not re.match(r"^https?://[^\s]+$", body.url):
            raise HTTPException(status_code=400, detail="The webhook URL must start with https:// (or http://).")
        return {"url": body.url}
    recipients = [x.strip() for x in body.recipients if x.strip()]
    if not body.host or not EMAIL_RE.match(body.sender) or not recipients or not all(EMAIL_RE.match(x) for x in recipients):
        raise HTTPException(status_code=400, detail="Email needs the SMTP server, a From address and at least one valid To address.")
    return {"host": body.host, "port": body.port, "security": body.security, "username": body.username,
            "sender": body.sender, "recipients": recipients}


class SilenceBody(BaseModel):
    event_id: Optional[int] = None          # silence this alert (and its return); with scope "rule": all of its rule
    rule_id: Optional[int] = None
    scope: str = Field("event", pattern="^(event|rule)$")
    minutes: int = Field(60, ge=5, le=60 * 24 * 30)
    reason: str = Field("", max_length=300)


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
        cfg = ch.config or {}
        return {"id": ch.id, "name": ch.name, "type": ch.type, "chat_id": cfg.get("chat_id", ""), "enabled": ch.enabled,
                "token_set": bool(ch.secret_encrypted), "secret_set": bool(ch.secret_encrypted),
                **{k: cfg[k] for k in ("url", "host", "port", "security", "username", "sender", "recipients") if k in cfg}}

    def rule_view(x: AlertRule) -> dict:
        return {k: getattr(x, k) for k in ("id", "name", "enabled", "type", "params", "cluster_id", "severity", "for_minutes",
                                           "repeat_minutes", "notify_resolved", "ai_summary", "channel_ids")}

    def event_view(e: AlertEvent) -> dict:
        iso = lambda d: _aware(d).isoformat() if d else None
        return {"id": e.id, "rule_id": e.rule_id, "rule": e.rule_name, "cluster_id": e.cluster_id, "cluster": e.cluster_name,
                "severity": e.severity, "state": e.state, "title": e.title, "message": e.message, "resource": e.resource,
                "first_seen": iso(e.first_seen), "last_seen": iso(e.last_seen), "fired_at": iso(e.fired_at),
                "resolved_at": iso(e.resolved_at), "notified_at": iso(e.notified_at), "notify_error": e.notify_error,
                "ai_summary": e.ai_summary, "haproxy_id": e.haproxy_id, "acked_by": e.acked_by, "acked_at": iso(e.acked_at)}

    def silence_view(x: AlertSilence) -> dict:
        return {"id": x.id, "rule_id": x.rule_id, "cluster_id": x.cluster_id, "haproxy_id": x.haproxy_id, "key": x.key,
                "title": x.title, "reason": x.reason, "until": _aware(x.until).isoformat(), "created_by": x.created_by,
                "created_at": _aware(x.created_at).isoformat() if x.created_at else None}

    def visible_event(p, s: Session, eid: int) -> AlertEvent:
        ev = s.get(AlertEvent, eid)
        if ev is None or not (p.can("cluster.view", ev.cluster_id) if ev.cluster_id is not None
                              else ev.haproxy_id is not None and p.can("haproxy.view", haproxy_id=ev.haproxy_id)):
            raise HTTPException(status_code=404, detail="alert not found")
        return ev

    # channels (settings.manage)
    @r.get("/channels")
    def list_channels(p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> list:
        return [channel_view(c) for c in s.execute(select(AlertChannel).order_by(AlertChannel.name)).scalars()]

    @r.get("/channels/names")
    def channel_names(p=Depends(current), s: Session = Depends(db_session)) -> list:
        return [{"id": c.id, "name": c.name, "type": c.type} for c in s.execute(select(AlertChannel)).scalars()]

    @r.post("/channels")
    def create_channel(body: ChannelBody, request: Request, p=Depends(require("settings.manage")), s: Session = Depends(db_session)) -> dict:
        secret = body.bot_token if body.type == "telegram" else body.secret
        ch = AlertChannel(name=body.name, type=body.type, config=channel_config(body, bool(secret)), enabled=body.enabled,
                          secret_encrypted=crypto().encrypt(secret) if secret else "")
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
        secret = body.bot_token if body.type == "telegram" else body.secret
        keep = body.type == ch.type and bool(ch.secret_encrypted)   # a stored secret only counts for the same type
        ch.config = channel_config(body, bool(secret) or keep)
        ch.name, ch.enabled, ch.type = body.name, body.enabled, body.type
        if secret:
            ch.secret_encrypted = crypto().encrypt(secret)
        elif not keep:
            ch.secret_encrypted = ""
        s.commit()
        audit(s, request, p, "alert-channel-update", ch.name, params={"token_changed": bool(secret)})
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
            engine_ref["engine"].send(ch, f"✅ <b>k10y test message</b>\nChannel “{html.escape(ch.name)}” is working. Sent by {html.escape(p.user.username)}.",
                                      {"status": "test", "text": f"k10y test message: channel {ch.name} is working.", "channel": ch.name})
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
        now = utcnow()
        silences = list(s.execute(select(AlertSilence).where(AlertSilence.until > now)).scalars())
        events = []
        for e in s.execute(q).scalars():
            v = event_view(e)
            hit = next((x for x in silences if x.matches(e, now)), None) if e.state != "resolved" else None
            v["silenced_until"] = _aware(hit.until).isoformat() if hit else None
            events.append(v)
        eng = engine_ref.get("engine")
        return {"events": events, "engine": {"last_run": eng.last_run.isoformat() if eng and eng.last_run else None,
                                             "error": eng.last_error if eng else "", "interval_s": EVAL_SECONDS}}

    @r.post("/events/{eid}/ack")
    def ack_event(eid: int, request: Request, p=Depends(current), s: Session = Depends(db_session)) -> dict:
        """'I'm on it': stops reminders for this alert and tells the rule's channels who took it."""
        ev = visible_event(p, s, eid)
        if ev.state == "resolved":
            raise HTTPException(status_code=400, detail="This alert is already resolved.")
        if not ev.acked_by:
            ev.acked_by, ev.acked_at = p.user.username, utcnow()
            rule = s.get(AlertRule, ev.rule_id)
            if rule is not None and ev.state == "firing" and engine_ref.get("engine"):
                text = f"👀 <b>Acknowledged</b> by {html.escape(p.user.username)} · <b>{html.escape(ev.cluster_name or 'hub')}</b>\n{html.escape(ev.title)}"
                engine_ref["engine"].deliver(s, rule, ev, text, "acknowledged")
            s.commit()
            audit(s, request, p, "alert-ack", ev.title[:200])
        return event_view(ev)

    @r.get("/silences")
    def list_silences(p=Depends(current), s: Session = Depends(db_session)) -> list:
        return [silence_view(x) for x in s.execute(select(AlertSilence).where(AlertSilence.until > utcnow())
                                                    .order_by(AlertSilence.until)).scalars()]

    @r.post("/silences")
    def create_silence(body: SilenceBody, request: Request, p=Depends(require("alerts.manage")), s: Session = Depends(db_session)) -> dict:
        x = AlertSilence(until=utcnow() + timedelta(minutes=body.minutes), reason=body.reason, created_by=p.user.username)
        if body.event_id is not None:
            ev = visible_event(p, s, body.event_id)
            x.rule_id = ev.rule_id
            if body.scope == "event":
                x.key, x.cluster_id, x.haproxy_id, x.title = ev.key, ev.cluster_id, ev.haproxy_id, f"{ev.title} ({ev.cluster_name})"
            else:
                x.title = f"Every alert of rule {ev.rule_name}"
        elif body.rule_id is not None:
            rule = s.get(AlertRule, body.rule_id)
            if rule is None:
                raise HTTPException(status_code=404, detail="rule not found")
            x.rule_id, x.title = rule.id, f"Every alert of rule {rule.name}"
        else:
            raise HTTPException(status_code=400, detail="Choose an alert or a rule to silence.")
        s.add(x)
        s.commit()
        audit(s, request, p, "alert-silence", x.title[:200], params={"minutes": body.minutes, "reason": body.reason})
        return silence_view(x)

    @r.delete("/silences/{sid}")
    def delete_silence(sid: int, request: Request, p=Depends(require("alerts.manage")), s: Session = Depends(db_session)) -> dict:
        x = s.get(AlertSilence, sid)
        if x is None:
            raise HTTPException(status_code=404, detail="silence not found")
        x.until = utcnow()
        s.commit()
        audit(s, request, p, "alert-unsilence", x.title[:200])
        return {"ok": True}

    return r
