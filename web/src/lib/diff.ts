// Line diff (longest common subsequence), for reviewing YAML edits before saving.
export interface DiffLine { op: " " | "+" | "-"; text: string; a?: number; b?: number }

export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.replace(/\n$/, "").split("\n"), b = after.replace(/\n$/, "").split("\n");
  // Trim the common head and tail so the O(n*m) table only covers the changed middle.
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const ma = a.slice(start, endA), mb = b.slice(start, endB), n = ma.length, m = mb.length;
  const out: DiffLine[] = a.slice(0, start).map((t, i) => ({ op: " ", text: t, a: i + 1, b: i + 1 }));
  if (n * m > 4_000_000) {            // huge change: show as remove-all / add-all
    ma.forEach((t, i) => out.push({ op: "-", text: t, a: start + i + 1 }));
    mb.forEach((t, i) => out.push({ op: "+", text: t, b: start + i + 1 }));
  } else {
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = ma[i] === mb[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0, j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && ma[i] === mb[j]) { out.push({ op: " ", text: ma[i], a: start + i + 1, b: start + j + 1 }); i++; j++; }
      else if (i < n && (j >= m || L[i + 1][j] >= L[i][j + 1])) { out.push({ op: "-", text: ma[i], a: start + i + 1 }); i++; }
      else { out.push({ op: "+", text: mb[j], b: start + j + 1 }); j++; }
    }
  }
  a.slice(endA).forEach((t, k) => out.push({ op: " ", text: t, a: endA + k + 1, b: endB + k + 1 }));
  return out;
}

/** Keep only changed lines plus `context` lines around them; gaps become a single "…" marker (text ""). */
export function hunks(lines: DiffLine[], context = 3): (DiffLine | null)[] {
  const keep = new Set<number>();
  lines.forEach((l, i) => { if (l.op !== " ") for (let k = i - context; k <= i + context; k++) keep.add(k); });
  const out: (DiffLine | null)[] = [];
  lines.forEach((l, i) => { if (keep.has(i)) out.push(l); else if (out[out.length - 1] !== null) out.push(null); });
  if (out[out.length - 1] === null) out.pop();
  return out;
}
