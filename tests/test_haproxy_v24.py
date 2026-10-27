"""HAProxy: every balance algorithm and health check, sticky sessions, timeouts, server options,
maintenance page, live IP blocklist, rate-limit unblock, minute history and group drift."""
import pytest

from k10y_agent.hub import app as hub
from k10y_agent.hub import haproxy as hx
from k10y_agent.hub.haproxy_monitor import Sample, average

from test_haproxy import FakeDataPlane
from test_hub import CSRF, client, ready_admin  # noqa: F401  (fixture)


class LiveFake(FakeDataPlane):
    """Adds the storage, runtime ACL and stick-table calls."""

    def __init__(self):
        super().__init__()
        self.files, self.acl, self.sticks = {}, {}, {"web": [{"key": "203.0.113.9", "http_req_rate": 40, "exp": 5000}]}

    def put_general_file(self, name, text, create):
        assert create == (name not in self.files)
        self.files[name] = {"storage_name": name, "file": f"/etc/haproxy/general/{name}", "text": text}
        return self.files[name]

    def runtime_acl_id(self, path):
        protected = any(hx._is_block(r) for f in self.live["frontends"].values() for r in f.get("tcp_request_rule_list") or [])
        if protected:   # after the reload HAProxy loaded the file into a runtime list
            self.acl.setdefault("0", [{"id": f"e{i}", "value": v} for i, v in enumerate(self.files[hx.BLOCKLIST]["text"].split())])
            return "0"
        return None

    def acl_entries(self, acl_id):
        return list(self.acl[acl_id])

    def add_acl_entry(self, acl_id, value):
        self.acl[acl_id].append({"id": f"e{len(self.acl[acl_id])}", "value": value})

    def delete_acl_entry(self, acl_id, entry_id):
        self.acl[acl_id] = [e for e in self.acl[acl_id] if e["id"] != entry_id]

    def stick_entries(self, table, count=200):
        return list(self.sticks.get(table, []))

    def set_stick_entry(self, table, key, data):
        for e in self.sticks[table]:
            if e["key"] == key:
                e.update(data)


def plan(dp, *ops):
    return hx.plan_and_apply(dp, [hx.Op(**o) for o in ops], apply=True)


def test_every_balance_algorithm_with_its_argument():
    dp = FakeDataPlane()
    for alg, arg, field in (("hdr", "X-User-Id", "hdr_name"), ("url_param", "user_id", "url_param"),
                            ("hash", "req.cook(session)", "hash_expression"), ("random", "", None), ("first", "", None)):
        plan(dp, {"op": "update", "kind": "backend", "name": "api", "data": {"balance": alg, "balance_arg": arg}})
        bal = dp.live["backends"]["api"]["balance"]
        assert bal["algorithm"] == alg and (field is None or bal[field] == arg)
    with pytest.raises(hx.DataPlaneError, match="header name"):
        plan(dp, {"op": "update", "kind": "backend", "name": "api", "data": {"balance": "hdr", "balance_arg": "bad header"}})
    with pytest.raises(hx.DataPlaneError, match="balance must be"):
        plan(dp, {"op": "update", "kind": "backend", "name": "api", "data": {"balance": "fastest"}})
    b = next(x for x in hx.overview(dp)["backends"] if x["name"] == "api")
    assert b["balance"] == "first" and b["balance_arg"] == ""


def test_database_and_protocol_health_checks():
    dp = FakeDataPlane()
    plan(dp, {"op": "create", "kind": "backend", "name": "pg", "data": {"mode": "tcp", "balance": "first", "check": {"type": "pgsql", "user": "haproxy_check"}}})
    assert dp.live["backends"]["pg"]["adv_check"] == "pgsql-check"
    assert dp.live["backends"]["pg"]["pgsql_check_params"] == {"username": "haproxy_check"}
    plan(dp, {"op": "update", "kind": "backend", "name": "pg", "data": {"check": {"type": "redis"}}})
    assert dp.live["backends"]["pg"]["adv_check"] == "redis-check" and "pgsql_check_params" not in dp.live["backends"]["pg"]
    plan(dp, {"op": "update", "kind": "backend", "name": "shop", "data": {"check": {"type": "http", "method": "HEAD", "uri": "/healthz", "host": "shop.example.com"}}})
    assert dp.live["backends"]["shop"]["httpchk_params"] == {"method": "HEAD", "uri": "/healthz", "version": "HTTP/1.1", "host": "shop.example.com"}
    with pytest.raises(hx.DataPlaneError, match="MySQL check"):
        plan(dp, {"op": "update", "kind": "backend", "name": "pg", "data": {"check": {"type": "mysql"}}})
    chk = {b["name"]: b["check"] for b in hx.overview(dp)["backends"]}
    assert chk["pg"]["type"] == "redis" and chk["shop"]["method"] == "HEAD" and chk["api"]["type"] == "connect"


