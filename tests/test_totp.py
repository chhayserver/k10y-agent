import time

import pytest
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub.security import totp_code, totp_match

CSRF = {"X-K10Y-CSRF": "1"}
PW = "New-admin-2"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hub.settings, "admin_password", "Admin-pass-1")
    monkeypatch.setattr(hub.settings, "admin_user", "admin")
    hub.state.clear()
    hub.limiter.fails.clear()
    hub.init(f"sqlite:///{tmp_path / 'hub.db'}", secret="test-secret", data_dir=str(tmp_path))
    c = TestClient(hub.app)
    c.post("/api/hub/auth/login", json={"username": "admin", "password": "Admin-pass-1"})
    assert c.post("/api/hub/auth/password", json={"current_password": "Admin-pass-1", "new_password": PW}, headers=CSRF).status_code == 200
    return c


def code_for(secret, offset=0):
    return totp_code(secret, int(time.time() // 30) + offset)


def test_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" (base32 below), T = 59 s -> 94287082 (last 6 digits: 287082).
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, 59 // 30) == "287082"
    assert totp_match(secret, "287 082", now=59) == 1
    assert totp_match(secret, "287082", last_step=1, now=59) is None   # same code can't be used twice


def enable(c):
    setup = c.post("/api/hub/auth/totp/setup", json={"password": PW}, headers=CSRF)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["uri"].startswith("otpauth://totp/k10y:admin?secret=")
    assert c.post("/api/hub/auth/totp/enable", json={"code": "000000" if code_for(secret) != "000000" else "111111"}, headers=CSRF).status_code == 400
    r = c.post("/api/hub/auth/totp/enable", json={"code": code_for(secret)}, headers=CSRF)
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def test_login_needs_code_once_enabled(client):
    assert client.post("/api/hub/auth/totp/setup", json={"password": "wrong"}, headers=CSRF).status_code == 400
    secret, recovery = enable(client)
    assert len(recovery) == 10 and client.get("/api/hub/auth/me").json()["user"]["totp_enabled"] is True
    client.post("/api/hub/auth/logout", headers=CSRF)

    fresh = TestClient(hub.app)
    r = fresh.post("/api/hub/auth/login", json={"username": "admin", "password": PW})
    assert r.status_code == 200 and r.json() == {"totp_required": True}
    assert fresh.get("/api/hub/auth/me").status_code == 401          # no session without the code
    assert fresh.post("/api/hub/auth/login", json={"username": "admin", "password": PW, "code": "12345"}).status_code == 401
    # The code used to enable can't be replayed; the next one (or a drifted one) works.
    r = fresh.post("/api/hub/auth/login", json={"username": "admin", "password": PW, "code": code_for(secret, 1)})
    assert r.status_code == 200 and r.json()["user"]["username"] == "admin"

    # A recovery code works exactly once.
    other = TestClient(hub.app)
    assert other.post("/api/hub/auth/login", json={"username": "admin", "password": PW, "code": recovery[0].upper()}).status_code == 200
    again = TestClient(hub.app)
    assert again.post("/api/hub/auth/login", json={"username": "admin", "password": PW, "code": recovery[0]}).status_code == 401
    assert fresh.get("/api/hub/auth/totp").json()["recovery_codes_left"] == 9
    actions = [a["action"] for a in fresh.get("/api/hub/audit").json()]
    assert "totp-enable" in actions and "totp-recovery-used" in actions


def test_policy_forces_setup_and_admin_reset(client):
    # Can't require two-factor before having it yourself.
    assert client.put("/api/hub/settings/security", json={"require_2fa": "everyone"}, headers=CSRF).status_code == 400
    r = client.post("/api/hub/users", json={"username": "bob", "display_name": "Bob", "password": "Bob-pass-12",
                                            "must_change_password": False, "assignments": []}, headers=CSRF)
    assert r.status_code == 200, r.text
    bob_id = r.json()["id"]
    enable(client)
    assert client.put("/api/hub/settings/security", json={"require_2fa": "everyone"}, headers=CSRF).status_code == 200

    bob = TestClient(hub.app)
    me = bob.post("/api/hub/auth/login", json={"username": "bob", "password": "Bob-pass-12"}).json()
    assert me["user"]["must_setup_2fa"] is True
    assert bob.get("/api/hub/alerts/rules").status_code == 403       # blocked until set up
    assert bob.post("/api/hub/auth/totp/disable", json={"password": "Bob-pass-12"}, headers=CSRF).status_code == 403
    setup = bob.post("/api/hub/auth/totp/setup", json={"password": "Bob-pass-12"}, headers=CSRF).json()
    done = bob.post("/api/hub/auth/totp/enable", json={"code": code_for(setup["secret"])}, headers=CSRF).json()
    assert done["me"]["user"]["must_setup_2fa"] is False
    assert bob.post("/api/hub/auth/totp/disable", json={"password": "Bob-pass-12"}, headers=CSRF).status_code == 400  # required

    reset = client.post(f"/api/hub/users/{bob_id}/totp/reset", headers=CSRF)
    assert reset.status_code == 200 and reset.json()["totp_enabled"] is False
    assert bob.get("/api/hub/auth/me").json()["user"]["must_setup_2fa"] is True
