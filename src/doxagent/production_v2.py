"""Production V2 provisioning and operator commands; no alternate auth or data provider."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from doxagent.settings import DoxAgentSettings


def paths(settings):
    return {
        "research": Path(settings.codex_runtime_sqlite_path),
        "initialization": Path(settings.ticker_initialization_control_path),
        "bus": Path(settings.message_bus_v2_sqlite_path),
        "runtime": Path(settings.persistent_runtime_v2_sqlite_path),
        "scheduler": Path(settings.runtime_scheduler_sqlite_path),
        "usage": Path(settings.model_usage_sqlite_path),
        "o4": Path(settings.codex_monitoring_o4_sqlite_path),
        "read": Path(os.environ["DOXAGENT_V2_READ_SQLITE_PATH"]),
    }


def check(*, databases=True):
    required = (
        "DOXAGENT_V2_READ_SQLITE_PATH",
        "DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH",
        "DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH",
        "DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH",
        "DOXAGENT_RUNTIME_SCHEDULER_SQLITE_PATH",
        "DOXAGENT_CODEX_RUNTIME_SQLITE_PATH",
        "DOXAGENT_MODEL_USAGE_SQLITE_PATH",
        "DOXAGENT_CODEX_MONITORING_O4_SQLITE_PATH",
        "DOXAGENT_EVENT_LIBRARY_ROOT",
        "DOXAGENT_CODEX_WORKSPACE_ROOT",
        "DOXAGENT_DASHBOARD_SUPABASE_URL",
        "DOXAGENT_DASHBOARD_SUPABASE_PUBLISHABLE_KEY",
        "DOXAGENT_CODEX_WORKER_BEARER_TOKEN",
        "DOXAGENT_CODEX_CAPABILITY_SECRET",
        "DASHSCOPE_API_KEY",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing production settings: " + ", ".join(missing))
    settings = DoxAgentSettings(_env_file=None)
    for key in ("persistent_runtime_v2", "codex_runtime", "runtime_scheduler", "model_usage"):
        if getattr(settings, key + "_storage_mode") != "sqlite":
            raise ValueError(key + " must use the production SQLite topology")
    for key in (
        "persistent_runtime_v2_enabled",
        "persistent_runtime_v2_w3_enabled",
        "message_bus_v2_enabled",
        "codex_d1_v2_enabled",
        "codex_research_lanes_enabled",
        "codex_document3_enabled",
        "codex_monitoring_o4_enabled",
    ):
        if not getattr(settings, key):
            raise ValueError(key + " must be enabled")
    for key in ("DOXAGENT_CODEX_WORKER_BEARER_TOKEN", "DOXAGENT_CODEX_CAPABILITY_SECRET"):
        if len(os.environ[key]) < 24:
            raise ValueError(key + " must have at least 24 characters")
    for name, path in paths(settings).items():
        if not path.is_absolute() or ".tmp" in path.parts:
            raise ValueError(name + " requires an absolute production data path")
        if databases and not path.is_file():
            raise ValueError(name + " database must be provisioned before starting workers")
    if not os.environ["DOXAGENT_DASHBOARD_SUPABASE_URL"].startswith("https://"):
        raise ValueError("Supabase requires HTTPS")
    return settings


def migrate():
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.model_usage.repository import SQLiteModelUsageRepository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
    from doxagent.ticker_initialization.repository import InitializationRepository
    from doxagent.trade_execution.repository import ExecutionRepository
    from doxagent.trade_execution.worker import WriterLock
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.v2_read.cli import backup
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.repository import ReadStore
    from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
    from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository

    settings = check(databases=False)
    locations = paths(settings)
    for path in locations.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        settings.event_library_root,
        settings.codex_workspace_root,
        settings.message_bus_v2_adapter_root,
        settings.crawler_plane_root,
    ):
        Path(path).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = locations["read"].parent.parent / "backups" / stamp
    # Refuse migrations while managed writers still own these databases.
    with (
        WriterLock(locations["runtime"].with_suffix(".migration")),
        WriterLock(locations["runtime"]),
        WriterLock(Path(str(locations["runtime"]) + ".v2-control")),
        WriterLock(Path(str(locations["runtime"]) + ".v2-delivery")),
        WriterLock(Path(str(locations["scheduler"]) + ".v2-scheduler")),
        WriterLock(Path(str(locations["read"]) + ".projector")),
    ):
        for name, path in locations.items():
            if path.exists():
                backup(path, backup_root / (name + ".sqlite3"))
        SQLiteCodexRuntimeRepository(locations["research"])
        SQLiteDocument3PolicyRepository(locations["research"])
        InitializationRepository(locations["initialization"])
        bus_repository = MessageBusV2Repository(locations["bus"])
        MessageBusV2Service(bus_repository).bootstrap()
        SQLitePersistentRuntimeV2Repository(locations["runtime"])
        journal = RuntimeJournal(locations["runtime"])
        ControlRepository(journal).migrate()
        ExecutionRepository(journal)
        SQLiteRuntimeSchedulerRepository(locations["scheduler"])
        SQLiteModelUsageRepository(locations["usage"])
        MonitoringO4Repository(locations["o4"])
        ReadStore(locations["read"]).migrate()
        captured = {}
        for name in ("research", "initialization", "bus", "runtime"):
            source = SourceOutbox(locations[name], name)
            captured[name] = source.migrate()
            # Install live capture before bounded snapshots. Historical receipts
            # remain BACKFILL; this never fabricates first-admission evidence.
            for table in captured[name]:
                while source.backfill(table, limit=500):
                    pass
        print(
            json.dumps(
                {
                    "migrated": list(locations),
                    "capture_tables": captured,
                    "backup_directory": str(backup_root),
                }
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("migrate")
    binding = commands.add_parser("bind-profile")
    binding.add_argument(
        "--ticker",
        required=True,
        help="Ticker symbol, or * to install the mode's global default profile",
    )
    binding.add_argument("--mode", required=True, choices=["PAPER_TRADING", "LIVE_TRADING"])
    binding.add_argument("--revision", required=True)
    binding.add_argument("--expected-revision")
    binding.add_argument("--actor", required=True)
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
    elif args.command == "check":
        check()
        print(json.dumps({"configuration": "ready", "orders_submitted": 0}))
    else:
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal
        from doxagent.v2_control.repository import ControlRepository

        settings = check()
        ControlRepository(
            RuntimeJournal(settings.persistent_runtime_v2_sqlite_path, initialize=False)
        ).bind(
            args.ticker, args.mode, args.revision, expected=args.expected_revision, actor=args.actor
        )
        print(json.dumps({"bound": args.ticker, "mode": args.mode, "revision": args.revision}))


if __name__ == "__main__":
    main()
