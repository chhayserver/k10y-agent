"""Background work for HAProxy instances, run by the hub.

Every 10 seconds, for each instance:
  * sample live stats into an in-memory history (one hour) for the charts and alert rules;
  * every 30 s: record the configuration if it changed (config history), refresh certificate
    details, and run the Kubernetes -> backend syncs.
A UDP syslog receiver collects HAProxy logs (``log <hub>:5514``) per ``log-tag``.
"""

import logging
import re
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import haproxy as hx
from .db import Cluster, utcnow

log = logging.getLogger("k10y.haproxy")
SAMPLE_S = 10
HISTORY_POINTS = 360          # 1 hour at 10 s
KEEP_MINUTES_MS = 7 * 86_400_000   # one-minute averages kept in the database for a week
SLOW_EVERY = 3                # config / certs / syncs every 30 s
SYNC_PREFIX = "k8s-"          # servers k10y manages for a Kubernetes sync


def strip_header(text: str) -> str:
    """Drop the Data Plane API's managed-file header so an old version can be posted again."""
    return "\n".join(l for l in text.splitlines() if not re.match(r"^# (_md5hash|_version)=|^# Dataplaneapi managed File|^# changing file directly", l)).lstrip("\n") + "\n"


class Sample:
    """Rates computed from two consecutive stats readings."""

    @staticmethod
    def take(stats: List[Dict[str, Any]], prev: Optional[Dict[str, Any]], dt: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        counters: Dict[str, Any] = {}
        point: Dict[str, Any] = {"f": {}, "b": {}}
        for it in stats:
            kind, s = it.get("type"), it.get("stats") or {}
            be = it.get("name") or ""            # frontends and backends: their own name
            if kind == "frontend":
                key = f"f:{be}"
                total = sum(int(s.get(k) or 0) for k in ("hrsp_1xx", "hrsp_2xx", "hrsp_3xx", "hrsp_4xx", "hrsp_5xx", "hrsp_other"))
                e5 = int(s.get("hrsp_5xx") or 0)
                counters[key] = (total, e5, int(s.get("bin") or 0), int(s.get("bout") or 0))
                p = (prev or {}).get(key)
                d_total = max(0, total - p[0]) if p else 0
                d_e5 = max(0, e5 - p[1]) if p else 0
                point["f"][be] = {"req": round(d_total / dt, 2) if p and dt else float(s.get("req_rate") or 0),
                                  "sess": int(s.get("scur") or 0), "slim": int(s.get("slim") or 0),
                                  "e5": round(d_e5 / dt, 3) if p and dt else 0.0,
                                  "e5_pct": round(100.0 * d_e5 / d_total, 1) if d_total else 0.0,
                                  "in": round((counters[key][2] - p[2]) / dt) if p and dt else 0,
                                  "out": round((counters[key][3] - p[3]) / dt) if p and dt else 0}
            elif kind == "backend":
                key = f"b:{be}"
                e5, tot = int(s.get("hrsp_5xx") or 0), int(s.get("stot") or 0)
                resp = sum(int(s.get(k) or 0) for k in ("hrsp_1xx", "hrsp_2xx", "hrsp_3xx", "hrsp_4xx", "hrsp_5xx", "hrsp_other"))
                counters[key] = (tot, e5, resp)
                p = (prev or {}).get(key)
                d_e5 = max(0, e5 - p[1]) if p else 0
                d_resp = max(0, resp - p[2]) if p and len(p) > 2 else 0
                point["b"][be] = {"req": round(max(0, tot - p[0]) / dt, 2) if p and dt else float(s.get("rate") or 0),
                                  "rq": round(d_resp / dt, 2) if p and dt else 0.0,      # HTTP responses / s (per route)
                                  "sess": int(s.get("scur") or 0), "rtime": int(s.get("rtime") or 0),
                                  "queue": int(s.get("qcur") or 0),
                                  "e5": round(d_e5 / dt, 3) if p and dt else 0.0,
                                  "e5_pct": round(min(100.0, 100.0 * d_e5 / d_resp), 1) if d_resp else 0.0}
        return point, counters


class HAProxyMonitor:
    def __init__(self, db_factory: Callable[[], Session], decrypt: Callable[[str], str],
                 agent_get: Callable[[Cluster, str], Any], make_client: Optional[Callable[..., hx.DataPlane]] = None):
        self.db_factory, self.decrypt, self.agent_get = db_factory, decrypt, agent_get
        self.make = make_client or (lambda inst, pw: hx.DataPlane(inst.url, inst.username, pw, inst.verify_tls))
        self.history: Dict[int, Deque[Tuple[int, Dict[str, Any]]]] = {}
        self.latest: Dict[int, Dict[str, Any]] = {}       # instance id -> {t, name, stats, error, certs}
        self._counters: Dict[int, Tuple[float, Dict[str, Any]]] = {}
        self._loops = 0
        self._minute: Dict[int, Tuple[int, List[Dict[str, Any]]]] = {}   # instance id -> (minute start ms, points)
        self._pruned = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="haproxy-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(3)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("HAProxy monitor cycle failed")
            self._stop.wait(SAMPLE_S)

    def run_once(self, slow: Optional[bool] = None) -> None:
        slow = (self._loops % SLOW_EVERY == 0) if slow is None else slow
        self._loops += 1
        with self.db_factory() as s:
            instances = list(s.execute(select(hx.HAProxyInstance)).scalars())
            known = {i.id for i in instances}
            for iid in list(self.latest):
                if iid not in known:
                    self.latest.pop(iid, None)
                    self.history.pop(iid, None)
            for inst in instances:
                self._sample(s, inst, slow)

    # --- per instance ---------------------------------------------------------
    def _sample(self, s: Session, inst: hx.HAProxyInstance, slow: bool) -> None:
        now = time.time()
        try:
            with self.make(inst, self.decrypt(inst.password_encrypted)) as dp:
                stats = dp.stats()
                last = self._counters.get(inst.id)
                point, counters = Sample.take(stats, last[1] if last else None, now - last[0] if last else 0)
                self._counters[inst.id] = (now, counters)
                if last:
                    with self.lock:
                        self.history.setdefault(inst.id, deque(maxlen=HISTORY_POINTS)).append((int(now * 1000), point))
                    self._add_minute(s, inst.id, int(now * 1000), point)
                entry = self.latest.setdefault(inst.id, {})
                entry.update({"t": now, "name": inst.name, "stats": stats, "point": point, "error": ""})
                if slow:
                    self._record_config(s, inst, dp)
                    try:
                        entry["certs"] = dp.certificates()
                    except hx.DataPlaneError:
                        entry["certs"] = entry.get("certs", [])
                    self.run_syncs(s, inst, dp)
        except hx.DataPlaneError as e:
            self.latest.setdefault(inst.id, {}).update({"t": now, "name": inst.name, "error": str(e)})

    def _add_minute(self, s: Session, iid: int, t_ms: int, point: Dict[str, Any]) -> None:
        """Collect samples per minute; when a minute ends, store its average for the longer chart ranges."""
        minute = t_ms - t_ms % 60_000
        start, points = self._minute.get(iid, (minute, []))
        if start != minute and points:
            s.add(hx.HAProxySample(instance_id=iid, t=start, data=average(points)))
            if time.time() - self._pruned > 3600:
                s.execute(delete(hx.HAProxySample).where(hx.HAProxySample.t < t_ms - KEEP_MINUTES_MS))
                self._pruned = time.time()
            s.commit()
            points = []
        self._minute[iid] = (minute, points + [point])

    def _record_config(self, s: Session, inst: hx.HAProxyInstance, dp: hx.DataPlane) -> None:
        version = dp.version()
        last = s.execute(select(hx.HAProxyConfigVersion).where(hx.HAProxyConfigVersion.instance_id == inst.id)
                         .order_by(hx.HAProxyConfigVersion.id.desc()).limit(1)).scalar_one_or_none()
        if last and last.version == version:
            return
        text = dp.raw()
        if last and strip_header(last.text) == strip_header(text):
            last.version = version
        else:
            s.add(hx.HAProxyConfigVersion(instance_id=inst.id, version=version, text=text,
                                          source="initial" if last is None else "external",
                                          username="" if last is None else "outside k10y",
                                          summary="configuration when k10y first saw it" if last is None else "changed outside k10y"))
        s.commit()

    def snapshot(self, s: Session, inst: hx.HAProxyInstance, dp: hx.DataPlane, username: str, summary: str, source: str = "k10y") -> None:
        """Store the configuration right after k10y applied a change."""
        s.add(hx.HAProxyConfigVersion(instance_id=inst.id, version=dp.version(), text=dp.raw(), source=source,
                                      username=username, summary=summary[:2000]))
        s.commit()

    # --- Kubernetes -> backend sync ---------------------------------------------
    def desired_servers(self, s: Session, sync: hx.HAProxyK8sSync) -> Dict[str, Tuple[str, int]]:
        cluster = s.get(Cluster, sync.cluster_id)
        if cluster is None:
            raise RuntimeError("cluster not found")
        svcs = self.agent_get(cluster, f"services?namespace={sync.namespace}")
        svc = next((x for x in svcs if x["name"] == sync.service), None)
        if svc is None:
            raise RuntimeError(f"service {sync.namespace}/{sync.service} not found")
        ports = svc.get("ports") or []
        port = next((p for p in ports if sync.port in (str(p.get("name") or ""), str(p.get("port")))), None) if sync.port else (ports[0] if ports else None)
        if port is None:
            raise RuntimeError(f"service has no port {sync.port or ''}".strip())
        out: Dict[str, Tuple[str, int]] = {}
        if sync.mode == "nodeport":
            if not port.get("node_port"):
                raise RuntimeError("the service has no NodePort; use type NodePort or the pod IPs mode")
            for n in self.agent_get(cluster, "nodes"):
                if n.get("ready") and n.get("internal_ip") and not n.get("unschedulable"):
                    out[_server_name(n["name"])] = (n["internal_ip"], int(port["node_port"]))
        else:
            target = str(port.get("target_port") or port.get("port"))
            if not target.isdigit():
                raise RuntimeError("named target ports aren't supported in pod IPs mode; use a numeric targetPort")
            detail = self.agent_get(cluster, f"resource?kind=Service&namespace={sync.namespace}&name={sync.service}")
            for p in detail.get("pods") or []:
                if p.get("pod_ip") and p.get("phase") == "Running" and p.get("ready_containers") == p.get("total_containers"):
                    out[_server_name(p["name"])] = (p["pod_ip"], int(target))
        return out

    def run_syncs(self, s: Session, inst: hx.HAProxyInstance, dp: hx.DataPlane, only: Optional[int] = None) -> List[str]:
        results = []
        syncs = s.execute(select(hx.HAProxyK8sSync).where(hx.HAProxyK8sSync.instance_id == inst.id)).scalars()
        for sync in syncs:
            if (only is not None and sync.id != only) or (only is None and not sync.enabled):
                continue
            try:
                desired = self.desired_servers(s, sync)
                if not desired:
                    raise RuntimeError("no ready nodes or pods found; servers left unchanged")
                be = next((b for b in dp.backends() if b["name"] == sync.backend), None)
                if be is None:
                    raise RuntimeError(f"backend {sync.backend} not found")
                current = {n: (v.get("address"), int(v.get("port") or 0)) for n, v in (be.get("servers") or {}).items() if n.startswith(SYNC_PREFIX)}
                ops = [hx.Op(op="delete", kind="server", backend=sync.backend, name=n) for n in current if n not in desired]
                for n, (addr, port) in desired.items():
                    if n not in current:
                        ops.append(hx.Op(op="create", kind="server", backend=sync.backend, name=n, data={"address": addr, "port": port, "check": True}))
                    elif current[n] != (addr, port):
                        ops.append(hx.Op(op="update", kind="server", backend=sync.backend, name=n, data={"address": addr, "port": port}))
                if ops:
                    r = hx.plan_and_apply(dp, ops, apply=True)
                    msg = "; ".join(r["summary"])
                    self.snapshot(s, inst, dp, "kubernetes sync", f"sync {sync.namespace}/{sync.service}: {msg}", source="sync")
                else:
                    msg = f"in sync ({len(desired)} servers)"
                sync.last_result, sync.last_error = msg, ""
            except Exception as e:     # one failing sync must not stop the others
                sync.last_error = str(e)[:1000]
            sync.last_sync_at = utcnow()
            s.commit()
            results.append(sync.last_error or sync.last_result)
        return results

    # --- reads for the API -------------------------------------------------------
    def series(self, iid: int, since_ms: int = 0) -> List[Tuple[int, Dict[str, Any]]]:
        with self.lock:
            return [p for p in self.history.get(iid, ()) if p[0] > since_ms]


def average(points: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Average a list of samples field by field (a name missing from a sample counts as absent, not zero)."""
    out: Dict[str, Any] = {"f": {}, "b": {}}
    for kind in ("f", "b"):
        sums: Dict[str, Dict[str, float]] = {}
        counts: Dict[str, int] = {}
        for p in points:
            for name, vals in (p.get(kind) or {}).items():
                acc = sums.setdefault(name, {})
                counts[name] = counts.get(name, 0) + 1
                for k, v in vals.items():
                    acc[k] = acc.get(k, 0.0) + float(v or 0)
        for name, acc in sums.items():
            out[kind][name] = {k: round(v / counts[name], 3) for k, v in acc.items()}
    return out


def _server_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9._-]", "-", name)[: 64 - len(SYNC_PREFIX)]
    return f"{SYNC_PREFIX}{n}"


# --- syslog receiver -------------------------------------------------------------------

SYSLOG_RE = re.compile(r"^<(\d{1,3})>(?:1 \S+ \S+ (\S+) \S+ \S+ \S+ |(?:\w{3}\s+\d+\s[\d:]+\s)?(?:(\S+?)(?:\[\d+\])?:\s))(.*)$", re.S)
HTTPLOG_RE = re.compile(
    r'^(?P<client>\S+?):(?P<cport>\d+) \[(?P<date>[^\]]+)\] (?P<frontend>\S+) (?P<backend>[^/\s]+)/(?P<server>\S+) '
    r'(?P<tq>-?\d+)/(?P<tw>-?\d+)/(?P<tc>-?\d+)/(?P<tr>-?\d+)/\+?(?P<ta>-?\d+) (?P<status>-?\d+) \+?(?P<bytes>\d+) '
    r'\S+ \S+ (?P<term>\S+) .*?"(?P<request>[^"]*)"\s*$')


def parse_line(raw: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    m = SYSLOG_RE.match(raw.strip())
    if not m:
        return None
    tag = m.group(2) or m.group(3) or "haproxy"
    msg = m.group(4).strip()
    entry: Dict[str, Any] = {"t": int(time.time() * 1000), "msg": msg}
    h = HTTPLOG_RE.match(msg)
    if h:
        d = h.groupdict()
        entry.update({"client": d["client"], "frontend": d["frontend"], "backend": d["backend"], "server": d["server"],
                      "status": int(d["status"]), "bytes": int(d["bytes"]), "ms": int(d["ta"]), "term": d["term"],
                      "request": d["request"]})
    return tag, entry


class SyslogReceiver:
    """UDP syslog listener; keeps the last lines per log tag."""

    def __init__(self, port: int, keep: int = 3000):
        self.port, self.keep = port, keep
        self.lines: Dict[str, Deque[Dict[str, Any]]] = {}
        self.seq = 0
        self.lock = threading.Lock()
        self.received = 0
        self._sock: Optional[socket.socket] = None

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("0.0.0.0", self.port))
        threading.Thread(target=self._loop, name="syslog", daemon=True).start()
        log.info("listening for HAProxy logs on udp/%d", self.port)

    def _loop(self) -> None:
        while True:
            try:
                data, _ = self._sock.recvfrom(65535)
            except OSError:
                return
            self.add(data.decode("utf-8", "replace"))

    def add(self, raw: str) -> None:
        parsed = parse_line(raw)
        if not parsed:
            return
        tag, entry = parsed
        with self.lock:
            self.seq += 1
            self.received += 1
            entry["seq"] = self.seq
            self.lines.setdefault(tag, deque(maxlen=self.keep)).append(entry)

    def read(self, tag: str, after: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        with self.lock:
            return [e for e in self.lines.get(tag, ()) if e["seq"] > after][-limit:]

    def tags(self) -> List[str]:
        with self.lock:
            return sorted(self.lines)
