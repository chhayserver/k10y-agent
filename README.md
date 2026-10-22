# k10y-agent

Monitor and control Kubernetes clusters from one dashboard.

Each cluster runs a small **k10y-agent**. It reads the Kubernetes API and metrics-server and
exposes a REST API. A **web dashboard** and a **CLI** talk to any number of agents, so one
screen covers all your clusters. Control actions (scale, restart, roll back, delete, drain,
apply YAML) go through guardrails: protected namespaces, dry run, confirmation and an audit log.

```
 Docker on your machine: k10y-dashboard :8080   (UI only; no cluster access, no credentials)
        │ your browser
        ├──► cluster "lab"   k10y-agent  http://<node-ip>:30800 ──► Kubernetes API + metrics-server
        └──► cluster "edge"  k10y-agent  http://<node-ip>:30800 ──► Kubernetes API + metrics-server

 k10y CLI ──────────────────► the same agents
```

## Features

**Monitoring**
- Cluster health (`healthy`, `degraded` or `critical`) with a problem list. Problems include
  NotReady nodes, node pressure, CrashLoopBackOff, ImagePull and OOM errors, long-pending pods,
  pods with many restarts, and under-replicated deployments.
- **Live CPU and memory charts** for the cluster, each node and each pod. Pod charts show the pod's
  request and limit. Ranges are 5m, 15m and 1h.
- Nodes, deployments, pods, services, ConfigMaps, Secrets (key names only), events and pod logs,
  including the previous crashed container.
- **Map**: a per-namespace relationship diagram of Ingress → Service → Deployment, StatefulSet,
  DaemonSet or Job → (ReplicaSet) → Pod → ConfigMap, Secret or PVC. Click anything to highlight
  what it depends on and what depends on it. Missing references and services that select no pods
  are flagged.

**Control**

| Resource | Actions |
|---|---|
| Deployments | Scale, restart, update image, roll back (pick a revision), delete |
| Pods | Delete (a controller recreates it) |
| Services, ConfigMaps, Secrets | Delete (warns if still in use) |
| Nodes | Cordon, uncordon, drain |
| Any manifest | Apply YAML (server-side apply, multiple documents) |

**Multi-cluster**
- Cluster switcher with a live health dot per cluster, and an **All clusters** overview.
- A standalone dashboard container that keeps working when any one cluster is down.
- Multi-cluster CLI: `k10y status --all`.

## Install (users: one command per cluster)

**Requirements:** `kubectl` with admin access to the cluster, and metrics-server for the CPU and
memory charts (included in RKE2, k3s, EKS, GKE and AKS). Nodes must be able to pull
`docker.io/k10y/k10y-agent` (amd64 or arm64), and your browser must be able to reach port **30800**
on a node.

**1. Install the agent.** Choose one:

```bash
# Monitoring + control (scale, restart, roll back, delete, drain, apply YAML)
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml

# Read-only monitoring
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install-readonly.yaml
```

**2. Wait for it, name the cluster, and get the API token.** The agent creates its own random
token on first start.

```bash
kubectl -n k10y rollout status deploy/k10y-agent
kubectl -n k10y set env deploy/k10y-agent K10Y_CLUSTER_NAME=prod     # optional display name
kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d; echo
```

**3. Open it.** Either:
- open the agent's own dashboard at `http://<any-node-ip>:30800` and paste the token, or
- use one dashboard for all your clusters:
  ```bash
  docker run -d --name k10y-dashboard --restart unless-stopped -p 8080:8080 \
    -e K10Y_MODE=dashboard k10y/k10y-agent:latest
  ```
  Then open `http://<server>:8080`, choose **Add cluster**, and enter `http://<node-ip>:30800` and the token.

**Other tasks:**

```bash
# Upgrade: apply the newer release's install.yaml (the token is kept)
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml

# Rotate the token
kubectl -n k10y delete secret k10y-agent && kubectl -n k10y rollout restart deploy/k10y-agent

# Uninstall
kubectl delete -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
```