def test_sticky_sessions_forwardfor_timeouts_and_server_options():
    dp = FakeDataPlane()
    plan(dp, {"op": "update", "kind": "backend", "name": "shop", "data": {"sticky": True, "forwardfor": True, "timeouts": {"server": 60, "tunnel": 3600}}})
    be = dp.live["backends"]["shop"]
    assert be["cookie"]["name"] == "SERVERID" and be["servers"]["shop1"]["cookie"] == "shop1"
    assert be["forwardfor"] == {"enabled": "enabled"} and be["server_timeout"] == 60000 and be["tunnel_timeout"] == 3600000
    plan(dp, {"op": "create", "kind": "server", "backend": "shop", "name": "shop2", "data": {"address": "10.0.0.2", "port": 443, "ssl": True, "maxconn": 200}})
    s2 = dp.live["backends"]["shop"]["servers"]["shop2"]
    assert s2["cookie"] == "shop2" and s2["ssl"] == "enabled" and s2["verify"] == "none" and s2["maxconn"] == 200
    o = next(b for b in hx.overview(dp)["backends"] if b["name"] == "shop")
    assert o["sticky"] and o["forwardfor"] and o["timeouts"] == {"connect": 0, "server": 60, "tunnel": 3600}
    plan(dp, {"op": "update", "kind": "backend", "name": "shop", "data": {"sticky": False, "timeouts": {}}})
    be = dp.live["backends"]["shop"]
    assert "cookie" not in be and "cookie" not in be["servers"]["shop1"] and "server_timeout" not in be
    with pytest.raises(hx.DataPlaneError, match="HTTP backend"):
        plan(dp, {"op": "create", "kind": "backend", "name": "db", "data": {"mode": "tcp", "sticky": True}})


def test_maintenance_page_is_managed_and_parsed():
    dp = FakeDataPlane()
    plan(dp, {"op": "update", "kind": "frontend", "name": "web", "data": {"policies": {
        "maintenance": {"enabled": True, "message": 'Upgrading the "shop" <b>$HOME</b>', "allow_ips": ["10.0.0.0/8"]}}}})
    rule = dp.live["frontends"]["web"]["http_request_rule_list"][0]
    assert rule["type"] == "return" and rule["return_status_code"] == 503 and rule["cond_test"] == "{ src 10.0.0.0/8 }"
    assert '"' not in rule["return_content"][1:-1] and "$" not in rule["return_content"]
    pol = next(f for f in hx.overview(dp)["frontends"] if f["name"] == "web")["policies"]
    assert pol["maintenance"] == {"enabled": True, "message": "Upgrading the shop bHOME/b", "allow_ips": ["10.0.0.0/8"]}
    assert pol["custom_rules"] == 0
    # saving other policies without "maintenance" keeps it; an explicit off removes it
    plan(dp, {"op": "update", "kind": "frontend", "name": "web", "data": {"policies": {"redirect_https": True}}})
    assert hx._is_maint(dp.live["frontends"]["web"]["http_request_rule_list"][0])
    plan(dp, {"op": "update", "kind": "frontend", "name": "web", "data": {"policies": {"maintenance": {"enabled": False}}}})
    assert not any(hx._is_maint(r) for r in dp.live["frontends"]["web"]["http_request_rule_list"])


def test_frontend_client_timeout():
    dp = FakeDataPlane()
    plan(dp, {"op": "update", "kind": "frontend", "name": "web", "data": {"timeouts": {"client": 45}}})
    assert dp.live["frontends"]["web"]["client_timeout"] == 45000
    assert next(f for f in hx.overview(dp)["frontends"] if f["name"] == "web")["timeouts"] == {"client": 45}


