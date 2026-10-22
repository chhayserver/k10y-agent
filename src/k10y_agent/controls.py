"""Control actions. Every mutating call goes through Controller so the
protected-namespace guard lives in one place; the API layer adds auditing."""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException
from kubernetes.dynamic import DynamicClient
from kubernetes.dynamic.exceptions import ResourceNotFoundError

log = logging.getLogger(__name__)

FIELD_MANAGER = "k10y"
MAX_APPLY_DOCUMENTS = 50


class ProtectedNamespaceError(Exception):
    """The target lives in a namespace no control action may touch."""


class ActionError(Exception):
    """The request itself is invalid (bad input, nothing to roll back to, ...)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def api_error_message(e: ApiException) -> str:
    try:
        return json.loads(e.body).get("message") or e.reason
    except (TypeError, ValueError):
        return e.reason or str(e)


class AuditLog:
    """In-memory ring of recent actions, mirrored to an append-only JSON-lines file."""

    def __init__(self, path: str, keep: int = 1000):
        self.path = path
        self.lock = threading.Lock()
        self.entries: deque = deque(maxlen=keep)
        try:
            with open(path) as f:
                for line in f:
                    if line.strip():
                        self.entries.append(json.loads(line))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("could not read audit log %s: %s", path, e)

    def record(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, separators=(",", ":"))
        log.info("audit %s", line)
        with self.lock:
            self.entries.append(entry)
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "a") as f:
                    f.write(line + "\n")
            except OSError as e:
                log.warning("could not write audit log %s: %s", self.path, e)

    def list(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self.lock:
            return list(reversed(self.entries))[:limit]


class Controller:
    def __init__(self, api_client: client.ApiClient, protected_namespaces: Iterable[str]):
        self.api_client = api_client
        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)
        self.protected = set(protected_namespaces)
        self._dynamic: Optional[DynamicClient] = None

    @property
    def dynamic(self) -> DynamicClient:
        # Created lazily: it runs API discovery on construction.
        if self._dynamic is None:
            self._dynamic = DynamicClient(self.api_client)
        return self._dynamic

    def guard(self, namespace: Optional[str]) -> None:
        if namespace in self.protected:
            raise ProtectedNamespaceError(f"namespace {namespace!r} is protected; control actions are not allowed there")

    @staticmethod
    def _dry(dry_run: bool) -> Dict[str, str]:
        return {"dry_run": "All"} if dry_run else {}

    # --- deployments ---------------------------------------------------------

    def scale(self, namespace: str, name: str, replicas: int, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        if replicas < 0:
            raise ActionError("replicas must be >= 0")
        current = self.apps.read_namespaced_deployment_scale(name, namespace).spec.replicas
        self.apps.patch_namespaced_deployment_scale(
            name, namespace, {"spec": {"replicas": replicas}}, **self._dry(dry_run)
        )
        return {"message": f"scaled {namespace}/{name} from {current} to {replicas} replicas"}

    def restart(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        # Same mechanism as `kubectl rollout restart`.
        patch = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now_iso()}}}}}
        self.apps.patch_namespaced_deployment(name, namespace, patch, **self._dry(dry_run))
        return {"message": f"rolling restart of {namespace}/{name} started"}

    def set_image(
        self, namespace: str, name: str, container: str, image: str, dry_run: bool = False
    ) -> Dict[str, Any]:
        self.guard(namespace)
        image = image.strip()
        if not image:
            raise ActionError("image must not be empty")
        dep = self.apps.read_namespaced_deployment(name, namespace)
        names = [c.name for c in dep.spec.template.spec.containers]
        if container not in names:
            raise ActionError(f"deployment has no container {container!r} (containers: {', '.join(names)})")
        idx = names.index(container)
        old = dep.spec.template.spec.containers[idx].image
        patch = [{"op": "replace", "path": f"/spec/template/spec/containers/{idx}/image", "value": image}]
        self.apps.patch_namespaced_deployment(name, namespace, patch, **self._dry(dry_run))
        return {"message": f"{namespace}/{name} container {container}: {old} -> {image}"}

    def history(self, namespace: str, name: str) -> List[Dict[str, Any]]:
        dep = self.apps.read_namespaced_deployment(name, namespace)
        current = (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
        revisions = []
        for rs in self.apps.list_namespaced_replica_set(namespace).items:
            owners = rs.metadata.owner_references or []
            if not any(o.uid == dep.metadata.uid for o in owners):
                continue
            rev = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
            if rev is None:
                continue
            revisions.append(
                {
                    "revision": int(rev),
                    "current": rev == current,
                    "replicaset": rs.metadata.name,
                    "images": [c.image for c in rs.spec.template.spec.containers],
                    "replicas": rs.status.replicas or 0,
                    "created": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
                }
            )
        return sorted(revisions, key=lambda r: r["revision"], reverse=True)

    def rollback(
        self, namespace: str, name: str, revision: Optional[int] = None, dry_run: bool = False
    ) -> Dict[str, Any]:
        self.guard(namespace)
        history = self.history(namespace, name)
        current = next((r for r in history if r["current"]), None)
        if revision is None:
            older = [r for r in history if current and r["revision"] < current["revision"]]
            if not older:
                raise ActionError("no previous revision to roll back to")
            target = older[0]
        else:
            target = next((r for r in history if r["revision"] == revision), None)
            if target is None:
                raise ActionError(f"revision {revision} not found")
            if target["current"]:
                raise ActionError(f"revision {revision} is already the current revision")
        rs = self.apps.read_namespaced_replica_set(target["replicaset"], namespace)
        template = self.api_client.sanitize_for_serialization(rs.spec.template)
        template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
        patch = [{"op": "replace", "path": "/spec/template", "value": template}]
        self.apps.patch_namespaced_deployment(name, namespace, patch, **self._dry(dry_run))
        return {
            "message": f"rolled back {namespace}/{name} to revision {target['revision']} ({', '.join(target['images'])})"
        }

    def scale_statefulset(self, namespace: str, name: str, replicas: int, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        if replicas < 0:
            raise ActionError("replicas must be >= 0")
        current = self.apps.read_namespaced_stateful_set_scale(name, namespace).spec.replicas
        self.apps.patch_namespaced_stateful_set_scale(name, namespace, {"spec": {"replicas": replicas}}, **self._dry(dry_run))
        return {"message": f"scaled statefulset {namespace}/{name} from {current} to {replicas} replicas"}

    def restart_workload(self, kind: str, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        """Rolling restart of a StatefulSet or DaemonSet (same mechanism as kubectl rollout restart)."""
        self.guard(namespace)
        patch = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now_iso()}}}}}
        fn = {"statefulset": self.apps.patch_namespaced_stateful_set, "daemonset": self.apps.patch_namespaced_daemon_set}[kind]
        fn(name, namespace, patch, **self._dry(dry_run))
        return {"message": f"rolling restart of {kind} {namespace}/{name} started"}

    def delete_job(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        client.BatchV1Api(self.api_client).delete_namespaced_job(
            name, namespace, propagation_policy="Background", **self._dry(dry_run))  # also removes its pods
        return {"message": f"deleted job {namespace}/{name} and its pods"}

    def delete_deployment(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        self.apps.delete_namespaced_deployment(name, namespace, **self._dry(dry_run))
        return {"message": f"deleted deployment {namespace}/{name}"}

    # --- pods & services -----------------------------------------------------

    def delete_pod(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        self.core.delete_namespaced_pod(name, namespace, **self._dry(dry_run))
        return {"message": f"deleted pod {namespace}/{name}"}

    def delete_service(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        self.core.delete_namespaced_service(name, namespace, **self._dry(dry_run))
        return {"message": f"deleted service {namespace}/{name}"}

    def delete_configmap(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        self.core.delete_namespaced_config_map(name, namespace, **self._dry(dry_run))
        return {"message": f"deleted configmap {namespace}/{name}"}

    def delete_secret(self, namespace: str, name: str, dry_run: bool = False) -> Dict[str, Any]:
        self.guard(namespace)
        self.core.delete_namespaced_secret(name, namespace, **self._dry(dry_run))
        return {"message": f"deleted secret {namespace}/{name}"}

    # --- nodes ---------------------------------------------------------------

    def cordon(self, name: str, unschedulable: bool = True, dry_run: bool = False) -> Dict[str, Any]:
        self.core.patch_node(name, {"spec": {"unschedulable": unschedulable}}, **self._dry(dry_run))
        return {"message": f"node {name} {'cordoned' if unschedulable else 'uncordoned'}"}

    def drain(self, name: str, dry_run: bool = False) -> Dict[str, Any]:
        """Cordon, then evict pods. Pods in protected namespaces, DaemonSet pods,
        static (mirror) pods and finished pods are left alone."""
        self.cordon(name, True, dry_run)
        pods = self.core.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}").items
        evicted, skipped, failed = [], [], []
        for pod in pods:
            ref = f"{pod.metadata.namespace}/{pod.metadata.name}"
            owners = pod.metadata.owner_references or []
            if pod.metadata.namespace in self.protected:
                skipped.append({"pod": ref, "reason": "protected namespace"})
            elif any(o.kind == "DaemonSet" for o in owners):
                skipped.append({"pod": ref, "reason": "DaemonSet pod"})
            elif "kubernetes.io/config.mirror" in (pod.metadata.annotations or {}):
                skipped.append({"pod": ref, "reason": "static pod"})
            elif pod.status.phase in ("Succeeded", "Failed"):
                skipped.append({"pod": ref, "reason": "already finished"})
            else:
                body = client.V1Eviction(
                    metadata=client.V1ObjectMeta(name=pod.metadata.name, namespace=pod.metadata.namespace)
                )
                try:
                    self.core.create_namespaced_pod_eviction(
                        pod.metadata.name, pod.metadata.namespace, body, **self._dry(dry_run)
                    )
                    evicted.append(ref)
                except ApiException as e:
                    failed.append({"pod": ref, "reason": api_error_message(e)})
        message = f"node {name} cordoned; evicted {len(evicted)} pod(s), skipped {len(skipped)}"
        if failed:
            message += f", {len(failed)} eviction(s) failed"
        return {"message": message, "evicted": evicted, "skipped": skipped, "failed": failed}

    # --- apply ---------------------------------------------------------------

    def apply(self, manifest: str, dry_run: bool = False) -> Dict[str, Any]:
        """Server-side apply of one or more YAML documents."""
        try:
            docs = [d for d in yaml.safe_load_all(manifest) if d]
        except yaml.YAMLError as e:
            raise ActionError(f"invalid YAML: {e}")
        if not docs:
            raise ActionError("no objects found in the YAML")
        if len(docs) > MAX_APPLY_DOCUMENTS:
            raise ActionError(f"at most {MAX_APPLY_DOCUMENTS} objects per apply")

        # Validate every document (and the namespace guard) before changing anything.
        planned = []
        for i, doc in enumerate(docs, 1):
            if not isinstance(doc, dict) or not doc.get("apiVersion") or not doc.get("kind"):
                raise ActionError(f"document {i}: apiVersion and kind are required")
            meta = doc.setdefault("metadata", {})
            if not meta.get("name"):
                raise ActionError(f"document {i} ({doc['kind']}): metadata.name is required")
            try:
                resource = self.dynamic.resources.get(api_version=doc["apiVersion"], kind=doc["kind"])
            except ResourceNotFoundError:
                raise ActionError(f"document {i}: unknown kind {doc['apiVersion']}/{doc['kind']}")
            namespace = None
            if resource.namespaced:
                namespace = meta.get("namespace") or "default"
                meta["namespace"] = namespace
                self.guard(namespace)
            elif doc["kind"] == "Namespace":
                self.guard(meta["name"])
            planned.append((doc, resource, namespace))

        results = []
        for doc, resource, namespace in planned:
            ref = f"{doc['kind']}/{doc['metadata']['name']}" + (f" in {namespace}" if namespace else "")
            try:
                self.dynamic.server_side_apply(
                    resource,
                    body=doc,
                    name=doc["metadata"]["name"],
                    namespace=namespace,
                    field_manager=FIELD_MANAGER,
                    force_conflicts=True,
                    **self._dry(dry_run),
                )
                results.append({"object": ref, "ok": True})
            except ApiException as e:
                results.append({"object": ref, "ok": False, "error": api_error_message(e)})
        ok = sum(r["ok"] for r in results)
        message = f"applied {ok}/{len(results)} object(s)"
        if ok < len(results):
            raise ActionError(message + ": " + "; ".join(f"{r['object']}: {r['error']}" for r in results if not r["ok"]))
        return {"message": message, "results": results}
