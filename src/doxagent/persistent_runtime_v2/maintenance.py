"""Runtime O2/O3 maintenance with explicit scopes and copy-on-write publication."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import ReferenceViewDeltaSnapshot
from doxagent.event_library.service import EventLibraryService
from doxagent.semantic_clock import semantic_day
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer

from .daily import RuntimeDeltaBatchAdapter
from .event_branch import branch_library
from .journal import RuntimeJournal, digest
from .schema import (
    BadcaseRecord,
    O3MaintenanceFeed,
    ProvisionalFactDetail,
    TradeRecord,
    W3CoverageGapRecord,
)
from .service import PersistentRuntimeV2Service
from .worker_receipts import ReceiptWorker


class RuntimeMaintenance:
    def __init__(
        self,
        settings: DoxAgentSettings,
        runtime: PersistentRuntimeV2Service,
        journal: RuntimeJournal,
        *,
        worker_factory: Any = None,
        o2_factory: Any = None,
        o3_factory: Any = None,
    ) -> None:
        self.settings, self.runtime, self.journal = settings, runtime, journal
        assert settings.ticker_initialization_control_path
        self.control = InitializationRepository(settings.ticker_initialization_control_path)
        self.worker_factory = worker_factory
        self.o2_factory, self.o3_factory = o2_factory, o3_factory

    def _frame(self, task: dict[str, Any]) -> dict[str, Any]:
        current = self.control.active_revision(task["ticker"])
        if current is None:
            raise ValueError("maintenance requires active inputs")
        prior = task["receipt"].get("frame")
        roles = ("document1", "document2", "event_library", "document3")
        if prior and all(
            prior["base"]["artifacts"][role] == current["artifacts"][role] for role in roles
        ):
            return dict(prior)
        if prior:
            self.journal.gap(
                f"superseded:{task['id']}:{prior['run_id']}",
                task["ticker"],
                "SUPERSEDED",
                "Rebase maintenance records onto manual/current activation",
            )
        day = date.fromisoformat(task["inputs"]["day"])
        cases = self.runtime.repository.list_cases(task["ticker"])
        if task["inputs"].get("scope") == "SWEEP":
            selected = {
                case.case_id for case in cases if case.sweep_id == task["inputs"]["sweep_id"]
            }
        else:
            selected = {
                case.case_id for case in cases if case.trading_date == day and case.sweep_id is None
            }
        if task["inputs"].get("case_ids"):
            selected = set(task["inputs"]["case_ids"])
        dates = {case.trading_date for case in cases if case.case_id in selected}
        sources = {case.source.source_message_id for case in cases if case.case_id in selected}
        candidates: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        badcases: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for value in sorted(dates):
            candidates.extend(
                item.model_dump(mode="json")
                for item in self.runtime.repository.list_daily_candidates(task["ticker"], value)
                if item.source_message_id in sources
            )
            trades.extend(
                item.model_dump(mode="json")
                for item in self.runtime.repository.list_daily_trades(task["ticker"], value)
                if item.case_id in selected
            )
            badcases.extend(
                item.model_dump(mode="json")
                for item in self.runtime.repository.list_daily_badcases(task["ticker"], value)
                if item.case_id in selected
            )
            gaps.extend(
                item.model_dump(mode="json")
                for item in self.runtime.repository.list_daily_w3_coverage_gaps(
                    task["ticker"], value
                )
                if item.case_id in selected
            )
        frame = {
            "base": current,
            "run_id": "runtime-maintain-" + digest([task["id"], current["revision_id"]])[:24],
            "candidates": candidates,
            "trades": trades,
            "badcases": badcases,
            "gaps": gaps,
            "case_ids": sorted(selected),
            "effect_versions": {
                identity: [
                    (effect.effect_id, effect.attempt_count, effect.status.value)
                    for effect in self.runtime.repository.list_effects(identity)
                ]
                for identity in selected
            },
            "execution_bundle_id": prior.get("execution_bundle_id")
            if prior
            else self.journal.get("execution", "active"),
        }
        frame["models"] = (
            prior.get("models")
            if prior
            else {
                "model": self.settings.codex_model,
                "provider": self.settings.codex_model_provider,
                "effort": self.settings.codex_reasoning_effort,
            }
        )
        if prior:
            for key in ("candidates", "trades", "badcases", "gaps", "case_ids", "effect_versions"):
                frame[key] = prior[key]
        for identity in frame["case_ids"]:
            self.journal.set("case_maintenance_owner", identity, task["id"])
        self.journal.checkpoint(task, frame=frame, o2=None, o3=None)
        return frame

    async def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
        prior_frame = task["receipt"].get("frame")
        existing = self.control.active_revision(task["ticker"])
        if prior_frame:
            try:
                existing = self.control.revision(prior_frame["run_id"] + "-activation")
            except (KeyError, ValueError):
                pass
        if existing and existing.get("runtime_metadata", {}).get("maintenance_id") == task["id"]:
            self._consume(task, task["receipt"]["frame"])
            return {"revision_id": existing["revision_id"], "reconciled": True}
        frame = self._frame(task)
        run_id, base = frame["run_id"], frame["base"]
        cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
        day = date.fromisoformat(task["inputs"]["day"])
        root = (
            Path(self.settings.persistent_runtime_v2_sqlite_path).resolve().parent
            / "runtime_artifacts"
        )
        candidate_root = root / run_id / "events"
        event_ref = base["artifacts"]["event_library"]
        source_root = Path(event_ref.get("root") or self.settings.event_library_root or "")
        relative = (
            Path(self.settings.event_library_market.upper())
            / task["ticker"]
            / "event_library.sqlite3"
        )
        repository = branch_library(
            source_root / relative,
            candidate_root / relative,
            task["ticker"],
            int(event_ref["version"]),
        )
        worker = (
            self.worker_factory()
            if self.worker_factory
            else HttpCodexWorkerClient(
                self.settings.codex_worker_base_url,
                self.settings.codex_worker_bearer_token or "",
                capability_secret=self.settings.codex_capability_secret,
            )
        )
        durable = ReceiptWorker(worker, self.journal, run_id)
        # Freeze O2/O3 assets before dispatch and materialize only immutable snapshots.
        assets = self.journal.get("maintenance_assets", run_id)
        if assets is None:
            execution = frame.get("execution_bundle_id")
            published_assets = (
                self.runtime.execution_bundles.get(execution)["assets"]
                if execution and self.runtime.execution_bundles
                else {}
            )
            assets = {
                key: value
                for key, value in published_assets.items()
                if key.startswith(("event_library/", "document3/"))
            }
            for component in ("event_library", "document3"):
                source = Path("prompts/codex_v2") / component
                for path in source.rglob("*"):
                    if (
                        path.is_file()
                        and f"{component}/{path.relative_to(source).as_posix()}" not in assets
                    ):
                        assets[f"{component}/{path.relative_to(source).as_posix()}"] = (
                            path.read_text(encoding="utf-8")
                        )
            self.journal.set("maintenance_assets", run_id, assets)
        prompt_root = root / run_id / "prompts"
        for name, content in assets.items():
            target = prompt_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_text(encoding="utf-8") != content:
                raise ValueError("immutable maintenance prompt content changed")
            target.write_text(content, encoding="utf-8")
        try:
            if not task["receipt"].get("o2"):
                o2 = (
                    self.o2_factory(repository, durable)
                    if self.o2_factory
                    else RemoteEventLibraryInitializer(
                        worker=durable,
                        workspace=worker,
                        service=EventLibraryService(repository),
                        local_workspace_root=self.settings.codex_workspace_root,
                        prompt_root=prompt_root / "event_library",
                        model=frame["models"]["model"],
                        model_provider=frame["models"]["provider"],
                        effort=frame["models"]["effort"],
                        timeout_seconds=self.settings.codex_node_timeout_seconds,
                    )
                )
                candidates = [
                    ProvisionalFactDetail.model_validate(item) for item in frame["candidates"]
                ]
                snapshot = RuntimeDeltaBatchAdapter(self.runtime.repository).freeze(
                    ticker=task["ticker"], trading_date=day, candidates=candidates, as_of=cutoff
                )
                snapshot = snapshot.model_copy(update={"snapshot_id": run_id, "epoch_id": run_id})
                upstream = None
                if self.o2_factory is None:
                    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository

                    bundle = SQLiteCodexRuntimeRepository(
                        self.settings.codex_runtime_sqlite_path
                    ).get_bundle(base["artifacts"]["document1"]["run_id"])
                    from doxagent.codex_runtime.schema import GlobalResearchBundle

                    if (
                        not isinstance(bundle, GlobalResearchBundle)
                        or not bundle.published_at
                        or not bundle.citation_manifest
                    ):
                        raise ValueError("pinned D1 research context unavailable for O2")
                    upstream = {
                        "d1_run_id": base["artifacts"]["document1"]["run_id"],
                        "d1_published_at": bundle.published_at.isoformat(),
                        "research_artifacts": {
                            role: bundle.reports[role].model_dump(mode="json")
                            for role in ("c1", "c3", "c5")
                        },
                        "citation_manifest": bundle.citation_manifest.model_dump(mode="json"),
                        "unified_as_of": cutoff.isoformat(),
                        "ticker": task["ticker"],
                    }
                _, publication, validation, _ = await o2.run(
                    snapshot=snapshot,
                    run_id=run_id + "-o2",
                    cutoff_at=cutoff,
                    export_dir=root / run_id / "export",
                    mode="INCREMENTAL",
                    upstream_context_manifest=upstream,
                )
                if validation is not None and str(getattr(validation, "status", "")) == "FAIL":
                    raise ValueError("O2 produced no usable publication")
                version = (
                    publication.published_library_version
                    if publication
                    else int(event_ref["version"])
                )
                with repository._read() as db:
                    resolved = [
                        row[0]
                        for row in db.execute(
                            "SELECT runtime_atomic_id FROM delta_items "
                            "WHERE runtime_scope=? AND status='RESOLVED'",
                            (snapshot.runtime_scope,),
                        )
                    ]
                resolved_signatures = {
                    item["runtime_signature"]
                    for item in frame["candidates"]
                    if f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                    in resolved
                }
                resolved.extend(
                    f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                    for item in frame["candidates"]
                    if item["runtime_signature"] in resolved_signatures
                )
                unresolved = [
                    item.runtime_atomic_id
                    for item in snapshot.atomics
                    if item.runtime_atomic_id not in resolved
                ]
                if unresolved:
                    self.journal.gap(
                        "o2-pending:" + task["id"],
                        task["ticker"],
                        "O2_UNRESOLVED_ATOMICS",
                        str(unresolved),
                    )
                self.journal.checkpoint(
                    task,
                    resolved_candidates=resolved,
                    o2={"version": version, "root": str(candidate_root)},
                )
            published = task["receipt"]["o2"]
            compiler = EventLibraryViewCompiler(repository)
            delta = (
                compiler.reference_view_delta(
                    task["ticker"],
                    from_version=int(event_ref["version"]),
                    to_version=published["version"],
                )
                if published["version"] != int(event_ref["version"])
                else ReferenceViewDeltaSnapshot(
                    ticker=task["ticker"],
                    from_library_version=published["version"],
                    to_library_version=published["version"],
                    reference_view_delta="",
                    removed_event_ids=[],
                )
            )
            feed = O3MaintenanceFeed(
                ticker=task["ticker"],
                trading_date=day,
                reference_view_delta=delta,
                trade_records=[TradeRecord.model_validate(item) for item in frame["trades"]],
                badcase_records=[BadcaseRecord.model_validate(item) for item in frame["badcases"]],
                w3_coverage_gaps=[
                    W3CoverageGapRecord.model_validate(item) for item in frame["gaps"]
                ],
                trade_candidates=[
                    item
                    for item in self.journal.values("candidates")
                    if item["case_id"] in frame["case_ids"]
                ],
            )
            if not task["receipt"].get("o3"):
                config = self.settings.model_copy(
                    update={
                        "event_library_root": str(candidate_root),
                        "codex_runtime_storage_mode": "sqlite",
                        "codex_model": frame["models"]["model"],
                        "codex_model_provider": frame["models"]["provider"],
                        "codex_reasoning_effort": frame["models"]["effort"],
                        "codex_published_storage_url": None,
                        "codex_published_storage_secret_key": None,
                    }
                )
                o3 = (
                    self.o3_factory(config, durable)
                    if self.o3_factory
                    else build_document3_orchestrator(config, worker=durable)
                )
                if hasattr(o3, "_agent"):
                    o3._agent._prompt_root = prompt_root / "document3"
                result = await o3.maintain(
                    ticker=task["ticker"],
                    event_library_version=published["version"],
                    base_policy_version=int(base["artifacts"]["document3"]["version"]),
                    run_id=run_id + "-o3",
                    cutoff_at=cutoff,
                    maintenance_feed=feed,
                    candidate_publication=True,
                )
                if str(result.status) in {"FAILED", "DEGRADED"}:
                    raise ValueError("O3 produced no usable maintenance output")
                self.journal.checkpoint(task, o3={"version": result.policy_set_version})
            metadata = {
                "maintenance_id": task["id"],
                "visibility_day": semantic_day(cutoff).isoformat(),
                "frame_hash": digest(frame),
            }
            with self.journal.transaction() as db:
                self.journal.fence(db, task)
            revision = self.control.activate_runtime_bundle(
                ticker=task["ticker"],
                identity=run_id + "-activation",
                base=base,
                event_ref=published,
                policy_ref=task["receipt"]["o3"],
                metadata=metadata,
            )
            self.journal.set("visibility", task["ticker"], {"day": metadata["visibility_day"]})
            self._consume(task, frame)
            return {"revision_id": revision["revision_id"]}
        except Exception:
            # A confirmed successful worker receipt with unusable artifacts needs
            # a new bounded repair turn; uncertain/running jobs retain their key.
            identity = getattr(durable, "last_identity", None)
            receipt = self.journal.get("worker_receipts", identity) if identity else None
            if (
                receipt
                and receipt.get("status") == "succeeded"
                and ("d3_o3_maintain" in str(identity) or not task["receipt"].get("o2"))
            ):
                durable.reject_output("maintenance artifacts did not validate")
            raise
        finally:
            if hasattr(worker, "aclose"):
                await worker.aclose()

    def _consume(self, task: dict[str, Any], frame: dict[str, Any]) -> None:
        self.runtime.repository.mark_daily_records_processed(
            candidate_keys=[
                f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                for item in frame["candidates"]
                if f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                in task["receipt"].get("resolved_candidates", [])
            ],
            trade_record_ids=[item["trade_record_id"] for item in frame["trades"]],
            badcase_ids=[item["badcase_id"] for item in frame["badcases"]],
            w3_coverage_gap_ids=[item["coverage_gap_id"] for item in frame["gaps"]],
        )
