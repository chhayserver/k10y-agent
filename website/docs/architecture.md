# Architecture

```
 Browser ──HTTPS──► k10y hub :8443   (dashboard, login and SSO, roles, audit, alerts, AI)
                      │   ├── PostgreSQL 17   (users, roles, clusters, instances, settings, audit, alerts, history)
                      │   └── backup          (daily dumps to ./data/backups)
                      ├──HTTPS──► agent in cluster "staging"     :30800 ──► Kubernetes API + metrics-server + kubelets
                      ├──HTTPS──► agent in cluster "production"  :30800 ──► Kubernetes API + metrics-server + kubelets
                      ├──HTTP(S)─► HAProxy Data Plane API         :5555  ──► HAProxy (config, runtime, stats)
                      └──HTTPS──► Nginx agent on "web1"           :9443  ──► nginx.conf, nginx -t, reload, access log
 Telegram, Slack, email, webhooks ◄── alerts ─┘      SIEM ◄── activity log export (webhook, syslog, files)
 k10y CLI ──HTTPS──► agents directly (one token per cluster), or the hub with an API token (port-forward)
```

## The hub

The hub is a Python (FastAPI) server that serves the dashboard (Vue 3 + TypeScript) and an API. It:

- signs people in and keeps their sessions;
- stores users, roles, cluster connections, settings, alert rules and history, and the audit log in **PostgreSQL**;
- **checks permissions** on every request, then forwards it to the right agent with that agent's token;
- runs the **alert engine**, which checks every rule against every cluster every 30 seconds;
- runs the **AI assistant**, using the agent's read-only API as its tools;
- relays **pod shells** and **live log streams** between the browser and the agent.

The hub runs in Docker with a read-only filesystem and no Linux capabilities. Its data lives in folders on the host: `./data/postgres`, `./data/hub` (TLS certificate) and `./data/backups`.

## The agent

The agent is a single pod (about 130 MiB of memory) in the `k10y` namespace of each cluster. It:

- reads the Kubernetes API and metrics-server, and keeps one hour of CPU and memory history in memory;
- carries out actions (scale, restart, edit, drain…) when control is enabled, refusing anything in protected namespaces;
- streams logs and opens shells;
- serves an **API only**, protected by a bearer token stored in the Secret `k10y-agent`, over HTTPS on NodePort 30800.

Agents never connect out to the hub. The hub connects to them, so an agent only needs to be reachable from the hub.

## HAProxy

The hub talks to each HAProxy's **Data Plane API** directly (no k10y agent on the load balancer). It samples the stats every 10 seconds for the live numbers and charts, and makes every configuration change in a Data Plane API transaction that HAProxy validates before it's committed. Server state changes (drain, maintenance) use the runtime API, so they apply instantly without a reload. See [HAProxy](./haproxy).

## The Nginx agent

Nginx has no management API, so a small k10y agent runs **next to each Nginx**: installed on the server as a systemd service, or built into the `k10y/k10y-nginx` image for Docker Compose. It's the same program as the cluster agent, started in Nginx mode. It:

- reads the configuration (every included file) and turns it into sites, routes and upstreams;
- follows the access log to count requests, errors and response times per site, route and upstream server, and checks every upstream server with a TCP connection every 10 seconds;
- changes configuration files by editing only the block concerned, checks the result with `nginx -t`, reloads, puts the old files back if anything fails, and keeps a history;
- serves an **API only** over HTTPS on port 9443, with its own token.

See [Nginx](./nginx).

## Request flow

1. You click **Scale** in the browser. The request goes to the hub with your session cookie.
2. The hub finds your roles and checks `workloads.scale` on that cluster.
3. The hub forwards the request to the agent with the agent's token, labelled with your username.
4. The agent checks protected namespaces, calls the Kubernetes API (with a dry run if you asked for one), and returns the result.
5. The hub writes an audit entry (who, what, where, result) to PostgreSQL and returns the result to you.

## Why this design

- **No kubeconfigs for people.** Access is managed in one place and can be revoked instantly.
- **One audit trail** across all clusters.
- **Small agents.** Each cluster only runs a reader with a scoped ServiceAccount; each Nginx only runs a small sidecar; HAProxy needs nothing extra.
- **Resilient.** If one cluster is down, the hub and every other cluster keep working.
