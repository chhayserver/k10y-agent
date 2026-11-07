# Actions and safety

Actions need **control enabled** on the agent (`install.yaml`) and the matching permission on that cluster. Open them from a row's **⋯** menu or the details panel. If you don't have permission for an action, it isn't shown.

## What you can do

| Resource | Actions | Permission |
|---|---|---|
| Deployment | Scale, restart, update image, roll back to a chosen revision, delete | `workloads.scale`, `.restart`, `.update_image`, `.rollback`, `.delete` |
| StatefulSet | Scale, restart | `workloads.scale`, `workloads.restart` |
| DaemonSet | Restart | `workloads.restart` |
| CronJob | Run now, suspend, resume | `workloads.restart` |
| Job | Delete (with its pods) | `workloads.delete` |
| Pod | Delete (its controller recreates it), [open a shell](./shell) | `pods.delete`, `pods.exec` |
| Service | Delete | `services.delete` |
| ConfigMap, Secret | Delete | `config.delete` |
| Node | Cordon, uncordon, drain | `nodes.manage` |
| Any object (not Secrets) | [Edit YAML](./edit-yaml) | `resources.edit` |
| Any manifest | Apply YAML (server-side apply, several documents at once) | `apply.yaml` |

## Built-in safety

- **Dry run:** every action can be tried first. Kubernetes validates it completely, and nothing changes.
- **Protected namespaces:** `kube-system` and `k10y` by default. The agent refuses every action, edit and shell there, including objects inside an applied manifest. Change the list per cluster in **Administration → System settings → Protected namespaces** (needs `namespaces.protect`; see below).
- **Typed confirmation:** deleting a Deployment, Service, ConfigMap or Secret, and draining a node, require typing the object's name.
- **In-use warnings:** deleting a ConfigMap, Secret or Service tells you what still uses it.
- **Conflict check:** YAML edits fail safely if someone else changed the object since you opened it.
- **Audit:** every attempt, including dry runs and refusals, is recorded with your username. See [Audit log](./audit).

## Roll back a Deployment

**⋯ → Roll back…** lists the Deployment's revisions with their images. Pick one, dry run, then roll back.

## Drain a node

**Nodes → ⋯ → Drain…** cordons the node, then evicts its pods using the eviction API, so PodDisruptionBudgets are respected. Pods in protected namespaces are left alone. Use **Uncordon** when maintenance is done.

## Apply YAML

**Apply YAML** (top bar) creates or updates objects from one or more YAML documents, like `kubectl apply --server-side`. The control role can't create RBAC objects or CRDs, so Apply YAML can't be used to gain more permissions.

## Protected namespaces

**Administration → System settings → Protected namespaces** lists each cluster you have `namespaces.protect` on (Admin and Kubernetes admin have it), with its protected namespaces. Add a namespace (it can be one that doesn't exist yet), remove one with ×, then **Save**. The change applies at once, for every user and also for the CLI and the AI assistant.

- Everything in a protected namespace can still be viewed; only changes are refused. When a node is drained, pods in protected namespaces are left in place.
- Removing `kube-system` or `k10y` asks you to confirm: without it, people with the right permissions could change core components or the k10y agent itself.
- The agent keeps the list in its own Secret (`k10y-agent`, key `protected-namespaces`), so it **survives restarts and upgrades**. The page shows **Install default** until it's changed here; after that, `K10Y_PROTECTED_NAMESPACES` is no longer used.
- Every change is in the [activity log](./audit) with who made it.
- Agents older than 2.6 show the list but can't change it here; update them first.
