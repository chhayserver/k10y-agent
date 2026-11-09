"""Nginx configuration -> sites, locations and upstreams, with the file and line of each.

crossplane parses nginx.conf and every included file; `combine()` follows the includes in order so the tree reads
like `nginx -T`. Variables are left as they are: k10y shows what's written, it doesn't evaluate it.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import crossplane

Directive = Dict[str, Any]

LB_METHODS = ("least_conn", "ip_hash", "hash", "random", "least_time")


def parse(conf: str) -> Dict[str, Any]:
    """The whole configuration as one tree, plus parse errors (file, line, message)."""
    payload = crossplane.parse(conf, catch_errors=True, comments=False, combine=False, check_ctx=False, check_args=False)
    files = payload.get("config") or []
    errors = [{"file": e.get("file"), "line": e.get("line"), "message": e.get("error")} for f in files for e in f.get("errors") or []]
    tree = combine(files, 0, set()) if files else []
    return {"tree": tree, "errors": errors, "files": [f["file"] for f in files]}


def combine(files: List[Dict[str, Any]], index: int, seen: set) -> List[Directive]:
    """Directives of file `index`, with each include replaced by the included files' directives."""
    if index in seen:   # include loop
        return []
    seen = seen | {index}
    out: List[Directive] = []
    path = files[index]["file"]

    def walk(items: List[Directive]) -> List[Directive]:
        res = []
        for d in items:
            d = {**d, "file": path}
            if d["directive"] == "include" and d.get("includes"):
                for i in d["includes"]:
                    res.extend(combine(files, i, seen))
                continue
            if "block" in d:
                d["block"] = walk(d["block"])
            res.append(d)
        return res

    out.extend(walk(files[index].get("parsed") or []))
    return out


def ref(d: Directive) -> str:
    return f"{d['file']}:{d['line']}"


def first(block: List[Directive], name: str) -> Optional[Directive]:
    return next((d for d in block if d["directive"] == name), None)


def every(block: List[Directive], name: str) -> List[Directive]:
    return [d for d in block if d["directive"] == name]


def arg(block: List[Directive], name: str, default: Any = None) -> Any:
    d = first(block, name)
    return " ".join(d["args"]) if d else default


# --- listen / server -------------------------------------------------------------------------

def listen(args: List[str]) -> Dict[str, Any]:
    """`listen 443 ssl http2` / `listen [::]:80 default_server` / `listen unix:/run/x.sock`."""
    target = args[0] if args else "80"
    flags = set(args[1:])
    host, port = "*", target
    if target.startswith("unix:"):
        host, port = target, None
    elif target.startswith("["):
        host, _, port = target[1:].partition("]")
        port = port.lstrip(":") or "80"
    elif ":" in target:
        host, port = target.rsplit(":", 1)
    elif not target.isdigit():
        host, port = target, "80"
    return {"address": host, "port": int(port) if port and port.isdigit() else None, "ssl": "ssl" in flags,
            "http2": "http2" in flags, "default": "default_server" in flags or "default" in flags,
            "proxy_protocol": "proxy_protocol" in flags, "udp": "udp" in flags, "text": " ".join(args)}


def location(d: Directive) -> Dict[str, Any]:
    args = d["args"]
    modifier, path = ("", args[0]) if len(args) == 1 else (args[0], args[1])
    match = {"=": "exact", "~": "regex", "~*": "regex-i", "^~": "prefix-priority", "": "prefix"}.get(modifier, "prefix")
    if path.startswith("@"):
        match = "named"
    block = d.get("block") or []
    proxy = arg(block, "proxy_pass") or arg(block, "grpc_pass") or arg(block, "fastcgi_pass") or arg(block, "uwsgi_pass")
    ret = first(block, "return")
    kind = ("proxy" if proxy else "redirect" if ret and ret["args"] and ret["args"][0] in ("301", "302", "303", "307", "308")
            else "return" if ret else "static" if first(block, "root") or first(block, "alias") or first(block, "try_files") else "other")
    return {"id": ref(d), "file": d["file"], "line": d["line"], "match": match, "path": path, "modifier": modifier, "kind": kind,
            "proxy_pass": proxy, "upstream": pass_target(proxy), "return": " ".join(ret["args"]) if ret else None,
            "root": arg(block, "root"), "alias": arg(block, "alias"), "try_files": arg(block, "try_files"),
            "rewrites": [" ".join(x["args"]) for x in every(block, "rewrite")],
            "allow": [" ".join(x["args"]) for x in every(block, "allow")], "deny": [" ".join(x["args"]) for x in every(block, "deny")],
            "limit_req": arg(block, "limit_req"), "auth_basic": arg(block, "auth_basic"),
            "websocket": any(x["args"][:2] == ["Upgrade", "$http_upgrade"] for x in every(block, "proxy_set_header")),
            "locations": [location(x) for x in every(block, "location")]}


