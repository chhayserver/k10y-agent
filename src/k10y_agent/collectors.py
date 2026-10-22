"""Read-only collectors that turn Kubernetes API objects into plain dicts."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.utils import parse_quantity

BAD_POD_STATUSES = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "OOMKilled",
    "Error",
    "ContainerCannotRun",
}
# Node conditions that signal trouble when True. Distributions add their own
# conditions (e.g. RKE2's EtcdIsVoter=True is healthy), so this is an allowlist.
NODE_PRESSURE_CONDITIONS = {"MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"}
RESTART_WARN_THRESHOLD = 5
PENDING_GRACE_SECONDS = 300


def _age_seconds(ts: Optional[datetime]) -> Optional[int]:
    if ts is None:
        return None
    return int((datetime.now(timezone.utc) - ts).total_seconds())


def _millicores(quantity: Optional[str]) -> int:
    return int(parse_quantity(quantity) * 1000) if quantity else 0


def _bytes(quantity: Optional[str]) -> int:
    return int(parse_quantity(quantity)) if quantity else 0


def _percent(used: int, total: int) -> Optional[float]:
    return round(used * 100 / total, 1) if total else None


def node_info(node: client.V1Node) -> Dict[str, Any]:
    conditions = node.status.conditions or []
    labels = node.metadata.labels or {}
    addresses = {a.type: a.address for a in node.status.addresses or []}
    allocatable = node.status.allocatable or {}
    return {
        "name": node.metadata.name,
        "ready": any(c.type == "Ready" and c.status == "True" for c in conditions),
        "unschedulable": bool(node.spec.unschedulable),
        "roles": sorted(
            k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")
        ),
        "pressure": [c.type for c in conditions if c.type in NODE_PRESSURE_CONDITIONS and c.status == "True"],
        "internal_ip": addresses.get("InternalIP"),
        "kubelet_version": node.status.node_info.kubelet_version,
        "os_image": node.status.node_info.os_image,
        "cpu_allocatable_m": _millicores(allocatable.get("cpu")),
        "memory_allocatable_bytes": _bytes(allocatable.get("memory")),
        "age_s": _age_seconds(node.metadata.creation_timestamp),
    }


def _resource_sum(containers: List[client.V1Container], kind: str, resource: str) -> Optional[int]:
    """Sum of requests/limits over containers; None when no container sets it."""
    values = [((getattr(c.resources, kind) or {}) if c.resources else {}).get(resource) for c in containers]
    values = [v for v in values if v]
    if not values:
        return None
    return sum(_millicores(v) if resource == "cpu" else _bytes(v) for v in values)


def pod_info(pod: client.V1Pod) -> Dict[str, Any]:
    statuses = pod.status.container_statuses or []
    status = pod.status.phase or "Unknown"
    for cs in statuses:
        state = cs.state
        if state and state.waiting and state.waiting.reason:
            status = state.waiting.reason
            break
        if state and state.terminated and state.terminated.reason and pod.status.phase != "Succeeded":
            status = state.terminated.reason
    if pod.metadata.deletion_timestamp:
        status = "Terminating"
    return {
        "namespace": pod.metadata.namespace,
        "name": pod.metadata.name,
        "node": pod.spec.node_name,
        "phase": pod.status.phase,
        "status": status,
        "ready_containers": sum(1 for cs in statuses if cs.ready),
        "total_containers": len(pod.spec.containers),
        "containers": [c.name for c in pod.spec.containers],
        "images": [c.image for c in pod.spec.containers],
        "cpu_request_m": _resource_sum(pod.spec.containers, "requests", "cpu"),
        "cpu_limit_m": _resource_sum(pod.spec.containers, "limits", "cpu"),
        "memory_request_bytes": _resource_sum(pod.spec.containers, "requests", "memory"),
        "memory_limit_bytes": _resource_sum(pod.spec.containers, "limits", "memory"),
        "restarts": sum(cs.restart_count for cs in statuses),
        "last_termination_reasons": [
            cs.last_state.terminated.reason
            for cs in statuses
            if cs.last_state and cs.last_state.terminated and cs.last_state.terminated.reason
        ],
        "pod_ip": pod.status.pod_ip,
        "age_s": _age_seconds(pod.metadata.creation_timestamp),
    }


def pod_problem(info: Dict[str, Any]) -> Optional[str]:
    """Return a short reason if the pod looks unhealthy, else None."""
    if info["status"] in BAD_POD_STATUSES:
        return info["status"]
    if info["phase"] == "Failed":
        return "Failed"
    if info["phase"] == "Pending" and (info["age_s"] or 0) > PENDING_GRACE_SECONDS:
        return "Pending for more than 5m"
    if info["restarts"] >= RESTART_WARN_THRESHOLD:
        last = info["last_termination_reasons"]
        return f"{info['restarts']} restarts" + (f" (last: {last[0]})" if last else "")
    return None


def deployment_info(dep: client.V1Deployment) -> Dict[str, Any]:
    st = dep.status
    return {
        "namespace": dep.metadata.namespace,
        "name": dep.metadata.name,
        "desired": dep.spec.replicas or 0,
        "ready": st.ready_replicas or 0,
        "updated": st.updated_replicas or 0,
        "available": st.available_replicas or 0,
        "images": [c.image for c in dep.spec.template.spec.containers],
        "containers": [{"name": c.name, "image": c.image} for c in dep.spec.template.spec.containers],
        "revision": (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
        "paused": bool(dep.spec.paused),
        "age_s": _age_seconds(dep.metadata.creation_timestamp),
    }


def _containers(spec: client.V1PodSpec) -> List[Dict[str, str]]:
    return [{"name": c.name, "image": c.image} for c in spec.containers]


def statefulset_info(s: client.V1StatefulSet) -> Dict[str, Any]:
    st = s.status
    return {
        "namespace": s.metadata.namespace, "name": s.metadata.name,
        "desired": s.spec.replicas if s.spec.replicas is not None else 1,
        "ready": st.ready_replicas or 0, "current": st.current_replicas or 0, "updated": st.updated_replicas or 0,
        "service_name": s.spec.service_name, "containers": _containers(s.spec.template.spec),
        "images": [c.image for c in s.spec.template.spec.containers],
        "age_s": _age_seconds(s.metadata.creation_timestamp),
    }


def daemonset_info(d: client.V1DaemonSet) -> Dict[str, Any]:
    st = d.status
    return {
        "namespace": d.metadata.namespace, "name": d.metadata.name,
        "desired": st.desired_number_scheduled or 0, "current": st.current_number_scheduled or 0,
        "ready": st.number_ready or 0, "updated": st.updated_number_scheduled or 0, "available": st.number_available or 0,
        "node_selector": d.spec.template.spec.node_selector or {}, "containers": _containers(d.spec.template.spec),
        "images": [c.image for c in d.spec.template.spec.containers],
        "age_s": _age_seconds(d.metadata.creation_timestamp),
    }


def job_info(j: Any) -> Dict[str, Any]:
    st = j.status
    conds = {c.type: c for c in st.conditions or [] if c.status == "True"}
    if "Complete" in conds or "SuccessCriteriaMet" in conds:
        status = "Complete"
    elif "Failed" in conds:
        status = "Failed"
    elif j.spec.suspend:
        status = "Suspended"
    else:
        status = "Running"
    duration = None
    if st.start_time:
        end = st.completion_time or (conds["Failed"].last_transition_time if "Failed" in conds else None)
        duration = int(((end or datetime.now(timezone.utc)) - st.start_time).total_seconds())
    owner = next((f"{o.kind}/{o.name}" for o in j.metadata.owner_references or [] if o.controller), None)
    return {
        "namespace": j.metadata.namespace, "name": j.metadata.name, "status": status,
        "completions": j.spec.completions if j.spec.completions is not None else 1,
        "succeeded": st.succeeded or 0, "failed": st.failed or 0, "active": st.active or 0,
        "failure_reason": conds["Failed"].reason if "Failed" in conds else None,
        "duration_s": duration, "owner": owner, "containers": _containers(j.spec.template.spec),
        "images": [c.image for c in j.spec.template.spec.containers],
        "age_s": _age_seconds(j.metadata.creation_timestamp),
    }


def service_info(svc: client.V1Service) -> Dict[str, Any]:
    spec = svc.spec
    ingress = (svc.status.load_balancer.ingress or []) if svc.status and svc.status.load_balancer else []
    return {
        "namespace": svc.metadata.namespace,
        "name": svc.metadata.name,
        "type": spec.type,
        "cluster_ip": spec.cluster_ip,
        "external_ips": (spec.external_ips or []) + [i.ip or i.hostname for i in ingress],
        "ports": [
            {
                "name": p.name,
                "protocol": p.protocol,
                "port": p.port,
                "target_port": str(p.target_port) if p.target_port is not None else None,
                "node_port": p.node_port,
            }
            for p in spec.ports or []
        ],
        "selector": spec.selector or {},
        "age_s": _age_seconds(svc.metadata.creation_timestamp),
    }


def event_info(ev: client.CoreV1Event) -> Dict[str, Any]:
    ts = ev.last_timestamp or ev.event_time or ev.metadata.creation_timestamp
    obj = ev.involved_object
    return {
        "type": ev.type,
        "reason": ev.reason,
        "message": ev.message,
        "object": f"{obj.kind}/{obj.name}",
        "namespace": obj.namespace,
        "count": ev.count or 1,
        "timestamp": ts.isoformat() if ts else None,
        "age_s": _age_seconds(ts),
    }


class Collector:
    def __init__(self, api_client: client.ApiClient):
        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)
        self.custom = client.CustomObjectsApi(api_client)
        self.version_api = client.VersionApi(api_client)
        self.networking = client.NetworkingV1Api(api_client)
        self.batch = client.BatchV1Api(api_client)

    def topology(self, namespace: str) -> Dict[str, Any]:
        from .topology import build_topology

        try:
            ingresses = self.networking.list_namespaced_ingress(namespace).items
        except ApiException as e:
            if e.status not in (403, 404):
                raise
            ingresses = []
        graph = build_topology(
            pods=self.core.list_namespaced_pod(namespace).items,
            deployments=self.apps.list_namespaced_deployment(namespace).items,
            replicasets=self.apps.list_namespaced_replica_set(namespace).items,
            statefulsets=self.apps.list_namespaced_stateful_set(namespace).items,
            daemonsets=self.apps.list_namespaced_daemon_set(namespace).items,
            services=self.core.list_namespaced_service(namespace).items,
            configmaps=self.core.list_namespaced_config_map(namespace).items,
            pvcs=self.core.list_namespaced_persistent_volume_claim(namespace).items,
            ingresses=ingresses,
            jobs=self.batch.list_namespaced_job(namespace).items,
            secrets=self.core.list_namespaced_secret(namespace).items,
        )
        return {"namespace": namespace, **graph}

    def _config_usage(self, namespace: Optional[str]) -> Dict[Tuple[str, str, str], List[str]]:
        """(kind, namespace, name) -> sorted workload/pod names that reference it."""
        from .topology import config_refs

        pods = (self.core.list_namespaced_pod(namespace) if namespace else self.core.list_pod_for_all_namespaces()).items
        rsets = (self.apps.list_namespaced_replica_set(namespace) if namespace
                 else self.apps.list_replica_set_for_all_namespaces()).items
        rs_owner = {
            rs.metadata.uid: f"{o.kind}/{o.name}"
            for rs in rsets for o in rs.metadata.owner_references or [] if o.controller
        }
        usage: Dict[Tuple[str, str, str], set] = {}
        for p in pods:
            ctrl = next((o for o in p.metadata.owner_references or [] if o.controller), None)
            who = rs_owner.get(ctrl.uid, f"{ctrl.kind}/{ctrl.name}") if ctrl else f"Pod/{p.metadata.name}"
            for kind, name, _, _ in config_refs(p.spec):
                usage.setdefault((kind, p.metadata.namespace, name), set()).add(who)
        return {k: sorted(v) for k, v in usage.items()}

    def configmaps(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (self.core.list_namespaced_config_map(namespace) if namespace
                 else self.core.list_config_map_for_all_namespaces()).items
        usage = self._config_usage(namespace)
        out = []
        for c in items:
            data, binary = c.data or {}, c.binary_data or {}
            out.append({
                "namespace": c.metadata.namespace,
                "name": c.metadata.name,
                "keys": sorted(list(data) + list(binary)),
                "size_bytes": sum(len(v.encode()) for v in data.values()) + sum(len(v) * 3 // 4 for v in binary.values()),
                "used_by": usage.get(("ConfigMap", c.metadata.namespace, c.metadata.name), []),
                "age_s": _age_seconds(c.metadata.creation_timestamp),
            })
        return out

    def configmap(self, namespace: str, name: str) -> Dict[str, Any]:
        c = self.core.read_namespaced_config_map(name, namespace)
        limit = 64 * 1024
        return {
            "namespace": namespace,
            "name": name,
            "data": {k: (v if len(v) <= limit else v[:limit] + "\n… (truncated)") for k, v in (c.data or {}).items()},
            "binary_keys": sorted(c.binary_data or {}),
            "labels": c.metadata.labels or {},
            "used_by": self._config_usage(namespace).get(("ConfigMap", namespace, name), []),
        }

    def secrets(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Secret metadata and key NAMES only. Values are never returned."""
        items = (self.core.list_namespaced_secret(namespace) if namespace
                 else self.core.list_secret_for_all_namespaces()).items
        usage = self._config_usage(namespace)
        return [
            {
                "namespace": s.metadata.namespace,
                "name": s.metadata.name,
                "type": s.type,
                "keys": sorted(s.data or {}),
                "used_by": usage.get(("Secret", s.metadata.namespace, s.metadata.name), []),
                "age_s": _age_seconds(s.metadata.creation_timestamp),
            }
            for s in items
        ]

    def namespaces(self) -> List[str]:
        return sorted(n.metadata.name for n in self.core.list_namespace().items)

    def namespace_details(self) -> List[Dict[str, Any]]:
        """Every namespace with workload counts, problems and current CPU/memory use."""
        out: Dict[str, Dict[str, Any]] = {}
        for n in self.core.list_namespace().items:
            out[n.metadata.name] = {
                "name": n.metadata.name, "status": n.status.phase if n.status else "Unknown",
                "labels": {k: v for k, v in (n.metadata.labels or {}).items() if k != "kubernetes.io/metadata.name"},
                "age_s": _age_seconds(n.metadata.creation_timestamp),
                "pods": 0, "running": 0, "problems": 0, "deployments": 0, "services": 0,
                "cpu_usage_m": None, "memory_usage_bytes": None,
            }
        for p in self.pods():
            ns = out.get(p["namespace"])
            if ns is None:
                continue
            ns["pods"] += 1
            ns["running"] += p["phase"] == "Running"
            ns["problems"] += bool(p["problem"])
            if p["cpu_usage_m"] is not None:
                ns["cpu_usage_m"] = (ns["cpu_usage_m"] or 0) + p["cpu_usage_m"]
                ns["memory_usage_bytes"] = (ns["memory_usage_bytes"] or 0) + p["memory_usage_bytes"]
        for d in self.deployments():
            if d["namespace"] in out:
                out[d["namespace"]]["deployments"] += 1
        for s in self.services():
            if s["namespace"] in out:
                out[s["namespace"]]["services"] += 1
        return sorted(out.values(), key=lambda x: x["name"])

    def version(self) -> str:
        return self.version_api.get_code().git_version

    def _metrics(self, resource: str, namespace: Optional[str] = None) -> Optional[List[dict]]:
        """Raw metrics.k8s.io items, or None when metrics-server is unavailable."""
        try:
            if namespace:
                data = self.custom.list_namespaced_custom_object(
                    "metrics.k8s.io", "v1beta1", namespace, resource
                )
            else:
                data = self.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", resource)
        except ApiException as e:
            if e.status in (404, 503):
                return None
            raise
        return data.get("items", [])

    def nodes(self) -> List[Dict[str, Any]]:
        nodes = [node_info(n) for n in self.core.list_node().items]
        usage = {m["metadata"]["name"]: m["usage"] for m in self._metrics("nodes") or []}
        for n in nodes:
            u = usage.get(n["name"])
            n["cpu_usage_m"] = _millicores(u["cpu"]) if u else None
            n["memory_usage_bytes"] = _bytes(u["memory"]) if u else None
            n["cpu_percent"] = _percent(n["cpu_usage_m"], n["cpu_allocatable_m"]) if u else None
            n["memory_percent"] = (
                _percent(n["memory_usage_bytes"], n["memory_allocatable_bytes"]) if u else None
            )
        return nodes

    def pods(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (
            self.core.list_namespaced_pod(namespace).items
            if namespace
            else self.core.list_pod_for_all_namespaces().items
        )
        pods = [pod_info(p) for p in items]
        usage = {}
        for m in self._metrics("pods", namespace) or []:
            containers = m.get("containers", [])
            usage[(m["metadata"]["namespace"], m["metadata"]["name"])] = (
                sum(_millicores(c["usage"]["cpu"]) for c in containers),
                sum(_bytes(c["usage"]["memory"]) for c in containers),
            )
        for p in pods:
            cpu, mem = usage.get((p["namespace"], p["name"]), (None, None))
            p["cpu_usage_m"] = cpu
            p["memory_usage_bytes"] = mem
            p["problem"] = pod_problem(p)
        return pods

    def deployments(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (
            self.apps.list_namespaced_deployment(namespace).items
            if namespace
            else self.apps.list_deployment_for_all_namespaces().items
        )
        return [deployment_info(d) for d in items]

    def statefulsets(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (self.apps.list_namespaced_stateful_set(namespace) if namespace
                 else self.apps.list_stateful_set_for_all_namespaces()).items
        return [statefulset_info(s) for s in items]

    def daemonsets(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (self.apps.list_namespaced_daemon_set(namespace) if namespace
                 else self.apps.list_daemon_set_for_all_namespaces()).items
        return [daemonset_info(d) for d in items]

    def jobs(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (self.batch.list_namespaced_job(namespace) if namespace else self.batch.list_job_for_all_namespaces()).items
        return [job_info(j) for j in items]

    def services(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        items = (
            self.core.list_namespaced_service(namespace).items
            if namespace
            else self.core.list_service_for_all_namespaces().items
        )
        return [service_info(s) for s in items]

    def events(
        self, namespace: Optional[str] = None, warnings_only: bool = False, limit: int = 100
    ) -> List[Dict[str, Any]]:
        selector = "type=Warning" if warnings_only else None
        items = (
            self.core.list_namespaced_event(namespace, field_selector=selector).items
            if namespace
            else self.core.list_event_for_all_namespaces(field_selector=selector).items
        )
        events = [event_info(e) for e in items]
        events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
        return events[:limit]

    def logs(
        self,
        namespace: str,
        pod: str,
        container: Optional[str] = None,
        tail: int = 200,
        previous: bool = False,
    ) -> str:
        return self.core.read_namespaced_pod_log(
            pod, namespace, container=container, tail_lines=tail, previous=previous
        )

    def summary(self, cluster_name: str) -> Dict[str, Any]:
        nodes = self.nodes()
        pods = self.pods()
        deployments = self.deployments()

        problems: List[Dict[str, Any]] = []
        for n in nodes:
            if not n["ready"]:
                problems.append({"kind": "Node", "namespace": None, "name": n["name"], "reason": "NotReady"})
            for cond in n["pressure"]:
                problems.append({"kind": "Node", "namespace": None, "name": n["name"], "reason": cond})
        for p in pods:
            if p["problem"]:
                problems.append(
                    {"kind": "Pod", "namespace": p["namespace"], "name": p["name"], "reason": p["problem"]}
                )
        for d in deployments:
            if d["ready"] < d["desired"]:
                problems.append(
                    {
                        "kind": "Deployment",
                        "namespace": d["namespace"],
                        "name": d["name"],
                        "reason": f"{d['ready']}/{d['desired']} replicas ready",
                    }
                )
        for s in self.statefulsets():
            if s["ready"] < s["desired"]:
                problems.append({"kind": "StatefulSet", "namespace": s["namespace"], "name": s["name"],
                                 "reason": f"{s['ready']}/{s['desired']} replicas ready"})
        for d in self.daemonsets():
            if d["ready"] < d["desired"]:
                problems.append({"kind": "DaemonSet", "namespace": d["namespace"], "name": d["name"],
                                 "reason": f"{d['ready']}/{d['desired']} pods ready"})
        for j in self.jobs():
            if j["status"] == "Failed":
                problems.append({"kind": "Job", "namespace": j["namespace"], "name": j["name"],
                                 "reason": f"Job failed{': ' + j['failure_reason'] if j['failure_reason'] else ''}"})

        if any(p["kind"] == "Node" and p["reason"] == "NotReady" for p in problems):
            health = "critical"
        elif problems:
            health = "degraded"
        else:
            health = "healthy"

        phases: Dict[str, int] = {}
        for p in pods:
            phases[p["phase"] or "Unknown"] = phases.get(p["phase"] or "Unknown", 0) + 1

        cpu_used = [n["cpu_usage_m"] for n in nodes if n["cpu_usage_m"] is not None]
        mem_used = [n["memory_usage_bytes"] for n in nodes if n["memory_usage_bytes"] is not None]
        cpu_total = sum(n["cpu_allocatable_m"] for n in nodes)
        mem_total = sum(n["memory_allocatable_bytes"] for n in nodes)

        return {
            "cluster": cluster_name,
            "kubernetes_version": self.version(),
            "health": health,
            "problems": problems,
            "nodes": {"total": len(nodes), "ready": sum(n["ready"] for n in nodes)},
            "pods": {"total": len(pods), "by_phase": phases},
            "deployments": {
                "total": len(deployments),
                "ready": sum(1 for d in deployments if d["ready"] >= d["desired"]),
            },
            "namespaces": len({p["namespace"] for p in pods}),
            "metrics_available": bool(cpu_used),
            "cpu": {
                "allocatable_m": cpu_total,
                "usage_m": sum(cpu_used) if cpu_used else None,
                "percent": _percent(sum(cpu_used), cpu_total) if cpu_used else None,
            },
            "memory": {
                "allocatable_bytes": mem_total,
                "usage_bytes": sum(mem_used) if mem_used else None,
                "percent": _percent(sum(mem_used), mem_total) if mem_used else None,
            },
        }
