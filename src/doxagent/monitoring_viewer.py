"""Run the local DoxAgent Monitoring Message Bus viewer."""

from __future__ import annotations

import argparse

from doxagent.monitoring.viewer import run_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Defaults to localhost.")
    parser.add_argument("--port", type=int, default=8766, help="Bind port. Defaults to 8766.")
    args = parser.parse_args(argv)

    run_server(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
