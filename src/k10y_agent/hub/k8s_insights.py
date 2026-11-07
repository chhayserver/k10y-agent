"""Kubernetes features the hub computes across agents and time.

  * Long-term CPU/memory history: every minute the hub reads each agent's latest measurements and keeps
    5-minute averages and peaks for a week (the agents themselves keep one hour in memory).
  * Right-sizing: real use (95th percentile and peak, from that history) against requests and limits.
  * Search across every cluster (and HAProxy instance) the user can see.
  * Which HAProxy backends send traffic to a Kubernetes Service, with their live numbers.
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import JSON, BigInteger, Integer, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, Cluster

log = logging.getLogger("k10y.k8s")
SAMPLE_S = 60
BUCKET_MS = 5 * 60_000
KEEP_MS = 7 * 86_400_000
MI = 1024 * 1024


class K8sSample(Base):
    """5-minute CPU/memory averages and peaks of a cluster, its nodes and its pods."""
    __tablename__ = "k8s_samples"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True)
    t: Mapped[int] = mapped_column(BigInteger, index=True)              # epoch ms, start of the 5 minutes
    data: Mapped[Any] = mapped_column(JSON)   # {"c": [cpu_avg, mem_avg, cpu_max, mem_max], "n": {node: [...]}, "p": {"ns/pod": [...]}}


def _stats(values: List[Tuple[int, int]]) -> List[int]:
    """[(cpu_m, mem_bytes), …] -> [cpu_avg, mem_avg, cpu_max, mem_max]."""
    n = len(values)
    return [round(sum(v[0] for v in values) / n), round(sum(v[1] for v in values) / n),
            max(v[0] for v in values), max(v[1] for v in values)]


class K8sHistory:
    """Samples every cluster each minute and writes one row per cluster every 5 minutes."""

    def __init__(self, db_factory: Callable[[], Session], agent_get: Callable[[Cluster, str], Any]):
        self.db_factory, self.agent_get = db_factory, agent_get
        self._acc: Dict[int, Tuple[int, Dict[str, List[Tuple[int, int]]]]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pruned = 0.0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="k8s-history", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(20)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("Kubernetes history sample failed")
            self._stop.wait(SAMPLE_S)

    def run_once(self, now_ms: Optional[int] = None) -> None:
        now_ms = now_ms or int(time.time() * 1000)
        with self.db_factory() as s:
            for c in s.execute(select(Cluster)).scalars().all():
                try:
                    snap = self.agent_get(c, "metrics/history?scope=snapshot")
                except Exception as e:          # an unreachable cluster just has a gap
                    log.debug("no metrics from %s: %s", c.name, e)
                    continue
                self.add(s, c.id, now_ms, snap)
            if time.time() - self._pruned > 3600:
                s.execute(delete(K8sSample).where(K8sSample.t < now_ms - KEEP_MS))
                self._pruned = time.time()
            s.commit()

    def add(self, s: Session, cluster_id: int, now_ms: int, snap: Dict[str, Any]) -> None:
        bucket = now_ms - now_ms % BUCKET_MS
        start, acc = self._acc.get(cluster_id, (bucket, {}))
        if start != bucket and acc:
            s.add(K8sSample(cluster_id=cluster_id, t=start, data=self._row(acc)))
            acc = {}
        if snap.get("cluster"):
            acc.setdefault("c", []).append((snap["cluster"][1], snap["cluster"][2]))
        for kind, key in (("nodes", "n:"), ("pods", "p:")):
            for name, point in (snap.get(kind) or {}).items():
                acc.setdefault(key + name, []).append((point[1], point[2]))
        self._acc[cluster_id] = (bucket, acc)

    @staticmethod
    def _row(acc: Dict[str, List[Tuple[int, int]]]) -> Dict[str, Any]:
        row: Dict[str, Any] = {"c": _stats(acc["c"]) if acc.get("c") else None, "n": {}, "p": {}}
        for key, values in acc.items():
            if key.startswith("n:"):
                row["n"][key[2:]] = _stats(values)
            elif key.startswith("p:"):
                row["p"][key[2:]] = _stats(values)
        return row


def series(s: Session, cluster_id: int, scope: str, since_ms: int, name: str = "") -> List[List[int]]:
    """[[t, cpu_avg, mem_avg, cpu_max, mem_max], …] for the cluster, one node or one pod ("ns/name")."""
    rows = s.execute(select(K8sSample.t, K8sSample.data).where(K8sSample.cluster_id == cluster_id, K8sSample.t > since_ms)
                     .order_by(K8sSample.t)).all()
    out = []
    for t, d in rows:
        v = d.get("c") if scope == "cluster" else (d.get("n" if scope == "node" else "p") or {}).get(name)
        if v:
            out.append([t, *v])
    return out


# --- right-sizing ------------------------------------------------------------------------------

def _p95(values: List[int]) -> int:
    if not values:
        return 0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]


def _owner_of_old_pod(pod: str, owners: List[str]) -> Optional[str]:
    """A pod that no longer exists: the workload whose name it starts with (web-7d9f8b6c5-x2k4p -> web)."""
    best = None
    for o in owners:
        name = o.split("/", 1)[1]
        if pod.startswith(name + "-") and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)?", pod[len(name) + 1:]):
            if best is None or len(name) > len(best.split("/", 1)[1]):
                best = o
    return best


def rightsizing(pods: List[Dict[str, Any]], rows: List[Tuple[int, Dict[str, Any]]]) -> Dict[str, Any]:
    """Compare each workload's per-pod use with its per-pod requests and limits."""
    workloads: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for p in pods:
        if not p.get("owner") or p.get("phase") != "Running":
            continue
        w = workloads.setdefault((p["namespace"], p["owner"]), {"pods": 0, "cpu_req": 0, "cpu_lim": 0, "mem_req": 0, "mem_lim": 0, "names": set()})
        w["pods"] += 1
        w["names"].add(p["name"])
        for field, key in (("cpu_req", "cpu_request_m"), ("cpu_lim", "cpu_limit_m"), ("mem_req", "memory_request_bytes"), ("mem_lim", "memory_limit_bytes")):
            w[field] = max(w[field], p.get(key) or 0)
    owners_by_ns: Dict[str, List[str]] = {}
    for ns, owner in workloads:
        owners_by_ns.setdefault(ns, []).append(owner)
    usage: Dict[Tuple[str, str], Dict[str, List[int]]] = {}
    first = last = None
    for t, d in rows:
        first, last = (t if first is None else first), t
        for key, v in (d.get("p") or {}).items():
            ns, pod = key.split("/", 1)
            owner = next((o for (wns, o), w in workloads.items() if wns == ns and pod in w["names"]), None) \
                or _owner_of_old_pod(pod, owners_by_ns.get(ns, []))
            if owner:
                u = usage.setdefault((ns, owner), {"cpu": [], "cpu_max": [], "mem": [], "mem_max": []})
                u["cpu"].append(v[0]); u["mem"].append(v[1]); u["cpu_max"].append(v[2]); u["mem_max"].append(v[3])
    out = []
    for (ns, owner), w in workloads.items():
        u = usage.get((ns, owner))
        if not u:
            continue
        cpu95, cpu_peak, mem95, mem_peak = _p95(u["cpu"]), max(u["cpu_max"]), _p95(u["mem"]), max(u["mem_max"])
        rec_cpu = max(10, int(round(cpu95 * 1.3 / 5) * 5))
        rec_mem = max(32 * MI, int(round(mem_peak * 1.3 / MI)) * MI)
        findings: List[Tuple[str, str]] = []     # (level, text)
        if not w["cpu_req"]:
            findings.append(("warning", "No CPU request: the scheduler can't plan for it"))
        elif cpu95 > w["cpu_req"]:
            findings.append(("warning", "CPU request too low for its normal use"))
        elif w["cpu_req"] > max(3 * cpu95, cpu95 + 200) and w["cpu_req"] - rec_cpu >= 100:
            findings.append(("info", "CPU over-provisioned"))
        if not w["mem_req"]:
            findings.append(("warning", "No memory request"))
        elif mem_peak > w["mem_req"]:
            findings.append(("warning", "Memory request lower than its peak use"))
        elif w["mem_req"] > 3 * mem_peak and w["mem_req"] - rec_mem >= 128 * MI:
            findings.append(("info", "Memory over-provisioned"))
        if w["mem_lim"] and mem_peak >= 0.9 * w["mem_lim"]:
            findings.append(("critical", "Close to its memory limit: OOM risk"))
        if w["cpu_lim"] and cpu_peak >= 0.95 * w["cpu_lim"]:
            findings.append(("warning", "Hitting its CPU limit: it gets throttled"))
        level = "critical" if any(f[0] == "critical" for f in findings) else "warning" if any(f[0] == "warning" for f in findings) \
            else "info" if findings else "good"
        out.append({
            "namespace": ns, "workload": owner, "pods": w["pods"],
            "cpu": {"request": w["cpu_req"], "limit": w["cpu_lim"], "p95": cpu95, "peak": cpu_peak, "recommended": rec_cpu},
            "memory": {"request": w["mem_req"], "limit": w["mem_lim"], "p95": mem95, "peak": mem_peak, "recommended": rec_mem,
                       "recommended_limit": max(rec_mem, int(round(mem_peak * 1.5 / MI)) * MI)},
            "findings": [f[1] for f in findings], "level": level,
            # what all pods together would give back with the recommended requests (negative = more needed)
            "savings": {"cpu_m": (w["cpu_req"] - rec_cpu) * w["pods"] if w["cpu_req"] else 0,
                        "memory_bytes": (w["mem_req"] - rec_mem) * w["pods"] if w["mem_req"] else 0},
        })
    order = {"critical": 0, "warning": 1, "info": 2, "good": 3}
    out.sort(key=lambda x: (order[x["level"]], -x["savings"]["cpu_m"], x["namespace"], x["workload"]))
    return {"workloads": out, "hours": round((last - first) / 3_600_000 + BUCKET_MS / 3_600_000, 1) if first is not None else 0}


