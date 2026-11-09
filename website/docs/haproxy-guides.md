# HAProxy: how-to guides

Step-by-step guides for running your load balancers from k10y. Pick **HAProxy** in the system switcher and the
instance in the second switcher. Every configuration change goes through the same review: the exact lines of
`haproxy.cfg` that change and HAProxy's own validation, before **Apply**. Which buttons you see depends on your
[role](./users-roles) on that HAProxy.

[[toc]]

## Connect your HAProxy

1. Make sure the **Data Plane API v3** runs next to HAProxy (it's in the `haproxytech/haproxy-*` images) and the hub can reach its port (5555 by default).
2. **Administration → HAProxy instances → Add HAProxy** (or **Add HAProxy** in the instance switcher). Needs `haproxy.manage`.
3. Fill in **Name** (e.g. `edge-lb-1`), **Data Plane API URL** (e.g. `http://10.0.0.20:5555`), **Username** and **Password**, then **Test connection**.
4. For live request logs, see [Logs](./haproxy#logs).

## Put a website or app behind HAProxy

1. **Frontends → New frontend** → **Website or web app**.
2. Choose the port: **80**, or **HTTPS on 443** with a certificate (upload it on **Certificates** first) and a redirect from 80.
3. Add the app servers (`10.0.0.11:8080`, `10.0.0.12:8080`…). The health check path defaults to `/healthz`; change it to your app's.
4. **Review change** → check the diff and validation → **Apply**.

## Route microservices by path

1. **Frontends → New frontend → Microservice gateway**: rows of *path → backend → servers → health check*, for example `/api/users → users`, `/api/orders → orders`, and a default backend for the rest.
2. To add one more service later: on the frontend, **+ Add route**: the path, then a **new backend** with its servers or an existing one. k10y puts it before shorter paths that would also match.
3. **Path and under** (`/orders`) matches `/orders` and `/orders/123` but not `/ordersx`.

## Add a server to a backend

**Backends** → the backend's **⋯ → Add server…** → address, port and weight (the health check settings are copied from the backend's other servers) → review → **Apply**. The new server takes traffic once its health check passes.

## Deploy a new version without downtime

One server at a time, by hand:
1. **Backends** → open the backend → the server's **⋯ → Drain**: no new connections, existing ones finish. This is instant, without a reload.
2. Wait until its sessions reach 0, deploy your new version on it.
3. **⋯ → Put back in service**: it takes traffic again once its health check passes. Repeat for the next server.

Or let k10y walk you through it: **⋯ → Rolling deploy…** on the backend drains each server, waits for its sessions to reach 0, lets you deploy, puts it back, waits for its health check, then moves on. It never drains the last healthy server.

To keep drained servers drained across reloads, set up a [server-state file](./haproxy#keep-drain-and-maintenance-across-reloads).

## Send a small share of traffic to a new version (canary)

- **By weight:** add the new server, then **⋯ → Change weight…**. The dialog shows the share it will get (e.g. weight 10 next to two servers at 100 → about 5%).
- **By header:** on the frontend, **Edit frontend…** → add a route of type **header** (for example `X-Canary: yes`) to the new backend, so only your testers reach it.

## Show a maintenance page

On the frontend, **⋯ → Maintenance page…**: every request gets a "Back soon" page (HTTP 503) with your message, while your servers keep running. Add your office or VPN addresses to still let them through for testing. **⋯ → Turn off maintenance page** ends it.

## Block an attacker or let a rate-limited client back in

- **Block:** on **Logs**, **Block** next to a client, or on **Rate limits & blocks → Blocked IPs**, enter an address or network (`203.0.113.0/24`). It's refused on every frontend, instantly.
- **Unblock** on the same page lifts it.
- **Rate limit:** on the frontend, **Edit frontend… → Rate limit per client IP** (N requests per S seconds, then 429). A client that hit it shows on **Rate limits & blocks**; **Unblock** lets it in again right away.

## Add or renew a TLS certificate

1. **Certificates → Upload**: paste or choose the certificate chain and the private key (PEM).
2. To renew, upload the new one with the same name: **Replace**. HAProxy reloads gracefully.
3. Add an alert rule *HAProxy → Certificate expiring* to be warned 14 days before.

## Keep a backend in sync with Kubernetes

On the backend, **⋯ → Sync from Kubernetes**: choose the cluster and the Service, then **Node ports** (HAProxy outside the cluster) or **Pod IPs** (HAProxy can reach pods). k10y adds and removes servers as nodes or pods come and go, every 30 seconds. **Stop syncing** ends it. See [Kubernetes link](./haproxy#kubernetes-link).

## Keep two HAProxy the same (an HA pair)

1. **Administration → HAProxy instances** → give both the same **Group** (e.g. `edge-pair`).
2. Every change dialog then offers **Also apply to the same group**: you see the diff and validation for each, and Apply changes them one after another.
3. **Compare configs** on the Overview shows any difference; **Make … the same** copies this configuration to the other after a review.

To copy just one frontend or backend to another HAProxy: **⋯ → Copy to another HAProxy…**.

## Undo a change

**Config history** lists every version: from k10y (with who), from a Kubernetes sync, or changed outside k10y. Open one to see the diff, then **Restore this version** (through the usual review). For a broken edit, HAProxy already refused it: the review shows each problem with its line number.

## Find out why users get errors (502, 503)

1. **Overview**: the problems list and the status line show backends without healthy servers.
2. **Topology**: red or orange lines carry 5xx errors, down to the server that returns them.
3. **Logs**: filter by **5xx** to see which requests fail, which server answered and how long it took.
4. **Ask AI** on a problem (or **⋯ → Diagnose with AI**): it reads server states, logs and config and proposes a fix you review.

## Get alerted when a backend goes down

**Alerts → New rule → HAProxy**: *A backend has no server up* (or *A server is down*, *Server errors (5xx) above*, *Certificate expiring*, *HAProxy unreachable*), for all instances or one, and the channel to notify (Telegram, Slack, email or a webhook). See [Alerts](./alerts).

## Give a teammate access to one HAProxy

**Administration → Users** → add a role **on: one HAProxy**: *HAProxy viewer* (see everything), *HAProxy operator* (also drain, maintenance, rolling deploys, block IPs) or *HAProxy admin* (also change the config and the connection). They won't see your other HAProxy instances.
