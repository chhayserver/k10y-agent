import secrets
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException
from pydantic import BaseModel, Field

from . import __version__
from .collectors import Collector
from .config import Settings
from .metrics_history import MetricsHistory
from .controls import ActionError, AuditLog, Controller, ProtectedNamespaceError, api_error_message, now_iso

STATIC_DIR = Path(__file__).parent / "static"

settings = Settings()
audit_log = AuditLog(settings.audit_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.mode != "dashboard":
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
def dashboard() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text()
    html = html.replace('<meta name="k10y-mode" content="agent">', f'<meta name="k10y-mode" content="{settings.mode}">')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def require_agent_mode() -> None:
    if settings.mode == "dashboard":
        raise HTTPException(status_code=404, detail="this is a dashboard-only instance; it has no cluster API")


api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_agent_mode), Depends(require_token)])


# --- read -------------------------------------------------------------------


@api.get("/info")
def info() -> dict:
    return {
        "cluster": settings.cluster_name,
        "agent_version": __version__,
        "kubernetes_version": get_collector().version(),
        "control_enabled": settings.control_enabled,
        "protected_namespaces": settings.protected,
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
    scope: str = Query("cluster", pattern="^(cluster|nodes|pod)$"),
    since: int = Query(0, ge=0, description="Only points newer than this epoch-ms timestamp."),
    namespace: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    if scope == "pod" and not (namespace and name):
        raise HTTPException(status_code=400, detail="scope=pod needs namespace and name")
    return metrics_history.query(scope, since, namespace, name)


@api.get("/namespaces")
def namespaces() -> list:
    return get_collector().namespaces()


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


@api.get("/deployments/{namespace}/{name}/history")
def deployment_history(namespace: str, name: str) -> list:
    return get_controller().history(namespace, name)


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


@api.post("/apply")
def apply_manifest(body: ApplyBody, request: Request) -> dict:
    return run_action(
        request, "apply", "manifest", body.dry_run,
        lambda: get_controller().apply(body.yaml, body.dry_run),
        {"bytes": len(body.yaml)},
    )


app.include_router(api)
