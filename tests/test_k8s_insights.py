"""Hub Kubernetes insights: 7-day history buckets, right-sizing, search across clusters (Secret names only with
secrets.view), and which HAProxy backends send traffic to a Service."""
import httpx
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx
from k10y_agent.hub.k8s_insights import BUCKET_MS, MI, K8sHistory, K8sSample, rightsizing, series

from test_haproxy import FakeDataPlane
from test_hub import CSRF, client, login, ready_admin  # noqa: F401  (fixture)


def test_history_writes_one_row_per_five_minutes(client):
    db = hub.state["db"]
    h = K8sHistory(db.session, lambda c, path: {})
    t0 = 1_800_000_000_000 - 1_800_000_000_000 % BUCKET_MS
    with db.session() as s:
        for i, cpu in enumerate([100, 300, 200]):          # three samples in the first five minutes
            h.add(s, 1, t0 + i * 60_000, {"cluster": [0, cpu, 1000 * MI], "nodes": {"n1": [0, cpu, 500]},
                                         "pods": {"shop/web-1": [0, cpu // 2, 64 * MI]}})
        h.add(s, 1, t0 + BUCKET_MS + 1000, {"cluster": [0, 50, 900 * MI], "pods": {}})   # next bucket: writes the first
        s.commit()
        rows = s.query(K8sSample).all()
        assert len(rows) == 1 and rows[0].t == t0
        assert rows[0].data["c"] == [200, 1000 * MI, 300, 1000 * MI]
        assert series(s, 1, "pod", 0, "shop/web-1") == [[t0, 100, 64 * MI, 150, 64 * MI]]
        assert series(s, 1, "node", 0, "n1")[0][1:3] == [200, 500]


def pod(name, owner, cpu_req=500, mem_req=512 * MI, mem_lim=600 * MI, phase="Running"):
    return {"namespace": "shop", "name": name, "owner": owner, "phase": phase, "cpu_request_m": cpu_req, "cpu_limit_m": None,
            "memory_request_bytes": mem_req, "memory_limit_bytes": mem_lim}


def test_rightsizing_verdicts():
    pods = [pod("web-abc12-x1", "Deployment/web"), pod("web-abc12-x2", "Deployment/web"),
            pod("api-def34-y1", "Deployment/api", cpu_req=0, mem_req=0, mem_lim=None)]
    rows = []
    for i in range(12):        # an hour: web uses ~40m CPU, 560Mi (close to its 600Mi limit); api has no requests
        rows.append((i * BUCKET_MS, {"p": {"shop/web-abc12-x1": [40, 500 * MI, 60, 560 * MI],
                                           "shop/web-oldhash-z9": [40, 500 * MI, 60, 550 * MI],      # an older pod of web
                                           "shop/api-def34-y1": [200, 100 * MI, 300, 120 * MI]}}))
    r = rightsizing(pods, rows)
    by = {w["workload"]: w for w in r["workloads"]}
    web, api = by["Deployment/web"], by["Deployment/api"]
    assert r["hours"] == 1.0
    assert web["level"] == "critical" and "Close to its memory limit: OOM risk" in web["findings"]
    assert "CPU over-provisioned" in web["findings"] and web["cpu"]["recommended"] == 50
    assert web["savings"]["cpu_m"] == (500 - 50) * 2 and web["pods"] == 2
    assert web["memory"]["recommended_limit"] >= 560 * MI * 1.4
    assert api["level"] == "warning" and "No CPU request: the scheduler can't plan for it" in api["findings"]
    assert r["workloads"][0]["workload"] == "Deployment/web"      # most urgent first


class FakeAgent:
    """Stands in for agents: answers /search per cluster URL."""
    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def get(self, path):
        req = httpx.Request("GET", self.url + path)
        if "search" in path:
            return httpx.Response(200, json=[{"kind": "Deployment", "namespace": "shop", "name": "payment-api", "status": "2/2 ready"},
                                             {"kind": "Secret", "namespace": "shop", "name": "payment-db", "status": "Opaque"}], request=req)
        return httpx.Response(404, json={"detail": "nope"}, request=req)


def test_search_across_clusters_hides_secret_names_without_permission(client, monkeypatch):
    monkeypatch.setattr(hub, "agent_client", lambda c: FakeAgent(c.url))
    ready_admin(client)
    for n in ("prod", "staging"):
        client.post("/api/hub/clusters", json={"name": n, "url": f"https://{n}:30800", "token": "t"}, headers=CSRF)
    r = client.get("/api/hub/search", params={"q": "payment"}).json()
    assert sorted((x["cluster"], x["kind"]) for x in r["results"]) == [("prod", "Deployment"), ("prod", "Secret"), ("staging", "Deployment"), ("staging", "Secret")]
    roles = {x["name"]: x["id"] for x in client.get("/api/hub/roles").json()}
    prod = next(c["id"] for c in client.get("/api/hub/clusters").json() if c["name"] == "prod")
    client.post("/api/hub/users", json={"username": "dev", "password": "Pass-word-12", "must_change_password": False,
                                        "assignments": [{"role_id": roles["Kubernetes viewer"], "cluster_id": prod}]}, headers=CSRF)
    dev = TestClient(hub.app)
    login(dev, "dev", "Pass-word-12")
    found = dev.get("/api/hub/search", params={"q": "payment"}).json()["results"]
    assert [(x["cluster"], x["kind"]) for x in found] == [("prod", "Deployment")]      # one cluster, no Secret names
    assert dev.get("/api/hub/clusters/99/history").status_code == 404


def test_service_shows_the_haproxy_backends_that_serve_it(client, monkeypatch):
    fake = FakeDataPlane()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: fake)
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "prod", "url": "https://prod:30800", "token": "t"}, headers=CSRF).json()["id"]
    iid = client.post("/api/hub/haproxy", json={"name": "lb1", "url": "http://lb1:5555", "username": "u", "password": "p"}, headers=CSRF).json()["id"]
    # shop1 (10.0.0.1:80) is a pod of the Service; api is linked with a Kubernetes sync
    monkeypatch.setattr(hub.haproxy_mon(), "desired_servers", lambda s, sync: {"pod-a": ("10.0.0.1", 80)} if sync.mode == "pods" else {})
    with hub.state["db"].session() as s:
        s.add(hx.HAProxyK8sSync(instance_id=iid, backend="api", cluster_id=cid, namespace="shop", service="web", port="", mode="pods"))
        s.commit()
    found = client.get(f"/api/hub/clusters/{cid}/services/shop/web/haproxy").json()
    assert sorted((x["backend"], x["via"]) for x in found) == [("api", "Kubernetes sync"), ("shop", "same addresses")]
    shop = next(x for x in found if x["backend"] == "shop")
    assert shop["instance"] == "lb1" and shop["frontends"] == ["web"] and shop["servers"][0]["address"] == "10.0.0.1"


def test_protected_namespaces_permission():
    from k10y_agent.hub.permissions import BUILTIN_ROLES, required_permission

    assert required_permission("PUT", "settings/protected-namespaces") == "namespaces.protect"
    roles = {name: perms for name, _, perms in BUILTIN_ROLES}
    assert "namespaces.protect" in roles["Admin"] and "namespaces.protect" in roles["Kubernetes admin"]
    assert "namespaces.protect" not in roles["Kubernetes operator"] and "namespaces.protect" not in roles["Kubernetes viewer"]


def test_agent_status_lists_missing_features(monkeypatch):
    from k10y_agent.hub import app as hub
    monkeypatch.setattr(hub, "__version__", "2.7.1")
    old = hub.agent_status("2.4.0")
    assert old["outdated"] is True and "Helm releases" in old["missing"] and "Helm rollback" in old["missing"]
    mid = hub.agent_status("2.6.0")
    assert mid["outdated"] and "Helm releases" not in mid["missing"] and "Helm rollback" in mid["missing"]
    assert hub.agent_status("2.7.1") == {"agent_version": "2.7.1", "outdated": False, "missing": []}
    assert hub.agent_status(None)["outdated"] is False
