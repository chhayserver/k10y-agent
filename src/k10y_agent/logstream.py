"""Live log streaming (kubectl logs -f), for one pod or every pod of a workload."""

import queue
import threading
import time
from typing import Any, Iterator, List, Optional, Tuple

from kubernetes.client.rest import ApiException

MAX_PODS = 20
MAX_SECONDS = 30 * 60      # streams end after 30 minutes; the browser reconnects if still open
HEARTBEAT_S = 15           # an empty write keeps proxies from closing idle streams


def _follow(core: Any, namespace: str, pod: str, container: Optional[str], tail: int, since: Optional[int],
            timestamps: bool, stop: threading.Event) -> Iterator[str]:
    resp = core.read_namespaced_pod_log(pod, namespace, container=container, follow=True, tail_lines=tail,
                                        since_seconds=since, timestamps=timestamps, _preload_content=False)
    try:
        buf = b""
        for chunk in resp.stream(1024):
            if stop.is_set():
                return
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                yield line.decode("utf-8", "replace")
        if buf:
            yield buf.decode("utf-8", "replace")
    finally:
        resp.release_conn()
        resp.close()


def stream(core: Any, targets: List[Tuple[str, Optional[str]]], namespace: str, tail: int = 100,
           since: Optional[int] = None, timestamps: bool = False, prefix: bool = False) -> Iterator[str]:
    """Merge the followed logs of (pod, container) targets into one line stream."""
    q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=5000)
    stop = threading.Event()

    def pump(pod: str, container: Optional[str]) -> None:
        tag = f"[{pod}{'/' + container if container else ''}] " if prefix else ""
        try:
            for line in _follow(core, namespace, pod, container, tail, since, timestamps, stop):
                q.put(tag + line)
        except ApiException as e:
            q.put(f"{tag}⚠ log stream error: {e.reason}")
        except Exception as e:  # connection closed, pod deleted...
            if not stop.is_set():
                q.put(f"{tag}⚠ log stream ended: {e}")
        finally:
            q.put(None)

    for pod, container in targets[:MAX_PODS]:
        threading.Thread(target=pump, args=(pod, container), daemon=True).start()
    if len(targets) > MAX_PODS:
        yield f"⚠ showing the first {MAX_PODS} of {len(targets)} pods\n"
    alive, deadline = min(len(targets), MAX_PODS), time.monotonic() + MAX_SECONDS
    try:
        while alive and time.monotonic() < deadline:
            try:
                item = q.get(timeout=HEARTBEAT_S)
            except queue.Empty:
                yield ""
                continue
            if item is None:
                alive -= 1
            else:
                yield item + "\n"
        if alive:
            yield "⚠ stream time limit reached\n"
    finally:
        stop.set()


def workload_pods(collector: Any, kind: str, namespace: str, name: str) -> List[str]:
    reader = {"deployments": collector.apps.read_namespaced_deployment, "statefulsets": collector.apps.read_namespaced_stateful_set,
              "daemonsets": collector.apps.read_namespaced_daemon_set, "jobs": collector.batch.read_namespaced_job}[kind]
    obj = reader(name, namespace)
    labels = (obj.spec.selector.match_labels or {}) if obj.spec.selector else {}
    if not labels:
        return []
    sel = ",".join(f"{k}={v}" for k, v in labels.items())
    pods = collector.core.list_namespaced_pod(namespace, label_selector=sel).items
    return sorted(p.metadata.name for p in pods if p.status.phase in ("Running", "Pending", "Succeeded", "Failed")
                  and not p.metadata.deletion_timestamp)
