"""Start the independent API after explicit source and read-store migrations."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        "doxagent.api_v2.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
