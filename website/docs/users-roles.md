# Users, roles and permissions

Admins with the `users.manage` permission manage people under **Administration → Users** and **Administration → Roles**.

## How access works

- A **role** is a named set of permissions. You can create as many as you like.
- A user gets one or more **role assignments**. Each assignment is for **everything**, for **one cluster**, or for **one HAProxy instance**.
- On one cluster, only the role's cluster permissions count there. On one HAProxy, only its HAProxy permissions count there.
- A user only sees clusters where they have at least `cluster.view`, and HAProxy instances where they have at least `haproxy.view`.
- **Hub** permissions (managing users, clusters, settings and alerts) only count when the role is assigned on **everything**.

So "Operator on staging, Viewer on production" is two assignments for one user, and so is "HAProxy operator on lb-edge-1 only".

<Shot src="/shots/roles.png" alt="Editing a role's permissions" url="k10y / roles" />

## Permissions

### Per cluster

| Permission | Allows |
|---|---|
| `cluster.view` | See the cluster: overview, nodes, workloads, pods, services, ConfigMaps, events, map, charts |
| `secrets.view` | See Secrets (names, types and key names; values are never shown) |
| `logs.view` | Read pod logs and live logs |
| `workloads.scale` | Scale Deployments and StatefulSets |
| `workloads.restart` | Restart workloads; suspend, resume and run CronJobs |
| `workloads.update_image` | Change a Deployment's container image |
| `workloads.rollback` | Roll back Deployments |
| `workloads.delete` | Delete Deployments and Jobs |
| `pods.exec` | Open a shell in a pod's container |
| `pods.delete` | Delete pods |
| `services.delete` | Delete Services |
| `config.delete` | Delete ConfigMaps and Secrets |
| `nodes.manage` | Cordon, uncordon and drain nodes |
| `resources.edit` | Edit an object's YAML in its details panel |
| `apply.yaml` | Create or update objects with Apply YAML |
| `ai.use` | Run AI investigations |
| `audit.view` | See the activity log |

### HAProxy (assign on one HAProxy instance, or on everything)

Permissions cover a **whole HAProxy instance**: all its frontends, backends and servers.

| Permission | Allows |
|---|---|
| `haproxy.view` | See the instance: frontends, backends, servers, live numbers, charts, topology, logs, certificates, stick tables, the IP blocklist and config history (not the raw config) |
| `haproxy.operate` | Run it without changing its config: drain, maintenance and re-enable servers, rolling deploys, block and unblock IPs, let rate-limited clients in again (instant, no reload) |
| `haproxy.configure` | Change its config: frontends, routes, backends, servers, maintenance pages, certificates, Kubernetes sync, copy and group sync, restore old versions; read and edit the raw config (it can contain passwords) |
| `haproxy.manage` | Edit or remove the instance and its Data Plane API login. **Adding** a new instance needs this on everything |

Notes:
- The **first** IP block on an instance adds the blocklist to every frontend, which is a config change, so it needs `haproxy.configure` once. After that, `haproxy.operate` is enough.
- Changing several instances of a group at once needs `haproxy.configure` on each of them. "Compare configs" only shows the members you can configure.

### Hub (assign on everything)

| Permission | Allows |
|---|---|
| `clusters.manage` | Add, edit and remove clusters (their agent URLs and tokens) |
| `users.manage` | Manage users, roles and role assignments |
| `settings.manage` | Manage system settings (AI providers, alert channels) |
| `alerts.manage` | Create and edit alert rules |

## Built-in roles

| Role | Permissions |
|---|---|
| **Admin** | Everything. It can't be edited or deleted. |
| **Operator** | View, logs, Secrets, scale, restart, update image, roll back, delete pods, edit YAML (new installs), apply YAML, AI, audit. **Not** the shell. |
| **Viewer** | View, logs and audit. |

Operator and Viewer are starting points: edit them or create your own.

## Add a user

**Users → New user**: username, display name, a starting password, and one or more role assignments. Keep **Must change password at next login** ticked so they choose their own.

## Day-to-day

- **Reset a password:** open the user, type a new password and save. They're signed out everywhere and must change it at the next sign-in.
- **Disable an account:** **Disable** on the Users page. They're signed out immediately and can't sign in until enabled again.
- **Sign a user out:** **Sign out** signs them out of every device. See [Sessions](./sessions).
- **Delete a user:** their past actions stay in the audit log under their username.

There must always be at least one active user who can manage users on all clusters. k10y won't let you remove the last one.

## Example roles

| Role | Permissions | Assign |
|---|---|---|
| Developer | `cluster.view`, `logs.view` | production only |
| Developer (staging) | Operator permissions + `pods.exec` | staging only |
| On-call | Operator permissions + `nodes.manage` | all clusters |
| Auditor | `cluster.view`, `audit.view` | all clusters |
| HAProxy operator | `haproxy.view`, `haproxy.operate` | one HAProxy, e.g. `lb-edge-1` |
| Load balancer admin | all four `haproxy.*` permissions | one HAProxy, or everything to also add new instances |
