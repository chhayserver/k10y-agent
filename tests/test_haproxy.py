"""HAProxy management: route parsing, change planning, runtime state, API permissions and audit."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx

from test_hub import CSRF, client, login, ready_admin  # noqa: F401  (fixture)

CFG = "/services/haproxy/configuration"
PART_KEYS = {"acls": "acl_list", "backend_switching_rules": "backend_switching_rule_list", "http_request_rules": "http_request_rule_list",
             "http_response_rules": "http_response_rule_list", "tcp_request_rules": "tcp_request_rule_list", "stick_rules": "stick_rule_list",
             "server_switching_rules": "server_switching_rule_list", "http_checks": "http_check_list", "tcp_checks": "tcp_check_rule_list"}


class FakeDataPlane:
    """In-memory stand-in for the Data Plane API v3 (just the calls k10y makes)."""

    def __init__(self):
        self.live = {
            "frontends": {"web": {"name": "web", "mode": "http", "default_backend": "shop",
                                  "binds": {"*:80": {"name": "*:80", "address": "*", "port": 80}},
                                  "acl_list": [{"acl_name": "is_api", "criterion": "path_beg", "value": "/api"}],
                                  "backend_switching_rule_list": [{"name": "api", "cond": "if", "cond_test": "is_api"}]}},
            "backends": {"shop": {"name": "shop", "balance": {"algorithm": "roundrobin"}, "adv_check": "httpchk",
                                  "httpchk_params": {"method": "GET", "uri": "/"},
                                  "servers": {"shop1": {"name": "shop1", "address": "10.0.0.1", "port": 80, "check": "enabled", "weight": 100}}},
                         "api": {"name": "api", "balance": {"algorithm": "leastconn"}, "servers": {}}},
        }
        self.version_n, self.txs, self.state, self.calls = 1, {}, {}, []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    # the methods DataPlane exposes, implemented over self.live / transactions
    def _cfg(self, tx):
        return self.txs[tx] if tx else self.live

    def version(self):
        return self.version_n

    def begin(self):
        tid = f"tx{len(self.txs) + 1}"
        self.txs[tid] = copy.deepcopy(self.live)
        return tid

    def commit(self, tx):
        self.live = self.txs.pop(tx)
        self.version_n += 1
        return "reload-1"

    def wait_reload(self, reload_id, timeout=30.0):
        self.waited = reload_id
        if getattr(self, "reload_fails", False):
            raise hx.DataPlaneError(502, "HAProxy did not reload, so the previous configuration is still active: boom")

    def put_raw(self, text):
        self.live = json.loads(text)
        self.version_n += 1
        return "reload-raw"

    def rollback(self, tx):
        self.txs.pop(tx, None)

    def frontends(self, tx=None):
        return list(copy.deepcopy(self._cfg(tx)["frontends"]).values())

    def backends(self, tx=None):
        return list(copy.deepcopy(self._cfg(tx)["backends"]).values())

    def raw(self, tx=None):
        return json.dumps(self._cfg(tx), indent=1, sort_keys=True)

    def validate(self, text):
        if '"algorithm": "nope"' in text:
            raise hx.DataPlaneError(400, "balance only supports roundrobin, ...")

    def stats(self):
        # Real shape: frontends/backends have their name in "name" and no backend_name.
        return [{"type": "frontend", "name": "web", "backend_name": None, "stats": {"status": "OPEN", "scur": 3, "req_rate": 7}},
                {"type": "backend", "name": "shop", "backend_name": None, "stats": {"status": "UP", "scur": 2, "rtime": 4}},
                {"type": "server", "name": "shop1", "backend_name": "shop", "stats": {"status": "UP", "check_status": "L7OK", "scur": 2}}]

    def info(self):
        return {"api": {"version": "v3.2.18 abc"}}

    def general_files(self):
        return list(getattr(self, "files", {}).values())

    def set_state(self, backend, server, state):
        self.state[(backend, server)] = state
        return {"admin_state": state}

    def call(self, method, path, tx=None, json=None, content=None, params=None):
        self.calls.append((method, path))
        cfg = self._cfg(tx)
        parts = path.replace(CFG + "/", "").split("/")
        if parts[0] in ("frontends", "backends") and len(parts) <= 2:
            coll = cfg[parts[0]]
            if method == "GET" and len(parts) == 2:
                return copy.deepcopy(coll.get(parts[1]))
            if method == "POST":
                coll[json["name"]] = json
            elif method == "PUT" and (params or {}).get("full_section") == "true":
                coll[parts[1]] = json
            elif method == "PUT":         # base settings only: the parts (rules, binds, servers) stay
                kept = {k: v for k, v in coll[parts[1]].items() if k.endswith("_list") or k in ("binds", "servers")}
                coll[parts[1]] = {**json, **kept}
                self.calls.append(("PUT-base", parts[1]))
            elif method == "DELETE":
                del coll[parts[1]]
            return json
        if parts[0] in ("frontends", "backends") and len(parts) == 3 and parts[2] in PART_KEYS and method == "PUT":
            if json:
                cfg[parts[0]][parts[1]][PART_KEYS[parts[2]]] = json
            else:
                cfg[parts[0]][parts[1]].pop(PART_KEYS[parts[2]], None)
            self.calls.append(("PUT-part", f"{parts[1]}/{parts[2]}"))
            return json
        if parts[0] == "frontends" and len(parts) >= 3 and parts[2] == "binds":
            binds = cfg["frontends"][parts[1]].setdefault("binds", {})
            if method == "POST":
                binds[json["name"]] = json
            elif method == "PUT":
                binds[parts[3]] = json
            elif method == "DELETE":
                del binds[parts[3]]
            return json
        if parts[0] == "backends" and parts[2] == "servers":
            servers = cfg["backends"][parts[1]].setdefault("servers", {})
            if method == "GET" and len(parts) == 3:
                return copy.deepcopy(list(servers.values()))
            if method == "GET":
                return copy.deepcopy(servers[parts[3]])
            if method == "POST":
                servers[json["name"]] = json
            elif method == "PUT":
                servers[parts[3]] = json
            elif method == "DELETE":
                del servers[parts[3]]
            return json
        raise AssertionError(f"unexpected call {method} {path}")


def test_existing_acl_names_are_reused():
    acls, rules = hx._routes_to_config([{"kind": "path_prefix", "value": "/api", "backend": "api", "style": "acl"}, {"kind": "host", "value": "a.io", "backend": "api"}],
                                       {"api"}, [{"acl_name": "is_api", "criterion": "path_beg", "value": "/api"}])
    assert [a["acl_name"] for a in acls] == ["is_api"] and rules[0]["cond_test"] == "is_api"     # existing named ACL kept
    assert rules[1]["cond_test"] == "{ hdr(host) -i a.io }"                                      # new routes are written inline


def test_routes_and_states_are_parsed():
    fe = FakeDataPlane().live["frontends"]["web"]
    routes, simple = hx.parse_routes(fe)
    assert simple and routes == [{"kind": "path_prefix", "value": "/api", "style": "acl", "backend": "api"}]
    fe["backend_switching_rule_list"].append({"name": "shop", "cond": "unless", "cond_test": "is_api"})
    assert hx.parse_routes(fe)[1] is False
    assert [hx._server_state(s) for s in ("UP", "DOWN", "MAINT", "DRAIN", "no check", "UP 1/3")] == ["up", "down", "maint", "drain", "nocheck", "up"]


def test_overview_merges_config_and_stats():
    o = hx.overview(FakeDataPlane())
    web = o["frontends"][0]
    assert web["binds"][0]["port"] == 80 and web["stats"]["req_rate"] == 7
    shop = next(b for b in o["backends"] if b["name"] == "shop")
    assert shop["used_by"] == ["web"] and shop["check"] == {"type": "http", "uri": "/", "method": "GET", "host": "", "user": ""}
    assert shop["servers"][0]["state"] == "up" and shop["servers"][0]["stats"]["check_status"] == "L7OK"
    assert shop["stats"]["rtime"] == 4
    assert o["keeps_server_state"] is False


def test_preview_does_not_change_and_apply_commits():
    dp = FakeDataPlane()
    ops = [hx.Op(op="create", kind="server", backend="shop", name="shop2", data={"address": "10.0.0.2", "port": 8080, "weight": 50, "check": True})]
    plan = hx.plan_and_apply(dp, ops, apply=False)
    assert plan["valid"] and '"shop2"' in plan["diff"] and "shop2" not in dp.live["backends"]["shop"]["servers"]
    assert dp.txs == {}                                               # transactions cleaned up
    done = hx.plan_and_apply(dp, ops, apply=True)
    assert done["applied"] and dp.live["backends"]["shop"]["servers"]["shop2"]["port"] == 8080 and dp.version_n == 2


def test_invalid_change_is_previewed_but_never_applied():
    dp = FakeDataPlane()
    bad = [hx.Op(op="update", kind="backend", name="shop", data={"balance": "roundrobin"})]
    bad[0].data["balance"] = "nope"
    with pytest.raises(hx.DataPlaneError, match="balance must be one of"):
        hx.plan_and_apply(dp, bad, apply=False)
    assert dp.txs == {}


def test_service_routes_replace_only_rule_acls_and_backend_in_use_is_protected():
    dp = FakeDataPlane()
    dp.live["frontends"]["web"]["acl_list"].append({"acl_name": "blocked_ip", "criterion": "src", "value": "10.9.9.9"})
    op = hx.Op(op="update", kind="frontend", name="web", data={
        "binds": [{"address": "*", "port": 80}, {"address": "*", "port": 8080}],
        "routes": [{"kind": "host", "value": "api.example.com", "backend": "api"}, {"kind": "path_prefix", "value": "/shop", "backend": "shop"}]})
    hx.plan_and_apply(dp, [op], apply=True)
    web = dp.live["frontends"]["web"]
    assert set(web["binds"]) == {"*:80", "*:8080"}
    assert [a["acl_name"] for a in web["acl_list"]] == ["blocked_ip"]          # unrelated ACL kept, routes written inline
    assert [r["cond_test"] for r in web["backend_switching_rule_list"]] == ["{ hdr(host) -i api.example.com }", "{ path_beg /shop }"]
    # adding a route keeps the existing ACL names, so the diff only shows the new route
    hx.plan_and_apply(dp, [hx.Op(op="update", kind="frontend", name="web", data={"routes": [
        {"kind": "host", "value": "api.example.com", "backend": "api"}, {"kind": "path_prefix", "value": "/shop", "backend": "shop"},
        {"kind": "path", "value": "/health", "backend": "shop"}]})], apply=True)
    assert [a["acl_name"] for a in dp.live["frontends"]["web"]["acl_list"]] == ["blocked_ip"]
    assert dp.live["frontends"]["web"]["backend_switching_rule_list"][2] == {"name": "shop", "cond": "if", "cond_test": "{ path /health }"}
    with pytest.raises(hx.DataPlaneError, match="still used by: web"):
        hx.plan_and_apply(dp, [hx.Op(op="delete", kind="backend", name="api")], apply=True)
    with pytest.raises(hx.DataPlaneError, match="unknown backend"):
        hx.plan_and_apply(dp, [hx.Op(op="create", kind="frontend", name="x", data={"binds": [{"port": 81}], "default_backend": "ghost"})], apply=True)
    with pytest.raises(hx.DataPlaneError, match="name must be"):
        hx.plan_and_apply(dp, [hx.Op(op="delete", kind="frontend", name="bad name")], apply=True)


def test_raw_edit_is_validated_before_apply():
    dp = FakeDataPlane()
    text = dp.raw().replace('"leastconn"', '"nope"')
    plan = hx.plan_and_apply(dp, [hx.Op(op="raw", kind="config", data={"text": text})], apply=False)
    assert plan["valid"] is False and "roundrobin" in plan["error"]
    with pytest.raises(hx.DataPlaneError, match="rejected"):
        hx.plan_and_apply(dp, [hx.Op(op="raw", kind="config", data={"text": text})], apply=True)
    good = dp.raw().replace('"leastconn"', '"roundrobin"')
    assert hx.plan_and_apply(dp, [hx.Op(op="raw", kind="config", data={"text": good})], apply=True)["applied"]
    assert dp.live["backends"]["api"]["balance"]["algorithm"] == "roundrobin"


def test_api_permissions_encryption_state_and_audit(client, monkeypatch):
    fake = FakeDataPlane()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: fake)
    ready_admin(client)
    r = client.post("/api/hub/haproxy", json={"name": "lb1", "url": "http://lb1:5555", "username": "admin", "password": "dp-secret"}, headers=CSRF)
    assert r.status_code == 200 and "dp-secret" not in r.text
    iid = r.json()["id"]
    with hub.state["db"].session() as s:
        row = s.get(hx.HAProxyInstance, iid)
        assert "dp-secret" not in row.password_encrypted and hub.crypto().decrypt(row.password_encrypted) == "dp-secret"
    assert client.post("/api/hub/haproxy/test", json={"url": "http://lb1:5555", "username": "admin", "instance_id": iid},
                       headers=CSRF).json()["servers"] == 1
    assert client.get(f"/api/hub/haproxy/{iid}/overview").json()["frontends"][0]["name"] == "web"
    assert client.put(f"/api/hub/haproxy/{iid}/servers/shop/shop1/state", json={"state": "drain"}, headers=CSRF).status_code == 200
    assert fake.state[("shop", "shop1")] == "drain"
    assert client.put(f"/api/hub/haproxy/{iid}/servers/shop/shop1/state", json={"state": "off"}, headers=CSRF).status_code == 422
    plan = client.post(f"/api/hub/haproxy/{iid}/changes", json={"ops": [{"op": "delete", "kind": "server", "backend": "shop", "name": "shop1"}]},
                       headers=CSRF).json()
    assert plan["applied"] is False and "shop1" in fake.live["backends"]["shop"]["servers"]
    actions = [(a["action"], a["dry_run"], a["ok"]) for a in client.get("/api/hub/audit").json()]
    assert ("haproxy-drain", False, True) in actions and ("haproxy-config", True, True) in actions

    # a viewer can look but not touch
    viewer = next(x for x in client.get("/api/hub/roles").json() if x["name"] == "Kubernetes viewer")
    client.patch(f"/api/hub/roles/{viewer['id']}", json={"name": "Kubernetes viewer", "permissions": ["cluster.view", "haproxy.view"]}, headers=CSRF)
    client.post("/api/hub/users", json={"username": "vic", "password": "Vic-pass-12", "must_change_password": False,
                                        "assignments": [{"role_id": viewer["id"], "cluster_id": None}]}, headers=CSRF)
    v = TestClient(hub.app)
    login(v, "vic", "Vic-pass-12")
    assert v.get(f"/api/hub/haproxy/{iid}/overview").status_code == 200
    assert v.put(f"/api/hub/haproxy/{iid}/servers/shop/shop1/state", json={"state": "maint"}, headers=CSRF).status_code == 403
    assert v.post(f"/api/hub/haproxy/{iid}/changes", json={"ops": [{"op": "delete", "kind": "server", "backend": "shop", "name": "shop1"}]},
                  headers=CSRF).status_code == 403
    assert v.get(f"/api/hub/haproxy/{iid}/config").status_code == 403
    assert v.post("/api/hub/haproxy", json={"name": "x", "url": "http://x:1", "username": "a", "password": "b"}, headers=CSRF).status_code == 403


def test_failed_reload_is_reported_not_hidden():
    dp = FakeDataPlane()
    dp.reload_fails = True
    with pytest.raises(hx.DataPlaneError, match="previous configuration is still active"):
        hx.plan_and_apply(dp, [hx.Op(op="delete", kind="server", backend="shop", name="shop1")], apply=True)
    ok = FakeDataPlane()
    hx.plan_and_apply(ok, [hx.Op(op="delete", kind="server", backend="shop", name="shop1")], apply=True)
    assert ok.waited == "reload-1"


def test_dataplane_waits_for_reload_status():
    import httpx
    seen = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/reloads/r1"):
            seen["n"] += 1
            return httpx.Response(200, json={"id": "r1", "status": "in_progress" if seen["n"] < 2 else "failed", "response": "[ALERT] bad"})
        return httpx.Response(202, headers={"Reload-ID": "r1"}, json={})
    dp = hx.DataPlane("http://lb:5555", "u", "p", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert dp.commit("t1") == "r1"
    with pytest.raises(hx.DataPlaneError, match="bad"):
        dp.wait_reload("r1", timeout=5)
