"""Detail view of any supported object: live spec/status, YAML, events and related objects
(the data behind the dashboard's resource panel, similar to Kubernetes Dashboard)."""

from typing import Any, Dict, List, Optional

import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException

from .collectors import Collector, event_info, pod_info, pod_problem
from .topology import config_refs

# kind -> (api attribute on Collector, read method, namespaced, apiVersion)
KINDS: Dict[str, tuple] = {
    "Pod": ("core", "read_namespaced_pod", True, "v1"),
    "Service": ("core", "read_namespaced_service", True, "v1"),
    "ConfigMap": ("core", "read_namespaced_config_map", True, "v1"),
    "PersistentVolumeClaim": ("core", "read_namespaced_persistent_volume_claim", True, "v1"),
    "Node": ("core", "read_node", False, "v1"),
    "Namespace": ("core", "read_namespace", False, "v1"),
    "Deployment": ("apps", "read_namespaced_deployment", True, "apps/v1"),
    "StatefulSet": ("apps", "read_namespaced_stateful_set", True, "apps/v1"),
    "DaemonSet": ("apps", "read_namespaced_daemon_set", True, "apps/v1"),
    "ReplicaSet": ("apps", "read_namespaced_replica_set", True, "apps/v1"),
    "Job": ("batch", "read_namespaced_job", True, "batch/v1"),
    "Ingress": ("networking", "read_namespaced_ingress", True, "networking.k8s.io/v1"),
    "CronJob": ("batch", "read_namespaced_cron_job", True, "batch/v1"),
    "PersistentVolume": ("core", "read_persistent_volume", False, "v1"),
    "HorizontalPodAutoscaler": ("autoscaling", "read_namespaced_horizontal_pod_autoscaler", True, "autoscaling/v2"),
    "Secret": (None, None, True, "v1"),  # listed by name; values are never returned
}


class NotSupported(Exception):
    pass


def _selector(labels: Optional[Dict[str, str]]) -> Optional[str]:
    return ",".join(f"{k}={v}" for k, v in labels.items()) if labels else None


def _ref(kind: str, namespace: Optional[str], name: str, relation: str, status: Optional[str] = None) -> Dict[str, Any]:
    return {"kind": kind, "namespace": namespace, "name": name, "relation": relation, "status": status}


