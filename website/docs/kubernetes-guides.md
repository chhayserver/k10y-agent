# Kubernetes: how-to guides

Step-by-step guides for everyday work with your clusters in k10y. Each step names the page and the button you'll see.
Pick the cluster in the sidebar first (**Kubernetes** in the system switcher, then the cluster). Which buttons you
see depends on your [role](./users-roles).

[[toc]]

## Find out why something is broken

1. Open **Overview**. The **Problems** list shows what needs attention first: crash loops, failing image pulls, OOM kills, pending pods, stuck rollouts, failed Jobs and CronJobs, PVCs that aren't bound or are nearly full, expiring certificates, NotReady nodes.
2. Click a problem to open the object's details panel. **Events** often says why straight away (for example *Back-off pulling image* or *Insufficient memory*).
3. For a pod that keeps restarting, open **Logs** and turn on **Previous container**: you see the output of the run that crashed.
4. Still unclear? **⋯ → Diagnose with AI** (needs `ai.use`). The assistant reads events, specs and logs with read-only tools and explains the cause; any fix it proposes opens the normal review, nothing is applied by itself.

## Deploy a new version of an app

1. **Deployments** → the row's **⋯ → Update image…**.
2. Pick the container, type the new image (for example `registry.example.com/shop:1.8.2`).
3. **Dry run** first: Kubernetes checks the change without applying it. Then **Apply**.
4. Watch it roll out on the Deployments page (ready / desired). If the rollout gets stuck (for example the new image can't start), **Overview** shows *stuck rollout*.

## Roll back a deployment

- Quick: **⋯ → Roll back…** returns to the previous revision.
- To a specific version: open the deployment, tab **Revisions**. Each revision shows its images, when it was made and who made it. **Compare** two revisions to see exactly what changed in the pod template, then **Roll back to #N**.

## Scale or restart

- **⋯ → Scale…** on a Deployment or StatefulSet: set the number of replicas (dry run, then apply).
- **⋯ → Restart** starts new pods one by one, like `kubectl rollout restart`: useful after changing a ConfigMap or Secret.
- If an autoscaler (HPA) manages the workload, **Autoscalers** shows its target and limits; scaling by hand is overridden by it.

## Read logs across all pods of an app

1. Open the Deployment (or StatefulSet, DaemonSet, Job) → **Logs**, or **⋯ → Live logs**.
2. Every pod's lines come in one stream, each starting with its pod name in colour. Pick **one pod** to narrow it down.
3. **Start from** the last 15 minutes, hour or 6 hours instead of the last lines; **Errors only** keeps lines that look like errors; your filter text is **highlighted**.
4. **Download** saves what you see as a `.log` file.

More in [Logs and live logs](./logs).

## Open a shell in a container

**Pods** → open the pod → **Shell** (needs `pods.exec`; not in protected namespaces). Pick the container if there are several. Every session is in the activity log. See [Pod shell](./shell).

## Change a ConfigMap, a Secret or any object

1. Open the object → **YAML** → **Edit** (needs `resources.edit`).
2. Change it, then **Review**: k10y shows the diff and runs a server-side dry run.
3. **Apply**. If someone else changed the object since you opened it, k10y refuses, so nothing is overwritten.

To create or update objects from a file, use **Apply YAML** in the top bar (needs `apply.yaml`). See [Edit YAML](./edit-yaml).

## Reach a database or Redis from your computer

1. Create an [API token](./sessions#api-tokens) (My settings → API tokens) and sign in once with the CLI: `k10y hub login https://k10y.example.com:8443`.
2. On **Services**, open the Service → **Forward** next to its port copies the command, for example:
   `k10y port-forward prod svc/postgres 15432:5432 -n shop`
3. Connect your tool to `localhost:15432`. Needs `pods.portforward`; every connection is audited.

## Open an internal web page (a dashboard, an admin UI)

**Services** → open the Service → **Open** next to its HTTP port. It opens in a new tab through the hub, without a NodePort or kubectl, with a link valid for one hour and sandboxed so the page can't act as you in k10y. Needs `services.proxy`.

## Take a node out for maintenance

1. **Nodes** → the node's **⋯ → Cordon**: no new pods are scheduled there.
2. **⋯ → Drain…**: pods are moved off through the eviction API (PodDisruptionBudgets are respected; pods in protected namespaces and DaemonSet pods stay). Type the node name to confirm.
3. Do your maintenance, then **⋯ → Uncordon**.

## Keep important namespaces safe

**Administration → System settings → Protected namespaces** (needs `namespaces.protect`): add the namespaces nobody may change from k10y (for example `kube-system`, `cert-manager`, `ingress-nginx`). They stay visible, but every action, edit and shell there is refused by the agent. The **Namespaces** page marks them with a lock. See [Protected namespaces](./actions#protected-namespaces).

## Right-size CPU and memory requests

1. **Workloads → Right-sizing**: each workload's real use per pod (CPU p95 and peak, memory peak) next to its requests and limits, over 1, 3 or 7 days.
2. Sort by **Verdict**: *Act now* (requests far too low, risk of throttling or OOM), *Fix*, *Can save*, *Fits*.
3. Apply the recommended values in the workload's YAML (or your Helm values) and watch the next days. Numbers get better after a day or two of normal traffic.

## Roll back or upgrade a Helm release

1. **Workloads → Helm releases** → click the release: its history, values and rendered manifest (needs `secrets.view`; secrets are masked).
2. **Compare revisions** to see what changed between two versions.
3. **Roll back…** on an older revision: a dry run, then the rollback (agent with Helm turned on, see [Kubernetes insights](./kubernetes#helm-releases)).
4. **Edit values…** to upgrade with new values on the same chart: **Dry run** shows the exact objects that change, then **Upgrade**. Masked values (`••••••`) keep their real value.

Without Helm turned on for the agent, **Copy rollback command** gives you the `helm rollback` command to run yourself.

## Check certificates and full disks

- **Network & config → Certificates**: every TLS certificate of your Ingresses and cert-manager, sorted by days left.
- **Storage**: how full each PVC is (orange from 80%, red from 90%).
- Both show up in **Overview → Problems** in time, and can [alert](./alerts) you.

## Find anything fast

Press **Ctrl K** (**⌘ K** on a Mac) anywhere and type part of a name: Deployments, Pods, Services, ConfigMaps, Helm releases… in **every cluster**, plus HAProxy and Nginx. Pick a result to open it.

## Get alerted when pods crash

1. **Administration → System settings → Alert channels → Add channel**: Telegram, Slack, email or a webhook.
2. **Alerts → New rule** → **Cluster problems** → choose the kinds (for example Pod, Deployment, CronJob) and namespaces, a hold time (so short blips stay quiet) and the channel.
3. When it fires, **Acknowledge** tells your team you're on it; **Silence…** mutes it during maintenance. See [Alerts](./alerts).

## Give someone access to one cluster

1. **Administration → Users → Add user** (or let them sign in with [single sign-on](./sessions#single-sign-on)).
2. Add a role and choose **on: one cluster**: *Kubernetes viewer* (read and logs), *Kubernetes operator* (also scale, restart, roll back, edit) or *Kubernetes admin* (everything). Or build your own role in **Roles**.
3. For a CI pipeline, create an [API token](./sessions#api-tokens) limited to a role instead of sharing a password.

## See who changed what

**Activity** lists every action, edit, shell, rollout and login, with who, where and the result; filter by cluster. **Download** saves it as CSV. To keep a copy in your SIEM, see [Audit log](./audit#export-to-a-siem-or-log-system).
