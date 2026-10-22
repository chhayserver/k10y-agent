# k10y-agent

Monitor and control Kubernetes clusters from one dashboard, with user accounts, roles, alerts and an
AI assistant.

Each cluster runs a small **k10y agent**. It reads the Kubernetes API and metrics-server and serves an
HTTPS API. The **k10y hub** (Docker Compose with PostgreSQL) is where people log in. It stores
users, roles, cluster connections, settings, the audit log and alert history in PostgreSQL. It also
serves the dashboard, and forwards each request to the right agent after checking the user's
permissions.

```
 Browser ──HTTPS──► k10y hub :8443  (Vue dashboard, login, roles, audit, alerts, AI)
                      │   └── PostgreSQL 17 (users, roles, clusters, encrypted secrets, audit, alerts)
                      ├──HTTPS──► cluster "lab"   k10y agent :30800 ──► Kubernetes API + metrics-server
                      └──HTTPS──► cluster "edge"  k10y agent :30800 ──► Kubernetes API + metrics-server
 Telegram ◄── alerts ─┘
 k10y CLI ──HTTPS──► agents directly (token per cluster)
```

Agents use a self-signed certificate. Certificate verification is off by default and can be
turned on for each cluster. No Ingress is needed; agents use a NodePort and the hub publishes a port.

## Features

**Monitoring**
- Cluster health (`healthy`, `degraded` or `critical`) with a problem list. Problems include
  NotReady nodes, node pressure, CrashLoopBackOff, ImagePull and OOM errors, long-pending pods,
  pods with many restarts, and under-replicated workloads.
- **Live CPU and memory charts** for the cluster, each node and each pod. Pod charts show the pod's
  request and limit.
- **Pages:**
  - Cluster: Overview, Map, AI Assistant, Nodes, Namespaces
  - Workloads: Deployments, StatefulSets, DaemonSets, Jobs, **CronJobs**,
    **Autoscalers (HPA)**, Pods
  - Network & config: Services, **Ingresses**, **Storage (PVCs and PVs)**, ConfigMaps,
    Secrets (key names only)
  - Activity: Events, **Alerts**, Activity (audit)
- **Live logs** that follow new lines like `kubectl logs -f`, for one pod or **every pod of a
  Deployment, StatefulSet, DaemonSet or Job**. Each line is tagged and coloured by pod. You can
  filter, pause, copy and download, and switch to a snapshot or the previous crashed container.
- **Details panel for every resource:** an overview, related objects to drill into, the live YAML
  and events. Workloads also have a **Logs** tab.
- **Edit YAML in place** (like `kubectl edit`): edit, review a line-by-line diff, dry run, save.
  A concurrent change by someone else is detected instead of overwritten. Secrets can't be edited.
- **Pod shell:** open an interactive terminal (bash, or sh) in any running container from the pod
  panel or the row menu. Every session is recorded in Activity.
- **Map:** a per-namespace relationship diagram of Ingress → Service → workload → Pod →
  ConfigMap, Secret or PVC.
- **My settings:** each user can hide pages from the sidebar and choose theme, refresh interval and
  start page. These are saved to the account, so they follow you to any device. You can also see
  your signed-in devices and sign them out.

