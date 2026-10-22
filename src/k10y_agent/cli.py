"""k10y CLI: talks to one or more k10y agents over HTTP."""

import getpass
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import typer
import yaml
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Monitor Kubernetes clusters through k10y agents.", no_args_is_help=True)
cluster_app = typer.Typer(help="Manage the agent endpoints the CLI knows about.", no_args_is_help=True)
app.add_typer(cluster_app, name="cluster")

console = Console()
CONFIG_PATH = Path(os.environ.get("K10Y_CONFIG", str(Path.home() / ".k10y" / "config.yaml")))

ClusterOpt = typer.Option(None, "--cluster", "-C", help="Cluster to query (default: current).")
NamespaceOpt = typer.Option(None, "--namespace", "-n", help="Limit to one namespace.")

HEALTH_STYLE = {"healthy": "green", "degraded": "yellow", "critical": "red"}


# --- config -----------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"current": None, "clusters": {}}
    data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    data.setdefault("current", None)
    data.setdefault("clusters", {})
    return data


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    CONFIG_PATH.chmod(0o600)  # contains tokens


def _resolve(name: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    cfg = load_config()
    name = name or cfg["current"]
    if not name:
        _fail("no cluster selected; run `k10y cluster add NAME URL --token TOKEN` first")
    if name not in cfg["clusters"]:
        _fail(f"unknown cluster {name!r}; see `k10y cluster list`")
    return name, cfg["clusters"][name]


def _fail(msg: str) -> None:
    console.print(f"[red]error:[/red] {msg}")
    raise typer.Exit(1)


# --- http -------------------------------------------------------------------


def _request(
    endpoint: Dict[str, Any],
    path: str,
    params: Optional[dict] = None,
    method: str = "GET",
    body: Optional[dict] = None,
) -> httpx.Response:
    headers = {"X-K10Y-Actor": f"cli:{getpass.getuser()}"}
    if endpoint.get("token"):
        headers["Authorization"] = f"Bearer {endpoint['token']}"
    params = {k: v for k, v in (params or {}).items() if v is not None}
    resp = httpx.request(
        method, endpoint["url"].rstrip("/") + path, headers=headers, params=params, json=body, timeout=60
    )
    resp.raise_for_status()
    return resp


def _error_detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, list):
            return "; ".join(f"{d.get('loc', [''])[-1]}: {d.get('msg')}" for d in detail)
        return str(detail)
    except ValueError:
        return resp.text[:300]


def _call(
    cluster: Optional[str], path: str, params: Optional[dict] = None, method: str = "GET", body: Optional[dict] = None
) -> Any:
    name, endpoint = _resolve(cluster)
    try:
        resp = _request(endpoint, path, params, method, body)
    except httpx.HTTPStatusError as e:
        _fail(f"{name}: {_error_detail(e.response)} (HTTP {e.response.status_code})")
    except httpx.HTTPError as e:
        _fail(f"{name}: cannot reach {endpoint['url']}: {e}")
    return resp.json() if "json" in resp.headers.get("content-type", "") else resp.text


def _get(cluster: Optional[str], path: str, params: Optional[dict] = None) -> Any:
    return _call(cluster, path, params)


# --- formatting -------------------------------------------------------------


