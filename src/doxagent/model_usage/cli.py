"""Maintenance commands for model usage cost-audit persistence."""

from __future__ import annotations

import argparse

from doxagent.model_usage.repository import copy_model_usage_sqlite_to_postgres
from doxagent.settings import DoxAgentSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m doxagent.model_usage.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser(
        "migrate-sqlite-to-postgres",
        help="Idempotently copy the legacy SQLite model usage events to Postgres.",
    )
    migrate.add_argument("--source", help="Legacy SQLite path; defaults to settings.")
    args = parser.parse_args()

    if args.command == "migrate-sqlite-to-postgres":
        settings = DoxAgentSettings()
        copied = copy_model_usage_sqlite_to_postgres(
            sqlite_path=args.source or settings.model_usage_sqlite_path,
            database_url=settings.require_database_url(),
        )
        print(f"Copied {copied} model usage event(s) to Postgres.")


if __name__ == "__main__":
    main()