# --- API ---------------------------------------------------------------------------------------

def build_router(db_session, current, agent_get: Callable[[Cluster, str], Any], haproxy_view: Callable[..., List[Dict[str, Any]]],
                 haproxy_search: Callable[[Any, str], List[Dict[str, Any]]]) -> APIRouter:
    r = APIRouter(prefix="/api/hub")

    def cluster_for(s: Session, p: Any, cid: int, perm: str = "cluster.view") -> Cluster:
        c = s.get(Cluster, cid)
        if c is None:
            raise HTTPException(status_code=404, detail="cluster not found")
        if not p.can(perm, cid):
            raise HTTPException(status_code=403, detail=f"missing permission: {perm}")
        return c

    @r.get("/clusters/{cid}/history")
    def history(cid: int, scope: str = Query("cluster", pattern="^(cluster|node|pod)$"), name: str = "", namespace: str = "",
                since: int = 0, p=Depends(current), s: Session = Depends(db_session)) -> dict:
        cluster_for(s, p, cid)
        key = f"{namespace}/{name}" if scope == "pod" else name
        return {"points": series(s, cid, scope, since or int(time.time() * 1000) - KEEP_MS, key), "interval_s": BUCKET_MS // 1000,
                "fields": ["t", "cpu_avg_m", "memory_avg_bytes", "cpu_max_m", "memory_max_bytes"]}

    @r.get("/clusters/{cid}/rightsizing")
    def rightsize(cid: int, days: int = Query(7, ge=1, le=7), namespace: str = "", p=Depends(current),
                  s: Session = Depends(db_session)) -> dict:
        c = cluster_for(s, p, cid)
        try:
            pods = agent_get(c, "pods" + (f"?namespace={namespace}" if namespace else ""))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"cannot read pods from the agent: {e}")
        since = int(time.time() * 1000) - days * 86_400_000
        rows = s.execute(select(K8sSample.t, K8sSample.data).where(K8sSample.cluster_id == cid, K8sSample.t > since)
                         .order_by(K8sSample.t)).all()
        return rightsizing(pods, [(t, d) for t, d in rows])

    @r.get("/search")
    def search(q: str = Query(..., min_length=2, max_length=100), p=Depends(current), s: Session = Depends(db_session)) -> dict:
        clusters = [c for c in s.execute(select(Cluster).order_by(Cluster.name)).scalars() if p.can("cluster.view", c.id)]

        def one(c: Cluster) -> Tuple[Cluster, Any]:
            try:
                return c, agent_get(c, f"search?q={q}")
            except Exception as e:
                return c, e
        results, errors = [], []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for c, found in pool.map(one, clusters):
                if isinstance(found, Exception):
                    errors.append({"cluster": c.name, "error": str(found)[:200]})
                    continue
                for x in found:
                    if x["kind"] == "Secret" and not p.can("secrets.view", c.id):
                        continue
                    results.append({**x, "system": "kubernetes", "cluster_id": c.id, "cluster": c.name})
        results += haproxy_search(p, q)
        return {"results": results[:300], "errors": errors}

    @r.get("/clusters/{cid}/services/{namespace}/{name}/haproxy")
    def service_haproxy(cid: int, namespace: str, name: str, p=Depends(current), s: Session = Depends(db_session)) -> list:
        c = cluster_for(s, p, cid)
        return haproxy_view(p, s, c, namespace, name)

    return r
