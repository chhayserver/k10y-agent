"""Copy every activity-log entry to a SIEM or files, and keep the database's log to a set age.

Entries are caught as they're inserted (so shells and port-forwards are included) and sent by a background thread:
  - webhook: JSON batches POSTed every few seconds, optionally signed (X-K10y-Signature: sha256=<HMAC of the body>)
  - syslog: RFC 5424 lines with the entry as JSON, over UDP, TCP or TLS
  - file: one JSON line per entry in <data_dir>/audit/audit-YYYY-MM-DD.jsonl, old files removed after N days
Setting "audit_export": {retention_days, webhook: {enabled, url, secret}, syslog: {enabled, host, port, protocol},
file: {enabled, keep_days}}. Secrets are stored encrypted.
"""

import hashlib
import hmac
import json
import logging
import queue
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from sqlalchemy import delete, event

from .db import AuditEntry, Setting

log = logging.getLogger("k10y.audit_export")
SETTING = "audit_export"
DEFAULTS: Dict[str, Any] = {"retention_days": 0, "webhook": {"enabled": False, "url": "", "secret": ""},
                            "syslog": {"enabled": False, "host": "", "port": 514, "protocol": "udp"},
                            "file": {"enabled": False, "keep_days": 30}}


def entry_dict(a: AuditEntry) -> Dict[str, Any]:
    t = a.time or datetime.now(timezone.utc)
    return {"id": a.id, "time": (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).isoformat(), "user": a.username,
            "cluster": a.cluster_name or None, "action": a.action, "target": a.target, "ok": a.ok, "dry_run": a.dry_run,
            "message": a.message, "params": a.params or {}, "client_ip": a.client_ip, "source": "k10y"}


def syslog_line(e: Dict[str, Any], hostname: str = "k10y-hub") -> bytes:
    """RFC 5424: <PRI>1 TIMESTAMP HOST APP PROCID MSGID - MSG (facility local0, info or warning)."""
    pri = 16 * 8 + (6 if e["ok"] else 4)
    return f"<{pri}>1 {e['time']} {hostname} k10y - {e['action'][:32]} - {json.dumps(e, default=str)}\n".encode()


class Exporter:
    def __init__(self, db_factory: Callable, decrypt: Callable[[str], str], data_dir: str):
        self.db_factory, self.decrypt, self.data_dir = db_factory, decrypt, Path(data_dir)
        self.q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=50_000)
        self._cfg: Dict[str, Any] = {}
        self._cfg_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error = ""
        self.sent = 0
        self._pruned = 0.0

    # --- capture -------------------------------------------------------------------------
    def listen(self) -> None:
        @event.listens_for(AuditEntry, "after_insert")
        def _captured(mapper, connection, target: AuditEntry) -> None:   # noqa: ARG001
            if self.enabled():
                try:
                    self.q.put_nowait(entry_dict(target))
                except queue.Full:
                    self.last_error = "export queue full: entries dropped (is the destination down?)"

    def config(self, fresh: bool = False) -> Dict[str, Any]:
        if fresh or time.time() - self._cfg_at > 30:
            with self.db_factory() as s:
                row = s.get(Setting, SETTING)
                self._cfg = merge(DEFAULTS, (row.value or {}) if row else {})
            self._cfg_at = time.time()
        return self._cfg

    def enabled(self) -> bool:
        c = self._cfg or DEFAULTS
        return any(c[k]["enabled"] for k in ("webhook", "syslog", "file"))

    # --- send ----------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self.config(fresh=True)
            self._thread = threading.Thread(target=self._loop, name="audit-export", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            batch: List[Dict[str, Any]] = []
            try:
                batch.append(self.q.get(timeout=5))
                while len(batch) < 500:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            try:
                cfg = self.config()
                if batch:
                    self.send(cfg, batch)
                    self.sent += len(batch)
                    self.last_error = ""
                if time.time() - self._pruned > 3600:
                    self._pruned = time.time()
                    self.prune(cfg)
            except Exception as e:     # keep going; the next batch may work
                self.last_error = str(e)[:300]
                log.warning("audit export failed: %s", e)

    def send(self, cfg: Dict[str, Any], batch: List[Dict[str, Any]]) -> None:
        errors = []
        for name, fn in (("webhook", self._webhook), ("syslog", self._syslog), ("file", self._file)):
            if cfg[name]["enabled"]:
                try:
                    fn(cfg[name], batch)
                except Exception as e:
                    errors.append(f"{name}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))

    def _webhook(self, c: Dict[str, Any], batch: List[Dict[str, Any]]) -> None:
        body = json.dumps({"entries": batch}, default=str).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "k10y-audit"}
        secret = self.decrypt(c["secret"]) if c.get("secret") else ""
        if secret:
            headers["X-K10y-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        r = httpx.post(c["url"], content=body, headers=headers, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")

    def _syslog(self, c: Dict[str, Any], batch: List[Dict[str, Any]]) -> None:
        lines = [syslog_line(e) for e in batch]
        host, port, proto = c["host"], int(c.get("port") or 514), c.get("protocol", "udp")
        if proto == "udp":
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for line in lines:
                    sock.sendto(line[:8192], (host, port))
            return
        raw = socket.create_connection((host, port), timeout=10)
        conn = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if proto == "tls" else raw
        with conn:
            for line in lines:   # octet counting (RFC 6587), what syslog over TCP/TLS expects
                conn.sendall(f"{len(line)} ".encode() + line)

    def _file(self, c: Dict[str, Any], batch: List[Dict[str, Any]]) -> None:
        folder = self.data_dir / "audit"
        folder.mkdir(parents=True, exist_ok=True)
        by_day: Dict[str, List[str]] = {}
        for e in batch:
            by_day.setdefault(e["time"][:10], []).append(json.dumps(e, default=str))
        for day, rows in by_day.items():
            with open(folder / f"audit-{day}.jsonl", "a", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")

    # --- retention ---------------------------------------------------------------------
    def prune(self, cfg: Dict[str, Any]) -> None:
        days = int(cfg.get("retention_days") or 0)
        if days > 0:
            with self.db_factory() as s:
                n = s.execute(delete(AuditEntry).where(AuditEntry.time < datetime.now(timezone.utc) - timedelta(days=days))).rowcount
                s.commit()
            if n:
                log.info("activity log: removed %s entries older than %s days", n, days)
        keep = int(cfg["file"].get("keep_days") or 0)
        if cfg["file"]["enabled"] and keep > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=keep)).strftime("%Y-%m-%d")
            for f in (self.data_dir / "audit").glob("audit-*.jsonl"):
                if f.stem[len("audit-"):] < cutoff:
                    f.unlink(missing_ok=True)


def merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
