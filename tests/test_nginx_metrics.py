import json

from k10y_agent.nginx import metrics


def test_parse_k10y_json_and_combined_lines():
    e = metrics.parse_line(json.dumps({"time": "t", "host": "shop.local", "server": "shop.local", "port": "80", "method": "GET",
                                       "uri": "/api/x?y=1", "status": 502, "bytes": 12, "rt": 0.25, "ua": "10.0.0.7:80, 10.0.0.8:80",
                                       "us": "502", "urt": "0.2", "ip": "1.2.3.4"}))
    assert e["status"] == 502 and e["rt"] == 0.25 and e["port"] == 80 and e["ua"].startswith("10.0.0.7")
    c = metrics.parse_line('1.2.3.4 - - [09/Nov/2026:10:00:00 +0000] "GET /cart HTTP/1.1" 404 153 "-" "curl/8" 0.003')
    assert c["status"] == 404 and c["uri"] == "/cart" and c["rt"] == 0.003 and c["host"] is None
    assert metrics.parse_line("garbage") is None and metrics.parse_line("") is None


def test_ingest_counts_per_site_location_and_upstream_server():
    cfg = {"sites": [{"id": "s:1", "context": "http", "server_names": ["shop.local"], "listen": [{"port": 80, "default": True}],
                      "locations": [{"id": "l:/", "match": "prefix", "path": "/", "locations": []},
                                    {"id": "l:/api/", "match": "prefix", "path": "/api/", "locations": []}]}],
           "upstreams": [{"name": "api", "servers": [{"address": "api1:80", "host": "api1", "port": 80}]}]}
    m = metrics.Metrics(lambda: cfg, lambda: None)
    m.resolved = {"api1": ["10.0.0.7"]}
    for status in (200, 200, 503):
        m.ingest(json.dumps({"host": "shop.local", "uri": "/api/v1", "status": status, "rt": 0.1, "ua": "10.0.0.7:80"}))
    m.ingest(json.dumps({"host": "shop.local", "uri": "/", "status": 404, "rt": 0.01}))
    b = m._bucket
    assert b.req == 4 and b.e5 == 1 and b.e4 == 1
    t, p = b.point()
    assert p["locations"]["l:/api/"]["req"] == 0.3 and p["locations"]["l:/"]["e4_pct"] == 100.0
    assert p["servers"]["api/api1:80"]["e5_pct"] == 33.3 and p["sites"]["s:1"]["req"] == 0.4
    assert [x["upstream_server"] for x in m.lines()][:1] == ["api1:80"]
