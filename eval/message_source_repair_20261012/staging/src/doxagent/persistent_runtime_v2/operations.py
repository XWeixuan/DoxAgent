"""Local operational controls; no node/model calls on inspection or reload."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doxagent.settings import DoxAgentSettings

from .journal import RuntimeJournal, digest


def add_commands(sub: Any) -> None:
    for name in ("status", "list-gaps", "reconcile", "resume", "pause"):
        command = sub.add_parser(name)
        command.add_argument("--ticker", required=True)
        if name == "pause":
            command.add_argument("--scope", choices=["processing", "all"], default="processing")
    for name, argument in (
        ("inspect-case", "case"),
        ("inspect-maintenance", "run"),
        ("inspect-selection", "run"),
        ("inspect-trade-output", "intent"),
    ):
        sub.add_parser(name).add_argument("--" + argument, required=True)
    resume = sub.add_parser("resume-node")
    resume.add_argument("--execution", required=True)
    resume.add_argument("--reason", required=True)
    sub.add_parser("reload-prompts").add_argument("--manifest", type=Path, required=True)
    config = sub.add_parser("import-monitoring-config")
    config.add_argument("--ticker", required=True)
    config.add_argument("--file", type=Path, required=True)
    config.add_argument("--reason", required=True)
    source = sub.add_parser("patch-source")
    source.add_argument("--source", required=True)
    source.add_argument("--file", type=Path, required=True)
    source.add_argument("--reason", required=True)
    policy = sub.add_parser("import-policy-set")
    policy.add_argument("--ticker", required=True)
    policy.add_argument("--file", type=Path, required=True)
    policy.add_argument("--reason", required=True)
    override = sub.add_parser("calendar-override")
    override.add_argument("--date", required=True)
    override.add_argument("--session", choices=["open", "closed"], required=True)
    override.add_argument("--reason", required=True)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--backup", type=Path)


def migrate(path: Path, *, dry_run: bool, backup: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "target_version": 1,
        "dry_run": dry_run,
        "legacy_pending": [],
    }
    if path.exists():
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "orchestration_meta" in tables:
                version = db.execute(
                    "SELECT value FROM orchestration_meta WHERE key='version'"
                ).fetchone()
                if version and int(version[0]) != RuntimeJournal.VERSION:
                    raise ValueError("unsupported orchestration schema")
            if "runtime_v2_cases" in tables:
                result["legacy_pending"] = [
                    row[0]
                    for row in db.execute(
                        "SELECT case_id FROM runtime_v2_cases "
                        "WHERE status NOT IN ('COMPLETED','FAILED')"
                    )
                ]
            if not dry_run:
                if backup is None or backup.exists() or backup.resolve() == path.resolve():
                    raise ValueError("migration requires a new explicit backup path")
                backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(backup) as target:
                    db.backup(target)
    if not dry_run:
        from .repository import SQLitePersistentRuntimeV2Repository

        SQLitePersistentRuntimeV2Repository(path)
        journal = RuntimeJournal(path)
        for identity in result["legacy_pending"]:
            journal.gap(
                identity,
                "",
                "LEGACY_RECOVERY_REVIEW",
                "Legacy date and records preserved; "
                "missing immutable input needs explicit recovery",
            )
    return result


def run_command(args: argparse.Namespace, settings: DoxAgentSettings) -> Any:
    path = Path(settings.persistent_runtime_v2_sqlite_path)
    if args.command == "migrate":
        return migrate(path, dry_run=args.dry_run, backup=args.backup)
    journal = RuntimeJournal(path)
    ticker = getattr(args, "ticker", "").upper()
    if args.command in {"pause", "resume"}:
        journal.set("pause", ticker, args.scope if args.command == "pause" else None)
        return {"ticker": ticker, "pause": journal.get("pause", ticker)}
    if args.command == "list-gaps":
        return journal.gaps(ticker)
    if args.command == "reload-prompts":
        from .execution_bundle import ExecutionBundles

        return {"execution_bundle_id": ExecutionBundles(journal).load_manifest(args.manifest)}
    if args.command in {"inspect-maintenance", "inspect-selection"}:
        return journal.get_task(args.run)
    if args.command == "inspect-trade-output":
        return journal.get("trade_intents", args.intent)
    if args.command == "inspect-case":
        from .repository import SQLitePersistentRuntimeV2Repository

        repository = SQLitePersistentRuntimeV2Repository(path)
        case = repository.get_case(args.case)
        return (
            {
                "case": case.model_dump(mode="json"),
                "effects": [
                    value.model_dump(mode="json") for value in repository.list_effects(args.case)
                ],
                "turns": [
                    value.model_dump(mode="json") for value in repository.list_turns(args.case)
                ],
            }
            if case
            else None
        )
    if args.command == "resume-node":
        task = journal.get_task(args.execution)
        if task is None:
            return resume_effect(journal, args.execution, args.reason)
        if task["status"] != "FAILED" or not args.reason.strip():
            raise ValueError("only failed executions with a reason can be resumed")
        if task and task["kind"] == "CASE":
            from .repository import SQLitePersistentRuntimeV2Repository
            from .schema import RuntimeCaseStatus

            repository = SQLitePersistentRuntimeV2Repository(path)
            case = repository.get_case_by_source(task["inputs"]["source"]["source_message_id"])
            if case:
                if not case.frozen_inputs:
                    raise ValueError(
                        "legacy Case lacks immutable inputs; cannot safely invent recovery"
                    )
                repository.save_case(case.model_copy(update={"status": RuntimeCaseStatus.RUNNING}))
                # Preserve successful rounds; a new explicit budget applies only to failed rounds.
                journal.set("round_resume", case.case_id, task["generation"] + 1)
        journal.resume(args.execution, args.reason)
        return journal.get_task(args.execution)
    if args.command == "patch-source":
        from doxagent.message_bus_v2.repository import MessageBusV2Repository
        from doxagent.message_bus_v2.schema import UpdateActor
        from doxagent.message_bus_v2.service import MessageBusV2Service

        if not args.reason.strip():
            raise ValueError("source change reason required")
        body = json.loads(args.file.read_text(encoding="utf-8"))
        service = MessageBusV2Service(MessageBusV2Repository(settings.message_bus_v2_sqlite_path))
        updated = service.update_source(
            args.source,
            body["patch"],
            actor=UpdateActor.USER,
            reason=args.reason,
            binding_patches=body.get("binding_patches"),
        )
        return {
            "source_id": updated.source_id,
            "version": updated.version,
            "scope": "ALL_BINDINGS_OF_THIS_SOURCE",
        }
    if args.command == "calendar-override":
        from datetime import date

        day = date.fromisoformat(args.date)
        if not args.reason.strip():
            raise ValueError("calendar override reason required")
        value = {"is_session": args.session == "open", "reason": args.reason}
        journal.set("calendar_overrides", "XNYS:" + str(day), value)
        return value
    if args.command == "import-policy-set":
        from doxagent.ticker_initialization.operations import replace_artifact
        from doxagent.ticker_initialization.repository import InitializationRepository
        from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
        from doxagent.workflows.codex_document3.schema import PolicySet

        policy = PolicySet.model_validate_json(args.file.read_text(encoding="utf-8"))
        if policy.ticker != ticker or not args.reason.strip():
            raise ValueError("ticker and operator reason required")
        policy_repository = SQLiteDocument3PolicyRepository(settings.codex_runtime_sqlite_path)
        identity = "manual-policy-" + digest([policy.model_dump(mode="json"), args.reason])[:24]
        version = policy_repository.reserve_version(ticker, identity)
        policy = policy.model_copy(update={"policy_set_version": version})
        policy_repository.publish_candidate(policy, run_id=identity)
        run = replace_artifact(
            InitializationRepository(_control_path(settings)),
            ticker,
            role="document3",
            reference={"version": version},
            reason=args.reason,
        )
        return {"activation_run_id": run.initialization_id, "policy_set_version": version}
    if args.command == "import-monitoring-config":
        return import_monitoring(settings, ticker, args.file, args.reason)
    from datetime import timedelta

    from doxagent.semantic_clock import boundary, semantic_day

    from .calendar import MarketCalendar

    now = journal.clock()
    if args.command == "reconcile":
        # Scheduling only. Long-running configured service dispatches node work.
        from .coordinator import RuntimeCoordinator

        coordinator = RuntimeCoordinator(None, journal)
        try:
            coordinator.reconcile(ticker)
        finally:
            coordinator.close()
    tasks = journal.tasks(ticker=ticker)
    counts: dict[str, int] = {}
    for task in tasks:
        key = task["kind"] + ":" + task["status"]
        counts[key] = counts.get(key, 0) + 1
    calendar = MarketCalendar(journal)
    return {
        "ticker": ticker,
        "semantic_day": str(semantic_day(now)),
        "is_session": calendar.is_session(semantic_day(now)),
        "next_boundary": boundary(semantic_day(now) + timedelta(days=1)).isoformat(),
        "schedule": journal.get("schedule", ticker),
        "pause": journal.get("pause", ticker),
        "execution_bundle_id": journal.get("execution", "active"),
        "queues": counts,
        "tasks": tasks,
        "gaps": journal.gaps(ticker),
    }


def import_monitoring(
    settings: DoxAgentSettings, ticker: str, path: Path, reason: str
) -> dict[str, Any]:
    from doxagent.message_bus_v2.schema import SourceDefinition, TickerSourceBinding, UpdateActor
    from doxagent.ticker_initialization.configuration import CandidateConfiguration
    from doxagent.ticker_initialization.operations import replace_artifact
    from doxagent.ticker_initialization.repository import InitializationRepository

    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) - {"sources", "bindings"}:
        raise ValueError("only source definitions and ticker bindings may be imported")
    sources = [SourceDefinition.model_validate(item) for item in payload.get("sources", [])]
    bindings = [TickerSourceBinding.model_validate(item) for item in payload.get("bindings", [])]
    if not bindings or any(item.ticker != ticker for item in bindings):
        raise ValueError("import requires bindings belonging to exactly this ticker")
    identity = "manual-config-" + digest([ticker, payload, datetime.now(UTC).isoformat()])[:24]
    config = CandidateConfiguration(settings.message_bus_v2_sqlite_path, identity, ticker)
    config.prepare()
    for source in sources:
        existing = config.live.get_source(source.source_id)
        fields = {"version", "created_at", "updated_at", "updated_by", "updated_reason"}
        if existing and existing.model_dump(exclude=fields) != source.model_dump(exclude=fields):
            raise ValueError("shared source mutation requires patch-source or a new source ID")
        config.candidate.save_source(
            source.model_copy(update={"updated_by": UpdateActor.USER, "updated_reason": reason})
        )
    for binding in bindings:
        previous = config.candidate.get_binding(binding.binding_id, include_tombstoned=True)
        config.candidate.save_binding(
            binding.model_copy(
                update={
                    "version": (previous.version + 1) if previous else 1,
                    "updated_by": UpdateActor.USER,
                    "updated_reason": reason,
                }
            )
        )
    run = replace_artifact(
        InitializationRepository(_control_path(settings)),
        ticker,
        role="monitoring_configuration",
        reference={"initialization_id": identity},
        reason=reason,
    )
    return {
        "activation_run_id": run.initialization_id,
        "configuration_id": identity,
        "status": "QUEUED_FOR_EXISTING_ACTIVATION_WORKER",
    }


def _control_path(settings: DoxAgentSettings) -> str:
    if not settings.ticker_initialization_control_path:
        raise ValueError("managed runtime control path is required")
    return settings.ticker_initialization_control_path


def resume_effect(journal: RuntimeJournal, identity: str, reason: str) -> dict[str, Any]:
    from .journal import encode
    from .schema import RuntimeEffect, RuntimeEffectStatus

    if not reason.strip():
        raise ValueError("resume reason required")
    with journal.transaction() as db:
        row = db.execute(
            "SELECT payload_json FROM runtime_v2_effects WHERE effect_id=?", (identity,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown execution/effect")
        effect = RuntimeEffect.model_validate_json(row[0])
        if effect.status is not RuntimeEffectStatus.FAILED:
            raise ValueError("only failed effects may be resumed")
        generation_row = db.execute(
            "SELECT payload FROM runtime_values WHERE namespace='round_resume' AND key=?",
            (effect.case_id,),
        ).fetchone()
        generation = int(json.loads(generation_row[0])) + 1 if generation_row else 1
        db.execute(
            "INSERT INTO runtime_values VALUES('effect_resume',?,?)",
            (
                identity + ":" + str(generation),
                encode({"before": effect.model_dump(mode="json"), "reason": reason}),
            ),
        )
        db.execute(
            "INSERT INTO runtime_values VALUES('round_resume',?,?) "
            "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
            (effect.case_id, encode(generation)),
        )
        effect = effect.model_copy(
            update={
                "status": RuntimeEffectStatus.PENDING_RETRY,
                "attempt_count": 0,
                "available_at": journal.clock(),
                "updated_at": journal.clock(),
            }
        )
        db.execute(
            "UPDATE runtime_v2_effects SET status=?,attempt_count=0,available_at=?,"
            "payload_json=?,updated_at=? WHERE effect_id=?",
            (
                effect.status.value,
                effect.available_at.isoformat(),
                effect.model_dump_json(),
                effect.updated_at.isoformat(),
                identity,
            ),
        )
    return effect.model_dump(mode="json")