> The agent serves plain HTTP on a NodePort. Keep port 30800 limited to your network, or put the
> agent behind an Ingress or LoadBalancer with TLS before exposing it beyond a private network.

The installers are generated from `deploy/k8s/` by `make manifests` (`scripts/gen_install.py`).

## Quick start (build from source)

Requirements: Docker, `kubectl`, `make`, and a Kubernetes cluster with metrics-server. RKE2, k3s,
EKS, GKE and AKS include metrics-server.

### 1. Build the image

```bash
make build        # docker build -t k10y/k10y-agent:1.1.0 .
make test         # unit tests, run inside the image
```

### 2. Get the image into the cluster

Choose the option that matches your cluster:

```bash
make load                         # RKE2 inside a Lima VM (VM=rke2): imports into the node's containerd
make load-k3d K3D_CLUSTER=edge    # k3d / k3s-in-Docker
```

Any other cluster pulls `docker.io/k10y/k10y-agent:1.1.0` from Docker Hub, so this step can be skipped.
To publish your own build (multi-arch amd64 + arm64):

```bash
docker login
make push                                             # → docker.io/k10y/k10y-agent:1.1.0 and :latest
make push REGISTRY_IMAGE=ghcr.io/<you>/k10y-agent     # or another registry, then set image: in deploy/k8s/agent.yaml
```

### 3. Deploy the agent

```bash
make deploy KUBECONFIG=~/.kube/<cluster>.yaml CLUSTER_NAME=<name>
make token  KUBECONFIG=~/.kube/<cluster>.yaml      # API token for the dashboard and CLI
```

`make deploy` creates the following in namespace `k10y`:
- a ServiceAccount with read RBAC, plus write RBAC when `CONTROL=true`
- a ConfigMap
- a Secret holding a random API token, created once
- the agent Deployment (one pod, about 130 MiB)
- a NodePort Service on **30800**

To deploy read-only, use `make deploy CONTROL=false ...`.

`make up` runs build, load, deploy and restart in one step for the default Lima cluster.

### 4. Open the dashboard

**Standalone (recommended):**

```bash
make dashboard                    # docker run ... -e K10Y_MODE=dashboard -p 8080:8080
open http://localhost:8080        # "Add your first cluster" → agent URL + token
make dashboard-stop
```

**Built in:** every agent also serves the dashboard at `http://<node-ip>:30800`, with that cluster
preselected.

Add more clusters from the sidebar's cluster switcher (**Add cluster…**). The connection is tested
before saving. The cluster list and tokens are kept only in your browser's local storage. Each
address you open the dashboard from keeps its own list.

## CLI

```bash
python3 -m venv .venv && .venv/bin/pip install -e .     # Python ≥ 3.9; CLI deps only
source .venv/bin/activate
k10y cluster add lab http://<node-ip>:30800 --token <token>

k10y status --all                 # every registered cluster
k10y nodes | pods --problems | deployments | services | events --warnings
k10y logs <ns> <pod> --tail 100 [--previous]

# control (add --dry-run to validate only; delete and drain ask for confirmation)
k10y scale demo web 3
k10y restart demo web
k10y set-image demo web nginx:1.28
k10y history demo web
k10y rollback demo web [--to-revision N]
k10y delete deployment|pod|service demo <name>
k10y cordon <node> / uncordon <node> / drain <node> --dry-run
k10y apply -f app.yaml
k10y audit
```

Pass `-C <cluster>` to target a cluster other than the current one. The CLI stores its config in
`~/.k10y/config.yaml` with mode 600.

## Security model

- **Auth**: every `/api/v1/*` route requires `Authorization: Bearer <token>`. Each cluster has its
  own token in the `k10y-agent` Secret.
