"""In-memory CPU/memory history, sampled from metrics-server in a background thread.

metrics-server refreshes every --metric-resolution (15s on RKE2/k3s). We poll more often
but only record a point when the measurement timestamp changes, and use that timestamp,
so charts show real measurements rather than repeated values.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from kubernetes.client.rest import ApiException
from kubernetes.utils import parse_quantity

log = logging.getLogger(__name__)

Point = Tuple[int, int, int]  # (epoch ms, cpu millicores, memory bytes)


def _ms(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def _cpu_m(q: str) -> int:
    return int(parse_quantity(q) * 1000)


def _bytes(q: str) -> int:
    return int(parse_quantity(q))


class MetricsHistory:
    def __init__(self, collector_factory: Callable[[], Any], poll_s: float = 5.0, retention_s: int = 3600):
        self.collector_factory = collector_factory
        self.poll_s = poll_s
        self.retention_ms = retention_s * 1000
        self.lock = threading.Lock()
        self.cluster: Deque[Point] = deque()
        self.nodes: Dict[str, Deque[Point]] = {}
        self.pods: Dict[Tuple[str, str], Deque[Point]] = {}
        self.node_alloc: Dict[str, Tuple[int, int]] = {}
        self.error: Optional[str] = None
        self.resolution_s: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="metrics-history", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.sample()
            except Exception as e:  # keep sampling through transient API errors
                self.error = str(e)
                log.warning("metrics sample failed: %s", e)
            self._stop.wait(max(0.5, self.poll_s - (time.monotonic() - started)))

    # --- sampling -------------------------------------------------------------

    def sample(self) -> None:
        c = self.collector_factory()
        try:
            node_metrics = c.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes")["items"]
            pod_metrics = c.custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")["items"]
        except ApiException as e:
            if e.status in (404, 503):
                self.error = "metrics-server is not available in this cluster"
                return
            raise
        nodes = c.core.list_node().items
        alloc = {
            n.metadata.name: (_cpu_m((n.status.allocatable or {}).get("cpu", "0")),
                              _bytes((n.status.allocatable or {}).get("memory", "0")))
            for n in nodes
        }
        now = int(time.time() * 1000)
        with self.lock:
            self.error = None
            self.node_alloc = alloc
            new_cluster_point = False
            for m in node_metrics:
                name = m["metadata"]["name"]
                t = _ms(m.get("timestamp")) or now
                if self._append(self.nodes.setdefault(name, deque()), (t, _cpu_m(m["usage"]["cpu"]), _bytes(m["usage"]["memory"]))):
                    new_cluster_point = True
            if new_cluster_point:
                latest = [self.nodes[n][-1] for n in alloc if n in self.nodes and self.nodes[n]]
                if latest:
                    t = max(p[0] for p in latest)
                    if self._append(self.cluster, (t, sum(p[1] for p in latest), sum(p[2] for p in latest))):
                        recent = list(self.cluster)[-11:]
                        if len(recent) >= 3:  # average spacing; single gaps jitter between 10s and 20s
                            self.resolution_s = round((recent[-1][0] - recent[0][0]) / 1000 / (len(recent) - 1), 1)
            for m in pod_metrics:
                key = (m["metadata"]["namespace"], m["metadata"]["name"])
                t = _ms(m.get("timestamp")) or now
                containers = m.get("containers", [])
                self._append(self.pods.setdefault(key, deque()), (
                    t, sum(_cpu_m(x["usage"]["cpu"]) for x in containers), sum(_bytes(x["usage"]["memory"]) for x in containers)))
            self._prune(now)

    def _append(self, series: Deque[Point], point: Point) -> bool:
        if series and point[0] <= series[-1][0]:
            return False  # same measurement as last time
        series.append(point)
        return True

    def _prune(self, now: int) -> None:
        cutoff = now - self.retention_ms
        for series in [self.cluster, *self.nodes.values(), *self.pods.values()]:
            while series and series[0][0] < cutoff:
                series.popleft()
        for key in [k for k, s in self.pods.items() if not s]:
            del self.pods[key]
        for key in [k for k, s in self.nodes.items() if not s and k not in self.node_alloc]:
            del self.nodes[key]

    # --- queries --------------------------------------------------------------

    @staticmethod
    def _since(series: Deque[Point], since: int) -> List[Point]:
        return [p for p in series if p[0] > since]

    def query(self, scope: str, since: int = 0, namespace: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            base = {"error": self.error, "resolution_s": self.resolution_s, "retention_s": self.retention_ms // 1000}
            if scope == "cluster":
                return {**base,
                        "cpu_allocatable_m": sum(a[0] for a in self.node_alloc.values()),
                        "memory_allocatable_bytes": sum(a[1] for a in self.node_alloc.values()),
                        "points": self._since(self.cluster, since)}
            if scope == "nodes":
                return {**base, "nodes": {
                    n: {"cpu_allocatable_m": self.node_alloc.get(n, (0, 0))[0],
                        "memory_allocatable_bytes": self.node_alloc.get(n, (0, 0))[1],
                        "points": self._since(s, since)}
                    for n, s in self.nodes.items()}}
            if scope == "pod":
                return {**base, "points": self._since(self.pods.get((namespace or "", name or ""), deque()), since)}
        raise ValueError(f"unknown scope {scope!r}")
