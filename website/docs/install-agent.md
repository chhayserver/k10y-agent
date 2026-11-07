# Install the agent

Install one agent in every cluster you want to see in k10y.

## Choose a mode

| Manifest | Can do |
|---|---|
| `install.yaml` | Monitoring **and** control: scale, restart, roll back, delete, drain, edit YAML, apply YAML, pod shell |
| `install-readonly.yaml` | Monitoring only. No write permissions are granted in the cluster at all. |

```bash
# monitoring + control
kubectl apply -f https://github.com/chhayserver/k10y-agent/releases/latest/download/install.yaml
# or monitoring only
kubectl apply -f https://github.com/chhayserver/k10y-agent/releases/latest/download/install-readonly.yaml
```

Or with **Helm**:

```bash
helm install k10y-agent https://github.com/chhayserver/k10y-agent/releases/latest/download/k10y-agent.tgz -n k10y --create-namespace \
  --set clusterName=prod-eu            # control: false for read-only; helm: true for Helm rollback/upgrade
```

The chart is generated from the same files as `install.yaml`, so it grants exactly the same permissions. Its values: `image`, `clusterName`, `control`, `protectedNamespaces`, `helm`, `service` (NodePort 30800 by default), `resources`, `extraEnv`, `nodeSelector`, `tolerations`, `affinity`. Use either the chart or `install.yaml` on a cluster, not both.

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

Then [connect the cluster](./clusters) in the hub. As the **agent address**, enter a node's IP and the NodePort, e.g. `192.168.64.2:30800`.

::: tip HTTPS, also through a NodePort
The agent serves **HTTPS** itself on its port (with a self-signed certificate), so the token travels encrypted. A NodePort only opens that same port on every node; it stays HTTPS. You don't have to type the scheme: k10y adds `https://`, and if you type `http://` for an agent that speaks TLS, it switches to `https://` for you. Leave **Verify the agent's TLS certificate** off for the self-signed certificate.
:::

## What the agent can access

- **Read:** nodes, namespaces, pods and logs, events, services, ConfigMaps, PVCs, PVs, Ingresses, Deployments, StatefulSets, DaemonSets, ReplicaSets, Jobs, CronJobs, HPAs, ResourceQuotas, LimitRanges, cert-manager Certificates, metrics, and volume usage from the kubelets (`nodes/stats`). Secrets are *listed* only for names and keys; their values are never returned.
- **Write** (`install.yaml` only): the actions listed above, plus server-side apply of common kinds and opening a Service's web page (`services/proxy`). There are **no RBAC, CRD or cluster-admin permissions**.
- **Optional:** `rbac-helm.yaml` gives the agent the built-in **edit** role so it can roll back Helm releases (see [Kubernetes insights](./kubernetes#helm-releases)).
- **Protected namespaces:** `kube-system` and `k10y` by default. Actions, edits and shells there are refused by the agent. Change them in **System settings → Protected namespaces** (see [Actions](./actions#protected-namespaces)); your choice survives upgrades.

## Requirements

- metrics-server, for CPU and memory charts (included in RKE2, k3s, EKS, GKE and AKS).
- The nodes can pull `docker.io/k10y/k10y-agent`.
- The hub can reach `https://<node-ip>:30800`. Keep that port limited to your network.

## Uninstall

```bash
kubectl delete -f https://github.com/chhayserver/k10y-agent/releases/latest/download/install.yaml
```
