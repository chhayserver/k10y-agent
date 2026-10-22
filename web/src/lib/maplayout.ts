// Layered layout of the relationship graph: ingress → service → workload → (replicaset) → pod → config.
export interface TNode { id: string; kind: string; namespace: string | null; name: string; detail: string; status: string; problem?: string | null; owner?: string | null; missing?: boolean; node_ports?: number[] }
export interface TEdge { from: string; to: string; type: string; labels: string[] }
export interface Topology { namespace: string; nodes: TNode[]; edges: TEdge[] }

export const MAP_COLUMNS = [
  { label: "Ingresses", kinds: ["Ingress"] }, { label: "Services", kinds: ["Service"] },
  { label: "Workloads", kinds: ["Deployment", "StatefulSet", "DaemonSet", "Job"] }, { label: "ReplicaSets", kinds: ["ReplicaSet"] },
  { label: "Pods", kinds: ["Pod"], anchor: true }, { label: "Config & storage", kinds: ["ConfigMap", "Secret", "PersistentVolumeClaim"] },
];
export const CONFIG_KINDS = new Set(["ConfigMap", "Secret", "PersistentVolumeClaim"]);
export const MAP = { W: 200, H: 54, ROW: 66, GAP: 64, HEAD: 30 };

export function layoutMap(topo: Topology, opts: { rs: boolean; config: boolean }) {
  const visible = topo.nodes.filter((n) => (opts.rs || n.kind !== "ReplicaSet") && (opts.config || !CONFIG_KINDS.has(n.kind)));
  const byId: Record<string, TNode> = Object.fromEntries(visible.map((n) => [n.id, n]));
  const podsViaRs = new Set(topo.edges.filter((e) => e.type === "rs-pod").map((e) => e.to));
  const drawn = topo.edges.filter((e) => byId[e.from] && byId[e.to] && (e.type === "routes" || e.type === "uses" || e.type === "selects-workload"
    || (e.type === "selects" && !byId[e.to].owner) || (e.type === "owns" && !(opts.rs && podsViaRs.has(e.to)))
    || (opts.rs && (e.type === "rs-owns" || e.type === "rs-pod"))));
  const nb: Record<string, string[]> = {};
  for (const e of topo.edges) if (byId[e.from] && byId[e.to]) { (nb[e.from] ||= []).push(e.to); (nb[e.to] ||= []).push(e.from); }
  const cols = MAP_COLUMNS.map((c) => ({ ...c, nodes: visible.filter((n) => c.kinds.includes(n.kind)) })).filter((c) => c.nodes.length);
  let ai = cols.findIndex((c) => (c as any).anchor);
  if (ai < 0) ai = cols.reduce((best, c, i) => (c.nodes.length > cols[best].nodes.length ? i : best), 0);
  const y: Record<string, number> = {};
  cols[ai].nodes.sort((a, b) => (Number(!a.owner) - Number(!b.owner)) || (a.owner || "").localeCompare(b.owner || "") || a.name.localeCompare(b.name, undefined, { numeric: true }));
  cols[ai].nodes.forEach((n, i) => { y[n.id] = i * MAP.ROW; });
  const place = (col: (typeof cols)[number]) => {
    const items = col.nodes.map((n) => {
      const ys = (nb[n.id] || []).filter((id) => id in y).map((id) => y[id]);
      return { n, d: ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Infinity };
    }).sort((a, b) => a.d - b.d || a.n.name.localeCompare(b.n.name));
    let last = -Infinity;
    for (const it of items) { const want = Number.isFinite(it.d) ? it.d : last === -Infinity ? 0 : last + MAP.ROW; y[it.n.id] = Math.max(want, last + MAP.ROW); last = y[it.n.id]; }
    col.nodes = items.map((it) => it.n);
  };
  for (let i = ai - 1; i >= 0; i--) place(cols[i]);
  for (let i = ai + 1; i < cols.length; i++) place(cols[i]);
  const minY = Math.min(...Object.values(y));
  const pos: Record<string, { x: number; y: number }> = {};
  cols.forEach((c, ci) => c.nodes.forEach((n) => { pos[n.id] = { x: ci * (MAP.W + MAP.GAP), y: y[n.id] - minY + MAP.HEAD }; }));
  const height = Math.max(...Object.values(pos).map((p) => p.y)) + MAP.H;
  const width = cols.length * (MAP.W + MAP.GAP) - MAP.GAP;
  const edges = drawn.map((e) => {
    const a = pos[e.from], b = pos[e.to], x1 = a.x + MAP.W, y1 = a.y + MAP.H / 2, x2 = b.x - 6, y2 = b.y + MAP.H / 2, mx = (x1 + x2) / 2;
    return { ...e, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` };
  });
  return { cols: cols.map((c, i) => ({ label: c.label, count: c.nodes.length, x: i * (MAP.W + MAP.GAP) })), pos, edges, byId, width, height };
}

/** Everything upstream and downstream of a node, plus the owners/services of any pod reached. */
export function relatedSet(topo: Topology, id: string): Set<string> {
  const fwd: Record<string, string[]> = {}, rev: Record<string, string[]> = {};
  for (const e of topo.edges) { if (e.type === "selects-workload") continue; (fwd[e.from] ||= []).push(e.to); (rev[e.to] ||= []).push(e.from); }
  const set = new Set([id]);
  const walk = (adj: Record<string, string[]>) => { const stack = [id], seen = new Set([id]); while (stack.length) for (const n of adj[stack.pop()!] || []) if (!seen.has(n)) { seen.add(n); set.add(n); stack.push(n); } };
  walk(fwd); walk(rev);
  const kindOf = (nid: string) => nid.split("/")[0];
  for (const n of [...set]) if (kindOf(n) === "Pod") (rev[n] || []).forEach((o) => set.add(o));
  for (const n of [...set]) if (["ReplicaSet", "Service"].includes(kindOf(n))) (rev[n] || []).forEach((o) => set.add(o));
  return set;
}
