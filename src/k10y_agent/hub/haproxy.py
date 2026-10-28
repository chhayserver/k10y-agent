"""HAProxy instances managed through the HAProxy Data Plane API (v3).

The hub talks to each instance's Data Plane API directly. Reads combine configuration and live
stats into one overview. Runtime changes (drain, maintenance, ready) apply instantly without a reload.
Configuration changes always run in a Data Plane API transaction: preview shows a diff of the
rendered config and HAProxy's own validation result; apply commits the transaction, which
validates again and reloads HAProxy.
"""

import copy
import difflib
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, utcnow

TIMEOUT = httpx.Timeout(20.0, connect=6.0)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ADMIN_STATES = ("ready", "drain", "maint")
BALANCE = ("roundrobin", "static-rr", "leastconn", "first", "source", "uri", "url_param", "hdr", "random", "hash", "rdp-cookie")
# Health check types k10y offers -> HAProxy's "option ..." (adv_check). "connect" = plain TCP connect, no option.
CHECKS = {"connect": None, "http": "httpchk", "tcp": "tcp-check", "pgsql": "pgsql-check", "mysql": "mysql-check",
          "redis": "redis-check", "ssl": "ssl-hello-chk", "smtp": "smtpchk", "ldap": "ldap-check"}
CHECK_OF = {v: k for k, v in CHECKS.items() if v}
TIMEOUTS = {"connect": "connect_timeout", "server": "server_timeout", "tunnel": "tunnel_timeout"}
STICKY_COOKIE = "SERVERID"
BLOCKLIST = "k10y-blocklist.lst"          # IPs refused on every frontend; edited live through the runtime API
MAINT_MARK = "k10y-maintenance"            # marks the maintenance page rule k10y manages
IP_RE = re.compile(r"^[0-9a-fA-F:.]+(/\d{1,3})?$")
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


class HAProxySample(Base):
    """One-minute averages of the live samples, kept for a week for the longer chart ranges."""
    __tablename__ = "haproxy_samples"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(Integer, index=True)
    t: Mapped[int] = mapped_column(BigInteger, index=True)             # epoch ms, start of the minute
    data: Mapped[Any] = mapped_column(JSON)


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


# The Data Plane API (seen in v3.2.18) crashes with "concurrent map iteration and map write" when two
# transactions are opened or closed at the same moment, and restarts. So every request that touches the
# configuration, transactions or storage of one HAProxy goes through one lock (re-entrant, so a whole change
# can hold it). Live stats and runtime state changes don't need it.
_TX_LOCKS: Dict[str, Any] = {}
_TX_LOCKS_GUARD = threading.Lock()
SERIALISED = ("/configuration/", "/transactions", "/storage/")
RETRIES = (1.0, 2.0, 4.0)       # waits before retrying a read while the Data Plane API restarts


_ORPHANS: Dict[str, set] = {}   # transactions k10y opened but could not close (e.g. the API restarted mid-change)


def tx_lock(dp: Any) -> Any:
    with _TX_LOCKS_GUARD:
        return _TX_LOCKS.setdefault(getattr(dp, "base", "") or str(id(dp)), threading.RLock())


class DataPlane:
    """Small client for the Data Plane API v3."""

    def __init__(self, url: str, username: str, password: str, verify: bool = False, client: Optional[httpx.Client] = None):
        self.base = url.rstrip("/") + "/v3"
        self.c = client or httpx.Client(auth=(username, password), verify=verify, timeout=TIMEOUT)

    def close(self) -> None:
        self.c.close()

    def _send(self, do: Callable[[], httpx.Response], method: str, serial: bool) -> httpx.Response:
        """Send one request (under the instance lock when needed). Reads are retried for a few seconds if
        the Data Plane API drops the connection, e.g. while it restarts; writes are not repeated."""
        for wait in (*RETRIES, None):
            try:
                if serial:
                    with tx_lock(self):
                        return do()
                return do()
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
                if method != "GET" or wait is None:
                    hint = ("" if method == "GET" else " It may have restarted in the middle of this change: check Config history "
                            "before trying again.")
                    raise DataPlaneError(502, f"cannot reach the Data Plane API: {e}.{hint}")
                time.sleep(wait)
            except httpx.HTTPError as e:
                raise DataPlaneError(502, f"cannot reach the Data Plane API: {e}")
        raise AssertionError("unreachable")

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
        serial = bool(tx) or any(s in path for s in SERIALISED)
        r = self._send(lambda: self.c.request(method, self.base + path, params=params, json=json, content=content, headers=headers),
                       method, serial)
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
        with tx_lock(self):
            self._close_orphans()
            try:
                return self.call("POST", "/services/haproxy/transactions", params={"version": self.version()})["id"]
            except DataPlaneError as e:
                if "maximum amount" not in str(e):
                    raise
                if not self._close_outdated():
                    raise DataPlaneError(e.status, f"{e} These are left open by another tool or an earlier crash and are for the "
                                                   "current configuration, so k10y won't close them itself: delete them with "
                                                   "DELETE /v3/services/haproxy/transactions/<id> on the Data Plane API.")
                return self.call("POST", "/services/haproxy/transactions", params={"version": self.version()})["id"]

    def _close_orphans(self) -> None:
        for tx in list(_ORPHANS.get(self.base, ())):
            try:
                self.call("DELETE", f"/services/haproxy/transactions/{tx}")
            except DataPlaneError as e:
                if "not found" not in str(e).lower() and e.status != 404:
                    continue                 # still unreachable: try again next time
            _ORPHANS[self.base].discard(tx)

    def _close_outdated(self) -> int:
        """Close transactions that can never be committed: failed ones, or ones made for an older config version."""
        current, closed = self.version(), 0
        for tr in self.call("GET", "/services/haproxy/transactions") or []:
            if tr.get("status") != "in_progress" or int(tr.get("_version") or 0) < current:
                try:
                    self.call("DELETE", f"/services/haproxy/transactions/{tr['id']}")
                    closed += 1
                except DataPlaneError:
                    pass
        return closed

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
        except DataPlaneError as e:
            if "not found" not in str(e).lower():
                _ORPHANS.setdefault(self.base, set()).add(tx)     # close it before the next change

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
        r = self._send(lambda: self.c.post(self.base + "/services/haproxy/storage/ssl_certificates",
                                           files={"file_upload": (name, pem.encode(), "application/x-pem-file")}), "POST", True)
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

    def set_stick_entry(self, table: str, key: str, data: Dict[str, int]) -> None:
        self.call("POST", f"/services/haproxy/runtime/stick_tables/{table}/entries", json={"key": key, "data_type": data})

    # general files and runtime ACL lists (IP blocklist) --------------------------
    def general_files(self) -> List[Dict[str, Any]]:
        return self.call("GET", "/services/haproxy/storage/general") or []

    def put_general_file(self, name: str, text: str, create: bool) -> Dict[str, Any]:
        """Create or replace a file in the Data Plane API storage without reloading HAProxy."""
        files = {"file_upload": (name, text.encode(), "text/plain")}
        url = self.base + "/services/haproxy/storage/general" + ("" if create else f"/{name}")
        r = self._send(lambda: self.c.post(url, files=files) if create else self.c.put(url, files=files, params={"skip_reload": "true"}),
                       "POST" if create else "PUT", True)
        if r.status_code >= 400:
            raise DataPlaneError(400 if r.status_code < 500 else 502, _message(r))
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else {}

    def runtime_acl_id(self, path: str) -> Optional[str]:
        for a in self.call("GET", "/services/haproxy/runtime/acls") or []:
            if a.get("storage_name") == path:
                return str(a.get("id"))
        return None

    def acl_entries(self, acl_id: str) -> List[Dict[str, Any]]:
        try:
            return self.call("GET", f"/services/haproxy/runtime/acls/{acl_id}/entries") or []
        except DataPlaneError as e:
            if "not found" in str(e):      # the API answers "not found" for an empty list
                return []
            raise

    def add_acl_entry(self, acl_id: str, value: str) -> None:
        self.call("POST", f"/services/haproxy/runtime/acls/{acl_id}/entries", json={"value": value})

    def delete_acl_entry(self, acl_id: str, entry_id: str) -> None:
        self.call("DELETE", f"/services/haproxy/runtime/acls/{acl_id}/entries/{entry_id}")


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


