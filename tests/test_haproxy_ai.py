"""HAProxy AI assistant: tools are read-only and redact secrets; proposed fixes are checked before review."""
from k10y_agent import ai
from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx
from k10y_agent.hub import haproxy_ai

from test_haproxy_more import FakeDP
from test_hub import CSRF, client, login, ready_admin  # noqa: F401  (fixture)


class FakeChat:
    """Calls two tools, then submits a diagnosis with good and bad proposals."""
    usage = {"input_tokens": 10, "output_tokens": 5}

    def __init__(self):
        self.turn, self.seen = 0, []

    def user(self, text):
        self.task = text

    def step(self):
        self.turn += 1
        if self.turn == 1:
            return [{"name": "get_overview", "args": {}}, {"name": "get_config_section", "args": {"name": "shop"}}], ""
        return [{"name": "submit_diagnosis", "args": {"summary": "shop1 is fine", "issues": [{"title": "x", "severity": "info", "root_cause": "y"}],
            "actions": [
                {"type": "set_weight", "backend": "shop", "server": "shop1", "weight": 50, "reason": "canary", "risk": "low"},
                {"type": "set_server_state", "backend": "shop", "server": "shop1", "state": "drain", "reason": "r", "risk": "low"},
                {"type": "update_backend", "backend": "shop", "check_type": "http", "check_uri": "/health", "reason": "r", "risk": "low"},
                {"type": "set_weight", "backend": "ghost", "server": "a", "weight": 1, "reason": "r", "risk": "low"}]}}], ""

    def results(self, outputs):
        self.seen += outputs


def test_tools_redact_and_filter():
    dp = FakeDP()
    dp.raw = lambda tx=None: "global\n  maxconn 100\nuserlist ops\n  user admin password $6$abc\nbackend shop\n  balance roundrobin\n  server s1 1.2.3.4:80 check\n"
    logs = [{"status": 200, "backend": "shop", "msg": "ok"}, {"status": 503, "backend": "shop", "term": "SC--", "msg": "x"},
            {"msg": "Server shop/s1 is DOWN"}]
    tools = haproxy_ai.HAProxyTools(dp, None, [], logs, [])
    assert "1.2.3.4:80" in tools.t_get_config_section("shop")
    assert "$6$abc" not in tools.t_get_config_section("ops") and "removed" in tools.t_get_config_section("ops")
    errs = tools.t_get_recent_logs(status="errors")
    assert [l["status"] for l in errs["lines"]] == [503] and errs["haproxy_events"] == ["Server shop/s1 is DOWN"]
    assert "note" in tools.t_get_live_rates()


def test_fixes_are_checked_and_turned_into_reviewable_changes(monkeypatch):
    chat = FakeChat()
    monkeypatch.setattr(ai, "make_chat", lambda cfg, system, tools=None: chat)
    tools = haproxy_ai.HAProxyTools(FakeDP(), None, [], [], [])
    res = haproxy_ai.run({"provider": "gemini", "model": "m"}, tools, "backend", "shop", "why slow?")
    assert "backend 'shop'" in chat.task and "why slow?" in chat.task
    assert [o[0]["name"] for o in chat.seen] == ["get_overview", "get_config_section"]
    weight, drain, check, ghost = res["actions"]
    assert weight["blocked"] is None and weight["ops"][0]["data"] == {"weight": 50}
    assert drain["blocked"] == "it would take the last healthy server out of rotation"   # shop1 is the only server
    assert check["ops"][0]["data"]["check"] == {"type": "http", "uri": "/health"}
    assert "does not exist" in ghost["blocked"]
    # the ops are valid k10y operations: they preview cleanly
    assert hx.plan_and_apply(FakeDP(), [hx.Op(**weight["ops"][0])], apply=False)["valid"]


def test_endpoint_needs_ai_permission_and_is_audited(client, monkeypatch):
    dp = FakeDP()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: dp)
    monkeypatch.setattr(ai, "make_chat", lambda cfg, system, tools=None: FakeChat())
    ready_admin(client)
    iid = client.post("/api/hub/haproxy", json={"name": "lb", "url": "http://lb:5555", "username": "a", "password": "p"}, headers=CSRF).json()["id"]
    r = client.post(f"/api/hub/haproxy/{iid}/ai/diagnose", json={"scope": "instance"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["summary"] == "shop1 is fine"
    assert any(a["action"] == "ai-diagnose" and a["target"] == "haproxy/lb" for a in client.get("/api/hub/audit").json())
    role = client.post("/api/hub/roles", json={"name": "LB viewer", "permissions": ["haproxy.view", "cluster.view"]}, headers=CSRF).json()
    client.post("/api/hub/users", json={"username": "lbv", "password": "Lbv-pass-123", "must_change_password": False,
                                        "assignments": [{"role_id": role["id"], "cluster_id": None}]}, headers=CSRF)
    from fastapi.testclient import TestClient
    v = TestClient(hub.app); login(v, "lbv", "Lbv-pass-123")
    assert v.post(f"/api/hub/haproxy/{iid}/ai/diagnose", json={"scope": "instance"}, headers=CSRF).status_code == 403
    assert client.post(f"/api/hub/haproxy/{iid}/ai/diagnose", json={"scope": "backend", "target": "bad name"}, headers=CSRF).status_code == 400
