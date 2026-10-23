# HAProxy

k10y can monitor and manage **HAProxy** load balancers next to your Kubernetes clusters:

- **See** every service (frontend), backend and server, with live health, sessions, request rates and errors.
- **Control** servers instantly: drain one before a deploy, put it in maintenance, bring it back. No reload needed.
- **Configure** services, backends and servers from forms, or edit the raw `haproxy.cfg`. Every change shows a diff, is validated by HAProxy, and only then applies.
- **Topology:** a live diagram from service → backend → servers.

<Shot src="/shots/haproxy-overview.png" alt="HAProxy overview with problems and a topology diagram" url="k10y / haproxy" />

## Requirements

k10y talks to HAProxy through the official **HAProxy Data Plane API v3**. It's included in the `haproxytech/haproxy-*` images and in HAProxy Enterprise. The hub needs network access to the API's port (5555 by default). Protect that port: allow only the hub, and use HTTPS if it crosses an untrusted network.

## Connect an HAProxy

You need the `clusters.manage` permission. Open **HAProxy → Add HAProxy** and enter:

- **Name:** how it appears in k10y, e.g. `edge-lb-1`.
- **Data Plane API URL:** e.g. `http://10.0.0.20:5555`.
- **Username and password** of a Data Plane API user.
- **Verify TLS:** for `https://` URLs with a certificate the hub trusts.

Click **Test connection**. You'll see the API version and how many services, backends and servers it found. The password is stored encrypted in the hub's database.

## Pages

| Tab | What you see |
|---|---|
| **Overview** | Services, servers up/total, sessions, requests per second, **problems** (backends with no healthy server, servers failing health checks) and the topology |
| **Services** | Each frontend: status, listen addresses, routes (path or host → backend), sessions, request rate, 5xx |
| **Backends** | Each backend with its balancing, health check, which services use it, and its servers: state, address, weight, health-check result and time, sessions, requests, 5xx, time since last change |
| **Topology** | Services → backends → servers. Line labels show the route; colours show server health. Hover to trace a path, click to open it. |
| **Config** | The full `haproxy.cfg`, editable |

The page refreshes every 5 seconds. Use **Pause** to stop it while you read.

## Drain, maintenance and back in service

From a server's **⋯** menu (permission `haproxy.operate`):

| Action | Effect |
|---|---|
| **Drain** | No new connections; existing sessions finish. Use it before deploying to that server. |
| **Maintenance** | Out of rotation now, and health checks stop. |
| **Put back in service** | Takes traffic again once its health check passes. |

These apply **instantly through HAProxy's runtime API, without a reload**, and each one is audited.

### Keep drain and maintenance across reloads

Runtime states live in the running HAProxy process. When the configuration changes, HAProxy reloads, and **without a server-state file a drained server goes back into rotation**. k10y shows a warning on the Backends tab when an instance isn't set up for this.

To keep states across reloads:

```
global
    server-state-file /var/run/haproxy/server-state

defaults
    load-server-state-from-file global
```

Then save the state just before each reload. With the Data Plane API, do it in the reload command, for example:

```sh
#!/bin/sh
# reload_cmd for the Data Plane API: save server states, then reload through the master CLI
echo "@1 show servers state" | nc -w 3 127.0.0.1 9999 > /var/run/haproxy/server-state
echo reload | nc -w 5 127.0.0.1 9999 | grep -q '^Success=1'
```

(HAProxy started with `-S 127.0.0.1:9999` for the master CLI. Adjust the address and paths to your setup.)

## Add, edit and remove

You need the `haproxy.configure` permission.

- **Services:** **New service**, or **⋯ → Edit service / Delete service**. Set the protocol (HTTP or TCP), listen addresses (`*:80`, `10.0.0.5:443`…), a default backend, and **routes**: *path starts with*, *path is* or *host is* → backend. The first match wins.
- **Backends:** **New backend** (with its first servers), or **⋯ → Edit / Delete / Add server**. Set the balancing method and health check (HTTP path, TCP connect, or connect only). A backend still used by a service can't be deleted.
- **Servers:** **⋯ → Edit / Remove server**. Set address, port, weight (0–256), health check, and backup.

Every change goes through the same review:

1. k10y prepares the change in a Data Plane API **transaction**. Nothing is live yet.
2. You see **exactly which lines of `haproxy.cfg` change**, and HAProxy's own **validation** result.
3. **Apply** commits it. HAProxy reloads gracefully: open connections finish on the old process. k10y waits for the reload result. If the reload fails, the previous configuration stays active and you're told why.

<Shot src="/shots/haproxy-change.png" alt="Reviewing an HAProxy change: one route added, validated by HAProxy" url="k10y / haproxy / services" />

Forms only change what you changed. Routing rules that are more complex than path or host matches are kept as they are, and can be edited on the **Config** tab.

## Raw configuration

**Config → Edit** opens the whole `haproxy.cfg` for anything the forms don't cover, such as TLS certificates, ACLs, stick tables or global tuning. **Review change** shows the diff and the validation result before you apply. Reading the raw config needs `haproxy.configure` too, because it can contain passwords.

## Permissions

| Permission | Allows |
|---|---|
| `haproxy.view` | All HAProxy pages (except the raw config) |
| `haproxy.operate` | Drain, maintenance, back in service |
| `haproxy.configure` | Add, edit and remove services, backends and servers; read and edit the raw config |
| `clusters.manage` | Connect, edit and remove HAProxy instances |

These are global permissions: assign the role on **all clusters**. On new installs, Viewer includes `haproxy.view` and Operator includes `haproxy.view` and `haproxy.operate`.

Every runtime action, preview (as a dry run) and applied change is in the [audit log](./audit).

## Try it: the HAProxy test lab

The repository includes a Docker lab with HAProxy 3.2, the Data Plane API, five demo web servers and one deliberately broken backend:

```bash
make haproxy-lab-up      # prints the URL and the generated API password
```

| | |
|---|---|
| Traffic | `http://localhost:8088` (`/` → shop, `/api` → api) |
| Broken service | `http://localhost:8081` (backend `legacy`, always down) |
| Stats page | `http://localhost:8404/stats` |
| Data Plane API | `http://localhost:5555/v3`, user `admin` |

Connect it in k10y with the URL `http://host.docker.internal:5555` (from a hub running in Docker on the same machine). `make haproxy-lab-down` stops it.
