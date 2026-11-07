import asyncio
import base64
import logging
import os
import time
import re
import secrets
from contextlib import asynccontextmanager

import httpx
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from pydantic import BaseModel, Field

from . import insights, __version__
from .collectors import Collector
from .config import Settings
from . import ai, logstream, podexec
from .metrics_history import MetricsHistory
from .resources import NotSupported, ResourceViewer
from .controls import ActionError, AuditLog, Controller, ProtectedNamespaceError, api_error_message, now_iso


settings = Settings()
audit_log = AuditLog(settings.audit_path)
log = logging.getLogger(__name__)
NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
token_error: Optional[str] = None


def provision_token() -> None:
    """Load the API token from settings.token_secret, creating it on first start."""
    global token_error
    if settings.api_token or not settings.token_secret:
        return
    try:
        namespace = NAMESPACE_FILE.read_text().strip()
        core = client.CoreV1Api(get_api_client())
        token = ""
        for _ in range(2):
            try:
                sec = core.read_namespaced_secret(settings.token_secret, namespace)
                token = base64.b64decode((sec.data or {}).get("api-token", "")).decode()
                break
            except ApiException as e:
                if e.status != 404:
                    raise
            try:
                core.create_namespaced_secret(namespace, client.V1Secret(
                    metadata=client.V1ObjectMeta(name=settings.token_secret, labels={"app.kubernetes.io/name": "k10y-agent"}),
                    string_data={"api-token": secrets.token_hex(24)},
                ))
                log.info("created API token Secret %s/%s", namespace, settings.token_secret)
            except ApiException as e:
                if e.status != 409:  # 409: another replica created it first; read it on the next loop
                    raise
        if not token:
            raise RuntimeError(f"Secret {namespace}/{settings.token_secret} has no api-token key")
        settings.api_token = token
        token_error = None
    except Exception as e:
        token_error = str(e)
        log.error("could not provision API token: %s", e)


# Protected namespaces chosen in the hub are kept in the agent's own token Secret (key protected-namespaces), so
# they survive restarts and upgrades. Without that key, K10Y_PROTECTED_NAMESPACES applies.
PROTECTED_KEY = "protected-namespaces"
protected_source = "env"


def _apply_protected(namespaces: List[str]) -> None:
    settings.protected_namespaces = ",".join(namespaces)
    get_controller().protected = set(namespaces)


def load_protected() -> None:
    global protected_source
    if not settings.token_secret:
        return
    try:
        sec = client.CoreV1Api(get_api_client()).read_namespaced_secret(settings.token_secret, NAMESPACE_FILE.read_text().strip())
    except (OSError, ApiException) as e:
        log.warning("could not read protected namespaces from Secret %s: %s", settings.token_secret, e)
        return
    raw = (sec.data or {}).get(PROTECTED_KEY)
    if raw is not None:
        _apply_protected([ns for ns in base64.b64decode(raw).decode().split(",") if ns])
        protected_source = "hub"


@asynccontextmanager
async def lifespan(_: FastAPI):
    provision_token()
    load_protected()
    metrics_history.start()
    yield
    metrics_history.stop()


app = FastAPI(title="k10y-agent", version=__version__, lifespan=lifespan)
if settings.cors_origins.strip():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-K10Y-Actor"],
        max_age=600,
    )


@lru_cache(maxsize=1)
def get_api_client() -> client.ApiClient:
    try:
        config.load_incluster_config()
    except ConfigException:
        config.load_kube_config(config_file=settings.kubeconfig)
    return client.ApiClient()


@lru_cache(maxsize=1)
def get_collector() -> Collector:
    return Collector(get_api_client())


@lru_cache(maxsize=1)
def get_controller() -> Controller:
    return Controller(get_api_client(), settings.protected)


metrics_history = MetricsHistory(get_collector, settings.metrics_poll_s, settings.metrics_retention_s)


