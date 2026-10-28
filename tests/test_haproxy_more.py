"""HAProxy: policies, extra routes, TLS binds, live rates, alert conditions, logs, k8s sync, config history, groups."""
import time

import pytest

from k10y_agent.hub import alerts
from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx
from k10y_agent.hub import haproxy_monitor as hm

from test_haproxy import FakeDataPlane
from test_hub import CSRF, client, ready_admin  # noqa: F401  (fixture)


class FakeDP(FakeDataPlane):
    def __init__(self):
        super().__init__()
        self.certs = {"shop_lab_local.pem": {"storage_name": "shop_lab_local.pem", "file": "/etc/haproxy/certs/shop_lab_local.pem",
                                             "not_after": "2026-11-01T00:00:00.000Z", "subject": "shop.lab.local"}}

    def call(self, method, path, tx=None, json=None, content=None, params=None, want_response=False):
        if path == "/services/haproxy/storage/ssl_certificates":
            return list(self.certs.values())
        return super().call(method, path, tx, json, content, params)

    def certificates(self):
        return list(self.certs.values())


def apply(dp, *ops):
    return hx.plan_and_apply(dp, list(ops), apply=True)


def test_policies_round_trip_and_keep_custom_rules():
    dp = FakeDP()
    dp.live["frontends"]["web"]["http_request_rule_list"] = [{"type": "set-header", "hdr_name": "X-Team", "hdr_format": "shop"}]
    apply(dp, hx.Op(op="update", kind="frontend", name="web", data={"policies": {
        "allow_ips": ["10.0.0.0/8", "192.168.1.5"], "redirect_https": True, "rate_limit": {"requests": 50, "period": 10}}}))
    fe = dp.live["frontends"]["web"]
    assert [r["type"] for r in fe["http_request_rule_list"]] == ["deny", "redirect", "track-sc", "deny", "set-header"]
    assert fe["stick_table"]["store"] == "http_req_rate(10s)"
    pol = hx.parse_policies(fe)
    assert pol == {"allow_ips": ["10.0.0.0/8", "192.168.1.5"], "redirect_https": True, "rate_limit": {"requests": 50, "period": 10}, "custom_rules": 1,
                   "maintenance": {"enabled": False, "message": "", "allow_ips": []}, "blocklist": False, "forwarded_port": False}
    apply(dp, hx.Op(op="update", kind="frontend", name="web", data={"policies": {"allow_ips": [], "redirect_https": False, "rate_limit": None}}))
    fe = dp.live["frontends"]["web"]
    assert fe["http_request_rule_list"] == [{"type": "set-header", "hdr_name": "X-Team", "hdr_format": "shop"}] and "stick_table" not in fe
    with pytest.raises(hx.DataPlaneError, match="not an IP"):
        apply(dp, hx.Op(op="update", kind="frontend", name="web", data={"policies": {"allow_ips": ["evil host"]}}))


def test_header_and_source_routes_and_tls_binds():
    dp = FakeDP()
    apply(dp, hx.Op(op="update", kind="frontend", name="web", data={
        "routes": [{"kind": "header", "header": "X-Canary", "value": "yes", "backend": "api"}, {"kind": "src", "value": "10.1.0.0/16,10.2.0.0/16", "backend": "api"}],
        "binds": [{"address": "*", "port": 80}, {"address": "*", "port": 443, "ssl": True, "certificate": "shop_lab_local.pem"}]}))
    fe = dp.live["frontends"]["web"]
    routes, simple = hx.parse_routes(fe)
    assert simple and routes == [{"kind": "header", "value": "yes", "header": "X-Canary", "style": "inline", "backend": "api"},
                                 {"kind": "src", "value": "10.1.0.0/16,10.2.0.0/16", "style": "inline", "backend": "api"}]
    assert fe["binds"]["*:443"]["ssl_certificate"] == "/etc/haproxy/certs/shop_lab_local.pem"
    with pytest.raises(hx.DataPlaneError, match="uploaded certificate"):
        apply(dp, hx.Op(op="update", kind="frontend", name="web", data={"binds": [{"address": "*", "port": 443, "ssl": True, "certificate": "nope.pem"}]}))


