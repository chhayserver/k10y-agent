# Nginx

k10y shows every Nginx you connect: its sites (server blocks) with their routes, upstreams with live server
health, requests per second, errors and response time per site, route and upstream server, the access and error
logs, TLS certificates and the full configuration. Alerts warn when a server stops answering, errors rise or a
certificate is about to expire.

Nginx doesn't have a management API, so a small **k10y agent runs next to it**: it reads the configuration
(every included file), follows the access log, checks each upstream server, and answers the hub over HTTPS with
its own token.

::: tip Coming next
Draining upstream servers and rolling deploys, IP blocking, rate limits and certificates come in the next releases.
:::

## Connect an Nginx

### In Docker Compose

Use the `k10y/k10y-nginx` image instead of `nginx`. It's the official Nginx image plus the agent, with the same
config mounts:

```yaml
services:
  nginx:
    image: k10y/k10y-nginx:latest
    ports: ["80:80", "443:443", "9443:9443"]   # 9443: the k10y agent (the hub must reach it)
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./conf.d:/etc/nginx/conf.d
      - k10y:/var/lib/k10y                   # keeps the agent's token and certificate
volumes:
  k10y:
```

The token is created on first start: `docker compose exec nginx cat /var/lib/k10y/token`.

### On a server

On the Nginx server (Debian, Ubuntu, RHEL, Rocky, Alma… with systemd and Python 3.9 or newer):

```bash
curl -fsSLO https://github.com/chhayserver/k10y-agent/releases/latest/download/install-nginx-agent.sh
less install-nginx-agent.sh            # read it first
sudo sh install-nginx-agent.sh
```

It installs the agent in `/opt/k10y-nginx`, starts the `k10y-nginx-agent` service on port **9443** and prints the
token (also in `/etc/k10y/nginx-token`). Settings are in `/etc/k10y/nginx-agent.env`. Run it again to upgrade.
The service runs as root (to read every config file and log, and later to reload Nginx) with the rest of the
system read-only.

### In the hub

**Administration → Nginx instances → Add Nginx** (needs `nginx.manage`): the agent's address (just the host name
is enough; port 9443 and `https://` are added) and its token. **Test** shows the Nginx and agent versions. Then
pick **Nginx** in the system switcher.

## Numbers per site, route and server

Open-source Nginx only counts requests in total, so the agent reads the **access log**. For numbers per site,
route and upstream server, log in k10y's format: add this to `http {}` and point an `access_log` at a file
(not only stdout):

```nginx
log_format k10y escape=json '{"time":"$time_iso8601","host":"$host","server":"$server_name","port":"$server_port","method":"$request_method","uri":"$request_uri","status":$status,"bytes":$body_bytes_sent,"rt":$request_time,"ua":"$upstream_addr","us":"$upstream_status","urt":"$upstream_response_time","ip":"$remote_addr","referer":"$http_referer","agent":"$http_user_agent"}';
access_log /var/log/nginx/k10y-access.log k10y;
```

The Overview has a **Copy log_format** button. With the standard `combined` format you get totals and routes,
but not sites or upstream servers. A `stub_status` location (for example `location /nginx_status { stub_status; allow 127.0.0.1; deny all; }`)
adds open connections.

## Pages

| Page | What you see |
|---|---|
| **Overview** | Status, requests/s, 5xx share, response time (p95), connections, upstream servers answering, problems, a traffic chart, and every site and upstream |
| **Sites** | Each server block: names, ports, TLS, default server, file and line; its routes (`location`s, nested too) with what each does (proxy, static files, redirect, fixed answer), where it goes, WebSocket, rate limits, IP rules, password, rewrites, and live req/s, 5xx and p95 per route |
| **Upstreams** | Balancing method, keepalive, which sites use it; each server with its state (up, backup, down in the config, not answering), weight, k10y's check result, its share of traffic, req/s, 5xx and p95 |
| **Logs** | Live requests (status, request, site, upstream server, time, client) with filters, and the error log |
| **Certificates** | Every `ssl_certificate`: domains, issuer, days left, the sites using it |
| **Raw config** | `nginx -T`: every file as Nginx reads it (needs `nginx.configure`, as it can contain secrets) |

k10y's upstream check is a TCP connection to each server every 10 seconds. Nginx itself marks a server failed
only after real requests fail (`max_fails`), so k10y often sees a problem first.

## Change the configuration

With `nginx.configure` on an instance:

- **Sites → Add site:** start from what it's for: an **app or API** (to an existing upstream, or a new one with its servers), a **static website** (a folder) or a **redirect** of a domain. Optionally HTTPS with a certificate and key file, and a redirect from http://.
- On each site: **Add route**, **Edit** or **Remove** a route (proxy with WebSocket and timeout, redirect, static files, fixed answer, and an optional IP allow-list), **Names and ports**, **Remove site**.
- **Upstreams → Add upstream / Edit:** balancing (round robin, least connections, client IP, hash, random), servers with weight, max fails, fail timeout, backup and down, and keepalive.
- **Raw config → Edit a file…** for anything else.

Every change opens a review first: the exact **diff**, and **`nginx -t`'s verdict**. If Nginx refuses it, each problem is shown with its **file, line and text**, and nothing changes. **Apply** writes the files, checks them again and reloads Nginx gracefully; if the check or the reload fails, the old files are put back.

How k10y edits your files:
- It changes **only the block** being edited (a route, an upstream, a site's `server_name` and `listen` lines) and leaves the rest of the file exactly as written: your comments, spacing and order. New blocks use the file's own indentation.
- New sites and upstreams go to a new file in `conf.d/` (or `sites-enabled/`) named after them.
- A form manages the directives it shows. If a route or upstream has others (for example `proxy_buffering`), the form says so: saving it would replace them, so edit the file in Raw config instead.
- One change at a time per Nginx.

**Config history** lists every change applied through k10y (version, time, who, what, files) with its diff. **Restore** puts the files back as they were before a version, through the same review. Changes made by hand on the server aren't listed, but they're visible in Raw config.

## Permissions

Roles can be given on **all of Nginx** or on **one Nginx instance** (Administration → Users).

| Permission | Allows |
|---|---|
| `nginx.view` | See sites, routes, upstreams, live numbers, logs and certificates |
| `nginx.operate` | Drain and maintenance of upstream servers, block IPs (coming next) |
| `nginx.configure` | Add, change and remove sites, routes and upstreams; edit any config file; restore old versions; read the raw config |
| `nginx.manage` | Add, edit and remove Nginx instances |

Built-in roles: **Nginx viewer**, **Nginx operator** and **Nginx admin** (Admin has everything).

## Alerts

Rules of type **Nginx**: an upstream server doesn't answer, an upstream has no server answering, 5xx above a
threshold, a certificate expiring, Nginx stopped or its config unreadable, or the agent unreachable. For all
instances or one.

## Try it: the Nginx lab

`make nginx-lab-up` starts `k10y/k10y-nginx` in front of five small apps on `http://localhost:8189` (shop on
three servers with least connections, one a backup; `/api/` on two), with the agent on `https://localhost:9443`.
The token: `docker exec k10y-nginx-lab-nginx-1 cat /var/lib/k10y/token`. `make nginx-lab-down` removes it.
