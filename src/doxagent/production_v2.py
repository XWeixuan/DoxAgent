"""Production V2 provisioning and operator commands; no alternate auth or data provider."""

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from doxagent.settings import DoxAgentSettings


def paths(settings):
    read=Path(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
    if read.is_absolute() and (read.suffix=='.json' or read.is_symlink()):
        from doxagent.v2_read.repository import ReadStore
        read=ReadStore(read).path
    return {
        "research": Path(settings.codex_runtime_sqlite_path),
        "initialization": Path(settings.ticker_initialization_control_path),
        "bus": Path(settings.message_bus_v2_sqlite_path),
        "runtime": Path(settings.persistent_runtime_v2_sqlite_path),
        "scheduler": Path(settings.runtime_scheduler_sqlite_path),
        "usage": Path(settings.model_usage_sqlite_path),
        "o4": Path(settings.codex_monitoring_o4_sqlite_path),
        "read": read,
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


def migrate(*, resume_backup=None):
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
    # Explicit release schema marker. Ordinary restarts do not instantiate mutating
    # repositories, backfill sources, or take another full database backup.
    import sqlite3
    from doxagent.v2_read.maintenance import atomic_json
    logical_read=Path(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
    marker = logical_read.parent / "production-schema.json"
    expected = {"version": "20260921-db-v4", "paths": {key: str(value) for key, value in locations.items()}}
    expected['paths']['read']=str(logical_read)  # Stable alias, not an expiring generation path.
    previous=json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else None
    compatible=previous==expected
    if previous and previous.get('version')==expected['version']:
        old_paths=previous.get('paths',{})
        compatible=all(old_paths.get(k)==v for k,v in expected['paths'].items() if k!='read')
        if compatible:
            try:compatible=ReadStore(old_paths['read']).path==locations['read'].resolve()
            except (OSError,ValueError,KeyError):compatible=False
    if compatible and all(path.is_file() for path in locations.values()):
        with sqlite3.connect(locations["read"].resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT version FROM schema_meta").fetchone()[0] == ReadStore.VERSION:
                from doxagent.v2_read.artifact_registry import upgrade_native_trigger_conflicts
                for name in ("read", "research", "initialization", "bus", "runtime"):
                    with sqlite3.connect(locations[name], timeout=10) as native:
                        native.execute("BEGIN IMMEDIATE")
                        upgrade_native_trigger_conflicts(native)
                        if name == "read":
                            from doxagent.v2_read.repository import migrate_case_evidence_indexes
                            migrate_case_evidence_indexes(native)
                atomic_json(marker,expected)
                print(json.dumps({"migrated": [], "schema_current": True}))
                return
    if locations['read'].is_file():
        with sqlite3.connect(locations['read'].resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_meta'").fetchone():
                version=db.execute('SELECT version FROM schema_meta').fetchone()[0]
                if version<ReadStore.VERSION and db.execute('SELECT 1 FROM objects LIMIT 1').fetchone():
                    raise ValueError('populated legacy Read requires serving-generation before source migration backups')
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_parent = logical_read.parent.parent / "backups"
    backup_root = Path(resume_backup).resolve(strict=True) if resume_backup else backup_parent / stamp
    if resume_backup and (backup_root.parent != backup_parent.resolve() or not backup_root.is_dir()):
        raise ValueError("resume backup must be an existing production backup directory")
    if not resume_backup:
        from doxagent.v2_read.backup_retention import estimate_migration_backup
        estimated = estimate_migration_backup({name: path for name, path in locations.items()
                                              if name in {"research", "initialization", "bus", "runtime", "read"}})
        reserve = int(os.environ.get("DOXAGENT_BACKUP_RESERVE_BYTES", str(2 * 1024**3)))
        if shutil.disk_usage(locations["read"].parent).free < estimated + reserve:
            raise RuntimeError("insufficient storage for migration backup plus reserve; archive backups or expand disk")
    # Refuse migrations while managed writers still own these databases.
    with (
        WriterLock(locations["runtime"].with_suffix(".migration")),
        WriterLock(locations["runtime"]),
        WriterLock(Path(str(locations["runtime"]) + ".v2-control")),
        WriterLock(Path(str(locations["runtime"]) + ".v2-delivery")),
        WriterLock(Path(str(locations["scheduler"]) + ".v2-scheduler")),
        WriterLock(Path(str(locations["read"]) + ".projector")),
    ):
        affected = {"research", "initialization", "bus", "runtime", "read"}
        backed_up = {}
        for name, path in locations.items():
            if path.exists() and name in affected:
                backed_up[name] = path
                target = backup_root / (name + ".sqlite3")
                if resume_backup:
                    if not target.is_file():
                        raise ValueError("resume backup is missing " + name)
                    # Explicit operator resume while all writers remain fenced. The backup API
                    # already committed the copy; do not repeat a multi-GB full-index check.
                    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as live, sqlite3.connect(target.as_uri()+"?mode=ro",uri=True) as saved:
                        if live.execute("PRAGMA page_count").fetchone() != saved.execute("PRAGMA page_count").fetchone():
                            raise ValueError("resume backup page count changed: " + name)
                        schema = "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                        if live.execute(schema).fetchall() != saved.execute(schema).fetchall():
                            raise ValueError("resume backup schema changed: " + name)
                        table = "commits" if name == "read" else "v2_source_outbox"
                        if live.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():
                            sql = "SELECT coalesce(max(seq),0) FROM " + table
                            if live.execute(sql).fetchone() != saved.execute(sql).fetchone():
                                raise ValueError("resume backup source watermark changed: " + name)
                else:
                    backup(path, target)
                print(json.dumps({"backup_verified":name}),flush=True)
        SQLiteCodexRuntimeRepository(locations["research"])
        SQLiteDocument3PolicyRepository(locations["research"])
        InitializationRepository(locations["initialization"])
        bus_repository = MessageBusV2Repository(locations["bus"])
        MessageBusV2Service(bus_repository).bootstrap()
        SQLitePersistentRuntimeV2Repository(locations["runtime"])
        journal = RuntimeJournal(locations["runtime"])
        ControlRepository(journal).migrate()
        ExecutionRepository(journal)
        if not locations["scheduler"].exists():
            SQLiteRuntimeSchedulerRepository(locations["scheduler"])
        if not locations["usage"].exists():
            SQLiteModelUsageRepository(locations["usage"])
        if not locations["o4"].exists():
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
        atomic_json(marker, expected)
        print(
            json.dumps(
                {
                    "migrated": list(locations),
                    "capture_tables": captured,
                    "backup_directory": str(backup_root),
                }
            )
        )
    # Compress/retire immutable copies after releasing the business writer fences.
    from doxagent.v2_read.backup_retention import complete_migration_backup
    if backed_up:
        print(json.dumps({"backup_retention": complete_migration_backup(backup_root, backed_up)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("migrate").add_argument("--resume-backup", type=Path)
    commands.add_parser("upgrade-v21").add_argument("--backup-manifest", type=Path, required=True)
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
        migrate(resume_backup=args.resume_backup)
    elif args.command == "upgrade-v21":
        from doxagent.production_v21 import upgrade_v21

        print(json.dumps(upgrade_v21(paths(check()), args.backup_manifest)))
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
