"""TCP port-forward to a pod or a Service, carried over a WebSocket (one connection per WebSocket).

A Service is resolved to one of its ready pods and its target port, as `kubectl port-forward svc/...` does. The
bytes go through the Kubernetes API's pods/portforward, so nothing has to be exposed on the nodes.
"""

import asyncio
from typing import Any, Optional, Tuple

from kubernetes.stream import portforward as k8s_portforward


class ForwardError(Exception):
    pass


def _ready(pod: Any) -> bool:
    return (pod.status and pod.status.phase == "Running" and not pod.metadata.deletion_timestamp
            and any(c.type == "Ready" and c.status == "True" for c in (pod.status.conditions or [])))


def _container_port(pod: Any, name: str) -> Optional[int]:
    for c in pod.spec.containers or []:
        for p in c.ports or []:
            if p.name == name:
                return p.container_port
    return None


def resolve(core: Any, kind: str, namespace: str, name: str, port: int) -> Tuple[str, int]:
    """(pod name, pod port) to forward to."""
    if kind == "pod":
        pod = core.read_namespaced_pod(name, namespace)
        if not _ready(pod):
            raise ForwardError(f"pod {name} is not running and ready")
        return name, port
    svc = core.read_namespaced_service(name, namespace)
    sp = next((p for p in svc.spec.ports or [] if p.port == port), None)
    if sp is None:
        raise ForwardError(f"Service {name} has no port {port} (it has {', '.join(str(p.port) for p in svc.spec.ports or []) or 'none'})")
    if (sp.protocol or "TCP") != "TCP":
        raise ForwardError(f"port {port} is {sp.protocol}; only TCP can be forwarded")
    selector = ",".join(f"{k}={v}" for k, v in (svc.spec.selector or {}).items())
    if not selector:
        raise ForwardError(f"Service {name} has no pod selector")
    pods = [p for p in core.list_namespaced_pod(namespace, label_selector=selector).items if _ready(p)]
    if not pods:
        raise ForwardError(f"no ready pod behind Service {name}")
    pod = sorted(pods, key=lambda p: p.metadata.name)[0]
    target = sp.target_port if sp.target_port is not None else port
    if isinstance(target, str) and not target.isdigit():
        found = _container_port(pod, target)
        if found is None:
            raise ForwardError(f"pod {pod.metadata.name} has no port named {target}")
        return pod.metadata.name, found
    return pod.metadata.name, int(target)


def open_forward(core: Any, namespace: str, pod: str, port: int) -> Any:
    pf = k8s_portforward(core.connect_get_namespaced_pod_portforward, pod, namespace, ports=str(port))
    sock = pf.socket(port)
    sock.setblocking(True)
    return pf, sock


async def bridge(ws: Any, pf: Any, sock: Any, port: int) -> Tuple[int, int]:
    """Copy bytes both ways until either side closes. Returns (bytes to the pod, bytes from the pod)."""
    sent = received = 0

    async def to_pod() -> None:
        nonlocal sent
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if data:
                sent += len(data)
                await asyncio.to_thread(sock.sendall, data)

    async def from_pod() -> None:
        nonlocal received
        while True:
            data = await asyncio.to_thread(sock.recv, 65536)
            if not data:
                return
            received += len(data)
            await ws.send_bytes(data)

    tasks = [asyncio.create_task(to_pod()), asyncio.create_task(from_pod())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            sock.close()
        except OSError:
            pass
        err = pf.error(port)
        if err:
            raise ForwardError(err)
    return sent, received
