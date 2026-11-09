"""The agent's API in Nginx mode (K10Y_MODE=nginx). Read-only for now: configuration as sites and upstreams,
live numbers, upstream checks, logs and certificates. Every request needs the agent's bearer token."""

import logging
import os
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query

from .. import __version__
from ..config import Settings
from . import metrics, model

settings = Settings()
log = logging.getLogger("k10y.nginx")
_cache: Dict[str, Any] = {"mtimes": None, "model": None, "at": 0.0}
_cache_lock = threading.Lock()


def provision_token() -> None:
    """K10Y_API_TOKEN, or the token in K10Y_TOKEN_FILE (created with a random token on first start)."""
    if settings.api_token or not settings.token_file:
        return
    path = Path(settings.token_file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(24) + "\n")
        path.chmod(0o600)
        log.warning("created the agent's API token in %s", path)
    settings.api_token = path.read_text().strip()


def config() -> Dict[str, Any]:
    """The parsed configuration, re-read when any of its files changes (checked at most every 2 seconds)."""
    with _cache_lock:
        now = time.time()
        if _cache["model"] is not None and now - _cache["at"] < 2:
            return _cache["model"]
        files = _cache["model"]["files"] if _cache["model"] else [settings.nginx_conf]
        mtimes = tuple((f, os.path.getmtime(f)) for f in files if os.path.exists(f))
        if _cache["model"] is None or mtimes != _cache["mtimes"]:
            _cache["model"] = model.model(settings.nginx_conf)
            _cache["mtimes"] = tuple((f, os.path.getmtime(f)) for f in _cache["model"]["files"] if os.path.exists(f))
        _cache["at"] = now
        return _cache["model"]


def stub_url() -> Optional[str]:
    return settings.nginx_status_url or model.find_stub_status(settings.nginx_conf)


stats = metrics.Metrics(config, stub_url)


@asynccontextmanager
async def lifespan(_: FastAPI):
    provision_token()
    stats.start()
    yield
    stats.stop()


app = FastAPI(title="k10y-agent (nginx)", version=__version__, lifespan=lifespan)


def require_token(authorization: Optional[str] = Header(None)) -> None:
    if not settings.api_token:
        if settings.token_file:
            raise HTTPException(status_code=503, detail="API token not available yet")
        return
    if not authorization or not secrets.compare_digest(authorization.encode(), f"Bearer {settings.api_token}".encode()):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "k10y-agent", "mode": "nginx", "version": __version__,
            "message": "API only. Add this agent to a k10y hub (Administration → Nginx instances)."}


api = APIRouter(prefix="/api/v1/nginx", dependencies=[Depends(require_token)])