class ResourceViewer:
    def __init__(self, collector: Collector):
        self.c = collector
        self.san = collector.core.api_client.sanitize_for_serialization

    # --- object -------------------------------------------------------------

    def _read(self, kind: str, namespace: Optional[str], name: str) -> Any:
        if kind == "Secret":
            items = self.c.core.list_namespaced_secret(namespace, field_selector=f"metadata.name={name}").items
            if not items:
                raise ApiException(status=404, reason="Not Found")
            return items[0]
        api_attr, method, namespaced, _ = KINDS[kind]
        fn = getattr(getattr(self.c, api_attr), method)
        return fn(name, namespace) if namespaced else fn(name)

    def _plain(self, kind: str, obj: Any) -> Dict[str, Any]:
        d = self.san(obj)
        d["apiVersion"] = d.get("apiVersion") or KINDS[kind][3]
        d["kind"] = d.get("kind") or kind
        meta = d.get("metadata", {})
        meta.pop("managedFields", None)
        if kind == "Secret":
            d["data"] = {k: f"<hidden: {len(v) * 3 // 4} bytes>" for k, v in (d.get("data") or {}).items()}
            d.pop("stringData", None)
            (meta.get("annotations") or {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
        return d

    # --- events -------------------------------------------------------------

    def _events(self, kind: str, namespace: Optional[str], name: str) -> List[Dict[str, Any]]:
        sel = f"involvedObject.name={name},involvedObject.kind={kind}"
        items = (self.c.core.list_namespaced_event(namespace, field_selector=sel).items if namespace
                 else self.c.core.list_event_for_all_namespaces(field_selector=sel).items)
        events = [event_info(e) for e in items]
        events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
        return events[:100]

    # --- related ------------------------------------------------------------

    def _pods(self, namespace: Optional[str], label_selector: Optional[str] = None,
              field_selector: Optional[str] = None) -> List[Dict[str, Any]]:
        if namespace:
            items = self.c.core.list_namespaced_pod(namespace, label_selector=label_selector, field_selector=field_selector).items
        else:
            items = self.c.core.list_pod_for_all_namespaces(label_selector=label_selector, field_selector=field_selector).items
        out = []
        for p in items:
            info = pod_info(p)
            info["problem"] = pod_problem(info)
            out.append({k: info[k] for k in ("namespace", "name", "status", "phase", "ready_containers", "total_containers",
                                             "restarts", "node", "pod_ip", "age_s", "problem")})
        return out

    def _services_selecting(self, namespace: str, labels: Dict[str, str]) -> List[Dict[str, Any]]:
        out = []
        for s in self.c.core.list_namespaced_service(namespace).items:
            sel = s.spec.selector or {}
            if sel and all((labels or {}).get(k) == v for k, v in sel.items()):
                out.append(_ref("Service", namespace, s.metadata.name, "Selected by service", s.spec.type))
        return out

    def _owner_chain(self, namespace: str, owners: List[Any]) -> List[Dict[str, Any]]:
        out = []
        for o in owners or []:
            out.append(_ref(o.kind, namespace, o.name, "Owned by"))
            if o.kind == "ReplicaSet":
                try:
                    rs = self.c.apps.read_namespaced_replica_set(o.name, namespace)
                    for oo in rs.metadata.owner_references or []:
                        out.append(_ref(oo.kind, namespace, oo.name, "Owned by"))
                except ApiException:
                    pass
        return out

    def _related(self, kind: str, obj: Any) -> Dict[str, Any]:
        ns = getattr(obj.metadata, "namespace", None)
        related: List[Dict[str, Any]] = []
        pods: Optional[List[Dict[str, Any]]] = None

        if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"):
            labels = obj.spec.selector.match_labels if obj.spec.selector else None
            pods = self._pods(ns, label_selector=_selector(labels)) if labels else []
            related += self._owner_chain(ns, obj.metadata.owner_references)
            if kind == "Deployment":
                for rs in self.c.apps.list_namespaced_replica_set(ns).items:
                    if any(o.uid == obj.metadata.uid for o in rs.metadata.owner_references or []):
                        rev = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
                        related.append(_ref("ReplicaSet", ns, rs.metadata.name, "ReplicaSets",
                                            f"rev {rev} · {rs.status.replicas or 0} pods"))
            tmpl_labels = obj.spec.template.metadata.labels if obj.spec.template and obj.spec.template.metadata else {}
            related += self._services_selecting(ns, tmpl_labels or {})
            for k, name, how, _ in sorted(config_refs(obj.spec.template.spec)):
                related.append(_ref(k, ns, name, "Uses", how))
        elif kind == "Pod":
            related += self._owner_chain(ns, obj.metadata.owner_references)
            if obj.spec.node_name:
                related.append(_ref("Node", None, obj.spec.node_name, "Runs on node"))
            related += self._services_selecting(ns, obj.metadata.labels or {})
            for k, name, how, _ in sorted(config_refs(obj.spec)):
                related.append(_ref(k, ns, name, "Uses", how))
        elif kind == "Service":
            sel = obj.spec.selector
            pods = self._pods(ns, label_selector=_selector(sel)) if sel else []
            for ing in self.c.networking.list_namespaced_ingress(ns).items:
                backends = {p.backend.service.name for r in ing.spec.rules or [] for p in (r.http.paths if r.http else []) or []
                            if p.backend and p.backend.service}
                if ing.spec.default_backend and ing.spec.default_backend.service:
                    backends.add(ing.spec.default_backend.service.name)
                if obj.metadata.name in backends:
                    related.append(_ref("Ingress", ns, ing.metadata.name, "Routed from ingress"))
        elif kind == "Node":
            pods = self._pods(None, field_selector=f"spec.nodeName={obj.metadata.name}")
        elif kind in ("ConfigMap", "Secret", "PersistentVolumeClaim"):
            if kind == "PersistentVolumeClaim" and obj.spec.volume_name:
                related.append(_ref("PersistentVolume", None, obj.spec.volume_name, "Bound to"))
            users = []
            for p in self.c.core.list_namespaced_pod(ns).items:
                if any(k == kind and n == obj.metadata.name for k, n, _, _ in config_refs(p.spec)):
                    users.append(p)
            pods = [self._pods(ns, field_selector=f"metadata.name={p.metadata.name}")[0] for p in users][:50]
            seen = set()
            for p in users:
                for r in self._owner_chain(ns, p.metadata.owner_references):
                    if r["kind"] != "ReplicaSet" and (r["kind"], r["name"]) not in seen:
                        seen.add((r["kind"], r["name"]))
                        related.append({**r, "relation": "Used by"})
        elif kind == "Ingress":
            for r in obj.spec.rules or []:
                for p in (r.http.paths if r.http else []) or []:
                    if p.backend and p.backend.service:
                        related.append(_ref("Service", ns, p.backend.service.name, "Routes to",
                                            f"{r.host or '*'}{p.path or '/'}"))
        elif kind == "CronJob":
            jobs = [j for j in self.c.batch.list_namespaced_job(ns).items
                    if any(o.uid == obj.metadata.uid for o in j.metadata.owner_references or [])]
            jobs.sort(key=lambda j: j.metadata.creation_timestamp, reverse=True)
            from .collectors import job_info
            for j in jobs[:10]:
                related.append(_ref("Job", ns, j.metadata.name, "Recent jobs", job_info(j)["status"]))
            for k, name, how, _ in sorted(config_refs(obj.spec.job_template.spec.template.spec)):
                related.append(_ref(k, ns, name, "Uses", how))
        elif kind == "PersistentVolume":
            if obj.spec.claim_ref:
                related.append(_ref("PersistentVolumeClaim", obj.spec.claim_ref.namespace, obj.spec.claim_ref.name, "Bound to"))
        elif kind == "HorizontalPodAutoscaler":
            ref = obj.spec.scale_target_ref
            related.append(_ref(ref.kind, ns, ref.name, "Scales"))
        elif kind == "Namespace":
            name = obj.metadata.name
            related.append(_ref("Namespace", None, name, "Contains",
                                f"{len(self.c.core.list_namespaced_pod(name).items)} pods, "
                                f"{len(self.c.apps.list_namespaced_deployment(name).items)} deployments, "
                                f"{len(self.c.core.list_namespaced_service(name).items)} services"))
        # de-duplicate, keep order
        seen_keys = set()
        unique = []
        for r in related:
            key = (r["relation"], r["kind"], r["namespace"], r["name"])
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(r)
        return {"related": unique, "pods": pods}

    # --- public -------------------------------------------------------------

    def get(self, kind: str, namespace: Optional[str], name: str) -> Dict[str, Any]:
        if kind not in KINDS:
            raise NotSupported(f"unsupported kind {kind!r}; supported: {', '.join(sorted(KINDS))}")
        namespaced = KINDS[kind][2]
        if namespaced and not namespace:
            raise NotSupported(f"{kind} needs a namespace")
        ns = namespace if namespaced else None
        obj = self._read(kind, ns, name)
        plain = self._plain(kind, obj)
        try:
            rel = self._related(kind, obj)
        except ApiException as e:  # partial RBAC: still show the object
            rel = {"related": [], "pods": None, "related_error": f"{e.status} {e.reason}"}
        return {
            "kind": kind,
            "namespace": ns,
            "name": name,
            "object": plain,
            "yaml": yaml.safe_dump(plain, sort_keys=False, default_flow_style=False, width=120),
            "events": self._events(kind, ns, name),
            **rel,
        }
