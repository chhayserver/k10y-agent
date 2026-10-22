import argparse

import uvicorn

from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="k10y-agent", description="k10y in-cluster agent")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve"])
    parser.parse_args()

    settings = Settings()
    uvicorn.run(
        "k10y_agent.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
