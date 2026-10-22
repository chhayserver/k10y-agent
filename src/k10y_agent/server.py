import argparse
import logging
import socket

import uvicorn

from .config import Settings
from .tls import ensure_self_signed


def main() -> None:
    parser = argparse.ArgumentParser(prog="k10y-agent", description="k10y agent / hub")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve"])
    parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    settings = Settings()
    app = "k10y_agent.hub.app:app" if settings.mode == "hub" else "k10y_agent.api:app"
    ssl = {}
    if settings.tls:
        cert, key = settings.tls_cert, settings.tls_key
        if not (cert and key):
            cert, key = ensure_self_signed(settings.tls_dir, socket.gethostname(), ["k10y-agent", "k10y-hub"])
        ssl = {"ssl_certfile": cert, "ssl_keyfile": key}
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level, **ssl)


if __name__ == "__main__":
    main()
