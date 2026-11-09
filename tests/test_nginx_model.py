import os
from pathlib import Path

from k10y_agent.nginx import model

CONF = Path(__file__).parent / "nginx_conf"


def load(tmp_path):
    # The sample uses /etc/nginx/conf.d; point the include at the test copy.
    text = (CONF / "nginx.conf").read_text().replace("/etc/nginx/conf.d", str(CONF / "conf.d"))
    (tmp_path / "nginx.conf").write_text(text)
    return model.model(str(tmp_path / "nginx.conf"))


def test_sites_upstreams_and_logs(tmp_path):
    m = load(tmp_path)
    assert m["errors"] == []
    names = [(s["context"], s["server_names"], s["kind"]) for s in m["sites"]]
    assert names == [("http", ["shop.example.com", "www.shop.example.com"], "redirect"), ("http", ["shop.example.com"], "mixed"),
                     ("stream", ["_"], "tcp")]
    main = m["sites"][1]
    assert main["ssl"] and main["listen"][0]["http2"] and main["listen"][0]["default"] and main["file"].endswith("shop.conf")
    assert main["upstreams"] == ["api", "shop"]
    locs = {l["path"]: l for l in main["locations"]}
    assert locs["/api/"]["upstream"] == "api" and locs["/api/"]["websocket"] and locs["/"]["kind"] == "proxy"
    assert locs["/health"]["match"] == "exact" and locs["/health"]["kind"] == "return"
    assert locs["\\.(png|jpg)$"]["match"] == "regex-i" and locs["/static/"]["match"] == "prefix-priority"
    shop = next(u for u in m["upstreams"] if u["name"] == "shop")
    assert shop["method"] == "least_conn" and shop["keepalive"] == "16"
    assert [(x["address"], x["weight"], x["backup"]) for x in shop["servers"]] == [("shop1:80", 3, False), ("shop2:80", 1, False), ("shop3:80", 1, True)]
    api = next(u for u in m["upstreams"] if u["name"] == "api")
    assert api["servers"][1]["down"] and api["used_by"] == [main["id"]]
    pg = next(u for u in m["upstreams"] if u["name"] == "pg")
    assert pg["context"] == "stream" and m["sites"][2]["upstreams"] == ["pg"]
    assert m["access_logs"] == [{"context": "http", "path": "/var/log/nginx/access.log", "format": "k10y"}]
    assert m["error_logs"] == ["/var/log/nginx/error.log"] and "k10y" in m["log_formats"]
    assert model.find_stub_status(str(tmp_path / "nginx.conf")) is None   # only on the TLS port: not usable


def test_location_matching_like_nginx(tmp_path):
    locs = load(tmp_path)["sites"][1]["locations"]
    pick = lambda uri: model.match_location(locs, uri)["path"]
    assert pick("/health") == "/health"                      # exact
    assert pick("/health/x") == "/"                          # exact doesn't match longer
    assert pick("/api/orders?id=1") == "/api/"               # longest prefix
    assert pick("/static/a.png") == "/static/"               # ^~ stops before regex
    assert pick("/img/a.PNG") == "\\.(png|jpg)$"             # regex (case-insensitive) beats prefix "/"
    assert pick("/cart") == "/"


def test_pick_site_by_host(tmp_path):
    sites = load(tmp_path)["sites"]
    assert model.pick_site(sites, "shop.example.com", port=443)["id"] == sites[1]["id"]
    assert model.pick_site(sites, "www.shop.example.com", port=80)["id"] == sites[0]["id"]
    assert model.pick_site(sites, "unknown.example", port=443)["id"] == sites[1]["id"]   # default_server
    ups = load(tmp_path)["upstreams"]
    assert model.upstream_for_address(ups, "10.0.0.7:8080", {}) == ("api", "10.0.0.7:8080")
    assert model.upstream_for_address(ups, "172.20.0.5:80", {"shop2": ["172.20.0.5"]}) == ("shop", "shop2:80")
