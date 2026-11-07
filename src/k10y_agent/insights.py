"""Deeper, read-only Kubernetes views the agent computes on request.

  * Helm releases, read from Helm's own release records (Secrets labelled owner=helm). Values are returned with
    anything that looks like a password, token or key masked; rolling back is left to the helm CLI.
  * TLS certificate expiry: the certificates of Ingress TLS secrets, and cert-manager Certificates.
  * CronJob run history and health.
  * ResourceQuotas and LimitRanges per namespace.
  * Rollout revision details (pod template and change cause) for comparing Deployment revisions.
  * One search over every kind of object in the cluster.
"""

import base64
import gzip
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import yaml
from kubernetes.client.rest import ApiException

MASK = "••••••"
SENSITIVE = re.compile(r"pass|secret|token|credential|private|apikey|api_key|auth|cert|(^|[_.-])key($|[_.-])", re.I)
CERT_WARN_DAYS = 14
CERT_CACHE_S = 300
_cert_cache: Dict[str, Any] = {"t": 0.0, "items": []}
_cert_lock = threading.Lock()


def _iso(ts: Any) -> Optional[str]:
    if not ts:
        return None
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def mask(value: Any, key: str = "") -> Any:
    """Mask values whose key looks secret (password, token, apiKey, …), recursively."""
    if isinstance(value, dict):
        return {k: mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    if key and SENSITIVE.search(key) and value not in (None, "", True, False) and not isinstance(value, (int, float)):
        return MASK
    return value


# --- Helm ------------------------------------------------------------------------------------

def _decode_release(secret: Any) -> Dict[str, Any]:
    """Helm 3 release record: Secret data["release"] = base64(gzip(json)) inside the Secret's own base64."""
    raw = base64.b64decode(base64.b64decode((secret.data or {}).get("release", "")))
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _release_view(rel: Dict[str, Any]) -> Dict[str, Any]:
    meta, info = (rel.get("chart") or {}).get("metadata") or {}, rel.get("info") or {}
    return {"name": rel.get("name"), "namespace": rel.get("namespace"), "revision": rel.get("version"),
            "status": info.get("status"), "chart": meta.get("name"), "chart_version": meta.get("version"),
            "app_version": meta.get("appVersion"), "updated": info.get("last_deployed"),
            "description": info.get("description") or ""}


def _helm_secrets(c: Any, namespace: Optional[str], name: Optional[str] = None) -> List[Any]:
    selector = "owner=helm" + (f",name={name}" if name else "")
    try:
        if namespace:
            return c.core.list_namespaced_secret(namespace, label_selector=selector).items
        return c.core.list_secret_for_all_namespaces(label_selector=selector).items
    except ApiException as e:
        if e.status == 403:
            return []
        raise


def helm_releases(c: Any, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every Helm release with its latest revision (chart, version, status)."""
    latest: Dict[Tuple[str, str], Any] = {}
    counts: Dict[Tuple[str, str], int] = {}
    for s in _helm_secrets(c, namespace):
        labels = s.metadata.labels or {}
        key = (s.metadata.namespace, labels.get("name", ""))
        counts[key] = counts.get(key, 0) + 1
        if key not in latest or int(labels.get("version", 0)) > int((latest[key].metadata.labels or {}).get("version", 0)):
            latest[key] = s
    out = []
    for key, s in latest.items():
        try:
            view = _release_view(_decode_release(s))
        except Exception:      # an unreadable record still shows up, from its labels
            labels = s.metadata.labels or {}
            view = {"name": labels.get("name"), "namespace": s.metadata.namespace, "revision": int(labels.get("version", 0)),
                    "status": labels.get("status"), "chart": None, "chart_version": None, "app_version": None,
                    "updated": _iso(s.metadata.creation_timestamp), "description": "release record could not be read"}
        out.append({**view, "revisions": counts[key]})
    return sorted(out, key=lambda r: (r["namespace"] or "", r["name"] or ""))


def helm_history(c: Any, namespace: str, name: str) -> List[Dict[str, Any]]:
    revs = []
    for s in _helm_secrets(c, namespace, name):
        try:
            revs.append(_release_view(_decode_release(s)))
        except Exception:
            continue
    if not revs:
        raise LookupError(f"Helm release {namespace}/{name} not found")
    return sorted(revs, key=lambda r: r["revision"] or 0, reverse=True)


def _mask_manifest(manifest: str) -> str:
    """Hide the data of any Secret in a rendered manifest."""
    docs = []
    for doc in re.split(r"(?m)^---\s*$", manifest or ""):
        try:
            obj = yaml.safe_load(doc)
        except yaml.YAMLError:
            docs.append(doc)
            continue
        if isinstance(obj, dict) and obj.get("kind") == "Secret":
            for field in ("data", "stringData"):
                if isinstance(obj.get(field), dict):
                    obj[field] = {k: MASK for k in obj[field]}
            docs.append("\n" + yaml.safe_dump(obj, sort_keys=False, allow_unicode=True))
        else:
            docs.append(doc)
    return "---".join(docs)


def helm_revision(c: Any, namespace: str, name: str, revision: int) -> Dict[str, Any]:
    """One revision's user values (secrets masked) and rendered manifest (Secret data masked)."""
    for s in _helm_secrets(c, namespace, name):
        if int((s.metadata.labels or {}).get("version", -1)) == revision:
            rel = _decode_release(s)
            return {**_release_view(rel), "values": yaml.safe_dump(mask(rel.get("config") or {}), sort_keys=False, allow_unicode=True),
                    "manifest": _mask_manifest(rel.get("manifest") or ""),
                    "notes": ((rel.get("info") or {}).get("notes") or "")[:4000]}
    raise LookupError(f"revision {revision} of {namespace}/{name} not found")


# --- TLS certificates -------------------------------------------------------------------------

def _cert_view(pem: bytes) -> Optional[Dict[str, Any]]:
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(pem)
    except ValueError:
        return None
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    issuer = ", ".join(a.value for a in cert.issuer if a.oid._name in ("commonName", "organizationName")) or cert.issuer.rfc4514_string()
    return {"not_after": not_after.isoformat(), "days_left": int((not_after - datetime.now(timezone.utc)).total_seconds() // 86400),
            "dns_names": names, "issuer": issuer}


def certificates(c: Any, fresh: bool = False) -> List[Dict[str, Any]]:
    """TLS certificates in use: Ingress TLS secrets, plus cert-manager Certificates. Cached for 5 minutes."""
    with _cert_lock:
        if not fresh and time.time() - _cert_cache["t"] < CERT_CACHE_S:
            return _cert_cache["items"]
    items: List[Dict[str, Any]] = []
    try:
        ingresses = c.networking.list_ingress_for_all_namespaces().items
    except ApiException:
        ingresses = []
    seen = set()
    for ing in ingresses:
        for tls in ing.spec.tls or []:
            if not tls.secret_name or (ing.metadata.namespace, tls.secret_name) in seen:
                continue
            seen.add((ing.metadata.namespace, tls.secret_name))
            entry = {"source": "Ingress", "namespace": ing.metadata.namespace, "name": tls.secret_name,
                     "ingress": ing.metadata.name, "hosts": tls.hosts or [], "ready": None}
            try:
                found = c.core.list_namespaced_secret(ing.metadata.namespace, field_selector=f"metadata.name={tls.secret_name}").items
                crt = (found[0].data or {}).get("tls.crt") if found else None
                view = _cert_view(base64.b64decode(crt)) if crt else None
                entry.update(view or {"error": "secret not found" if not found else "no certificate in the secret"})
            except ApiException as e:
                entry["error"] = f"cannot read the secret ({e.status})"
            items.append(entry)
    try:
        for cm in c.custom.list_cluster_custom_object("cert-manager.io", "v1", "certificates").get("items", []):
            st, sp, md = cm.get("status") or {}, cm.get("spec") or {}, cm.get("metadata") or {}
            ready = next((x.get("status") == "True" for x in st.get("conditions") or [] if x.get("type") == "Ready"), None)
            entry = {"source": "cert-manager", "namespace": md.get("namespace"), "name": md.get("name"), "secret": sp.get("secretName"),
                     "hosts": sp.get("dnsNames") or [], "issuer": (sp.get("issuerRef") or {}).get("name"), "ready": ready}
            if st.get("notAfter"):
                na = datetime.fromisoformat(st["notAfter"].replace("Z", "+00:00"))
                entry.update({"not_after": na.isoformat(), "days_left": int((na - datetime.now(timezone.utc)).total_seconds() // 86400)})
            items.append(entry)
    except ApiException:
        pass     # cert-manager not installed, or no permission
    items.sort(key=lambda x: (x.get("days_left") if x.get("days_left") is not None else 10**6))
    with _cert_lock:
        _cert_cache.update({"t": time.time(), "items": items})
    return items


def certificate_problems(c: Any) -> List[Dict[str, Any]]:
    out = []
    for x in certificates(c):
        days = x.get("days_left")
        what = ", ".join(x.get("hosts") or []) or x["name"]
        if days is not None and days < 0:
            reason = f"TLS certificate expired {-days} days ago ({what})"
        elif days is not None and days < CERT_WARN_DAYS:
            reason = f"TLS certificate expires in {days} days ({what})"
        elif x.get("ready") is False:
            reason = f"cert-manager certificate not ready ({what})"
        else:
            continue
        out.append({"kind": "Certificate", "namespace": x["namespace"], "name": x["name"], "reason": reason})
    return out


# --- volume usage ------------------------------------------------------------------------------
# How full each PVC is, from every node's kubelet (/stats/summary). The agent calls the kubelet directly with its
# ServiceAccount token, which needs only the read-only nodes/stats permission (not nodes/proxy), and checks the
# kubelet's certificate against the cluster CA unless K10Y_KUBELET_INSECURE=true.

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
VOLUME_CACHE_S = 60
_vol_cache: Dict[str, Any] = {"t": 0.0, "data": None}
_vol_lock = threading.Lock()


def volume_full_percent() -> float:
    import os
    try:
        return float(os.environ.get("K10Y_VOLUME_FULL_PERCENT", "90"))
    except ValueError:
        return 90.0


def _kubelet_address(node: Any) -> Optional[str]:
    addrs = {a.type: a.address for a in (node.status.addresses or [])} if node.status else {}
    ip = addrs.get("InternalIP") or addrs.get("ExternalIP") or addrs.get("Hostname")
    if not ip:
        return None
    port = getattr(getattr(getattr(node.status, "daemon_endpoints", None), "kubelet_endpoint", None), "port", None) or 10250
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def parse_stats_summary(summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """PVC volumes of a kubelet /stats/summary -> {"namespace/claim": usage}."""
    out: Dict[str, Dict[str, Any]] = {}
    for pod in summary.get("pods") or []:
        for v in pod.get("volume") or []:
            ref = v.get("pvcRef")
            cap, used = v.get("capacityBytes"), v.get("usedBytes")
            if not ref or not cap:
                continue
            inodes, inodes_used = v.get("inodes"), v.get("inodesUsed")
            out[f"{ref.get('namespace')}/{ref.get('name')}"] = {
                "used_bytes": used, "capacity_bytes": cap, "available_bytes": v.get("availableBytes"),
                "used_percent": round(100 * (used or 0) / cap, 1),
                "inodes_percent": round(100 * inodes_used / inodes, 1) if inodes and inodes_used is not None else None,
                "pod": pod.get("podRef", {}).get("name"), "node": summary.get("node", {}).get("nodeName")}
    return out


def volume_usage(c: Any, fresh: bool = False) -> Dict[str, Any]:
    """{"volumes": {"ns/claim": usage}, "errors": [...], "available": bool}. Cached for a minute."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    import httpx
    with _vol_lock:
        if not fresh and _vol_cache["data"] is not None and time.time() - _vol_cache["t"] < VOLUME_CACHE_S:
            return _vol_cache["data"]
    try:
        token = open(f"{SA_DIR}/token").read().strip()
    except OSError:
        data = {"volumes": {}, "errors": ["volume usage is read from the kubelets, so it needs the agent to run inside the cluster"],
                "available": False}
        with _vol_lock:
            _vol_cache.update(t=time.time(), data=data)
        return data
    verify: Any = False if os.environ.get("K10Y_KUBELET_INSECURE", "").lower() == "true" else f"{SA_DIR}/ca.crt"
    nodes = [n for n in c.core.list_node().items
             if any(x.type == "Ready" and x.status == "True" for x in (n.status.conditions or []))]

    def one(node: Any) -> Tuple[Dict[str, Any], Optional[str]]:
        addr = _kubelet_address(node)
        if not addr:
            return {}, f"{node.metadata.name}: no address"
        try:
            r = httpx.get(f"https://{addr}/stats/summary", headers={"Authorization": f"Bearer {token}"}, verify=verify, timeout=6.0)
        except httpx.HTTPError as e:
            hint = " (set K10Y_KUBELET_INSECURE=true if the kubelet's certificate isn't signed by the cluster CA)" if "CERTIFICATE" in str(e).upper() else ""
            return {}, f"{node.metadata.name}: {e}{hint}"
        if r.status_code == 403:
            return {}, f"{node.metadata.name}: forbidden (the agent needs nodes/stats: re-apply install.yaml)"
        if r.status_code >= 400:
            return {}, f"{node.metadata.name}: kubelet answered {r.status_code}"
        return parse_stats_summary(r.json()), None

    volumes: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(nodes)))) as pool:
        for found, err in pool.map(one, nodes):
            volumes.update(found)
            if err:
                errors.append(err)
    data = {"volumes": volumes, "errors": errors, "available": bool(volumes) or not errors}
    with _vol_lock:
        _vol_cache.update(t=time.time(), data=data)
    return data


def _size(b: Optional[float]) -> str:
    if not b:
        return "0"
    for unit, n in (("Ti", 2 ** 40), ("Gi", 2 ** 30), ("Mi", 2 ** 20)):
        if b >= n:
            return f"{b / n:.1f}{unit}"
    return f"{int(b)}B"


def volume_problems(c: Any) -> List[Dict[str, Any]]:
    limit, out = volume_full_percent(), []
    for key, v in volume_usage(c)["volumes"].items():
        ns, name = key.split("/", 1)
        if v["used_percent"] >= limit:
            out.append({"kind": "PersistentVolumeClaim", "namespace": ns, "name": name,
                        "reason": f"volume {v['used_percent']:.0f}% full ({_size(v['used_bytes'])} of {_size(v['capacity_bytes'])})"})
        elif v.get("inodes_percent") is not None and v["inodes_percent"] >= limit:
            out.append({"kind": "PersistentVolumeClaim", "namespace": ns, "name": name,
                        "reason": f"volume out of inodes ({v['inodes_percent']:.0f}% used)"})
    return out


# --- CronJobs ---------------------------------------------------------------------------------

def cronjob_runs(jobs: List[Dict[str, Any]], namespace: str, name: str) -> List[Dict[str, Any]]:
    """The Jobs a CronJob created, newest first (from collector job dicts)."""
    owned = [j for j in jobs if j["namespace"] == namespace and j.get("owner") == f"CronJob/{name}"]
    return sorted(owned, key=lambda j: j["age_s"] if j["age_s"] is not None else 0)


def cronjob_problem(cj: Dict[str, Any], runs: List[Dict[str, Any]]) -> Optional[str]:
    """Last run failed, or it keeps being scheduled without succeeding."""
    if cj["suspended"]:
        return None
    last = next((j for j in runs if j["status"] in ("Complete", "Failed")), None)
    if last and last["status"] == "Failed":
        return "last run failed" + (f": {last['failure_reason']}" if last.get("failure_reason") else "")
    sched, ok = cj.get("last_schedule_s"), cj.get("last_success_s")
    if sched is not None and not cj["active"] and sched > 600:
        if ok is None and cj["age_s"] and cj["age_s"] > 3600:
            return "has never succeeded"
        if ok is not None and ok - sched > 600:
            return "the latest scheduled run didn't succeed"
    return None


# --- quotas -----------------------------------------------------------------------------------

def quotas(c: Any, namespace: Optional[str] = None) -> Dict[str, Any]:
    from kubernetes.utils import parse_quantity

    def num(q: Any) -> Optional[float]:
        try:
            return float(parse_quantity(q))
        except (ValueError, TypeError):
            return None
    try:
        rq = (c.core.list_namespaced_resource_quota(namespace) if namespace else c.core.list_resource_quota_for_all_namespaces()).items
        lr = (c.core.list_namespaced_limit_range(namespace) if namespace else c.core.list_limit_range_for_all_namespaces()).items
    except ApiException as e:
        if e.status == 403:
            return {"quotas": [], "limit_ranges": [], "error": "the agent has no permission to read quotas (re-apply install.yaml)"}
        raise
    out_q = []
    for q in rq:
        hard, used = (q.status.hard or {}) if q.status else {}, (q.status.used or {}) if q.status else {}
        rows = []
        for res, h in sorted(hard.items()):
            u, hv = num(used.get(res, "0")), num(h)
            rows.append({"resource": res, "hard": h, "used": used.get(res, "0"),
                         "percent": round(100 * u / hv, 1) if hv and u is not None else None})
        out_q.append({"namespace": q.metadata.namespace, "name": q.metadata.name, "items": rows})
    out_l = []
    for l in lr:
        for item in l.spec.limits or []:
            out_l.append({"namespace": l.metadata.namespace, "name": l.metadata.name, "type": item.type,
                          "default": item.default or {}, "default_request": item.default_request or {},
                          "min": item.min or {}, "max": item.max or {}})
    return {"quotas": out_q, "limit_ranges": out_l, "error": None}


# --- rollout revisions --------------------------------------------------------------------------

def revision_details(c: Any, namespace: str, name: str) -> List[Dict[str, Any]]:
    """Each Deployment revision with its change cause and pod template (as YAML, for comparing)."""
    dep = c.apps.read_namespaced_deployment(name, namespace)
    current = (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
    out = []
    for rs in c.apps.list_namespaced_replica_set(namespace).items:
        if not any(o.uid == dep.metadata.uid for o in rs.metadata.owner_references or []):
            continue
        ann = rs.metadata.annotations or {}
        rev = ann.get("deployment.kubernetes.io/revision")
        if rev is None:
            continue
        tmpl = c.core.api_client.sanitize_for_serialization(rs.spec.template)
        (tmpl.get("metadata") or {}).get("labels", {}).pop("pod-template-hash", None)
        out.append({"revision": int(rev), "current": rev == current, "replicaset": rs.metadata.name,
                    "images": [x.image for x in rs.spec.template.spec.containers], "replicas": rs.status.replicas or 0,
                    "created": _iso(rs.metadata.creation_timestamp), "change_cause": ann.get("kubernetes.io/change-cause") or "",
                    "template": yaml.safe_dump(tmpl, sort_keys=False)})
    return sorted(out, key=lambda r: r["revision"], reverse=True)


# --- search -------------------------------------------------------------------------------------

def search(c: Any, q: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Objects whose name contains q (case-insensitive), across every namespace and kind."""
    needle = q.strip().lower()
    if len(needle) < 2:
        return []
    out: List[Dict[str, Any]] = []

    def add(kind: str, items: List[Dict[str, Any]], status=lambda x: x.get("status")) -> None:
        for x in items:
            if needle in (x.get("name") or "").lower():
                out.append({"kind": kind, "namespace": x.get("namespace"), "name": x["name"], "status": status(x)})
    sources = [
        ("Deployment", c.deployments, lambda x: f"{x['ready']}/{x['desired']} ready"),
        ("StatefulSet", c.statefulsets, lambda x: f"{x['ready']}/{x['desired']} ready"),
        ("DaemonSet", c.daemonsets, lambda x: f"{x['ready']}/{x['desired']} ready"),
        ("CronJob", c.cronjobs, lambda x: "suspended" if x["suspended"] else x["schedule"]),
        ("Job", c.jobs, lambda x: x["status"]),
        ("Pod", c.pods, lambda x: x["status"]),
        ("Service", c.services, lambda x: x.get("type")),
        ("Ingress", c.ingresses, lambda x: ", ".join(x.get("hosts") or [])),
        ("ConfigMap", c.configmaps, lambda x: None),
        ("Secret", c.secrets, lambda x: x.get("type")),
        ("PersistentVolumeClaim", c.persistentvolumeclaims, lambda x: x.get("status")),
        ("Node", c.nodes, lambda x: "Ready" if x.get("ready") else "NotReady"),
    ]
    for kind, fn, st in sources:
        try:
            add(kind, fn(), st)
        except (ApiException, AttributeError, KeyError):
            continue
    for r in helm_releases(c):
        if needle in (r["name"] or "").lower():
            out.append({"kind": "HelmRelease", "namespace": r["namespace"], "name": r["name"], "status": r["status"]})
    exact = lambda x: (x["name"].lower() != needle, x["name"].lower().find(needle), x["kind"], x["name"])
    return sorted(out, key=exact)[:limit]
