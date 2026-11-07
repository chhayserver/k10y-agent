import httpx
from fastapi.testclient import TestClient

from k10y_agent import api as agent_api
from k10y_agent.hub import app as hub

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)


def test_forward_link_is_sandboxed_cookieless_and_permission_checked(client, monkeypatch):
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "lab", "url": "https://agent:1", "token": "t"}, headers=CSRF).json()["id"]
    seen = []

    async def fake_request(self, method, url, params=None, content=None, headers=None):
        seen.append((method, url, headers))
        return httpx.Response(200, headers={"content-type": "text/html", "set-cookie": "evil=1"}, content=b"<h1>app</h1>")

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    bad = client.post(f"/api/hub/clusters/{cid}/forward", json={"namespace": "demo", "service": "web/../x", "port": "80"}, headers=CSRF)
    assert bad.status_code == 422
    r = client.post(f"/api/hub/clusters/{cid}/forward", json={"namespace": "demo", "service": "web", "port": "http"}, headers=CSRF)
    assert r.status_code == 200, r.text
    link = r.json()["url"]
    anon = TestClient(hub.app)                                  # no hub cookie: the token is enough
    page = anon.get(link + "static/app.js?v=1")
    assert page.status_code == 200 and page.text == "<h1>app</h1>"
    assert page.headers["content-security-policy"].startswith("sandbox") and "allow-same-origin" not in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "no-referrer" and "set-cookie" not in page.headers
    assert seen[-1][1] == "https://agent:1/api/v1/proxy/services/demo/web/http/static/app.js"
    assert seen[-1][2]["Authorization"] == "Bearer t"
    assert anon.get("/fwd/not-a-token/").status_code == 404
    assert any(a["action"] == "open-service" for a in client.get("/api/hub/audit").json())

    # Expired links stop working.
    for v in hub.state["forwards"].values():
        v["expires"] = 0
    assert anon.get(link).status_code == 404


def test_agent_service_proxy_guards(monkeypatch):
    calls = []

    def fake_raw(method, path, query, headers, body):
        calls.append((path, method, query))
        return 200, {"Content-Type": "text/plain", "Set-Cookie": "x=1"}, b"hello"

    monkeypatch.setattr(agent_api.settings, "api_token", "")
    monkeypatch.setattr(agent_api.settings, "token_secret", "")
    monkeypatch.setattr(agent_api, "k8s_raw_request", fake_raw)
    c = TestClient(agent_api.app)
    monkeypatch.setattr(agent_api.settings, "control_enabled", False)
    assert c.get("/api/v1/proxy/services/demo/web/80/").status_code == 403
    monkeypatch.setattr(agent_api.settings, "control_enabled", True)
    monkeypatch.setattr(agent_api.settings, "protected_namespaces", "kube-system")
    assert c.get("/api/v1/proxy/services/kube-system/dns/53/").status_code == 403
    r = c.get("/api/v1/proxy/services/demo/web/80/api/x?q=1")
    assert r.status_code == 200 and r.text == "hello" and "set-cookie" not in r.headers
    assert calls[-1] == ("/api/v1/namespaces/demo/services/web:80/proxy/api/x", "GET", [("q", "1")])