def nginx(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run([settings.nginx_bin, *args], capture_output=True, text=True, timeout=timeout)


def master_pid() -> Optional[int]:
    tree = model.parse(settings.nginx_conf)["tree"]
    pid_file = next((d["args"][0] for d in tree if d["directive"] == "pid" and d["args"]), None)
    for path in ([pid_file] if pid_file else []) + ["/run/nginx.pid", "/var/run/nginx.pid"]:
        try:
            pid = int(Path(path).read_text().strip())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            continue
    return None


@api.get("/info")
def info() -> dict:
    try:
        out = nginx("-v", timeout=5)
        version = re.search(r"nginx/(\S+)", out.stderr + out.stdout)
        version = version.group(1) if version else None
    except (OSError, subprocess.TimeoutExpired):
        version = None
    m = config()
    return {"agent_version": __version__, "name": settings.cluster_name if settings.cluster_name != "kubernetes" else os.uname().nodename,
            "nginx_version": version, "running": master_pid() is not None, "conf": settings.nginx_conf,
            "files": m["files"], "parse_errors": m["errors"], "access_logs": stats.log_paths(), "log_formats": stats.formats,
            "stub_status": stub_url(), "recommended_log_format": metrics.K10Y_LOG_FORMAT}


@api.get("/config")
def get_config() -> dict:
    return config()


@api.get("/config/raw")
def raw_config() -> dict:
    """`nginx -T`: every file, as Nginx reads it (the hub asks nginx.configure: it can contain secrets)."""
    try:
        out = nginx("-T")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HTTPException(status_code=500, detail=f"cannot run {settings.nginx_bin}: {e}")
    if out.returncode != 0:
        raise HTTPException(status_code=400, detail=(out.stderr or out.stdout).strip()[-1000:])
    return {"text": out.stdout, "check": out.stderr.strip()[-2000:]}


@api.get("/stats")
def get_stats(since: int = 0) -> dict:
    return {"points": stats.series(since), "interval_s": metrics.BUCKET_S, "stub": stats.stub}


@api.get("/upstreams/health")
def upstream_health() -> dict:
    return stats.health


@api.get("/logs")
def logs(after: int = 0, limit: int = Query(300, ge=1, le=2000)) -> dict:
    return {"lines": stats.lines(after, limit), "formats": stats.formats, "paths": stats.log_paths()}


@api.get("/errors")
def error_log(lines: int = Query(200, ge=1, le=2000)) -> dict:
    out = []
    for path in config()["error_logs"] or ["/var/log/nginx/error.log"]:
        path = path.split(" ")[0]
        if path in ("stderr", "off") or path.startswith(("syslog:", "memory:")):
            continue
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 256 * 1024))
                text = f.read().decode(errors="replace").splitlines()[-lines:]
        except OSError as e:
            out.append({"path": path, "error": str(e)})
            continue
        for line in text:
            m = re.match(r"^(\S+ \S+) \[(\w+)\] (?:\d+#\d+: )?(?:\*\d+ )?(.*)$", line)
            out.append({"path": path, "time": m.group(1) if m else None, "level": m.group(2) if m else None, "message": m.group(3) if m else line})
    return {"lines": out[-lines:]}


@api.get("/certificates")
def certificates() -> list:
    certs: Dict[str, Dict[str, Any]] = {}
    for s in config()["sites"]:
        path = s.get("ssl_certificate")
        if not path or "$" in path:
            continue
        c = certs.setdefault(path, {**metrics.certificate(path), "sites": []})
        c["sites"].append({"id": s["id"], "server_names": s["server_names"]})
    return sorted(certs.values(), key=lambda c: c.get("days_left", 1e9))


@api.get("/summary")
def summary() -> dict:
    """What the hub's overview and alerts need: counts, live totals and problems."""
    m, last = config(), (stats.series()[-1][1] if stats.series() else {})
    problems: List[Dict[str, Any]] = []
    for u in m["upstreams"]:
        up = [srv for srv in u["servers"] if not srv["down"]]
        bad = [srv for srv in up if stats.health.get(f"{srv['host']}:{srv['port']}", {}).get("up") is False]
        for srv in bad:
            why = stats.health["%s:%s" % (srv["host"], srv["port"])]["error"]
            problems.append({"kind": "server_down", "upstream": u["name"], "server": srv["address"],
                             "message": f"{srv['address']} in {u['name']} doesn't answer: {why}"})
        if up and len(bad) == len([x for x in up if not x["backup"]]):
            problems.append({"kind": "upstream_down", "upstream": u["name"], "message": f"no server of {u['name']} answers"})
    for c in certificates():
        if c.get("days_left") is not None and c["days_left"] < 14:
            when = "expired" if c["days_left"] < 0 else "expires in %d days" % c["days_left"]
            problems.append({"kind": "cert_expiry", "path": c["path"], "days_left": c["days_left"],
                             "message": f"certificate {Path(c['path']).name} {when}"})
    if m["errors"]:
        problems.append({"kind": "config_error", "message": f"{len(m['errors'])} problem(s) reading the configuration"})
    if master_pid() is None:
        problems.append({"kind": "not_running", "message": "Nginx isn't running"})
    return {"sites": len(m["sites"]), "upstreams": len(m["upstreams"]),
            "servers": sum(len(u["servers"]) for u in m["upstreams"]), "req": last.get("req", stats.stub.get("req_rate")),
            "e5_pct": last.get("e5_pct"), "p95": last.get("p95"), "active": stats.stub.get("active"), "problems": problems}


app.include_router(api)
