// Formatting and status helpers shared by every page.

export const fmt = {
  age(s: number | null | undefined): string {
    if (s == null) return "–";
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) { const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return m ? `${h}h ${m}m` : `${h}h`; }
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
    return h ? `${d}d ${h}h` : `${d}d`;
  },
  bytes(b: number | null | undefined): string {
    if (b == null) return "–";
    if (b >= 2 ** 30) return `${(b / 2 ** 30).toFixed(1)} GiB`;
    if (b >= 2 ** 20) return `${Math.round(b / 2 ** 20)} MiB`;
    return `${Math.round(b / 1024)} KiB`;
  },
  cpu(m: number | null | undefined): string {
    return m == null ? "–" : m >= 1000 ? `${(m / 1000).toFixed(2)} cores` : `${m}m`;
  },
  pct(p: number | null | undefined): string { return p == null ? "–" : `${Math.round(p)}%`; },
  time(t: number | string): string { return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); },
  timeSec(t: number | string): string { return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); },
  dateTime(t: string | null | undefined): string {
    return t ? new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
  },
  ago(ts: string | null | undefined): string {
    return ts ? `${fmt.age(Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000)))} ago` : "–";
  },
  plural(n: number, w: string): string { return `${n} ${w}${n === 1 ? "" : "s"}`; },
};

export type Level = "good" | "warning" | "serious" | "critical" | "neutral" | "pending";

export const HEALTH: Record<string, { level: Level; label: string }> = {
  healthy: { level: "good", label: "Healthy" },
  degraded: { level: "warning", label: "Degraded" },
  critical: { level: "critical", label: "Critical" },
};

export const CRITICAL_POD = new Set(["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
  "CreateContainerConfigError", "CreateContainerError", "RunContainerError", "OOMKilled", "Error", "ContainerCannotRun", "Failed"]);

export interface PodLike { status: string; phase: string; problem?: string | null; ready_containers: number; total_containers: number }

export function podLevel(p: PodLike): Level {
  if (p.status === "Terminating") return "neutral";
  if (p.problem) return CRITICAL_POD.has(p.status) ? "critical" : "warning";
  if (p.phase === "Succeeded") return "neutral";
  if (p.phase === "Pending") return "pending";
  if (p.phase === "Running") return p.ready_containers === p.total_containers ? "good" : "warning";
  return "neutral";
}
export const podLabel = (p: PodLike) => (p.phase === "Succeeded" && p.status === "Succeeded" ? "Completed" : p.status);

export function problemLevel(pr: { kind: string; reason: string }): Level {
  if (pr.kind === "Node") return pr.reason === "NotReady" ? "critical" : "serious";
  if (pr.kind === "Pod" && CRITICAL_POD.has(pr.reason)) return "critical";
  if (pr.kind === "Job") return "critical";
  return "warning";
}

export const KIND_ICON: Record<string, string> = {
  Namespace: "folder", Node: "nodes", Ingress: "globe", Service: "service", Deployment: "deploy", StatefulSet: "db",
  DaemonSet: "nodes", Job: "pending", CronJob: "pending", ReplicaSet: "copy", Pod: "pods", ConfigMap: "file", Secret: "lock",
  PersistentVolumeClaim: "disk", PersistentVolume: "disk", HorizontalPodAutoscaler: "scale",
};
export const DETAIL_KINDS = new Set(["Pod", "Service", "ConfigMap", "PersistentVolumeClaim", "Node", "Namespace", "Deployment",
  "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "Ingress", "Secret"]);

export function readyLevel(x: { ready: number; desired: number }): [Level, string] {
  if (x.desired === 0) return ["neutral", "Scaled to 0"];
  return x.ready >= x.desired ? ["good", "Ready"] : ["warning", "Degraded"];
}

/** Escape text for the few places that build HTML strings (dialog descriptions). */
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