def require_token(authorization: Optional[str] = Header(None)) -> None:
    if not settings.api_token:
        if settings.token_secret:  # fail closed: never serve unauthenticated by accident
            provision_token()
            if not settings.api_token:
                raise HTTPException(status_code=503, detail=f"API token not available yet: {token_error}")
        else:
            return
    expected = f"Bearer {settings.api_token}".encode()
    if not authorization or not secrets.compare_digest(authorization.encode(), expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.exception_handler(ApiException)
def kubernetes_error(_: Request, exc: ApiException) -> JSONResponse:
    status = exc.status if exc.status and 400 <= exc.status < 600 else 502
    return JSONResponse(status_code=status, content={"detail": f"kubernetes API: {api_error_message(exc)}"})


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> dict:
    # The agent is API-only: people use the k10y hub, which checks logins and roles and keeps the audit log.
    return {"service": "k10y-agent", "version": __version__,
            "message": "API only. Add this agent to a k10y hub (Administration → Clusters) to use the dashboard."}


api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


# --- read -------------------------------------------------------------------


@api.get("/info")
def info() -> dict:
    return {
        "cluster": settings.cluster_name,
        "agent_version": __version__,
        "kubernetes_version": get_collector().version(),
        "control_enabled": settings.control_enabled,
        "protected_namespaces": settings.protected,
        "helm_enabled": settings.helm_enabled and settings.control_enabled,
        "protected_source": protected_source,           # "env" (install default) or "hub" (chosen in k10y)
        "protected_persisted": bool(settings.token_secret),  # changes survive restarts and upgrades
    }


@api.get("/summary")
def summary() -> dict:
    return get_collector().summary(settings.cluster_name)


@api.get("/nodes")
def nodes() -> list:
    return get_collector().nodes()


@api.get("/pods")
def pods(namespace: Optional[str] = None, problems_only: bool = False) -> list:
    result = get_collector().pods(namespace)
    return [p for p in result if p["problem"]] if problems_only else result


@api.get("/deployments")
def deployments(namespace: Optional[str] = None) -> list:
    return get_collector().deployments(namespace)


@api.get("/statefulsets")
def statefulsets(namespace: Optional[str] = None) -> list:
    return get_collector().statefulsets(namespace)


@api.get("/daemonsets")
def daemonsets(namespace: Optional[str] = None) -> list:
    return get_collector().daemonsets(namespace)


@api.get("/jobs")
def jobs(namespace: Optional[str] = None) -> list:
    return get_collector().jobs(namespace)


@api.get("/cronjobs")
def cronjobs(namespace: Optional[str] = None) -> list:
    return get_collector().cronjobs(namespace)


@api.get("/ingresses")
def ingresses(namespace: Optional[str] = None) -> list:
    return get_collector().ingresses(namespace)


@api.get("/persistentvolumes")
def persistentvolumes() -> list:
    return get_collector().persistentvolumes()


@api.get("/persistentvolumeclaims")
def persistentvolumeclaims(namespace: Optional[str] = None) -> list:
    pvcs = get_collector().persistentvolumeclaims(namespace)
    try:
        usage = insights.volume_usage(get_collector())["volumes"]
    except Exception:
        usage = {}
    for p in pvcs:
        p["usage"] = usage.get(f"{p['namespace']}/{p['name']}")
    return pvcs


@api.get("/volumes/usage")
def volumes_usage(fresh: bool = False) -> dict:
    """How full each PVC is (from the kubelets), the alert threshold, and any node that couldn't be read."""
    return {**insights.volume_usage(get_collector(), fresh), "full_percent": insights.volume_full_percent()}


@api.get("/hpas")
def hpas(namespace: Optional[str] = None) -> list:
    return get_collector().hpas(namespace)


@api.get("/services")
def services(namespace: Optional[str] = None) -> list:
    return get_collector().services(namespace)


@api.get("/configmaps")
def list_configmaps(namespace: Optional[str] = None) -> list:
    return get_collector().configmaps(namespace)


@api.get("/configmaps/{namespace}/{name}")
def configmap(namespace: str, name: str) -> dict:
    return get_collector().configmap(namespace, name)


@api.get("/secrets")
def list_secrets(namespace: Optional[str] = None) -> list:
    return get_collector().secrets(namespace)


@api.get("/metrics/history")
def metrics(
    scope: str = Query("cluster", pattern="^(cluster|nodes|pod|snapshot)$"),
    since: int = Query(0, ge=0, description="Only points newer than this epoch-ms timestamp."),
    namespace: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    if scope == "pod" and not (namespace and name):
        raise HTTPException(status_code=400, detail="scope=pod needs namespace and name")
    return metrics_history.query(scope, since, namespace, name)


# --- AI assistant -------------------------------------------------------------


def _own_namespace() -> Optional[str]:
    try:
        return NAMESPACE_FILE.read_text().strip()
    except OSError:
        return None


ai_store = ai.AIConfigStore(
    lambda: client.CoreV1Api(get_api_client()), _own_namespace,
    {"provider": settings.ai_provider, "api_key": settings.ai_api_key, "model": settings.ai_model,
     "base_url": settings.ai_base_url},
)


class AIConfigBody(BaseModel):
    provider: str = Field(pattern="^(gemini|openai|openrouter)$")
    api_key: Optional[str] = Field(None, max_length=512, description="Omit to keep the stored key.")
    model: str = Field("", max_length=200)
    base_url: str = Field("", max_length=300)
    activate: bool = Field(True, description="Also make this the provider used for investigations.")


class AIActiveBody(BaseModel):
    provider: str = Field(pattern="^(gemini|openai|openrouter)$")


class AIModelsBody(BaseModel):
    provider: str = Field(pattern="^(gemini|openai|openrouter)$")
    api_key: Optional[str] = Field(None, max_length=512)
    base_url: str = Field("", max_length=300)


class DiagnoseBody(BaseModel):
    provider: Optional[str] = Field(None, pattern="^(gemini|openai|openrouter)$", description="Default: the active provider.")
    scope: str = Field("cluster", pattern="^(cluster|namespace|object)$")
    namespace: Optional[str] = Field(None, max_length=253)
    kind: Optional[str] = Field(None, pattern="^(Pod|Deployment|Service|Node)$")
    name: Optional[str] = Field(None, max_length=253)
    question: str = Field("", max_length=2000)


def _ai_error(e: Exception) -> HTTPException:
    if isinstance(e, ai.ProviderError):
        return HTTPException(status_code=502, detail=str(e))
    if isinstance(e, httpx.HTTPError):
        return HTTPException(status_code=504, detail=f"AI provider unreachable: {e}")
    raise e


@api.get("/ai/config")
def ai_config() -> dict:
    return ai.AIConfigStore.public(ai_store.load_all())


def _audit_ai(request: Request, action: str, message: str, params: dict) -> None:
    audit_log.record({"time": now_iso(), "actor": (request.headers.get("x-k10y-actor") or "unknown")[:64],
                      "client": request.client.host if request.client else None, "action": action,
                      "target": "secret/k10y-ai", "params": params, "dry_run": False, "ok": True, "message": message})


@api.post("/ai/config")
def save_ai_config(body: AIConfigBody, request: Request) -> dict:
    base_url = (body.base_url or ai.PROVIDERS[body.provider]["base_url"]).rstrip("/")
    if not base_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="base URL must start with https://")
    try:
        ai_store.save(body.provider, body.api_key, body.model, base_url, body.activate)
    except ai.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit_ai(request, "ai-config", f"AI settings saved for {body.provider} ({body.model or 'default model'})"
              + (", now active" if body.activate else ""),
              {"provider": body.provider, "model": body.model, "key_changed": body.api_key is not None, "activate": body.activate})
    return ai_config()


@api.post("/ai/active")
def set_ai_active(body: AIActiveBody, request: Request) -> dict:
    try:
        ai_store.set_active(body.provider)
    except ai.ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit_ai(request, "ai-config", f"Active AI provider switched to {body.provider}", {"provider": body.provider})
    return ai_config()


@api.delete("/ai/config")
def clear_ai_config(request: Request, provider: str = Query(..., pattern="^(gemini|openai|openrouter)$")) -> dict:
    ai_store.clear(provider)
    _audit_ai(request, "ai-config", f"AI key removed for {provider}", {"provider": provider})
    return ai_config()


@api.post("/ai/models")
def ai_models(body: AIModelsBody) -> dict:
    cfg = ai_store.load(body.provider)  # that provider's saved key, if any
    cfg["base_url"] = (body.base_url or ai.PROVIDERS[body.provider]["base_url"]).rstrip("/")
    if body.api_key:
        cfg["api_key"] = body.api_key
    try:
        return {"models": ai.list_models(cfg)}
    except Exception as e:
        raise _ai_error(e)


@api.post("/ai/diagnose")
def ai_diagnose(body: DiagnoseBody, request: Request) -> dict:
    if body.scope == "namespace" and not body.namespace:
        raise HTTPException(status_code=400, detail="scope=namespace needs a namespace")
    if body.scope == "object" and not (body.kind and body.name and (body.namespace or body.kind == "Node")):
        raise HTTPException(status_code=400, detail="scope=object needs kind, name and namespace")
    if body.scope == "cluster":
        task = "Check the whole cluster: find every current problem, explain root causes and propose fixes."
    elif body.scope == "namespace":
        task = f"Check namespace '{body.namespace}': find current problems, explain root causes and propose fixes."
    else:
        where = f" in namespace '{body.namespace}'" if body.namespace else ""
        task = f"Investigate {body.kind} '{body.name}'{where}: is anything wrong, why, and how to fix it?"
    if body.question:
        task += f"\n\nThe user also asks: {body.question}"
    target = {"cluster": "cluster", "namespace": f"namespace/{body.namespace}"}.get(
        body.scope, f"{(body.kind or '').lower()}/{body.namespace}/{body.name}")
    cfg = ai_store.load(body.provider)
    tools = ai.ClusterTools(get_collector(), lambda ns, name: get_controller().history(ns, name))
    started = now_iso()
    try:
        result = ai.diagnose(cfg, tools, task, settings.protected)
    except Exception as e:
        err = _ai_error(e)
        audit_log.record({"time": started, "actor": (request.headers.get("x-k10y-actor") or "unknown")[:64],
                          "client": request.client.host if request.client else None, "action": "ai-diagnose",
                          "target": target,
                          "params": {"model": cfg.get("model")}, "dry_run": False, "ok": False, "message": err.detail})
        raise err
    audit_log.record({"time": started, "actor": (request.headers.get("x-k10y-actor") or "unknown")[:64],
                      "client": request.client.host if request.client else None, "action": "ai-diagnose",
                      "target": target, "params": {"model": result["model"], "steps": len(result["steps"])},
                      "dry_run": False, "ok": True,
                      "message": f"{len(result['issues'])} issue(s), {len(result['actions'])} proposed action(s), "
                                 f"{result['seconds']}s with {result['provider']}/{result['model']}"})
    return {**result, "control_enabled": settings.control_enabled}


@api.get("/resource")
def resource(kind: str, name: str, namespace: Optional[str] = None) -> dict:
    """Live object (+YAML), its events and related objects, for the dashboard's detail panel."""
    try:
        return ResourceViewer(get_collector()).get(kind, namespace or None, name)
    except NotSupported as e:
        raise HTTPException(status_code=400, detail=str(e))


@api.get("/namespaces")
def namespaces() -> list:
    return get_collector().namespaces()


@api.get("/namespaces/details")
def namespace_details() -> list:
    return get_collector().namespace_details()


@api.get("/topology")
def topology(namespace: str = Query(..., min_length=1)) -> dict:
    return get_collector().topology(namespace)


@api.get("/events")
def events(
    namespace: Optional[str] = None,
    warnings_only: bool = False,
    limit: int = Query(100, ge=1, le=1000),
) -> list:
    return get_collector().events(namespace, warnings_only, limit)


@api.get("/pods/{namespace}/{pod}/logs", response_class=PlainTextResponse)
def logs(
    namespace: str,
    pod: str,
    container: Optional[str] = None,
    tail: int = Query(200, ge=1, le=5000),
    previous: bool = False,
) -> str:
    return get_collector().logs(namespace, pod, container, tail, previous)


STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@api.get("/pods/{namespace}/{pod}/logs/stream")
def logs_stream(
    namespace: str,
    pod: str,
    container: Optional[str] = None,
    tail: int = Query(100, ge=0, le=5000),
    since_seconds: Optional[int] = Query(None, ge=1),
    timestamps: bool = False,
) -> StreamingResponse:
    """Follow one container's logs (like kubectl logs -f)."""
    gen = logstream.stream(get_collector().core, [(pod, container)], namespace, tail, since_seconds, timestamps)
    return StreamingResponse(gen, media_type="text/plain; charset=utf-8", headers=STREAM_HEADERS)


@api.get("/{kind}/{namespace}/{name}/logs/stream")
def workload_logs_stream(
    kind: str,
    namespace: str,
    name: str,
    container: Optional[str] = None,
    tail: int = Query(50, ge=0, le=1000),
    since_seconds: Optional[int] = Query(None, ge=1),
    timestamps: bool = False,
) -> StreamingResponse:
    """Follow the logs of every pod of a Deployment, StatefulSet, DaemonSet or Job, each line tagged with its pod."""
    if kind not in ("deployments", "statefulsets", "daemonsets", "jobs"):
        raise HTTPException(status_code=404, detail="logs are available for deployments, statefulsets, daemonsets and jobs")
    pods = logstream.workload_pods(get_collector(), kind, namespace, name)
    if not pods:
        raise HTTPException(status_code=404, detail=f"{name} has no pods")
    gen = logstream.stream(get_collector().core, [(p, container) for p in pods], namespace, tail, since_seconds, timestamps,
                           prefix=True)
    return StreamingResponse(gen, media_type="text/plain; charset=utf-8", headers=STREAM_HEADERS)


@api.get("/deployments/{namespace}/{name}/history")
def deployment_history(namespace: str, name: str) -> list:
    return get_controller().history(namespace, name)


# --- insights: Helm, certificates, CronJob runs, quotas, revisions, search (all read-only) ----------

def _found(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@api.get("/deployments/{namespace}/{name}/revisions")
def deployment_revisions(namespace: str, name: str) -> list:
    """Every revision with its change cause and pod template, to compare two revisions."""
    return insights.revision_details(get_collector(), namespace, name)


@api.get("/helm/releases")
def helm_releases(namespace: Optional[str] = None) -> list:
    return insights.helm_releases(get_collector(), namespace)


@api.get("/helm/releases/{namespace}/{name}")
def helm_history(namespace: str, name: str) -> list:
    return _found(lambda: insights.helm_history(get_collector(), namespace, name))


@api.get("/helm/releases/{namespace}/{name}/revisions/{revision}")
def helm_revision(namespace: str, name: str, revision: int) -> dict:
    """Values (secret-looking keys masked) and rendered manifest (Secret data masked). The hub asks secrets.view."""
    return _found(lambda: insights.helm_revision(get_collector(), namespace, name, revision))


@api.get("/certificates")
def certificates(fresh: bool = False) -> list:
    return insights.certificates(get_collector(), fresh)


@api.get("/cronjobs/{namespace}/{name}/runs")
def cronjob_runs(namespace: str, name: str) -> dict:
    c = get_collector()
    cj = next((x for x in c.cronjobs(namespace) if x["name"] == name), None)
    if cj is None:
        raise HTTPException(status_code=404, detail=f"CronJob {namespace}/{name} not found")
    runs = insights.cronjob_runs(c.jobs(namespace), namespace, name)
    return {"cronjob": cj, "runs": runs, "problem": insights.cronjob_problem(cj, runs)}


@api.get("/quotas")
def quotas(namespace: Optional[str] = None) -> dict:
    return insights.quotas(get_collector(), namespace)


@api.get("/search")
def search(q: str = Query(..., min_length=2, max_length=100)) -> list:
    return insights.search(get_collector(), q)


NS_NAME = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"


class ProtectedBody(BaseModel):
    namespaces: List[str] = Field(..., max_length=200)


@api.put("/settings/protected-namespaces")
def set_protected_namespaces(body: ProtectedBody, request: Request) -> dict:
    """Choose the namespaces where every action, edit and shell is refused."""
    global protected_source
    names = sorted({n.strip() for n in body.namespaces if n.strip()})
    bad = [n for n in names if not re.match(NS_NAME, n)]
    if bad:
        raise HTTPException(status_code=400, detail=f"not a valid namespace name: {', '.join(bad)}")
    before = settings.protected
    if settings.token_secret:
        client.CoreV1Api(get_api_client()).patch_namespaced_secret(
            settings.token_secret, NAMESPACE_FILE.read_text().strip(), {"stringData": {PROTECTED_KEY: ",".join(names)}})
    _apply_protected(names)
    protected_source = "hub"
    added, removed = sorted(set(names) - set(before)), sorted(set(before) - set(names))
    message = "; ".join(x for x in (f"protected {', '.join(added)}" if added else "",
                                     f"no longer protected {', '.join(removed)}" if removed else "") if x) or "no change"
    audit_log.record({"time": now_iso(), "actor": (request.headers.get("x-k10y-actor") or "unknown")[:64],
                      "client": request.client.host if request.client else None, "action": "protected-namespaces",
                      "target": "protected namespaces", "params": {"namespaces": names, "added": added, "removed": removed},
                      "dry_run": False, "ok": True, "message": message})
    return {**info(), "message": message}


@api.get("/audit")
def audit(limit: int = Query(200, ge=1, le=1000)) -> list:
    return audit_log.list(limit)


# --- control ----------------------------------------------------------------


class DryRun(BaseModel):
    dry_run: bool = False


class ScaleBody(DryRun):
    replicas: int = Field(ge=0, le=1000)


class ImageBody(DryRun):
    container: str = Field(min_length=1)
    image: str = Field(min_length=1, max_length=512)


class RollbackBody(DryRun):
    revision: Optional[int] = Field(None, ge=1)


class ApplyBody(DryRun):
    yaml: str = Field(min_length=1, max_length=512_000)


def run_action(
    request: Request,
    action: str,
    target: str,
    dry_run: bool,
    fn: Callable[[], Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a control action with the enablement check, error mapping and audit entry."""
    if not settings.control_enabled:
        raise HTTPException(status_code=403, detail="control actions are disabled on this agent")
    entry = {
        "time": "",
        "actor": (request.headers.get("x-k10y-actor") or "unknown")[:64],
        "client": request.client.host if request.client else None,
        "action": action,
        "target": target,
        "params": params or {},
        "dry_run": dry_run,
    }
    status, detail = 200, None
    try:
        result = fn()
    except ProtectedNamespaceError as e:
        status, detail = 403, str(e)
    except ActionError as e:
        status, detail = 400, str(e)
    except ApiException as e:
        status, detail = (e.status if e.status and 400 <= e.status < 600 else 502), api_error_message(e)
    entry["time"] = now_iso()
    if detail is not None:
        audit_log.record({**entry, "ok": False, "message": detail})
        raise HTTPException(status_code=status, detail=detail)
    message = result["message"] + (" (dry run, nothing changed)" if dry_run else "")
    audit_log.record({**entry, "ok": True, "message": message})
    return {**result, "ok": True, "dry_run": dry_run, "message": message}


@api.post("/deployments/{namespace}/{name}/scale")
def scale_deployment(namespace: str, name: str, body: ScaleBody, request: Request) -> dict:
    return run_action(
        request, "scale", f"deployment/{namespace}/{name}", body.dry_run,
        lambda: get_controller().scale(namespace, name, body.replicas, body.dry_run),
        {"replicas": body.replicas},
    )


@api.post("/deployments/{namespace}/{name}/restart")
def restart_deployment(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(
        request, "restart", f"deployment/{namespace}/{name}", body.dry_run,
        lambda: get_controller().restart(namespace, name, body.dry_run),
    )


@api.post("/deployments/{namespace}/{name}/image")
def set_deployment_image(namespace: str, name: str, body: ImageBody, request: Request) -> dict:
    return run_action(
        request, "set-image", f"deployment/{namespace}/{name}", body.dry_run,
        lambda: get_controller().set_image(namespace, name, body.container, body.image, body.dry_run),
        {"container": body.container, "image": body.image},
    )


HELM_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,51}[a-z0-9])?$")


def helm_rollback(namespace: str, name: str, revision: int, dry_run: bool) -> Dict[str, Any]:
    """`helm rollback`, so Helm's hooks run and its history stays right. Runs as the agent's ServiceAccount."""
    import shutil
    import subprocess
    import tempfile
    get_controller().guard(namespace)
    if not settings.helm_enabled:
        raise ActionError("Helm rollback is off on this agent. Set K10Y_HELM_ENABLED=true and apply rbac-helm.yaml to turn it on.")
    if not HELM_NAME.match(name) or not re.match(NS_NAME, namespace) or revision < 1:
        raise ActionError("invalid release, namespace or revision")
    helm = shutil.which("helm")
    if not helm:
        raise ActionError("helm is not installed in this agent image")
    cmd = [helm, "rollback", name, str(revision), "--namespace", namespace, "--timeout", "5m0s"]
    if dry_run:
        cmd.append("--dry-run")
    if settings.kubeconfig:
        cmd += ["--kubeconfig", settings.kubeconfig]
    with tempfile.TemporaryDirectory(prefix="helm-") as home:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "HELM_CACHE_HOME": f"{home}/cache",
               "HELM_CONFIG_HOME": f"{home}/config", "HELM_DATA_HOME": f"{home}/data"}
        for k in ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT"):
            if k in os.environ:
                env[k] = os.environ[k]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=330, env=env)
        except subprocess.TimeoutExpired:
            raise ActionError("helm rollback took longer than 5 minutes")
    out = (r.stdout + "\n" + r.stderr).strip()
    if r.returncode != 0:
        raise ActionError(f"helm: {out[-600:] or 'failed'}")
    what = f"{name} in {namespace} to revision {revision}"
    return {"message": f"Dry run: Helm can roll back {what}" if dry_run else f"Rolled back {what}", "output": out[-2000:]}


@api.post("/helm/releases/{namespace}/{name}/rollback")
def rollback_helm_release(namespace: str, name: str, body: RollbackBody, request: Request) -> dict:
    if body.revision is None:
        raise HTTPException(status_code=400, detail="revision is required")
    return run_action(request, "helm-rollback", f"helmrelease/{namespace}/{name}", body.dry_run,
                      lambda: helm_rollback(namespace, name, body.revision, body.dry_run), {"revision": body.revision})


@api.post("/deployments/{namespace}/{name}/rollback")
def rollback_deployment(namespace: str, name: str, body: RollbackBody, request: Request) -> dict:
    return run_action(
        request, "rollback", f"deployment/{namespace}/{name}", body.dry_run,
        lambda: get_controller().rollback(namespace, name, body.revision, body.dry_run),
        {"revision": body.revision},
    )


@api.delete("/deployments/{namespace}/{name}")
def delete_deployment(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(
        request, "delete", f"deployment/{namespace}/{name}", dry_run,
        lambda: get_controller().delete_deployment(namespace, name, dry_run),
    )


@api.post("/statefulsets/{namespace}/{name}/scale")
def scale_statefulset(namespace: str, name: str, body: ScaleBody, request: Request) -> dict:
    return run_action(
        request, "scale", f"statefulset/{namespace}/{name}", body.dry_run,
        lambda: get_controller().scale_statefulset(namespace, name, body.replicas, body.dry_run),
        {"replicas": body.replicas},
    )


@api.post("/statefulsets/{namespace}/{name}/restart")
def restart_statefulset(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "restart", f"statefulset/{namespace}/{name}", body.dry_run,
                      lambda: get_controller().restart_workload("statefulset", namespace, name, body.dry_run))


@api.post("/daemonsets/{namespace}/{name}/restart")
def restart_daemonset(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "restart", f"daemonset/{namespace}/{name}", body.dry_run,
                      lambda: get_controller().restart_workload("daemonset", namespace, name, body.dry_run))


@api.post("/cronjobs/{namespace}/{name}/suspend")
def suspend_cronjob(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "suspend", f"cronjob/{namespace}/{name}", body.dry_run,
                      lambda: get_controller().suspend_cronjob(namespace, name, True, body.dry_run))


@api.post("/cronjobs/{namespace}/{name}/resume")
def resume_cronjob(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "resume", f"cronjob/{namespace}/{name}", body.dry_run,
                      lambda: get_controller().suspend_cronjob(namespace, name, False, body.dry_run))


@api.post("/cronjobs/{namespace}/{name}/trigger")
def trigger_cronjob(namespace: str, name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "trigger", f"cronjob/{namespace}/{name}", body.dry_run,
                      lambda: get_controller().trigger_cronjob(namespace, name, body.dry_run))


@api.delete("/jobs/{namespace}/{name}")
def delete_job(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(request, "delete", f"job/{namespace}/{name}", dry_run,
                      lambda: get_controller().delete_job(namespace, name, dry_run))


@api.delete("/pods/{namespace}/{name}")
def delete_pod(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(
        request, "delete", f"pod/{namespace}/{name}", dry_run,
        lambda: get_controller().delete_pod(namespace, name, dry_run),
    )


@api.delete("/services/{namespace}/{name}")
def delete_service(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(
        request, "delete", f"service/{namespace}/{name}", dry_run,
        lambda: get_controller().delete_service(namespace, name, dry_run),
    )


@api.delete("/configmaps/{namespace}/{name}")
def delete_configmap(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(
        request, "delete", f"configmap/{namespace}/{name}", dry_run,
        lambda: get_controller().delete_configmap(namespace, name, dry_run),
    )


@api.delete("/secrets/{namespace}/{name}")
def delete_secret(namespace: str, name: str, request: Request, dry_run: bool = False) -> dict:
    return run_action(
        request, "delete", f"secret/{namespace}/{name}", dry_run,
        lambda: get_controller().delete_secret(namespace, name, dry_run),
    )


@api.post("/nodes/{name}/cordon")
def cordon_node(name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "cordon", f"node/{name}", body.dry_run,
                      lambda: get_controller().cordon(name, True, body.dry_run))


@api.post("/nodes/{name}/uncordon")
def uncordon_node(name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "uncordon", f"node/{name}", body.dry_run,
                      lambda: get_controller().cordon(name, False, body.dry_run))


@api.post("/nodes/{name}/drain")
def drain_node(name: str, body: DryRun, request: Request) -> dict:
    return run_action(request, "drain", f"node/{name}", body.dry_run,
                      lambda: get_controller().drain(name, body.dry_run))


class EditBody(BaseModel):
    kind: str = Field(max_length=64)
    namespace: Optional[str] = Field(None, max_length=253)
    name: str = Field(max_length=253)
    yaml: str = Field(max_length=500_000)
    dry_run: bool = False


@api.put("/resource")
def edit_resource(body: EditBody, request: Request) -> dict:
    target = f"{body.kind.lower()}/{body.namespace + '/' if body.namespace else ''}{body.name}"
    return run_action(request, "edit", target, body.dry_run,
                      lambda: get_controller().replace_object(body.yaml, body.kind, body.namespace, body.name, body.dry_run),
                      {"bytes": len(body.yaml)})


@app.websocket("/api/v1/pods/{namespace}/{pod}/exec")
async def pod_exec(ws: WebSocket, namespace: str, pod: str, container: Optional[str] = None) -> None:
    """Interactive shell in a container. Needs the API token, control enabled and a non-protected namespace."""
    try:
        require_token(ws.headers.get("authorization"))
    except HTTPException as e:
        await ws.close(code=4401, reason=str(e.detail)[:120])
        return
    if not settings.control_enabled:
        await ws.close(code=4403, reason="control actions are disabled on this agent")
        return
    if namespace in settings.protected:
        await ws.close(code=4403, reason=f"namespace {namespace} is protected")
        return
    await ws.accept()
    actor = (ws.headers.get("x-k10y-actor") or "unknown")[:64]
    target = f"pod/{namespace}/{pod}" + (f"/{container}" if container else "")
    started = time.monotonic()
    try:
        # Own ApiClient: kubernetes.stream temporarily swaps the client's request method while connecting.
        core = client.CoreV1Api(client.ApiClient(get_api_client().configuration))
        resp = await asyncio.to_thread(podexec.open_exec, core, namespace, pod, container)
    except Exception as e:
        reason = api_error_message(e) if isinstance(e, ApiException) else str(e)
        audit_log.record({"time": now_iso(), "actor": actor, "client": ws.client.host if ws.client else None,
                          "action": "exec", "target": target, "params": {}, "dry_run": False, "ok": False, "message": reason})
        await ws.send_text(f"\r\n\x1b[31mCould not start a shell: {reason}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    try:
        await podexec.bridge(ws, resp)
    except WebSocketDisconnect:
        pass
    finally:
        audit_log.record({"time": now_iso(), "actor": actor, "client": ws.client.host if ws.client else None,
                          "action": "exec", "target": target, "params": {"seconds": int(time.monotonic() - started)},
                          "dry_run": False, "ok": True, "message": "shell session ended"})
        try:
            await ws.close()
        except RuntimeError:
            pass


@api.post("/apply")
def apply_manifest(body: ApplyBody, request: Request) -> dict:
    return run_action(
        request, "apply", "manifest", body.dry_run,
        lambda: get_controller().apply(body.yaml, body.dry_run),
        {"bytes": len(body.yaml)},
    )


app.include_router(api)