def _term(crit: str, raw: str) -> Optional[Dict[str, str]]:
    """One ACL test (criterion + value) -> a simple route kind, or None."""
    kind = ROUTE_CRITERIA.get(crit)
    header = HEADER_RE.match(crit) if crit and not kind else None
    value = re.sub(r"^(-i|-m str)\s+", "", raw or "").strip()
    if header:
        return {"kind": "header", "value": value, "header": header.group(1)}
    if kind:
        return {"kind": kind, "value": ",".join(value.split()) if kind == "src" else value}
    return None


def _inline(cond_test: str) -> Optional[List[Tuple[str, str]]]:
    """'{ path /order } || { path_beg /order/ }' -> [("path", "/order"), ("path_beg", "/order/")]."""
    terms = []
    for part in cond_test.split("||"):
        m = re.fullmatch(r"\s*\{\s*(\S+)\s+([^{}]*?)\s*\}\s*", part)
        if not m:
            return None
        terms.append((m.group(1), m.group(2)))
    return terms


def parse_routes(fe: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """use_backend rules -> routes. Understands named ACLs (acl is_api path_beg /api), inline conditions
    ({ path_beg /api }), the service style ({ path /order } || { path_beg /order/ }) and rules without a
    condition; anything else is kept as a read-only "other" route."""
    acls: Dict[str, List[Dict[str, Any]]] = {}
    for a in fe.get("acl_list") or []:
        acls.setdefault(a["acl_name"], []).append(a)
    routes, simple = [], True
    for rule in fe.get("backend_switching_rule_list") or []:
        test, cond, be = (rule.get("cond_test") or "").strip(), rule.get("cond") or "", rule.get("name")
        route = None
        if not cond and not test:
            route = {"kind": "always", "value": "", "style": "always"}
        elif cond == "if" and re.fullmatch(r"[\w.-]+", test) and len(acls.get(test, [])) == 1:
            a = acls[test][0]
            route = _term(a["criterion"], a.get("value") or "")
            if route:
                route["style"] = "acl"
        elif cond == "if" and (terms := _inline(test)):
            if len(terms) == 2 and {terms[0][0], terms[1][0]} == {"path", "path_beg"}:
                exact = next(v for c, v in terms if c == "path").strip()
                under = next(v for c, v in terms if c == "path_beg").strip()
                if under == exact.rstrip("/") + "/":
                    route = {"kind": "service", "value": exact, "style": "inline"}
            elif len(terms) == 1:
                route = _term(*terms[0])
                if route:
                    route["style"] = "inline"
        if route:
            routes.append({**route, "backend": be})
        else:
            simple = False
            routes.append({"kind": "other", "value": f"{cond} {test}".strip(), "backend": be})
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


def _is_xfport(r: Dict[str, Any]) -> bool:
    return r.get("type") == "set-header" and (r.get("hdr_name") or "").lower() == "x-forwarded-port" \
        and r.get("hdr_format") == "%[dst_port]" and not r.get("cond")


def _is_maint(r: Dict[str, Any]) -> bool:
    return r.get("type") == "return" and int(r.get("return_status_code") or 0) == 503 and MAINT_MARK in (r.get("return_content") or "")


def _is_block(r: Dict[str, Any]) -> bool:
    return r.get("type") == "connection" and r.get("action") == "reject" and BLOCKLIST in (r.get("cond_test") or "")


def _maint_page(message: str) -> str:
    # One line, no double quotes: HAProxy keeps it as a quoted string in the config.
    msg = re.sub(r'["\\<>$\r\n]', "", message or "").strip()[:300] or "We're doing some maintenance. Please try again in a few minutes."
    return (f"<!-- {MAINT_MARK} --><!doctype html><html><head><meta charset='utf-8'><title>Back soon</title></head>"
            "<body style='font-family:system-ui,sans-serif;text-align:center;padding:18vh 20px;color:#333'>"
            f"<h1 style='font-weight:600'>Back soon</h1><p>{msg}</p></body></html>")


def parse_policies(fe: Dict[str, Any]) -> Dict[str, Any]:
    rules = fe.get("http_request_rule_list") or []
    allow = next((r for r in rules if _is_allow(r)), None)
    maint = next((r for r in rules if _is_maint(r)), None)
    limit = next((r for r in rules if _is_limit(r)), None)
    st = fe.get("stick_table") or {}
    period = None
    m = re.search(r"http_req_rate\((\d+)s?\)", st.get("store") or "")
    if m:
        period = int(m.group(1))
    managed = [r for r in rules if _is_allow(r) or _is_redirect(r) or (_is_track(r) and limit) or _is_limit(r) or _is_maint(r) or _is_xfport(r)]
    msg = re.search(r"<p>(.*?)</p>", (maint or {}).get("return_content") or "")
    return {
        "maintenance": {"enabled": maint is not None, "message": msg.group(1) if msg else "",
                        "allow_ips": re.findall(r"[^\s{}]+", maint.get("cond_test") or "")[1:] if maint and maint.get("cond") == "unless" else []},
        "blocklist": any(_is_block(r) for r in fe.get("tcp_request_rule_list") or []),
        "allow_ips": re.findall(r"[^\s{}]+", (allow or {}).get("cond_test", ""))[1:] if allow else [],
        "redirect_https": any(_is_redirect(r) for r in rules),
        "forwarded_port": any(_is_xfport(r) for r in rules),
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
                  "certificate": (b.get("ssl_certificate") or "").rsplit("/", 1)[-1], "certificate_file": b.get("ssl_certificate") or ""}
                 for n, b in (fe.get("binds") or {}).items()]
        frontends.append({"name": fe["name"], "mode": fe.get("mode") or "http", "binds": binds,
                          "default_backend": fe.get("default_backend"), "routes": routes, "simple_routes": simple,
                          "stats_page": bool((fe.get("stats_options") or {}).get("stats_enable")),
                          "policies": parse_policies(fe),
                          "timeouts": {"client": (fe.get("client_timeout") or 0) // 1000},
                          "stats": idx.get(("frontend", fe["name"]), {})})
    backends = []
    for be in bes:
        servers = []
        for name, s in (be.get("servers") or {}).items():
            st = idx.get(("server", f"{be['name']}/{name}"), {})
            servers.append({"name": name, "address": s.get("address"), "port": s.get("port"), "weight": s.get("weight", 1),
                            "inter": (s.get("inter") or 0) / 1000, "rise": s.get("rise") or 0, "fall": s.get("fall") or 0,
                            "check": s.get("check") == "enabled", "backup": s.get("backup") == "enabled",
                            "maxconn": s.get("maxconn") or 0, "ssl": s.get("ssl") == "enabled",
                            "state": _server_state(st.get("status")), "stats": st})
        chk, bal = be.get("adv_check"), be.get("balance") or {}
        hc = be.get("httpchk_params") or {}
        backends.append({"name": be["name"], "mode": be.get("mode") or "http", "balance": bal.get("algorithm") or "roundrobin",
                         "balance_arg": bal.get("hdr_name") or bal.get("url_param") or bal.get("hash_expression") or bal.get("rdp_cookie_name") or "",
                         "check": {"type": CHECK_OF.get(chk, "connect"), "uri": hc.get("uri") or "", "method": hc.get("method") or "GET",
                                   "host": hc.get("host") or "",
                                   "user": (be.get("mysql_check_params") or be.get("pgsql_check_params") or {}).get("username") or ""},
                         "sticky": bool(be.get("cookie")), "forwardfor": (be.get("forwardfor") or {}).get("enabled") == "enabled",
                         "redispatch": (be.get("redispatch") or {}).get("enabled") == "enabled", "retries": be.get("retries") or 0,
                         "keepalive": "enabled" in (be.get("tcpka"), be.get("srvtcpka")),
                         "stick_src": _stick_src_minutes(be), "check_timing": _check_timing(be),
                         "timeouts": {k: (be.get(f) or 0) // 1000 for k, f in TIMEOUTS.items()},
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
    """Routes -> ACLs + use_backend rules. A route that already uses a named ACL keeps it (so editing one route
    doesn't rename the others); every other route is written inline, e.g. if { path /order } || { path_beg /order/ }."""
    reuse = {(a["criterion"], a.get("value")): a["acl_name"] for a in existing or []}
    acls, rules, taken = [], [], set()
    for i, r in enumerate(routes, 1):
        kind, value, be = r.get("kind"), str(r.get("value") or "").strip(), r.get("backend")
        if be not in backends:
            raise DataPlaneError(400, f"route {i}: unknown backend {be!r}")
        if kind == "always":
            if i != len(routes):
                raise DataPlaneError(400, f"route {i}: a route for all traffic must be the last one")
            rules.append({"name": be})
            continue
        if kind not in ("path_prefix", "path", "service", "host", "header", "src") or not value or re.search(r"\s", value):
            raise DataPlaneError(400, f"route {i}: choose a type and a value without spaces")
        if kind in ("path_prefix", "path", "service") and not value.startswith("/"):
            raise DataPlaneError(400, f"route {i}: a path starts with /, e.g. /order")
        if kind == "header" and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(r.get("header") or "")):
            raise DataPlaneError(400, f"route {i}: header name must be letters, digits or dashes")
        if kind == "src" and not all(re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", x) for x in value.split(",")):
            raise DataPlaneError(400, f"route {i}: source must be IPs or networks like 10.0.0.0/8, separated by commas")
        if kind == "service":
            base = value.rstrip("/") or "/"
            test = "{ path_beg / }" if base == "/" else "{ path %s } || { path_beg %s/ }" % (base, base)
            rules.append({"name": be, "cond": "if", "cond_test": test})
            continue
        criterion = {"path_prefix": "path_beg", "path": "path", "host": "hdr(host)", "src": "src"}.get(kind) or f"hdr({r['header']})"
        acl_value = f"-i {value}" if kind == "host" else f"-m str {value}" if kind == "header" else value.replace(",", " ") if kind == "src" else value
        name = reuse.get((criterion, acl_value)) if r.get("style") == "acl" else None
        if name and name not in taken:
            taken.add(name)
            acls.append({"acl_name": name, "criterion": criterion, "value": acl_value})
            rules.append({"name": be, "cond": "if", "cond_test": name})
        else:
            rules.append({"name": be, "cond": "if", "cond_test": "{ %s %s }" % (criterion, acl_value)})
    return acls, rules


POLICY_KINDS = (("maint", _is_maint), ("allow", _is_allow), ("xfport", _is_xfport), ("redirect", _is_redirect),
                ("track", _is_track), ("limit", _is_limit))


def _apply_policies(fe: Dict[str, Any], pol: Dict[str, Any]) -> None:
    """Rewrite the managed http-request rules (maintenance page, allow-list, X-Forwarded-Port, HTTPS redirect,
    rate limit). Other rules are kept, and a managed rule that doesn't change keeps its place and exact form
    (e.g. a redirect written without a code stays that way)."""
    rules = fe.get("http_request_rule_list") or []
    had_limit = any(_is_limit(r) for r in rules)
    want: Dict[str, Any] = {}
    # a track-sc rule is ours only when it belongs to a rate limit (before or after this change)
    kind_of = lambda r: next((k for k, f in POLICY_KINDS if f(r) and (k != "track" or had_limit or want.get("limit"))), None)
    if "maintenance" not in pol:
        want["maint"] = next((r for r in rules if _is_maint(r)), None)
    elif (pol.get("maintenance") or {}).get("enabled"):
        maint = pol["maintenance"]
        bypass = [x.strip() for x in maint.get("allow_ips") or [] if str(x).strip()]
        for x in bypass:
            if not IP_RE.match(x):
                raise DataPlaneError(400, f"maintenance: {x!r} is not an IP address or network")
        rule = {"type": "return", "return_status_code": 503, "return_content_type": "text/html",
                "return_content_format": "string", "return_content": f'"{_maint_page(maint.get("message") or "")}"'}
        if bypass:
            rule.update({"cond": "unless", "cond_test": "{ src " + " ".join(bypass) + " }"})
        want["maint"] = rule
    ips = [x.strip() for x in pol.get("allow_ips") or [] if str(x).strip()]
    for x in ips:
        if not re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", x):
            raise DataPlaneError(400, f"allowed source {x!r} is not an IP address or network")
    if ips:
        want["allow"] = {"type": "deny", "deny_status": 403, "cond": "unless", "cond_test": "{ src " + " ".join(ips) + " }"}
    keep_xf = "forwarded_port" not in pol and any(_is_xfport(r) for r in rules)
    if pol.get("forwarded_port") or keep_xf:
        want["xfport"] = {"type": "set-header", "hdr_name": "X-Forwarded-Port", "hdr_format": "%[dst_port]"}
    if pol.get("redirect_https"):
        want["redirect"] = {"type": "redirect", "redir_type": "scheme", "redir_value": "https", "cond": "unless", "cond_test": "{ ssl_fc }"}
    limit = pol.get("rate_limit")
    if limit:
        n, period = int(limit.get("requests") or 0), int(limit.get("period") or 10)
        if not 1 <= n <= 1_000_000 or not 1 <= period <= 3600:
            raise DataPlaneError(400, "rate limit: 1-1000000 requests per 1-3600 seconds")
        st = fe.get("stick_table") or {}
        if st and "http_req_rate" not in (st.get("store") or ""):
            raise DataPlaneError(400, "this frontend already has a different stick table; set the rate limit in the raw config")
        fe["stick_table"] = {"type": "ip", "size": 100000, "expire": period * 1000, "store": f"http_req_rate({period}s)"}
        want["track"] = {"type": "track-sc", "track_sc_key": "src", "track_sc_stick_counter": 0}
        want["limit"] = {"type": "deny", "deny_status": 429, "cond": "if", "cond_test": "{ sc_http_req_rate(0) gt %d }" % n}
    elif had_limit and "http_req_rate" in ((fe.get("stick_table") or {}).get("store") or ""):
        fe.pop("stick_table", None)
    out, seen = [], set()
    for r in rules:
        k = kind_of(r)
        if k is None:
            out.append(r)
        elif k not in seen and want.get(k):
            seen.add(k)
            # unchanged meaning -> keep the line exactly as it was written
            same = k in ("redirect", "xfport", "track", "maint") if k != "maint" else r == want[k]
            out.append(r if same or r == want[k] else want[k])
        else:
            seen.add(k)
    order = [k for k, _ in POLICY_KINDS]
    for k in order:
        if not want.get(k) or k in seen:
            continue
        # before the first managed rule that comes later in the natural order, else after the last managed one
        later = next((i for i, r in enumerate(out) if (rk := kind_of(r)) and order.index(rk) > order.index(k)), None)
        last = max((i for i, r in enumerate(out) if kind_of(r)), default=-1)
        out.insert(later if later is not None else last + 1, want[k])
    fe["http_request_rule_list"] = out


def _balance(alg: Any, arg: Any) -> Dict[str, Any]:
    if isinstance(alg, dict):
        alg, arg = alg.get("algorithm"), alg.get("arg", arg)
    arg = str(arg or "").strip()
    if alg not in BALANCE:
        raise DataPlaneError(400, f"balance must be one of {', '.join(BALANCE)}")
    out: Dict[str, Any] = {"algorithm": alg}
    if alg == "hdr":
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", arg):
            raise DataPlaneError(400, "balance by header: enter the header name, e.g. X-User-Id")
        out["hdr_name"] = arg
    elif alg == "url_param":
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", arg):
            raise DataPlaneError(400, "balance by URL parameter: enter the parameter name, e.g. user_id")
        out["url_param"] = arg
    elif alg == "hash":
        if not arg or re.search(r"[\s{}]", arg):
            raise DataPlaneError(400, "balance by hash: enter a sample expression without spaces, e.g. req.cook(session)")
        out["hash_expression"] = arg
    elif alg == "rdp-cookie" and arg:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", arg):
            raise DataPlaneError(400, "RDP cookie name: letters, digits, dash or underscore")
        out["rdp_cookie_name"] = arg
    return out


def _set_check(be: Dict[str, Any], chk: Dict[str, Any]) -> None:
    typ = chk.get("type") or "connect"
    typ = "connect" if typ == "default" else typ
    if typ not in CHECKS:
        raise DataPlaneError(400, f"health check must be one of {', '.join(CHECKS)}")
    for k in ("adv_check", "httpchk_params", "mysql_check_params", "pgsql_check_params", "smtpchk_params"):
        be.pop(k, None)
    if CHECKS[typ]:
        be["adv_check"] = CHECKS[typ]
    if typ == "http":
        uri, method = str(chk.get("uri") or "/").strip(), str(chk.get("method") or "GET").upper()
        if not uri.startswith("/") or re.search(r"\s", uri):
            raise DataPlaneError(400, "health check path must start with / and have no spaces")
        if method not in ("GET", "HEAD", "OPTIONS"):
            raise DataPlaneError(400, "health check method must be GET, HEAD or OPTIONS")
        params = {"method": method, "uri": uri}
        host = str(chk.get("host") or "").strip()
        if host:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}(:\d+)?", host):
                raise DataPlaneError(400, "health check host must be a host name like api.example.com")
            params.update({"version": "HTTP/1.1", "host": host})
        be["httpchk_params"] = params
    elif typ in ("mysql", "pgsql"):
        user = str(chk.get("user") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user):
            raise DataPlaneError(400, f"{'MySQL' if typ == 'mysql' else 'PostgreSQL'} check: enter the user it logs in with, e.g. haproxy_check")
        be[f"{typ}_check_params"] = {"username": user, **({"client_version": "post-41"} if typ == "mysql" else {})}


def _stick_src_minutes(be: Dict[str, Any]) -> int:
    """'stick-table type ip … expire 5m' + 'stick on src' -> 5 (minutes); 0 when the backend doesn't do that."""
    st, rules = be.get("stick_table") or {}, be.get("stick_rule_list") or []
    on_src = any(r.get("type") == "on" and r.get("pattern") == "src" and not r.get("table") for r in rules)
    return max(1, round((st.get("expire") or 0) / 60000)) if on_src and st.get("type") == "ip" and not st.get("store") else 0


def _set_stick_src(be: Dict[str, Any], minutes: int) -> None:
    if not 0 <= minutes <= 10080:
        raise DataPlaneError(400, "keep clients on the same server for 0-10080 minutes (0 = off)")
    st = be.get("stick_table") or {}
    if minutes and st and (st.get("store") or st.get("type") != "ip"):
        raise DataPlaneError(400, "this backend already has another stick table; change it in the raw config")
    others = [r for r in be.get("stick_rule_list") or [] if not (r.get("type") == "on" and r.get("pattern") == "src" and not r.get("table"))]
    if minutes:
        be["stick_table"] = {"type": "ip", "size": 204800, "expire": minutes * 60000}      # size 200k
        be["stick_rule_list"] = others + [{"type": "on", "pattern": "src"}]
    elif _stick_src_minutes(be):
        be.pop("stick_table", None)
        if others:
            be["stick_rule_list"] = others
        else:
            be.pop("stick_rule_list", None)


def _timing(t: Dict[str, Any]) -> Dict[str, int]:
    """Health check timing in seconds/counts -> server params (inter in ms)."""
    inter, rise, fall = float(t.get("inter") or 0), int(t.get("rise") or 0), int(t.get("fall") or 0)
    if inter and not 0.1 <= inter <= 3600:
        raise DataPlaneError(400, "check interval must be 0.1-3600 seconds")
    if not 0 <= rise <= 100 or not 0 <= fall <= 100:
        raise DataPlaneError(400, "rise and fall must be 1-100 checks")
    return {k: v for k, v in (("inter", int(round(inter * 1000))), ("rise", rise), ("fall", fall)) if v}


def _check_timing(be: Dict[str, Any]) -> Dict[str, float]:
    s = next((x for x in (be.get("servers") or {}).values() if x.get("inter") or x.get("rise") or x.get("fall")), {})
    return {"inter": (s.get("inter") or 0) / 1000, "rise": s.get("rise") or 0, "fall": s.get("fall") or 0}


def _block_rule(path: str) -> Dict[str, Any]:
    return {"type": "connection", "action": "reject", "cond": "if", "cond_test": "{ src -f %s }" % path}


# Parts of a section the Data Plane API can replace on their own. Sending only what changed keeps every other line
# exactly as it was written (a full-section save re-renders the whole section: "60s" becomes "1m", bind options move).
SECTION_PARTS = {
    "frontend": {"acl_list": "acls", "backend_switching_rule_list": "backend_switching_rules", "http_request_rule_list": "http_request_rules",
                 "http_response_rule_list": "http_response_rules", "tcp_request_rule_list": "tcp_request_rules"},
    "backend": {"acl_list": "acls", "http_request_rule_list": "http_request_rules", "http_response_rule_list": "http_response_rules",
                "stick_rule_list": "stick_rules", "tcp_request_rule_list": "tcp_request_rules", "server_switching_rule_list": "server_switching_rules",
                "http_check_list": "http_checks", "tcp_check_rule_list": "tcp_checks"},
}
NESTED = {"frontend": "binds", "backend": "servers"}


def _base(section: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in section.items() if not (k.endswith("_list") or k in ("binds", "servers", "server_templates", "ssl_front_uses"))}


def _save_section(dp: DataPlane, tx: str, kind: str, name: str, current: Optional[Dict[str, Any]], new: Dict[str, Any]) -> None:
    """Create a section, or write only the parts of it that changed."""
    coll = f"/services/haproxy/configuration/{kind}s"
    if current is None:
        dp.call("POST", coll, tx, json=new, params={"full_section": "true"})
        return
    parts, nested = SECTION_PARTS[kind], NESTED[kind]
    other = [k for k in set(new) | set(current) if k.endswith("_list") and k not in parts and new.get(k) != current.get(k)]
    if other:       # something k10y doesn't write part by part: save the whole section
        dp.call("PUT", f"{coll}/{name}", tx, json=new, params={"full_section": "true"})
        return
    if _base(new) != _base(current):
        dp.call("PUT", f"{coll}/{name}", tx, json=_base(new))
    for key, endpoint in parts.items():
        if (new.get(key) or []) != (current.get(key) or []):
            dp.call("PUT", f"{coll}/{name}/{endpoint}", tx, json=new.get(key) or [])
    old_items, new_items = current.get(nested) or {}, new.get(nested) or {}
    for n in old_items.keys() - new_items.keys():
        dp.call("DELETE", f"{coll}/{name}/{nested}/{n}", tx)
    for n, item in new_items.items():
        if n not in old_items:
            dp.call("POST", f"{coll}/{name}/{nested}", tx, json=item)
        elif item != old_items[n]:
            dp.call("PUT", f"{coll}/{name}/{nested}/{n}", tx, json=item)


def apply_op(dp: DataPlane, tx: str, op: Op) -> str:
    """Apply one operation inside a transaction. Returns a short description for the audit log."""
    cfg = "/services/haproxy/configuration"
    d = op.data
    if op.kind == "frontend":
        name = _check_name(op.name, "Frontend")
        if op.op == "delete":
            dp.call("DELETE", f"{cfg}/frontends/{name}", tx)
            return f"delete frontend {name}"
        backends = {b["name"] for b in dp.backends(tx)}
        current = next((f for f in dp.frontends(tx) if f["name"] == name), None)
        if op.op == "create" and current:
            raise DataPlaneError(400, f"frontend {name} already exists")
        if op.op == "update" and not current:
            raise DataPlaneError(404, f"frontend {name} not found")
        fe = copy.deepcopy(current) if current else {"name": name}
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
                if b.get("ssl") and str(b.get("certificate") or "").startswith("/"):
                    path = str(b["certificate"]).strip()
                    if re.search(r"\s", path):
                        raise DataPlaneError(400, f"listen {addr}:{port}: the certificate path can't contain spaces")
                    bind.update({"ssl": True, "ssl_certificate": path})      # a file on the HAProxy server, e.g. /etc/haproxy/site.pem
                elif b.get("ssl"):
                    cert = str(b.get("certificate") or "")
                    if cert_dir is None:
                        certs = {c["storage_name"]: c.get("file") for c in dp.call("GET", "/services/haproxy/storage/ssl_certificates") or []}
                        cert_dir = certs
                    if cert not in cert_dir:
                        raise DataPlaneError(400, f"listen {addr}:{port}: choose an uploaded certificate, or the path of a certificate file on the HAProxy server")
                    bind.update({"ssl": True, "ssl_certificate": cert_dir[cert]})
                binds[bind["name"]] = bind
            if not binds:
                raise DataPlaneError(400, "a frontend needs at least one listen address")
            fe["binds"] = binds
        if "timeouts" in d:
            fe.pop("client_timeout", None)
            client = int((d["timeouts"] or {}).get("client") or 0)
            if client:
                if not 1 <= client <= 86400:
                    raise DataPlaneError(400, "client timeout must be 1-86400 seconds")
                fe["client_timeout"] = client * 1000
        if d.get("policies") is not None:
            _apply_policies(fe, d["policies"])
        if op.op == "create":
            # A frontend created after the blocklist was set up gets the same protection.
            path = next((f.get("file") for f in dp.general_files() if f.get("storage_name") == BLOCKLIST), None)
            if path:
                fe["tcp_request_rule_list"] = [_block_rule(path)] + (fe.get("tcp_request_rule_list") or [])
        if d.get("routes") is not None:
            used = {r.get("cond_test") for r in fe.get("backend_switching_rule_list") or []}
            acls, rules = _routes_to_config(d["routes"], backends, [a for a in fe.get("acl_list") or [] if a["acl_name"] in used])
            fe["acl_list"] = [a for a in fe.get("acl_list") or [] if a["acl_name"] not in used] + acls
            fe["backend_switching_rule_list"] = rules
        _save_section(dp, tx, "frontend", name, current, fe)
        return f"{op.op} frontend {name}"

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
        be = copy.deepcopy(current) if current else {"name": name}
        if "mode" in d:
            if d["mode"] not in ("http", "tcp"):
                raise DataPlaneError(400, "mode must be http or tcp")
            be["mode"] = d["mode"]
        if "balance" in d:
            be["balance"] = _balance(d["balance"], d.get("balance_arg"))
        if "check" in d:
            _set_check(be, d["check"] or {})
        be.setdefault("servers", {})
        if "sticky" in d:
            if d["sticky"]:
                if (be.get("mode") or "http") != "http":
                    raise DataPlaneError(400, "sticky sessions need an HTTP backend (they use a cookie)")
                be["cookie"] = {"name": STICKY_COOKIE, "type": "insert", "indirect": True, "nocache": True}
                for n, s in be["servers"].items():
                    s.setdefault("cookie", n)
            else:
                be.pop("cookie", None)
                for s in be["servers"].values():
                    s.pop("cookie", None)
        if "forwardfor" in d:
            if d["forwardfor"]:
                be["forwardfor"] = {"enabled": "enabled"}
            else:
                be.pop("forwardfor", None)
        if "redispatch" in d:
            if d["redispatch"]:
                be["redispatch"] = {"enabled": "enabled"}
            else:
                be.pop("redispatch", None)
        if "retries" in d:
            n = int(d["retries"] or 0)
            if not 0 <= n <= 100:
                raise DataPlaneError(400, "retries must be 0-100")
            if n:
                be["retries"] = n
            else:
                be.pop("retries", None)
        if "keepalive" in d:
            for k in ("tcpka", "srvtcpka"):
                if d["keepalive"]:
                    be[k] = "enabled"
                else:
                    be.pop(k, None)
        if "stick_src" in d:
            _set_stick_src(be, int(d["stick_src"] or 0))
        if "check_timing" in d:
            timing = _timing(d["check_timing"] or {})
            for s in be["servers"].values():
                for k in ("inter", "rise", "fall"):
                    if timing.get(k):
                        s[k] = timing[k]
                    else:
                        s.pop(k, None)
        if "timeouts" in d:
            for k, field in TIMEOUTS.items():
                v = int((d["timeouts"] or {}).get(k) or 0)
                be.pop(field, None)
                if v:
                    if not 1 <= v <= 86400:
                        raise DataPlaneError(400, f"{k} timeout must be 1-86400 seconds")
                    be[field] = v * 1000
        _save_section(dp, tx, "backend", name, current, be)
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
        if "maxconn" in d:
            m = int(d["maxconn"] or 0)
            if not 0 <= m <= 1_000_000:
                raise DataPlaneError(400, "max connections must be 0-1000000 (0 = no limit)")
            if m:
                srv["maxconn"] = m
            else:
                srv.pop("maxconn", None)
        if "ssl" in d:
            if d["ssl"]:
                srv.update({"ssl": "enabled", "verify": "none"})
            else:
                srv.pop("ssl", None)
                srv.pop("verify", None)
        if any(k in d for k in ("inter", "rise", "fall")):
            for k, v in _timing({k: d.get(k) for k in ("inter", "rise", "fall")}).items():
                srv[k] = v
        elif op.op == "create":
            sibling = next(iter(dp.call("GET", base, tx) or []), None)
            for k in ("inter", "rise", "fall"):
                if sibling and sibling.get(k):
                    srv[k] = sibling[k]
        if op.op == "create" and (dp.call("GET", f"{cfg}/backends/{backend}", tx) or {}).get("cookie"):
            srv["cookie"] = name          # sticky sessions: each server needs its own cookie value
        if op.op == "create":
            dp.call("POST", base, tx, json=srv)
        else:
            dp.call("PUT", f"{base}/{name}", tx, json=srv)
        return f"{op.op} server {backend}/{name}"

    raise DataPlaneError(400, f"unsupported operation {op.op} {op.kind}")


LOG_TAG_RE = re.compile(r"^\s*log-tag\s")


def _keep_log_tag(ref: str, theirs: str) -> str:
    """ref's configuration, but with the other instance's own log-tag lines (in the same place when ref has one)."""
    own = [l for l in theirs.splitlines() if LOG_TAG_RE.match(l)]
    lines = ref.splitlines()
    at = next((i for i, l in enumerate(lines) if LOG_TAG_RE.match(l)), None)
    if at is None:      # at the end of the global section, after its last setting
        g = next((i for i, l in enumerate(lines) if re.match(r"^global\b", l)), None)
        at = len(lines) if g is None else next((i for i in range(g + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines))
        while g is not None and at > g + 1 and not lines[at - 1].strip():
            at -= 1
    out = [l for l in lines[:at] if not LOG_TAG_RE.match(l)] + own + [l for l in lines[at:] if not LOG_TAG_RE.match(l)]
    return "\n".join(out) + "\n"
HEADER_LINE = re.compile(r"^# (_md5hash=|_version=|Dataplaneapi managed File|changing file directly)")


def _diff(before: str, after: str) -> str:
    """Unified diff without the Data Plane API's file header (it changes on every save)."""
    body = lambda text: [l for l in text.splitlines() if not HEADER_LINE.match(l)]
    return "\n".join(difflib.unified_diff(body(before), body(after), "current", "new", lineterm="", n=3))


def plan_and_apply(dp: DataPlane, ops: List[Op], apply: bool) -> Dict[str, Any]:
    """Preview (diff + validation) or apply a list of operations as one change."""
    with tx_lock(dp):
        return _plan_and_apply(dp, ops, apply)


def _plan_and_apply(dp: DataPlane, ops: List[Op], apply: bool) -> Dict[str, Any]:
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


class StickResetBody(BaseModel):
    key: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n;#]+$")


class BlockBody(BaseModel):
    ip: str = Field(min_length=2, max_length=64)
    reason: str = Field("", max_length=200)


class AiBody(BaseModel):
    scope: str = Field("instance", pattern="^(instance|frontend|service|backend)$")
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
        return [view(i) for i in s.execute(select(HAProxyInstance).order_by(HAProxyInstance.name)).scalars()
                if p.can("haproxy.view", haproxy_id=i.id)]           # only the instances this user has a role on

    @r.post("/test")
    def test_instance(body: TestBody, p=Depends(require("haproxy.manage")), s: Session = Depends(db_session)) -> dict:
        # testing a new instance needs haproxy.manage on all of HAProxy; an existing one, on that instance
        if not p.can("haproxy.manage", haproxy_id=body.instance_id) if body.instance_id else not p.can("haproxy.manage"):
            raise HTTPException(status_code=403, detail="missing permission: haproxy.manage")
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
    def create_instance(body: InstanceBody, request: Request, p=Depends(require("haproxy.manage")),
                        s: Session = Depends(db_session)) -> dict:
        if not p.can("haproxy.manage"):   # adding an instance is for roles on all of HAProxy
            raise HTTPException(status_code=403, detail="missing permission: haproxy.manage (on all HAProxy instances)")
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
    def update_instance(iid: int, body: InstanceBody, request: Request, p=Depends(require("haproxy.manage")),
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
    def delete_instance(iid: int, request: Request, p=Depends(require("haproxy.manage")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        for model in (HAProxyConfigVersion, HAProxyK8sSync):
            for row in s.execute(select(model).where(model.instance_id == iid)).scalars():
                s.delete(row)
        from .db import RoleAssignment          # roles given on this instance go with it
        for row in s.execute(select(RoleAssignment).where(RoleAssignment.haproxy_id == iid)).scalars():
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
                                                                           HAProxyInstance.id != iid)).scalars()
                 if p.can("haproxy.view", haproxy_id=i.id)] if inst.group_name else []
        return {"instance": view(inst), "group": group, **data}

    @r.get("/{iid}/history")
    def history(iid: int, since: int = 0, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        get(s, iid)
        m = monitor()
        # Up to an hour: 10-second samples from memory. Longer ranges: one-minute averages from the database.
        if since and since < int(time.time() * 1000) - 3_600_000:
            rows = s.execute(select(HAProxySample.t, HAProxySample.data).where(HAProxySample.instance_id == iid, HAProxySample.t > since)
                             .order_by(HAProxySample.t)).all()
            return {"points": [[t_, d] for t_, d in rows], "interval_s": 60}
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
            with dp_for(get(s, iid)) as dp, tx_lock(dp):
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

    @r.post("/{iid}/stick-tables/{table}/reset")
    def stick_reset(iid: int, table: str, body: StickResetBody, request: Request,
                    p=Depends(require("haproxy.operate")), s: Session = Depends(db_session)) -> dict:
        """Set an entry's rates back to zero, e.g. to let a client that hit the rate limit in again."""
        inst = get(s, iid)
        _check_name(table, "Table")
        try:
            with dp_for(inst) as dp:
                entry = next((e for e in dp.stick_entries(table, 100000) if e.get("key") == body.key), None)
                if entry is None:
                    raise DataPlaneError(404, f"{body.key} is not in table {table} (it may have expired already)")
                rates = {k: 0 for k in entry if k.endswith("_rate") and entry[k] is not None}
                if not rates:
                    raise DataPlaneError(400, "this table stores no rates to reset")
                dp.set_stick_entry(table, body.key, rates)
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-unblock", f"{inst.name}/{table}/{body.key}", ok=False, message=str(e))
            raise fail(e)
        audit(s, request, p, "haproxy-unblock", f"{inst.name}/{table}/{body.key}", message="rates reset to 0")
        return {"ok": True, "message": f"{body.key} can send requests again"}

    # --- IP blocklist: refused on every frontend, changed live without a reload --------------
    def blocklist_state(dp: DataPlane) -> Dict[str, Any]:
        path = next((f.get("file") for f in dp.general_files() if f.get("storage_name") == BLOCKLIST), None)
        fes = [f for f in dp.frontends() if not (f.get("stats_options") or {}).get("stats_enable")]
        protected = [f["name"] for f in fes if any(_is_block(r) for r in f.get("tcp_request_rule_list") or [])]
        acl = dp.runtime_acl_id(path) if path and protected else None
        entries = dp.acl_entries(acl) if acl else []
        return {"path": path, "acl": acl, "entries": entries, "ready": bool(path and acl and len(protected) == len(fes)),
                "protected": protected, "missing": [f["name"] for f in fes if f["name"] not in protected]}

    @r.get("/{iid}/blocklist")
    def get_blocklist(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        try:
            with dp_for(get(s, iid)) as dp:
                st = blocklist_state(dp)
        except DataPlaneError as e:
            raise fail(e)
        return {"ready": st["ready"], "ips": [e.get("value") for e in st["entries"]], "protected": st["protected"], "missing": st["missing"]}

    def save_blocklist(dp: DataPlane, ips: List[str]) -> None:
        dp.put_general_file(BLOCKLIST, "".join(f"{x}\n" for x in ips), create=False)

    @r.post("/{iid}/blocklist")
    def block_ip(iid: int, body: BlockBody, request: Request, p=Depends(require("haproxy.operate")),
                 s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        ip = body.ip.strip()
        try:
            if not IP_RE.match(ip):
                raise DataPlaneError(400, f"{ip!r} is not an IP address or network like 203.0.113.7 or 203.0.113.0/24")
            with dp_for(inst) as dp:
                st = blocklist_state(dp)
                ips = [e.get("value") for e in st["entries"]]
                if ip in ips:
                    return {"ok": True, "message": f"{ip} is already blocked", "reloaded": False}
                if st["ready"]:
                    dp.add_acl_entry(st["acl"], ip)          # live, no reload
                    save_blocklist(dp, ips + [ip])            # and kept for the next reload
                    reloaded = False
                else:
                    # First use: create the list file and add one "reject" rule to every frontend (one reload).
                    # That is a config change, so it needs haproxy.configure; later blocks only haproxy.operate.
                    if not p.can("haproxy.configure", haproxy_id=iid):
                        raise HTTPException(status_code=403, detail="The first block on an HAProxy adds the blocklist to every "
                                                                    "frontend (a config change), so it needs haproxy.configure. "
                                                                    "After that, haproxy.operate is enough.")
                    path = st["path"] or dp.put_general_file(BLOCKLIST, f"{ip}\n", create=True).get("file")
                    if st["path"]:
                        save_blocklist(dp, ips + [ip])
                    if not path:
                        raise DataPlaneError(502, "the Data Plane API did not return the blocklist file path")
                    with tx_lock(dp):
                        tx = dp.begin()
                        try:
                            for fe in dp.frontends(tx):
                                if fe["name"] in st["missing"]:
                                    new = copy.deepcopy(fe)
                                    new["tcp_request_rule_list"] = [_block_rule(path)] + (fe.get("tcp_request_rule_list") or [])
                                    _save_section(dp, tx, "frontend", fe["name"], fe, new)
                            dp.validate(dp.raw(tx))
                            reload_id = dp.commit(tx)
                        except DataPlaneError:
                            dp.rollback(tx)
                            raise
                    dp.wait_reload(reload_id)
                    snapshot(s, inst, dp, p, f"IP blocklist set up on {', '.join(st['missing'])}; blocked {ip}")
                    reloaded = True
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-block", f"{inst.name}/{ip}", ok=False, message=str(e))
            raise fail(e)
        audit(s, request, p, "haproxy-block", f"{inst.name}/{ip}", message=body.reason[:200] or "blocked")
        return {"ok": True, "message": f"{ip} is blocked on every frontend", "reloaded": reloaded}

    @r.delete("/{iid}/blocklist")
    def unblock_ip(iid: int, ip: str, request: Request, p=Depends(require("haproxy.operate")),
                   s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        try:
            with dp_for(inst) as dp:
                st = blocklist_state(dp)
                entry = next((e for e in st["entries"] if e.get("value") == ip), None)
                if entry is None:
                    raise DataPlaneError(404, f"{ip} is not blocked")
                dp.delete_acl_entry(st["acl"], str(entry.get("id")))
                save_blocklist(dp, [e.get("value") for e in st["entries"] if e is not entry])
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-unblock", f"{inst.name}/{ip}", ok=False, message=str(e))
            raise fail(e)
        audit(s, request, p, "haproxy-unblock", f"{inst.name}/{ip}", message="removed from the blocklist")
        return {"ok": True, "message": f"{ip} is no longer blocked"}

    # --- groups: do the instances share the same configuration? -------------------------
    @r.get("/{iid}/group-drift")
    def group_drift(iid: int, p=Depends(require("haproxy.configure")), s: Session = Depends(db_session)) -> dict:
        from .haproxy_monitor import strip_header
        inst = get(s, iid)
        if not inst.group_name:
            return {"group": "", "text": "", "others": []}
        try:
            with dp_for(inst) as dp:
                ref = strip_header(dp.raw())
        except DataPlaneError as e:
            raise fail(e)
        others = []
        for o in s.execute(select(HAProxyInstance).where(HAProxyInstance.group_name == inst.group_name, HAProxyInstance.id != iid)
                           .order_by(HAProxyInstance.name)).scalars():
            if not p.can("haproxy.configure", haproxy_id=o.id):
                continue                     # its config isn't yours to read
            try:
                with dp_for(o) as dp:
                    theirs = strip_header(dp.raw())
                # Each instance keeps its own log-tag (that's how k10y tells their logs apart).
                text = _keep_log_tag(ref, theirs)
                diff = _diff(theirs, text)
                others.append({"id": o.id, "name": o.name, "same": not diff, "diff": diff, "text": text, "error": ""})
            except DataPlaneError as e:
                others.append({"id": o.id, "name": o.name, "same": False, "diff": "", "error": str(e)})
        return {"group": inst.group_name, "text": ref, "others": others}

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
        denied = [iid for iid in body.instance_ids if not p.can("haproxy.configure", haproxy_id=iid)]
        if denied:
            names = [get(s, i).name for i in denied]
            raise HTTPException(status_code=403, detail=f"missing permission: haproxy.configure on {', '.join(names)}")
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
        if not (p.can("ai.use") or "ai.use" in p.haproxy_perms(iid)):
            raise HTTPException(status_code=403, detail="missing permission: ai.use")
        inst = get(s, iid)
        if body.scope != "instance":
            try:
                _check_name(body.target, "Frontend" if body.scope in ("frontend", "service") else "Backend")
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
