"""Codex SDK W3 exception-plane context and one-Case turn runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
    CodexPersistentRuntimeAgentRole,
    CodexPersistentRuntimeNode,
    GlobalResearchBundle,
    PublishedDocument,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.control_io import read_io, write_io
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.w3_timing import measured, observed, span
from doxagent.workflows.codex_document2.schema import (
    Document2Bundle,
    Document2Document,
    Document2DocumentV21,
)
from doxagent.workflows.codex_document3.consumer_contracts import parse_document2
from doxagent.workflows.codex_document3.repository import Document3PolicyRepository
from doxagent.workflows.codex_document3.schema import PolicySet
from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3

from . import policy_consumption
from .schema import (
    RuntimeCase,
    W3CaseResult,
    W3CaseResultV3,
    W3ContextVersionPin,
    W3Mode,
    W3RouteCase,
    W3ThreadKind,
    W3ThreadSlot,
    parse_w3_result,
    strict_json_schema,
)
from .w3_models import execution_profile
from .w3_research_store import ENTRY, compact


def research_pin_key(case):
    from .journal import digest

    p = case.version_pin
    return digest(
        [
            case.ticker,
            p.document1_run_id,
            p.document2_run_id,
            p.event_library_root,
            p.event_library_version,
            p.policy_set_version,
            ENTRY,
        ]
    )


_EVENT_ID = re.compile(r"\bE[1-9][0-9]*\b")


@dataclass(frozen=True)
class W3ExecutionEnvironment:
    """Explicit execution capabilities; production callers retain their defaults."""

    mode: str = "PRODUCTION"
    web_search_mode: str = "live"
    data_mcp_enabled: bool = True

    def compatibility(self):
        return dict(
            mode=self.mode,
            web_search_mode=self.web_search_mode,
            read_only=True,
            data_mcp_enabled=self.data_mcp_enabled,
            allow_subagents=False,
        )

    @classmethod
    def offline(cls):
        return cls(mode="OFFLINE_REPLAY", web_search_mode="disabled", data_mcp_enabled=False)


class W3Error(RuntimeError):
    def __init__(self, code: str, message: str, *, invalid_thread: bool = False, step=None) -> None:
        super().__init__(message)
        self.code = code
        self.invalid_thread = invalid_thread
        self.w3_step = step


class W3PreparedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_pin: W3ContextVersionPin
    document1: str = Field(min_length=1)
    document2: Document2DocumentV21 | Document2Document
    policy_set: PolicySetV3 | PolicySet
    reference_view: str = Field(min_length=1)
    document2_publication_state: str
    research_identity: str | None = None
    reference_identity: str | None = None
    reference_event_ids: frozenset[str] | None = None
    original_document2: str | None = None
    original_policy_set: str | None = None
    context_cutoffs: dict[str, str | None] = Field(default_factory=dict)
    policy_origins: dict[str, list[str]] = Field(default_factory=dict)


class W3ContextProvider(Protocol):
    async def load(self, case: RuntimeCase) -> W3PreparedContext: ...


class PolicyConsumptionReader(Protocol):
    def list_consumed_policy_revisions(self, ticker: str) -> set[tuple[str, str]]: ...


class W3Agent(Protocol):
    def run(
        self,
        *,
        case: RuntimeCase,
        w3_case: W3RouteCase,
        slot: W3ThreadSlot,
    ) -> tuple[W3CaseResult, str | None, W3ContextVersionPin]: ...

    def close(self) -> None: ...


class PublishedW3ContextProvider:
    """Load the current D2, its exact D1 provenance, and version-pinned O2/O3 state."""

    def __init__(
        self,
        *,
        runtime_repository: CodexRuntimeRepository,
        policy_repository: Document3PolicyRepository,
        event_library_reader: PublishedEventLibraryReader,
        policy_consumption_reader: PolicyConsumptionReader | None = None,
        published_storage: PublishedDocumentStorage | None = None,
        context_cache_size: int = 16,
    ) -> None:
        if context_cache_size < 1:
            raise ValueError("W3 context cache size must be positive")
        self._runtime = runtime_repository
        self._policies = policy_repository
        self._events = event_library_reader
        self._policy_consumption = policy_consumption_reader
        self._storage = published_storage
        self._context_cache_size = context_cache_size
        self._context_cache: OrderedDict[tuple[str, str, str, int, int], W3PreparedContext] = (
            OrderedDict()
        )
        self._context_cache_lock = threading.RLock()
        self._load_locks = {}
        self._research_bases = OrderedDict()
        self._reference_cache = OrderedDict()

    @observed("context.published_load")
    async def load(self, case: RuntimeCase) -> W3PreparedContext:
        pin = case.version_pin
        key = (
            case.ticker,
            pin.document1_run_id,
            pin.document2_run_id,
            pin.event_library_root,
            pin.policy_set_version,
        )
        async with self._load_locks.setdefault(key, asyncio.Lock()):
            return await self._load(case)

    def _reference(self, case):
        key = (
            case.version_pin.event_library_root,
            case.ticker,
            case.version_pin.event_library_version,
        )
        if key not in self._reference_cache:
            reader = (
                PublishedEventLibraryReader(case.version_pin.event_library_root)
                if case.version_pin.event_library_root
                else self._events
            )
            view = reader.reference_view(
                case.ticker, version=case.version_pin.event_library_version
            )
            if view is None or not view.reference_view.strip():
                raise W3Error("w3_reference_view_unavailable", "Pinned Reference View unavailable")
            self._reference_cache[key] = view
            while len(self._reference_cache) > self._context_cache_size:
                self._reference_cache.popitem(last=False)
        return self._reference_cache[key]

    async def _load(self, case: RuntimeCase) -> W3PreparedContext:
        if case.version_pin.document2_run_id:
            pinned = self._runtime.get_bundle(case.version_pin.document2_run_id)
            if not isinstance(pinned, Document2Bundle) or pinned.ticker.upper() != case.ticker:
                raise W3Error("w3_document2_unavailable", "Pinned Published D2 is unavailable")
            d2_bundle = pinned
        else:
            d2_bundle = self._current_document2(case.ticker)
        if d2_bundle.handoff is None:
            raise W3Error("w3_document2_unavailable", "Current Published D2 has no handoff")
        cache_key = (
            case.ticker,
            d2_bundle.run_id,
            (case.version_pin.document1_run_id or d2_bundle.source_global_run_id)
            + ":"
            + (case.version_pin.event_library_root or ""),
            case.version_pin.event_library_version,
            case.version_pin.policy_set_version,
        )
        cached = self._cached_context(cache_key)
        with span("context.cache_lookup", hit=cached is not None):
            pass
        if cached is not None:
            return self._without_consumed_policies(cached)
        base_key = (*cache_key[:3], cache_key[4])
        base = self._research_bases.get(base_key)
        if base is not None:
            from .journal import digest

            reference = self._reference(case)
            prepared = base.model_copy(
                update={
                    "version_pin": base.version_pin.model_copy(
                        update={"event_library_version": reference.version}
                    ),
                    "reference_view": reference.reference_view,
                    "reference_identity": digest(
                        ["w3-reference-v1", reference.version, reference.reference_view]
                    ),
                    "reference_event_ids": frozenset(_EVENT_ID.findall(reference.reference_view)),
                }
            )
            self._cache_context(cache_key, prepared)
            return self._without_consumed_policies(prepared)
        d2_published = self._runtime.get_published_document(
            d2_bundle.run_id,
            d2_bundle.handoff.document2_artifact_id,
        )
        if d2_published is None:
            raise W3Error("w3_document2_unavailable", "Current Published D2 body is unavailable")
        d2_text = (await self._read_published(d2_published)).decode("utf-8")
        try:
            document2 = parse_document2(d2_text)
        except ValidationError as exc:
            raise W3Error("w3_document2_invalid", "Current Published D2 is invalid") from exc
        if document2.ticker.upper() != case.ticker:
            raise W3Error("w3_document2_ticker_mismatch", "Published D2 ticker mismatch")

        d1_bundle = self._runtime.get_bundle(
            case.version_pin.document1_run_id or document2.source_global_run_id
        )
        if not isinstance(d1_bundle, GlobalResearchBundle) or d1_bundle.handoff is None:
            raise W3Error(
                "w3_document1_unavailable",
                "D2 source_global_run_id does not resolve to Published Global Research",
            )
        if d1_bundle.ticker.upper() != case.ticker:
            raise W3Error("w3_document1_ticker_mismatch", "Pinned D1 ticker mismatch")
        d1_published = self._runtime.get_published_document(
            d1_bundle.run_id,
            d1_bundle.handoff.document_artifact_id,
        )
        if d1_published is None:
            raise W3Error("w3_document1_unavailable", "Published D1 body is unavailable")
        document1 = (await self._read_published(d1_published)).decode("utf-8")

        policy_set = self._policies.get_version(
            case.ticker,
            case.version_pin.policy_set_version,
        )
        if policy_set is None:
            raise W3Error(
                "w3_policy_set_unavailable",
                "Version-pinned full PolicySet is unavailable",
            )
        reference = self._reference(case)

        origins = {}
        original_policy = policy_set.model_dump_json()
        policy_path = getattr(self._policies, "path", None)
        if policy_path:
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(str(policy_path))) as db:
                table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='codex_document3_v21_staged_sets'"
                ).fetchone()
                row = (
                    db.execute(
                        "SELECT run_id,receipt FROM codex_document3_v21_staged_sets "
                        "WHERE ticker=? AND version=?",
                        (case.ticker, policy_set.policy_set_version),
                    ).fetchone()
                    if table
                    else None
                )
            if row:
                receipt = json.loads(row[1])
                origins = receipt.get("policy_origins", {})
                bundle = self._runtime.get_bundle(row[0])
                handoff = getattr(bundle, "handoff", None)
                artifact = getattr(handoff, "published_artifact", None)
                if artifact:
                    published = self._runtime.get_published_document(row[0], artifact.artifact_id)
                    if published:
                        original_policy = (await self._read_published(published)).decode("utf-8")
        from .journal import digest

        prepared = W3PreparedContext(
            reference_identity=digest(
                ["w3-reference-v1", reference.version, reference.reference_view]
            ),
            reference_event_ids=frozenset(_EVENT_ID.findall(reference.reference_view)),
            research_identity=digest(
                [
                    "w3-research-pack-v1",
                    case.ticker,
                    d1_published.sha256,
                    d2_published.sha256,
                    original_policy,
                    origins,
                ]
            ),
            version_pin=W3ContextVersionPin(
                document1_run_id=d1_bundle.run_id,
                document2_run_id=d2_bundle.run_id,
                event_library_version=reference.version,
                policy_set_version=policy_set.policy_set_version,
            ),
            document1=document1,
            document2=document2,
            policy_set=policy_set,
            reference_view=reference.reference_view,
            document2_publication_state=str(d2_bundle.publication_state or "COMPLETE"),
            original_document2=d2_text,
            original_policy_set=original_policy,
            policy_origins=origins,
            context_cutoffs={
                "D1": None,
                "D2": document2.as_of.isoformat(),
                "PolicySet": (
                    policy_set.as_of.isoformat() if getattr(policy_set, "as_of", None) else None
                ),
                "Reference View": None,
            },
        )
        self._research_bases[base_key] = prepared
        while len(self._research_bases) > self._context_cache_size:
            self._research_bases.popitem(last=False)
        self._cache_context(cache_key, prepared)
        return self._without_consumed_policies(prepared)

    def _without_consumed_policies(self, context: W3PreparedContext) -> W3PreparedContext:
        if (
            isinstance(context.policy_set, PolicySetV3)
            or not policy_consumption.POLICY_AUTO_FREEZE_ENABLED
            or self._policy_consumption is None
        ):
            return context.model_copy()
        projection = self._policies.get_projection(
            context.policy_set.ticker,
            context.policy_set.policy_set_version,
        )
        if projection is None:
            raise W3Error(
                "w3_policy_projection_unavailable",
                "Version-pinned Runtime Policy Projection is unavailable",
            )
        consumed = self._policy_consumption.list_consumed_policy_revisions(
            context.policy_set.ticker
        )
        revision_by_policy = {
            item.policy_id: item.activation_revision for item in projection.policies
        }
        active = [
            policy
            for policy in context.policy_set.policies
            if (policy.policy_id, revision_by_policy.get(policy.policy_id, "")) not in consumed
        ]
        return context.model_copy(
            deep=False,
            update={"policy_set": context.policy_set.model_copy(update={"policies": active})},
        )

    def _current_document2(self, ticker: str) -> Document2Bundle:
        bundle = self._runtime.get_current_document2_bundle(ticker)
        if bundle is not None:
            return bundle
        raise W3Error("w3_document2_unavailable", "No current Published D2 is available")

    def _cached_context(self, key: tuple[str, str, str, int, int]) -> W3PreparedContext | None:
        with self._context_cache_lock:
            value = self._context_cache.get(key)
            if value is None:
                return None
            self._context_cache.move_to_end(key)
            return value.model_copy()

    def _cache_context(
        self,
        key: tuple[str, str, str, int, int],
        value: W3PreparedContext,
    ) -> None:
        with self._context_cache_lock:
            self._context_cache[key] = value.model_copy()
            self._context_cache.move_to_end(key)
            while len(self._context_cache) > self._context_cache_size:
                self._context_cache.popitem(last=False)

    @observed("context.published_read")
    async def _read_published(self, value: PublishedDocument) -> bytes:
        if value.content_text is not None:
            content = value.content_text.encode("utf-8")
        elif self._storage is not None and value.storage_path is not None:
            content = await self._storage.get(value.storage_path)
        else:
            raise W3Error(
                "w3_published_storage_unavailable",
                "Published artifact storage unavailable",
            )
        if len(content) != value.size_bytes:
            raise W3Error("w3_published_size_mismatch", "Published artifact size mismatch")
        if hashlib.sha256(content).hexdigest() != value.sha256:
            raise W3Error("w3_published_digest_mismatch", "Published artifact digest mismatch")
        return content


class CodexW3AgentRunner:
    """Run concurrent W3 turns on one dedicated async loop and preserve main threads."""

    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        context_provider: W3ContextProvider,
        prompt_root: str | Path,
        model: str = "gpt-6-luna",
        execution_model_override: str | None = None,
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 600,
        workspace_namespace: str = "persistent-runtime-w3",
        journal: Any = None,
        environment: W3ExecutionEnvironment | None = None,
    ) -> None:
        self._worker = worker
        self._case_repository = None
        self.environment = environment or W3ExecutionEnvironment()
        self._journal = journal
        self._workspace = workspace
        self._context = context_provider
        self._prompt_root = Path(prompt_root)
        self._model = model
        self._execution_model_override = execution_model_override
        self._model_provider = model_provider
        self._effort = effort
        self._timeout_seconds = timeout_seconds
        self._workspace_namespace = re.sub(
            r"[^a-z0-9-]+",
            "-",
            workspace_namespace.lower(),
        ).strip("-")
        if not self._workspace_namespace:
            raise ValueError("workspace_namespace must contain an alphanumeric character")
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="prv2-w3-async",
            daemon=True,
        )
        self._loop_thread.start()
        self._closed = False
        self._asset_preparations = {}
        self._pending_v4 = {}
        self._pending_v4_lock = threading.Lock()

    def entry_enabled(self, case):
        return case.frozen_inputs.get("w3_contracts", {}).get(
            "w3_entry_version"
        ) == ENTRY and hasattr(self._worker, "bind_w3_context")

    def _install_context(self, case, context):
        async def install():
            components = await self._worker.prepare_w3_research(
                case.ticker,
                dict(
                    d1=context.document1,
                    d2=context.original_document2
                    or compact(context.document2.model_dump(mode="json")),
                    policy=context.original_policy_set
                    or compact(context.policy_set.model_dump(mode="json")),
                    reference=context.reference_view,
                ),
            )
            saved = dict(
                components=components,
                version_pin=context.version_pin.model_dump(mode="json"),
                context_cutoffs=context.context_cutoffs,
                reference_event_ids=sorted(
                    context.reference_event_ids
                    if context.reference_event_ids is not None
                    else set(_EVENT_ID.findall(context.reference_view))
                ),
            )
            (
                await write_io(
                    self._journal.path,
                    self._journal.set,
                    "w3_research_v2",
                    research_pin_key(case),
                    saved,
                    retry=True,
                )
            )
            return saved

        return self._wait_v4(install())

    def configure_ready(self, case, template, *, active_public=True):
        if not self.entry_enabled(case):
            return
        profile = execution_profile(
            case,
            model=self._model,
            effort=self._effort,
            provider=self._model_provider,
            override=self._execution_model_override,
        )
        assets = case.frozen_inputs["w3_assets"]
        return self._wait_v4(
            self._worker.configure_w3_ready(
                dict(
                    parent_thread_id=template["thread_id"],
                    active_public=active_public,
                    profile=profile,
                    web_search_mode=self.environment.web_search_mode,
                    instructions={
                        "AGENTS.md": assets["w3/AGENTS.md"],
                        "skills/skill.md": assets["w3/skills/skill.md"],
                    },
                )
            )
        )

    @observed("thread.ready_claim")
    def claim_ready(self, case, record, profile):
        request = dict(
            case_id=case.case_id,
            record_id=record["record_id"],
            parent_thread_id=record["parent_id"],
            profile=profile,
        )
        try:
            value = self._wait_v4(self._worker.claim_w3_ready(request))
        except Exception as exc:
            if getattr(exc, "code", None) != "w3_ready_unconfigured":
                raise
            self.configure_ready(case, {"thread_id": record["parent_id"]}, active_public=False)
            value = self._wait_v4(self._worker.claim_w3_ready(request))
        with self._journal.transaction() as db:
            db.execute(
                "UPDATE runtime_v2_w3_threads SET thread_id=?,workspace=? WHERE record_id=?",
                (value["thread_id"], value["run_id"], record["record_id"]),
            )
        record.update(thread_id=value["thread_id"], workspace=value["run_id"])

    def stop_market(self, case_id):
        if hasattr(self._worker, "stop_w3_market"):
            return self._wait_v4(self._worker.stop_w3_market(case_id))

    def start_market(self, case):
        from datetime import timedelta

        if self.entry_enabled(case):
            return self._wait_v4(
                self._worker.start_w3_market(
                    dict(
                        case_id=case.case_id,
                        ticker=case.ticker,
                        mode=self.environment.mode,
                        deadline_at=(
                            datetime.now().astimezone()
                            + timedelta(seconds=self._timeout_seconds + 60)
                        ).isoformat(),
                    )
                )
            )

    def queue_market(self, case):
        if not self.entry_enabled(case):
            return
        from datetime import UTC, timedelta

        pending = asyncio.run_coroutine_threadsafe(
            self._worker.start_w3_market(
                dict(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    mode=self.environment.mode,
                    deadline_at=(
                        datetime.now(UTC) + timedelta(seconds=self._timeout_seconds + 60)
                    ).isoformat(),
                )
            ),
            self._loop,
        )

        def observed(future):
            try:
                future.result()
            except Exception as exc:
                self._journal.gap("w3-market-prepare", case.ticker, type(exc).__name__, str(exc))

        pending.add_done_callback(observed)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        from doxagent.control_io import loop_watch

        self.loop_lag = {}
        self._lag_task = self._loop.create_task(loop_watch(self.loop_lag))
        self._loop.run_forever()
        self._lag_task.cancel()
        self._loop.run_until_complete(asyncio.gather(self._lag_task, return_exceptions=True))

    def bind_case_repository(self, repository):
        self._case_repository = repository

    def _wait_v4(self, coroutine, *, key=None):
        if self._closed:
            coroutine.close()
            raise W3Error("w3_runner_closed", "W3 runner is closed")
        with self._pending_v4_lock:
            pending = self._pending_v4.get(key) if key is not None else None
            if pending is None:
                pending = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
                if key is not None:
                    self._pending_v4[key] = pending
            else:
                coroutine.close()
        try:
            return pending.result(timeout=self._timeout_seconds + 90)
        except TimeoutError:
            if pending.done():
                raise  # The coroutine itself failed, rather than caller wait expired.
            if key is None:
                pending.cancel()
                raise
            self._journal.set(
                "w3_pending_external",
                key,
                {"status": "PENDING_EXTERNAL", "at": self._journal.clock().isoformat()},
            )
            raise W3Error(
                "w3_pending_external",
                "Original W3 execution remains owned; reconnect to it",
                step="MODEL",
            )
        finally:
            if key is not None and pending.done():
                with self._pending_v4_lock:
                    if self._pending_v4.get(key) is pending:
                        del self._pending_v4[key]

    def prepare_public_assets(self, case):
        if self.entry_enabled(case):
            context = self._wait_v4(self._context.load(case))
            return self._install_context(case, context)

        async def prepare():
            from .w3_assets import research_assets

            context = await self._context.load(case)
            research = research_assets(context, self._journal)
            if hasattr(self._workspace, "ensure_research_pack"):
                await self._workspace.ensure_research_pack(research[0], research[1])
                from .journal import digest

                reference = context.reference_identity or digest(
                    [
                        "w3-reference-v1",
                        context.version_pin.event_library_version,
                        context.reference_view,
                    ]
                )
                await self._workspace.ensure_research_pack(
                    reference, {"reference_view.md": context.reference_view}
                )

        return self._wait_v4(prepare())

    @observed("context.prepare")
    def prepare_case(self, case):
        try:
            if self.entry_enabled(case):
                from types import SimpleNamespace

                saved = self._journal.get("w3_research_v2", research_pin_key(case))
                if saved is None:
                    if self.environment.mode == "OFFLINE_REPLAY":
                        # Explicit replay preparation has no production latency budget.
                        saved = self.prepare_public_assets(case)
                    else:
                        key = research_pin_key(case)
                        pending = self._asset_preparations.get(key)
                        if pending is not None and pending.done():
                            self._asset_preparations.pop(key, None)
                            pending.result()
                            saved = self._journal.get("w3_research_v2", key)
                        if saved is None:
                            if pending is None or pending.done():
                                self._asset_preparations[key] = asyncio.run_coroutine_threadsafe(
                                    asyncio.to_thread(self.prepare_public_assets, case), self._loop
                                )
                            raise W3Error(
                                "w3_prepare_pending",
                                "Exact pinned research is preparing off the Case stage; "
                                "no version substitution",
                            )
                policies = case.frozen_inputs.get("projection", {}).get("policies", [])
                wrapped = [
                    SimpleNamespace(
                        policy_id=p["policy_id"],
                        model_dump=lambda mode=None, v=p: v,
                        activation_conditions=[
                            SimpleNamespace(condition_id=c["condition_id"])
                            for c in p.get("activation_conditions", [])
                        ],
                    )
                    for p in policies
                ]
                context = SimpleNamespace(
                    components=saved["components"],
                    version_pin=W3ContextVersionPin.model_validate(saved["version_pin"]),
                    policy_set=SimpleNamespace(policies=wrapped),
                    context_cutoffs=saved["context_cutoffs"],
                    reference_view="",
                    policy_origins={},
                    reference_event_ids=frozenset(saved.get("reference_event_ids", []))
                    | frozenset(
                        p["provisional_event_id"] for p in case.frozen_inputs.get("provisional", [])
                    ),
                )
                return self._citation_context(case, context)
            return self._citation_context(case, self._wait_v4(self._prepare_v4(case)))
        except Exception as exc:
            exc.w3_step = "CONTEXT"
            raise

    def _citation_context(self, case, context):
        """Expose only already-frozen, loaded Events alongside the pinned reference."""
        if (
            case.frozen_inputs.get("w3_contracts", {}).get("w3_citation_scope_version")
            != "loaded-events.v1"
        ):
            return context
        saved = self._journal.get("w3_citation_scope", case.case_id)
        if saved is None:
            frozen = self._journal.get("round_inputs", case.case_id + ":W1:R2", {})
            from .w1_r2_context import frozen_event_details

            details = frozen_event_details(frozen)
            events = {}
            for event in details.get("canonical_events", []):
                identity = event.get("event_id")
                if identity and _EVENT_ID.fullmatch(identity):
                    events[identity] = event
            for event in case.frozen_inputs.get("provisional", []):
                identity = event["provisional_event_id"]
                events[identity] = event
            saved = dict(events=events, version_pin=case.version_pin.model_dump(mode="json"))
            self._journal.set("w3_citation_scope", case.case_id, saved)
        ids = frozenset(context.reference_event_ids or ()) | frozenset(saved["events"])
        updates = dict(reference_event_ids=ids)
        if hasattr(context, "components"):
            context.citation_events = saved["events"]
            context.reference_event_ids = ids
            return context
        reference = context.reference_view
        missing = {
            k: v for k, v in saved["events"].items() if k not in (context.reference_event_ids or ())
        }
        if missing:
            reference += "\n\n## Frozen loaded Event Details for this Case\n"
            for identity, event in sorted(missing.items()):
                reference += (
                    "\n### " + identity + "\n" + json.dumps(event, ensure_ascii=False) + "\n"
                )
            from .journal import digest

            updates.update(
                reference_view=reference,
                reference_identity=digest(["case-reference-v2", reference]),
            )
        return context.model_copy(update=updates)

    @observed("context.load_and_provisional")
    async def _prepare_v4(self, case):
        context = await self._context.load(case)
        provisional = case.frozen_inputs.get("provisional", [])
        if provisional:
            reference = context.reference_view + "\n\n## Prior known Runtime provisional facts\n"
            reference += json.dumps(provisional, ensure_ascii=False, indent=2)
            from .journal import digest

            context = context.model_copy(
                update={
                    "reference_view": reference,
                    "reference_identity": digest(
                        ["w3-reference-v1", context.version_pin.event_library_version, reference]
                    ),
                    "reference_event_ids": frozenset(_EVENT_ID.findall(reference)),
                }
            )
        return context

    def register_template(self, job_id):
        return self._wait_v4(self._worker.register_template(job_id))

    def register_legacy_thread(self, job_id):
        return self._wait_v4(self._worker.register_legacy_thread(job_id))

    def prepare_thread(self, request):
        return self._wait_v4(self._worker.prepare_thread(request))

    @property
    def supports_prepare(self):
        return hasattr(self._worker, "prepare_thread")

    @observed("thread.fork_roundtrip")
    def fork_thread(self, request):
        return self._wait_v4(self._worker.fork_thread(request))

    def thread_status(self, thread_id):
        return self._wait_v4(self._worker.thread_status(thread_id))

    def delete_thread(self, thread_id):
        return self._wait_v4(self._worker.delete_thread(thread_id))

    def acknowledge_job(self, job_id):
        return self._wait_v4(self._worker.acknowledge_job(job_id))

    def run_phase(self, *, case, record, phase, payload, context=None, research=None):
        return self._wait_v4(
            self._run_phase(
                case=case,
                record=record,
                phase=phase,
                payload=payload,
                context=context,
                research=research,
            ),
            key=f"{case.case_id}:{record['record_id']}:{phase}",
        )

    @observed("phase.materialize_and_model")
    async def _run_phase(self, **kwargs):
        try:
            return await self._run_phase_materialized(**kwargs)
        except Exception as exc:
            if not getattr(exc, "w3_step", None):
                exc.w3_step = "MATERIALIZE"
            raise

    async def _run_phase_materialized(
        self, *, case, record, phase, payload, context=None, research=None
    ):
        from .journal import digest
        from .w3_contracts import (
            OPERATIONS_PHASE,
            W3_LEGACY_CONTRACT,
            contract_models,
            is_unified_contract,
            parse_w3_operations,
            parse_w3_trade_decision,
        )
        from .worker_receipts import ReceiptWorker

        assets = case.frozen_inputs["w3_assets"]
        new_entry = self.entry_enabled(case)
        run_id = record["workspace"]
        attempt_id = case.case_id[-24:] + "-" + phase
        case_root = f"attempts/{attempt_id}"
        batch = hasattr(self._workspace, "materialize_case")
        files, packs = {}, {}

        async def write(run, path, content):
            if batch:
                files[path] = content
            else:
                await self._workspace.write_text(run, path, content)

        single_skill = "w3/AGENTS.md" in assets
        skill_path = "skills/skill.md" if single_skill else "skills/w3.md"
        instructions = {
            "AGENTS.md": assets["w3/AGENTS.md" if single_skill else "w3/agent.md"],
            skill_path: assets["w3/" + skill_path],
        }
        if batch:
            instruction_pack = digest(["w3-instructions-v1", instructions])
            await self._workspace.ensure_research_pack(instruction_pack, instructions)
            packs["."] = instruction_pack
        else:
            for path, content in instructions.items():
                await write(run_id, path, content)
        if research is not None and phase != "w3_template" and not new_entry:
            asset_key, research_files, _ = research
            if batch:
                await self._workspace.ensure_research_pack(asset_key, research_files)
                packs[f"context/research/{asset_key}"] = asset_key
            elif not (
                await read_io(self._journal.get, "w3_workspace_assets", digest([run_id, asset_key]))
            ):
                for path, content in research_files.items():
                    await write(run_id, f"context/research/{asset_key}/{path}", content)
                (
                    await write_io(
                        self._journal.path,
                        self._journal.set,
                        "w3_workspace_assets",
                        digest([run_id, asset_key]),
                        True,
                        retry=True,
                    )
                )
        reference_pack = ""
        if context is not None and phase != "w3_template" and not new_entry:
            if batch and phase != OPERATIONS_PHASE:
                reference_pack = context.reference_identity or digest(
                    [
                        "w3-reference-v1",
                        context.version_pin.event_library_version,
                        context.reference_view,
                    ]
                )
                await self._workspace.ensure_research_pack(
                    reference_pack, {"reference_view.md": context.reference_view}
                )
                packs[f"{case_root}"] = reference_pack
            else:
                await write(run_id, f"{case_root}/reference_view.md", context.reference_view)
        if phase == "w3_template":
            schema = {
                "type": "object",
                "properties": {"ready": {"type": "boolean"}},
                "required": ["ready"],
                "additionalProperties": False,
            }
            template = assets["w3/template.md"] if not single_skill else ""
        else:
            trade_model, operations_model = contract_models(
                case.w3_contract_version or W3_LEGACY_CONTRACT
            )
            model = operations_model if phase == OPERATIONS_PHASE else trade_model
            schema = strict_json_schema(model.model_json_schema())
            frozen = case.frozen_inputs.get("w3_contracts", {})
            schema = frozen.get(
                "w3_operations_schema" if phase == OPERATIONS_PHASE else "w3_trade_schema", schema
            )
            template = assets["w3/task.md"] if not single_skill else ""
        if phase == OPERATIONS_PHASE and context is not None:
            payload = dict(payload)
            payload["allowed_reference_event_ids"] = sorted(
                context.reference_event_ids
                if context.reference_event_ids is not None
                else set(_EVENT_ID.findall(context.reference_view))
            )
            payload["reference_rule"] = (
                "novelty.reference_ids accepts only the listed E# Event IDs, not F#, PIN IDs, "
                "news IDs or URLs. OLD requires actual fact coverage by one of these Events. "
                "Previously seen articles and insufficient body are not Event coverage."
            )
        task = dict(phase=phase, input=payload)
        if new_entry:
            task.update(
                entry_version=ENTRY,
                contract_version=case.w3_contract_version,
                output_fields=list(schema.get("properties", {})),
                instruction=(
                    "Inherited AGENTS and unique Skill apply. "
                    "This is the current phase input; do not reread contracts/task/schema "
                    "routinely. Research uses w3_research tools with context_ref. "
                    "Print one text body; follow next_cursor for incomplete relevant content."
                ),
            )
            if phase != "w3_template":
                frozen_key = case.case_id + ":" + phase
                binding = await read_io(self._journal.get, "w3_context_v2", frozen_key)
                if binding is None:
                    manifest = await read_io(
                        self._journal.get, "w3_context_manifest_v2", frozen_key
                    )
                    from doxagent.v2_read.native_content import NativeContent

                    codec = NativeContent(self._journal.path)
                    if manifest is not None:
                        manifest = await read_io(codec.decode, manifest)
                    if manifest is None:
                        manifest = dict(
                            case_id=case.case_id,
                            w3_contract_version=case.w3_contract_version,
                            phase=phase,
                            ticker=case.ticker,
                            inline_policy_ids=[p["policy_id"] for p in payload.get("policies", [])],
                            mode=self.environment.mode,
                            cutoff_at=payload.get("cutoff_at")
                            or case.source.message_bus_event_time.isoformat(),
                            version_pin=context.version_pin.model_dump(mode="json"),
                            components=context.components,
                            **(
                                {}
                                if is_unified_contract(case.w3_contract_version)
                                else dict(policy_usage=case.frozen_inputs.get("policy_usage", []))
                            ),
                            state={
                                k: payload[k]
                                for k in (
                                    "watch",
                                    "pins",
                                    "submitted_decision",
                                    "output_disposition",
                                )
                                if k in payload
                            },
                            history=payload.get("recent_trade_intents", []),
                            events={
                                **getattr(context, "citation_events", {}),
                                **{
                                    e.get("event_id", e.get("provisional_event_id")): e
                                    for e in payload.get("w1", {}).get("events", [])
                                },
                            },
                            evidence=case.frozen_inputs.get("w3_replay", {}).get(
                                "evidence_files", {}
                            ),
                            deadline_at=case.frozen_inputs.get("w3_deadline_at"),
                        )
                        if not manifest["deadline_at"]:
                            from datetime import UTC, timedelta

                            manifest["deadline_at"] = (
                                datetime.now(UTC) + timedelta(seconds=self._timeout_seconds + 60)
                            ).isoformat()
                        if self.environment.mode == "OFFLINE_REPLAY":
                            from doxagent.codex_worker.w3_market import offline_market_view

                            manifest["market_view"] = offline_market_view(
                                manifest["evidence"], manifest["cutoff_at"], case.ticker
                            )
                        (
                            await write_io(
                                self._journal.path,
                                self._journal.set,
                                "w3_context_manifest_v2",
                                frozen_key,
                                json.loads(await read_io(codec.encode, manifest)),
                                retry=True,
                            )
                        )
                    binding = await measured(
                        "case.context_bind",
                        self._worker.bind_w3_context(case.case_id, phase, manifest),
                    )
                    (
                        await write_io(
                            self._journal.path,
                            self._journal.set,
                            "w3_context_v2",
                            frozen_key,
                            binding,
                            retry=True,
                        )
                    )
                task.update(binding)
                payload.pop("ibkr_history", None)
                payload.pop("research_path", None)
                task["input"] = payload
        if self.environment.mode == "OFFLINE_REPLAY":
            task["execution_environment"] = self.environment.compatibility()
            task["instruction"] = (
                "This is an offline historical replay. Web Search, external MCP providers, "
                "IBKR API and "
                "subagents are disabled. Use the supplied local precollected search and "
                "price files instead of making network or IBKR calls. They replace only "
                "evidence delivery; retain the skill's decision and operations rules. "
                "Do not open URLs or use shell/Python network requests."
            )
            if phase != "w3_template":
                replay = case.frozen_inputs.get("w3_replay", {})
                evidence_root = f"{case_root}/evidence"
                if phase != OPERATIONS_PHASE and not new_entry:
                    for name, content in replay.get("evidence_files", {}).items():
                        await write(run_id, f"{evidence_root}/{name}", content)
                else:
                    evidence_root = f"attempts/{case.case_id[-24:]}-trade_decision/evidence"
                task["evidence"] = (
                    {
                        "access": "w3_research.read",
                        "kind": "raw_document",
                        "ids": list(replay.get("evidence_files", {})),
                    }
                    if new_entry
                    else dict(root=evidence_root, index=f"{evidence_root}/index.json")
                )
                if replay.get("policy_execution_view") and not new_entry:
                    view_path = f"{case_root}/policy_execution_view.json"
                    await write(
                        run_id,
                        view_path,
                        json.dumps(replay["policy_execution_view"], ensure_ascii=False, indent=2),
                    )
                    task["policy_execution_view"] = view_path
        paths = dict(
            task_path=f"{case_root}/task.json",
            output_schema=f"{case_root}/output_schema.json",
            skill_path=skill_path,
            reference_path=(
                f"attempts/{case.case_id[-24:]}-trade_decision/reference_view.md"
                if phase == OPERATIONS_PHASE
                else f"{case_root}/reference_view.md"
            )
            if phase != "w3_template"
            else None,
            research_path=f"context/research/{research[0]}"
            if research
            else payload.get("research_path", ""),
        )
        if single_skill:
            task.update(paths)
            if new_entry:
                for name in (
                    "research_path",
                    "reference_path",
                    "task_path",
                    "output_schema",
                    "skill_path",
                ):
                    task.pop(name, None)
            if phase == "w3_template":
                task["instruction"] = (
                    "Initialize the shared AGENTS.md and unique skill only. "
                    "Do not evaluate a message, research a trade, or perform operations. "
                    'Return {"ready": true} under output_schema.'
                )
        await write(
            run_id, f"{case_root}/task.json", json.dumps(task, ensure_ascii=False, indent=2)
        )
        await write(
            run_id,
            f"{case_root}/output_schema.json",
            json.dumps(schema, ensure_ascii=False, indent=2),
        )
        prompt = template
        for token, value in {
            "{phase}": phase,
            "{payload}": json.dumps(task, ensure_ascii=False),
            "{task_path}": f"{case_root}/task.json",
            "{schema_path}": f"{case_root}/output_schema.json",
            "{skill_path}": skill_path,
            "{reference_path}": (
                f"attempts/{case.case_id[-24:]}-trade_decision/reference_view.md"
                if phase == OPERATIONS_PHASE
                else f"{case_root}/reference_view.md"
            ),
            "{research_path}": (
                f"context/research/{research[0]}" if research else payload.get("research_path", "")
            ),
        }.items():
            prompt = prompt.replace(token, value)
        if single_skill:
            # Load the only business skill once in the mother thread; case turns
            # inherit it and carry only phase-specific data and artifact pointers.
            prompt = (
                assets["w3/skills/skill.md"] + "\n\n" if phase == "w3_template" else ""
            ) + json.dumps(task, ensure_ascii=False)
        profile = execution_profile(
            case,
            model=self._model,
            effort=self._effort,
            provider=self._model_provider,
            override=self._execution_model_override,
        )
        if batch:
            if phase != "w3_template":
                files[f"{case_root}/audit/w3_identity.json"] = json.dumps(
                    {
                        "case_id": case.case_id,
                        "ticker": case.ticker,
                        "phase": phase,
                        "context_version_pin": context.version_pin.model_dump(mode="json")
                        if context
                        else None,
                    },
                    separators=(",", ":"),
                )
            await self._workspace.materialize_case(run_id, packs, files)
        if (
            phase == "trade_decision"
            and not new_entry
            and not record.get("thread_id")
            and hasattr(self._worker, "prepare_thread")
        ):
            from doxagent.codex_worker.schema import W3PrepareRequest

            from .w3_threads import W3ThreadRegistry

            cutoff = (
                datetime.fromisoformat(case.frozen_inputs["w3_replay"]["cutoff_at"])
                if self.environment.mode == "OFFLINE_REPLAY"
                else case.source.message_bus_event_time
            )
            request = W3PrepareRequest(
                operation_key="w3-fork:" + record["record_id"],
                record_id=record["record_id"],
                source_case_id=case.case_id,
                parent_thread_id=record["parent_id"],
                run_id=run_id,
                attempt_id=attempt_id,
                research_pack_id=research[0] if research else "",
                reference_pack_id=reference_pack,
                case_overlay_sha=digest(files or payload),
                cutoff_at=cutoff,
                profile={
                    **profile,
                    "ticker": case.ticker,
                    "data_mcp_enabled": str(self.environment.data_mcp_enabled).lower(),
                    "web_search_mode": self.environment.web_search_mode,
                    "timeout_seconds": str(self._timeout_seconds),
                },
            )
            registry = W3ThreadRegistry(self._journal)
            stage = await read_io(registry.stage, case.case_id, phase)
            refs = stage.get("input_refs") or {}
            saved = refs.get("prepare_request")
            if saved:
                request = W3PrepareRequest.model_validate(saved)
            else:
                refs.update(
                    record_id=record["record_id"],
                    operation_key=request.operation_key,
                    prepare_request=request.model_dump(mode="json"),
                )

                def save_prepare():
                    with self._journal.transaction(operation="stage.prepare_request") as db:
                        db.execute(
                            "UPDATE runtime_v2_w3_stages SET input_refs=? WHERE case_id=? AND phase=?",
                            (json.dumps(refs), case.case_id, phase),
                        )

                await write_io(self._journal.path, save_prepare, retry=True)
            prepared = await self._worker.prepare_thread(request)
            record["thread_id"] = prepared["thread_id"]
            (
                await write_io(
                    self._journal.path,
                    registry.set_thread,
                    record["record_id"],
                    record["thread_id"],
                    retry=True,
                )
            )

        def observe_job(job):
            if (
                phase != "trade_decision"
                or job.status not in {"queued", "running"}
                or self._case_repository is None
            ):
                return
            case.error_code = case.error_message = None
            case.updated_at = self._journal.clock()
            case.frozen_inputs["w3_execution_state"] = dict(
                status="RUNNING" if job.status == "running" else "PENDING",
                phase=phase,
                step=job.execution_phase,
                worker_job_id=job.job_id,
            )
            self._journal.set(
                "w3_job_state", case.case_id, case.frozen_inputs["w3_execution_state"]
            )

        worker = ReceiptWorker(
            self._worker,
            self._journal,
            record["record_id"] if phase == "w3_template" else case.case_id,
            case_id=None if phase == "w3_template" else case.case_id,
            source_case_id=case.case_id
            if phase == "w3_template" and not case.case_id.startswith("public-")
            else None,
            on_update=observe_job if phase == "trade_decision" else None,
            repair_output=phase == OPERATIONS_PHASE,
            replace_failed_model=bool(self._execution_model_override)
            and "w3_execution_profile" not in case.frozen_inputs,
        )
        job = await measured(
            "worker.roundtrip." + phase,
            worker.run(
                WorkerRunRequest(
                    workflow_version=CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
                    research_lane=ResearchLane.PERSISTENT_RUNTIME,
                    run_id=run_id,
                    ticker=case.ticker,
                    node=CodexPersistentRuntimeNode.W3,
                    agent_role=CodexPersistentRuntimeAgentRole.W3,
                    attempt_id=attempt_id,
                    cutoff_at=datetime.fromisoformat(case.frozen_inputs["w3_replay"]["cutoff_at"])
                    if self.environment.mode == "OFFLINE_REPLAY"
                    else case.source.message_bus_event_time,
                    prompt=prompt,
                    output_schema=schema,
                    thread_id=record.get("thread_id"),
                    task_phase=phase,
                    web_search_mode="disabled"
                    if phase == "w3_template"
                    else self.environment.web_search_mode,
                    read_only=True,
                    data_mcp_enabled=False
                    if phase == "w3_template" or new_entry
                    else self.environment.data_mcp_enabled,
                    research_assets_mcp_enabled=new_entry and phase != "w3_template",
                    w3_entry_version=ENTRY if new_entry else None,
                    allow_subagents=False,
                    max_subagents=0,
                    timeout_seconds=self._timeout_seconds,
                    model=profile["model"],
                    model_provider=profile["provider"],
                    effort=profile["effort"],
                    execution_lane="background"
                    if phase in {"w3_template", OPERATIONS_PHASE}
                    else "realtime",
                )
            ),
        )
        if job.status != "succeeded" or not job.final_response or not job.thread_id:
            raise W3Error(
                job.error_code or "w3_phase_failed", job.error_message or job.status, step="MODEL"
            )
        try:
            if phase == "w3_template":
                if json.loads(job.final_response) != {"ready": True}:
                    raise ValueError("template initialization did not return ready=true")
                result = {"ready": True}
            elif phase == OPERATIONS_PHASE:
                result, issues = parse_w3_operations(
                    job.final_response, version=case.w3_contract_version or W3_LEGACY_CONTRACT
                )
                (
                    await write_io(
                        self._journal.path,
                        self._journal.set,
                        "w3_action_parse_issues",
                        case.case_id,
                        issues,
                        retry=True,
                    )
                )
            else:
                result = parse_w3_trade_decision(
                    job.final_response, version=case.w3_contract_version or W3_LEGACY_CONTRACT
                )
            if context is not None and phase != "w3_template":
                known = {
                    p.policy_id: {c.condition_id for c in p.activation_conditions}
                    for p in context.policy_set.policies
                }
                if phase == OPERATIONS_PHASE:
                    if not is_unified_contract(case.w3_contract_version) and not set(
                        result.policy.policy_ids
                    ).issubset(known):
                        raise ValueError(
                            "final Policy references must be present in the frozen PolicySet"
                        )
                    for group in (
                        ()
                        if is_unified_contract(case.w3_contract_version)
                        else result.policy.matched_condition_ids
                    ):
                        if not set(group.condition_ids).issubset(known.get(group.policy_id, set())):
                            raise ValueError(
                                "final Condition attribution is outside the frozen PolicySet"
                            )
                    event_ids = (
                        set(context.reference_event_ids)
                        if context.reference_event_ids is not None
                        else set(_EVENT_ID.findall(context.reference_view))
                    )
                    if not set(result.novelty.reference_ids).issubset(event_ids):
                        raise ValueError(
                            "novelty references must be present in the pinned Reference View"
                        )
                elif (
                    not is_unified_contract(case.w3_contract_version)
                    and result.policy_trade
                    and result.policy_trade.condition_id
                    not in known.get(result.policy_trade.policy_id, set())
                ):
                    raise ValueError("selected Condition must be present in the frozen PolicySet")
        except (ValueError, ValidationError) as exc:
            worker.reject_output(str(exc))
            raise W3Error("w3_phase_output_invalid", str(exc)[:4000], step="VALIDATE") from exc
        return result, job

    def run(
        self,
        *,
        case: RuntimeCase,
        w3_case: W3RouteCase,
        slot: W3ThreadSlot,
    ) -> tuple[W3CaseResult, str | None, W3ContextVersionPin]:
        if self._closed:
            raise W3Error("w3_runner_closed", "W3 runner is closed")
        future = asyncio.run_coroutine_threadsafe(
            self._run_case(case=case, w3_case=w3_case, slot=slot),
            self._loop,
        )
        return future.result(timeout=self._timeout_seconds + 90)

    async def _run_case(
        self,
        *,
        case: RuntimeCase,
        w3_case: W3RouteCase,
        slot: W3ThreadSlot,
    ) -> tuple[W3CaseResult, str | None, W3ContextVersionPin]:
        context = await self._context.load(case)
        provisional = case.frozen_inputs.get("provisional", [])
        if provisional:
            reference = context.reference_view + "\n\n## Prior known Runtime provisional facts\n"
            reference += (
                "These facts were known before this Case. "
                "Their original semantic day is preserved.\n"
            )
            for item in provisional:
                reference += (
                    f"\n### {item['provisional_event_id']} (semantic day {item['trading_date']})\n"
                    + json.dumps(item["candidate"], ensure_ascii=False)
                    + "\n"
                )
            from .journal import digest

            context = context.model_copy(
                update={
                    "reference_view": reference,
                    "reference_identity": digest(
                        ["w3-reference-v1", context.version_pin.event_library_version, reference]
                    ),
                    "reference_event_ids": frozenset(_EVENT_ID.findall(reference)),
                }
            )
        if slot.kind is W3ThreadKind.MAIN:
            run_id = f"{self._workspace_namespace}-{case.ticker.lower()}-main"
        else:
            run_id = (
                f"{self._workspace_namespace}-{case.ticker.lower()}-"
                f"fallback-{w3_case.w3_case_id[-24:]}"
            )
        if case.execution_bundle_id:
            run_id += "-" + case.execution_bundle_id[:16]
        attempt_id = f"w3-{w3_case.attempt_count:02d}"
        assets = case.frozen_inputs.get("w3_assets", {})

        def read_asset(name: str) -> str:
            if assets:
                return str(assets[f"w3/{name}"])
            return (self._prompt_root / name).read_text(encoding="utf-8")

        agent = read_asset("agent.md")
        skill_name = (
            "uncovered_new.md"
            if w3_case.mode is W3Mode.UNCOVERED_NEW
            else "revalidate_then_evaluate.md"
        )
        skill_files = {skill_name: read_asset("skills/" + skill_name)}
        if w3_case.mode is W3Mode.REVALIDATE_THEN_EVALUATE:
            skill_files["uncovered_new.md"] = read_asset("skills/uncovered_new.md")
        result_model = W3CaseResultV3 if case.orchestration_revision >= 3 else W3CaseResult
        schema = case.frozen_inputs.get("w3_output_schema") or strict_json_schema(
            result_model.model_json_schema()
        )
        if "w3_output_schema" not in case.frozen_inputs:
            case.frozen_inputs["w3_output_schema"] = schema
            from .journal import digest

            case.frozen_inputs["w3_output_schema_hash"] = digest(schema)
            if self._journal:
                (
                    await write_io(
                        self._journal.path,
                        self._journal.set,
                        "w3_case_contract",
                        case.case_id,
                        {"schema": schema, "sha256": digest(schema)},
                        retry=True,
                    )
                )
        event_boundary = (
            case.source.published_at
            or case.source.message_bus_event_time
            or case.source.collected_at
        )
        task = {
            "w3_case_id": w3_case.w3_case_id,
            "runtime_case_id": case.case_id,
            "mode": w3_case.mode.value,
            "ticker": case.ticker,
            "cutoff_at": case.source.message_bus_event_time.isoformat(),
            "event_boundary_at": event_boundary.isoformat(),
            "source_message": case.source.model_dump(mode="json"),
            "original_w1": w3_case.w1_final.model_dump(mode="json"),
            "original_w2": {
                "policy_ids": w3_case.w2_final.policy_ids if w3_case.w2_final.policy_hit else [],
                "matched_condition_ids": [
                    g.model_dump(mode="json") for g in w3_case.w2_final.hit_condition_ids
                ]
                if w3_case.w2_final.policy_hit
                else [],
                "confidence": w3_case.w2_final.confidence.value,
                "reason": w3_case.w2_final.reason,
            },
            "first_round_policy_assessment": w3_case.w2_final.model_dump(mode="json"),
            "w2_alignment": w3_case.w2_alignment.model_dump(mode="json")
            if w3_case.w2_alignment
            else None,
            "first_round_tags": [tag.value for tag in w3_case.first_round_tags],
            "orchestration_revision": w3_case.orchestration_revision,
            "initial_route_reason": w3_case.route_reason,
            "policy_usage": case.frozen_inputs.get("policy_usage", []),
            "policy_watch_followup": case.frozen_inputs.get("policy_watch_followup", []),
            "context_version_pin": context.version_pin.model_dump(mode="json"),
            "document2_publication_state": context.document2_publication_state,
        }
        case_root = f"cases/{w3_case.w3_case_id}"
        files = {
            "AGENTS.md": agent,
            f"{case_root}/task.json": json.dumps(task, ensure_ascii=False, indent=2),
            f"{case_root}/context/document1.md": context.document1,
            f"{case_root}/context/document2.json": context.document2.model_dump_json(indent=2),
            f"{case_root}/context/policy_set.json": context.policy_set.model_dump_json(indent=2),
            f"{case_root}/context/reference_view.md": context.reference_view,
            f"{case_root}/context/output_schema.json": json.dumps(
                schema,
                ensure_ascii=False,
                indent=2,
            ),
        }
        for name, content in skill_files.items():
            files[f"skills/{name}"] = content
        for path, content in files.items():
            await self._workspace.write_text(run_id, path, content)
        if w3_case.mode is W3Mode.REVALIDATE_THEN_EVALUATE:
            skill_instruction = (
                "Read AGENTS.md and skills/revalidate_then_evaluate.md first. "
                "If Stage A resolves to NEW + no Policy, then read "
                "skills/uncovered_new.md and continue Stage B in this same turn. "
            )
        else:
            skill_instruction = "Read AGENTS.md and skills/uncovered_new.md. "
        prompt = (
            f"Execute W3 Case {w3_case.w3_case_id} in {w3_case.mode.value} mode. "
            f"{skill_instruction}Read {case_root}/task.json, then the four "
            f"business context files and output_schema.json under {case_root}/context. "
            "Complete this Case "
            "in one turn and return only "
            "the strict JSON result. The output w3_case_id must exactly equal "
            f"{w3_case.w3_case_id!r}. The authoritative current Case task payload is:\n"
            f"{json.dumps(task, ensure_ascii=False, separators=(',', ':'))}"
        )
        profile = execution_profile(
            case,
            model=self._model,
            effort=self._effort,
            provider=self._model_provider,
            override=self._execution_model_override,
        )
        worker: Any = self._worker
        if self._journal:
            from .worker_receipts import ReceiptWorker

            worker = ReceiptWorker(
                worker,
                self._journal,
                w3_case.w3_case_id,
                case_id=case.case_id,
                replace_failed_model=bool(self._execution_model_override)
                and "w3_execution_profile" not in case.frozen_inputs,
            )
        job = await worker.run(
            WorkerRunRequest(
                workflow_version=CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
                research_lane=ResearchLane.PERSISTENT_RUNTIME,
                run_id=run_id,
                ticker=case.ticker,
                node=CodexPersistentRuntimeNode.W3,
                agent_role=CodexPersistentRuntimeAgentRole.W3,
                attempt_id=attempt_id,
                cutoff_at=case.source.message_bus_event_time,
                prompt=prompt,
                output_schema=schema,
                thread_id=slot.thread_id,
                model=profile["model"],
                model_provider=profile["provider"],
                effort=profile["effort"],
                read_only=True,
                data_mcp_enabled=True,
                allow_subagents=False,
                max_subagents=0,
                timeout_seconds=self._timeout_seconds,
            )
        )
        if job.status != "succeeded" or not job.final_response:
            message = job.error_message or job.status
            lowered = f"{job.error_code or ''} {message}".lower()
            invalid_thread = "thread" in lowered and any(
                token in lowered for token in ("not found", "invalid", "resume", "missing")
            )
            raise W3Error(
                job.error_code or "w3_worker_turn_failed",
                message[:4000],
                invalid_thread=invalid_thread,
            )
        try:
            result, watch_issues = parse_w3_result(
                job.final_response, revision=case.orchestration_revision
            )
            if self._journal and watch_issues:
                (
                    await write_io(
                        self._journal.path,
                        self._journal.set,
                        "policy_watch_validation",
                        case.case_id,
                        {"ticker": case.ticker, "case_id": case.case_id, "issues": watch_issues},
                        retry=True,
                    )
                )
        except ValidationError as exc:
            if self._journal:
                worker.reject_output("invalid structured output")
            raise W3Error("w3_invalid_structured_output", str(exc)[:4000]) from exc
        if result.w3_case_id != w3_case.w3_case_id:
            if self._journal:
                worker.reject_output("case correlation mismatch")
            raise W3Error(
                "w3_case_correlation_mismatch",
                "W3 output belongs to a different Case",
            )
        try:
            self._validate_semantics(w3_case.mode, result, context)
        except W3Error:
            if self._journal:
                worker.reject_output("semantic output rejected")
            raise
        return result, job.thread_id, context.version_pin

    @staticmethod
    def _validate_semantics(
        mode: W3Mode,
        result: W3CaseResult,
        context: W3PreparedContext,
    ) -> None:
        if mode is W3Mode.UNCOVERED_NEW:
            if result.novelty.result.value != "NEW" or result.policy.policy_ids:
                raise W3Error(
                    "w3_mode1_contract_violation",
                    "Mode 1 must preserve NEW with no Policy",
                )
        policy_ids = {item.policy_id for item in context.policy_set.policies}
        if not set(result.policy.policy_ids).issubset(policy_ids):
            raise W3Error(
                "w3_policy_reference_invalid",
                "W3 returned a Policy ID outside the version-pinned PolicySet",
            )
        if (
            isinstance(result, W3CaseResultV3)
            and result.policy_execution
            and result.policy_execution.selected_condition
        ):
            selected = result.policy_execution.selected_condition
            policies = {p.policy_id: p for p in context.policy_set.policies}
            if not any(
                c.condition_id == selected.condition_id
                for c in policies[selected.policy_id].activation_conditions
            ):
                raise W3Error(
                    "w3_condition_reference_invalid",
                    "Selected Condition is outside the pinned Policy",
                )
        event_ids = (
            set(context.reference_event_ids)
            if context.reference_event_ids is not None
            else set(_EVENT_ID.findall(context.reference_view))
        )
        if not set(result.novelty.reference_ids).issubset(event_ids):
            raise W3Error(
                "w3_event_reference_invalid",
                "W3 returned an Event ID outside the version-pinned Reference View",
            )

    def close(self) -> None:
        if self._closed:
            return
        for pending in self._asset_preparations.values():
            try:
                pending.result(timeout=30)
            except Exception:
                pending.cancel()
        self._asset_preparations.clear()
        self._closed = True
        close = getattr(self._worker, "aclose", None)
        if close is not None:
            try:
                asyncio.run_coroutine_threadsafe(close(), self._loop).result(timeout=30)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=30)
        self._loop.close()