def test_sample_rates_from_counters():
    s1 = [{"type": "frontend", "backend_name": None, "name": "web", "stats": {"hrsp_2xx": 100, "hrsp_5xx": 0, "scur": 3, "slim": 100}},
          {"type": "backend", "backend_name": None, "name": "shop", "stats": {"stot": 100, "hrsp_5xx": 0, "rtime": 12}}]
    _, c1 = hm.Sample.take(s1, None, 0)
    s2 = [{"type": "frontend", "backend_name": None, "name": "web", "stats": {"hrsp_2xx": 180, "hrsp_5xx": 20, "scur": 5, "slim": 100}},
          {"type": "backend", "backend_name": None, "name": "shop", "stats": {"stot": 200, "hrsp_5xx": 20, "rtime": 15}}]
    p, _ = hm.Sample.take(s2, c1, 10)
    assert p["f"]["web"] == {"req": 10.0, "sess": 5, "slim": 100, "e5": 2.0, "e5_pct": 20.0, "in": 0, "out": 0}
    assert p["b"]["shop"]["req"] == 10.0 and p["b"]["shop"]["rtime"] == 15


def test_haproxy_alert_conditions():
    latest = {"stats": [
        {"type": "server", "backend_name": "legacy", "name": "old1", "stats": {"status": "DOWN"}},
        {"type": "server", "backend_name": "shop", "name": "s1", "stats": {"status": "UP"}},
        {"type": "server", "backend_name": "shop", "name": "s2", "stats": {"status": "DOWN"}}],
        "point": {"f": {"web": {"req": 20, "e5_pct": 30, "sess": 95, "slim": 100}}},
        "certs": [{"storage_name": "a.pem", "not_after": "2000-01-01T00:00:00Z"}], "error": ""}
    keys = lambda c, **p: [x[0] for x in alerts.evaluate_haproxy(latest, {"condition": c, **p})]
    assert keys("server_down") == ["hx/server/legacy/old1", "hx/server/shop/s2"]
    assert keys("backend_down") == ["hx/backend/legacy"]
    assert keys("error_rate", threshold=10) == ["hx/5xx/web"] and keys("error_rate", threshold=50) == []
    assert keys("sessions", threshold=90) == ["hx/sessions/web"]
    assert keys("cert_expiry", threshold=14) == ["hx/cert/a.pem"]
    assert alerts.evaluate_haproxy({"error": "refused"}, {"condition": "unreachable"})[0][0] == "hx/unreachable"
    assert alerts.evaluate_haproxy({"error": "refused"}, {"condition": "server_down"}) is None
    assert alerts.evaluate_haproxy(None, {"condition": "server_down"}) is None


def test_syslog_lines_are_parsed():
    tag, e = hm.parse_line('<134>Oct 23 07:00:00 lab-lb[8]: 172.23.0.1:40000 [23/Oct/2026:07:00:00.123] web shop/shop1 0/0/1/2/3 200 615 - - ---- 1/1/0/0/0 0/0 "GET /cart HTTP/1.1"')
    assert tag == "lab-lb" and e["status"] == 200 and e["server"] == "shop1" and e["ms"] == 3 and e["request"] == "GET /cart HTTP/1.1"
    tag, e = hm.parse_line("<133>Oct 23 07:00:00 haproxy[8]: Server legacy/old1 is DOWN, reason: Layer4 timeout")
    assert tag == "haproxy" and "status" not in e and "DOWN" in e["msg"]
    rx = hm.SyslogReceiver(0)
    for i in range(3):
        rx.add(f"<134>Oct 23 07:00:0{i} lab-lb[8]: line {i}")
    assert [x["msg"] for x in rx.read("lab-lb", after=1)] == ["line 1", "line 2"] and rx.tags() == ["lab-lb"]


