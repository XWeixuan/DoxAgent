"""Runtime O2/O3 maintenance with explicit scopes and copy-on-write publication."""

from __future__ import annotations

import re
from doxagent.running_mode import RunningMode

from datetime import date, datetime
from pathlib import Path
from typing import Any

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.service import EventLibraryService
from doxagent.semantic_clock import semantic_day
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.v2_control.repository import ControlError
from doxagent.workflows.codex_document3.service import build_document3_orchestrator
from doxagent.workflows.codex_event_library.remote_runner import RemoteEventLibraryInitializer

from .bounded_inputs import case_values
from .daily import RuntimeDeltaBatchAdapter
from .event_branch import branch_library
from .journal import LeaseLost, MaintenancePhaseFailure, RuntimeJournal, RuntimeStopping, digest
from .schema import (
    BadcaseRecord,
    O3MaintenanceFeedV2,
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
        self.o3_enabled = self.control.o3_maintenance_enabled()
        self.worker_factory = worker_factory
        self.o2_factory, self.o3_factory = o2_factory, o3_factory
        self.resource_gc_due = False

    async def _retain_infrastructure_failure(self, durable, job):
        from doxagent.control_io import write_io

        from .maintenance_recovery import transient_failure

        # The HTTP client raises for infrastructure exhaustion before returning
        # the job. Retain its confirmed terminal receipt so ReceiptWorker can
        # advance the dispatch generation after the task's cooldown.
        if (
            job.status == "failed"
            and job.execution_phase == "SETTLED"
            and not job.cleanup_error
            and transient_failure(job.error_code or "", job.error_message or "")
        ):
            await write_io(
                self.journal.path,
                self.journal.set,
                "worker_receipts",
                durable.last_identity,
                job.model_dump(mode="json"),
                retry=True,
            )

    def collect_resources(self, *, should_yield=None):
        from .owner_exit import drain,register_legacy
        self.resource_gc_due=False
        register_legacy(self.journal)
        report=drain(self.journal,self.settings)
        from .history_exit import collect
        report['history']=collect(self)
        report['next_seconds']=min(report['history'].get('next_seconds',900),60 if report['processed'] else 900)
        return report

    def _frame(self, task: dict[str, Any]) -> dict[str, Any]:
        current = self.control.active_revision(task["ticker"])
        if current is None:
            raise ValueError("maintenance requires active inputs")
        prior = task["receipt"].get("frame")
        from .normal_o2 import supplemental, SupplementalRetired
        if supplemental(task) and prior:
            return dict(prior)  # reconcile accepted work before applying the original Control CAS
        mode = RunningMode(current.get("running_mode", "FULL"))
        if prior and RunningMode(prior["base"].get("running_mode", "FULL")) is not mode:
            raise ValueError("maintenance running mode changed")
        roles = ("document1", "event_library") if mode is RunningMode.LIGHTWEIGHT else ("document1", "document2", "event_library", "document3")
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
        from .bounded_inputs import members, records

        include_o3 = mode is RunningMode.FULL and getattr(self, "o3_enabled", True)
        membership = members(self.runtime.repository, self.journal, task) if include_o3 else []
        selected = {item["case_id"] for item in membership}
        inputs = (
            {key: prior[key] for key in ("candidates", "trades", "badcases", "gaps")}
            if prior
            else records(self.runtime.repository, membership, ticker=task["ticker"], include_o3=mode is RunningMode.FULL and getattr(self, "o3_enabled", True))
        )
        if not prior:
            from .normal_o2 import freeze_candidates
            inputs['candidates'] = freeze_candidates(self.runtime.repository, self.journal, task, current)
        from .policy_consumption import consumption_feed

        with self.journal.transaction(write=False) as db:
            policy_consumption = consumption_feed(db, task["ticker"]) if mode is RunningMode.FULL and getattr(self, "o3_enabled", True) else []
        frame = {
            "policy_consumption": policy_consumption,
            "base": current,
            "run_id": "runtime-maintain-" + digest([task["id"], current["revision_id"]])[:24],
            **inputs,
            "case_ids": sorted(selected),
            "effect_versions": {item["case_id"]: item["effects"] for item in membership},
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
        self.journal.checkpoint(task, frame=frame, o2=None, o3=None, o2_pages=[], o2_page_input=None)
        return frame

    async def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
        from .normal_o2 import SupplementalRetired, supplemental
        if supplemental(task) and not task.get('receipt', {}).get('frame'):
            raise SupplementalRetired(task['id'])
        if task.get('receipt',{}).get('process_retired'):
            raise ValueError('TERMINAL_MAINTENANCE_INPUTS_RETIRED_NEW_RUN_REQUIRED')
        prior_frame = task["receipt"].get("frame")
        existing = self.control.active_revision(task["ticker"])
        if prior_frame:
            try:
                existing = self.control.revision(prior_frame["run_id"] + "-activation")
            except (KeyError, ValueError):
                pass
        if existing and existing.get("runtime_metadata", {}).get("maintenance_id") == task["id"]:
            self._admit(existing)
            self._consume(task, task["receipt"]["frame"], include_o3=existing.get("runtime_metadata", {}).get("o3_outcome") != "SKIPPED_BY_CONTROL")
            return {"revision_id": existing["revision_id"], "reconciled": True}
        o3_enabled = self.control.o3_maintenance_enabled()
        self.o3_enabled = o3_enabled
        frame = self._frame(task)
        run_id, base = frame["run_id"], frame["base"]
        light = base.get("running_mode", "FULL") == "LIGHTWEIGHT"
        o3_enabled = o3_enabled and not light
        frozen_model = frame["models"]["model"]
        override = self.settings.persistent_runtime_v2_maintenance_execution_model_override
        execution_model = override if frozen_model == "gpt-6-luna" and override else frozen_model
        cutoff = datetime.fromisoformat(task["receipt"].get("o2_freeze", {}).get("at", task["inputs"]["cutoff"]))
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
        if not o3_enabled:
            # A maintenance lease already serializes a ticker. Readers use an
            # explicit activation version, so append only changed revisions to
            # its existing repository instead of cloning the selected database.
            from doxagent.event_library.repository import EventLibraryRepository

            candidate_root = source_root
            repository = EventLibraryRepository(source_root / relative)
        else:
            repository = branch_library(
                source_root / relative, candidate_root / relative,
                task["ticker"], int(event_ref["version"]),
            )
            self.journal.set(
                "w3_library_lineage", str(candidate_root),
                self.journal.get("w3_library_lineage", str(source_root), str(source_root)),
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
        durable = ReceiptWorker(
            worker,
            self.journal,
            run_id,
            control_epoch=task["inputs"].get("control_epoch"),
            replace_failed_model=execution_model != frozen_model,
            recover_timeouts=True,
            recover_artifacts=True,
        )
        from .normal_o2 import supplemental
        durable.allow_new_dispatch = not supplemental(task)
        durable.on_update = lambda job: self._retain_infrastructure_failure(durable, job)
        reference_task = {**task, "id": run_id}
        # Freeze O2/O3 assets before dispatch and materialize only immutable snapshots.
        components = ("event_library", "document3", "open_event_atlas") if o3_enabled else ("event_library",)
        assets = self.journal.get("maintenance_assets", run_id)
        if assets is None:
            execution = frame.get("execution_bundle_id")
            execution_bundle = (
                self.runtime.execution_bundles.get(execution)
                if execution and self.runtime.execution_bundles
                else {}
            )
            published_assets = execution_bundle.get("assets", {})
            assets = {
                key: value
                for key, value in published_assets.items()
                if key.startswith(tuple(c + "/" for c in components))
            }
            for component in components:
                if execution_bundle.get("orchestration_revision", 1) >= 3:
                    if not any(key.startswith(component + "/") for key in assets):
                        raise ValueError(f"frozen maintenance assets missing: {component}")
                    continue
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
            if name.split("/", 1)[0] not in components:
                continue
            target = prompt_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_text(encoding="utf-8") != content:
                raise ValueError("immutable maintenance prompt content changed")
            target.write_text(content, encoding="utf-8")
        operation, o2 = "o2-execute", None
        try:
            from .reference_capture import freeze_before

            if o3_enabled:
                freeze_before(
                    self.journal,
                    reference_task,
                    EventLibraryViewCompiler(repository),
                    int(event_ref["version"]),
                )
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
                        model=execution_model,
                        model_provider=frame["models"]["provider"],
                        effort=frame["models"]["effort"],
                        timeout_seconds=self.settings.codex_node_timeout_seconds,
                    )
                )
                unique, signature_members = {}, {}
                for item in frame['candidates']:
                    signature = item['runtime_signature']
                    unique.setdefault(signature, item)
                    signature_members.setdefault(signature, []).append(
                        f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}")
                candidates = [ProvisionalFactDetail.model_validate(item) for item in unique.values()]
                pages = ([candidates[i:i + 128] for i in range(0, len(candidates), 128)] or [[]]) if task['receipt'].get('o2_freeze') else [candidates]
                page_receipts = list(task['receipt'].get('o2_pages', []))
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
                for page_index, page in enumerate(pages):
                    if page_index < len(page_receipts):
                        continue
                    page_id = run_id if len(pages) == 1 else f'{run_id}-page-{page_index:04d}'
                    page_input = task['receipt'].get('o2_page_input')
                    if not page_input or page_input['index'] != page_index:
                        page_input = dict(index=page_index, run_id=page_id,
                                          base_version=repository.published_version(task['ticker']))
                        self.journal.checkpoint(task, o2_page_input=page_input)
                    page_base = page_input['base_version']
                    snapshot = RuntimeDeltaBatchAdapter(self.runtime.repository).freeze(
                        ticker=task['ticker'], trading_date=day, candidates=page, as_of=cutoff)
                    snapshot = snapshot.model_copy(update={'snapshot_id': page_id, 'epoch_id': page_id})
                    _, publication, validation, _ = await o2.run(
                        snapshot=snapshot,
                        run_id=page_id + "-o2",
                        cutoff_at=cutoff,
                        export_dir=root / page_id / "export",
                        mode="INCREMENTAL",
                        upstream_context_manifest=upstream,
                    )
                    if validation is not None and str(getattr(validation, "status", "")) == "FAIL":
                        raise ValueError("O2 produced no usable publication")
                    operation = "o2-settlement"
                    version = (
                        publication.published_library_version
                        if publication
                        else int(page_base)
                    )
                    with repository._read() as db:
                        resolved = [
                            row[0]
                            for row in db.execute(
                                "SELECT i.runtime_atomic_id FROM delta_items i "
                                "JOIN delta_batches b ON b.batch_id=i.batch_id "
                                "WHERE b.source_snapshot_id=? AND i.status='RESOLVED'",
                                (snapshot.snapshot_id,),
                            )
                        ]
                    resolved = set(resolved)
                    resolved_signatures = {
                        item.runtime_signature for item in page
                        if f'runtime-v2-msg:{item.source_message_id}:{item.candidate_index}' in resolved}
                    resolved.update(identity for signature in resolved_signatures
                                    for identity in signature_members[signature])
                    unresolved = [
                        item.runtime_atomic_id
                        for item in snapshot.atomics
                        if item.runtime_atomic_id not in resolved
                    ]
                    if unresolved:
                        self.journal.gap(
                            "o2-pending:" + task["id"] + ":" + str(page_index),
                            task["ticker"],
                            "O2_UNRESOLVED_ATOMICS",
                            str(unresolved),
                        )
                    page_receipts.append(dict(version=version, resolved=sorted(set(resolved)), snapshot_id=page_id))
                    self.journal.checkpoint(task, o2_pages=page_receipts)
                self.journal.checkpoint(task,
                    resolved_candidates=sorted({identity for page in page_receipts for identity in page['resolved']}),
                    o2={'version': page_receipts[-1]['version'], 'root': str(candidate_root)})
            published = task["receipt"]["o2"]

            if o3_enabled:
                # Case threads no longer follow Event associations.
                compiler = EventLibraryViewCompiler(repository)
                from .reference_capture import actual_delta

                delta = actual_delta(
                    self.journal,
                    reference_task,
                    compiler,
                    int(event_ref["version"]),
                    published["version"],
                )
                feed = O3MaintenanceFeedV2(
                    policy_consumption=frame.get("policy_consumption", []),
                    ticker=task["ticker"],
                    trading_date=day,
                    reference_view_delta=delta,
                    trade_records=[TradeRecord.model_validate(item) for item in frame["trades"]],
                    badcase_records=[BadcaseRecord.model_validate(item) for item in frame["badcases"]],
                    w3_coverage_gaps=[
                        W3CoverageGapRecord.model_validate(item) for item in frame["gaps"]
                    ],
                    trade_candidates=list(case_values(self.journal, "candidates", frame["case_ids"])),
                )
                if not task["receipt"].get("o3"):
                    from .reference_capture import prepare

                    prepare(self.journal, reference_task, compiler, candidate_root, day, delta)
                    config = self.settings.model_copy(
                        update={
                            "event_library_root": str(candidate_root),
                            "codex_runtime_storage_mode": "sqlite",
                            "codex_model": execution_model,
                            "codex_model_provider": frame["models"]["provider"],
                            "codex_reasoning_effort": frame["models"]["effort"],
                            "codex_published_storage_url": None,
                            "codex_published_storage_secret_key": None,
                        }
                    )
                    from doxagent.workflows.codex_document3.repository import (
                        SQLiteDocument3PolicyRepository,
                    )

                    base_policy = SQLiteDocument3PolicyRepository(
                        config.codex_runtime_sqlite_path
                    ).get_version(task["ticker"], int(base["artifacts"]["document3"]["version"]))
                    orchestration = (
                        "v2.1" if base_policy and base_policy.schema_version == "document3.v3" else "v2"
                    )
                    o3 = (
                        self.o3_factory(config, durable)
                        if self.o3_factory
                        else build_document3_orchestrator(
                            config, worker=durable, orchestration_version=orchestration
                        )
                    )
                    if hasattr(o3, "_agent"):
                        o3._agent._prompt_root = prompt_root / "document3"
                    operation = "o3-maintain"
                    kwargs = dict(
                        ticker=task["ticker"],
                        event_library_version=published["version"],
                        base_policy_version=int(base["artifacts"]["document3"]["version"]),
                        run_id=run_id + "-o3",
                        maintenance_feed=feed,
                    )
                    if hasattr(o3, "state"):
                        from doxagent.workflows.codex_document3.assets_v21 import default_node_assets

                        # Snapshot paths, never mutate the original prompt/skill files.
                        o3.runner.assets = default_node_assets(
                            prompt_root / "document3", prompt_root / "open_event_atlas"
                        )
                        result = await o3.maintain(**kwargs, as_of=cutoff)
                        if str(result.status) not in {"NOOP", "DEGRADED", "FAILED"}:
                            from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
                            from doxagent.workflows.codex_document3.publication_v21 import (
                                finalize_candidate_v21,
                            )
                            from doxagent.workflows.codex_document3.repository import (
                                SQLiteDocument3PolicyRepository,
                            )

                            await finalize_candidate_v21(
                                run_id + "-o3",
                                published_storage=getattr(o3, "published_storage", None),
                                state=o3.state,
                                workspace=durable,
                                runtime_repository=SQLiteCodexRuntimeRepository(
                                    config.codex_runtime_sqlite_path
                                ),
                                policy_repository=SQLiteDocument3PolicyRepository(
                                    config.codex_runtime_sqlite_path
                                ),
                            )
                    else:
                        result = await o3.maintain(
                            **kwargs, cutoff_at=cutoff, candidate_publication=True
                        )
                    if str(result.status) in {"FAILED", "DEGRADED"}:
                        raise ValueError("O3 produced no usable maintenance output")
                    from .reference_capture import settle

                    settle(self.journal, run_id, "SUCCEEDED")
                    policy_ref = {"version": result.policy_set_version}
                    if str(result.status) != "NOOP":
                        policy_ref["run_id"] = run_id + "-o3"
                    self.journal.checkpoint(task, o3=policy_ref)
            elif not light:
                # Keep the exact accepted D3 release. Deferred is not O3 success.
                self.journal.checkpoint(task, o3_outcome="SKIPPED_BY_CONTROL")
                pending = self.journal.get("o3_pending", task["ticker"])
                if pending is None:
                    self.journal.set("o3_pending", task["ticker"], {
                        "since": self.journal.clock().isoformat(),
                        "base_event": base["artifacts"]["event_library"],
                        "base_policy": base["artifacts"]["document3"],
                        "pending": True,
                    })
            operation = "runtime-publish"
            current_visibility = self.journal.get("visibility", task["ticker"], {}) or {}
            visibility_day = max(
                semantic_day(cutoff).isoformat(),
                current_visibility.get("day") or "",
                (base.get("runtime_metadata") or {}).get("visibility_day") or "",
            )
            metadata = {
                "o3_outcome": "NOT_APPLICABLE" if light else "APPLIED" if o3_enabled else "SKIPPED_BY_CONTROL",
                "maintenance_id": task["id"],
                "control_epoch": task["inputs"].get("control_epoch"),
                "visibility_day": visibility_day,
                "frame_hash": digest(frame),
            }
            with self.journal.transaction() as db:
                self.journal.fence(db, task)
            revision = self.control.activate_runtime_bundle(
                ticker=task["ticker"],
                identity=run_id + "-activation",
                base=base,
                event_ref=published,
                policy_ref=None if light else task["receipt"]["o3"] if o3_enabled else base["artifacts"]["document3"],
                metadata=metadata,
            )
            self._admit(revision)
            if self.runtime.coordinator is not None:
                self.runtime.coordinator._public_next[task["ticker"]] = 0
            self.journal.set("visibility", task["ticker"], {"day": metadata["visibility_day"]})
            self._consume(task, frame, include_o3=o3_enabled)
            return {"revision_id": revision["revision_id"]}
        except Exception as exc:
            from doxagent.ticker_initialization.repository import InitializationError
            if supplemental(task) and isinstance(exc, InitializationError) and str(exc) == 'runtime maintenance superseded by activation':
                raise SupplementalRetired(task['id']) from exc
            if isinstance(exc, (LeaseLost, ControlError, RuntimeStopping, SupplementalRetired)) or (supplemental(task) and getattr(exc,'code',None)=='w3_dispatch_unknown'):
                raise
            from .reference_capture import settle

            settle(self.journal, run_id, "FAILED")
            # A confirmed successful worker receipt with unusable artifacts needs
            # a new bounded repair turn; uncertain/running jobs retain their key.
            if operation == "o2-execute":
                operation = getattr(o2, "operation", operation)
            identity = getattr(durable, "last_identity", None)
            receipt = self.journal.get("worker_receipts", identity) if identity else None
            if (
                operation in {"o2-execute", "o3-maintain"}
                and receipt
                and receipt.get("status") == "succeeded"
                and ("d3_o3_maintain" in str(identity) or not task["receipt"].get("o2"))
            ):
                durable.reject_output("maintenance artifacts did not validate")
            request = self.journal.get("worker_requests", identity, {}) if identity else {}
            phase = operation
            if operation == "o2-execute" and identity:
                phase = re.sub(r"-retry-\d+$", "", request.get("attempt_id", operation))
            code = (
                (receipt or {}).get("error_code")
                if operation in {"o2-execute", "o3-maintain"}
                else None
            )
            cause: BaseException | None = exc
            while cause is not None:
                if getattr(cause, "code", None) == "WORKER_INFRA_RECOVERY_EXHAUSTED":
                    code = "WORKER_INFRA_RECOVERY_EXHAUSTED"
                    break
                cause = cause.__cause__
            # Durable Worker identity handles transport reconciliation; uncertain
            # jobs and quarantined cleanup must not mint a new dispatch.
            exhausted = code == "WORKER_INFRA_RECOVERY_EXHAUSTED"
            from .maintenance_recovery import transient_failure

            infrastructure = transient_failure(code or type(exc).__name__, str(exc))
            raise MaintenancePhaseFailure(
                str(exc),
                phase=phase,
                code=code or type(exc).__name__,
                scope="infrastructure" if infrastructure else "phase",
                retryable=(not isinstance(exc, ValueError) and not exhausted) or infrastructure,
            ) from exc
        finally:
            if hasattr(worker, "aclose"):
                await worker.aclose()
            self.resource_gc_due=False

    def _admit(self, revision):
        if self.control.active_revision(revision["ticker"])["revision_id"] != revision["revision_id"]:
            return
        if revision.get("running_mode", "FULL") == "LIGHTWEIGHT":
            self.runtime.prepare_library_visibility(revision["ticker"])
            return
        from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository

        from .policy_consumption import admit_revision

        admit_revision(
            self.journal,
            revision=revision,
            policy_repository=SQLiteDocument3PolicyRepository(
                self.settings.codex_runtime_sqlite_path
            ),
        )

        prepare = getattr(self.runtime, "prepare_library_visibility", None)
        if prepare:
            prepare(revision["ticker"])

    def _consume(self, task: dict[str, Any], frame: dict[str, Any], *, include_o3=True) -> None:
        resolved = set(task['receipt'].get('resolved_candidates', []))
        groups = dict(
            candidate_keys=[
                f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                for item in frame["candidates"]
                if f"runtime-v2-msg:{item['source_message_id']}:{item['candidate_index']}"
                in resolved
            ],
            trade_record_ids=[item["trade_record_id"] for item in frame["trades"]] if include_o3 else [],
            badcase_ids=[item["badcase_id"] for item in frame["badcases"]] if include_o3 else [],
            w3_coverage_gap_ids=[item["coverage_gap_id"] for item in frame["gaps"]] if include_o3 else [],
        )

        # Activation is the authority; fact acknowledgements are idempotent.
        # Release the shared writer between small pages, including on recovery.
        maximum = max(map(len, groups.values()), default=0)
        for offset in range(0, maximum, 100):
            self.runtime.repository.mark_daily_records_processed(
                **{key:values[offset:offset + 100] for key,values in groups.items()})
