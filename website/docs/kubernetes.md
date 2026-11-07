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

**Roll back…** copies the `helm rollback <release> <revision> -n <namespace>` command. k10y doesn't write Helm's records itself, so hooks and history stay consistent.

## Certificates

**Network & config → Certificates** shows the TLS certificates of Ingress TLS secrets (only the certificate's names and dates are read, never the key) and cert-manager `Certificate`s, sorted by days left. Certificates that expire within 14 days, are expired or not ready appear on the Overview's problem list, so [alerts](./alerts) can warn you.

## CronJobs

A CronJob's details panel shows its recent runs (start, duration, result). The problem list warns when its last run failed, when it has never succeeded, or when the latest scheduled run didn't succeed. Suspended CronJobs are left alone, and their Jobs no longer show up twice as failed Jobs.

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

These features use the agent's read-only RBAC: ResourceQuotas and LimitRanges, and cert-manager `certificates`. Re-apply `install.yaml` (or `install-readonly.yaml`) when you [upgrade](./upgrading) the agent to pick them up.