**Control** (each action needs its own permission; see [Roles](#users-roles-and-permissions))

| Resource | Actions |
|---|---|
| Deployments | Scale, restart, update image, roll back, delete |
| StatefulSets | Scale, restart |
| DaemonSets | Restart |
| CronJobs | **Run now**, suspend, resume |
| Jobs | Delete (with its pods) |
| Pods | Open a shell, delete (a controller recreates it) |
| Services, ConfigMaps, Secrets | Delete (warns if still in use) |
| Nodes | Cordon, uncordon, drain |
| Any object except Secrets | Edit YAML with diff review |
| Any manifest | Apply YAML (server-side apply, multiple documents) |

Every action has a dry run. `kube-system` and `k10y` are protected namespaces. Destructive
actions require typing the name. Every attempt is written to the audit log with the user who made
it.

**Alerts** (in the hub)
- Rules for **cluster problems** (filter by kind and namespace), **CPU or memory thresholds** (per
  node or for the whole cluster), and **cluster unreachable**. A rule covers all clusters or just one.
- A **hold time** keeps short blips from sending messages. You can also choose reminders while an
  alert is firing, a message when it resolves, and an optional **AI diagnosis** in the message.
- **Telegram** notifications. Create a bot with @BotFather, paste the token, and press *Find chats*
  to get the chat ID. Bot tokens are stored encrypted.
- Alert history is kept in PostgreSQL and shown on the Alerts page.

**AI assistant** (Google Gemini, or CodeCraft API / any OpenAI-compatible API)
- Investigates the whole cluster, a namespace, or one object, using read-only tools. It never reads
  Secret values. It returns a summary, root causes and **proposed fixes**. Nothing is applied until a
  person reviews it.
- Keys for both providers are stored encrypted in the hub (System settings), and you can switch
  between them with one click.

## Quick start

### 1. Start the hub (Docker Compose)

Requirements: Docker with Compose.

```bash
git clone https://github.com/zorouzumaki1111-cmd/k10y-agent.git && cd k10y-agent
make hub-up          # first run creates .env with a random DB password, secret key and admin password
```

Without `make`, download
[`docker-compose.yml`](https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/docker-compose.yml)
and create `.env` next to it:

```bash
cat > .env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
K10Y_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')
K10Y_ADMIN_USER=admin
K10Y_ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=\n')
EOF
chmod 600 .env && docker compose up -d && grep ADMIN .env
```

Open **https://\<server\>:8443**. The certificate is self-signed, so accept the browser warning
once. Log in with the admin from `.env`; you must set a new password at first login.

**Where the data lives:** folders on the host, next to `docker-compose.yml`:
`./data/postgres` (the PostgreSQL database: users, roles, clusters, settings, audit, alerts) and
`./data/hub` (the hub's HTTPS certificate). Set `K10Y_DATA_DIR=/some/path` in `.env` to use another
location.

**Backups** run automatically: the `backup` service writes a compressed dump to `./data/backups`
every 24 hours and keeps the newest 14. Change this with `K10Y_BACKUP_EVERY_HOURS` and
`K10Y_BACKUP_KEEP`.

```bash
make hub-backup                                          # back up now
make hub-restore FILE=data/backups/k10y-<date>.sql.gz    # restore (replaces the current data)
```

> **Back up `.env`.** `K10Y_SECRET_KEY` encrypts the agent tokens, AI keys and Telegram tokens stored
> in PostgreSQL. Without it, those secrets cannot be decrypted.

### 2. Install an agent on each cluster

Requirements: `kubectl` with admin access, and metrics-server for the charts (included in RKE2,
k3s, EKS, GKE and AKS). The hub must be able to reach port **30800** on a node.

```bash
# Monitoring + control
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
# or read-only monitoring
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install-readonly.yaml

kubectl -n k10y rollout status deploy/k10y-agent
kubectl -n k10y create configmap k10y-settings --from-literal=cluster-name=prod   # optional display name
kubectl -n k10y rollout restart deploy/k10y-agent
kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d; echo   # API token
```

### 3. Add the cluster in the hub

**Administration → Clusters → Add cluster**: enter `https://<node-ip>:30800` and the token, then
**Test connection**. Leave *Verify TLS* off for the agent's self-signed certificate.

**Other tasks:**

```bash
# Upgrade an agent: apply the newer install.yaml (the token is kept)
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
# Upgrade the hub: change the image tag in docker-compose.yml, then
docker compose pull && docker compose up -d
# Rotate an agent token (then update it in Administration → Clusters)
kubectl -n k10y delete secret k10y-agent && kubectl -n k10y rollout restart deploy/k10y-agent
# Uninstall an agent
kubectl delete -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
```

## Users, roles and permissions

Roles are fully custom: an admin chooses any combination of permissions. A user gets roles either
for **all clusters** or for **one cluster**, so "operator on staging, viewer on production" is two
assignments. Clusters a user has no role on are invisible to them.

| Permission | Allows |
|---|---|
| `cluster.view` | See the cluster: every page except Secrets, logs, AI and audit |
| `secrets.view` | Secrets page (names and key names only) |
| `logs.view` | Pod logs and live logs |
| `workloads.scale` / `.restart` / `.update_image` / `.rollback` / `.delete` | Workload actions (`restart` also covers CronJob run, suspend and resume) |
| `pods.exec` | Open a shell in a container (not in Operator or Viewer by default) |
| `pods.delete`, `services.delete`, `config.delete` | Delete those objects |
| `nodes.manage` | Cordon, uncordon, drain |
| `resources.edit` | Edit an object's YAML in its details panel |
| `apply.yaml` | Apply YAML |
| `ai.use` | AI Assistant |
| `audit.view` | Activity page |
| `clusters.manage`, `users.manage`, `settings.manage`, `alerts.manage` | Administration (only when assigned for all clusters) |

There are three built-in roles. **Admin** always has every permission and cannot be edited or
removed from the last admin. **Operator** and **Viewer** are starting points you can change.

## Security model

- **Login:**
  - Passwords are hashed with argon2id, and there is a password policy and a forced change at first
    login.
  - Sessions are stored server-side, with an `HttpOnly`, `Secure`, `SameSite=Strict` cookie and a
    sliding 12-hour expiry.
  - Changes require a CSRF header.
  - Login attempts are limited to 5 failures per 5 minutes per user and IP.
  - Users can see and sign out their devices. Admins can sign a user out everywhere or disable the
    account, which ends all of its sessions at once.
- **Agents are API-only.** They serve no web page and allow no cross-origin browser calls by
  default, so the hub is the only way in for people. The CLI uses agent tokens directly.
- **Permissions are enforced in the hub** on every request forwarded to an agent. The browser never
  sees agent tokens. Write requests that don't match a known action are refused, and the agent's
  own AI endpoints can't be reached through the hub.
- **Secrets at rest:** agent tokens, AI keys and Telegram bot tokens are encrypted (Fernet) with
  `K10Y_SECRET_KEY`. PostgreSQL is not published on the host.
- **Transport:** the hub and agents serve HTTPS. Both generate a self-signed EC certificate on first
  start, or you can mount your own with `K10Y_TLS_CERT` / `K10Y_TLS_KEY`. The hub skips agent
  certificate checks unless *Verify TLS* is on for that cluster.
- **Pod shells** need `pods.exec`, control enabled on the agent, and a namespace that isn't
  protected. The WebSocket only accepts pages from the hub itself, which blocks cross-site
  WebSocket hijacking. Opening and closing a shell, with its duration, is audited.
- **Audit:** every control attempt (including dry runs, refusals and unreachable agents), every login
  and every admin change is stored in PostgreSQL with user, cluster, target, parameters, result and
  client IP. Agents keep their own local log too.
- **Least-privilege RBAC** on each cluster:
  - Read access is always granted.
  - Write access is opt-in (`rbac-control.yaml`). It has no RBAC or CRD permissions, so Apply YAML
    cannot be used to escalate privileges.
  - Protected namespaces are enforced by the agent itself.
- **Containers** run as non-root with a read-only filesystem, all capabilities dropped and
  `no-new-privileges` set.

## Configuration

Hub (`docker-compose.yml` / `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `K10Y_MODE` | `agent` | `hub` for the hub |
| `K10Y_DATABASE_URL` | — | `postgresql://user:pw@host:5432/db` |
| `K10Y_SECRET_KEY` | — | Encrypts stored secrets. Keep it safe and stable |
| `K10Y_ADMIN_USER`, `K10Y_ADMIN_PASSWORD` | `admin`, random | First admin, created only when there are no users. A random password is printed in the log |
| `K10Y_SESSION_HOURS` | `12` | Idle session lifetime |
| `K10Y_PUBLIC_URL` | — | Hub address used for links in alert messages |
| `K10Y_ALERTS_ENABLED` | `true` | Run the alert engine (rules are evaluated every 30 s) |
| `K10Y_TLS`, `K10Y_TLS_CERT`, `K10Y_TLS_KEY`, `K10Y_TLS_DIR` | `false`, —, —, `/tmp/k10y-tls` | HTTPS; a self-signed certificate is generated when no cert or key is given |
| `K10Y_HUB_PORT` | `8443` | Published port (compose) |
| `K10Y_DATA_DIR` | `./data` | Host folder for the database, certificate and backups (compose) |
| `K10Y_BACKUP_EVERY_HOURS`, `K10Y_BACKUP_KEEP` | `24`, `14` | Automatic backup interval and how many to keep (compose) |

Agent (`deploy/k8s/agent.yaml`):

| Variable | Default | Purpose |
|---|---|---|
| `K10Y_TOKEN_SECRET` | — | Read the API token from this Secret, creating it if missing |
| `K10Y_CONTROL_ENABLED` | `false` | Allow control actions (also needs `rbac-control.yaml`) |
| `K10Y_PROTECTED_NAMESPACES` | `kube-system` | Comma-separated; the installer sets `kube-system,k10y` |
| `K10Y_TLS` | `true` in the manifests | Serve HTTPS on 8443, exposed as NodePort 30800 |
| `K10Y_METRICS_POLL_S`, `K10Y_METRICS_RETENTION_S` | `5`, `3600` | Live chart sampling and in-memory history |

## CLI

The CLI talks to agents directly with each cluster's token.

```bash
python3 -m venv .venv && .venv/bin/pip install -e . && source .venv/bin/activate
k10y cluster add lab https://<node-ip>:30800 --token <token> --insecure   # -k: self-signed cert
k10y status --all
k10y nodes | pods --problems | deployments | services | events --warnings
k10y logs <ns> <pod> --tail 100 [--previous]
k10y scale demo web 3 [--dry-run]     # also: restart, set-image, history, rollback, delete, cordon, drain, apply -f
k10y audit
```

## Agent API

The agent serves no web page. Interactive API docs are at `https://<agent>:30800/docs`. Every route needs `Authorization: Bearer
<token>`. Through the hub, the same routes are at `/api/hub/clusters/{id}/api/<route>`.

| Route | Description |
|---|---|
| `GET /api/v1/info`, `/summary` | Cluster info; health, counts, usage, problems |
| `GET /api/v1/nodes`, `/pods`, `/deployments`, `/statefulsets`, `/daemonsets`, `/jobs`, `/cronjobs`, `/hpas`, `/services`, `/ingresses`, `/persistentvolumeclaims`, `/persistentvolumes`, `/configmaps`, `/secrets` | Resources (`?namespace=`) |
| `GET /api/v1/pods/{ns}/{pod}/logs?container=&tail=&previous=` | Log snapshot |
| `GET /api/v1/pods/{ns}/{pod}/logs/stream?container=&tail=&since_seconds=` | **Live** logs (chunked text) |
| `GET /api/v1/{deployments,statefulsets,daemonsets,jobs}/{ns}/{name}/logs/stream` | Live logs of every pod, lines prefixed `[pod]` |
| `GET /api/v1/resource?kind=&namespace=&name=` | One object: YAML, events, related objects |
| `GET /api/v1/events`, `/metrics/history`, `/topology`, `/namespaces/details`, `/audit` | Events, chart data, map, namespaces, local audit |
| `POST /api/v1/deployments/{ns}/{name}/{scale,restart,image,rollback}` | Deployment actions (`dry_run` supported everywhere) |
| `POST /api/v1/statefulsets/{ns}/{name}/{scale,restart}`, `/daemonsets/{ns}/{name}/restart` | Workload actions |
| `POST /api/v1/cronjobs/{ns}/{name}/{trigger,suspend,resume}` | CronJob actions |
| `DELETE /api/v1/{deployments,jobs,pods,services,configmaps,secrets}/{ns}/{name}` | Delete |
| `POST /api/v1/nodes/{name}/{cordon,uncordon,drain}`, `POST /api/v1/apply` | Nodes, apply YAML |
| `PUT /api/v1/resource` | `{"kind", "namespace", "name", "yaml", "dry_run"}`: replace one object, checked by its resourceVersion |
| `WS /api/v1/pods/{ns}/{pod}/exec?container=` | Interactive shell. Client sends `{"type":"input","data"}` / `{"type":"resize","cols","rows"}` |

## Development

```bash
make build                  # Docker image (builds the Vue app, then the Python package)
make test                   # unit tests inside the image
make hub-up / hub-logs / hub-down
make load && make deploy    # RKE2 in the Lima VM (VM=rke2); make load-k3d for k3d
make push                   # multi-arch (amd64 + arm64) to docker.io/k10y/k10y-agent:$(TAG) and :latest
make manifests              # regenerate deploy/install/*.yaml from deploy/k8s/
```

Frontend work: `cd web && npm install && npm run dev`. The Vite dev server proxies `/api` to a hub
on `https://localhost:8443`.

```
src/k10y_agent/
  api.py, collectors.py, controls.py, resources.py, topology.py, logstream.py, metrics_history.py
                       agent: REST API, read views, actions, details panel, map, live logs, charts
  hub/                 hub: app.py (auth, users, roles, clusters, proxy, audit, AI), db.py (models),
                       security.py, permissions.py, alerts.py (rules, engine, Telegram)
  ai.py                AI providers, read-only tools, investigation loop
  tls.py, server.py    self-signed certificates; uvicorn entry point
  cli.py               k10y CLI
web/                   dashboard: Vue 3 + TypeScript + Vite + Pinia (built into the image)
deploy/k8s/            agent namespace, RBAC, Deployment, NodePort Service
deploy/install/        generated one-file installers
docker-compose.yml     hub + PostgreSQL
tests/                 unit tests
```

Lab used during development: **lab** is RKE2 v1.36 in a Lima Ubuntu VM on macOS (`vzNAT`, node IP
pinned). **edge** is k3d with NodePort 30800 mapped to `localhost:30801`.

## Roadmap

- [x] Monitoring, problems, logs, events; control actions with guardrails
- [x] Map, details panels, all common resource pages
- [x] Multi-cluster, multi-arch image, one-command installer
- [x] Live CPU/memory charts and live log streaming
- [x] AI assistant (Gemini / OpenAI-compatible) with human-approved fixes
- [x] Hub with PostgreSQL: login, custom roles, encrypted secrets, permanent audit log
- [x] Alerts with Telegram
- [x] Edit YAML, pod shell, per-user settings, session management, automatic backups
- [ ] More alert channels (Slack, email, webhooks)
- [ ] Two-factor login (TOTP)
- [ ] Long-term metrics (Prometheus integration)
- [ ] Single sign-on (OIDC)
