"""Exit policy operations. No broker order command is exposed."""

import argparse
import json
import sqlite3
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings
from doxagent.trade_execution.cli import redact
from doxagent.trade_execution.repository import ExecutionRepository

from .repository import ExitRepository
from .schema import ExitPolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import-policy")
    load.add_argument("--file")
    bind = commands.add_parser("bind")
    bind.add_argument("--account", required=True)
    bind.add_argument("--environment", choices=["PAPER", "LIVE"], required=True)
    bind.add_argument("--revision", help="Omit to disable new admissions")
    bind.add_argument("--ticker", default="*")
    bind.add_argument("--reason", required=True)
    status = commands.add_parser("status")
    status.add_argument("--position")
    pause = commands.add_parser("pause")
    pause.add_argument("--position", required=True)
    pause.add_argument("--resume", action="store_true")
    pause.add_argument("--reason", required=True)
    args = parser.parse_args()
    path = Path(args.db or DoxAgentSettings().persistent_runtime_v2_sqlite_path)
    if args.command == "status":
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            output = (
                [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT payload FROM ts_positions"
                        + (" WHERE id=?" if args.position else " WHERE phase!='CLOSED'"),
                        (args.position,) if args.position else (),
                    )
                ]
                if "ts_positions" in tables
                else []
            )
    else:
        ExecutionRepository(journal := RuntimeJournal(path))
        repo = ExitRepository(journal)
        if args.command == "import-policy":
            policy = (
                ExitPolicy.model_validate_json(Path(args.file).read_text(encoding="utf8"))
                if args.file
                else ExitPolicy()
            )
            output = {"revision": repo.import_policy(policy)}
        elif args.command == "bind":
            repo.bind(
                args.account,
                args.environment,
                args.revision,
                ticker=args.ticker,
                reason=args.reason,
            )
            output = {"bound": args.revision, "scope": "NEW_EXECUTIONS_ONLY"}
        else:
            repo.pause(args.position, not args.resume, args.reason)
            output = {"position": args.position, "paused": not args.resume}
    print(json.dumps(redact(output), ensure_ascii=False))


if __name__ == "__main__":
    main()
