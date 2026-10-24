"""HAProxy instances managed through the HAProxy Data Plane API (v3).

The hub talks to each instance's Data Plane API directly. Reads combine configuration and live
stats into one overview. Runtime changes (drain, maintenance, ready) apply instantly without a reload.
Configuration changes always run in a Data Plane API transaction: preview shows a diff of the
rendered config and HAProxy's own validation result; apply commits the transaction, which
validates again and reloads HAProxy.
"""

import difflib
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, utcnow

TIMEOUT = httpx.Timeout(20.0, connect=6.0)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ADMIN_STATES = ("ready", "drain", "maint")
BALANCE = ("roundrobin", "static-rr", "leastconn", "first", "source", "uri")
STAT_KEYS = ("status", "scur", "smax", "slim", "rate", "rate_max", "req_rate", "req_tot", "stot", "bin", "bout",
             "hrsp_2xx", "hrsp_3xx", "hrsp_4xx", "hrsp_5xx", "ereq", "econ", "eresp", "check_status", "check_desc",
             "check_duration", "lastchg", "downtime", "act", "bck", "weight", "qcur", "rtime", "ttime")
# Routing rules k10y can show and edit as simple "routes"; anything else is shown as-is, read-only.
ROUTE_CRITERIA = {"path_beg": "path_prefix", "path": "path", "hdr(host)": "host", "req.hdr(host)": "host", "src": "src"}
HEADER_RE = re.compile(r"^(?:req\.)?hdr\(([A-Za-z0-9-]+)\)$")


class HAProxyInstance(Base):
    __tablename__ = "haproxy_instances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(300))                       # Data Plane API base, e.g. http://lb1:5555
    username: Mapped[str] = mapped_column(String(128))
    password_encrypted: Mapped[str] = mapped_column(Text)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    log_tag: Mapped[str] = mapped_column(String(64), default="")        # syslog tag this HAProxy sends (log-tag)
    group_name: Mapped[str] = mapped_column(String(64), default="")     # instances in a group can get the same change


class HAProxyConfigVersion(Base):
    """Every configuration k10y has seen for an instance: applied here, changed elsewhere, or rolled back."""
    __tablename__ = "haproxy_config_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="k10y")     # k10y | external | initial | rollback | sync
    username: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HAProxyK8sSync(Base):
    """Keep a backend's servers in sync with a Kubernetes Service (node ports or pod IPs)."""
    __tablename__ = "haproxy_k8s_syncs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(Integer, index=True)
    backend: Mapped[str] = mapped_column(String(64))
    cluster_id: Mapped[int] = mapped_column(Integer)
    namespace: Mapped[str] = mapped_column(String(253))
    service: Mapped[str] = mapped_column(String(253))
    port: Mapped[str] = mapped_column(String(64), default="")           # service port name or number; empty = first
    mode: Mapped[str] = mapped_column(String(16), default="nodeport")   # nodeport | pods
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_result: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")


class DataPlaneError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _message(r: httpx.Response) -> str:
    try:
        d = r.json()
        msg = d.get("message") or str(d)
    except ValueError:
        msg = r.text
    # Data Plane API wraps haproxy -c output in msg="..." lines; keep the useful part.
    found = re.findall(r'msg="([^"]+)"', msg)
    return ("; ".join(found) if found else msg).strip()[:1500]


