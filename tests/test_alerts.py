from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from k10y_agent.hub import alerts
from k10y_agent.hub import app as hub
from k10y_agent.hub.db import Cluster, utcnow

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)

SUMMARY_BAD = {"problems": [{"kind": "Pod", "namespace": "demo", "name": "web-1", "reason": "CrashLoopBackOff"},
                            {"kind": "Pod", "namespace": "kube-system", "name": "dns", "reason": "Pending"}],
               "cpu": {"percent": 20}, "memory": {"percent": 95}}
SUMMARY_OK = {"problems": [], "cpu": {"percent": 20}, "memory": {"percent": 40}}


def test_problem_filters():
    conds = alerts.evaluate_problems(SUMMARY_BAD, {"exclude_namespaces": ["kube-system"]})
    assert [c[0] for c in conds] == ["problem/Pod/demo/web-1/CrashLoopBackOff"]
    assert alerts.evaluate_problems(SUMMARY_BAD, {"kinds": ["Node"]}) == []


def test_resource_thresholds():
    assert alerts.evaluate_resource(SUMMARY_BAD, [], {"metric": "memory", "scope": "cluster", "threshold": 90})
    nodes = [{"name": "n1", "cpu_percent": 97, "memory_percent": 10}, {"name": "n2", "cpu_percent": 5}]
    conds = alerts.evaluate_resource({}, nodes, {"metric": "cpu", "scope": "node", "threshold": 90})
    assert [c[0] for c in conds] == ["resource/node/n1/cpu"]


def test_engine_fires_after_hold_time_then_resolves(client, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "telegram_send", lambda token, chat, text, timeout=15.0: sent.append((token, chat, text)))
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "lab", "url": "https://x:1", "token": "t"}, headers=CSRF).json()["id"]
    ch = client.post("/api/hub/alerts/channels", json={"name": "ops", "chat_id": "-100", "bot_token": "123:ABC"}, headers=CSRF)
    assert ch.status_code == 200 and ch.json()["token_set"] and "123:ABC" not in ch.text
    rule = client.post("/api/hub/alerts/rules", json={"name": "Broken pods", "type": "problems", "for_minutes": 2,
                                                       "params": {"exclude_namespaces": ["kube-system"]},
                                                       "severity": "critical", "channel_ids": [ch.json()["id"]]}, headers=CSRF)
    assert rule.status_code == 200, rule.text
    bad = client.post("/api/hub/alerts/rules", json={"name": "x", "type": "resource", "params": {"metric": "disk"}}, headers=CSRF)
    assert bad.status_code == 400

    summary = {"v": SUMMARY_BAD}
    engine = hub.alert_engine()
    engine.agent_get = lambda c, path: summary["v"] if path == "summary" else []
    t0 = utcnow()
    engine.run_once(t0)
    events = client.get("/api/hub/alerts/events").json()["events"]
    assert [e["state"] for e in events] == ["pending"] and sent == []
    engine.run_once(t0 + timedelta(minutes=1))
    assert sent == []                                                # still inside the hold time
    engine.run_once(t0 + timedelta(minutes=3))
    assert len(sent) == 1 and sent[0][:2] == ("123:ABC", "-100") and "CrashLoopBackOff" in sent[0][2]
    engine.run_once(t0 + timedelta(minutes=4))
    assert len(sent) == 1                                            # no duplicates while firing
    summary["v"] = SUMMARY_OK
    engine.run_once(t0 + timedelta(minutes=5))
    assert len(sent) == 2 and "Resolved" in sent[1][2]
    ev = client.get("/api/hub/alerts/events").json()["events"][0]
    assert ev["state"] == "resolved" and ev["cluster"] == "lab"
    assert client.get("/api/hub/alerts/events?state=firing").json()["events"] == []


def test_unreachable_cluster_and_flapping_pending_is_silent(client, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "telegram_send", lambda *a, **k: sent.append(a))
    ready_admin(client)
    client.post("/api/hub/clusters", json={"name": "edge", "url": "https://x:1", "token": "t"}, headers=CSRF)
    ch = client.post("/api/hub/alerts/channels", json={"name": "ops", "chat_id": "1", "bot_token": "b"}, headers=CSRF).json()
    client.post("/api/hub/alerts/rules", json={"name": "Down", "type": "unreachable", "for_minutes": 1,
                                               "channel_ids": [ch["id"]]}, headers=CSRF)
    engine = hub.alert_engine()
    up = {"v": False}

    def get(c, path):
        if not up["v"]:
            raise RuntimeError("connection refused")
        return SUMMARY_OK
    engine.agent_get = get
    t0 = utcnow()
    engine.run_once(t0)
    up["v"] = True
    engine.run_once(t0 + timedelta(seconds=30))                      # recovered before the hold time: no message
    assert sent == []
    up["v"] = False
    engine.run_once(t0 + timedelta(minutes=2))
    engine.run_once(t0 + timedelta(minutes=4))
    assert len(sent) == 1 and "unreachable" in sent[0][2]


def test_alert_permissions(client):
    ready_admin(client)
    viewer = next(r for r in client.get("/api/hub/roles").json() if r["name"] == "Viewer")
    u = client.post("/api/hub/users", json={"username": "viewer1", "password": "View-pass-1", "must_change_password": False,
                                        "assignments": [{"role_id": viewer["id"], "cluster_id": None}]}, headers=CSRF)
    v = TestClient(hub.app)
    assert u.status_code == 200, u.text
    lr = v.post("/api/hub/auth/login", json={"username": "viewer1", "password": "View-pass-1"})
    assert lr.status_code == 200, lr.text
    assert v.get("/api/hub/alerts/events").status_code == 200
    assert v.get("/api/hub/alerts/channels").status_code == 403
    assert v.post("/api/hub/alerts/rules", json={"name": "x", "type": "problems"}, headers=CSRF).status_code == 403