def _age(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _mem(n: Optional[int]) -> str:
    if n is None:
        return "-"
    for unit, size in (("Gi", 1 << 30), ("Mi", 1 << 20), ("Ki", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return f"{n}B"


def _cpu(m: Optional[int]) -> str:
    return "-" if m is None else f"{m}m"


def _pct(p: Optional[float]) -> str:
    return "-" if p is None else f"{p:.0f}%"


# --- cluster management -----------------------------------------------------


@cluster_app.command("add")
def cluster_add(
    name: str,
    url: str = typer.Argument(..., help="Agent URL, e.g. http://192.168.64.2:30800"),
    token: str = typer.Option("", "--token", "-t", help="Agent API token."),
) -> None:
    """Register an agent endpoint (and make it current if it is the first)."""
    cfg = load_config()
    cfg["clusters"][name] = {"url": url, "token": token}
    if not cfg["current"]:
        cfg["current"] = name
    save_config(cfg)
    console.print(f"added cluster [bold]{name}[/bold] -> {url}")


@cluster_app.command("list")
def cluster_list() -> None:
    """List registered clusters."""
    cfg = load_config()
    table = Table("", "NAME", "URL")
    for name, ep in cfg["clusters"].items():
        table.add_row("*" if name == cfg["current"] else "", name, ep["url"])
    console.print(table)


@cluster_app.command("use")
def cluster_use(name: str) -> None:
    """Set the current cluster."""
    cfg = load_config()
    if name not in cfg["clusters"]:
        _fail(f"unknown cluster {name!r}")
    cfg["current"] = name
    save_config(cfg)
    console.print(f"current cluster: [bold]{name}[/bold]")


@cluster_app.command("remove")
def cluster_remove(name: str) -> None:
    """Forget a cluster."""
    cfg = load_config()
    if cfg["clusters"].pop(name, None) is None:
        _fail(f"unknown cluster {name!r}")
    if cfg["current"] == name:
        cfg["current"] = next(iter(cfg["clusters"]), None)
    save_config(cfg)
    console.print(f"removed cluster {name}")


# --- monitoring commands ----------------------------------------------------


@app.command()
def status(
    cluster: Optional[str] = ClusterOpt,
    all_clusters: bool = typer.Option(False, "--all", "-A", help="Show every registered cluster."),
) -> None:
    """Health overview of one cluster, or all clusters with --all."""
    if all_clusters:
        cfg = load_config()
        table = Table("CLUSTER", "HEALTH", "NODES", "PODS", "CPU", "MEMORY", "PROBLEMS")
        for name, ep in cfg["clusters"].items():
            try:
                s = _request(ep, "/api/v1/summary").json()
            except httpx.HTTPError as e:
                table.add_row(name, "[red]unreachable[/red]", "-", "-", "-", "-", str(e)[:60])
                continue
            style = HEALTH_STYLE.get(s["health"], "white")
            table.add_row(
                name,
                f"[{style}]{s['health']}[/{style}]",
                f"{s['nodes']['ready']}/{s['nodes']['total']}",
                str(s["pods"]["total"]),
                _pct(s["cpu"]["percent"]),
                _pct(s["memory"]["percent"]),
                str(len(s["problems"])),
            )
        console.print(table)
        return

    s = _get(cluster, "/api/v1/summary")
    style = HEALTH_STYLE.get(s["health"], "white")
    console.print(
        f"[bold]{s['cluster']}[/bold]  {s['kubernetes_version']}  health: [{style}]{s['health']}[/{style}]"
    )
    console.print(
        f"nodes {s['nodes']['ready']}/{s['nodes']['total']} ready · "
        f"pods {s['pods']['total']} {s['pods']['by_phase']} · "
        f"deployments {s['deployments']['ready']}/{s['deployments']['total']} ready · "
        f"namespaces {s['namespaces']}"
    )
    console.print(
        f"cpu {_cpu(s['cpu']['usage_m'])}/{_cpu(s['cpu']['allocatable_m'])} ({_pct(s['cpu']['percent'])}) · "
        f"memory {_mem(s['memory']['usage_bytes'])}/{_mem(s['memory']['allocatable_bytes'])} "
        f"({_pct(s['memory']['percent'])})"
    )
    if s["problems"]:
        table = Table("KIND", "NAMESPACE", "NAME", "REASON", title="Problems")
        for p in s["problems"]:
            table.add_row(p["kind"], p["namespace"] or "-", p["name"], f"[yellow]{p['reason']}[/yellow]")
        console.print(table)


@app.command()
def nodes(cluster: Optional[str] = ClusterOpt) -> None:
    """Nodes with readiness and resource usage."""
    table = Table("NAME", "STATUS", "ROLES", "IP", "CPU", "CPU%", "MEMORY", "MEM%", "VERSION", "AGE")
    for n in _get(cluster, "/api/v1/nodes"):
        state = "[green]Ready[/green]" if n["ready"] else "[red]NotReady[/red]"
        if n["unschedulable"]:
            state += ",SchedulingDisabled"
        if n["pressure"]:
            state += "," + ",".join(n["pressure"])
        table.add_row(
            n["name"],
            state,
            ",".join(n["roles"]) or "-",
            n["internal_ip"] or "-",
            _cpu(n["cpu_usage_m"]),
            _pct(n["cpu_percent"]),
            _mem(n["memory_usage_bytes"]),
            _pct(n["memory_percent"]),
            n["kubelet_version"],
            _age(n["age_s"]),
        )
    console.print(table)


@app.command()
def pods(
    cluster: Optional[str] = ClusterOpt,
    namespace: Optional[str] = NamespaceOpt,
    problems: bool = typer.Option(False, "--problems", "-p", help="Only unhealthy pods."),
    sort: str = typer.Option("name", "--sort", help="name | cpu | memory | restarts"),
) -> None:
    """Pods with status, restarts and usage."""
    items: List[dict] = _get(cluster, "/api/v1/pods", {"namespace": namespace, "problems_only": problems})
    keys = {
        "cpu": lambda p: -(p["cpu_usage_m"] or 0),
        "memory": lambda p: -(p["memory_usage_bytes"] or 0),
        "restarts": lambda p: -p["restarts"],
    }
    items.sort(key=keys.get(sort, lambda p: (p["namespace"], p["name"])))
    table = Table("NAMESPACE", "NAME", "READY", "STATUS", "RESTARTS", "CPU", "MEMORY", "NODE", "AGE")
    for p in items:
        status_text = f"[yellow]{p['status']}[/yellow]" if p["problem"] else p["status"]
        table.add_row(
            p["namespace"],
            p["name"],
            f"{p['ready_containers']}/{p['total_containers']}",
            status_text,
            str(p["restarts"]),
            _cpu(p["cpu_usage_m"]),
            _mem(p["memory_usage_bytes"]),
            p["node"] or "-",
            _age(p["age_s"]),
        )
    console.print(table)


@app.command()
def deployments(cluster: Optional[str] = ClusterOpt, namespace: Optional[str] = NamespaceOpt) -> None:
    """Deployments and their replica status."""
    table = Table("NAMESPACE", "NAME", "READY", "UP-TO-DATE", "AVAILABLE", "IMAGES", "AGE")
    for d in _get(cluster, "/api/v1/deployments", {"namespace": namespace}):
        ready = f"{d['ready']}/{d['desired']}"
        table.add_row(
            d["namespace"],
            d["name"],
            ready if d["ready"] >= d["desired"] else f"[yellow]{ready}[/yellow]",
            str(d["updated"]),
            str(d["available"]),
            ", ".join(d["images"]),
            _age(d["age_s"]),
        )
    console.print(table)


@app.command()
def events(
    cluster: Optional[str] = ClusterOpt,
    namespace: Optional[str] = NamespaceOpt,
    warnings: bool = typer.Option(False, "--warnings", "-w", help="Only Warning events."),
    limit: int = typer.Option(50, "--limit", "-l"),
) -> None:
    """Recent cluster events, newest first."""
    params = {"namespace": namespace, "warnings_only": warnings, "limit": limit}
    table = Table("AGE", "TYPE", "REASON", "NAMESPACE", "OBJECT", "MESSAGE")
    for e in _get(cluster, "/api/v1/events", params):
        etype = f"[yellow]{e['type']}[/yellow]" if e["type"] == "Warning" else e["type"]
        table.add_row(_age(e["age_s"]), etype, e["reason"] or "-", e["namespace"] or "-", e["object"], e["message"] or "")
    console.print(table)


@app.command()
def logs(
    namespace: str,
    pod: str,
    cluster: Optional[str] = ClusterOpt,
    container: Optional[str] = typer.Option(None, "--container", "-c"),
    tail: int = typer.Option(200, "--tail"),
    previous: bool = typer.Option(False, "--previous", "-p", help="Logs of the previous (crashed) container."),
) -> None:
    """Print a pod's logs."""
    params = {"container": container, "tail": tail, "previous": previous}
    text = _get(cluster, f"/api/v1/pods/{namespace}/{pod}/logs", params)
    print(text, end="")


@app.command()
def services(cluster: Optional[str] = ClusterOpt, namespace: Optional[str] = NamespaceOpt) -> None:
    """Services with their ports and NodePort URLs."""
    nodes_list = _get(cluster, "/api/v1/nodes")
    host = next((n["internal_ip"] for n in nodes_list if n["ready"] and n["internal_ip"]), "<node-ip>")
    table = Table("NAMESPACE", "NAME", "TYPE", "CLUSTER-IP", "PORTS", "ACCESS", "AGE")
    for s in _get(cluster, "/api/v1/services", {"namespace": namespace}):
        ports = ", ".join(
            f"{p['port']}" + (f"->{p['target_port']}" if p["target_port"] and p["target_port"] != str(p["port"]) else "")
            + f"/{p['protocol']}" for p in s["ports"]
        )
        access = "\n".join(f"http://{host}:{p['node_port']}" for p in s["ports"] if p["node_port"]) or "-"
        table.add_row(s["namespace"], s["name"], s["type"], s["cluster_ip"] or "-", ports or "-", access, _age(s["age_s"]))
    console.print(table)


# --- control commands -------------------------------------------------------

DryRunOpt = typer.Option(False, "--dry-run", help="Validate on the server without changing anything.")
YesOpt = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation.")


def _confirm(question: str, yes: bool, dry_run: bool) -> None:
    if yes or dry_run:
        return
    if not sys.stdin.isatty():
        _fail("refusing to run without confirmation; pass --yes")
    if not typer.confirm(question):
        raise typer.Exit(1)


def _act(cluster: Optional[str], method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    res = _call(cluster, path, params, method, body)
    style = "cyan" if res.get("dry_run") else "green"
    console.print(f"[{style}]{'dry run' if res.get('dry_run') else 'ok'}:[/{style}] {res['message']}")
    return res


@app.command()
def scale(
    namespace: str, deployment: str, replicas: int,
    cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt,
) -> None:
    """Scale a deployment to N replicas."""
    _act(cluster, "POST", f"/api/v1/deployments/{namespace}/{deployment}/scale", {"replicas": replicas, "dry_run": dry_run})


@app.command()
def restart(namespace: str, deployment: str, cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt) -> None:
    """Rolling restart of a deployment."""
    _act(cluster, "POST", f"/api/v1/deployments/{namespace}/{deployment}/restart", {"dry_run": dry_run})


@app.command("set-image")
def set_image(
    namespace: str, deployment: str,
    image: str = typer.Argument(..., help="New image, e.g. nginx:1.28"),
    container: Optional[str] = typer.Option(None, "--container", "-c", help="Container name (default: the only one)."),
    cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt,
) -> None:
    """Update a deployment's container image (rolling update)."""
    if container is None:
        deps = _get(cluster, "/api/v1/deployments", {"namespace": namespace})
        dep = next((d for d in deps if d["name"] == deployment), None)
        if dep is None:
            _fail(f"deployment {namespace}/{deployment} not found")
        names = [c["name"] for c in dep["containers"]]
        if len(names) != 1:
            _fail(f"deployment has several containers ({', '.join(names)}); pass --container")
        container = names[0]
    _act(cluster, "POST", f"/api/v1/deployments/{namespace}/{deployment}/image",
         {"container": container, "image": image, "dry_run": dry_run})


@app.command()
def history(namespace: str, deployment: str, cluster: Optional[str] = ClusterOpt) -> None:
    """Revision history of a deployment."""
    table = Table("REVISION", "CURRENT", "IMAGES", "REPLICAS", "CREATED")
    for r in _get(cluster, f"/api/v1/deployments/{namespace}/{deployment}/history"):
        table.add_row(f"#{r['revision']}", "*" if r["current"] else "", ", ".join(r["images"]), str(r["replicas"]), r["created"] or "-")
    console.print(table)


@app.command()
def rollback(
    namespace: str, deployment: str,
    revision: Optional[int] = typer.Option(None, "--to-revision", help="Revision to return to (default: previous)."),
    cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt,
) -> None:
    """Roll a deployment back to an earlier revision."""
    _act(cluster, "POST", f"/api/v1/deployments/{namespace}/{deployment}/rollback", {"revision": revision, "dry_run": dry_run})


@app.command()
def delete(
    kind: str = typer.Argument(..., help="deployment | pod | service"),
    namespace: str = typer.Argument(...),
    name: str = typer.Argument(...),
    cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt, yes: bool = YesOpt,
) -> None:
    """Delete a deployment, pod or service."""
    kind = kind.lower().rstrip("s")
    if kind not in ("deployment", "pod", "service"):
        _fail("kind must be deployment, pod or service")
    _confirm(f"Delete {kind} {namespace}/{name}?", yes, dry_run)
    _act(cluster, "DELETE", f"/api/v1/{kind}s/{namespace}/{name}", params={"dry_run": dry_run})


@app.command()
def cordon(node: str, cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt) -> None:
    """Mark a node unschedulable."""
    _act(cluster, "POST", f"/api/v1/nodes/{node}/cordon", {"dry_run": dry_run})


@app.command()
def uncordon(node: str, cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt) -> None:
    """Mark a node schedulable again."""
    _act(cluster, "POST", f"/api/v1/nodes/{node}/uncordon", {"dry_run": dry_run})


@app.command()
def drain(node: str, cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt, yes: bool = YesOpt) -> None:
    """Cordon a node and evict its pods (protected namespaces are skipped)."""
    _confirm(f"Drain node {node}? Its pods will be evicted.", yes, dry_run)
    res = _act(cluster, "POST", f"/api/v1/nodes/{node}/drain", {"dry_run": dry_run})
    for p in res.get("evicted", []):
        console.print(f"  evict {p}")
    for p in res.get("failed", []):
        console.print(f"  [red]failed[/red] {p['pod']}: {p['reason']}")


@app.command()
def apply(
    file: Path = typer.Option(..., "--filename", "-f", help="YAML file, or - for stdin.", allow_dash=True),
    cluster: Optional[str] = ClusterOpt, dry_run: bool = DryRunOpt,
) -> None:
    """Create or update objects from a YAML file (server-side apply)."""
    text = sys.stdin.read() if str(file) == "-" else file.read_text()
    res = _act(cluster, "POST", "/api/v1/apply", {"yaml": text, "dry_run": dry_run})
    for r in res.get("results", []):
        console.print(f"  {r['object']}")


@app.command()
def audit(cluster: Optional[str] = ClusterOpt, limit: int = typer.Option(30, "--limit", "-l")) -> None:
    """Recent control actions recorded by the agent."""
    table = Table("TIME", "SOURCE", "ACTION", "TARGET", "RESULT", "DETAILS")
    for a in _get(cluster, "/api/v1/audit", {"limit": limit}):
        result = "[red]failed[/red]" if not a["ok"] else "dry run" if a["dry_run"] else "[green]ok[/green]"
        table.add_row(a["time"], a["actor"], a["action"], a["target"], result, a["message"])
    console.print(table)


if __name__ == "__main__":
    app()
