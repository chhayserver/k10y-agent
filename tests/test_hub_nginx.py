from fastapi.testclient import TestClient

from k10y_agent.hub import alerts
from k10y_agent.hub import app as hub
from k10y_agent.hub import nginx

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)

SUMMARY = {"sites": 1, "upstreams": 1, "servers": 2, "req": 10.0, "e5_pct": 12.0, "p95": 40.0, "active": 3,
           "problems": [{"kind": "server_down", "upstream": "shop", "server": "shop2:80", "message": "shop2:80 in shop doesn't answer: refused"},
                        {"kind": "cert_expiry", "path": "/etc/nginx/certs/shop.pem", "days_left": 5, "message": "certificate shop.pem expires in 5 days"}]}


def fake_call(url, token, verify, path, params=None):
    assert token == "agent-token"
    return {"info": {"nginx_version": "1.28.0", "running": True}, "config": {"sites": [], "upstreams": []}, "summary": SUMMARY,
            "upstreams/health": {}, "config/raw": {"text": "http {}"}, "stats": {"points": []}}[path]


def test_agent_url():
    assert nginx.agent_url("web1") == "https://web1:9443"
    assert nginx.agent_url("10.0.0.5:9443/") == "https://10.0.0.5:9443"
    assert nginx.agent_url("http://web1:8080") == "http://web1:8080"


def test_instances_roles_and_reads(client, monkeypatch):
    monkeypatch.setattr(nginx, "call", fake_call)
    ready_admin(client)
    made = client.post("/api/hub/nginx", json={"name": "web1", "url": "web1", "token": "agent-token"}, headers=CSRF)
    assert made.status_code == 200, made.text
    assert "agent-token" not in made.text and made.json()["url"] == "https://web1:9443"
    nid = made.json()["id"]
    other = client.post("/api/hub/nginx", json={"name": "web2", "url": "web2", "token": "agent-token"}, headers=CSRF).json()["id"]
    ov = client.get(f"/api/hub/nginx/{nid}/overview").json()
    assert ov["info"]["nginx_version"] == "1.28.0" and ov["summary"]["servers"] == 2

    # An "Nginx viewer" on web1 only: sees web1, not web2; can't read the raw config.
    roles = {r["name"]: r["id"] for r in client.get("/api/hub/roles").json()}
    assert {"Nginx viewer", "Nginx operator", "Nginx admin"} <= set(roles)
    client.post("/api/hub/users", json={"username": "ops", "password": "Ops-pass-123", "must_change_password": False,
                                        "assignments": [{"role_id": roles["Nginx viewer"], "nginx_id": nid}]}, headers=CSRF)
    ops = TestClient(hub.app)
    me = ops.post("/api/hub/auth/login", json={"username": "ops", "password": "Ops-pass-123"}).json()
    assert [i["name"] for i in me["nginx"]] == ["web1"] and me["global_permissions"] == []
    assert [i["name"] for i in ops.get("/api/hub/nginx").json()] == ["web1"]
    assert ops.get(f"/api/hub/nginx/{nid}/summary").status_code == 200
    assert ops.get(f"/api/hub/nginx/{other}/summary").status_code == 403
    assert ops.get(f"/api/hub/nginx/{nid}/config/raw").status_code == 403
    assert client.get(f"/api/hub/nginx/{nid}/config/raw").json()["text"] == "http {}"
    assert ops.post("/api/hub/nginx", json={"name": "x", "url": "x", "token": "t"}, headers=CSRF).status_code == 403


def test_nginx_alert_conditions():
    keys = lambda c, p=None: [x[0] for x in alerts.evaluate_nginx(7, SUMMARY, {"condition": c, **(p or {})})]
    assert keys("server_down") == ["ng/7/server_down/shop/shop2:80"]
    assert keys("upstream_down") == []
    assert keys("error_rate", {"threshold": 10}) == ["ng/7/5xx"] and keys("error_rate", {"threshold": 20}) == []
    assert keys("cert_expiry", {"threshold": 14}) == ["ng/7/cert//etc/nginx/certs/shop.pem"]
    assert alerts.evaluate_nginx(7, RuntimeError("down"), {"condition": "unreachable"})[0][0] == "ng/7/unreachable"
    assert alerts.evaluate_nginx(7, RuntimeError("down"), {"condition": "server_down"}) is None
