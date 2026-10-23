# Architecture

```
 Browser ──HTTPS──► k10y hub :8443   (dashboard, login, roles, audit, alerts, AI)
                      │   ├── PostgreSQL 17   (users, roles, clusters, settings, audit, alerts)
                      │   └── backup          (daily dumps to ./data/backups)
                      ├──HTTPS──► agent in cluster "staging"     :30800 ──► Kubernetes API + metrics-server
                      └──HTTPS──► agent in cluster "production"  :30800 ──► Kubernetes API + metrics-server
 Telegram ◄── alerts ─┘
 k10y CLI ──HTTPS──► agents directly (one token per cluster)
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

## Request flow

1. You click **Scale** in the browser. The request goes to the hub with your session cookie.
2. The hub finds your roles and checks `workloads.scale` on that cluster.
3. The hub forwards the request to the agent with the agent's token, labelled with your username.
4. The agent checks protected namespaces, calls the Kubernetes API (with a dry run if you asked for one), and returns the result.
5. The hub writes an audit entry (who, what, where, result) to PostgreSQL and returns the result to you.

## Why this design

- **No kubeconfigs for people.** Access is managed in one place and can be revoked instantly.
- **One audit trail** across all clusters.
- **Small agents.** Each cluster only runs a reader with a scoped ServiceAccount.
- **Resilient.** If one cluster is down, the hub and every other cluster keep working.
