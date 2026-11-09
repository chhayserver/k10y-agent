"""Live numbers for Nginx, which the open-source version doesn't report per site or upstream itself:

- the access log, tailed and counted in 10-second buckets per site, route (location) and upstream server
  (best with the k10y log format below; the standard "combined" format gives totals and routes only);
- stub_status (connections, total requests) when the configuration has it;
- an active check of every upstream server (TCP connect), every 10 seconds;
- certificate expiry of every ssl_certificate.
"""

import json
import os
import re
import socket
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import httpx

from . import model

BUCKET_S = 10
KEEP_POINTS = 360           # one hour
RECENT_LINES = 2000

# Recommended: every field k10y uses, as JSON (Nginx escapes it). Add with `access_log <path> k10y;`.
K10Y_LOG_FORMAT = ("""log_format k10y escape=json '{"time":"$time_iso8601","host":"$host","server":"$server_name",'"""
                   """'"port":"$server_port","method":"$request_method","uri":"$request_uri","status":$status,'"""
                   """'"bytes":$body_bytes_sent,"rt":$request_time,"ua":"$upstream_addr","us":"$upstream_status",'"""
                   """'"urt":"$upstream_response_time","ip":"$remote_addr","referer":"$http_referer","agent":"$http_user_agent"}';""")

COMBINED = re.compile(r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<uri>\S+)[^"]*" (?P<status>\d{3}) '
                      r'(?P<bytes>\d+|-)(?: "(?P<referer>[^"]*)" "(?P<agent>[^"]*)")?(?: (?P<rt>[\d.]+))?')
ALIASES = {"status": ("status", "s", "code"), "uri": ("uri", "request_uri", "path", "u"), "host": ("host", "http_host", "h"),
           "server": ("server", "server_name"), "rt": ("rt", "request_time", "duration"), "ua": ("ua", "upstream_addr", "upstream"),
           "urt": ("urt", "upstream_response_time"), "method": ("method", "request_method", "m"), "ip": ("ip", "remote_addr", "client"),
           "bytes": ("bytes", "body_bytes_sent", "b"), "port": ("port", "server_port"), "us": ("us", "upstream_status")}


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """One access-log line (JSON, k10y or any with the usual names, or combined) -> fields k10y uses."""
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            raw = json.loads(line)
        except ValueError:
            return None
        e = {k: next((raw[a] for a in names if a in raw and raw[a] not in ("", "-")), None) for k, names in ALIASES.items()}
        e["time"] = raw.get("time") or raw.get("time_iso8601") or raw.get("t")
        e["referer"], e["agent"] = raw.get("referer"), raw.get("agent")
    else:
        m = COMBINED.match(line)
        if not m:
            return None
        e = {k: m.group(k) for k in ("ip", "time", "method", "uri", "status", "bytes", "referer", "agent", "rt")}
        e.update(host=None, server=None, ua=None, urt=None, port=None, us=None)
    try:
        e["status"] = int(e["status"])
    except (TypeError, ValueError):
        return None
    e["bytes"] = int(e["bytes"]) if str(e.get("bytes") or "").isdigit() else 0
    e["rt"] = float(e["rt"]) if _num(e.get("rt")) else None
    e["port"] = int(e["port"]) if str(e.get("port") or "").isdigit() else None
    return e


def _num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))] * 1000, 1)   # ms


