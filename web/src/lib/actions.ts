// Row menus and control actions, filtered by the user's permissions on the current cluster.
import ImageForm from "@/forms/ImageForm.vue";
import RollbackForm from "@/forms/RollbackForm.vue";
import ScaleForm from "@/forms/ScaleForm.vue";
import YamlForm from "@/forms/YamlForm.vue";
import router from "@/router";
import { aiRequest, type AIRequest } from "@/stores/aiRequest";
import { useCluster } from "@/stores/cluster";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { closePanel, openAction, openDetail, panel, type MenuItem } from "@/stores/ui";
import { esc } from "./format";

const ref = (o: { namespace?: string; name: string }) => `<b>${esc(o.namespace ? `${o.namespace}/` : "")}${esc(o.name)}</b>`;
const plural: Record<string, string> = { deployment: "deployments", statefulset: "statefulsets", daemonset: "daemonsets", job: "jobs",
  pod: "pods", service: "services", configmap: "configmaps", secret: "secrets" };

export function startAI(req: AIRequest, autorun = true) {
  aiRequest.req = req; aiRequest.autorun = autorun;
  closePanel();
  router.push("/ai");
}
export function showOnMap(kind: string, o: { namespace: string; name: string }) {
  prefs.map.ns = o.namespace;
  router.push({ path: "/map", query: { select: `${kind}/${o.namespace}/${o.name}` } });
}

export function actScale(o: any, kind = "deployment") {
  openAction({
    title: `Scale ${kind}`, desc: `${ref(o)} currently has <b>${o.ready ?? "?"}/${o.desired ?? "?"}</b> replicas ready.`,
    form: ScaleForm, model: { replicas: o.desired ?? 1 }, confirmLabel: "Scale",
    request: (m, dry) => {
      const n = Number(m.replicas);
      if (!Number.isInteger(n) || n < 0 || n > 1000) throw new Error("Replicas must be a whole number from 0 to 1000.");
      return { path: `${plural[kind]}/${o.namespace}/${o.name}/scale`, body: { replicas: n, dry_run: dry } };
    },
  });
}
export function actRestart(o: any, kind = "deployment") {
  const how = kind === "daemonset" ? "the pod on each node is replaced in turn" : "pods are replaced one at a time";
  openAction({ title: `Restart ${kind}`, desc: `Rolling restart of ${ref(o)}: ${how}.`, confirmLabel: "Restart",
    request: (_m, dry) => ({ path: `${plural[kind]}/${o.namespace}/${o.name}/restart`, body: { dry_run: dry } }) });
}
export function actSuspend(o: any, suspend: boolean) {
  openAction({ title: `${suspend ? "Suspend" : "Resume"} CronJob`, confirmLabel: suspend ? "Suspend" : "Resume",
    desc: suspend ? `${ref(o)} stops creating new jobs. Jobs already running keep going.` : `${ref(o)} creates jobs on its schedule again.`,
    request: (_m, dry) => ({ path: `cronjobs/${o.namespace}/${o.name}/${suspend ? "suspend" : "resume"}`, body: { dry_run: dry } }) });
}
export function actTrigger(o: any) {
  openAction({ title: "Run CronJob now", confirmLabel: "Run now",
    desc: `Creates a job from ${ref(o)} right away, like <code>kubectl create job --from=cronjob/${esc(o.name)}</code>. The schedule is not changed.`,
    request: (_m, dry) => ({ path: `cronjobs/${o.namespace}/${o.name}/trigger`, body: { dry_run: dry } }) });
}
export function actImage(o: any) {
  const containers = o.containers || [];
  openAction({
    title: "Update image", desc: `Starts a rolling update of ${ref(o)}. You can roll back afterwards.`, form: ImageForm, confirmLabel: "Update image",
    model: { containers, container: containers[0]?.name, image: containers[0]?.image },
    request: (m, dry) => {
      const cur = containers.find((c: any) => c.name === m.container)?.image;
      if (!String(m.image || "").trim()) throw new Error("Enter an image, for example nginx:1.27.");
      if (m.image.trim() === cur) throw new Error("That is already the current image.");
      return { path: `deployments/${o.namespace}/${o.name}/image`, body: { container: m.container, image: m.image.trim(), dry_run: dry } };
    },
  });
}
export function actRollback(o: any) {
  openAction({
    title: "Roll back deployment", desc: `Choose the revision of ${ref(o)} to return to. This starts a rolling update.`, form: RollbackForm,
    model: { namespace: o.namespace, name: o.name, revision: null }, confirmLabel: "Roll back",
    request: (m, dry) => {
      if (!m.revision) throw new Error("Pick a revision.");
      return { path: `deployments/${o.namespace}/${o.name}/rollback`, body: { revision: m.revision, dry_run: dry } };
    },
  });
}
export function actDelete(kind: string, o: any) {
  const what: Record<string, string> = {
    deployment: `Deletes the deployment ${ref(o)} and all of its pods. This cannot be undone.`,
    job: `Deletes the job ${ref(o)} and its pods (including their logs).`,
    pod: `Deletes the pod ${ref(o)}. If a controller manages it, a replacement pod is created automatically.`,
    service: `Deletes the service ${ref(o)}. Anything reaching the app through it, including its NodePort, stops working.`,
    configmap: `Deletes the ConfigMap ${ref(o)}.`, secret: `Deletes the Secret ${ref(o)}.`,
  };
  const users = o.used_by?.length ? `<div class="callout">Still used by <b>${esc(o.used_by.join(", "))}</b>. Pods that need it will fail to start.</div>` : "";
  openAction({
    title: `Delete ${kind}`, desc: what[kind] + users, danger: true, confirmLabel: `Delete ${kind}`, confirmText: kind === "pod" ? null : o.name,
    request: () => ({ method: "DELETE", path: `${plural[kind]}/${o.namespace}/${o.name}` }),
    after: () => { if (panel.current?.name === o.name) closePanel(); },
  });
}
export function actCordon(n: any, on: boolean) {
  openAction({
    title: on ? "Cordon node" : "Uncordon node", confirmLabel: on ? "Cordon" : "Uncordon",
    desc: on ? `Marks <b>${esc(n.name)}</b> unschedulable: running pods keep running, but no new pods are placed on it.`
      : `Allows new pods to be scheduled on <b>${esc(n.name)}</b> again.`,
    request: (_m, dry) => ({ path: `nodes/${n.name}/${on ? "cordon" : "uncordon"}`, body: { dry_run: dry } }),
  });
}
export function actDrain(n: any) {
  const c = useCluster(), only = (c.data?.nodes || []).length === 1;
  openAction({
    title: "Drain node", danger: true, confirmLabel: "Drain node", confirmText: n.name,
    desc: `Cordons <b>${esc(n.name)}</b> and evicts its pods. DaemonSet pods, static pods and pods in protected namespaces
      (${esc((c.info?.protected_namespaces || []).join(", "))}) are left alone.
      <div class="callout" style="margin-top:10px">${only ? "<b>This is your only node.</b> Evicted pods stay Pending until you uncordon it. " : ""}Run a dry run first to see exactly which pods are affected.</div>`,
    request: (_m, dry) => ({ path: `nodes/${n.name}/drain`, body: { dry_run: dry } }),
  });
}
const APPLY_SAMPLE = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello
  namespace: demo
