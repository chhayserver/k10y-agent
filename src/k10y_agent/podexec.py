"""Interactive shell in a container (like kubectl exec -it), bridged to a WebSocket.

Messages from the client are JSON text frames: {"type": "input", "data": "..."} and
{"type": "resize", "cols": 120, "rows": 30}. Terminal output is sent back as text frames.
"""

import asyncio
import json
import queue
import threading
import time
from typing import Any, Optional, Tuple

from kubernetes.stream import stream

MAX_SECONDS = 2 * 60 * 60
SHELL = ["/bin/sh", "-c", "export TERM=xterm-256color; if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh; fi"]


def open_exec(core: Any, namespace: str, pod: str, container: Optional[str]) -> Any:
    kwargs = {"container": container} if container else {}
    return stream(core.connect_get_namespaced_pod_exec, pod, namespace, command=SHELL, stdin=True, stdout=True,
                  stderr=True, tty=True, _preload_content=False, **kwargs)


async def bridge(ws: Any, resp: Any) -> None:
    """Copy terminal output to the WebSocket and WebSocket input to the terminal until either side closes.

    Only the worker thread touches the Kubernetes stream; keystrokes and resizes reach it through a queue,
    so the event loop never blocks on the stream.
    """
    loop = asyncio.get_running_loop()
    out: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
    inbox: "queue.Queue[Tuple[int, str]]" = queue.Queue()
    stop = threading.Event()

    def emit(item: Optional[str]) -> None:
        try:
            loop.call_soon_threadsafe(out.put_nowait, item)
        except RuntimeError:  # event loop already closed: the client is gone
            pass

    def worker() -> None:
        deadline = time.monotonic() + MAX_SECONDS
        try:
            while resp.is_open() and not stop.is_set():
                if time.monotonic() > deadline:
                    emit("\r\n[session time limit reached]\r\n")
                    break
                while True:
                    try:
                        channel, data = inbox.get_nowait()
                    except queue.Empty:
                        break
                    resp.write_channel(channel, data)
                resp.update(timeout=0.02)
                for ch in (1, 2):
                    if resp.peek_channel(ch):
                        emit(resp.read_channel(ch))
        except Exception as e:  # connection dropped
            if not stop.is_set():
                emit(f"\r\n[connection closed: {e}]\r\n")
        finally:
            emit(None)

    threading.Thread(target=worker, name="exec", daemon=True).start()

    async def to_client() -> None:
        while True:
            data = await out.get()
            if data is None:
                return
            await ws.send_text(data)

    async def to_pod() -> None:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if msg.get("type") == "input" and isinstance(msg.get("data"), str):
                inbox.put((0, msg["data"]))                                   # stdin
            elif msg.get("type") == "resize":
                cols, rows = int(msg.get("cols") or 80), int(msg.get("rows") or 24)
                inbox.put((4, json.dumps({"Width": max(10, cols), "Height": max(5, rows)})))  # resize channel

    tasks = [asyncio.create_task(to_client()), asyncio.create_task(to_pod())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        try:
            resp.close()
        except Exception:
            pass
