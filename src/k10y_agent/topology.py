"""Relationship graph of a namespace: ingress -> service -> workload -> (replicaset) -> pod -> config.

Edges (all point "downstream"):
  routes      Ingress -> Service              (ingress backend)
  selects     Service -> Pod                  (label selector match)
  selects-workload Service -> Workload        (aggregate of `selects`, for drawing)
  owns        Workload -> Pod                 (collapsed ownership, ReplicaSet hidden)
  rs-owns     Deployment -> ReplicaSet
  rs-pod      ReplicaSet -> Pod
  uses        Pod/Workload -> ConfigMap/Secret/PersistentVolumeClaim
"""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from kubernetes import client

from .collectors import pod_info, pod_problem

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job")
# Injected into every pod by the service-account token volume; pure noise on a map.
IGNORED_CONFIGMAPS = {"kube-root-ca.crt"}


def node_id(kind: str, namespace: Optional[str], name: str) -> str:
    return f"{kind}/{namespace or ''}/{name}"


def config_refs(spec: client.V1PodSpec) -> Set[Tuple[str, str, str, bool]]:
    """(kind, name, how, optional) for every ConfigMap/Secret/PVC a pod spec references."""
    refs: Set[Tuple[str, str, str, bool]] = set()
    for v in spec.volumes or []:
        if v.config_map and v.config_map.name not in IGNORED_CONFIGMAPS:
            refs.add(("ConfigMap", v.config_map.name, "volume", bool(v.config_map.optional)))
        if v.secret and v.secret.secret_name:
            refs.add(("Secret", v.secret.secret_name, "volume", bool(v.secret.optional)))
        if v.persistent_volume_claim:
            refs.add(("PersistentVolumeClaim", v.persistent_volume_claim.claim_name, "volume", False))
        if v.projected:
            for src in v.projected.sources or []:
                if src.config_map and src.config_map.name not in IGNORED_CONFIGMAPS:
                    refs.add(("ConfigMap", src.config_map.name, "volume", bool(src.config_map.optional)))
                if src.secret and src.secret.name:
                    refs.add(("Secret", src.secret.name, "volume", bool(src.secret.optional)))
    for c in (spec.containers or []) + (spec.init_containers or []):
        for e in c.env or []:
            vf = e.value_from
            if vf and vf.config_map_key_ref and vf.config_map_key_ref.name:
                refs.add(("ConfigMap", vf.config_map_key_ref.name, "env", bool(vf.config_map_key_ref.optional)))
            if vf and vf.secret_key_ref and vf.secret_key_ref.name:
                refs.add(("Secret", vf.secret_key_ref.name, "env", bool(vf.secret_key_ref.optional)))
        for ef in c.env_from or []:
            if ef.config_map_ref and ef.config_map_ref.name:
                refs.add(("ConfigMap", ef.config_map_ref.name, "envFrom", bool(ef.config_map_ref.optional)))
            if ef.secret_ref and ef.secret_ref.name:
                refs.add(("Secret", ef.secret_ref.name, "envFrom", bool(ef.secret_ref.optional)))
    for s in spec.image_pull_secrets or []:
        if s.name:
            refs.add(("Secret", s.name, "imagePullSecret", False))
    return refs


def _selects(selector: Dict[str, str], labels: Optional[Dict[str, str]]) -> bool:
    labels = labels or {}
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def _ingress_backends(ing: Any) -> Iterable[Tuple[str, str]]:
    """(service name, path) pairs of an networking.k8s.io/v1 Ingress."""
    spec = ing.spec
    if spec.default_backend and spec.default_backend.service:
        yield spec.default_backend.service.name, "(default)"
    for rule in spec.rules or []:
        for p in (rule.http.paths if rule.http else []) or []:
            if p.backend and p.backend.service:
                yield p.backend.service.name, f"{rule.host or '*'}{p.path or '/'}"


