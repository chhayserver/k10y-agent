from fastapi.testclient import TestClient

from k10y_agent import api


def test_token_check_works_on_every_route(monkeypatch):
    monkeypatch.setattr(api.settings, "api_token", "t0ken")
    client = TestClient(api.app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/audit").status_code == 401
    ok = client.get("/api/v1/audit", headers={"Authorization": "Bearer t0ken"})
    assert ok.status_code == 200, ok.text


def test_module_names_not_shadowed():
    import secrets

    assert api.secrets is secrets


def test_cors_preflight_allows_dashboard_on_other_origin():
    client = TestClient(api.app)
    r = client.options("/api/v1/summary", headers={
        "Origin": "http://192.168.64.2:30800",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-k10y-actor",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "http://192.168.64.2:30800")


def test_dashboard_mode_serves_ui_but_no_cluster_api(monkeypatch):
    monkeypatch.setattr(api.settings, "mode", "dashboard")
    monkeypatch.setattr(api.settings, "api_token", "")
    client = TestClient(api.app)
    page = client.get("/")
    assert page.status_code == 200
    assert '<meta name="k10y-mode" content="dashboard">' in page.text
    assert client.get("/api/v1/summary").status_code == 404


def test_fails_closed_when_token_secret_cannot_be_read(monkeypatch):
    monkeypatch.setattr(api.settings, "api_token", "")
    monkeypatch.setattr(api.settings, "token_secret", "k10y-agent")
    monkeypatch.setattr(api, "NAMESPACE_FILE", api.Path("/nonexistent/namespace"))
    r = TestClient(api.app).get("/api/v1/audit")
    assert r.status_code == 503
    assert "API token not available" in r.json()["detail"]


def test_token_created_then_reused(monkeypatch, tmp_path):
    import base64 as b64
    from kubernetes.client.rest import ApiException

    store = {}

    class FakeCore:
        def read_namespaced_secret(self, name, ns):
            if name not in store:
                raise ApiException(status=404)
            return type("S", (), {"data": {"api-token": b64.b64encode(store[name].encode()).decode()}})()

        def create_namespaced_secret(self, ns, body):
            store[body.metadata.name] = body.string_data["api-token"]

    nsfile = tmp_path / "namespace"
    nsfile.write_text("k10y")
    monkeypatch.setattr(api, "NAMESPACE_FILE", nsfile)
    monkeypatch.setattr(api.client, "CoreV1Api", lambda *_: FakeCore())
    monkeypatch.setattr(api, "get_api_client", lambda: None)
    monkeypatch.setattr(api.settings, "token_secret", "k10y-agent")
    for _ in range(2):
        monkeypatch.setattr(api.settings, "api_token", "")
        api.provision_token()
        assert api.settings.api_token == store["k10y-agent"] and len(store["k10y-agent"]) == 48
    assert len(store) == 1
