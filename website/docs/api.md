# Agent API

Each agent serves a REST API over HTTPS. Interactive documentation is at `https://<agent>:30800/docs`.

- **Authentication:** `Authorization: Bearer <token>` on every request.
- **Through the hub:** the same routes are available to signed-in users at `/api/hub/clusters/{id}/api/<route>`, checked against their roles.
- **Labelling:** send `X-K10Y-Actor: <name>` to label your actions in the agent's log.
- **Dry run:** every action accepts `"dry_run": true` (or `?dry_run=true` for deletes).

## Read

| Route | Returns |
|---|---|
| `GET /api/v1/info` | Cluster name, versions, control setting, protected namespaces |
| `GET /api/v1/summary` | Health, counts, usage and the problem list |
| `GET /api/v1/nodes` · `/pods` · `/deployments` · `/statefulsets` · `/daemonsets` · `/jobs` · `/cronjobs` · `/hpas` · `/services` · `/ingresses` · `/persistentvolumeclaims` · `/persistentvolumes` · `/configmaps` · `/secrets` | Lists (`?namespace=` to filter) |
| `GET /api/v1/resource?kind=&namespace=&name=` | One object: YAML, events, related objects |
| `GET /api/v1/events?namespace=&warnings_only=&limit=` | Events, newest first |
| `GET /api/v1/metrics/history?scope=cluster\|nodes\|pod` | CPU and memory history |
| `GET /api/v1/topology?namespace=` | The relationship graph behind the Map |
| `GET /api/v1/namespaces/details` | Namespaces with counts and usage |
| `GET /api/v1/audit?limit=` | The agent's own action log |

## Logs

| Route | Returns |
|---|---|
| `GET /api/v1/pods/{ns}/{pod}/logs?container=&tail=&previous=` | A log snapshot (plain text) |
| `GET /api/v1/pods/{ns}/{pod}/logs/stream?container=&tail=&since_seconds=` | Live logs (chunked plain text) |
| `GET /api/v1/{deployments\|statefulsets\|daemonsets\|jobs}/{ns}/{name}/logs/stream` | Live logs of every pod, each line prefixed `[pod]` |

## Actions

| Route | Body |
|---|---|
| `POST /api/v1/deployments/{ns}/{name}/scale` | `{"replicas": 3}` |
| `POST /api/v1/deployments/{ns}/{name}/restart` | `{}` |
| `POST /api/v1/deployments/{ns}/{name}/image` | `{"container": "app", "image": "nginx:1.28"}` |
| `POST /api/v1/deployments/{ns}/{name}/rollback` | `{"revision": 2}` (omit for the previous one) |
| `POST /api/v1/statefulsets/{ns}/{name}/scale` · `/restart` | as above |
| `POST /api/v1/daemonsets/{ns}/{name}/restart` | `{}` |
| `POST /api/v1/cronjobs/{ns}/{name}/trigger` · `/suspend` · `/resume` | `{}` |
| `DELETE /api/v1/{deployments\|jobs\|pods\|services\|configmaps\|secrets}/{ns}/{name}` | — |
| `POST /api/v1/nodes/{name}/cordon` · `/uncordon` · `/drain` | `{}` |
| `POST /api/v1/apply` | `{"yaml": "..."}` |
| `PUT /api/v1/resource` | `{"kind", "namespace", "name", "yaml"}`: replace one object, guarded by its `resourceVersion` |

## Shell (WebSocket)

`WS /api/v1/pods/{ns}/{pod}/exec?container=`

Send JSON text frames, `{"type": "input", "data": "ls\r"}` and `{"type": "resize", "cols": 120, "rows": 30}`. Terminal output comes back as text frames.

## Example

```bash
TOKEN=$(kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d)
curl -sk -H "Authorization: Bearer $TOKEN" https://<node-ip>:30800/api/v1/summary
curl -sk -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"replicas": 3, "dry_run": true}' https://<node-ip>:30800/api/v1/deployments/demo/web/scale
```