class Bucket:
    def __init__(self, t: int):
        self.t, self.req, self.e4, self.e5, self.bytes, self.times = t, 0, 0, 0, 0, []
        self.groups: Dict[str, Dict[str, Dict[str, Any]]] = {"sites": {}, "locations": {}, "upstreams": {}, "servers": {}}

    def add(self, kind: str, key: str, status: int, rt: Optional[float]) -> None:
        g = self.groups[kind].setdefault(key, {"req": 0, "e5": 0, "e4": 0, "times": []})
        g["req"] += 1
        g["e5"] += status >= 500
        g["e4"] += 400 <= status < 500
        if rt is not None and len(g["times"]) < 2000:
            g["times"].append(rt)

    def point(self) -> Tuple[int, Dict[str, Any]]:
        rate = lambda n: round(n / BUCKET_S, 2)
        groups = {kind: {k: {"req": rate(v["req"]), "e5_pct": round(100 * v["e5"] / v["req"], 1) if v["req"] else 0.0,
                             "e4_pct": round(100 * v["e4"] / v["req"], 1) if v["req"] else 0.0, "p95": _pct(v["times"], 0.95)}
                         for k, v in g.items()} for kind, g in self.groups.items()}
        return self.t * 1000, {"req": rate(self.req), "e5_pct": round(100 * self.e5 / self.req, 1) if self.req else 0.0,
                               "e4_pct": round(100 * self.e4 / self.req, 1) if self.req else 0.0, "bytes": rate(self.bytes),
                               "p50": _pct(self.times, 0.5), "p95": _pct(self.times, 0.95), **groups}


