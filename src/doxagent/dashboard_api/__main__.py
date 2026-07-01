"""Run the DoxAgent Dashboard State API mock server."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m doxagent.dashboard_api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on code changes during local frontend development.",
    )
    args = parser.parse_args()
    uvicorn.run(
        "doxagent.dashboard_api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
