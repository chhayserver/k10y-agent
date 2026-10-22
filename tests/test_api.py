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
