"""Permission catalog, built-in roles, and which permission each proxied agent call needs."""

import re
from typing import Dict, List, Optional, Tuple

# Cluster-scoped permissions can be granted on all clusters or on one cluster.
CLUSTER_PERMISSIONS: Dict[str, str] = {
    "cluster.view": "See the cluster: overview, nodes, workloads, pods, services, ConfigMaps, events, map, charts",
    "secrets.view": "See Secrets (names, types and key names; values are never shown)",
    "logs.view": "Read pod logs",
    "workloads.scale": "Scale Deployments and StatefulSets",
    "workloads.restart": "Restart workloads; suspend, resume and run CronJobs",
    "workloads.update_image": "Change a Deployment's container image",
    "workloads.rollback": "Roll back Deployments",
    "workloads.delete": "Delete Deployments and Jobs",
    "pods.exec": "Open a shell in a pod's container",
    "pods.delete": "Delete pods",
    "services.delete": "Delete Services",
    "config.delete": "Delete ConfigMaps and Secrets",
    "nodes.manage": "Cordon, uncordon and drain nodes",
    "resources.edit": "Edit an object's YAML in its details panel",
    "apply.yaml": "Create or update objects with Apply YAML",
    "ai.use": "Run AI investigations",
    "audit.view": "See the activity log",
}
# Global permissions only count when granted on all clusters.
HUB_PERMISSIONS: Dict[str, str] = {
    "clusters.manage": "Add, edit and remove clusters (their agent URLs and tokens)",
    "users.manage": "Manage users, roles and role assignments",
    "settings.manage": "Manage system settings (AI providers, alert channels)",
    "alerts.manage": "Create and edit alert rules",
}
# HAProxy permissions are global too: they count only when the role is assigned on all clusters.
HAPROXY_PERMISSIONS: Dict[str, str] = {
    "haproxy.view": "See HAProxy: frontends, backends, servers, live numbers, charts, topology, logs, certificates, "
                    "stick tables, the IP blocklist and config history (not the raw config)",
    "haproxy.operate": "Run HAProxy without changing its config: drain, maintenance and re-enable servers, rolling "
                       "deploys, block and unblock IPs, let rate-limited clients in again (instant, no reload)",
    "haproxy.configure": "Change HAProxy's config: frontends, routes, backends, servers, maintenance pages, "
                         "certificates, Kubernetes sync, copy and group sync, restore old versions; read and edit "
                         "the raw config (it can contain passwords)",
    "haproxy.manage": "Add, edit and remove HAProxy instances and their Data Plane API logins",
}
GLOBAL_PERMISSIONS: Dict[str, str] = {**HUB_PERMISSIONS, **HAPROXY_PERMISSIONS}
ALL_PERMISSIONS = {**CLUSTER_PERMISSIONS, **GLOBAL_PERMISSIONS}

# One set of roles per system, plus Admin for everything. Kubernetes roles are assigned on everything or one
# cluster; HAProxy roles on everything or one HAProxy instance.
K8S_OPERATOR = ["cluster.view", "logs.view", "secrets.view", "workloads.scale", "workloads.restart", "workloads.update_image",
                "workloads.rollback", "pods.delete", "resources.edit", "apply.yaml", "ai.use", "audit.view"]
BUILTIN_ROLES: List[Tuple[str, str, List[str]]] = [
    ("Admin", "Everything, including users, roles, clusters, HAProxy instances and settings", sorted(ALL_PERMISSIONS)),
    ("Kubernetes viewer", "Kubernetes, read-only: clusters, workloads, logs and activity",
     ["cluster.view", "logs.view", "audit.view"]),
    ("Kubernetes operator", "Kubernetes: view, read logs, run control actions (scale, restart, roll back…) and AI",
     K8S_OPERATOR),
    ("Kubernetes admin", "Kubernetes: every cluster permission, including deletes, shells, node drain and Apply YAML",
     sorted(CLUSTER_PERMISSIONS)),
    ("HAProxy viewer", "HAProxy, read-only: frontends, backends, servers, live numbers, logs and history",
     ["haproxy.view"]),
    ("HAProxy operator", "HAProxy without config changes: drain and maintenance, rolling deploys, block IPs, AI",
     ["haproxy.view", "haproxy.operate", "ai.use"]),
    ("HAProxy admin", "HAProxy, everything: change the config, manage the connection, AI",
     ["haproxy.view", "haproxy.operate", "haproxy.configure", "haproxy.manage", "ai.use"]),
]
# Built-in roles of older versions, now split per system: old name -> (new name, HAProxy role its users also get).
SPLIT_ROLES: Dict[str, Tuple[str, str]] = {"Operator": ("Kubernetes operator", "HAProxy operator"),
                                           "Viewer": ("Kubernetes viewer", "HAProxy viewer")}
# What each built-in role had before HAProxy existed: what an installation from before 2.2 already has.
# Anything in a definition beyond what an installation has seen is added once on upgrade (see app.init).
BUILTIN_BASELINE: Dict[str, List[str]] = {name: [p for p in perms if not p.startswith("haproxy.")] for name, _, perms in BUILTIN_ROLES}

# (method, path regex relative to /api/v1/, permission). First match wins.
_RULES: List[Tuple[str, re.Pattern, str]] = [(m, re.compile(p), perm) for m, p, perm in [
    ("GET", r"^secrets(/|$)", "secrets.view"),
    ("GET", r"^pods/[^/]+/[^/]+/logs(/stream)?$", "logs.view"),
    ("GET", r"^(deployments|statefulsets|daemonsets|jobs)/[^/]+/[^/]+/logs/stream$", "logs.view"),
    ("GET", r"^resource$", "cluster.view"),           # Secret objects re-checked in the hub
    ("GET", r"^audit$", "audit.view"),
    ("GET", r"^ai(/|$)", "__deny__"),                 # AI runs in the hub
    ("GET", r".*", "cluster.view"),
    ("POST", r"^(deployments|statefulsets)/[^/]+/[^/]+/scale$", "workloads.scale"),
    ("POST", r"^(deployments|statefulsets|daemonsets)/[^/]+/[^/]+/restart$", "workloads.restart"),
    ("POST", r"^deployments/[^/]+/[^/]+/image$", "workloads.update_image"),
    ("POST", r"^deployments/[^/]+/[^/]+/rollback$", "workloads.rollback"),
    ("POST", r"^cronjobs/[^/]+/[^/]+/(suspend|resume|trigger)$", "workloads.restart"),
    ("POST", r"^nodes/[^/]+/(cordon|uncordon|drain)$", "nodes.manage"),
    ("POST", r"^apply$", "apply.yaml"),
    ("PUT", r"^resource$", "resources.edit"),
    ("DELETE", r"^(deployments|jobs)/[^/]+/[^/]+$", "workloads.delete"),
    ("DELETE", r"^pods/[^/]+/[^/]+$", "pods.delete"),
    ("DELETE", r"^services/[^/]+/[^/]+$", "services.delete"),
    ("DELETE", r"^(configmaps|secrets)/[^/]+/[^/]+$", "config.delete"),
]]


def required_permission(method: str, path: str) -> Optional[str]:
    """Permission needed to proxy `method path` to an agent; None means not allowed at all."""
    for m, pattern, perm in _RULES:
        if m == method.upper() and pattern.search(path):
            return None if perm == "__deny__" else perm
    return None