def pass_target(proxy: Optional[str]) -> Optional[str]:
    """proxy_pass http://backend/api -> "backend"; http://10.0.0.5:8080 -> "10.0.0.5:8080"."""
    if not proxy:
        return None
    m = re.match(r"^(?:[a-z]+://)?([^/\s]+)", proxy)
    return m.group(1) if m else proxy


def site(d: Directive, context: str) -> Dict[str, Any]:
    block = d.get("block") or []
    names = [n for x in every(block, "server_name") for n in x["args"]]
    listens = [listen(x["args"]) for x in every(block, "listen")] or [listen(["80" if context == "http" else ""])]
    locs = [location(x) for x in every(block, "location")]
    direct = arg(block, "proxy_pass")  # stream servers
    ret = first(block, "return")
    kinds = {l["kind"] for l in locs}
    kind = ("tcp" if context == "stream" else "redirect" if ret and not locs else
            "proxy" if kinds == {"proxy"} else "static" if kinds <= {"static"} and kinds else "mixed" if kinds else "other")
    return {"id": ref(d), "file": d["file"], "line": d["line"], "context": context, "server_names": names or ["_"],
            "listen": listens, "ssl": any(l["ssl"] for l in listens) or (arg(block, "ssl") == "on"),
            "ssl_certificate": arg(block, "ssl_certificate"), "ssl_certificate_key": arg(block, "ssl_certificate_key"),
            "root": arg(block, "root"), "return": " ".join(ret["args"]) if ret else None,
            "proxy_pass": direct, "upstream": pass_target(direct), "access_log": arg(block, "access_log"),
            "kind": kind, "locations": locs}


def upstream(d: Directive, context: str) -> Dict[str, Any]:
    block = d.get("block") or []
    method = next((x["directive"] for x in block if x["directive"] in LB_METHODS), "round_robin")
    servers = []
    for x in every(block, "server"):
        address = x["args"][0] if x["args"] else ""
        opts = {k: v for k, _, v in (a.partition("=") for a in x["args"][1:])}
        host, _, port = address.rpartition(":") if ":" in address and not address.startswith("unix:") else (address, "", "")
        servers.append({"id": ref(x), "line": x["line"], "address": address, "host": host or address,
                        "port": int(port) if port.isdigit() else (80 if context == "http" else None),
                        "weight": int(opts.get("weight", 1)) if str(opts.get("weight", 1)).isdigit() else 1,
                        "max_fails": opts.get("max_fails"), "fail_timeout": opts.get("fail_timeout"), "max_conns": opts.get("max_conns"),
                        "backup": "backup" in x["args"][1:], "down": "down" in x["args"][1:]})
    m = first(block, method) if method != "round_robin" else None
    return {"id": ref(d), "file": d["file"], "line": d["line"], "context": context, "name": d["args"][0] if d["args"] else "",
            "method": method, "method_args": " ".join(m["args"]) if m else "", "keepalive": arg(block, "keepalive"),
            "servers": servers}


def model(conf: str) -> Dict[str, Any]:
    """Sites (server blocks), upstreams, and where logs go, for http and stream."""
    parsed = parse(conf)
    tree = parsed["tree"]
    out: Dict[str, Any] = {"conf": conf, "files": parsed["files"], "errors": parsed["errors"], "sites": [], "upstreams": [],
                           "log_formats": {}, "access_logs": [], "error_logs": []}
    for top in tree:
        if top["directive"] == "error_log":
            out["error_logs"].append(top["args"][0])
        if top["directive"] not in ("http", "stream"):
            continue
        context, block = top["directive"], top.get("block") or []
        for d in block:
            if d["directive"] == "server":
                out["sites"].append(site(d, context))
            elif d["directive"] == "upstream":
                out["upstreams"].append(upstream(d, context))
            elif d["directive"] == "log_format" and d["args"]:
                out["log_formats"][d["args"][0]] = " ".join(a for a in d["args"][1:] if not a.startswith("escape="))
            elif d["directive"] == "access_log" and d["args"]:
                out["access_logs"].append({"context": context, "path": d["args"][0], "format": d["args"][1] if len(d["args"]) > 1 else "combined"})
            elif d["directive"] == "error_log" and d["args"]:
                out["error_logs"].append(d["args"][0])
    # Per-site logs too (they win over the http-level one for that site).
    for s in out["sites"]:
        if s["access_log"] and s["access_log"] != "off":
            path, _, fmt = s["access_log"].partition(" ")
            if path not in {a["path"] for a in out["access_logs"]}:
                out["access_logs"].append({"context": s["context"], "path": path, "format": fmt.split(" ")[0] or "combined"})
    names = {u["name"] for u in out["upstreams"]}
    for s in out["sites"]:
        for l in walk_locations(s["locations"]):
            l["upstream_known"] = l["upstream"] in names
        s["upstreams"] = sorted({l["upstream"] for l in walk_locations(s["locations"]) if l["upstream"] in names}
                                | ({s["upstream"]} if s["upstream"] in names else set()))
    for u in out["upstreams"]:
        u["used_by"] = [s["id"] for s in out["sites"] if u["name"] in s["upstreams"]]
    return out