def test_blocklist_first_use_reloads_then_changes_live(client, monkeypatch):
    fake = LiveFake()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: fake)
    ready_admin(client)
    iid = client.post("/api/hub/haproxy", json={"name": "lb1", "url": "http://lb1:5555", "username": "a", "password": "p"}, headers=CSRF).json()["id"]
    assert client.get(f"/api/hub/haproxy/{iid}/blocklist").json() == {"ready": False, "ips": [], "protected": [], "missing": ["web"]}
    assert client.post(f"/api/hub/haproxy/{iid}/blocklist", json={"ip": "not an ip"}, headers=CSRF).status_code == 400
    r = client.post(f"/api/hub/haproxy/{iid}/blocklist", json={"ip": "203.0.113.7", "reason": "scraper"}, headers=CSRF).json()
    assert r["reloaded"] is True and fake.version_n == 2
    assert hx._is_block(fake.live["frontends"]["web"]["tcp_request_rule_list"][0])
    r = client.post(f"/api/hub/haproxy/{iid}/blocklist", json={"ip": "198.51.100.0/24"}, headers=CSRF).json()
    assert r["reloaded"] is False and fake.version_n == 2                      # no reload the second time
    assert fake.files[hx.BLOCKLIST]["text"] == "203.0.113.7\n198.51.100.0/24\n"  # kept for the next reload
    assert client.get(f"/api/hub/haproxy/{iid}/blocklist").json()["ips"] == ["203.0.113.7", "198.51.100.0/24"]
    assert client.delete(f"/api/hub/haproxy/{iid}/blocklist", params={"ip": "203.0.113.7"}, headers=CSRF).status_code == 200
    assert fake.files[hx.BLOCKLIST]["text"] == "198.51.100.0/24\n" and fake.version_n == 2
    # a frontend created later gets the same rule
    hx.plan_and_apply(fake, [hx.Op(op="create", kind="frontend", name="admin", data={"binds": [{"port": 8081}], "default_backend": "shop"})], apply=True)
    assert hx._is_block(fake.live["frontends"]["admin"]["tcp_request_rule_list"][0])
    actions = [a["action"] for a in client.get("/api/hub/audit").json()]
    assert "haproxy-block" in actions and "haproxy-unblock" in actions


def test_unblock_rate_limited_client(client, monkeypatch):
    fake = LiveFake()
    monkeypatch.setattr(hx, "DataPlane", lambda *a, **k: fake)
    ready_admin(client)
    iid = client.post("/api/hub/haproxy", json={"name": "lb1", "url": "http://lb1:5555", "username": "a", "password": "p"}, headers=CSRF).json()["id"]
    assert client.post(f"/api/hub/haproxy/{iid}/stick-tables/web/reset", json={"key": "203.0.113.9"}, headers=CSRF).status_code == 200
    assert fake.sticks["web"][0]["http_req_rate"] == 0
    assert client.post(f"/api/hub/haproxy/{iid}/stick-tables/web/reset", json={"key": "192.0.2.1"}, headers=CSRF).status_code == 404


def test_backend_route_numbers_and_minute_average():
    stats = lambda resp, e5: [{"type": "backend", "name": "api", "stats": {"stot": resp, "hrsp_2xx": resp - e5, "hrsp_5xx": e5, "rtime": 12}}]
    _, c = Sample.take(stats(100, 0), None, 0)
    point, _ = Sample.take(stats(200, 10), c, 10)
    assert point["b"]["api"]["rq"] == 10.0 and point["b"]["api"]["e5_pct"] == 10.0 and point["b"]["api"]["rtime"] == 12
    avg = average([{"f": {}, "b": {"api": {"rq": 10, "rtime": 10}}}, {"f": {}, "b": {"api": {"rq": 20, "rtime": 30}}}])
    assert avg["b"]["api"] == {"rq": 15.0, "rtime": 20.0}


def test_group_drift_shows_differences(client, monkeypatch):
    a, b = FakeDataPlane(), FakeDataPlane()
    b.live["backends"]["api"]["balance"] = {"algorithm": "roundrobin"}
    fakes = {"http://a:5555": a, "http://b:5555": b}
    monkeypatch.setattr(hx, "DataPlane", lambda url, *x, **k: fakes[url])
    ready_admin(client)
    ia = client.post("/api/hub/haproxy", json={"name": "a", "url": "http://a:5555", "username": "u", "password": "p", "group_name": "edge"}, headers=CSRF).json()["id"]
    client.post("/api/hub/haproxy", json={"name": "b", "url": "http://b:5555", "username": "u", "password": "p", "group_name": "edge"}, headers=CSRF)
    d = client.get(f"/api/hub/haproxy/{ia}/group-drift").json()
    assert d["group"] == "edge" and d["others"][0]["name"] == "b" and not d["others"][0]["same"] and "leastconn" in d["others"][0]["diff"]
    b.live = a.live
    assert client.get(f"/api/hub/haproxy/{ia}/group-drift").json()["others"][0]["same"] is True


