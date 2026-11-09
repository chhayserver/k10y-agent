# Upgrading

Upgrade the hub and the agents to the same version.

## Hub

```bash
cd k10y
curl -LO https://github.com/chhayserver/k10y-agent/releases/latest/download/docker-compose.yml
docker compose pull
docker compose up -d
```

The database schema is upgraded automatically on start. Take a [backup](./backups#back-up-now) first.

## Agents

```bash
kubectl apply -f https://github.com/chhayserver/k10y-agent/releases/latest/download/install.yaml
```

The API token and cluster name are kept. With Helm: `helm upgrade k10y-agent https://github.com/chhayserver/k10y-agent/releases/latest/download/k10y-agent.tgz -n k10y`.

If an agent is older than the hub, **All clusters**, **Administration → Clusters** and the sidebar say so and list what it's missing.

## Nginx agents

- **On a server:** run `install-nginx-agent.sh` again (it upgrades in place and keeps the token and settings).
- **Docker Compose:** `docker compose pull nginx && docker compose up -d nginx` with `k10y/k10y-nginx:latest` (or a version tag).

## Version notes

### 2.9 → 2.10
- Nginx changes from k10y need the Nginx agent 2.10 or newer: upgrade it as above.

### 2.8 → 2.9
- Nginx is new. Nothing to change for existing clusters and HAProxy instances.

### 2.7 → 2.8
- The new `install.yaml` adds `pods/portforward` to the control role (for `k10y port-forward`). Re-apply it.
- New permissions `pods.portforward`; Admin and Kubernetes admin have it.

### 2.6 → 2.7
- The new `install.yaml` adds `nodes/stats` (volume usage) and `services/proxy` (open a Service in the browser). Re-apply it.
- The first apply resets protected namespaces to `kube-system,k10y` once; after that, the list chosen in the hub is kept.

### 2.0 → 2.1
- The new `install.yaml` adds `pods/exec` to the control role, which the pod shell needs.
- The new `docker-compose.yml` adds the `backup` service.
- New permissions `pods.exec` and `resources.edit`. Admin has both. Add them to other roles if you want.
- Agents are API-only: their old built-in web page is gone. Use the hub.

### 1.x → 2.0
- 2.0 introduced the hub. Agents now serve **HTTPS**, so agent URLs start with `https://`.
- The standalone browser-storage dashboard was replaced by the hub. Add your clusters in the hub.

See the [changelog](/changelog) for everything in each release.
