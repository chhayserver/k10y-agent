import pytest
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub.permissions import required_permission

CSRF = {"X-K10Y-CSRF": "1"}
ADMIN_PW = "Admin-pass-1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hub.settings, "admin_password", ADMIN_PW)
    monkeypatch.setattr(hub.settings, "admin_user", "admin")
    hub.state.clear()
    hub.limiter.fails.clear()
    hub.init(f"sqlite:///{tmp_path / 'hub.db'}", secret="test-secret", data_dir=str(tmp_path))
    return TestClient(hub.app)


def login(c, user="admin", pw=ADMIN_PW):
    return c.post("/api/hub/auth/login", json={"username": user, "password": pw})


def ready_admin(c):
    assert login(c).status_code == 200
    r = c.post("/api/hub/auth/password", json={"current_password": ADMIN_PW, "new_password": "New-admin-2"}, headers=CSRF)
    assert r.status_code == 200, r.text
    return c


def test_login_requires_password_change_first(client):
    assert client.get("/api/hub/auth/me").status_code == 401
    r = login(client)
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    assert client.get("/api/hub/users").status_code == 403          # blocked until password changed
    ready_admin_pw = client.post("/api/hub/auth/password", json={"current_password": ADMIN_PW, "new_password": "short"},
                                 headers=CSRF)
    assert ready_admin_pw.status_code == 400                         # policy enforced
    assert client.post("/api/hub/auth/password", json={"current_password": ADMIN_PW, "new_password": "New-admin-2"},
                       headers=CSRF).status_code == 200
    assert client.get("/api/hub/users").status_code == 200


def test_bad_logins_are_rate_limited_and_audited(client):
    for _ in range(5):
        assert login(client, pw="wrong").status_code == 401
    assert login(client, pw="wrong").status_code == 429
    assert login(client).status_code == 429                          # even the right password, until the window passes


def test_mutations_need_csrf_header(client):
    ready_admin(client)
    r = client.post("/api/hub/roles", json={"name": "x", "permissions": []})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


def test_custom_role_scoped_to_one_cluster(client):
    ready_admin(client)
    a = client.post("/api/hub/clusters", json={"name": "a", "url": "https://127.0.0.1:1", "token": "t-a"}, headers=CSRF).json()
    b = client.post("/api/hub/clusters", json={"name": "b", "url": "https://127.0.0.1:2", "token": "t-b"}, headers=CSRF).json()
    role = client.post("/api/hub/roles", json={"name": "Scaler", "description": "view + scale",
                                               "permissions": ["cluster.view", "workloads.scale"]}, headers=CSRF)
    assert role.status_code == 200, role.text
    assert client.post("/api/hub/roles", json={"name": "Bad", "permissions": ["nope"]}, headers=CSRF).status_code == 400
    u = client.post("/api/hub/users", json={"username": "dev", "password": "Dev-pass-1", "must_change_password": False,
                                            "assignments": [{"role_id": role.json()["id"], "cluster_id": a["id"]}]},
                    headers=CSRF)
    assert u.status_code == 200, u.text

    dev = TestClient(hub.app)
    me = login(dev, "dev", "Dev-pass-1").json()
    assert [c["name"] for c in me["clusters"]] == ["a"]               # cluster b is invisible
    assert me["global_permissions"] == []
    assert dev.get("/api/hub/users").status_code == 403
    # Allowed on a (agent unreachable -> 502), forbidden on b (404 hides it), forbidden action -> 403.
    assert dev.post(f"/api/hub/clusters/{a['id']}/api/deployments/x/y/scale", json={"replicas": 1}, headers=CSRF).status_code == 502
    assert dev.post(f"/api/hub/clusters/{b['id']}/api/deployments/x/y/scale", json={"replicas": 1}, headers=CSRF).status_code == 404
    assert dev.delete(f"/api/hub/clusters/{a['id']}/api/pods/x/y", headers=CSRF).status_code == 403
    assert dev.get(f"/api/hub/clusters/{a['id']}/api/resource?kind=Secret&namespace=x&name=y").status_code == 403

    audit = client.get("/api/hub/audit").json()
    scale = next(e for e in audit if e["username"] == "dev" and e["action"] == "scale")
    assert scale["cluster"] == "a" and scale["ok"] is False


def test_cannot_remove_last_admin(client):
    ready_admin(client)
    me = client.get("/api/hub/auth/me").json()
    admin_id = me["user"]["id"]
    assert client.delete(f"/api/hub/users/{admin_id}", headers=CSRF).status_code == 400
    assert client.patch(f"/api/hub/users/{admin_id}", json={"assignments": []}, headers=CSRF).status_code == 400
    roles = client.get("/api/hub/roles").json()
    admin_role = next(r for r in roles if r["name"] == "Admin")
    assert admin_role["locked"] is True
    assert client.delete(f"/api/hub/roles/{admin_role['id']}", headers=CSRF).status_code == 400


def test_cluster_tokens_are_encrypted_at_rest(client):
    ready_admin(client)
    client.post("/api/hub/clusters", json={"name": "a", "url": "https://x:1", "token": "super-secret-token"}, headers=CSRF)
    with hub.state["db"].session() as s:
        from k10y_agent.hub.db import Cluster
        row = s.query(Cluster).one()
        assert "super-secret-token" not in row.token_encrypted
        assert hub.crypto().decrypt(row.token_encrypted) == "super-secret-token"
    listing = client.get("/api/hub/clusters").text
    assert "super-secret-token" not in listing


@pytest.mark.parametrize("method,path,perm", [
    ("GET", "pods", "cluster.view"),
    ("GET", "secrets", "secrets.view"),
    ("GET", "pods/ns/p/logs", "logs.view"),
    ("GET", "ai/config", None),
    ("POST", "deployments/ns/d/scale", "workloads.scale"),
    ("POST", "statefulsets/ns/s/scale", "workloads.scale"),
    ("POST", "daemonsets/ns/d/restart", "workloads.restart"),
    ("POST", "nodes/n/drain", "nodes.manage"),
    ("POST", "apply", "apply.yaml"),
    ("DELETE", "secrets/ns/s", "config.delete"),
    ("DELETE", "jobs/ns/j", "workloads.delete"),
    ("POST", "ai/config", None),
    ("DELETE", "nodes/n", None),
])
def test_proxy_permission_map(method, path, perm):
    assert required_permission(method, path) == perm
