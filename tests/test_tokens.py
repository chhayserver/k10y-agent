from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub

from test_hub import CSRF, ready_admin, client  # noqa: F401  (fixture)


def test_api_token_acts_as_user_limited_by_role_and_can_be_revoked(client):
    ready_admin(client)
    roles = {r["name"]: r["id"] for r in client.get("/api/hub/roles").json()}
    full = client.post("/api/hub/auth/tokens", json={"name": "ci", "expires_days": 30}, headers=CSRF).json()
    viewer = client.post("/api/hub/auth/tokens", json={"name": "ro", "expires_days": 0, "role_id": roles["Kubernetes viewer"]},
                         headers=CSRF).json()
    assert full["token"].startswith("k10y_") and full["prefix"] == full["token"][:11] and viewer["expires_at"] is None
    assert "token" not in client.get("/api/hub/auth/tokens").json()[0]          # shown once only

    api = TestClient(hub.app)                                                    # no cookie, no CSRF header
    h = {"Authorization": f"Bearer {full['token']}"}
    assert api.get("/api/hub/auth/me", headers=h).json()["user"]["username"] == "admin"
    made = api.post("/api/hub/clusters", json={"name": "lab", "url": "https://x:1", "token": "t"}, headers=h)
    assert made.status_code == 200, made.text
    assert api.post("/api/hub/auth/tokens", json={"name": "x"}, headers=h).status_code == 403   # tokens can't mint tokens

    ro = {"Authorization": f"Bearer {viewer['token']}"}
    assert api.get("/api/hub/clusters", headers=ro).status_code == 200
    assert api.post("/api/hub/clusters", json={"name": "x", "url": "https://y:1", "token": "t"}, headers=ro).status_code == 403
    audit = client.get("/api/hub/audit").json()
    assert any(a["action"] == "cluster-create" and (a.get("params") or {}).get("via_token") == "ci" for a in audit), audit[:3]

    assert client.delete(f"/api/hub/auth/tokens/{full['id']}", headers=CSRF).status_code == 200
    assert api.get("/api/hub/auth/me", headers=h).status_code == 401
    assert api.get("/api/hub/auth/me", headers={"Authorization": "Bearer k10y_wrong"}).status_code == 401