def test_group_copy_keeps_each_instance_log_tag():
    ref = "global\n  maxconn 4000\n  log-tag lab-lb\ndefaults\n  mode http\n"
    theirs = "global\n  log-tag lab-lb-2\n  maxconn 2000\ndefaults\n  mode http\n"
    out = hx._keep_log_tag(ref, theirs)
    assert "log-tag lab-lb-2" in out and "log-tag lab-lb\n" not in out and "maxconn 4000" in out
    assert out.index("maxconn 4000") < out.index("log-tag lab-lb-2")          # stays where ref had its tag
    assert hx._keep_log_tag(ref, "global\n") == "global\n  maxconn 4000\ndefaults\n  mode http\n"
    assert hx._keep_log_tag("global\n  maxconn 1\n\ndefaults\n", theirs) == "global\n  maxconn 1\n  log-tag lab-lb-2\n\ndefaults\n"


def test_dataplane_serialises_config_calls_and_survives_a_restart(monkeypatch):
    """Two threads never talk to the configuration/transactions API of one HAProxy at the same time
    (the Data Plane API can crash on that); reads are retried while it restarts, writes are not."""
    import threading, time as _t
    import httpx
    monkeypatch.setattr(hx, "RETRIES", (0.01, 0.01, 0.01))
    state = {"now": 0, "max": 0, "drop": 0, "posts": 0}
    lock = threading.Lock()

    def handler(request):
        if state["drop"]:
            state["drop"] -= 1
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if request.method == "POST":
            state["posts"] += 1
        if "/configuration/" in request.url.path or "/transactions" in request.url.path:
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            _t.sleep(0.01)
            with lock:
                state["now"] -= 1
        return httpx.Response(200, json={"id": "tx1"} if request.method == "POST" else 5)

    def client():
        return hx.DataPlane("http://lb-race:5555", "u", "p", client=httpx.Client(transport=httpx.MockTransport(handler)))
    threads = [threading.Thread(target=lambda: [client().call("GET", "/services/haproxy/configuration/version") for _ in range(10)]) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert state["max"] == 1                                              # never two at once
    state["drop"] = 2
    assert client().call("GET", "/services/haproxy/configuration/version") == 5   # read retried after a restart
    state["drop"], posts = 1, state["posts"]
    with pytest.raises(hx.DataPlaneError, match="Config history"):
        client().call("POST", "/services/haproxy/transactions")           # a write is never sent twice
    assert state["posts"] == posts
    # live stats don't wait for the lock
    with hx.tx_lock(client()):
        done = []
        t = threading.Thread(target=lambda: done.append(client().call("GET", "/services/haproxy/stats/native")))
        t.start(); t.join(1)
        assert done


def test_transactions_left_open_by_a_crash_are_closed(monkeypatch):
    import httpx
    monkeypatch.setattr(hx, "RETRIES", (0.01,))
    log, st = [], {"drop_delete": True, "full": False, "open": {"old1": 3, "cur1": 7}}

    def handler(request):
        path, m = request.url.path, request.method
        log.append((m, path.rsplit("/", 1)[-1]))
        if path.endswith("/configuration/version"):
            return httpx.Response(200, json=7)
        if m == "DELETE":
            if st["drop_delete"]:
                st["drop_delete"] = False
                raise httpx.RemoteProtocolError("Server disconnected")       # API restarted while closing
            st["open"].pop(path.rsplit("/", 1)[-1], None)
            return httpx.Response(204)
        if m == "GET" and path.endswith("/transactions"):
            return httpx.Response(200, json=[{"id": k, "_version": v, "status": "in_progress"} for k, v in st["open"].items()])
        if m == "POST" and path.endswith("/transactions"):
            if st["full"]:
                st["full"] = False
                return httpx.Response(406, json={"message": "cannot start a new transaction, reached the maximum amount of 20 active transactions"})
            return httpx.Response(201, json={"id": "new"})
        return httpx.Response(404, json={"message": "unexpected"})

    dp = hx.DataPlane("http://lb-orphans:5555", "u", "p", client=httpx.Client(transport=httpx.MockTransport(handler)))
    dp.rollback("t1")                                            # could not close it: remembered
    assert "t1" in hx._ORPHANS[dp.base]
    assert dp.begin() == "new"
    assert ("DELETE", "t1") in log and "t1" not in hx._ORPHANS[dp.base]   # closed before the next change
    st["full"] = True
    assert dp.begin() == "new"                                   # limit reached: outdated ones are closed
    assert "old1" not in st["open"] and "cur1" in st["open"]     # another tool's current transaction is left alone
