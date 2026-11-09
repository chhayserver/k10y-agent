"""Change Nginx configuration files by editing their text, so comments, spacing and layout stay as written.

A directive is found by the line crossplane reports; its extent (to `;` or the matching `}`) is found with a small
tokenizer that understands quotes, escapes and comments. New blocks are rendered with the file's own indentation.
Applying a change: write the files, `nginx -t`, put the old files back if the check fails (or on a dry run),
otherwise reload. Every applied change is kept in the history with the files before and after.
"""

import difflib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOCK = threading.Lock()
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class EditError(Exception):
    pass


# --- finding a directive's text ----------------------------------------------------------------------

def line_offset(text: str, line: int) -> int:
    """Offset of the start of 1-based `line`."""
    off = 0
    for _ in range(line - 1):
        nl = text.find("\n", off)
        if nl < 0:
            raise EditError(f"line {line} is past the end of the file")
        off = nl + 1
    return off


def span(text: str, line: int, name: str) -> Tuple[int, int]:
    """(start, end) of the directive `name` that starts on `line`: from its first character to its `;` or the
    matching `}` (inclusive), skipping quoted strings and comments."""
    start = line_offset(text, line)
    m = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w])").search(text, start)
    if not m or "\n" in text[start:m.start()]:
        raise EditError(f"can't find '{name}' on line {line} (the file may have changed: reload the page)")
    i, depth, n = m.start(), 0, len(text)
    quote: Optional[str] = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return m.start(), i + 1
            if depth < 0:
                raise EditError(f"unbalanced braces after line {line}")
        elif c == ";" and depth == 0:
            return m.start(), i + 1
        i += 1
    raise EditError(f"'{name}' on line {line} doesn't end")


