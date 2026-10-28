"""HAProxy permissions per instance: a role can be given on all of HAProxy or on one HAProxy instance, and every
HAProxy endpoint checks the instance it's about. Cluster and HAProxy scopes don't leak into each other."""
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx

from test_haproxy import FakeDataPlane
from test_haproxy_v24 import LiveFake
from test_hub import CSRF, client, login, ready_admin  # noqa: F401  (fixture)

DRAIN = {"state": "drain"}
CHANGE = {"ops": [{"op": "update", "kind": "server", "backend": "shop", "name": "shop1", "data": {"weight": 5}}], "apply": False}


def setup(client, monkeypatch):
    fakes = {"http://a:5555": LiveFake(), "http://b:5555": LiveFake()}
    monkeypatch.setattr(hx, "DataPlane", lambda url, *x, **k: fakes[url])
    ready_admin(client)
    a = client.post("/api/hub/haproxy", json={"name": "lb-a", "url": "http://a:5555", "username": "u", "password": "p", "group_name": "pair"}, headers=CSRF).json()["id"]
    b = client.post("/api/hub/haproxy", json={"name": "lb-b", "url": "http://b:5555", "username": "u", "password": "p", "group_name": "pair"}, headers=CSRF).json()["id"]
    roles = {}
    for name, perms in (("HAProxy operator", ["haproxy.view", "haproxy.operate"]),
                        ("HAProxy admin", ["haproxy.view", "haproxy.operate", "haproxy.configure", "haproxy.manage"]),
                        ("Cluster viewer", ["cluster.view"])):
        roles[name] = client.post("/api/hub/roles", json={"name": name, "permissions": perms}, headers=CSRF).json()["id"]
    return a, b, roles, fakes


def user(client, name, assignments):
    r = client.post("/api/hub/users", json={"username": name, "password": "Pass-word-12", "must_change_password": False,
                                            "assignments": assignments}, headers=CSRF)
    assert r.status_code == 200, r.text
    c = TestClient(hub.app)
    assert login(c, name, "Pass-word-12").status_code == 200
    return c


def test_role_on_one_haproxy_only_reaches_that_haproxy(client, monkeypatch):
    a, b, roles, _ = setup(client, monkeypatch)
    ola = user(client, "ola", [{"role_id": roles["HAProxy operator"], "haproxy_id": a}])
    assert [i["name"] for i in ola.get("/api/hub/haproxy").json()] == ["lb-a"]
    me = ola.get("/api/hub/auth/me").json()
    assert me["haproxy"] == [{"id": a, "name": "lb-a", "permissions": ["haproxy.operate", "haproxy.view"]}]
    assert me["global_permissions"] == [] and me["clusters"] == []
    assert ola.get(f"/api/hub/haproxy/{a}/overview").status_code == 200
    assert ola.get(f"/api/hub/haproxy/{b}/overview").status_code == 403
    assert ola.put(f"/api/hub/haproxy/{a}/servers/shop/shop1/state", json=DRAIN, headers=CSRF).status_code == 200
    assert ola.put(f"/api/hub/haproxy/{b}/servers/shop/shop1/state", json=DRAIN, headers=CSRF).status_code == 403
    assert ola.post(f"/api/hub/haproxy/{a}/changes", json=CHANGE, headers=CSRF).status_code == 403        # no configure
    assert ola.get(f"/api/hub/haproxy/{a}/config").status_code == 403                                     # raw config
    assert ola.patch(f"/api/hub/haproxy/{a}", json={"name": "x", "url": "http://a:5555", "username": "u"}, headers=CSRF).status_code == 403
    assert ola.post(f"/api/hub/haproxy/{a}/stick-tables/web/reset", json={"key": "203.0.113.9"}, headers=CSRF).status_code == 200
    # the first block changes the config, so operate isn't enough; once set up, it is
    r = ola.post(f"/api/hub/haproxy/{a}/blocklist", json={"ip": "203.0.113.7"}, headers=CSRF)
    assert r.status_code == 403 and "haproxy.configure" in r.json()["detail"]
    assert client.post(f"/api/hub/haproxy/{a}/blocklist", json={"ip": "203.0.113.7"}, headers=CSRF).json()["reloaded"]
    assert ola.post(f"/api/hub/haproxy/{a}/blocklist", json={"ip": "198.51.100.1"}, headers=CSRF).json()["reloaded"] is False


