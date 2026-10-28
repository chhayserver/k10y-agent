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

The API token and cluster name are kept.

## Version notes

### 2.0 → 2.1
- The new `install.yaml` adds `pods/exec` to the control role, which the pod shell needs.
- The new `docker-compose.yml` adds the `backup` service.
- New permissions `pods.exec` and `resources.edit`. Admin has both. Add them to other roles if you want.
- Agents are API-only: their old built-in web page is gone. Use the hub.

### 1.x → 2.0
- 2.0 introduced the hub. Agents now serve **HTTPS**, so agent URLs start with `https://`.
- The standalone browser-storage dashboard was replaced by the hub. Add your clusters in the hub.

See the [changelog](/changelog) for everything in each release.
