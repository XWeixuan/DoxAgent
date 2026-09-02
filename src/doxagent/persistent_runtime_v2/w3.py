"""Codex SDK W3 exception-plane context and one-Case turn runner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Literal, Protocol

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
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_document2.schema import Document2Bundle, Document2Document
from doxagent.workflows.codex_document3.repository import Document3PolicyRepository
from doxagent.workflows.codex_document3.schema import PolicySet

from .schema import (
    RuntimeCase,
    W3CaseResult,
    W3ContextVersionPin,
    W3Mode,
    W3RouteCase,
    W3ThreadKind,
    W3ThreadSlot,
    strict_json_schema,
)

_EVENT_ID = re.compile(r"\bE[1-9][0-9]*\b")


class W3Error(RuntimeError):
    def __init__(self, code: str, message: str, *, invalid_thread: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.invalid_thread = invalid_thread


class W3PreparedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_pin: W3ContextVersionPin
    document1: str = Field(min_length=1)
    document2: Document2Document
    policy_set: PolicySet
    reference_view: str = Field(min_length=1)
    document2_publication_state: str


class W3ContextProvider(Protocol):
    async def load(self, case: RuntimeCase) -> W3PreparedContext: ...


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
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._runtime = runtime_repository
        self._policies = policy_repository
        self._events = event_library_reader
        self._storage = published_storage

    async def load(self, case: RuntimeCase) -> W3PreparedContext:
        d2_bundle = self._current_document2(case.ticker)
        if d2_bundle.handoff is None:
            raise W3Error("w3_document2_unavailable", "Current Published D2 has no handoff")
        d2_published = self._runtime.get_published_document(
            d2_bundle.run_id,
            d2_bundle.handoff.document2_artifact_id,
        )
        if d2_published is None:
            raise W3Error("w3_document2_unavailable", "Current Published D2 body is unavailable")
        d2_text = (await self._read_published(d2_published)).decode("utf-8")
        try:
            document2 = Document2Document.model_validate_json(d2_text)
        except ValidationError as exc:
            raise W3Error("w3_document2_invalid", "Current Published D2 is invalid") from exc
        if document2.ticker.upper() != case.ticker:
            raise W3Error("w3_document2_ticker_mismatch", "Published D2 ticker mismatch")

        d1_bundle = self._runtime.get_bundle(document2.source_global_run_id)
        if not isinstance(d1_bundle, GlobalResearchBundle) or d1_bundle.handoff is None:
            raise W3Error(
                "w3_document1_unavailable",
                "D2 source_global_run_id does not resolve to Published Global Research",
            )
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
        reference = self._events.reference_view(
            case.ticker,
            version=case.version_pin.event_library_version,
        )
        if reference is None or not reference.reference_view.strip():
            raise W3Error(
                "w3_reference_view_unavailable",
                "Version-pinned Reference View is unavailable",
            )

        return W3PreparedContext(
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
        )

    def _current_document2(self, ticker: str) -> Document2Bundle:
        summaries = self._runtime.list_run_summaries(
            ticker,
            limit=100,
            research_lane=ResearchLane.DOCUMENT2,
        )
        for summary in summaries:
            bundle = self._runtime.get_bundle(summary.run_id)
            if (
                isinstance(bundle, Document2Bundle)
                and bundle.current
                and bundle.status == "published"
            ):
                return bundle
        raise W3Error("w3_document2_unavailable", "No current Published D2 is available")

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
        model: str = "gpt-5.6-luna",
        model_provider: str | None = None,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "max",
        timeout_seconds: int = 600,
        workspace_namespace: str = "persistent-runtime-w3",
    ) -> None:
        self._worker = worker
        self._workspace = workspace
        self._context = context_provider
        self._prompt_root = Path(prompt_root)
        self._model = model
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

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

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
        if slot.kind is W3ThreadKind.MAIN:
            run_id = f"{self._workspace_namespace}-{case.ticker.lower()}-main"
        else:
            run_id = (
                f"{self._workspace_namespace}-{case.ticker.lower()}-"
                f"fallback-{w3_case.w3_case_id[-24:]}"
            )
        attempt_id = f"w3-{w3_case.attempt_count:02d}"
        agent = (self._prompt_root / "agent.md").read_text(encoding="utf-8")
        skill_name = (
            "uncovered_new.md"
            if w3_case.mode is W3Mode.UNCOVERED_NEW
            else "revalidate_then_evaluate.md"
        )
        skill_files = {
            skill_name: (self._prompt_root / "skills" / skill_name).read_text(
                encoding="utf-8"
            )
        }
        if w3_case.mode is W3Mode.REVALIDATE_THEN_EVALUATE:
            skill_files["uncovered_new.md"] = (
                self._prompt_root / "skills" / "uncovered_new.md"
            ).read_text(encoding="utf-8")
        schema = strict_json_schema(W3CaseResult.model_json_schema())
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
            "original_w2": w3_case.w2_final.model_dump(mode="json"),
            "initial_route_reason": w3_case.route_reason,
            "context_version_pin": context.version_pin.model_dump(mode="json"),
            "document2_publication_state": context.document2_publication_state,
        }
        case_root = f"cases/{w3_case.w3_case_id}"
        files = {
            "AGENTS.md": agent,
            f"{case_root}/task.json": json.dumps(task, ensure_ascii=False, indent=2),
            f"{case_root}/context/document1.md": context.document1,
            f"{case_root}/context/document2.json": context.document2.model_dump_json(
                indent=2
            ),
            f"{case_root}/context/policy_set.json": context.policy_set.model_dump_json(
                indent=2
            ),
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
        job = await self._worker.run(
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
                model=self._model,
                model_provider=self._model_provider,
                effort=self._effort,
                read_only=True,
                data_mcp_enabled=False,
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
            result = W3CaseResult.model_validate_json(job.final_response)
        except ValidationError as exc:
            raise W3Error("w3_invalid_structured_output", str(exc)[:4000]) from exc
        if result.w3_case_id != w3_case.w3_case_id:
            raise W3Error(
                "w3_case_correlation_mismatch",
                "W3 output belongs to a different Case",
            )
        self._validate_semantics(w3_case.mode, result, context)
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
        event_ids = set(_EVENT_ID.findall(context.reference_view))
        if not set(result.novelty.reference_ids).issubset(event_ids):
            raise W3Error(
                "w3_event_reference_invalid",
                "W3 returned an Event ID outside the version-pinned Reference View",
            )

    def close(self) -> None:
        if self._closed:
            return
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
