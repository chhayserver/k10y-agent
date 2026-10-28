---
title: Changelog
---

# Changelog

Every release is a multi-arch image on Docker Hub (`k10y/k10y-agent`, amd64 and arm64) with `install.yaml`, `install-readonly.yaml` and `docker-compose.yml` attached to the [GitHub release](https://github.com/chhayserver/k10y-agent/releases).

## 2.4.0 <Badge type="tip" text="latest" />

- **New frontend, guided:** pick what you're setting up (microservice gateway by path, website, API, WebSocket, PostgreSQL, MySQL, Redis, MongoDB, other TCP) and fill in only what it needs, with realistic example values. One reviewed change creates the frontend and all its backends with their servers.
- **Every balancing method and health check:** round robin, least connections, random, first available, client IP, URL path/parameter, header, custom hash, RDP cookie; HTTP (method, path, Host), TCP, PostgreSQL, MySQL, Redis, TLS, SMTP and LDAP checks. Plus sticky sessions, X-Forwarded-For, timeouts, and server max connections and TLS. Each option says when to use it.
- **Add a route in one step:** path → new backend with its servers (or an existing one), placed before shorter paths.
- **Live numbers per route** (requests/s, error rate, response time) on Frontends and the Overview, and **live traffic on the topology**.
- **Charts up to 7 days** (15 min, 1 h, 6 h, 24 h, 7 days) from one-minute averages kept in PostgreSQL.
- **Maintenance page** per frontend, with addresses that still get through.
- **Block an IP instantly** (no reload after the first time), from **Rate limits & blocks** or a log line, and **unblock a rate-limited client**.
- **Copy a frontend or backend to another HAProxy**, and **compare configs** across a group with "make the same".
- **Works the way hand-written configs do:** k10y reads and edits inline conditions (`use_backend inbound-order if { path /order } || { path_beg /order/ }`, shown as "/order and /order/…"), `use_backend` without a condition, `X-Forwarded-Port`, certificate files on the HAProxy (`bind 0.0.0.0:443 ssl crt /etc/haproxy/new-tls.pem`), `option redispatch` + `retries`, check timing (`check inter 1s rise 3 fall 1`), `stick-table … stick on src` and TCP keep-alive. New routes are written inline in the same style, new servers copy the timing of the others, and a change writes only the part that changed, so other lines stay as written.
- **Templates in the same style:** Microservices by path (HTTPS 443, `/order` → `inbound-order` on NodePorts), Web app over HTTPS (like Mattermost, TLS to the server), RabbitMQ (stick on source IP 5 min, keep-alive, 300 s timeouts), plus API, WebSocket and databases.
- **Fix: the Data Plane API could crash** ("concurrent map iteration and map write", seen in v3.2.18) when two changes ran at the same moment, e.g. a preview while the Kubernetes sync ran. k10y now sends configuration and transaction requests to each HAProxy one at a time, retries reads while the Data Plane API restarts, and closes transactions a crash left open (and outdated ones when the limit of 20 is reached). Tested with 450 parallel previews: no crash.

## 2.3.1

- **OpenRouter as an AI provider:** one key for models from many vendors (Gemini, GPT, Claude, DeepSeek, Qwen…). **Load models** lists only models that support tool calling. Keys for Gemini, CodeCraft and OpenRouter are kept side by side; switch with one click when one is down.

## 2.3.0

- **HAProxy dashboard redesign:** a status line that says what's wrong, key numbers with trends (requests/s, error rate, response time, sessions, servers up), problems only when there are some, each frontend drawn as its routing (proxy, or path routing to several backends) with live server health, and one traffic chart where you pick the metric, compare by frontend or backend, and show only some. **Customize** chooses the sections; saved to your account.
- **Frontends and Backends pages as dashboards:** number tiles at the top that follow your filters (requests, error rate, sessions, response time, healthy backends or servers up). Frontends: filter by type (Proxy, Path routing, Other rules), problems, TLS, rate limit or IP allow-list, sort by traffic or errors, and switch between **Cards** (each frontend drawn as its routing) and **Table**. Backends: filter by the frontend that uses them.
- **"Services" are now called "Frontends"** in the HAProxy pages, the AI assistant and alerts, matching HAProxy's own terms. Old `/haproxy/services` links still work.

## 2.2.1

- **AI assistant for HAProxy:** investigate the whole HAProxy, one service or one backend with read-only tools (health checks, live rates, logs, config, certificates, recent changes). Proposed fixes go through the normal review; unsafe ones are refused.
- **Backends page as one table:** a row per backend with health bar, settings and traffic; click to expand its servers (state, weight and traffic share, health check, requests). Search, filters (Problems, From Kubernetes), sorting, expand/collapse all; backends with problems open by themselves.
- **Faster, more reliable AI** for Kubernetes and HAProxy: lower thinking level for Gemini 3 models during investigations, longer per-call timeout and one automatic retry. A clear message when the AI provider itself is failing (5xx), suggesting to retry or switch provider.

## 2.2.0

**One place for all your DevOps tools**
- **Systems:** a system switcher in the sidebar (Kubernetes, HAProxy, more to come). Each system has its own pages and picker; Alerts, Activity, Administration and My settings are shared.
- **Home:** the health of every cluster and HAProxy instance, firing alerts and recent changes on one screen. The default start page.
- New URLs (`/k8s/…`, `/haproxy/…`, `/admin/…`); old links redirect.
- A banner offers a reload when a newer dashboard is deployed.

**HAProxy management** (through the HAProxy Data Plane API v3)
- Services, backends and servers with live health, sessions, request rates and errors; a service → backend → server **topology**.
- **Live charts** (requests, 5xx, sessions, response time) and **live request logs** (syslog to the hub).
- **Drain, maintenance, back in service** instantly; **weights** for canaries; a guided **rolling deploy**.
- Add, edit and remove **services, backends, servers and routes** (path, host, header, client IP), **TLS listeners**, **IP allow-lists**, **HTTPS redirect** and **per-IP rate limits**, or edit the raw config. Every change is a transaction with a **diff**, **HAProxy's own validation**, a graceful reload and a confirmed result.
- **Certificates** (upload, replace, expiry), **stick tables**, **config history with rollback** (including changes made outside k10y).
- **Groups:** apply one change to several HAProxy at once.
- **Alerts** for HAProxy: server down, backend down, 5xx rate, sessions near the limit, certificate expiry, unreachable.
- **Kubernetes link:** keep a backend in sync with a Kubernetes Service (node ports or pod IPs).
- New permissions `haproxy.view`, `haproxy.operate`, `haproxy.configure`; Administration → HAProxy instances.

**Upgrade notes:** use the new `docker-compose.yml`: it publishes UDP 5514 for HAProxy logs. The database is upgraded automatically.

## 2.1.0

**New**
- **Pod shell:** an interactive terminal in any running container, in the browser. Needs the new `pods.exec` permission; every session is audited.
- **Edit YAML** in the details panel, like `kubectl edit`: a line-by-line diff, dry run, and a conflict check if someone else changed the object. New permission `resources.edit`.
- **Sessions:** see your signed-in devices and sign them out. Admins can sign a user out everywhere, and disable or enable accounts.
- **Settings follow you:** sidebar pages, theme, refresh, start page and chart range are saved to your account.
- **Automatic backups** of the hub database (daily, newest 14 kept), plus `make hub-backup` and `make hub-restore`.
- New sign-in page with the k10y-agent logo, a show/hide button on every password field, and a password strength meter.

**Security**
- Agents are **API-only**: the old built-in dashboard and `K10Y_MODE=dashboard` were removed, and cross-origin browser calls are off by default.

**Upgrade notes:** apply the new `install.yaml` (it adds `pods/exec` to the control role) and use the new `docker-compose.yml` (it adds the `backup` service). `pods.exec` isn't in the Operator role by default.

## 2.0.0

- **The hub:** a central server with PostgreSQL, login, fully custom roles (per cluster or global), encrypted secrets and a permanent audit log. The browser only talks to the hub, and agents are reached over HTTPS.
- **HTTPS everywhere**, with self-signed certificates by default. No Ingress needed.
- **New dashboard** in Vue 3 + TypeScript.
- **Alerts:** problem, CPU/memory threshold and unreachable-cluster rules, with Telegram notifications and history.
- **Live log streaming** for a pod or every pod of a workload.
- New pages: **CronJobs** (run now, suspend, resume), **Autoscalers**, **Ingresses** and **Storage**.

**Upgrade notes:** agent URLs are now `https://`. Add your clusters in the hub; the old browser-storage dashboard is replaced.

## 1.5.0
- **Namespaces** page: status, pods, problems, deployments, services, CPU and memory per namespace.
- **Settings:** hide sidebar pages, choose theme, refresh interval and start page.

## 1.4.0
- **StatefulSets** (scale, restart), **DaemonSets** (restart) and **Jobs** (delete with their pods) pages.
- Under-replicated StatefulSets and DaemonSets, and failed Jobs, appear under Problems.

## 1.3.0
- **Details panel** for every resource: overview, related objects with drill-down, live YAML and events.

## 1.2.1
- Keep keys for **both** AI providers and switch between them with one click.

## 1.2.0
- **AI assistant** with Google Gemini or any OpenAI-compatible API. It reads with read-only tools, explains root causes and proposes fixes. Nothing changes until a person approves.

## 1.1.0
- **One-command install:** `kubectl apply -f install.yaml`, and the agent creates its own API token.

## 1.0.0
- First release: monitoring, problems, logs and events; control actions with dry run, protected namespaces and audit; the map; multi-cluster dashboard; live CPU and memory charts; multi-arch image.
