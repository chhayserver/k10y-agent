---
title: Changelog
---

# Changelog

Every release is a multi-arch image on Docker Hub (`k10y/k10y-agent`, amd64 and arm64) with `install.yaml`, `install-readonly.yaml` and `docker-compose.yml` attached (plus the optional `rbac-helm.yaml` from 2.7) to the [GitHub release](https://github.com/chhayserver/k10y-agent/releases).

## 2.7.1 <Badge type="tip" text="latest" />

- **New website address:** the docs and website moved to **https://k10y-agents.pages.dev** (the old k10y.pages.dev no longer works). Links in the dashboard point there.
- **License:** k10y is free software under the **GNU AGPL-3.0**. The image now carries the license (`/usr/share/doc/k10y/LICENSE`) and an OCI license label.
- **CI** runs tests, the type check and the image build on every pull request.

## 2.7.0

- **Two-factor login:** a code from an authenticator app at every sign-in, with 10 single-use recovery codes. Set it up in **My settings**; require it for admins or everyone in **System settings → Sign-in security**; admins can reset it for a lost phone.
- **More alert channels:** Slack, email (SMTP) and webhooks (JSON, optionally signed with HMAC-SHA256), next to Telegram.
- **Acknowledge and silence alerts:** acknowledging stops reminders and tells the channels who's on it; silences mute one alert (also when it comes back) or a whole rule for 30 minutes up to a week.
- **Full-volume alerts:** Storage shows how full each PVC is, and alerts warn at 90% (bytes or inodes). Read from the kubelets with the read-only `nodes/stats` permission.
- **Helm rollback from k10y** (opt-in): dry run, then `helm rollback` on the agent, with review and audit. Needs `K10Y_HELM_ENABLED=true` and `rbac-helm.yaml`.
- **Open a Service in the browser** through the hub: one-hour, sandboxed links, with the new `services.proxy` permission.
- **HAProxy topology:** live traffic down to each server (requests/s and 5xx on every backend → server line).
- **HAProxy review:** when HAProxy rejects a change, each problem is shown with its line, section and text.
- Pages that need a newer agent say so instead of showing an error.
- Browser tests in the repo (`make e2e`), and the hub's startup moved to FastAPI's lifespan.
- **Agent RBAC:** `nodes/stats` (read) and `services/proxy` (control). Re-apply `install.yaml` when upgrading.

## 2.6.0

- **Protected namespaces in the hub:** choose each cluster's protected namespaces in **System settings → Protected namespaces**, with the new `namespaces.protect` permission (Admin and Kubernetes admin have it; it can be given per cluster). The agent keeps the list in its own Secret, so it survives restarts and upgrades, and every change is in the activity log.
- **Add cluster: just type the address.** Enter `192.168.64.2:30800` without a scheme; k10y finds how the agent answers. The agent serves HTTPS itself, also through a NodePort, so `https://` is added for you, and an `http://` address is switched to `https://` when the agent speaks TLS (before, it failed with "Server disconnected").
- **Search everything (Ctrl/⌘ K):** find any Kubernetes object or Helm release in every cluster, and HAProxy frontends, backends and servers, from the top bar.
- **Rollout history:** a Deployment's **Revisions** tab lists each revision with images and who changed it, compares two revisions, and rolls back to any one.
- **Helm releases:** charts, versions, status and history; values and manifest (secrets masked, needs `secrets.view`); compare revisions; copy the rollback command.
- **Workload logs:** pick a pod or all, start from the last 15 min / hour / 6 hours, errors only, highlighted matches.
- **Smarter alerts:** OOMKilled, stuck rollouts, failed or late CronJobs, PVCs pending or lost, and TLS certificates expiring within 14 days join the problem list.
- **Certificates page** with days left for Ingress TLS secrets and cert-manager Certificates.
- **CronJob runs** and **namespace quotas** in the details panels.
- **Right-sizing:** real CPU (p95) and memory (peak) per pod against requests and limits, with recommended values and what you'd free.
- **7-day history:** the hub keeps 5-minute samples for 7 days; Overview, Nodes and pods get 6 h, 24 h and 7 d ranges.
- **Service → HAProxy:** a Service shows the HAProxy backends that send traffic to it.
- **Agent RBAC:** read access to ResourceQuotas, LimitRanges and cert-manager Certificates. Re-apply `install.yaml` when upgrading.

## 2.5.0

- **HAProxy permissions per instance:** a role can now be assigned on **one HAProxy instance** (like one cluster), so someone can operate `lb-edge-1` without seeing or touching the others. Lists, pages, buttons, alerts and group changes all follow it.
- **New `haproxy.manage` permission** for adding, editing and removing HAProxy instances and their Data Plane API logins (it used to be part of `clusters.manage`). Roles that had `clusters.manage` get it automatically.
- **Roles per system:** built-in **Kubernetes viewer / operator / admin** and **HAProxy viewer / operator / admin**, plus Admin. The old Operator and Viewer become the Kubernetes ones, and their users automatically get the matching HAProxy role, so nobody loses access. HAProxy operator and admin can use the HAProxy AI assistant on their instance.
- **Built-in roles pick up new permissions on upgrade** (once; a permission you removed stays removed).
- **Clearer permissions on the Roles page:** HAProxy permissions have their own section, and each says exactly what it allows.
- **Fix:** the first IP block (which adds the blocklist to every frontend) now needs `haproxy.configure`, since it changes the config.

## 2.4.1

- **Fix:** in a resource's details panel, a long container image no longer wraps one letter per line, and the "Image" and "Ports" headers no longer run together.
- **Fix:** table headers without a sort button (resource panels) now line up with their values.

## 2.4.0

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