- **Least-privilege RBAC**:
  - `deploy/k8s/rbac.yaml` is read access.
  - `deploy/k8s/rbac-control.yaml` is the opt-in write access. It has no RBAC or CRD permissions, so
    Apply YAML cannot be used to escalate privileges.
- **Protected namespaces** (`kube-system` and `k10y` by default, set with `PROTECTED=`): every
  control action targeting them is rejected, including objects inside an applied manifest. Drain
  skips their pods.
- **Dry run** is available for every action. Deleting a deployment, service, ConfigMap or Secret,
  or draining a node, requires typing its name.
- **Audit log**: every action is recorded with time, source, target and result. This includes dry
  runs and rejected attempts. View it on the Activity page, with `k10y audit`, or at
  `GET /api/v1/audit`. It is stored at `/data/audit.jsonl` on an emptyDir, so it survives container
  restarts but not pod replacement.
- **Secrets**: the API returns names, type and key names only, never values. Listing Secrets
  requires the `list secrets` permission, and Kubernetes cannot grant "metadata only". To hide
  Secrets entirely, remove that rule from `rbac.yaml`.
- **Container hardening**: the agent runs as non-root with a read-only root filesystem, all Linux
  capabilities dropped and the RuntimeDefault seccomp profile.
- **CORS**: agents allow cross-origin requests (`K10Y_CORS_ORIGINS`, default `*`) so that a
  dashboard served from another address can call them. This is safe because auth is a bearer
  token, never a cookie.
- **Standalone dashboard**: holds no kubeconfig, no RBAC and no tokens. Its `/api` routes return 404.

## Configuration

Agent environment variables (set via `make deploy` or `deploy/k8s/agent.yaml`):

| Variable | Default | Purpose |
|---|---|---|
| `K10Y_MODE` | `agent` | `agent`, or `dashboard` (UI only, no cluster access) |
| `K10Y_CLUSTER_NAME` | `default` | Name shown in the dashboard and CLI |
| `K10Y_API_TOKEN` | *(empty)* | Bearer token. Empty means no auth, unless `K10Y_TOKEN_SECRET` is set |
| `K10Y_TOKEN_SECRET` | *(empty)* | Read the token from this Secret in the agent's namespace, creating it if missing. Fails closed (503) if that's impossible |
| `K10Y_CONTROL_ENABLED` | `false` | Allow control actions (also needs `rbac-control.yaml`) |
| `K10Y_PROTECTED_NAMESPACES` | `kube-system` | Comma-separated; `make deploy` sets `kube-system,k10y` |
| `K10Y_CORS_ORIGINS` | `*` | Allowed browser origins; empty disables CORS |
| `K10Y_METRICS_POLL_S` | `5` | How often to check metrics-server |
| `K10Y_METRICS_RETENTION_S` | `3600` | In-memory CPU/memory history kept for charts |
| `K10Y_AUDIT_PATH` | `/data/audit.jsonl` | Audit log file |
| `K10Y_KUBECONFIG` | — | Only when running outside a cluster |

Run `make manifests` after changing `deploy/k8s/` to regenerate the installers.

Makefile variables: `CLUSTER_NAME`, `KUBECONFIG`, `CONTROL`, `PROTECTED`, `VM`, `K3D_CLUSTER`,
`IMAGE` (default `k10y/k10y-agent`), `TAG` (default `1.1.0`), `REGISTRY_IMAGE`, `PLATFORMS` and
`DASHBOARD_PORT`.

### Live charts

The agent checks metrics-server every 5 seconds. It records a point only when metrics-server has a
new measurement, which on RKE2 and k3s is about every 15 seconds, and uses that measurement's
timestamp. It keeps one hour in memory for the cluster, every node and every pod, and the dashboard
polls for new points every 5 seconds. History starts empty after an agent restart; long-term
history is a job for Prometheus.

## API

Interactive docs are at `http://<agent>/docs`. Control requests may send `X-K10Y-Actor` to label
their audit entries; the dashboard sends `dashboard` and the CLI sends `cli:<user>`.

