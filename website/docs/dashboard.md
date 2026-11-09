# Dashboard tour

k10y manages more than one kind of system. The **system switcher** at the top of the sidebar picks one: **Kubernetes**, **HAProxy** or **Nginx** (more will follow). Below it, a second switcher picks the cluster or the HAProxy instance, and the sidebar shows only that system's pages. **Alerts**, **Activity**, **Administration** and **My settings** are shared, at the bottom.

**Home** shows every system at once: the health of each cluster, HAProxy and Nginx instance, firing alerts, and the latest changes. It's the default start page. For step-by-step tasks, see the [Kubernetes guides](./kubernetes-guides) and [HAProxy guides](./haproxy-guides).

You only see the systems, pages and clusters your roles allow, and you can hide any page you don't use in **My settings**.

## Kubernetes: cluster

| Page | What it shows |
|---|---|
| **All clusters** | Every cluster with its health, nodes, pods and CPU/memory, and a note when its agent is older than the hub. Click one to switch to it. |
| **Overview** | Health (healthy, degraded or critical), open problems, CPU and memory charts (5 min up to 7 days), and recent warnings. |
| **Map** | A diagram for one namespace: Ingress → Service → workload → (ReplicaSet) → Pod → ConfigMap, Secret or PVC. Click a box to highlight what it depends on and what depends on it. Missing references are flagged. |
| **AI Assistant** | Ask the AI to investigate the cluster, a namespace or one object. See [AI assistant](./ai). |
| **Nodes** | Status, roles, CPU and memory, capacity, IP and version, plus CPU and memory charts for every node (up to 7 days). Cordon, uncordon and drain. The details panel shows conditions and taints. |
| **Namespaces** | Pods running/total, problems, deployments, services, CPU and memory per namespace; protected namespaces have a lock. A namespace's panel shows its quotas and limit ranges. |

<Shot src="/shots/overview.png" alt="Overview page" url="k10y / lab / overview" />

## Kubernetes: workloads

**Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Autoscalers and Pods.** Each list has search, a namespace filter, sorting, and a row menu (**⋯**) with the actions you're allowed to run (scale, restart, update image, roll back, live logs, shell, diagnose with AI, delete). Click any row to open its details panel: a Deployment's has a **Revisions** tab (history, compare, roll back to any revision), a CronJob's shows its recent runs.

| Page | What it shows |
|---|---|
| **Helm releases** | Every release with chart, versions, status; history, values and manifest (secrets masked), compare revisions, roll back and upgrade with new values. See [Kubernetes insights](./kubernetes#helm-releases). |
| **Right-sizing** | Real CPU and memory use per pod against requests and limits, with recommended values. See [Right-sizing](./kubernetes#right-sizing). |

<Shot src="/shots/cronjobs.png" alt="CronJobs page" url="k10y / lab / cronjobs" />

## Kubernetes: network & config

**Services** (with NodePort links, and in a Service's panel **Open** for its web page through the hub, **Forward** for `k10y port-forward`, and the HAProxy backends that send traffic to it), **Ingresses** (hosts and routes), **Storage** (PVCs with how full they are, and PVs), **Certificates** (TLS certificates with days left), **ConfigMaps** and **Secrets**. For Secrets you see names, types and key names only. Values are never shown.

## Activity

| Page | What it shows |
|---|---|
| **Events** | Kubernetes events, newest first, with a warnings filter. |
| **Alerts** | Firing, pending and resolved alerts, and the alert rules. See [Alerts](./alerts). |
| **Activity** | The audit log: who did what, where, when, and the result. See [Audit log](./audit). |

## Details panel

Click any row to open it. Tabs:
- **Overview:** metadata, labels, the important spec and status for that kind, containers with images, resources and probes, conditions, and **related objects**. Click a related object to drill down, and use **Back** to return.
- **YAML:** the live object. With permission, you can [edit it](./edit-yaml).
- **Events:** events for this object.
- **Logs** (workloads): [live logs](./logs) from all of its pods.

Pods open a dedicated panel with live CPU and memory charts, logs and a **Shell** button.

## Handy details

- Press <kbd>Ctrl</kbd> <kbd>K</kbd> (<kbd>⌘</kbd> <kbd>K</kbd> on a Mac) to search every cluster, HAProxy and Nginx; <kbd>/</kbd> jumps to the page's own search box.
- **Pause** stops auto-refresh while you read; the refresh interval is in **My settings**.
- The theme follows your system, or choose light or dark.

## HAProxy

**Overview**, **Frontends**, **Backends**, **Topology**, **Certificates**, **Rate limits & blocks**, **Logs**, **Config history** and **Raw config** for the selected HAProxy instance. See [HAProxy](./haproxy).

## Nginx

**Overview**, **Sites**, **Upstreams**, **Logs**, **Certificates**, **Config history** and **Raw config** for the selected Nginx instance, with live numbers per site, route and upstream server. See [Nginx](./nginx).

## Everywhere

- **Search** (Ctrl/⌘ K in the top bar) finds objects in every cluster, HAProxy and Nginx.
- **Home** shows every system's health; **Alerts** and **Activity** cover all of them.