def walk_locations(locs: List[Dict[str, Any]]):
    for l in locs:
        yield l
        yield from walk_locations(l.get("locations") or [])


def find_stub_status(conf: str) -> Optional[str]:
    """Look through the raw tree for `stub_status` (it isn't part of the location model)."""
    tree = parse(conf)["tree"]
    for top in tree:
        if top["directive"] != "http":
            continue
        for srv in top.get("block") or []:
            if srv["directive"] != "server":
                continue
            ports = [listen(x["args"]) for x in every(srv.get("block") or [], "listen")]
            port = next((p["port"] for p in ports if p["port"] and not p["ssl"]), None)
            for loc in every(srv.get("block") or [], "location"):
                if first(loc.get("block") or [], "stub_status") and port:
                    path = loc["args"][-1]
                    return f"http://127.0.0.1:{port}{path}"
    return None


def match_location(locs: List[Dict[str, Any]], uri: str) -> Optional[Dict[str, Any]]:
    """Which location Nginx picks for `uri`: exact, then longest prefix (^~ stops), then the first regex, else
    the longest prefix. Nested locations are searched inside the chosen one."""
    path = uri.split("?", 1)[0]
    plain = [l for l in locs if l["match"] in ("prefix", "prefix-priority", "exact")]
    for l in plain:
        if l["match"] == "exact" and l["path"] == path:
            return l
    best = max((l for l in plain if l["match"] != "exact" and path.startswith(l["path"])), key=lambda l: len(l["path"]), default=None)
    if best is None or best["match"] != "prefix-priority":
        for l in locs:
            if l["match"] in ("regex", "regex-i"):
                try:
                    if re.search(l["path"], path, re.I if l["match"] == "regex-i" else 0):
                        return match_location(l.get("locations") or [], uri) or l
                except re.error:
                    continue
    if best is not None:
        return match_location(best.get("locations") or [], uri) or best
    return None


def pick_site(sites: List[Dict[str, Any]], host: str, server_name: str = "", port: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """The server block a request went to: by $server_name (exact), else by Host like Nginx does (exact, leading
    wildcard, trailing wildcard, regex), else the default server for the port."""
    http = [s for s in sites if s["context"] == "http" and (port is None or any(l["port"] == port for l in s["listen"]))] \
        or [s for s in sites if s["context"] == "http"]
    if server_name:
        hit = next((s for s in http if server_name in s["server_names"]), None)
        if hit:
            return hit
    host = (host or "").split(":", 1)[0].lower()
    for test in (lambda n: n == host, lambda n: n.startswith("*.") and host.endswith(n[1:]),
                 lambda n: n.startswith(".") and (host == n[1:] or host.endswith(n)),
                 lambda n: n.endswith(".*") and host.startswith(n[:-1]),
                 lambda n: n.startswith("~") and _re(n[1:], host)):
        hit = next((s for s in http if any(test(n.lower()) for n in s["server_names"])), None)
        if hit:
            return hit
    return next((s for s in http if any(l["default"] for l in s["listen"])), http[0] if http else None)


def _re(pattern: str, value: str) -> bool:
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return False


def upstream_for_address(upstreams: List[Dict[str, Any]], addr: str, resolved: Dict[str, List[str]]) -> Tuple[Optional[str], Optional[str]]:
    """$upstream_addr "10.0.0.7:8080" -> (upstream name, server address as written), using resolved host names."""
    for u in upstreams:
        for srv in u["servers"]:
            if srv["address"] == addr:
                return u["name"], srv["address"]
            host, _, port = addr.rpartition(":")
            if str(srv["port"]) == port and host in resolved.get(srv["host"], []):
                return u["name"], srv["address"]
    return None, None
