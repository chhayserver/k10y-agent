# Install the agent

Install one agent in every cluster you want to see in k10y.

## Choose a mode

| Manifest | Can do |
|---|---|
| `install.yaml` | Monitoring **and** control: scale, restart, roll back, delete, drain, edit YAML, apply YAML, pod shell |
| `install-readonly.yaml` | Monitoring only. No write permissions are granted in the cluster at all. |

```bash
# monitoring + control
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
# or monitoring only
kubectl apply -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install-readonly.yaml
```

Both create, in namespace `k10y`:
- a ServiceAccount with read access (and, for `install.yaml`, the opt-in control role);
- the agent Deployment (one pod, non-root, read-only filesystem);
- a NodePort Service on **30800** (HTTPS);
- on first start, a Secret `k10y-agent` with a random API token.

## Name the cluster

```bash
kubectl -n k10y create configmap k10y-settings --from-literal=cluster-name=production
kubectl -n k10y rollout restart deploy/k10y-agent
```

## Get the token

```bash
kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d; echo
```

Then [connect the cluster](./clusters) in the hub.

## What the agent can access

- **Read:** nodes, namespaces, pods and logs, events, services, ConfigMaps, PVCs, PVs, Ingresses, Deployments, StatefulSets, DaemonSets, ReplicaSets, Jobs, CronJobs, HPAs, metrics. Secrets are *listed* only for names and keys; their values are never returned.
- **Write** (`install.yaml` only): the actions listed above, plus server-side apply of common kinds. There are **no RBAC, CRD or cluster-admin permissions**.
- **Protected namespaces:** `kube-system` and `k10y` by default. Actions, edits and shells there are refused by the agent.

## Requirements

- metrics-server, for CPU and memory charts (included in RKE2, k3s, EKS, GKE and AKS).
- The nodes can pull `docker.io/k10y/k10y-agent`.
- The hub can reach `https://<node-ip>:30800`. Keep that port limited to your network.

## Uninstall

```bash
kubectl delete -f https://github.com/zorouzumaki1111-cmd/k10y-agent/releases/latest/download/install.yaml
```