| Route | Description |
|---|---|
| `GET /api/v1/info` | Cluster name, versions, control setting, protected namespaces |
| `GET /api/v1/summary` | Health, counts, usage, problems |
| `GET /api/v1/nodes`, `/pods`, `/deployments`, `/services`, `/configmaps`, `/secrets` | Resources (`?namespace=`) |
| `GET /api/v1/configmaps/{ns}/{name}` | ConfigMap data |
| `GET /api/v1/events?namespace=&warnings_only=&limit=` | Events, newest first |
| `GET /api/v1/pods/{ns}/{pod}/logs?container=&tail=&previous=` | Logs (plain text) |
| `GET /api/v1/metrics/history?scope=cluster\|nodes\|pod&namespace=&name=&since=` | CPU/memory history (epoch ms) |
| `GET /api/v1/topology?namespace=` | Relationship graph for the Map |
| `GET /api/v1/namespaces` | Namespace names |
| `GET /api/v1/deployments/{ns}/{name}/history` | Revisions |
| `GET /api/v1/audit?limit=` | Audit log, newest first |
| `POST /api/v1/deployments/{ns}/{name}/scale` | `{"replicas": 3, "dry_run": false}` |
| `POST /api/v1/deployments/{ns}/{name}/restart` | `{"dry_run": false}` |
| `POST /api/v1/deployments/{ns}/{name}/image` | `{"container": "app", "image": "nginx:1.28"}` |
| `POST /api/v1/deployments/{ns}/{name}/rollback` | `{"revision": 2}` (omit for previous) |
| `DELETE /api/v1/{deployments,pods,services,configmaps,secrets}/{ns}/{name}?dry_run=` | Delete |
| `POST /api/v1/nodes/{name}/{cordon,uncordon,drain}` | `{"dry_run": false}` |
| `POST /api/v1/apply` | `{"yaml": "...", "dry_run": true}` |

## Project layout

```
src/k10y_agent/
  api.py              FastAPI app: routes, auth, CORS, audit, dashboard mode
  collectors.py       read-only views of nodes, pods, workloads, services, config, events
  controls.py         control actions + protected-namespace guard + audit log
  topology.py         relationship graph for the Map page
  metrics_history.py  background CPU/memory sampler for the live charts
  cli.py              k10y CLI (multi-cluster)
  config.py           K10Y_* settings
  static/index.html   the dashboard (single file, no build step)
deploy/k8s/           namespace, RBAC (read + opt-in control), Deployment, NodePort Service
tests/                unit tests (`make test`)
Dockerfile, Makefile
```

## Lab setup used during development

- **lab**: RKE2 v1.36 in a Lima Ubuntu 24.04 VM on macOS (Apple Silicon). The VM uses Lima's
  `vzNAT` network so NodePorts are reachable from the Mac, and RKE2's `node-ip` is pinned in
  `/etc/rancher/rke2/config.yaml`.
- **edge**: k3s via `k3d cluster create edge -p "30801:30800@server:0"`. Its agent is at
  `http://localhost:30801`.

## Roadmap

- [x] Read-only monitoring, problems, logs, events
- [x] Control actions with guardrails and audit log
- [x] Map, ConfigMaps and Secrets views
- [x] Multi-cluster dashboard and standalone dashboard container
- [x] Live CPU/memory charts (cluster, node, pod)
- [ ] Alerts: a rule engine with Slack, Telegram and email notifications
- [x] Multi-arch image on Docker Hub (`k10y/k10y-agent`, `make push`)
- [x] One-command installer (`install.yaml`, `install-readonly.yaml`) with self-provisioned token
- [ ] Long-term metrics (Prometheus integration) and persistent audit log (PVC)
- [ ] Per-user authentication instead of one shared token per cluster
- [ ] Chat/AI operator built on the same API
