"""Run the DoxAgent Dashboard State API mock server."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m doxagent.dashboard_api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload on code changes during local frontend development.",
    )
    parser.add_argument(
        "--mode",
        choices=["mock", "real"],
        default=None,
        help="Dashboard API backend mode. Defaults to DOXAGENT_DASHBOARD_API_MODE or mock.",
    )
    args = parser.parse_args()
    if args.mode:
        os.environ["DOXAGENT_DASHBOARD_API_MODE"] = args.mode
    import uvicorn

    if args.reload:
        uvicorn.run(
            "doxagent.dashboard_api.app:app",
            host=args.host,
            port=args.port,
            reload=True,
        )
        return

    from doxagent.dashboard_api.app import create_app

    uvicorn.run(create_app(mode=args.mode), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