class DataPlane:
    """Small client for the Data Plane API v3."""

    def __init__(self, url: str, username: str, password: str, verify: bool = False, client: Optional[httpx.Client] = None):
        self.base = url.rstrip("/") + "/v3"
        self.c = client or httpx.Client(auth=(username, password), verify=verify, timeout=TIMEOUT)

    def close(self) -> None:
        self.c.close()

    def __enter__(self) -> "DataPlane":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    def call(self, method: str, path: str, tx: Optional[str] = None, json: Any = None, content: Optional[str] = None,
             params: Optional[Dict[str, Any]] = None, want_response: bool = False) -> Any:
        params = dict(params or {})
        if tx:
            params["transaction_id"] = tx
        headers = {"Content-Type": "text/plain"} if content is not None else None
        try:
            r = self.c.request(method, self.base + path, params=params, json=json, content=content, headers=headers)
        except httpx.HTTPError as e:
            raise DataPlaneError(502, f"cannot reach the Data Plane API: {e}")
        if r.status_code == 401:
            raise DataPlaneError(502, "the Data Plane API rejected the username or password")
        if r.status_code >= 400:
            raise DataPlaneError(400 if r.status_code < 500 else 502, _message(r))
        if want_response:
            return r
        if not r.content:
            return None
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    # config ---------------------------------------------------------------
    def version(self) -> int:
        return int(self.call("GET", "/services/haproxy/configuration/version"))

    def frontends(self, tx: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.call("GET", "/services/haproxy/configuration/frontends", tx, params={"full_section": "true"}) or []

    def backends(self, tx: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.call("GET", "/services/haproxy/configuration/backends", tx, params={"full_section": "true"}) or []

    def raw(self, tx: Optional[str] = None) -> str:
        d = self.call("GET", "/services/haproxy/configuration/raw", tx)
        return d.get("data", "") if isinstance(d, dict) else (d or "")

    def validate(self, text: str) -> None:
        self.call("POST", "/services/haproxy/configuration/raw", content=text,
                  params={"only_validate": "true", "skip_version": "true"})

    def begin(self) -> str:
        return self.call("POST", "/services/haproxy/transactions", params={"version": self.version()})["id"]

    def commit(self, tx: str) -> Optional[str]:
        """Commit a transaction; returns the reload ID when HAProxy has to reload."""
        return self.call("PUT", f"/services/haproxy/transactions/{tx}", want_response=True).headers.get("Reload-ID")

    def put_raw(self, text: str) -> Optional[str]:
        r = self.call("POST", "/services/haproxy/configuration/raw", content=text, params={"version": self.version()},
                      want_response=True)
        return r.headers.get("Reload-ID")

    def wait_reload(self, reload_id: Optional[str], timeout: float = 30.0) -> None:
        """Wait until HAProxy has reloaded with the new config. A failed reload is rolled back by the
        Data Plane API, so the previous configuration stays active; report that clearly."""
        if not reload_id:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            d = self.call("GET", f"/services/haproxy/reloads/{reload_id}") or {}
            status = d.get("status")
            if status == "succeeded":
                return
            if status == "failed":
                raise DataPlaneError(502, "HAProxy did not reload, so the previous configuration is still active: "
                                          + str(d.get("response") or "")[:600])
            time.sleep(0.5)
        raise DataPlaneError(504, "HAProxy has not confirmed the reload yet; check its status before changing more")

    def rollback(self, tx: str) -> None:
        try:
            self.call("DELETE", f"/services/haproxy/transactions/{tx}")
        except DataPlaneError:
            pass

    # runtime --------------------------------------------------------------
    def stats(self) -> List[Dict[str, Any]]:
        d = self.call("GET", "/services/haproxy/stats/native")
        d = d[0] if isinstance(d, list) and d else d
        return (d or {}).get("stats") or []

    def set_state(self, backend: str, server: str, state: str) -> Dict[str, Any]:
        return self.call("PUT", f"/services/haproxy/runtime/backends/{backend}/servers/{server}", json={"admin_state": state})

    def info(self) -> Dict[str, Any]:
        return self.call("GET", "/info") or {}

    # certificates (Data Plane API storage) ----------------------------------
    def certificates(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.call("GET", "/services/haproxy/storage/ssl_certificates") or []:
            try:
                out.append(self.call("GET", f"/services/haproxy/storage/ssl_certificates/{c['storage_name']}") or c)
            except DataPlaneError:
                out.append(c)
        return out

    def upload_certificate(self, name: str, pem: str) -> Dict[str, Any]:
        try:
            r = self.c.post(self.base + "/services/haproxy/storage/ssl_certificates", files={"file_upload": (name, pem.encode(), "application/x-pem-file")})
        except httpx.HTTPError as e:
            raise DataPlaneError(502, f"cannot reach the Data Plane API: {e}")
        if r.status_code >= 400:
            raise DataPlaneError(400 if r.status_code < 500 else 502, _message(r))
        self.wait_reload(r.headers.get("Reload-ID"))
        return r.json()

    def replace_certificate(self, storage_name: str, pem: str) -> Dict[str, Any]:
        r = self.call("PUT", f"/services/haproxy/storage/ssl_certificates/{storage_name}", content=pem, want_response=True)
        self.wait_reload(r.headers.get("Reload-ID"))
        return r.json() if r.content else {}

    def delete_certificate(self, storage_name: str) -> None:
        r = self.call("DELETE", f"/services/haproxy/storage/ssl_certificates/{storage_name}", want_response=True)
        self.wait_reload(r.headers.get("Reload-ID"))

    # stick tables (runtime) ---------------------------------------------------
    def stick_tables(self) -> List[Dict[str, Any]]:
        return self.call("GET", "/services/haproxy/runtime/stick_tables") or []

    def stick_entries(self, table: str, count: int = 200) -> List[Dict[str, Any]]:
        return self.call("GET", f"/services/haproxy/runtime/stick_tables/{table}/entries", params={"count": count}) or []


# --- overview: config + live stats in one model ----------------------------------------

def _stats_index(stats: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Index native stats by (type, name). Frontends and backends carry their name in "name";
    servers carry the server in "name" and their backend in "backend_name"."""
    out = {}
    for it in stats:
        s = it.get("stats") or {}
        kind, name = it.get("type", ""), it.get("name", "")
        key = f"{it.get('backend_name')}/{name}" if kind == "server" else name
        out[(kind, key)] = {k: s.get(k) for k in STAT_KEYS if k in s}
    return out


def parse_routes(fe: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    acls: Dict[str, List[Dict[str, Any]]] = {}
    for a in fe.get("acl_list") or []:
        acls.setdefault(a["acl_name"], []).append(a)
    routes, simple = [], True
    for rule in fe.get("backend_switching_rule_list") or []:
        test, cond = (rule.get("cond_test") or "").strip(), rule.get("cond") or ""
        defs = acls.get(test, [])
        crit = defs[0]["criterion"] if len(defs) == 1 else ""
        kind = ROUTE_CRITERIA.get(crit)
        header = HEADER_RE.match(crit) if crit and not kind else None
        if cond == "if" and (kind or header) and re.fullmatch(r"[\w.-]+", test):
            value = re.sub(r"^(-i|-m str)\s+", "", defs[0].get("value") or "").strip()
            if header:
                routes.append({"kind": "header", "value": value, "header": header.group(1), "backend": rule.get("name")})
            else:
                routes.append({"kind": kind, "value": ",".join(value.split()) if kind == "src" else value, "backend": rule.get("name")})
        else:
            simple = False
            routes.append({"kind": "other", "value": f"{cond} {test}".strip(), "backend": rule.get("name")})
    return routes, simple


# Request policies k10y manages as form settings. They're recognised by their exact shape; any other
# http-request rule is left untouched (and shown as "custom rules").
def _is_allow(r: Dict[str, Any]) -> bool:
    return r.get("type") == "deny" and r.get("deny_status") == 403 and r.get("cond") == "unless" \
        and re.fullmatch(r"\{ src [^{}]+ \}", r.get("cond_test") or "") is not None


def _is_redirect(r: Dict[str, Any]) -> bool:
    return r.get("type") == "redirect" and r.get("redir_type") == "scheme" and r.get("redir_value") == "https" \
        and (r.get("cond_test") or "").replace(" ", "") == "{ssl_fc}"


def _is_track(r: Dict[str, Any]) -> bool:
    return r.get("type") == "track-sc" and r.get("track_sc_key") == "src" and int(r.get("track_sc_stick_counter") or 0) == 0


def _is_limit(r: Dict[str, Any]) -> bool:
    return r.get("type") == "deny" and r.get("deny_status") == 429 and r.get("cond") == "if" \
        and re.fullmatch(r"\{ sc_http_req_rate\(0\) gt \d+ \}", r.get("cond_test") or "") is not None


def parse_policies(fe: Dict[str, Any]) -> Dict[str, Any]:
    rules = fe.get("http_request_rule_list") or []
    allow = next((r for r in rules if _is_allow(r)), None)
    limit = next((r for r in rules if _is_limit(r)), None)
    st = fe.get("stick_table") or {}
    period = None
    m = re.search(r"http_req_rate\((\d+)s?\)", st.get("store") or "")
    if m:
        period = int(m.group(1))
    managed = [r for r in rules if _is_allow(r) or _is_redirect(r) or (_is_track(r) and limit) or _is_limit(r)]
    return {
        "allow_ips": re.findall(r"[^\s{}]+", (allow or {}).get("cond_test", ""))[1:] if allow else [],
        "redirect_https": any(_is_redirect(r) for r in rules),
        "rate_limit": {"requests": int(re.search(r"gt (\d+)", limit["cond_test"]).group(1)), "period": period or 10} if limit else None,
        "custom_rules": len(rules) - len(managed),
    }


def overview(dp: DataPlane) -> Dict[str, Any]:
    by_name = lambda x: x["name"]   # the Data Plane API may reorder sections after edits; keep the UI stable
    fes, bes, stats = sorted(dp.frontends(), key=by_name), sorted(dp.backends(), key=by_name), dp.stats()
    idx = _stats_index(stats)
    used_by: Dict[str, List[str]] = {}
    frontends = []
    for fe in fes:
        routes, simple = parse_routes(fe)
        for b in {fe.get("default_backend"), *[r["backend"] for r in routes]} - {None}:
            used_by.setdefault(b, []).append(fe["name"])
        binds = [{"address": b.get("address") or "*", "port": b.get("port"), "ssl": bool(b.get("ssl")), "name": n,
                  "certificate": (b.get("ssl_certificate") or "").rsplit("/", 1)[-1]}
                 for n, b in (fe.get("binds") or {}).items()]
        frontends.append({"name": fe["name"], "mode": fe.get("mode") or "http", "binds": binds,
                          "default_backend": fe.get("default_backend"), "routes": routes, "simple_routes": simple,
                          "stats_page": bool((fe.get("stats_options") or {}).get("stats_enable")),
                          "policies": parse_policies(fe),
                          "stats": idx.get(("frontend", fe["name"]), {})})
    backends = []
    for be in bes:
        servers = []
        for name, s in (be.get("servers") or {}).items():
            st = idx.get(("server", f"{be['name']}/{name}"), {})
            servers.append({"name": name, "address": s.get("address"), "port": s.get("port"), "weight": s.get("weight", 1),
                            "check": s.get("check") == "enabled", "backup": s.get("backup") == "enabled",
                            "state": _server_state(st.get("status")), "stats": st})
        chk = be.get("adv_check")
        backends.append({"name": be["name"], "mode": be.get("mode") or "http", "balance": (be.get("balance") or {}).get("algorithm") or "roundrobin",
                         "check": {"type": "http" if chk == "httpchk" else ("tcp" if chk == "tcp-check" else "default"),
                                   "uri": (be.get("httpchk_params") or {}).get("uri") or ""},
                         "servers": servers, "used_by": sorted(set(used_by.get(be["name"], []))),
                         "stats": idx.get(("backend", be["name"]), {})})
    raw = dp.raw()
    # Drain/maintenance are runtime states; without a server-state file a reload resets them.
    keeps_state = "server-state-file" in raw and "load-server-state-from-file" in raw
    return {"frontends": frontends, "backends": backends, "config_version": dp.version(), "keeps_server_state": keeps_state}


def _server_state(status: Optional[str]) -> str:
    """HAProxy status text -> up | down | drain | maint | nocheck | unknown."""
    s = (status or "").upper()
    if s.startswith("MAINT"):
        return "maint"
    if s.startswith("DRAIN") or "NOLB" in s:
        return "drain"
    if s.startswith("UP"):
        return "up"
    if s.startswith("DOWN"):
        return "down"
    if s == "NO CHECK":
        return "nocheck"
    return "unknown"


# --- configuration changes ------------------------------------------------------------

class Op(BaseModel):
    op: Literal["create", "update", "delete", "raw"]
    kind: Literal["frontend", "backend", "server", "config"]
    name: str = Field("", max_length=64)
    backend: Optional[str] = Field(None, max_length=64)    # for servers
    data: Dict[str, Any] = Field(default_factory=dict)


def _check_name(name: str, what: str) -> str:
    if not NAME_RE.match(name or ""):
        raise DataPlaneError(400, f"{what} name must be letters, digits, dot, dash or underscore (max 64)")
    return name


def _port(v: Any) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        p = 0
    if not 1 <= p <= 65535:
        raise DataPlaneError(400, "port must be 1-65535")
    return p


def _routes_to_config(routes: List[Dict[str, Any]], backends: set,
                      existing: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Routes -> ACLs + use_backend rules. An ACL that already matches the same thing keeps its name,
    so editing one route doesn't rename the others."""
    reuse = {(a["criterion"], a.get("value")): a["acl_name"] for a in existing or []}
    acls, rules, taken = [], [], set()
    for i, r in enumerate(routes, 1):
        kind, value, be = r.get("kind"), str(r.get("value") or "").strip(), r.get("backend")
        if kind not in ("path_prefix", "path", "host", "header", "src") or not value or re.search(r"\s", value):
            raise DataPlaneError(400, f"route {i}: choose a type and a value without spaces")
        if kind == "header" and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(r.get("header") or "")):
            raise DataPlaneError(400, f"route {i}: header name must be letters, digits or dashes")
        if kind == "src" and not all(re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", x) for x in value.split(",")):
            raise DataPlaneError(400, f"route {i}: source must be IPs or networks like 10.0.0.0/8, separated by commas")
        if be not in backends:
            raise DataPlaneError(400, f"route {i}: unknown backend {be!r}")
        criterion = {"path_prefix": "path_beg", "path": "path", "host": "hdr(host)", "src": "src"}.get(kind) or f"hdr({r['header']})"
        acl_value = f"-i {value}" if kind == "host" else f"-m str {value}" if kind == "header" else value.replace(",", " ") if kind == "src" else value
        name = reuse.get((criterion, acl_value))
        if not name or name in taken:
            name, n = f"k10y_route_{i}", i
            while name in taken or name in reuse.values():
                n += 1
                name = f"k10y_route_{n}"
        taken.add(name)
        acls.append({"acl_name": name, "criterion": criterion, "value": acl_value})
        rules.append({"name": be, "cond": "if", "cond_test": name})
    return acls, rules


def _apply_policies(fe: Dict[str, Any], pol: Dict[str, Any]) -> None:
    """Rewrite the managed http-request rules (allow-list, HTTPS redirect, rate limit); keep all others in place."""
    rules = fe.get("http_request_rule_list") or []
    had_limit = any(_is_limit(r) for r in rules)
    rules = [r for r in rules if not (_is_allow(r) or _is_redirect(r) or _is_limit(r) or (had_limit and _is_track(r)))]
    managed = []
    ips = [x.strip() for x in pol.get("allow_ips") or [] if str(x).strip()]
    for x in ips:
        if not re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", x):
            raise DataPlaneError(400, f"allowed source {x!r} is not an IP address or network")
    if ips:
        managed.append({"type": "deny", "deny_status": 403, "cond": "unless", "cond_test": "{ src " + " ".join(ips) + " }"})
    if pol.get("redirect_https"):
        managed.append({"type": "redirect", "redir_type": "scheme", "redir_value": "https", "redir_code": 301, "cond": "unless", "cond_test": "{ ssl_fc }"})
    limit = pol.get("rate_limit")
    if limit:
        n, period = int(limit.get("requests") or 0), int(limit.get("period") or 10)
        if not 1 <= n <= 1_000_000 or not 1 <= period <= 3600:
            raise DataPlaneError(400, "rate limit: 1-1000000 requests per 1-3600 seconds")
        st = fe.get("stick_table") or {}
        if st and "http_req_rate" not in (st.get("store") or ""):
            raise DataPlaneError(400, "this service already has a different stick table; set the rate limit in the raw config")
        fe["stick_table"] = {"type": "ip", "size": 100000, "expire": period * 1000, "store": f"http_req_rate({period}s)"}
        managed += [{"type": "track-sc", "track_sc_key": "src", "track_sc_stick_counter": 0},
                    {"type": "deny", "deny_status": 429, "cond": "if", "cond_test": "{ sc_http_req_rate(0) gt %d }" % n}]
    elif had_limit and "http_req_rate" in ((fe.get("stick_table") or {}).get("store") or ""):
        fe.pop("stick_table", None)
    fe["http_request_rule_list"] = managed + rules


def apply_op(dp: DataPlane, tx: str, op: Op) -> str:
    """Apply one operation inside a transaction. Returns a short description for the audit log."""
    cfg = "/services/haproxy/configuration"
    d = op.data
    if op.kind == "frontend":
        name = _check_name(op.name, "Service")
        if op.op == "delete":
            dp.call("DELETE", f"{cfg}/frontends/{name}", tx)
            return f"delete service {name}"
        backends = {b["name"] for b in dp.backends(tx)}
        current = next((f for f in dp.frontends(tx) if f["name"] == name), None)
        if op.op == "create" and current:
            raise DataPlaneError(400, f"service {name} already exists")
        if op.op == "update" and not current:
            raise DataPlaneError(404, f"service {name} not found")
        fe = dict(current or {"name": name})
        if "mode" in d:
            if d["mode"] not in ("http", "tcp"):
                raise DataPlaneError(400, "mode must be http or tcp")
            fe["mode"] = d["mode"]
        if "default_backend" in d:
            if d["default_backend"] and d["default_backend"] not in backends:
                raise DataPlaneError(400, f"unknown backend {d['default_backend']!r}")
            if d["default_backend"]:
                fe["default_backend"] = d["default_backend"]
            else:
                fe.pop("default_backend", None)
        if "binds" in d:
            binds, cert_dir = {}, None
            for b in d["binds"] or []:
                addr, port = (str(b.get("address") or "*").strip() or "*"), _port(b.get("port"))
                bind = {"name": f"{addr}:{port}", "address": addr, "port": port}
                if b.get("ssl"):
                    cert = str(b.get("certificate") or "")
                    if cert_dir is None:
                        certs = {c["storage_name"]: c.get("file") for c in dp.call("GET", "/services/haproxy/storage/ssl_certificates") or []}
                        cert_dir = certs
                    if cert not in cert_dir:
                        raise DataPlaneError(400, f"listen {addr}:{port}: choose an uploaded certificate for TLS")
                    bind.update({"ssl": True, "ssl_certificate": cert_dir[cert]})
                binds[bind["name"]] = bind
            if not binds:
                raise DataPlaneError(400, "a service needs at least one listen address")
            fe["binds"] = binds
        if d.get("policies") is not None:
            _apply_policies(fe, d["policies"])
        if d.get("routes") is not None:
            used = {r.get("cond_test") for r in fe.get("backend_switching_rule_list") or []}
            acls, rules = _routes_to_config(d["routes"], backends, [a for a in fe.get("acl_list") or [] if a["acl_name"] in used])
            fe["acl_list"] = [a for a in fe.get("acl_list") or [] if a["acl_name"] not in used] + acls
            fe["backend_switching_rule_list"] = rules
        if op.op == "create":
            dp.call("POST", f"{cfg}/frontends", tx, json=fe, params={"full_section": "true"})
        else:
            dp.call("PUT", f"{cfg}/frontends/{name}", tx, json=fe, params={"full_section": "true"})
        return f"{op.op} service {name}"

    if op.kind == "backend":
        name = _check_name(op.name, "Backend")
        if op.op == "delete":
            users = [f["name"] for f in dp.frontends(tx) if f.get("default_backend") == name
                     or any(r.get("name") == name for r in f.get("backend_switching_rule_list") or [])]
            if users:
                raise DataPlaneError(400, f"backend {name} is still used by: {', '.join(users)}")
            dp.call("DELETE", f"{cfg}/backends/{name}", tx)
            return f"delete backend {name}"
        current = next((b for b in dp.backends(tx) if b["name"] == name), None)
        if op.op == "create" and current:
            raise DataPlaneError(400, f"backend {name} already exists")
        if op.op == "update" and not current:
            raise DataPlaneError(404, f"backend {name} not found")
        be = dict(current or {"name": name})
        if "mode" in d:
            if d["mode"] not in ("http", "tcp"):
                raise DataPlaneError(400, "mode must be http or tcp")
            be["mode"] = d["mode"]
        if "balance" in d:
            if d["balance"] not in BALANCE:
                raise DataPlaneError(400, f"balance must be one of {', '.join(BALANCE)}")
            be["balance"] = {"algorithm": d["balance"]}
        if "check" in d:
            chk = d["check"] or {}
            be.pop("adv_check", None)
            be.pop("httpchk_params", None)
            if chk.get("type") == "http":
                uri = str(chk.get("uri") or "/").strip()
                if not uri.startswith("/") or re.search(r"\s", uri):
                    raise DataPlaneError(400, "health check path must start with / and have no spaces")
                be["adv_check"] = "httpchk"
                be["httpchk_params"] = {"method": "GET", "uri": uri}
            elif chk.get("type") == "tcp":
                be["adv_check"] = "tcp-check"
        be.setdefault("servers", {})
        if op.op == "create":
            dp.call("POST", f"{cfg}/backends", tx, json=be, params={"full_section": "true"})
        else:
            dp.call("PUT", f"{cfg}/backends/{name}", tx, json=be, params={"full_section": "true"})
        return f"{op.op} backend {name}"

    if op.kind == "server":
        backend = _check_name(op.backend or "", "Backend")
        name = _check_name(op.name, "Server")
        base = f"{cfg}/backends/{backend}/servers"
        if op.op == "delete":
            dp.call("DELETE", f"{base}/{name}", tx)
            return f"delete server {backend}/{name}"
        current = None
        if op.op == "update":
            current = dp.call("GET", f"{base}/{name}", tx)
        srv = dict(current or {"name": name})
        if "address" in d or op.op == "create":
            addr = str(d.get("address") or "").strip()
            if not addr or re.search(r"\s", addr):
                raise DataPlaneError(400, "server address is required (host name or IP, no spaces)")
            srv["address"] = addr
        if "port" in d or op.op == "create":
            srv["port"] = _port(d.get("port"))
        if "weight" in d:
            w = int(d["weight"])
            if not 0 <= w <= 256:
                raise DataPlaneError(400, "weight must be 0-256")
            srv["weight"] = w
        if "check" in d:
            srv["check"] = "enabled" if d["check"] else "disabled"
        if "backup" in d:
            if d["backup"]:
                srv["backup"] = "enabled"
            else:
                srv.pop("backup", None)
        if op.op == "create":
            dp.call("POST", base, tx, json=srv)
        else:
            dp.call("PUT", f"{base}/{name}", tx, json=srv)
        return f"{op.op} server {backend}/{name}"

    raise DataPlaneError(400, f"unsupported operation {op.op} {op.kind}")


HEADER_LINE = re.compile(r"^# (_md5hash=|_version=|Dataplaneapi managed File|changing file directly)")


def _diff(before: str, after: str) -> str:
    """Unified diff without the Data Plane API's file header (it changes on every save)."""
    body = lambda text: [l for l in text.splitlines() if not HEADER_LINE.match(l)]
    return "\n".join(difflib.unified_diff(body(before), body(after), "current", "new", lineterm="", n=3))


def plan_and_apply(dp: DataPlane, ops: List[Op], apply: bool) -> Dict[str, Any]:
    """Preview (diff + validation) or apply a list of operations as one change."""
    if not ops:
        raise DataPlaneError(400, "nothing to change")
    raw_ops = [o for o in ops if o.op == "raw"]
    if raw_ops and len(ops) > 1:
        raise DataPlaneError(400, "a raw config edit must be the only operation")
    baseline = dp.begin()   # an empty transaction renders the current config the same way as the changed one
    try:
        before = dp.raw(baseline)
    finally:
        dp.rollback(baseline)

    if raw_ops:
        after = str(raw_ops[0].data.get("text") or "")
        if not after.strip():
            raise DataPlaneError(400, "the configuration is empty")
        diff = _diff(before, after)
        try:
            dp.validate(after)
            valid, error = True, ""
        except DataPlaneError as e:
            valid, error = False, str(e)
        if apply:
            if not valid:
                raise DataPlaneError(400, f"HAProxy rejected the configuration: {error}")
            dp.wait_reload(dp.put_raw(after))
        return {"diff": diff, "valid": valid, "error": error, "applied": apply, "summary": ["edit raw configuration"]}

    tx = dp.begin()
    committed = False
    try:
        summary = [apply_op(dp, tx, op) for op in ops]
        after = dp.raw(tx)
        diff = _diff(before, after)
        try:
            dp.validate(after)
            valid, error = True, ""
        except DataPlaneError as e:
            valid, error = False, str(e)
        if apply:
            if not valid:
                raise DataPlaneError(400, f"HAProxy rejected the change: {error}")
            reload_id = dp.commit(tx)
            committed = True
            dp.wait_reload(reload_id)
        return {"diff": diff, "valid": valid, "error": error, "applied": apply, "summary": summary}
    finally:
        if not committed:
            dp.rollback(tx)


# --- hub API --------------------------------------------------------------------------

class InstanceBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=8, max_length=300)
    username: str = Field(min_length=1, max_length=128)
    password: Optional[str] = Field(None, max_length=256, description="Omit to keep the stored password.")
    verify_tls: bool = False
    log_tag: str = Field("", max_length=64, pattern=r"^[A-Za-z0-9._-]*$")
    group_name: str = Field("", max_length=64)


class TestBody(BaseModel):
    url: str = Field(min_length=8, max_length=300)
    username: str = Field(min_length=1, max_length=128)
    password: Optional[str] = Field(None, max_length=256)
    verify_tls: bool = False
    instance_id: Optional[int] = None


class StateBody(BaseModel):
    state: str = Field(pattern="^(ready|drain|maint)$")


class ChangesBody(BaseModel):
    ops: List[Op] = Field(min_length=1, max_length=50)
    apply: bool = False
    note: str = Field("", max_length=200)          # e.g. "roll back to version 12"


class GroupChangesBody(ChangesBody):
    instance_ids: List[int] = Field(min_length=1, max_length=20)


class CertBody(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    pem: str = Field(min_length=50, max_length=200_000)


class AiBody(BaseModel):
    scope: str = Field("instance", pattern="^(instance|service|backend)$")
    target: str = Field("", max_length=64)
    question: str = Field("", max_length=2000)


class SyncBody(BaseModel):
    cluster_id: int
    namespace: str = Field(min_length=1, max_length=253)
    service: str = Field(min_length=1, max_length=253)
    port: str = Field("", max_length=64)
    mode: str = Field("nodeport", pattern="^(nodeport|pods)$")
    enabled: bool = True


def _cert_view(c: Dict[str, Any], used_by: List[str]) -> Dict[str, Any]:
    exp = c.get("not_after")
    days = None
    if exp:
        try:
            days = (datetime.fromisoformat(exp.replace("Z", "+00:00")) - utcnow()).total_seconds() / 86400
        except ValueError:
            pass
    return {"name": c.get("storage_name"), "file": c.get("file"), "subject": c.get("subject") or "", "issuer": c.get("issuers") or "",
            "domains": c.get("domains") or c.get("subject_alternative_names") or "", "not_before": c.get("not_before"),
            "not_after": exp, "days_left": None if days is None else round(days, 1), "used_by": used_by}


def build_router(db_session, require, audit, crypto, monitor: Callable[[], Any] = lambda: None,
                 syslog: Callable[[], Any] = lambda: None, can_cluster: Callable[[Any, str, int], bool] = lambda p, perm, cid: False,
                 ai_config: Callable[[Session], Dict[str, str]] = lambda s: {},
                 client_factory: Optional[Callable[..., DataPlane]] = None) -> APIRouter:
    r = APIRouter(prefix="/api/hub/haproxy")
    make = client_factory or (lambda inst, pw: DataPlane(inst.url, inst.username, pw, inst.verify_tls))

    def view(i: HAProxyInstance) -> dict:
        return {"id": i.id, "name": i.name, "url": i.url, "username": i.username, "verify_tls": i.verify_tls,
                "log_tag": i.log_tag or i.name, "group_name": i.group_name or "",
                "created_at": i.created_at.isoformat() if i.created_at else None}

    def get(s: Session, iid: int) -> HAProxyInstance:
        inst = s.get(HAProxyInstance, iid)
        if inst is None:
            raise HTTPException(status_code=404, detail="HAProxy instance not found")
        return inst

    def dp_for(inst: HAProxyInstance) -> DataPlane:
        return make(inst, crypto().decrypt(inst.password_encrypted))

    def fail(e: DataPlaneError) -> HTTPException:
        return HTTPException(status_code=e.status, detail=str(e))

    def check_url(url: str) -> str:
        if not re.match(r"^https?://[^\s/]+", url):
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
        return url.rstrip("/")

    def snapshot(s: Session, inst: HAProxyInstance, dp: DataPlane, p: Any, summary: str, source: str = "k10y") -> None:
        m = monitor()
        if m is not None:
            try:
                m.snapshot(s, inst, dp, p.user.username, summary, source)
            except DataPlaneError:
                pass

    # --- instances -----------------------------------------------------------------
    @r.get("")
    def list_instances(p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> list:
        return [view(i) for i in s.execute(select(HAProxyInstance).order_by(HAProxyInstance.name)).scalars()]

    @r.post("/test")
    def test_instance(body: TestBody, p=Depends(require("clusters.manage")), s: Session = Depends(db_session)) -> dict:
        pw = body.password
        if not pw and body.instance_id:
            pw = crypto().decrypt(get(s, body.instance_id).password_encrypted)
        if not pw:
            raise HTTPException(status_code=400, detail="password is required")
        try:
            with DataPlane(check_url(body.url), body.username, pw, body.verify_tls) as dp:
                info = dp.info()
                data = overview(dp)
        except DataPlaneError as e:
            raise fail(e)
        return {"api_version": ((info.get("api") or {}).get("version") or "").split(" ")[0],
                "frontends": len(data["frontends"]), "backends": len(data["backends"]),
                "servers": sum(len(b["servers"]) for b in data["backends"])}

    @r.post("")
    def create_instance(body: InstanceBody, request: Request, p=Depends(require("clusters.manage")),
                        s: Session = Depends(db_session)) -> dict:
        if not body.password:
            raise HTTPException(status_code=400, detail="password is required")
        if s.execute(select(HAProxyInstance).where(HAProxyInstance.name == body.name)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="an instance with this name already exists")
        inst = HAProxyInstance(name=body.name, url=check_url(body.url), username=body.username,
                               password_encrypted=crypto().encrypt(body.password), verify_tls=body.verify_tls,
                               log_tag=body.log_tag or body.name, group_name=body.group_name.strip())
        s.add(inst)
        s.commit()
        audit(s, request, p, "haproxy-add", inst.name, params={"url": inst.url})
        return view(inst)

    @r.patch("/{iid}")
    def update_instance(iid: int, body: InstanceBody, request: Request, p=Depends(require("clusters.manage")),
                        s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        inst.name, inst.url, inst.username, inst.verify_tls = body.name, check_url(body.url), body.username, body.verify_tls
        inst.log_tag, inst.group_name = body.log_tag or body.name, body.group_name.strip()
        if body.password:
            inst.password_encrypted = crypto().encrypt(body.password)
        s.commit()
        audit(s, request, p, "haproxy-update", inst.name, params={"url": inst.url, "password_changed": bool(body.password)})
        return view(inst)

    @r.delete("/{iid}")
    def delete_instance(iid: int, request: Request, p=Depends(require("clusters.manage")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        for model in (HAProxyConfigVersion, HAProxyK8sSync):
            for row in s.execute(select(model).where(model.instance_id == iid)).scalars():
                s.delete(row)
        s.delete(inst)
        s.commit()
        audit(s, request, p, "haproxy-remove", inst.name)
        return {"ok": True}

    # --- reads ------------------------------------------------------------------------
    @r.get("/{iid}/overview")
    def instance_overview(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        try:
            with dp_for(inst) as dp:
                data = overview(dp)
        except DataPlaneError as e:
            raise fail(e)
        syncs = {x.backend: sync_view(x) for x in s.execute(select(HAProxyK8sSync).where(HAProxyK8sSync.instance_id == iid)).scalars()}
        for b in data["backends"]:
            b["k8s_sync"] = syncs.get(b["name"])
        group = [view(i) for i in s.execute(select(HAProxyInstance).where(HAProxyInstance.group_name == inst.group_name,
                                                                           HAProxyInstance.id != iid)).scalars()] if inst.group_name else []
        return {"instance": view(inst), "group": group, **data}

    @r.get("/{iid}/history")
    def history(iid: int, since: int = 0, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        get(s, iid)
        m = monitor()
        return {"points": m.series(iid, since) if m else [], "interval_s": 10}

    @r.get("/{iid}/logs")
    def logs(iid: int, after: int = 0, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        rx = syslog()
        tag = inst.log_tag or inst.name
        return {"enabled": rx is not None, "port": rx.port if rx else None, "tag": tag,
                "lines": rx.read(tag, after) if rx else [], "other_tags": [x for x in (rx.tags() if rx else []) if x != tag]}

    @r.get("/{iid}/config")
    def raw_config(iid: int, p=Depends(require("haproxy.configure")), s: Session = Depends(db_session)) -> dict:
        try:
            with dp_for(get(s, iid)) as dp:
                tx = dp.begin()
                try:
                    return {"text": dp.raw(tx), "version": dp.version()}
                finally:
                    dp.rollback(tx)
        except DataPlaneError as e:
            raise fail(e)

    # --- runtime ------------------------------------------------------------------------
    @r.put("/{iid}/servers/{backend}/{server}/state")
    def server_state(iid: int, backend: str, server: str, body: StateBody, request: Request,
                     p=Depends(require("haproxy.operate")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        target = f"{inst.name}/{backend}/{server}"
        try:
            _check_name(backend, "Backend")
            _check_name(server, "Server")
            with dp_for(inst) as dp:
                dp.set_state(backend, server, body.state)
        except DataPlaneError as e:
            audit(s, request, p, f"haproxy-{body.state}", target, ok=False, message=str(e))
            raise fail(e)
        words = {"ready": "back in service", "drain": "draining (no new connections)", "maint": "in maintenance"}
        msg = f"{backend}/{server} is {words[body.state]}"
        audit(s, request, p, f"haproxy-{body.state}", target, message=msg)
        return {"ok": True, "message": msg}

    @r.get("/{iid}/stick-tables")
    def stick_tables(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> list:
        try:
            with dp_for(get(s, iid)) as dp:
                return dp.stick_tables()
        except DataPlaneError as e:
            raise fail(e)

    @r.get("/{iid}/stick-tables/{table}")
    def stick_entries(iid: int, table: str, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> list:
        _check_name(table, "Table")
        try:
            with dp_for(get(s, iid)) as dp:
                return dp.stick_entries(table)
        except DataPlaneError as e:
            raise fail(e)

    # --- configuration changes ----------------------------------------------------------
    def run_changes(s: Session, request: Request, p: Any, inst: HAProxyInstance, body: ChangesBody) -> dict:
        try:
            with dp_for(inst) as dp:
                result = plan_and_apply(dp, body.ops, body.apply)
                if body.apply:
                    snapshot(s, inst, dp, p, (body.note + ": " if body.note else "") + "; ".join(result["summary"]),
                             "rollback" if body.note.startswith("roll back") else "k10y")
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-config", inst.name, ok=False, dry_run=not body.apply, message=str(e),
                  params={"ops": [f"{o.op} {o.kind} {o.backend + '/' if o.backend else ''}{o.name}" for o in body.ops]})
            raise fail(e)
        audit(s, request, p, "haproxy-config", inst.name, dry_run=not body.apply,
              message=(body.note + ": " if body.note else "") + "; ".join(result["summary"]) + ("" if result["valid"] else f" (invalid: {result['error']})"),
              params={"ops": result["summary"]})
        return result

    @r.post("/{iid}/changes")
    def changes(iid: int, body: ChangesBody, request: Request, p=Depends(require("haproxy.configure")),
                s: Session = Depends(db_session)) -> dict:
        return run_changes(s, request, p, get(s, iid), body)

    @r.post("/group-changes")
    def group_changes(body: GroupChangesBody, request: Request, p=Depends(require("haproxy.configure")),
                      s: Session = Depends(db_session)) -> dict:
        """Preview the same change on several instances, or apply it one by one (stopping at the first failure)."""
        results = []
        for iid in body.instance_ids:
            inst = get(s, iid)
            try:
                res = run_changes(s, request, p, inst, body)
                results.append({"instance_id": iid, "name": inst.name, **res})
            except HTTPException as e:
                results.append({"instance_id": iid, "name": inst.name, "valid": False, "applied": False, "error": e.detail, "diff": ""})
                if body.apply:
                    break
        return {"results": results}

    # --- config history -----------------------------------------------------------------
    @r.get("/{iid}/versions")
    def versions(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> list:
        get(s, iid)
        rows = s.execute(select(HAProxyConfigVersion).where(HAProxyConfigVersion.instance_id == iid)
                         .order_by(HAProxyConfigVersion.id.desc()).limit(200)).scalars()
        return [{"id": v.id, "version": v.version, "source": v.source, "username": v.username, "summary": v.summary,
                 "created_at": v.created_at.isoformat() if v.created_at else None, "lines": v.text.count("\n")} for v in rows]

    @r.get("/{iid}/versions/{vid}")
    def version_text(iid: int, vid: int, p=Depends(require("haproxy.configure")), s: Session = Depends(db_session)) -> dict:
        v = s.get(HAProxyConfigVersion, vid)
        if v is None or v.instance_id != iid:
            raise HTTPException(status_code=404, detail="version not found")
        from .haproxy_monitor import strip_header
        return {"id": v.id, "version": v.version, "text": strip_header(v.text)}

    # --- certificates -------------------------------------------------------------------
    def cert_users(dp: DataPlane) -> Dict[str, List[str]]:
        users: Dict[str, List[str]] = {}
        for fe in dp.frontends():
            for b in (fe.get("binds") or {}).values():
                if b.get("ssl_certificate"):
                    users.setdefault(b["ssl_certificate"].rsplit("/", 1)[-1], []).append(f"{fe['name']} ({b.get('name')})")
        return users

    @r.get("/{iid}/certificates")
    def certificates(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> list:
        try:
            with dp_for(get(s, iid)) as dp:
                users = cert_users(dp)
                return [_cert_view(c, users.get(c.get("storage_name") or "", [])) for c in dp.certificates()]
        except DataPlaneError as e:
            raise fail(e)

    @r.post("/{iid}/certificates")
    def upload_certificate(iid: int, body: CertBody, request: Request, p=Depends(require("haproxy.configure")),
                           s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        if "PRIVATE KEY" not in body.pem or "CERTIFICATE" not in body.pem:
            raise HTTPException(status_code=400, detail="paste the certificate and its private key (PEM), one after the other")
        name = body.name if body.name.endswith(".pem") else body.name + ".pem"
        try:
            with dp_for(inst) as dp:
                existing = {c.get("storage_name") for c in dp.call("GET", "/services/haproxy/storage/ssl_certificates") or []}
                stored = name.replace(".", "_", name.count(".") - 1) if name.count(".") > 1 else name
                if name in existing or stored in existing:
                    c = dp.replace_certificate(name if name in existing else stored, body.pem)
                    action = "replaced"
                else:
                    c = dp.upload_certificate(name, body.pem)
                    action = "uploaded"
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-cert", f"{inst.name}/{name}", ok=False, message=str(e))
            raise fail(e)
        audit(s, request, p, "haproxy-cert", f"{inst.name}/{name}", message=f"certificate {action}")
        return {"ok": True, "message": f"Certificate {action}", "certificate": c}

    @r.delete("/{iid}/certificates/{name}")
    def delete_certificate(iid: int, name: str, request: Request, p=Depends(require("haproxy.configure")),
                           s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        try:
            with dp_for(inst) as dp:
                users = cert_users(dp).get(name)
                if users:
                    raise DataPlaneError(400, f"certificate is still used by {', '.join(users)}")
                dp.delete_certificate(name)
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-cert", f"{inst.name}/{name}", ok=False, message=str(e))
            raise fail(e)
        audit(s, request, p, "haproxy-cert", f"{inst.name}/{name}", message="certificate deleted")
        return {"ok": True}

    # --- AI assistant -------------------------------------------------------------------
    @r.post("/{iid}/ai/diagnose")
    def ai_diagnose(iid: int, body: AiBody, request: Request, p=Depends(require("haproxy.view")),
                    s: Session = Depends(db_session)) -> dict:
        from .. import ai
        from . import haproxy_ai
        if not p.can("ai.use"):
            raise HTTPException(status_code=403, detail="missing permission: ai.use")
        inst = get(s, iid)
        if body.scope != "instance":
            try:
                _check_name(body.target, "Service" if body.scope == "service" else "Backend")
            except DataPlaneError as e:
                raise fail(e)
        m, rx = monitor(), syslog()
        versions = [{"version": v.version, "when": v.created_at.isoformat() if v.created_at else None, "who": v.username,
                     "source": v.source, "summary": v.summary[:200]}
                    for v in s.execute(select(HAProxyConfigVersion).where(HAProxyConfigVersion.instance_id == iid)
                                       .order_by(HAProxyConfigVersion.id.desc()).limit(10)).scalars()]
        target = f"haproxy/{inst.name}" + (f"/{body.scope}/{body.target}" if body.scope != "instance" else "")
        try:
            with dp_for(inst) as dp:
                tools = haproxy_ai.HAProxyTools(dp, m.latest.get(iid) if m else None, m.series(iid) if m else [],
                                                rx.read(inst.log_tag or inst.name, 0, 1000) if rx else [], versions)
                result = haproxy_ai.run(ai_config(s), tools, body.scope, body.target, body.question)
        except ai.ProviderError as e:
            audit(s, request, p, "ai-diagnose", target, ok=False, message=str(e))
            raise HTTPException(status_code=502, detail=str(e))
        except httpx.HTTPError as e:
            audit(s, request, p, "ai-diagnose", target, ok=False, message=str(e))
            raise HTTPException(status_code=504, detail=f"AI provider unreachable: {e}")
        except DataPlaneError as e:
            raise fail(e)
        audit(s, request, p, "ai-diagnose", target, params={"model": result["model"], "steps": len(result["steps"])},
              message=f"{len(result['issues'])} issue(s), {len(result['actions'])} proposed action(s), {result['seconds']}s")
        return result

    # --- Kubernetes sync ------------------------------------------------------------------
    def sync_view(x: HAProxyK8sSync) -> dict:
        return {"id": x.id, "backend": x.backend, "cluster_id": x.cluster_id, "namespace": x.namespace, "service": x.service,
                "port": x.port, "mode": x.mode, "enabled": x.enabled, "last_result": x.last_result, "last_error": x.last_error,
                "last_sync_at": x.last_sync_at.isoformat() if x.last_sync_at else None}

    @r.put("/{iid}/k8s-sync/{backend}")
    def set_sync(iid: int, backend: str, body: SyncBody, request: Request, p=Depends(require("haproxy.configure")),
                 s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        _check_name(backend, "Backend")
        if not can_cluster(p, "cluster.view", body.cluster_id):
            raise HTTPException(status_code=403, detail="you need cluster.view on that cluster")
        row = s.execute(select(HAProxyK8sSync).where(HAProxyK8sSync.instance_id == iid, HAProxyK8sSync.backend == backend)).scalar_one_or_none()
        if row is None:
            row = HAProxyK8sSync(instance_id=iid, backend=backend, cluster_id=body.cluster_id, namespace=body.namespace, service=body.service)
            s.add(row)
        row.cluster_id, row.namespace, row.service, row.port, row.mode, row.enabled = (
            body.cluster_id, body.namespace, body.service, body.port, body.mode, body.enabled)
        s.commit()
        audit(s, request, p, "haproxy-k8s-sync", f"{inst.name}/{backend}", message=f"sync from {body.namespace}/{body.service} ({body.mode})")
        result = run_sync(s, inst, row.id)
        s.refresh(row)
        return {**sync_view(row), "message": result}

    @r.post("/{iid}/k8s-sync/{backend}/run")
    def run_sync_now(iid: int, backend: str, p=Depends(require("haproxy.configure")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        row = s.execute(select(HAProxyK8sSync).where(HAProxyK8sSync.instance_id == iid, HAProxyK8sSync.backend == backend)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="no sync for this backend")
        result = run_sync(s, inst, row.id)
        s.refresh(row)
        return {**sync_view(row), "message": result}

    @r.delete("/{iid}/k8s-sync/{backend}")
    def delete_sync(iid: int, backend: str, request: Request, p=Depends(require("haproxy.configure")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        row = s.execute(select(HAProxyK8sSync).where(HAProxyK8sSync.instance_id == iid, HAProxyK8sSync.backend == backend)).scalar_one_or_none()
        if row is not None:
            s.delete(row)
            s.commit()
            audit(s, request, p, "haproxy-k8s-sync", f"{inst.name}/{backend}", message="sync removed (servers kept)")
        return {"ok": True}

    def run_sync(s: Session, inst: HAProxyInstance, sync_id: int) -> str:
        m = monitor()
        if m is None:
            raise HTTPException(status_code=503, detail="the HAProxy monitor is not running")
        try:
            with dp_for(inst) as dp:
                out = m.run_syncs(s, inst, dp, only=sync_id)
        except DataPlaneError as e:
            raise fail(e)
        return out[0] if out else ""

    return r