def test_kubernetes_sync_adds_updates_and_removes_servers(client, monkeypatch):
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "lab", "url": "https://x:1", "token": "t"}, headers=CSRF).json()["id"]
    nodes = [{"name": "n1", "ready": True, "internal_ip": "10.0.0.1"}, {"name": "n2", "ready": True, "internal_ip": "10.0.0.2"},
             {"name": "n3", "ready": False, "internal_ip": "10.0.0.3"}]
    svc = [{"name": "web", "ports": [{"name": "http", "port": 80, "target_port": "8080", "node_port": 30699}]}]
    agent = lambda c, path: nodes if path == "nodes" else svc if path.startswith("services") else {
        "pods": [{"name": "web-1", "pod_ip": "10.42.0.9", "phase": "Running", "ready_containers": 1, "total_containers": 1}]}
    dp = FakeDP()
    mon = hm.HAProxyMonitor(hub.state["db"].session, lambda v: v, agent, make_client=lambda inst, pw: dp)
    with hub.state["db"].session() as s:
        inst = hx.HAProxyInstance(name="lb", url="http://lb:5555", username="a", password_encrypted="x")
        s.add(inst); s.commit()
        s.add(hx.HAProxyK8sSync(instance_id=inst.id, backend="api", cluster_id=cid, namespace="demo", service="web", mode="nodeport"))
        s.commit()
        assert mon.run_syncs(s, inst, dp)[0].startswith("create server")
        assert {n: (v["address"], v["port"]) for n, v in dp.live["backends"]["api"]["servers"].items()} == {"k8s-n1": ("10.0.0.1", 30699), "k8s-n2": ("10.0.0.2", 30699)}
        nodes[1]["ready"] = False
        assert "delete server api/k8s-n2" in mon.run_syncs(s, inst, dp)[0]
        assert mon.run_syncs(s, inst, dp)[0] == "in sync (1 servers)"
        row = s.query(hx.HAProxyK8sSync).one(); row.mode = "pods"; s.commit()
        mon.run_syncs(s, inst, dp)
        assert {n: (v["address"], v["port"]) for n, v in dp.live["backends"]["api"]["servers"].items()} == {"k8s-web-1": ("10.42.0.9", 8080)}
        versions = s.query(hx.HAProxyConfigVersion).all()
        assert versions and all(v.source == "sync" for v in versions)


def test_config_history_records_initial_external_and_dedupes(client):
    ready_admin(client)
    dp = FakeDP()
    mon = hm.HAProxyMonitor(hub.state["db"].session, lambda v: v, lambda c, p: [], make_client=lambda inst, pw: dp)
    with hub.state["db"].session() as s:
        inst = hx.HAProxyInstance(name="lb", url="http://lb:5555", username="a", password_encrypted="x"); s.add(inst); s.commit()
        mon._record_config(s, inst, dp); mon._record_config(s, inst, dp)
        apply(dp, hx.Op(op="delete", kind="server", backend="shop", name="shop1"))   # someone else changes it
        mon._record_config(s, inst, dp)
        assert [v.source for v in s.query(hx.HAProxyConfigVersion).order_by(hx.HAProxyConfigVersion.id)] == ["initial", "external"]


def test_monitor_samples_history_and_api_endpoints(client, monkeypatch):
    dp = FakeDP()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: dp)
    ready_admin(client)
    iid = client.post("/api/hub/haproxy", json={"name": "lb1", "url": "http://lb1:5555", "username": "admin", "password": "pw",
                                                "group_name": "edge"}, headers=CSRF).json()["id"]
    iid2 = client.post("/api/hub/haproxy", json={"name": "lb2", "url": "http://lb2:5555", "username": "admin", "password": "pw",
                                                 "group_name": "edge"}, headers=CSRF).json()["id"]
    mon = hub.haproxy_mon()
    mon.make = lambda inst, pw: dp
    mon.run_once(slow=True); time.sleep(0.01); mon.run_once(slow=False)
    assert client.get(f"/api/hub/haproxy/{iid}/history").json()["points"]
    o = client.get(f"/api/hub/haproxy/{iid}/overview").json()
    assert [g["name"] for g in o["group"]] == ["lb2"] and o["instance"]["log_tag"] == "lb1"
    vers = client.get(f"/api/hub/haproxy/{iid}/versions").json()
    assert vers[0]["source"] == "initial"
    assert "frontends" in client.get(f"/api/hub/haproxy/{iid}/versions/{vers[0]['id']}").json()["text"]
    certs = client.get(f"/api/hub/haproxy/{iid}/certificates").json()
    assert certs[0]["name"] == "shop_lab_local.pem" and certs[0]["days_left"] is not None
    # group change: preview on both, apply on both, recorded in history
    body = {"instance_ids": [iid, iid2], "ops": [{"op": "update", "kind": "server", "backend": "shop", "name": "shop1", "data": {"weight": 10}}]}
    prev = client.post("/api/hub/haproxy/group-changes", json=body, headers=CSRF).json()["results"]
    assert [r["valid"] for r in prev] == [True, True]
    done = client.post("/api/hub/haproxy/group-changes", json={**body, "apply": True, "note": "canary"}, headers=CSRF).json()["results"]
    assert all(r["applied"] for r in done) and dp.live["backends"]["shop"]["servers"]["shop1"]["weight"] == 10
    assert client.get(f"/api/hub/haproxy/{iid}/versions").json()[0]["summary"].startswith("canary:")
    logs = client.get(f"/api/hub/haproxy/{iid}/logs").json()
    assert logs["tag"] == "lb1" and logs["enabled"] is False
