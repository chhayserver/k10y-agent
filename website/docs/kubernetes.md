# Kubernetes insights

Beyond lists of resources, k10y reads what Kubernetes and Helm already record and turns it into answers. Everything here is read-only.

## Search everything

Press **Ctrl K** (**⌘ K** on a Mac) or click the search box in the top bar. Type part of a name to find Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Pods, Services, Ingresses, ConfigMaps, Secrets, PVCs, Namespaces and Helm releases **in every cluster you can see**, plus HAProxy frontends, backends and servers. Pick a result to open it (k10y switches cluster for you). Secrets only show up with `secrets.view`.

## Rollout history

A Deployment's details panel has a **Revisions** tab: every revision Kubernetes keeps (`revisionHistoryLimit`), with its images, when it was made and why: the `kubernetes.io/change-cause` annotation, or the k10y user and action that created it (from the [audit log](./audit)).

- **Compare** any two revisions to see what changed in the pod template.
- **Roll back to #N** returns to any older revision (needs `workloads.rollback`), with the usual review and audit.

## Helm releases

**Workloads → Helm releases** lists every release with its chart, version, app version, status and revision, read from Helm's own records (Secrets labelled `owner=helm`). Open one to see its history, and with `secrets.view` its values and rendered manifest, and compare two revisions. Anything that looks like a password, token or key is masked.

**Roll back from k10y** (opt-in): with Helm turned on for the agent, **Roll back…** runs a dry run first, then `helm rollback` on the agent (the image includes Helm 3.16), so Helm's hooks run and its history stays right. It needs `workloads.rollback`, refuses protected namespaces, and is in the activity log. Helm re-applies whatever the chart contains, so the agent needs Kubernetes' built-in **edit** role for this:

```bash
kubectl apply -f https://github.com/chhayserver/k10y-agent/releases/latest/download/rbac-helm.yaml
kubectl -n k10y set env deploy/k10y-agent K10Y_HELM_ENABLED=true
```

**Edit values…** (needs `apply.yaml` and `secrets.view`, and Helm turned on) upgrades a release with changed values, using the chart it already runs: edit the values, **Dry run** to see exactly which rendered objects change, then **Upgrade**. Values shown as `••••••` keep their real value. Helm doesn't keep subcharts in its release records, so charts with dependencies can't be upgraded this way; use Helm with the chart's source for those.

Without it, the button is **Copy rollback command** and gives you `helm rollback <release> <revision> -n <namespace>` to run yourself.

## Certificates

**Network & config → Certificates** shows the TLS certificates of Ingress TLS secrets (only the certificate's names and dates are read, never the key) and cert-manager `Certificate`s, sorted by days left. Certificates that expire within 14 days, are expired or not ready appear on the Overview's problem list, so [alerts](./alerts) can warn you.

## CronJobs

A CronJob's details panel shows its recent runs (start, duration, result). The problem list warns when its last run failed, when it has never succeeded, or when the latest scheduled run didn't succeed. Suspended CronJobs are left alone, and their Jobs no longer show up twice as failed Jobs.

## Volume usage and full volumes

**Storage** shows how full each PersistentVolumeClaim is (a bar, orange from 80%, red from 90%; hover for bytes and inodes). The problem list, and so [alerts](./alerts), warn when a volume is **90% full** or out of inodes. Change the threshold with `K10Y_VOLUME_FULL_PERCENT` on the agent.

The agent reads this from each node's kubelet (`/stats/summary`), with the read-only `nodes/stats` permission (not `nodes/proxy`), checking the kubelet's certificate against the cluster CA. Usage is only known while a pod has the volume mounted. If your kubelets use certificates not signed by the cluster CA, set `K10Y_KUBELET_INSECURE=true`.

Usage is only known for volumes the kubelet measures: CSI volumes (AWS EBS, GCE PD, Azure Disk, Longhorn, Ceph, …) and emptyDir. `hostPath`-based volumes, such as k3s's default `local-path`, show no usage.

## Port-forward (databases, Redis, any TCP)

Reach a pod's or Service's TCP port from your computer, through the hub, without kubectl access to the cluster:

```bash
k10y hub login https://k10y.example.com:8443          # once, with an API token (My settings → API tokens)
k10y port-forward lab svc/postgres 15432:5432 -n shop  # then connect to localhost:15432
```

A Service's panel has **Forward** next to each TCP port, which copies this command. It needs the `pods.portforward` permission (Admin and Kubernetes admin have it) and a control-enabled agent; protected namespaces are refused. A Service is forwarded to one of its ready pods. Every connection is in the activity log with its duration and bytes.

## Open a Service in the browser

A Service's details panel has **Open** next to each TCP port. It opens the Service's web page in a new tab through the hub, without kubectl or a NodePort, using the Kubernetes API server's service proxy.

- Needs the `services.proxy` permission (Admin and Kubernetes admin have it) and a control-enabled agent; protected namespaces are refused.
- Each link is valid for one hour, works only for you, and stops working if you lose the permission. Opening it is in the activity log.
- The page runs **sandboxed**: it can't read k10y's cookies or use your k10y session, and the link isn't sent to other sites.
- It's for HTTP apps and dashboards. Apps that load files from absolute paths (`/static/…`) or need WebSockets may not work fully this way.

## Namespace quotas

A Namespace's details panel shows its ResourceQuotas (used against hard, with a bar per resource) and LimitRanges (default requests and limits).

## Right-sizing

**Workloads → Right-sizing** compares what each workload really uses with its requests and limits, per pod, over the last 1, 3 or 7 days:

- CPU: 95th percentile and peak. Recommended request = p95 × 1.3.
- Memory: peak. Recommended request = peak × 1.3, and a limit at least that.

Each workload gets a verdict (**Act now**, **Fix**, **Can save**, **Fits**) with what to change, and the top shows how much CPU and memory you'd free. It uses the hub's own 5-minute measurements, so it appears after about 10 minutes and gets better over a day or two.

## 7-day history

The hub samples every cluster each minute and keeps 5-minute points for 7 days. On **Overview**, **Nodes** and a pod's panel, the **6 h**, **24 h** and **7 d** ranges use them; the short ranges still come from the agent.

## Service → HAProxy

A Service's details panel shows which HAProxy backends send traffic to it: backends synced from the Service, and servers whose address matches its node ports or endpoints. Click one to open it on the HAProxy pages.

## Permissions the agent needs

**Agent outdated?** All clusters, **Administration → Clusters** and the sidebar say when a cluster's agent is older than the hub, and hovering shows which features that costs and the update command.

These features use the agent's read-only RBAC: ResourceQuotas and LimitRanges, cert-manager `certificates`, and `nodes/stats` for volume usage. **Open** uses `services/proxy` from the control role. Re-apply `install.yaml` (or `install-readonly.yaml`) when you [upgrade](./upgrading) the agent to pick them up.
