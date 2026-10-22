from types import SimpleNamespace

from k10y_agent.metrics_history import MetricsHistory


class FakeCollector:
    def __init__(self):
        self.ts = "2026-10-22T04:00:00Z"
        self.cpu = "250m"
        self.custom = SimpleNamespace(list_cluster_custom_object=self._metrics)
        node = SimpleNamespace(metadata=SimpleNamespace(name="n1"), status=SimpleNamespace(allocatable={"cpu": "2", "memory": "4Gi"}))
        self.core = SimpleNamespace(list_node=lambda: SimpleNamespace(items=[node]))

    def _metrics(self, group, version, resource):
        if resource == "nodes":
            return {"items": [{"metadata": {"name": "n1"}, "timestamp": self.ts, "usage": {"cpu": self.cpu, "memory": "1Gi"}}]}
        return {"items": [{"metadata": {"namespace": "demo", "name": "web"}, "timestamp": self.ts,
                           "containers": [{"usage": {"cpu": "10m", "memory": "64Mi"}}, {"usage": {"cpu": "5m", "memory": "16Mi"}}]}]}


def test_records_only_new_measurements(monkeypatch):
    fake = FakeCollector()
    h = MetricsHistory(lambda: fake, retention_s=10**9)
    h.sample()
    h.sample()  # same metrics-server timestamp -> no duplicate point
    assert len(h.query("cluster")["points"]) == 1
    fake.ts, fake.cpu = "2026-10-22T04:00:15Z", "500m"
    h.sample()
    cluster = h.query("cluster")
    assert [p[1] for p in cluster["points"]] == [250, 500]
    assert cluster["cpu_allocatable_m"] == 2000
    assert cluster["resolution_s"] is None or cluster["resolution_s"] > 0


def test_since_and_pod_scope():
    fake = FakeCollector()
    h = MetricsHistory(lambda: fake, retention_s=10**9)
    h.sample()
    t0 = h.query("cluster")["points"][0][0]
    assert h.query("cluster", since=t0)["points"] == []
    pod = h.query("pod", namespace="demo", name="web")["points"]
    assert pod[0][1:] == (15, 80 * 2**20)
    assert h.query("nodes")["nodes"]["n1"]["memory_allocatable_bytes"] == 4 * 2**30