spec:
  replicas: 2
  selector: {matchLabels: {app: hello}}
  template:
    metadata: {labels: {app: hello}}
    spec:
      containers: [{name: hello, image: nginx:1.27}]`;
export function actApply(yaml = "", title = "Apply YAML", desc?: string, after?: () => void) {
  openAction({
    title, wide: true, form: YamlForm, model: { yaml, placeholder: APPLY_SAMPLE }, confirmLabel: "Apply", after,
    desc: desc || `Creates or updates the objects below with server-side apply. Separate objects with <code>---</code>. Objects in protected namespaces (${esc((useCluster().info?.protected_namespaces || []).join(", "))}) are rejected.`,
    request: (m, dry) => {
      if (!String(m.yaml || "").trim()) throw new Error("Paste a YAML manifest first.");
      return { path: "apply", body: { yaml: m.yaml, dry_run: dry } };
    },
  });
}

/** The ⋯ menu for any listed object. */
export function menuFor(kind: string, o: any): MenuItem[] {
  const s = useSession(), c = useCluster(), can = (p: string) => s.can(p) && c.control;
  const prot = c.isProtected(o.namespace);
  const guard = (items: MenuItem[]) => (prot ? [{ note: `${o.namespace} is protected` }, ...items.map((i) => (i.sep || (i as any).safe ? i : { ...i, disabled: true }))] : items);
  const safe = (i: MenuItem): MenuItem => ({ ...i, ...({ safe: true } as any) });
  const ai = s.can("ai.use") ? [safe({ label: "Diagnose with AI", icon: "ai", run: () => startAI({ scope: "object", kind: KIND[kind], namespace: o.namespace, name: o.name }) })] : [];
  const KIND_ = KIND[kind];
  const details = safe({ label: kind === "pod" ? "Details & logs" : "Open details", icon: "file", run: () => openDetail(KIND_, o.namespace, o.name) });
  const map = safe({ label: "Show on map", icon: "map", run: () => showOnMap(KIND_, o) });
  const items: MenuItem[] = [details];
  if (["deployment", "statefulset", "daemonset", "job"].includes(kind) && s.can("logs.view"))
    items.push(safe({ label: "Live logs", icon: "events", run: () => openDetail(KIND_, o.namespace, o.name, { tab: "logs" }) }));
  if (kind === "deployment") {
    if (can("workloads.scale")) items.push({ label: "Scale…", icon: "scale", run: () => actScale(o) });
    if (can("workloads.restart")) items.push({ label: "Restart", icon: "refresh", run: () => actRestart(o) });
    if (can("workloads.update_image")) items.push({ label: "Update image…", icon: "tag", run: () => actImage(o) });
    if (can("workloads.rollback")) items.push({ label: "Roll back…", icon: "rollback", run: () => actRollback(o) });
    items.push(map, ...ai);
    if (can("workloads.delete")) items.push({ sep: true }, { label: "Delete deployment…", icon: "trash", danger: true, run: () => actDelete("deployment", o) });
  } else if (kind === "statefulset") {
    if (can("workloads.scale")) items.push({ label: "Scale…", icon: "scale", run: () => actScale(o, "statefulset") });
    if (can("workloads.restart")) items.push({ label: "Restart", icon: "refresh", run: () => actRestart(o, "statefulset") });
    items.push(map, ...ai);
  } else if (kind === "daemonset") {
    if (can("workloads.restart")) items.push({ label: "Restart", icon: "refresh", run: () => actRestart(o, "daemonset") });
    items.push(map);
  } else if (kind === "job") {
    items.push(map);
    if (can("workloads.delete")) items.push({ sep: true }, { label: "Delete job…", icon: "trash", danger: true, run: () => actDelete("job", o) });
  } else if (kind === "cronjob") {
    if (can("workloads.restart")) items.push({ label: "Run now…", icon: "play", run: () => actTrigger(o) },
      o.suspended ? { label: "Resume", icon: "play", run: () => actSuspend(o, false) } : { label: "Suspend", icon: "pause", run: () => actSuspend(o, true) });
    items.push(...ai);
  } else if (kind === "ingress" || kind === "pvc") {
    items.push(map, ...ai);
  } else if (kind === "pv" || kind === "hpa") {
    items.push(...ai);
  } else if (kind === "pod") {
    items.push(map, ...ai);
    if (can("pods.delete")) items.push({ sep: true }, { label: "Delete pod…", icon: "trash", danger: true, run: () => actDelete("pod", o) });
  } else if (kind === "service") {
    items.push(map);
    if (can("services.delete")) items.push({ sep: true }, { label: "Delete service…", icon: "trash", danger: true, run: () => actDelete("service", o) });
  } else if (kind === "configmap" || kind === "secret") {
    items.push(map);
    if (can("config.delete")) items.push({ sep: true }, { label: `Delete ${kind === "secret" ? "Secret" : "ConfigMap"}…`, icon: "trash", danger: true, run: () => actDelete(kind, o) });
  } else if (kind === "node") {
    if (can("nodes.manage")) items.push(o.unschedulable ? { label: "Uncordon", icon: "ok", run: () => actCordon(o, false) } : { label: "Cordon", icon: "ban", run: () => actCordon(o, true) },
      { label: "Drain…", icon: "drain", danger: true, run: () => actDrain(o) });
    return items;
  } else if (kind === "namespace") {
    return [safe({ label: "Open details", icon: "file", run: () => openDetail("Namespace", "", o.name) }),
      safe({ label: "Show on map", icon: "map", run: () => { prefs.map.ns = o.name; router.push("/map"); } }),
      ...(s.can("ai.use") ? [{ label: "Diagnose with AI", icon: "ai", run: () => startAI({ scope: "namespace", namespace: o.name }) }] : [])];
  }
  return guard(items);
}
export const KIND: Record<string, string> = { deployment: "Deployment", statefulset: "StatefulSet", daemonset: "DaemonSet", job: "Job",
  pod: "Pod", service: "Service", configmap: "ConfigMap", secret: "Secret", node: "Node", namespace: "Namespace", cronjob: "CronJob",
  ingress: "Ingress", pvc: "PersistentVolumeClaim", pv: "PersistentVolume", hpa: "HorizontalPodAutoscaler" };