def test_haproxy_admin_on_one_instance(client, monkeypatch):
    a, b, roles, _ = setup(client, monkeypatch)
    ben = user(client, "ben", [{"role_id": roles["HAProxy admin"], "haproxy_id": b}])
    assert ben.post(f"/api/hub/haproxy/{b}/changes", json=CHANGE, headers=CSRF).status_code == 200
    assert ben.post(f"/api/hub/haproxy/{a}/changes", json=CHANGE, headers=CSRF).status_code == 403
    assert ben.patch(f"/api/hub/haproxy/{b}", json={"name": "lb-b", "url": "http://b:5555", "username": "u", "group_name": "pair"}, headers=CSRF).status_code == 200
    assert ben.delete(f"/api/hub/haproxy/{a}", headers=CSRF).status_code == 403
    # adding a new instance needs the role on all of HAProxy
    r = ben.post("/api/hub/haproxy", json={"name": "lb-c", "url": "http://c:5555", "username": "u", "password": "p"}, headers=CSRF)
    assert r.status_code == 403 and "all HAProxy" in r.json()["detail"]
    # one change on both members of the group is refused, naming the one without access
    r = ben.post("/api/hub/haproxy/group-changes", json={**CHANGE, "instance_ids": [b, a]}, headers=CSRF)
    assert r.status_code == 403 and "lb-a" in r.json()["detail"]
    # group view and compare only show what ben may see
    assert ben.get(f"/api/hub/haproxy/{b}/overview").json()["group"] == []
    assert ben.get(f"/api/hub/haproxy/{b}/group-drift").json()["others"] == []


def test_scopes_do_not_leak_and_assignments_go_with_the_instance(client, monkeypatch):
    a, b, roles, _ = setup(client, monkeypatch)
    # cluster permissions in a role given on one HAProxy don't count; HAProxy permissions given on one cluster don't either
    cid = client.post("/api/hub/clusters", json={"name": "c1", "url": "https://c1:8080", "token": "t"}, headers=CSRF).json().get("id")
    eve = user(client, "eve", [{"role_id": roles["Cluster viewer"], "haproxy_id": a}]
                              + ([{"role_id": roles["HAProxy operator"], "cluster_id": cid}] if cid else []))
    me = eve.get("/api/hub/auth/me").json()
    assert me["clusters"] == [] and me["haproxy"] == []
    assert eve.get("/api/hub/haproxy").status_code == 403                 # no HAProxy role anywhere
    assert client.post("/api/hub/users", json={"username": "bad", "password": "Pass-word-12", "assignments": [
        {"role_id": roles["Cluster viewer"], "cluster_id": cid or 1, "haproxy_id": a}]}, headers=CSRF).status_code == 400
    # a role on all of HAProxy reaches every instance
    max_ = user(client, "max", [{"role_id": roles["HAProxy operator"]}])
    assert sorted(i["name"] for i in max_.get("/api/hub/haproxy").json()) == ["lb-a", "lb-b"]
    # deleting an instance removes the roles given on it
    user(client, "ola", [{"role_id": roles["HAProxy operator"], "haproxy_id": a}])
    assert client.delete(f"/api/hub/haproxy/{a}", headers=CSRF).status_code == 200
    users = {u["username"]: u for u in client.get("/api/hub/users").json()}
    assert users["ola"]["assignments"] == []


def test_permission_catalog_has_an_haproxy_section(client):
    ready_admin(client)
    cat = client.get("/api/hub/permissions").json()
    assert set(cat["haproxy"]) == {"haproxy.view", "haproxy.operate", "haproxy.configure", "haproxy.manage"}
    assert "haproxy.view" not in cat["global"] and "clusters.manage" in cat["global"]
    admin = next(r for r in client.get("/api/hub/roles").json() if r["name"] == "Admin")
    assert "haproxy.manage" in admin["permissions"]