class Metrics:
    """Background threads: log tailer, upstream checks, stub_status. `config()` gives the current model."""

    def __init__(self, config: Callable[[], Dict[str, Any]], stub_url: Callable[[], Optional[str]]):
        self.config, self.stub_url = config, stub_url
        self.points: Deque[Tuple[int, Dict[str, Any]]] = deque(maxlen=KEEP_POINTS)
        self.recent: Deque[Dict[str, Any]] = deque(maxlen=RECENT_LINES)
        self.health: Dict[str, Dict[str, Any]] = {}      # "host:port" -> {up, ms, error, since}
        self.resolved: Dict[str, List[str]] = {}
        self.stub: Dict[str, Any] = {}
        self.formats: Dict[str, str] = {}                 # log path -> "k10y" | "json" | "combined" | "unknown"
        self._bucket: Optional[Bucket] = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    # --- lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        for name, fn in (("nginx-logs", self._tail_loop), ("nginx-health", self._health_loop), ("nginx-buckets", self._bucket_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()

    # --- access log ---------------------------------------------------------------------
    def log_paths(self) -> List[str]:
        return sorted({a["path"] for a in self.config().get("access_logs") or []
                       if a["path"] not in ("off", "syslog") and "$" not in a["path"] and not a["path"].startswith("syslog:")})

    def _tail_loop(self) -> None:
        files: Dict[str, Tuple[Any, int]] = {}
        while not self._stop.is_set():
            for path in self.log_paths():
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                f, ino = files.get(path, (None, None))
                if f is None or ino != st.st_ino or f.tell() > st.st_size:       # new, rotated or truncated
                    if f:
                        f.close()
                    f = open(path, "r", encoding="utf-8", errors="replace")
                    if ino is None:
                        f.seek(0, os.SEEK_END)                                       # only new requests at start
                    files[path] = (f, st.st_ino)
                for line in f.readlines():
                    self.ingest(line, path)
            self._stop.wait(1)

    def ingest(self, line: str, path: str = "") -> None:
        e = parse_line(line)
        if e is None:
            self.formats.setdefault(path, "unknown")
            return
        self.formats[path] = "k10y" if e.get("ua") is not None or e.get("server") is not None else (
            "json" if line.lstrip().startswith("{") else "combined")
        cfg = self.config()
        s = model.pick_site(cfg.get("sites") or [], e.get("host") or "", e.get("server") or "", e.get("port"))
        loc = model.match_location(s["locations"], e.get("uri") or "/") if s else None
        e["site"], e["location"] = (s["id"] if s else None), (loc["id"] if loc else None)
        e["upstream"], e["upstream_server"] = None, None
        if e.get("ua"):
            last = str(e["ua"]).split(",")[-1].strip().split(" : ")[-1]       # the try that answered
            e["upstream"], e["upstream_server"] = model.upstream_for_address(cfg.get("upstreams") or [], last, self.resolved)
            e["upstream_addr"] = last
        with self._lock:
            self._seq += 1
            e["seq"] = self._seq
            self.recent.append(e)
            b = self._current()
            b.req += 1
            b.e5 += e["status"] >= 500
            b.e4 += 400 <= e["status"] < 500
            b.bytes += e["bytes"]
            if e["rt"] is not None and len(b.times) < 5000:
                b.times.append(e["rt"])
            for kind, key in (("sites", e["site"]), ("locations", e["location"]), ("upstreams", e["upstream"]),
                              ("servers", f"{e['upstream']}/{e['upstream_server']}" if e["upstream"] else None)):
                if key:
                    b.add(kind, key, e["status"], e["rt"])

    def _current(self) -> Bucket:
        t = int(time.time()) // BUCKET_S * BUCKET_S
        if self._bucket is None or self._bucket.t != t:
            if self._bucket is not None:
                self.points.append(self._bucket.point())
            self._bucket = Bucket(t)
        return self._bucket

    def _bucket_loop(self) -> None:
        """Close buckets on time even when no requests come in (so charts show zero, not a gap)."""
        last_stub = 0.0
        while not self._stop.wait(1):
            with self._lock:
                self._current()
            if time.time() - last_stub >= BUCKET_S:
                last_stub = time.time()
                self._poll_stub()

    def series(self, since_ms: int = 0) -> List[Tuple[int, Dict[str, Any]]]:
        with self._lock:
            return [p for p in self.points if p[0] > since_ms]

    def lines(self, after: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self.recent if e["seq"] > after][-limit:]

    # --- stub_status --------------------------------------------------------------------
    def _poll_stub(self) -> None:
        url = self.stub_url()
        if not url:
            return
        try:
            text = httpx.get(url, timeout=3).text
            nums = [int(x) for x in re.findall(r"\d+", text)]
            if len(nums) >= 7:
                prev = self.stub
                self.stub = {"active": nums[0], "accepts": nums[1], "handled": nums[2], "requests": nums[3], "reading": nums[4],
                             "writing": nums[5], "waiting": nums[6], "t": time.time()}
                if prev.get("requests") is not None and self.stub["t"] > prev["t"]:
                    self.stub["req_rate"] = round(max(0, nums[3] - prev["requests"]) / (self.stub["t"] - prev["t"]), 2)
        except (httpx.HTTPError, ValueError):
            self.stub = {"error": f"can't read {url}"}

    # --- upstream checks ----------------------------------------------------------------
    def _health_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_upstreams()
            except Exception:       # a bad config must not stop the checks
                pass
            self._stop.wait(10)

    def check_upstreams(self) -> None:
        targets = {(srv["host"], srv["port"]) for u in self.config().get("upstreams") or [] for srv in u["servers"]
                   if srv["port"] and not srv["host"].startswith("unix:")}
        for host, port in targets:
            key = f"{host}:{port}"
            started = time.monotonic()
            try:
                infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                self.resolved[host] = sorted({i[4][0] for i in infos})
                with socket.create_connection((host, port), timeout=2):
                    pass
                up, error = True, ""
            except OSError as e:
                up, error = False, str(e)[:200]
            prev = self.health.get(key, {})
            self.health[key] = {"up": up, "ms": round((time.monotonic() - started) * 1000, 1), "error": error,
                                "since": prev.get("since") if prev.get("up") == up else time.time(), "checked": time.time()}


def certificate(path: str) -> Dict[str, Any]:
    from datetime import datetime, timezone

    from cryptography import x509
    try:
        pem = open(path, "rb").read()
        cert = x509.load_pem_x509_certificate(pem)
    except (OSError, ValueError) as e:
        return {"path": path, "error": str(e)[:200]}
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    issuer = ", ".join(a.value for a in cert.issuer if a.oid._name in ("commonName", "organizationName"))
    return {"path": path, "dns_names": names, "issuer": issuer, "not_after": not_after.isoformat(),
            "days_left": int((not_after - datetime.now(timezone.utc)).total_seconds() // 86400)}
