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
ROUTE_CRITERIA = {"path_beg": "path_prefix", "path": "path", "hdr(host)": "host", "req.hdr(host)": "host"}


class HAProxyInstance(Base):
    __tablename__ = "haproxy_instances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(String(300))                       # Data Plane API base, e.g. http://lb1:5555
    username: Mapped[str] = mapped_column(String(128))
    password_encrypted: Mapped[str] = mapped_column(Text)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


# --- overview: config + live stats in one model ----------------------------------------

def _stats_index(stats: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    out = {}
    for it in stats:
        s = it.get("stats") or {}
        out[(it.get("type", ""), it.get("backend_name") or "", it.get("name", ""))] = {k: s.get(k) for k in STAT_KEYS if k in s}
    return out


def parse_routes(fe: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    acls: Dict[str, List[Dict[str, Any]]] = {}
    for a in fe.get("acl_list") or []:
        acls.setdefault(a["acl_name"], []).append(a)
    routes, simple = [], True
    for rule in fe.get("backend_switching_rule_list") or []:
        test, cond = (rule.get("cond_test") or "").strip(), rule.get("cond") or ""
        defs = acls.get(test, [])
        kind = ROUTE_CRITERIA.get(defs[0]["criterion"]) if len(defs) == 1 else None
        if cond == "if" and kind and re.fullmatch(r"[\w.-]+", test):
            value = re.sub(r"^-i\s+", "", defs[0].get("value") or "").strip()
            routes.append({"kind": kind, "value": value, "backend": rule.get("name")})
        else:
            simple = False
            routes.append({"kind": "other", "value": f"{cond} {test}".strip(), "backend": rule.get("name")})
    return routes, simple


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
        binds = [{"address": b.get("address") or "*", "port": b.get("port"), "ssl": bool(b.get("ssl")), "name": n}
                 for n, b in (fe.get("binds") or {}).items()]
        frontends.append({"name": fe["name"], "mode": fe.get("mode") or "http", "binds": binds,
                          "default_backend": fe.get("default_backend"), "routes": routes, "simple_routes": simple,
                          "stats_page": bool((fe.get("stats_options") or {}).get("stats_enable")),
                          "stats": idx.get(("frontend", fe["name"], "FRONTEND"), {})})
    backends = []
    for be in bes:
        servers = []
        for name, s in (be.get("servers") or {}).items():
            st = idx.get(("server", be["name"], name), {})
            servers.append({"name": name, "address": s.get("address"), "port": s.get("port"), "weight": s.get("weight", 1),
                            "check": s.get("check") == "enabled", "backup": s.get("backup") == "enabled",
                            "state": _server_state(st.get("status")), "stats": st})
        chk = be.get("adv_check")
        backends.append({"name": be["name"], "mode": be.get("mode") or "http", "balance": (be.get("balance") or {}).get("algorithm") or "roundrobin",
                         "check": {"type": "http" if chk == "httpchk" else ("tcp" if chk == "tcp-check" else "default"),
                                   "uri": (be.get("httpchk_params") or {}).get("uri") or ""},
                         "servers": servers, "used_by": sorted(set(used_by.get(be["name"], []))),
                         "stats": idx.get(("backend", be["name"], "BACKEND"), {})})
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
        if kind not in ("path_prefix", "path", "host") or not value or re.search(r"\s", value):
            raise DataPlaneError(400, f"route {i}: choose a type and a value without spaces")
        if be not in backends:
            raise DataPlaneError(400, f"route {i}: unknown backend {be!r}")
        criterion = {"path_prefix": "path_beg", "path": "path", "host": "hdr(host)"}[kind]
        acl_value = f"-i {value}" if kind == "host" else value
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
            binds = {}
            for b in d["binds"] or []:
                addr, port = (str(b.get("address") or "*").strip() or "*"), _port(b.get("port"))
                binds[f"{addr}:{port}"] = {"name": f"{addr}:{port}", "address": addr, "port": port}
            if not binds:
                raise DataPlaneError(400, "a service needs at least one listen address")
            fe["binds"] = binds
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


def _diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), "current", "new", lineterm="", n=3)
    return "\n".join(l for l in lines if not re.match(r"^[+-]# _(md5hash|version)=", l))


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


def build_router(db_session, require, audit, crypto, client_factory: Optional[Callable[..., DataPlane]] = None) -> APIRouter:
    r = APIRouter(prefix="/api/hub/haproxy")
    make = client_factory or (lambda inst, pw: DataPlane(inst.url, inst.username, pw, inst.verify_tls))

    def view(i: HAProxyInstance) -> dict:
        return {"id": i.id, "name": i.name, "url": i.url, "username": i.username, "verify_tls": i.verify_tls,
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
                               password_encrypted=crypto().encrypt(body.password), verify_tls=body.verify_tls)
        s.add(inst)
        s.commit()
        audit(s, request, p, "haproxy-add", inst.name, params={"url": inst.url})
        return view(inst)

    @r.patch("/{iid}")
    def update_instance(iid: int, body: InstanceBody, request: Request, p=Depends(require("clusters.manage")),
                        s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        inst.name, inst.url, inst.username, inst.verify_tls = body.name, check_url(body.url), body.username, body.verify_tls
        if body.password:
            inst.password_encrypted = crypto().encrypt(body.password)
        s.commit()
        audit(s, request, p, "haproxy-update", inst.name, params={"url": inst.url, "password_changed": bool(body.password)})
        return view(inst)

    @r.delete("/{iid}")
    def delete_instance(iid: int, request: Request, p=Depends(require("clusters.manage")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        s.delete(inst)
        s.commit()
        audit(s, request, p, "haproxy-remove", inst.name)
        return {"ok": True}

    @r.get("/{iid}/overview")
    def instance_overview(iid: int, p=Depends(require("haproxy.view")), s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        try:
            with dp_for(inst) as dp:
                return {"instance": view(inst), **overview(dp)}
        except DataPlaneError as e:
            raise fail(e)

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

    @r.post("/{iid}/changes")
    def changes(iid: int, body: ChangesBody, request: Request, p=Depends(require("haproxy.configure")),
                s: Session = Depends(db_session)) -> dict:
        inst = get(s, iid)
        try:
            with dp_for(inst) as dp:
                result = plan_and_apply(dp, body.ops, body.apply)
        except DataPlaneError as e:
            audit(s, request, p, "haproxy-config", inst.name, ok=False, dry_run=not body.apply, message=str(e),
                  params={"ops": [f"{o.op} {o.kind} {o.backend + '/' if o.backend else ''}{o.name}" for o in body.ops]})
            raise fail(e)
        audit(s, request, p, "haproxy-config", inst.name, dry_run=not body.apply,
              message="; ".join(result["summary"]) + ("" if result["valid"] else f" (invalid: {result['error']})"),
              params={"ops": result["summary"]})
        return result

    return r
