import os
import stat
from pathlib import Path

import pytest

from k10y_agent.nginx import edit, model

MAIN = """user nginx;
events { worker_connections 1024; }
http {
    include {conf}/conf.d/*.conf;
}
"""
SITE = """# Shop front door: keep this comment
upstream shop {
  least_conn;   # my style: 2 spaces
  server shop1:80 weight=3;
  server shop2:80;
}

server {
  listen 80;
  server_name shop.local;   # main name

  location / {
    proxy_pass http://shop;
  }
  location = /health { return 200 "ok"; }
}
"""


@pytest.fixture()
def conf(tmp_path):
    (tmp_path / "conf.d").mkdir()
    (tmp_path / "nginx.conf").write_text(MAIN.replace("{conf}", str(tmp_path)))
    (tmp_path / "conf.d" / "shop.conf").write_text(SITE)
    return tmp_path


def change(conf):
    return edit.Change(model.model(str(conf / "nginx.conf")), str(conf))


def test_add_route_keeps_comments_and_indentation(conf):
    c = change(conf)
    site = c.model["sites"][0]
    c.apply({"kind": "location", "op": "create", "site": site["id"],
             "data": {"path": "/api/", "kind": "proxy", "target": "api:8080", "websocket": True}})
    text = c.texts[str(conf / "conf.d" / "shop.conf")]
    assert "# Shop front door: keep this comment" in text and "server_name shop.local;   # main name" in text
    assert "  location /api/ {\n    proxy_pass http://api:8080;" in text          # the file's 2-space step
    assert 'proxy_set_header Connection "upgrade";' in text
    assert text.rstrip().endswith("}\n}") or text.rstrip().endswith("}")
    m = model.model(str(conf / "nginx.conf"))               # still parses after writing it
    (conf / "conf.d" / "shop.conf").write_text(text)
    paths = [l["path"] for l in model.model(str(conf / "nginx.conf"))["sites"][0]["locations"]]
    assert paths == ["/", "/health", "/api/"] and m["errors"] == []


def test_update_and_delete_blocks(conf):
    c = change(conf)
    up = c.model["upstreams"][0]
    c.apply({"kind": "upstream", "op": "update", "id": up["id"], "data": {"method": "round_robin", "servers": [
        {"address": "shop1:80", "weight": 2}, {"address": "shop2:80"}, {"address": "shop3:80", "backup": True}]}})
    health = next(l for l in c.model["sites"][0]["locations"] if l["path"] == "/health")
    c.apply({"kind": "location", "op": "delete", "id": health["id"]})
    text = c.texts[str(conf / "conf.d" / "shop.conf")]
    assert "least_conn" not in text and "server shop1:80 weight=2;" in text and "server shop3:80 backup;" in text
    assert "/health" not in text and "# Shop front door" in text
    (conf / "conf.d" / "shop.conf").write_text(text)
    m = model.model(str(conf / "nginx.conf"))
    assert m["errors"] == [] and [s["address"] for s in m["upstreams"][0]["servers"]] == ["shop1:80", "shop2:80", "shop3:80"]
    with pytest.raises(edit.EditError, match="still used"):
        edit.Change(m, str(conf)).apply({"kind": "upstream", "op": "delete", "id": m["upstreams"][0]["id"]})


def test_new_site_in_conf_d_and_rename(conf):
    c = change(conf)
    c.apply({"kind": "site", "op": "create", "data": {"server_names": ["api.local"], "locations": [{"path": "/", "kind": "proxy", "target": "api"}],
                                                      "upstream": {"name": "api", "servers": [{"address": "api1:80"}, {"address": "api2:80"}]}}})
    assert str(conf / "conf.d" / "api.local.conf") in c.created
    site = c.model["sites"][0]
    c.apply({"kind": "site", "op": "update", "id": site["id"], "data": {"server_names": ["shop.local", "www.shop.local"], "listen": ["8080"]}})
    for p, t in c.texts.items():
        Path(p).write_text(t)
    m = model.model(str(conf / "nginx.conf"))
    assert m["errors"] == []
    # conf.d/*.conf is read in alphabetical order: api.local.conf first
    assert [(s["server_names"], [l["port"] for l in s["listen"]]) for s in m["sites"]] == [(["api.local"], [80]), (["shop.local", "www.shop.local"], [8080])]
    assert [u["name"] for u in m["upstreams"]] == ["api", "shop"]


