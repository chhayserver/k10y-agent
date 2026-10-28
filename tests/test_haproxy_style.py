"""A real-world config (exactly as the Data Plane API returns it): inline service routes, a rule with no condition,
X-Forwarded-Port, certificate files, redispatch/retries, check timing, TCP keep-alive and stick on src.
k10y must read all of it as editable settings and, when editing, keep the way it's written."""
import copy
import json
import pathlib

from k10y_agent.hub import haproxy as hx

from test_haproxy import FakeDataPlane

FIXTURE = json.loads((pathlib.Path(__file__).parent / "haproxy_user_style.json").read_text())


def user_dp():
    dp = FakeDataPlane()
    dp.live = copy.deepcopy(FIXTURE)
    return dp


def plan(dp, *ops):
    return hx.plan_and_apply(dp, [hx.Op(**o) for o in ops], apply=True)


def test_reads_the_whole_config_as_editable_settings():
    o = hx.overview(user_dp())
    fe = {f["name"]: f for f in o["frontends"]}
    m = fe["microservices"]
    assert m["simple_routes"] and m["routes"] == [
        {"kind": "service", "value": "/order", "style": "inline", "backend": "inbound-order"},
        {"kind": "service", "value": "/payment", "style": "inline", "backend": "inbound-payment"}]
    assert m["policies"]["forwarded_port"] and m["policies"]["redirect_https"] and m["policies"]["custom_rules"] == 0
    assert m["binds"][0]["certificate_file"].endswith("shop_lab_local.pem") and m["binds"][0]["address"] == "0.0.0.0"
    assert m["timeouts"] == {"client": 60}
    assert fe["mattermost"]["simple_routes"] and fe["mattermost"]["routes"] == [{"kind": "always", "value": "", "style": "always", "backend": "mattermost"}]
    be = {b["name"]: b for b in o["backends"]}
    order = be["inbound-order"]
    assert order["redispatch"] and order["retries"] == 3 and order["timeouts"] == {"connect": 10, "server": 60, "tunnel": 0}
    assert order["check_timing"] == {"inter": 1.0, "rise": 3, "fall": 1}
    rabbit = be["outbound-rabbitmq-dev"]
    assert rabbit["stick_src"] == 5 and rabbit["keepalive"] and rabbit["redispatch"] and rabbit["mode"] == "tcp"
    assert rabbit["check_timing"] == {"inter": 2.0, "rise": 3, "fall": 2}
    assert be["mattermost"]["servers"][0]["ssl"]


def test_editing_keeps_the_way_it_is_written():
    dp = user_dp()
    before = copy.deepcopy(dp.live["frontends"]["microservices"]["http_request_rule_list"])
    # add a route the house way: /invoice and everything under /invoice/, inline, no named ACL
    m = next(f for f in hx.overview(dp)["frontends"] if f["name"] == "microservices")
    routes = [*m["routes"], {"kind": "service", "value": "/invoice", "backend": "inbound-order"}]
    plan(dp, {"op": "update", "kind": "frontend", "name": "microservices", "data": {"routes": routes}})
    fe = dp.live["frontends"]["microservices"]
    assert fe["backend_switching_rule_list"][2] == {"name": "inbound-order", "cond": "if", "cond_test": "{ path /invoice } || { path_beg /invoice/ }"}
    assert fe["backend_switching_rule_list"][:2] == FIXTURE["frontends"]["microservices"]["backend_switching_rule_list"]
    assert not fe.get("acl_list")
    # turning on a rate limit leaves the existing set-header and redirect lines exactly as they were
    plan(dp, {"op": "update", "kind": "frontend", "name": "microservices", "data": {"policies": {
        "allow_ips": [], "redirect_https": True, "forwarded_port": True, "rate_limit": {"requests": 100, "period": 10}}}})
    rules = dp.live["frontends"]["microservices"]["http_request_rule_list"]
    assert rules[:2] == before and [r["type"] for r in rules[2:]] == ["track-sc", "deny"]
    assert "redir_code" not in rules[1]


def test_new_server_matches_the_others_and_backend_options_round_trip():
    dp = user_dp()
    plan(dp, {"op": "create", "kind": "server", "backend": "inbound-order", "name": "server4", "data": {"address": "172.2.2.4", "port": 32133, "check": True}})
    s4 = dp.live["backends"]["inbound-order"]["servers"]["server4"]
    assert (s4["inter"], s4["rise"], s4["fall"]) == (1000, 3, 1)                 # same check timing as server1-3
    plan(dp, {"op": "update", "kind": "backend", "name": "outbound-rabbitmq-dev", "data": {"stick_src": 10, "check_timing": {"inter": 3, "rise": 2, "fall": 3}}})
    r = dp.live["backends"]["outbound-rabbitmq-dev"]
    assert r["stick_table"]["expire"] == 600000 and r["stick_rule_list"] == [{"type": "on", "pattern": "src"}]
    assert all((s["inter"], s["rise"], s["fall"]) == (3000, 2, 3) for s in r["servers"].values())
    plan(dp, {"op": "update", "kind": "backend", "name": "outbound-rabbitmq-dev", "data": {"stick_src": 0, "keepalive": False, "redispatch": False}})
    r = dp.live["backends"]["outbound-rabbitmq-dev"]
    assert "stick_table" not in r and "stick_rule_list" not in r and "tcpka" not in r and "redispatch" not in r


def test_certificate_file_on_the_server_is_kept():
    dp = user_dp()
    path = FIXTURE["frontends"]["mattermost"]["binds"]["0.0.0.0:8066"]["ssl_certificate"]
    plan(dp, {"op": "update", "kind": "frontend", "name": "mattermost", "data": {"binds": [{"address": "0.0.0.0", "port": 8066, "ssl": True, "certificate": path}]}})
    assert dp.live["frontends"]["mattermost"]["binds"]["0.0.0.0:8066"]["ssl_certificate"] == path


def test_only_the_changed_part_is_written():
    """Adding a route writes only the use_backend rules; the bind, timeout and header lines aren't touched."""
    dp = user_dp()
    m = next(f for f in hx.overview(dp)["frontends"] if f["name"] == "microservices")
    dp.calls.clear()
    plan(dp, {"op": "update", "kind": "frontend", "name": "microservices",
              "data": {"routes": [*m["routes"], {"kind": "service", "value": "/invoice", "backend": "inbound-order"}]}})
    writes = [c for c in dp.calls if c[0] not in ("GET",)]
    assert ("PUT-part", "microservices/backend_switching_rules") in writes
    assert not any(c[0] == "PUT-base" for c in writes) and not any(c[1].endswith("acls") for c in writes if c[0] == "PUT-part")
    dp.calls.clear()
    plan(dp, {"op": "update", "kind": "frontend", "name": "microservices", "data": {"timeouts": {"client": 90}}})
    assert [c for c in dp.calls if c[0] in ("PUT-base", "PUT-part")] == [("PUT-base", "microservices")]
    assert dp.live["frontends"]["microservices"]["binds"] == FIXTURE["frontends"]["microservices"]["binds"]