def build_topology(
    *,
    pods: List[client.V1Pod],
    deployments: List[client.V1Deployment],
    replicasets: List[client.V1ReplicaSet],
    statefulsets: List[client.V1StatefulSet],
    daemonsets: List[client.V1DaemonSet],
    services: List[client.V1Service],
    configmaps: List[client.V1ConfigMap],
    pvcs: List[client.V1PersistentVolumeClaim],
    ingresses: List[Any],
    jobs: Optional[List[Any]] = None,
    secrets: Optional[List[client.V1Secret]] = None,
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def add_node(kind: str, ns: Optional[str], name: str, detail: str = "", status: str = "neutral",
                 problem: Optional[str] = None, **extra: Any) -> str:
        nid = node_id(kind, ns, name)
        nodes[nid] = {"id": nid, "kind": kind, "namespace": ns, "name": name, "detail": detail,
                      "status": status, "problem": problem, **extra}
        return nid

    def add_edge(src: str, dst: str, etype: str, label: str = "") -> None:
        key = (src, dst, etype)
        if key in edges:
            if label and label not in edges[key]["labels"]:
                edges[key]["labels"].append(label)
        else:
            edges[key] = {"from": src, "to": dst, "type": etype, "labels": [label] if label else []}

    # --- workloads --------------------------------------------------------
    uid_to_node: Dict[str, str] = {}
    templates: Dict[str, client.V1PodSpec] = {}
    for d in deployments:
        desired, ready = d.spec.replicas or 0, d.status.ready_replicas or 0
        nid = add_node("Deployment", d.metadata.namespace, d.metadata.name, f"{ready}/{desired} ready",
                       "good" if ready >= desired else "warning",
                       None if ready >= desired else f"{ready}/{desired} replicas ready")
        uid_to_node[d.metadata.uid] = nid
        templates[nid] = d.spec.template.spec
    for s in statefulsets:
        desired, ready = s.spec.replicas or 0, s.status.ready_replicas or 0
        nid = add_node("StatefulSet", s.metadata.namespace, s.metadata.name, f"{ready}/{desired} ready",
                       "good" if ready >= desired else "warning",
                       None if ready >= desired else f"{ready}/{desired} replicas ready")
        uid_to_node[s.metadata.uid] = nid
        templates[nid] = s.spec.template.spec
    for ds in daemonsets:
        desired, ready = ds.status.desired_number_scheduled or 0, ds.status.number_ready or 0
        nid = add_node("DaemonSet", ds.metadata.namespace, ds.metadata.name, f"{ready}/{desired} ready",
                       "good" if ready >= desired else "warning",
                       None if ready >= desired else f"{ready}/{desired} pods ready")
        uid_to_node[ds.metadata.uid] = nid
        templates[nid] = ds.spec.template.spec

    for j in jobs or []:
        st = j.status
        if st.succeeded:
            detail, status, problem = "Complete", "neutral", None
        elif st.failed and not st.active:
            detail, status, problem = "Failed", "critical", "job failed"
        else:
            detail, status, problem = f"{st.active or 0} active", "warning" if st.failed else "neutral", None
        nid = add_node("Job", j.metadata.namespace, j.metadata.name, detail, status, problem)
        uid_to_node[j.metadata.uid] = nid
        templates[nid] = j.spec.template.spec

    rs_owner: Dict[str, str] = {}
    for rs in replicasets:
        owner = next((uid_to_node[o.uid] for o in rs.metadata.owner_references or [] if o.uid in uid_to_node), None)
        replicas = rs.status.replicas or 0
        if owner is None or replicas == 0:
            continue  # old revisions scaled to zero only clutter the map
        rev = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
        nid = add_node("ReplicaSet", rs.metadata.namespace, rs.metadata.name,
                       f"rev {rev} · {rs.status.ready_replicas or 0}/{replicas} ready" if rev else f"{replicas} replicas",
                       "neutral", None, revision=rev)
        uid_to_node[rs.metadata.uid] = nid
        rs_owner[nid] = owner
        add_edge(owner, nid, "rs-owns")

    # --- pods ---------------------------------------------------------------
    pod_nodes: List[Tuple[str, client.V1Pod]] = []
    workload_has_pods: Set[str] = set()
    for p in pods:
        info = pod_info(p)
        problem = pod_problem(info)
        status = "critical" if problem else "neutral" if p.status.phase == "Succeeded" else (
            "good" if p.status.phase == "Running" and info["ready_containers"] == info["total_containers"] else "warning")
        label = "Completed" if p.status.phase == "Succeeded" else info["status"]
        owner_ref = next((o for o in p.metadata.owner_references or [] if o.controller), None)
        owner = uid_to_node.get(owner_ref.uid) if owner_ref else None
        workload = rs_owner.get(owner, owner) if owner else None
        nid = add_node("Pod", p.metadata.namespace, p.metadata.name,
                       f"{label} · {info['ready_containers']}/{info['total_containers']}", status, problem,
                       owner=workload, phase=p.status.phase, node=p.spec.node_name)
        pod_nodes.append((nid, p))
        if owner:
            if owner in rs_owner:
                add_edge(owner, nid, "rs-pod")
            add_edge(workload, nid, "owns")
            workload_has_pods.add(workload)

    # --- config & storage ---------------------------------------------------
    cm_names = {(c.metadata.namespace, c.metadata.name) for c in configmaps}
    secret_names = {(s.metadata.namespace, s.metadata.name) for s in secrets} if secrets is not None else None
    pvc_by_name = {(c.metadata.namespace, c.metadata.name): c for c in pvcs}

    def add_config(kind: str, ns: str, name: str, optional: bool) -> str:
        nid = node_id(kind, ns, name)
        if nid in nodes:
            return nid
        if kind == "ConfigMap":
            exists = (ns, name) in cm_names
            return add_node(kind, ns, name, "ConfigMap" if exists else "not found",
                            "neutral" if exists or optional else "critical",
                            None if exists or optional else "referenced but does not exist", missing=not exists)
        if kind == "PersistentVolumeClaim":
            pvc = pvc_by_name.get((ns, name))
            if pvc is None:
                return add_node(kind, ns, name, "not found", "critical", "referenced but does not exist", missing=True)
            phase = pvc.status.phase if pvc.status else "Unknown"
            size = ((pvc.status.capacity or {}) if pvc.status else {}).get("storage") or \
                ((pvc.spec.resources.requests or {}) if pvc.spec.resources else {}).get("storage", "")
            return add_node(kind, ns, name, f"{phase}{f' · {size}' if size else ''}",
                            "neutral" if phase == "Bound" else "warning",
                            None if phase == "Bound" else f"claim is {phase}")
        if secret_names is None:  # existence unknown
            return add_node(kind, ns, name, "Secret", "neutral")
        exists = (ns, name) in secret_names
        return add_node(kind, ns, name, "Secret" if exists else "not found",
                        "neutral" if exists or optional else "critical",
                        None if exists or optional else "referenced but does not exist", missing=not exists)

    for nid, p in pod_nodes:
        for kind, name, how, optional in sorted(config_refs(p.spec)):
            add_edge(nid, add_config(kind, p.metadata.namespace, name, optional), "uses", how)
    # Workloads without pods (e.g. scaled to 0) still show what their template references.
    for wid, spec in templates.items():
        if wid in workload_has_pods:
            continue
        ns = nodes[wid]["namespace"]
        for kind, name, how, optional in sorted(config_refs(spec)):
            add_edge(wid, add_config(kind, ns, name, optional), "uses", how)

    # --- services -----------------------------------------------------------
    for svc in services:
        spec = svc.spec
        selector = spec.selector or {}
        np = [str(p.node_port) for p in spec.ports or [] if p.node_port]
        detail = spec.type + (f" · :{', :'.join(np)}" if np else "")
        sid = add_node("Service", svc.metadata.namespace, svc.metadata.name, detail, "neutral", None,
                       type=spec.type, node_ports=[int(x) for x in np])
        matched = 0
        for pid, p in pod_nodes:
            if p.metadata.namespace == svc.metadata.namespace and _selects(selector, p.metadata.labels):
                matched += 1
                add_edge(sid, pid, "selects")
                owner = nodes[pid].get("owner")
                if owner:
                    add_edge(sid, owner, "selects-workload")
        if selector and matched == 0:
            nodes[sid]["status"] = "warning"
            nodes[sid]["problem"] = "selector matches no pods"
        nodes[sid]["pods"] = matched

    # --- ingresses ----------------------------------------------------------
    for ing in ingresses:
        iid = add_node("Ingress", ing.metadata.namespace, ing.metadata.name,
                       ", ".join(sorted({r.host for r in ing.spec.rules or [] if r.host})) or "Ingress", "neutral")
        for svc_name, path in _ingress_backends(ing):
            sid = node_id("Service", ing.metadata.namespace, svc_name)
            if sid not in nodes:
                add_node("Service", ing.metadata.namespace, svc_name, "not found", "critical",
                         "referenced by an ingress but does not exist", missing=True)
            add_edge(iid, sid, "routes", path)

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