def test_validation_errors():
    for bad in ({"path": "", "kind": "proxy", "target": "x"}, {"path": "/a;b", "kind": "proxy", "target": "x"},
                {"path": "/", "kind": "redirect", "code": "200", "target": "/x"}, {"path": "(", "match": "regex", "kind": "return"},
                {"path": "/", "kind": "static", "target": "relative/dir"}):
        with pytest.raises(edit.EditError):
            edit.location_directives(bad)
    with pytest.raises(edit.EditError):
        edit.upstream_directives({"name": "bad name", "servers": [{"address": "a:1"}]})


def fake_nginx(tmp_path, ok: bool, bad_file: str = "", line: int = 0):
    p = tmp_path / "nginx"
    out = "nginx: configuration file test is successful" if ok else f'nginx: [emerg] unknown directive "frobnicate" in {bad_file}:{line}'
    p.write_text(f"#!/bin/sh\necho '{out}' >&2\nexit {0 if ok else 1}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_run_restores_on_failure_and_records_history(conf, tmp_path):
    shop = conf / "conf.d" / "shop.conf"
    original = shop.read_text()
    c = change(conf)
    c.apply({"kind": "file", "op": "update", "path": str(shop), "text": original + "frobnicate yes;\n"})
    r = edit.run(fake_nginx(tmp_path, False, str(shop), 18), str(conf / "nginx.conf"), c, True, "true", str(tmp_path / "hist"))
    assert not r["valid"] and not r["applied"] and shop.read_text() == original                 # put back
    assert r["errors"][0]["line"] == 18 and r["errors"][0]["message"].startswith("unknown directive")

    c = change(conf)
    c.apply({"kind": "location", "op": "create", "site": c.model["sites"][0]["id"], "data": {"path": "/x", "kind": "return", "code": "204"}})
    dry = edit.run(fake_nginx(tmp_path, True), str(conf / "nginx.conf"), c, False, "true", str(tmp_path / "hist"))
    assert dry["valid"] and not dry["applied"] and shop.read_text() == original and "+  location /x {" in dry["diff"]
    done = edit.run(fake_nginx(tmp_path, True), str(conf / "nginx.conf"), c, True, "true", str(tmp_path / "hist"), "alice")
    assert done["applied"] and "location /x" in shop.read_text() and done["version"] == 1
    h = edit.history(str(tmp_path / "hist"))
    assert h[0]["actor"] == "alice" and h[0]["summary"] == ["add route /x to shop.local"]
    back = edit.restore_change(str(tmp_path / "hist"), 1, model.model(str(conf / "nginx.conf")), str(conf))
    edit.run(fake_nginx(tmp_path, True), str(conf / "nginx.conf"), back, True, "true", str(tmp_path / "hist"))
    assert shop.read_text() == original


def test_check_errors_dedupes_log_and_stderr_lines(tmp_path):
    f = tmp_path / "a.conf"
    f.write_text("x;\nfrobnicate on;\n")
    out = (f'2026/11/09 10:00:00 [emerg] 74#74: unknown directive "frobnicate" in {f}:2\n'
           f'nginx: [emerg] unknown directive "frobnicate" in {f}:2\nnginx: configuration file test failed')
    assert edit.check_errors(out) == [{"file": str(f), "line": 2, "level": "emerg", "message": 'unknown directive "frobnicate"', "text": "frobnicate on;"}]
