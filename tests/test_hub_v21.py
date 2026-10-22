"""2.1: personal preferences, sessions, user lock-out, YAML edit permission, pod shell auth, DB migration."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine, inspect, text

from k10y_agent.hub import app as hub
from k10y_agent.hub.db import Database
from k10y_agent.hub.permissions import required_permission

from test_hub import ADMIN_PW, CSRF, client, login, ready_admin  # noqa: F401  (fixture)


def test_preferences_follow_the_user(client):
    ready_admin(client)
    assert client.get("/api/hub/auth/preferences").json() == {"data": None}
    prefs = {"theme": "dark", "hidden": ["map"], "start": "pods"}
    assert client.put("/api/hub/auth/preferences", json=prefs, headers=CSRF).status_code == 200
    other = TestClient(hub.app)                                       # another device
    assert login(other, pw="New-admin-2").status_code == 200
    assert other.get("/api/hub/auth/preferences").json()["data"] == prefs
    assert client.put("/api/hub/auth/preferences", content="x" * 20000, headers=CSRF).status_code == 413
    assert client.put("/api/hub/auth/preferences", json=[1], headers=CSRF).status_code == 400


def test_sessions_list_and_sign_out_elsewhere(client):
    ready_admin(client)
    phone = TestClient(hub.app)
    login(phone, pw="New-admin-2")
    mine = client.get("/api/hub/auth/sessions").json()
    assert len(mine) == 2 and sum(x["current"] for x in mine) == 1
    other = next(x for x in mine if not x["current"])
    assert client.delete(f"/api/hub/auth/sessions/{other['id']}", headers=CSRF).status_code == 200
    assert phone.get("/api/hub/auth/me").status_code == 401           # that device is signed out
    login(phone, pw="New-admin-2")
    assert client.post("/api/hub/auth/sessions/revoke-others", json={}, headers=CSRF).json()["revoked"] == 1
    assert client.get("/api/hub/auth/me").status_code == 200          # current session kept


def test_admin_signs_out_and_disables_a_user(client):
    ready_admin(client)
    viewer = next(r for r in client.get("/api/hub/roles").json() if r["name"] == "Viewer")
    u = client.post("/api/hub/users", json={"username": "bob", "password": "Bob-pass-12", "must_change_password": False,
                                            "assignments": [{"role_id": viewer["id"], "cluster_id": None}]}, headers=CSRF).json()
    bob = TestClient(hub.app)
    login(bob, "bob", "Bob-pass-12")
    users = {x["username"]: x for x in client.get("/api/hub/users").json()}
    assert users["bob"]["sessions"] == 1 and users["bob"]["last_seen_at"]
    assert len(client.get(f"/api/hub/users/{u['id']}/sessions").json()) == 1
    assert client.post(f"/api/hub/users/{u['id']}/sessions/revoke", json={}, headers=CSRF).json()["revoked"] == 1
    assert bob.get("/api/hub/auth/me").status_code == 401
    client.patch(f"/api/hub/users/{u['id']}", json={"is_active": False}, headers=CSRF)
    assert login(bob, "bob", "Bob-pass-12").status_code == 401        # locked


def test_edit_and_exec_permissions():
    assert required_permission("PUT", "resource") == "resources.edit"
    assert required_permission("PUT", "deployments/ns/x") is None


def test_pod_shell_requires_login_same_origin_and_permission(client):
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "a", "url": "https://127.0.0.1:1", "token": "t"}, headers=CSRF).json()["id"]
    path = f"/api/hub/clusters/{cid}/exec/demo/web-1"
    anon = TestClient(hub.app)
    with pytest.raises(WebSocketDisconnect) as e:
        with anon.websocket_connect(path, headers={"origin": "http://testserver"}):
            pass
    assert e.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as e:                     # logged in, but another site's page
        with client.websocket_connect(path, headers={"origin": "https://evil.example"}):
            pass
    assert e.value.code == 4403
    role = client.post("/api/hub/roles", json={"name": "Viewer2", "permissions": ["cluster.view"]}, headers=CSRF).json()
    client.post("/api/hub/users", json={"username": "carol", "password": "Carol-pass-1", "must_change_password": False,
                                        "assignments": [{"role_id": role["id"], "cluster_id": cid}]}, headers=CSRF)
    carol = TestClient(hub.app)
    login(carol, "carol", "Carol-pass-1")
    with pytest.raises(WebSocketDisconnect) as e:
        with carol.websocket_connect(path, headers={"origin": "http://testserver"}):
            pass
    assert e.value.code == 4403
    # Admin is allowed; the agent is unreachable, so the hub reports it in the terminal and audits the attempt.
    with client.websocket_connect(path, headers={"origin": "http://testserver"}) as ws:
        assert "Cannot open a shell" in ws.receive_text()
    entry = next(a for a in client.get("/api/hub/audit").json() if a["action"] == "exec")
    assert entry["ok"] is False and entry["target"] == "pod/demo/web-1"


def test_old_database_gets_new_session_column(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    eng = create_engine(url)
    with eng.begin() as c:   # sessions table as shipped in 2.0
        c.execute(text("CREATE TABLE sessions (id INTEGER PRIMARY KEY, token_hash VARCHAR(64), user_id INTEGER, "
                       "created_at TIMESTAMP, expires_at TIMESTAMP, ip VARCHAR(64), user_agent VARCHAR(255))"))
    Database(url).create_all()
    assert "last_seen_at" in {c["name"] for c in inspect(eng).get_columns("sessions")}
    assert "user_preferences" in inspect(eng).get_table_names()


def test_pod_shell_relays_both_ways(client, monkeypatch):
    import asyncio
    sent = []

    class FakeAgentWS:
        close_reason = ""

        def __init__(self):
            self.q = asyncio.Queue()
            self.q.put_nowait("root@pod:/# ")

        async def send(self, msg):
            sent.append(msg)
            await self.q.put("hello-42\r\nroot@pod:/# ")

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.q.get()

        async def close(self):
            pass

    async def fake_connect(url, **kw):
        assert url.startswith("wss://127.0.0.1:1/api/v1/pods/demo/web-1/exec")
        assert kw["additional_headers"]["Authorization"] == "Bearer t"
        return FakeAgentWS()

    monkeypatch.setattr(hub, "ws_connect", fake_connect)
    ready_admin(client)
    cid = client.post("/api/hub/clusters", json={"name": "a", "url": "https://127.0.0.1:1", "token": "t"}, headers=CSRF).json()["id"]
    with client.websocket_connect(f"/api/hub/clusters/{cid}/exec/demo/web-1", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_text() == "root@pod:/# "
        ws.send_text('{"type": "input", "data": "echo hi\\n"}')
        assert "hello-42" in ws.receive_text()
    assert sent == ['{"type": "input", "data": "echo hi\\n"}']