def whole_lines(text: str, start: int, end: int) -> Tuple[int, int]:
    """Grow a span to whole lines when nothing else shares them (so removing it leaves no blank indentation)."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    if text[ls:start].strip() == "" and text[end:le].strip() == "":
        return ls, min(len(text), le + 1)
    return start, end


def indent_of(text: str, offset: int) -> str:
    ls = text.rfind("\n", 0, offset) + 1
    return re.match(r"[ \t]*", text[ls:]).group(0)


def indent_unit(text: str) -> str:
    """The file's indentation step (tab, or the smallest run of spaces used), 4 spaces if it can't tell."""
    if re.search(r"^\t+\S", text, re.M):
        return "\t"
    sizes = sorted({len(m) for m in re.findall(r"^( +)\S", text, re.M)})
    return " " * (sizes[0] if sizes else 4)


# --- rendering -----------------------------------------------------------------------------------

def quote(v: str) -> str:
    """An argument as Nginx needs it: as is when safe or already quoted, otherwise in double quotes."""
    v = str(v)
    if re.match(r"^[^\s;{}\"'#]+$", v) or (len(v) > 1 and v[0] == v[-1] == '"'):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(directives: List[Any], indent: str, unit: str) -> str:
    """[(name, [args]) | (name, [args], [children])] -> text, one directive per line."""
    out = []
    for d in directives:
        name, args = d[0], [a for a in d[1] if a != ""]
        head = " ".join([name] + [quote(a) for a in args])
        if len(d) > 2:
            out.append(f"{indent}{head} {{")
            out.append(render(d[2], indent + unit, unit))
            out.append(f"{indent}}}")
        else:
            out.append(f"{indent}{head};")
    return "\n".join(x for x in out if x != "")


LOCATION_KINDS = ("proxy", "redirect", "static", "return")


def location_directives(spec: Dict[str, Any]) -> list:
    """A route from a form: where it matches and what it does."""
    match = {"prefix": [], "exact": ["="], "prefix-priority": ["^~"], "regex": ["~"], "regex-i": ["~*"]}.get(spec.get("match", "prefix"))
    if match is None:
        raise EditError("unknown match type")
    path = str(spec.get("path") or "").strip()
    if not path or any(c in path for c in "{};\n"):
        raise EditError("the path is empty or has characters Nginx can't take")
    if spec.get("match", "prefix") in ("regex", "regex-i"):
        try:
            re.compile(path)
        except re.error as e:
            raise EditError(f"the regular expression is invalid: {e}")
    kind, body = spec.get("kind", "proxy"), []
    if kind == "proxy":
        target = str(spec.get("target") or "").strip()
        if not target:
            raise EditError("a proxy route needs a target (an upstream or an address)")
        url = target if re.match(r"^https?://", target) else f"http://{target}"
        body.append(("proxy_pass", [url]))
        body += [("proxy_set_header", ["Host", "$host"]), ("proxy_set_header", ["X-Real-IP", "$remote_addr"]),
                 ("proxy_set_header", ["X-Forwarded-For", "$proxy_add_x_forwarded_for"]),
                 ("proxy_set_header", ["X-Forwarded-Proto", "$scheme"])]
        if spec.get("websocket"):
            body += [("proxy_http_version", ["1.1"]), ("proxy_set_header", ["Upgrade", "$http_upgrade"]),
                     ("proxy_set_header", ["Connection", '"upgrade"'])]
        if spec.get("timeout"):
            body.append(("proxy_read_timeout", [str(spec["timeout"])]))
    elif kind == "redirect":
        code, to = str(spec.get("code") or "301"), str(spec.get("target") or "").strip()
        if code not in ("301", "302", "303", "307", "308") or not to:
            raise EditError("a redirect needs a code (301, 302, 307, 308) and where to send people")
        body.append(("return", [code, to]))
    elif kind == "static":
        root = str(spec.get("target") or "").strip()
        if not root.startswith("/"):
            raise EditError("static files need a folder (an absolute path)")
        body.append(("alias" if spec.get("alias") else "root", [root]))
        body.append(("try_files", ["$uri", "$uri/", "=404"]))
    elif kind == "return":
        code = str(spec.get("code") or "200")
        if not code.isdigit():
            raise EditError("the status must be a number")
        body.append(("return", [code] + ([json.dumps(str(spec["text"]))] if spec.get("text") else [])))
    else:
        raise EditError(f"kind must be one of {', '.join(LOCATION_KINDS)}")
    for ip in spec.get("allow") or []:
        body.append(("allow", [ip]))
    if spec.get("allow"):
        body.append(("deny", ["all"]))
    return [("location", match + [path], body)]


def upstream_directives(spec: Dict[str, Any]) -> list:
    name = str(spec.get("name") or "")
    if not NAME_RE.match(name):
        raise EditError("an upstream name has letters, digits, dot, dash or underscore")
    body: list = []
    method = spec.get("method", "round_robin")
    if method not in ("round_robin", "least_conn", "ip_hash", "hash", "random"):
        raise EditError("unknown balancing method")
    if method == "hash":
        body.append(("hash", [str(spec.get("hash_key") or "$request_uri"), "consistent"]))
    elif method != "round_robin":
        body.append((method, []))
    servers = spec.get("servers") or []
    if not servers:
        raise EditError("an upstream needs at least one server")
    for s in servers:
        addr = str(s.get("address") or "").strip()
        if not addr or any(c in addr for c in " ;{}"):
            raise EditError(f"server address {addr!r} isn't valid")
        args = [addr]
        if int(s.get("weight") or 1) != 1:
            args.append(f"weight={int(s['weight'])}")
        for k in ("max_fails", "fail_timeout", "max_conns"):
            if s.get(k) not in (None, ""):
                args.append(f"{k}={s[k]}")
        if s.get("backup"):
            args.append("backup")
        if s.get("down"):
            args.append("down")
        body.append(("server", args))
    if spec.get("keepalive"):
        body.append(("keepalive", [str(int(spec["keepalive"]))]))
    return [("upstream", [name], body)]


def server_directives(spec: Dict[str, Any]) -> list:
    names = [n for n in spec.get("server_names") or [] if n]
    if not names or any(any(c in n for c in " ;{}") for n in names):
        raise EditError("a site needs at least one valid name (e.g. shop.example.com)")
    body: list = []
    port = int(spec.get("port") or (443 if spec.get("tls") else 80))
    body.append(("listen", [str(port)] + (["ssl"] if spec.get("tls") else [])))
    if spec.get("tls"):
        body.append(("http2", ["on"]))
    body.append(("server_name", names))
    if spec.get("tls"):
        if not spec.get("ssl_certificate") or not spec.get("ssl_certificate_key"):
            raise EditError("TLS needs a certificate and a key file")
        body += [("ssl_certificate", [spec["ssl_certificate"]]), ("ssl_certificate_key", [spec["ssl_certificate_key"]])]
    for loc in spec.get("locations") or []:
        body += location_directives(loc)
    out = [("server", [], body)]
    if spec.get("tls") and spec.get("redirect_http"):
        out.append(("server", [], [("listen", ["80"]), ("server_name", names), ("return", ["301", "https://$host$request_uri"])]))
    return out


# --- changes -----------------------------------------------------------------------------------

class Change:
    """The new text of every file a list of operations touches."""

    def __init__(self, model: Dict[str, Any], conf_dir: str):
        self.model, self.conf_dir = model, conf_dir
        self.texts: Dict[str, str] = {}
        self.created: List[str] = []
        self.deleted: List[str] = []
        self.summary: List[str] = []

    def text(self, path: str) -> str:
        if path not in self.texts:
            self.texts[path] = Path(path).read_text()
        return self.texts[path]

    def splice(self, path: str, start: int, end: int, new: str) -> None:
        t = self.text(path)
        self.texts[path] = t[:start] + new + t[end:]

    def find(self, kind: str, ident: str) -> Dict[str, Any]:
        items = {"site": self.model["sites"], "upstream": self.model["upstreams"],
                 "location": [l for s in self.model["sites"] for l in _walk(s["locations"])]}[kind]
        for it in items:
            if it["id"] == ident or (kind == "upstream" and it["name"] == ident):
                return it
        raise EditError(f"{kind} {ident} not found (the config may have changed: reload the page)")

    def block_end_insert(self, path: str, line: int, name: str) -> Tuple[int, str]:
        """Where to add a child to the block `name` at `line` (just before its `}`), and the child indent."""
        t = self.text(path)
        start, end = span(t, line, name)
        close = end - 1
        base = indent_of(t, start)
        return t.rfind("\n", 0, close) + 1 if t[t.rfind("\n", 0, close) + 1:close].strip() == "" else close, base + indent_unit(t)

    def apply(self, op: Dict[str, Any]) -> None:
        kind, action = op.get("kind"), op.get("op")
        if kind == "location" and action == "create":
            site = self.find("site", op["site"])
            pos, ind = self.block_end_insert(site["file"], site["line"], "server")
            text = render(location_directives(op["data"]), ind, indent_unit(self.text(site["file"])))
            self.splice(site["file"], pos, pos, text + "\n")
            self.summary.append(f"add route {op['data'].get('path')} to {', '.join(site['server_names'])}")
        elif kind == "location" and action in ("update", "delete"):
            loc = self.find("location", op["id"])
            t = self.text(loc["file"])
            start, end = span(t, loc["line"], "location")
            if action == "delete":
                s, e = whole_lines(t, start, end)
                self.splice(loc["file"], s, e, "")
                self.summary.append(f"remove route {loc['path']}")
            else:
                new = render(location_directives(op["data"]), "", indent_unit(t)).replace("\n", "\n" + indent_of(t, start))
                self.splice(loc["file"], start, end, new)
                self.summary.append(f"change route {loc['path']}")
        elif kind == "upstream" and action == "create":
            path = op.get("file") or self.new_file(op["data"]["name"])
            if any(u["name"] == op["data"]["name"] for u in self.model["upstreams"]):
                raise EditError(f"there's already an upstream called {op['data']['name']}")
            self.add_http_block(path, upstream_directives(op["data"]))
            self.summary.append(f"add upstream {op['data']['name']} ({len(op['data'].get('servers') or [])} servers)")
        elif kind == "upstream" and action in ("update", "delete"):
            u = self.find("upstream", op["id"])
            t = self.text(u["file"])
            start, end = span(t, u["line"], "upstream")
            if action == "delete":
                if u["used_by"]:
                    raise EditError(f"upstream {u['name']} is still used by {len(u['used_by'])} site(s)")
                s, e = whole_lines(t, start, end)
                self.splice(u["file"], s, e, "")
                self.summary.append(f"remove upstream {u['name']}")
            else:
                data = {**op["data"], "name": u["name"]}
                new = render(upstream_directives(data), "", indent_unit(t)).replace("\n", "\n" + indent_of(t, start))
                self.splice(u["file"], start, end, new)
                self.summary.append(f"change upstream {u['name']}")
        elif kind == "site" and action == "create":
            name = (op["data"].get("server_names") or ["site"])[0]
            path = op.get("file") or self.new_file(name)
            if op["data"].get("upstream"):
                self.add_http_block(path, upstream_directives(op["data"]["upstream"]))
            self.add_http_block(path, server_directives(op["data"]))
            self.summary.append(f"add site {', '.join(op['data']['server_names'])}")
        elif kind == "site" and action == "delete":
            site = self.find("site", op["id"])
            t = self.text(site["file"])
            start, end = span(t, site["line"], "server")
            s, e = whole_lines(t, start, end)
            self.splice(site["file"], s, e, "")
            self.summary.append(f"remove site {', '.join(site['server_names'])}")
        elif kind == "site" and action == "update":
            site = self.find("site", op["id"])
            self.update_site_heads(site, op["data"])
        elif kind == "file" and action == "update":
            path = op["path"]
            if path not in self.model["files"] and not path.startswith(self.conf_dir + "/"):
                raise EditError("you can only edit files that belong to this Nginx's configuration")
            self.texts[path] = str(op["text"])
            self.summary.append(f"edit {path}")
        else:
            raise EditError(f"unknown change {kind}.{action}")

    def update_site_heads(self, site: Dict[str, Any], data: Dict[str, Any]) -> None:
        """Rename a site or change its ports: rewrite its server_name and listen lines, nothing else."""
        t = self.text(site["file"])
        start, end = span(t, site["line"], "server")
        block = t[start:end]
        names = [n for n in data.get("server_names") or [] if n]
        if not names:
            raise EditError("a site needs at least one name")
        ind = None
        for m in list(re.finditer(r"^([ \t]*)(server_name|listen)\b[^;]*;[ \t]*\n?", block, re.M))[::-1]:
            ind = ind or m.group(1)
            block = block[:m.start()] + block[m.end():]
        ind = ind or indent_of(t, start) + indent_unit(t)
        heads = [f"{ind}listen {l};" for l in data.get("listen") or [str(x["text"]) for x in site["listen"]]]
        heads.append(f"{ind}server_name {' '.join(names)};")
        brace = block.index("{") + 1
        nl = block.find("\n", brace)
        block = block[:nl + 1] + "\n".join(heads) + "\n" + block[nl + 1:]
        self.splice(site["file"], start, end, block)
        self.summary.append(f"change site {', '.join(site['server_names'])}")

    def new_file(self, name: str) -> str:
        """A new file for a new site or upstream, where this Nginx keeps its sites."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "site"
        for folder in ("conf.d", "sites-enabled", "http.d"):
            if any(f.startswith(os.path.join(self.conf_dir, folder) + "/") for f in self.model["files"]):
                path = os.path.join(self.conf_dir, folder, f"{slug}.conf")
                break
        else:
            raise EditError("this Nginx doesn't include a conf.d/ or sites-enabled/ folder; add the site to a file in Raw config")
        if os.path.exists(path) or path in self.texts:
            raise EditError(f"{path} already exists")
        self.created.append(path)
        self.texts[path] = ""
        return path

    def add_http_block(self, path: str, directives: list) -> None:
        """Append to a file that's included inside http {} (conf.d), or before the end of http {} in nginx.conf."""
        t = self.text(path)
        unit = indent_unit(t) if t else "    "
        if path == self.model["conf"]:
            m = re.search(r"^http\s*\{", t, re.M)
            if not m:
                raise EditError("no http {} block in the main configuration")
            _, end = span(t, t.count("\n", 0, m.start()) + 1, "http")
            pos = t.rfind("\n", 0, end - 1) + 1
            self.splice(path, pos, pos, render(directives, unit, unit) + "\n")
        else:
            sep = "" if not t or t.endswith("\n\n") else ("\n" if t.endswith("\n") else "\n\n")
            self.texts[path] = t + sep + render(directives, "", unit) + "\n"

    def diff(self) -> str:
        parts = []
        for path, new in self.texts.items():
            old = "" if path in self.created else Path(path).read_text()
            if old != new:
                parts.append("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), path if old else "/dev/null", path, n=3)))
        return "\n".join(parts)


def _walk(locs):
    for l in locs:
        yield l
        yield from _walk(l.get("locations") or [])


# --- checking and applying ---------------------------------------------------------------------

ERR_RE = re.compile(r"\[(emerg|alert|crit|error|warn)\] (.*?) in (/\S+?):(\d+)\s*$", re.M)


def check_errors(output: str) -> List[Dict[str, Any]]:
    """`nginx -t` output -> [{file, line, level, message, text}] for the lines k10y can point at."""
    out, seen = [], set()
    for m in ERR_RE.finditer(output):
        path, line = m.group(3), int(m.group(4))
        message = re.sub(r"^\d+#\d+: ", "", m.group(2).strip())     # the same error also comes as a log line
        if (path, line, message) in seen:
            continue
        seen.add((path, line, message))
        try:
            text = Path(path).read_text().splitlines()[line - 1].strip()
        except (OSError, IndexError):
            text = ""
        out.append({"file": path, "line": line, "level": m.group(1), "message": message, "text": text})
    return out


def run(nginx_bin: str, conf: str, change: Change, apply: bool, reload_cmd: str, history_dir: str, actor: str = "") -> Dict[str, Any]:
    """Write, `nginx -t`, and put everything back unless it's applied and valid. Then reload and record history."""
    if not change.texts:
        raise EditError("nothing to change")
    diff = change.diff()
    with LOCK:
        backups = {p: (Path(p).read_text() if os.path.exists(p) else None) for p in change.texts}
        try:
            for p, text in change.texts.items():
                if p in change.deleted:
                    Path(p).unlink(missing_ok=True)
                    continue
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                Path(p).write_text(text)
            t = subprocess.run([nginx_bin, "-t", "-c", conf], capture_output=True, text=True, timeout=30)
            output = (t.stderr + t.stdout).strip()
            valid = t.returncode == 0
            errors = check_errors(output) if not valid else []
        except Exception:
            restore(backups)
            raise
        if not apply or not valid:
            restore(backups)
            return {"valid": valid, "applied": False, "diff": diff, "errors": errors, "check": output[-3000:], "summary": change.summary}
        r = subprocess.run(reload_cmd.split(), capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            restore(backups)
            subprocess.run(reload_cmd.split(), capture_output=True, text=True, timeout=30)
            raise EditError(f"the reload failed, the old configuration was put back: {(r.stderr or r.stdout).strip()[-500:]}")
        entry = record(history_dir, backups, change.texts, change.summary, actor)
    return {"valid": True, "applied": True, "diff": diff, "errors": [], "check": output[-3000:], "summary": change.summary, "version": entry}


def restore(backups: Dict[str, Optional[str]]) -> None:
    for p, old in backups.items():
        if old is None:
            Path(p).unlink(missing_ok=True)
        else:
            Path(p).write_text(old)


# --- history -----------------------------------------------------------------------------------

def record(history_dir: str, before: Dict[str, Optional[str]], after: Dict[str, str], summary: List[str], actor: str) -> int:
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    existing = sorted(int(p.stem) for p in d.glob("*.json") if p.stem.isdigit())
    version = (existing[-1] + 1) if existing else 1
    (d / f"{version}.json").write_text(json.dumps({"version": version, "time": time.time(), "actor": actor, "summary": summary,
                                                   "before": before, "after": after}))
    for old in existing[:-199]:        # keep the last 200
        (d / f"{old}.json").unlink(missing_ok=True)
    return version


def history(history_dir: str) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(Path(history_dir).glob("*.json"), key=lambda x: -int(x.stem) if x.stem.isdigit() else 0):
        try:
            e = json.loads(p.read_text())
        except ValueError:
            continue
        out.append({k: e[k] for k in ("version", "time", "actor", "summary")} | {"files": sorted(e["after"])})
    return out


def version(history_dir: str, v: int) -> Dict[str, Any]:
    p = Path(history_dir) / f"{v}.json"
    if not p.exists():
        raise EditError(f"version {v} not found")
    e = json.loads(p.read_text())
    e["diff"] = "\n".join("".join(difflib.unified_diff((e["before"].get(f) or "").splitlines(True), (e["after"][f] or "").splitlines(True), f, f, n=3))
                          for f in sorted(e["after"]))
    return e


def restore_change(history_dir: str, v: int, model: Dict[str, Any], conf_dir: str) -> Change:
    """A change that puts the files of version `v` back as they were before it."""
    e = version(history_dir, v)
    c = Change(model, conf_dir)
    for path, old in e["before"].items():
        if old is None:
            if os.path.exists(path):
                c.texts[path] = ""
                c.deleted.append(path)
        else:
            c.texts[path] = old
    c.summary.append(f"restore the files from before version {v} ({'; '.join(e['summary'])})")
    return c
