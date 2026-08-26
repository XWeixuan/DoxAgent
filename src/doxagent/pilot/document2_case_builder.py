"""Formal Codex App Pilot case export for a real Document2 attempt."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import uuid4

from pydantic import BaseModel

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.errors import WorkerUnavailable
from doxagent.codex_runtime.published_storage import (
    PublishedDocumentStorage,
    SupabasePublishedDocumentStorage,
)
from doxagent.codex_runtime.repository import (
    CodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    ArtifactKind,
    CodexAgentRole,
    CodexD1Node,
    CodexD2AgentRole,
    CodexD2Node,
    CodexResearchAgentRole,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    ResearchLane,
    utc_now,
)
from doxagent.codex_worker.schema import WorkerJob
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.data_server import (
    GUIDE_TOOL_NAME,
    READ_TOOL_NAME,
    VALIDATE_CITATIONS_TOOL_NAME,
)
from doxagent.pilot.case_builder import DEFAULT_PILOT_CAPABILITY_HOURS
from doxagent.pilot.templates import render_config, render_document2_task
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document2.inputs import (
    DoxAtlasNarrativeReportProvider,
    OptionalInput,
    _qualify_d1_aliases,
    _qualify_d1_context,
)
from doxagent.workflows.codex_document2.schema import (
    CandidateDiscoveryResult,
    DomainReviewResult,
    ExpectationShell,
    InputAvailability,
    ShellFinalizationResult,
    ShellSynthesisResult,
    strict_json_schema,
)

_PILOT_NODES = frozenset(
    {
        CodexD2Node.O0_CANDIDATE_C1,
        CodexD2Node.O0_CANDIDATE_C3,
        CodexD2Node.O0_CANDIDATE_C5,
        CodexD2Node.O0_CANDIDATE_NARRATIVE,
        CodexD2Node.O0_SYNTHESIS,
        CodexD2Node.O0_REVIEW_C1,
        CodexD2Node.O0_REVIEW_C3,
        CodexD2Node.O0_REVIEW_C5,
        CodexD2Node.O0_FINALIZATION,
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    }
)


class Document2PilotSourceAttemptUnavailable(ValueError):
    pass


class Document2PilotShellSelectionRequired(ValueError):
    def __init__(self, available_shell_ids: list[str]) -> None:
        self.available_shell_ids = tuple(available_shell_ids)
        available = ", ".join(self.available_shell_ids)
        super().__init__(
            "multiple Pilot shells are available; rerun advance with --shell. "
            f"available: {available}"
        )


@dataclass(frozen=True)
class Document2PilotCaseRequest:
    source_workspace_run: str
    node: CodexD2Node
    case_id: str
    capability_hours: int = DEFAULT_PILOT_CAPABILITY_HOURS
    upstream_cases: tuple[Document2PilotUpstreamCase, ...] = ()
    source_global_run_id: str | None = None
    shell_key: str | None = None


@dataclass(frozen=True)
class Document2PilotUpstreamCase:
    node: CodexD2Node
    case_root: Path


@dataclass(frozen=True)
class PreparedDocument2PilotCase:
    case_root: Path
    source_workspace_run: str
    node: CodexD2Node
    attempt_id: str
    input_sha256: str
    task_path: Path


class Document2PilotCaseBuilder:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        cases_root: str | Path,
        python: str | Path,
        runtime_env_file: str | Path,
        settings: DoxAgentSettings | None = None,
        repository: CodexRuntimeRepository | None = None,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.cases_root = Path(cases_root).resolve()
        self.python = Path(python).resolve()
        self.runtime_env_file = Path(runtime_env_file).resolve()
        self.settings = settings or DoxAgentSettings()
        self._repository = repository
        self._published_storage = published_storage
        self._source_cache_root = self.cases_root / "document2" / "_sources"
        if not self.settings.codex_worker_bearer_token:
            raise ValueError("DOXAGENT_CODEX_WORKER_BEARER_TOKEN is required")
        if not self.settings.codex_capability_secret:
            raise ValueError("DOXAGENT_CODEX_CAPABILITY_SECRET is required")
        self._client = HttpCodexWorkerClient(
            self.settings.codex_worker_base_url,
            self.settings.codex_worker_bearer_token,
            capability_secret=self.settings.codex_capability_secret,
        )

    async def prepare(self, request: Document2PilotCaseRequest) -> PreparedDocument2PilotCase:
        _identifier(request.source_workspace_run, "source_workspace_run")
        _identifier(request.case_id, "case_id")
        if request.node not in _PILOT_NODES:
            raise ValueError("node is not an agent-executed Document2 Pilot node")
        if not 1 <= request.capability_hours <= DEFAULT_PILOT_CAPABILITY_HOURS:
            raise ValueError(
                f"capability_hours must be between 1 and {DEFAULT_PILOT_CAPABILITY_HOURS}"
            )
        case_root = self.cases_root / "document2" / request.node.value / request.case_id
        if case_root.exists():
            raise FileExistsError(f"Pilot case already exists: {case_root}")
        if request.source_global_run_id is not None:
            return await self._prepare_bootstrap(request, case_root)
        case_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{request.case_id}-", dir=case_root.parent
        ) as tmp:
            staging = Path(tmp) / request.case_id
            staging.mkdir()
            initial = await self._client.export_workspace(request.source_workspace_run)
            _safe_extract(initial, staging)
            job = _latest_successful_attempt(staging, request.node)
            shutil.rmtree(staging)
            staging.mkdir()
            complete = await self._client.export_workspace(
                request.source_workspace_run,
                control_attempt_id=job.attempt_id,
            )
            _safe_extract(complete, staging)
            prepared = self._materialize(staging, case_root, request, attempt_id=job.attempt_id)
            os.replace(staging, case_root)
        return PreparedDocument2PilotCase(
            case_root=case_root,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=prepared.attempt_id,
            input_sha256=prepared.input_sha256,
            task_path=case_root / "PILOT_TASK.md",
        )

    async def _prepare_bootstrap(
        self,
        request: Document2PilotCaseRequest,
        case_root: Path,
    ) -> PreparedDocument2PilotCase:
        source_global_run_id = request.source_global_run_id
        if source_global_run_id is None:
            raise ValueError("source_global_run_id is required for Pilot bootstrap")
        _identifier(source_global_run_id, "source_global_run_id")
        bundle = self._bootstrap_bundle(source_global_run_id)
        reports = await self._bootstrap_reports(bundle)
        context = await self._bootstrap_context(request, bundle, reports)
        attempt_id = f"d2-pilot-{request.node.value.removeprefix('d2_')[:36]}-{uuid4().hex[:10]}"
        case_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{request.case_id}-", dir=case_root.parent
        ) as tmp:
            staging = Path(tmp) / request.case_id
            staging.mkdir()
            self._seed_bootstrap_attempt(staging, request.node, attempt_id, context)
            prepared = self._materialize(staging, case_root, request, attempt_id=attempt_id)
            os.replace(staging, case_root)
        return PreparedDocument2PilotCase(
            case_root=case_root,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=prepared.attempt_id,
            input_sha256=prepared.input_sha256,
            task_path=case_root / "PILOT_TASK.md",
        )

    def _bootstrap_bundle(self, source_global_run_id: str) -> GlobalResearchBundle:
        repository = self._bootstrap_repository()
        bundle = repository.get_bundle(source_global_run_id)
        if not isinstance(bundle, GlobalResearchBundle) or bundle.status != "published":
            raise ValueError("source_global_run_id must reference a published Global Research run")
        if bundle.published_at is None or bundle.handoff is None:
            raise ValueError("published Global Research bundle is missing its handoff")
        return bundle

    def _bootstrap_repository(self) -> CodexRuntimeRepository:
        if self._repository is not None:
            return self._repository
        if not self.settings.database_url:
            raise ValueError("DOXAGENT_DATABASE_URL is required for Pilot bootstrap")
        self._repository = PostgresCodexRuntimeRepository(self.settings.database_url)
        return self._repository

    def _bootstrap_storage(self) -> PublishedDocumentStorage:
        if self._published_storage is not None:
            return self._published_storage
        url = self.settings.codex_published_storage_url
        secret = self.settings.codex_published_storage_secret_key
        if not url or not secret:
            raise ValueError("published Document1 Storage is required for Pilot bootstrap")
        self._published_storage = SupabasePublishedDocumentStorage(
            url,
            secret,
            self.settings.codex_published_storage_bucket,
        )
        return self._published_storage

    async def _bootstrap_reports(self, bundle: GlobalResearchBundle) -> dict[str, str]:
        reports: dict[str, str] = {}
        cache_root = self._source_cache_root / bundle.run_id
        cache_root.mkdir(parents=True, exist_ok=True)
        repository = self._bootstrap_repository()
        for role in ("c1", "c3", "c5"):
            reference = bundle.reports.get(role)
            if reference is None:
                raise ValueError(f"Global Research bundle is missing required {role} report")
            cache_path = cache_root / f"{role}.md"
            if cache_path.is_file():
                raw = cache_path.read_bytes()
                if hashlib.sha256(raw).hexdigest() == reference.sha256:
                    reports[role] = _qualify_d1_aliases(raw.decode("utf-8"))
                    continue
            published = repository.get_published_document(bundle.run_id, reference.artifact_id)
            if published is None:
                raise ValueError(f"published Global Research {role} report is unavailable")
            if published.content_text is not None:
                raw = published.content_text.encode("utf-8")
            else:
                if published.storage_path is None:
                    raise ValueError(
                        f"published Global Research {role} report has no content location"
                    )
                raw = await self._bootstrap_storage().get(published.storage_path)
            digest = hashlib.sha256(raw).hexdigest()
            if (
                len(raw) != published.size_bytes
                or digest != published.sha256
                or digest != reference.sha256
            ):
                raise ValueError(f"published Global Research {role} report failed integrity checks")
            cache_path.write_bytes(raw)
            reports[role] = _qualify_d1_aliases(raw.decode("utf-8"))
        return reports

    async def _bootstrap_narrative(self, bundle: GlobalResearchBundle) -> OptionalInput:
        cache_root = self._source_cache_root / bundle.run_id
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_path = cache_root / "narrative.json"
        if cache_path.is_file():
            try:
                return OptionalInput.model_validate_json(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        provider = DoxAtlasNarrativeReportProvider(default_real_tool_registry(self.settings))
        narrative = await provider.load(
            ticker=bundle.ticker,
            as_of=cast(datetime, bundle.published_at),
        )
        cache_path.write_text(narrative.model_dump_json(indent=2), encoding="utf-8")
        return narrative

    async def _bootstrap_horizontal(
        self, bundle: GlobalResearchBundle
    ) -> tuple[dict[str, object], str | None]:
        references = [
            item
            for item in self._bootstrap_repository().list_artifacts(bundle.run_id, limit=500)
            if item.node is CodexD1Node.PROGRAM_COLLECTION and item.kind is ArtifactKind.BUNDLE
        ]
        for reference in reversed(references):
            cache_path = self._source_cache_root / bundle.run_id / "horizontal.json"
            try:
                if cache_path.is_file():
                    raw = cache_path.read_bytes()
                    if (
                        len(raw) == reference.size_bytes
                        and hashlib.sha256(raw).hexdigest() == reference.sha256
                    ):
                        cached = json.loads(raw)
                        if isinstance(cached, dict):
                            return cast(
                                dict[str, object], _qualify_d1_context(cached)
                            ), reference.artifact_id
                file = await self._client.read_text(bundle.run_id, reference.relative_path)
                content = file.content
                if content is None:
                    continue
                raw = content.encode("utf-8")
                if (
                    len(raw) != reference.size_bytes
                    or file.sha256 != reference.sha256
                    or hashlib.sha256(raw).hexdigest() != reference.sha256
                ):
                    continue
                horizontal = json.loads(content)
                if not isinstance(horizontal, dict):
                    continue
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(raw)
                return cast(
                    dict[str, object], _qualify_d1_context(horizontal)
                ), reference.artifact_id
            except (OSError, ValueError, WorkerUnavailable):
                # Pilot bootstrap remains permissive when the source Worker no
                # longer retains this unpublished, local-only D1 artifact.
                continue
        return {}, None

    async def _bootstrap_context(
        self,
        request: Document2PilotCaseRequest,
        bundle: GlobalResearchBundle,
        reports: dict[str, str],
    ) -> dict[str, object]:
        as_of = cast(datetime, bundle.published_at)
        horizontal, horizontal_artifact_id = await self._bootstrap_horizontal(bundle)
        event_library = OptionalInput(
            status=InputAvailability.NOT_CONFIGURED,
            warning="Event Library integration is reserved but not configured.",
            metadata={
                "interface_version": "event-library-read-v2",
                "read_only": True,
            },
        )
        common: dict[str, object] = {
            "ticker": bundle.ticker,
            "as_of": as_of.isoformat(),
            "future_nodes": [
                cast(
                    dict[str, object],
                    _qualify_d1_context(item.model_dump(mode="json", by_alias=True)),
                )
                for item in bundle.future_nodes
            ],
            "event_library": event_library.model_dump(mode="json"),
            "horizontal_indicators": horizontal,
        }
        node = request.node
        candidate_roles = {
            CodexD2Node.O0_CANDIDATE_C1: "c1",
            CodexD2Node.O0_CANDIDATE_C3: "c3",
            CodexD2Node.O0_CANDIDATE_C5: "c5",
        }
        if node in candidate_roles:
            role = candidate_roles[node]
            return {
                "source_role": role,
                "primary_source": reports[role],
                "narrative_run_id": None,
                **common,
            }
        if node is CodexD2Node.O0_CANDIDATE_NARRATIVE:
            narrative = await self._bootstrap_narrative(bundle)
            if narrative.status is not InputAvailability.AVAILABLE:
                raise Document2PilotSourceAttemptUnavailable(
                    narrative.warning or "source has no recent Narrative report"
                )
            return {
                "source_role": "narrative",
                "primary_source": json.dumps(narrative.payload, ensure_ascii=False, default=str),
                "narrative_run_id": narrative.source_run_id,
                **common,
            }

        upstream = _upstream_completions(request.upstream_cases)
        if node is CodexD2Node.O0_SYNTHESIS:
            labels = {
                CodexD2Node.O0_CANDIDATE_C1: "c1",
                CodexD2Node.O0_CANDIDATE_C3: "c3",
                CodexD2Node.O0_CANDIDATE_C5: "c5",
                CodexD2Node.O0_CANDIDATE_NARRATIVE: "narrative",
            }
            return {
                "candidate_sets": _pilot_candidate_sets_context(upstream, labels),
                **common,
            }
        review_roles = {
            CodexD2Node.O0_REVIEW_C1: ("C1", "c1"),
            CodexD2Node.O0_REVIEW_C3: ("C3", "c3"),
            CodexD2Node.O0_REVIEW_C5: ("C5", "c5"),
        }
        if node in review_roles:
            reviewer, report_role = review_roles[node]
            return {
                "reviewer_role": reviewer,
                "original_domain_report": reports[report_role],
                "provisional_shells": upstream.get(CodexD2Node.O0_SYNTHESIS, {}),
                **common,
            }
        if node is CodexD2Node.O0_FINALIZATION:
            review_labels = {
                CodexD2Node.O0_REVIEW_C1: "C1",
                CodexD2Node.O0_REVIEW_C3: "C3",
                CodexD2Node.O0_REVIEW_C5: "C5",
            }
            return {
                "provisional_shells": upstream.get(CodexD2Node.O0_SYNTHESIS, {}),
                "domain_reviews": {
                    review_labels[source]: value
                    for source, value in upstream.items()
                    if source in review_labels
                },
                **common,
            }

        narrative = await self._bootstrap_narrative(bundle)
        handoff = cast(GlobalResearchHandoffV1, bundle.handoff)
        global_research = {
            "run_id": bundle.run_id,
            "ticker": bundle.ticker,
            "published_at": as_of.isoformat(),
            "reports": reports,
            "report_artifact_ids": {
                role: bundle.reports[role].artifact_id for role in ("c1", "c3", "c5")
            },
            "entity_relations": [
                cast(
                    dict[str, object],
                    _qualify_d1_context(item.model_dump(mode="json", by_alias=True)),
                )
                for item in bundle.entity_relations
            ],
            "future_nodes": common["future_nodes"],
            "horizontal_collection": horizontal,
            "horizontal_artifact_id": horizontal_artifact_id,
            "document_artifact_id": handoff.document_artifact_id,
            "citation_manifest_artifact_id": handoff.citation_manifest_artifact_id,
        }
        o0_finalization = upstream.get(CodexD2Node.O0_FINALIZATION, {})
        if node is CodexD2Node.O1_STATE:
            canonical_shell = _select_bootstrap_shell(
                o0_finalization, request.shell_key
            )
        else:
            previous_o1_nodes = {
                CodexD2Node.O1_REALIZATION: CodexD2Node.O1_STATE,
                CodexD2Node.O1_GAPS: CodexD2Node.O1_REALIZATION,
                CodexD2Node.O1_FINALIZATION: CodexD2Node.O1_GAPS,
            }
            canonical_shell = upstream.get(previous_o1_nodes[node], {})
        turns = {
            CodexD2Node.O1_STATE: "STATE",
            CodexD2Node.O1_REALIZATION: "REALIZATION",
            CodexD2Node.O1_GAPS: "GAPS",
            CodexD2Node.O1_FINALIZATION: "FINALIZATION",
        }
        if node not in turns:
            raise ValueError(f"unsupported Document2 Pilot bootstrap node: {node.value}")
        return {
            "canonical_shell": canonical_shell,
            "o0_finalization": o0_finalization,
            "global_research": global_research,
            "narrative_research": narrative.model_dump(mode="json"),
            "event_library": event_library.model_dump(mode="json"),
            "turn": turns[node],
        }

    def _seed_bootstrap_attempt(
        self,
        staging: Path,
        node: CodexD2Node,
        attempt_id: str,
        context: dict[str, object],
    ) -> None:
        agent_asset, skill_asset, output_model = _bootstrap_contract(node)
        input_root = staging / "attempts" / attempt_id / "input"
        input_root.mkdir(parents=True)
        relative_root = f"attempts/{attempt_id}/input"
        task = {
            "schema_version": "document2-task-v1",
            "node": node.value,
            "required_files": [
                f"{relative_root}/AGENTS.md",
                f"{relative_root}/agent.md",
                f"{relative_root}/skill.md",
                f"{relative_root}/context.json",
                f"{relative_root}/output_schema.json",
            ],
            "previous_failure": None,
        }
        asset_root = self.repo_root / "prompts" / "codex_v2" / "document2"
        files = {
            "AGENTS.md": _read_asset(asset_root, "AGENTS.md"),
            "agent.md": _read_asset(asset_root, agent_asset),
            "skill.md": _read_asset(asset_root, skill_asset),
            "context.json": json.dumps(context, ensure_ascii=False, indent=2, default=str),
            "output_schema.json": json.dumps(
                strict_json_schema(output_model.model_json_schema()),
                ensure_ascii=False,
                indent=2,
            ),
            "task.json": json.dumps(task, ensure_ascii=False, indent=2),
        }
        for name, content in files.items():
            (input_root / name).write_text(content, encoding="utf-8")

    async def aclose(self) -> None:
        await self._client.aclose()

    def _materialize(
        self,
        staging: Path,
        installed_root: Path,
        request: Document2PilotCaseRequest,
        *,
        attempt_id: str,
    ) -> PreparedDocument2PilotCase:
        attempt_root = staging / "attempts" / attempt_id
        input_root = attempt_root / "input"
        required = [
            "AGENTS.md",
            "agent.md",
            "skill.md",
            "task.json",
            "context.json",
            "output_schema.json",
        ]
        missing = [name for name in required if not (input_root / name).is_file()]
        if missing:
            raise ValueError(f"source Document2 attempt is missing inputs: {', '.join(missing)}")
        context = json.loads((input_root / "context.json").read_text(encoding="utf-8"))
        if not isinstance(context, dict):
            raise ValueError("Document2 context.json must be an object")
        ticker = str(_find(context, "ticker") or "").upper()
        if not ticker:
            raise ValueError("Document2 context does not contain ticker")
        cutoff = _parse_datetime(_find(context, "as_of")) or utc_now()
        _keep_only_attempt(staging, attempt_id)
        shutil.rmtree(attempt_root / "output", ignore_errors=True)
        (attempt_root / "output").mkdir(parents=True)
        (attempt_root / "audit").mkdir(parents=True, exist_ok=True)
        (attempt_root / "audit" / "pilot_issues.md").write_text("", encoding="utf-8")
        snapshot = staging / "artifacts" / "snapshots" / f"{attempt_id}.json"
        snapshot.unlink(missing_ok=True)
        upstream_manifest = _materialize_pilot_upstream(staging, request.upstream_cases)
        role = _role_for_node(request.node)
        policy = DataToolPolicyRegistry()
        canonical_tools = sorted(policy.allowed_tools_for_ticker(request.node, role, ticker))
        contracts = build_data_tool_contracts(default_real_tool_registry(self.settings))
        enabled_tools = [GUIDE_TOOL_NAME, READ_TOOL_NAME, VALIDATE_CITATIONS_TOOL_NAME]
        enabled_tools.extend(
            contract.mcp_name
            for tool_id in canonical_tools
            if (contract := contracts.get(tool_id)) is not None and contract.exposed
        )
        codec = DataCapabilityCodec(self.settings.codex_capability_secret or "")
        capability = codec.issue(
            workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT2,
            run_id=request.source_workspace_run,
            node_id=request.node,
            node_attempt_id=attempt_id,
            agent_role=role,
            ticker=ticker,
            cutoff_at=cutoff,
            enabled_tool_ids=canonical_tools,
            ttl_seconds=request.capability_hours * 3600,
            pilot_case_id=request.case_id,
        )
        source_input_sha = _tree_hash(input_root)
        pilot_upstream_root = staging / "context" / "pilot_upstream"
        pilot_upstream_sha = (
            _tree_hash(pilot_upstream_root) if pilot_upstream_root.is_dir() else None
        )
        input_sha = (
            hashlib.sha256(f"{source_input_sha}\0{pilot_upstream_sha}".encode()).hexdigest()
            if pilot_upstream_sha is not None
            else source_input_sha
        )
        manifest = {
            "schema_version": "codex-research-pilot-case-v2",
            "case_id": request.case_id,
            "profile": "quality",
            "source_run_id": request.source_workspace_run,
            "run_id": request.source_workspace_run,
            "source_global_run_id": request.source_global_run_id,
            "bootstrap_from_global_research": request.source_global_run_id is not None,
            "node": request.node.value,
            "workflow_version": CODEX_DOCUMENT2_WORKFLOW_VERSION,
            "research_lane": ResearchLane.DOCUMENT2.value,
            "agent_role": role.value,
            "node_attempt_id": attempt_id,
            "attempt_id": attempt_id,
            "ticker": ticker,
            "cutoff_at": cutoff.isoformat(),
            "input_sha256": input_sha,
            "source_input_sha256": source_input_sha,
            "pilot_upstream_sha256": pilot_upstream_sha,
            "enabled_canonical_tools": canonical_tools,
            "enabled_data_tools": enabled_tools,
            "capability_expires_after_hours": request.capability_hours,
            "pilot_upstream": upstream_manifest,
            "created_at": utc_now().isoformat(),
        }
        (staging / "case_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        config_root = staging / ".codex"
        config_root.mkdir(parents=True, exist_ok=True)
        (config_root / "config.toml").write_text(
            render_config(
                python=self.python,
                case_root=installed_root,
                run_id=request.source_workspace_run,
                attempt_id=attempt_id,
                case_id=request.case_id,
                capability=capability,
                public_key=codec.public_key,
                enabled_data_tools=enabled_tools,
                ibkr={
                    "enabled": str(self.settings.ibkr_tws_enabled).lower(),
                    "host": self.settings.ibkr_tws_host,
                    "port": str(self.settings.ibkr_tws_port),
                    "client_id": str(self.settings.ibkr_tws_client_id),
                    "timeout_seconds": str(self.settings.ibkr_tws_timeout_seconds),
                    "market_data_type": str(self.settings.ibkr_tws_market_data_type),
                },
                runtime_env_file=self.runtime_env_file,
            ),
            encoding="utf-8",
        )
        (staging / "PILOT_TASK.md").write_text(
            render_document2_task(
                case_root=installed_root,
                node=request.node.value,
                run_id=request.source_workspace_run,
                attempt_id=attempt_id,
                has_pilot_upstream=bool(upstream_manifest),
            ),
            encoding="utf-8",
        )
        _protect_inputs(staging, attempt_id)
        return PreparedDocument2PilotCase(
            case_root=staging,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=attempt_id,
            input_sha256=input_sha,
            task_path=staging / "PILOT_TASK.md",
        )


def _bootstrap_contract(node: CodexD2Node) -> tuple[str, str, type[BaseModel]]:
    if node in {
        CodexD2Node.O0_CANDIDATE_C1,
        CodexD2Node.O0_CANDIDATE_C3,
        CodexD2Node.O0_CANDIDATE_C5,
        CodexD2Node.O0_CANDIDATE_NARRATIVE,
    }:
        return "agents/o0.md", "skills/candidate-discovery.md", CandidateDiscoveryResult
    if node is CodexD2Node.O0_SYNTHESIS:
        return "agents/o0.md", "skills/shell-synthesis.md", ShellSynthesisResult
    review_agents = {
        CodexD2Node.O0_REVIEW_C1: "agents/c1-review.md",
        CodexD2Node.O0_REVIEW_C3: "agents/c3-review.md",
        CodexD2Node.O0_REVIEW_C5: "agents/c5-review.md",
    }
    if node in review_agents:
        return review_agents[node], "skills/domain-review.md", DomainReviewResult
    if node is CodexD2Node.O0_FINALIZATION:
        return "agents/o0.md", "skills/shell-finalization.md", ShellFinalizationResult
    o1_skills = {
        CodexD2Node.O1_STATE: "skills/state-research.md",
        CodexD2Node.O1_REALIZATION: "skills/realization-research.md",
        CodexD2Node.O1_GAPS: "skills/gap-research.md",
        CodexD2Node.O1_FINALIZATION: "skills/research-finalization.md",
    }
    if node in o1_skills:
        return "agents/o1.md", o1_skills[node], ExpectationShell
    raise ValueError(f"unsupported Document2 Pilot bootstrap node: {node.value}")


def _read_asset(root: Path, relative_path: str) -> str:
    path = (root / relative_path).resolve()
    resolved_root = root.resolve()
    if resolved_root not in path.parents and path != resolved_root:
        raise ValueError("Document2 asset path escaped its root")
    return path.read_text(encoding="utf-8")


def _upstream_completions(
    upstream_cases: tuple[Document2PilotUpstreamCase, ...],
) -> dict[CodexD2Node, dict[str, object]]:
    completions: dict[CodexD2Node, dict[str, object]] = {}
    for upstream in upstream_cases:
        manifest = json.loads(
            (upstream.case_root / "case_manifest.json").read_text(encoding="utf-8")
        )
        attempt_id = str(manifest.get("node_attempt_id") or "")
        _identifier(attempt_id, "upstream attempt_id")
        raw = json.loads(
            (upstream.case_root / "attempts" / attempt_id / "output" / "completion.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(raw, dict):
            raise ValueError(
                f"Pilot upstream completion must be a JSON object: {upstream.case_root}"
            )
        completions[upstream.node] = cast(dict[str, object], raw)
    return completions


def _pilot_candidate_sets_context(
    upstream: dict[CodexD2Node, dict[str, object]],
    labels: dict[CodexD2Node, str],
) -> dict[str, dict[str, object]]:
    contextualized: dict[str, dict[str, object]] = {}
    for source, payload in upstream.items():
        source_role = labels.get(source)
        if source_role is None:
            continue
        copied = dict(payload)
        raw_candidates = payload.get("candidates")
        if isinstance(raw_candidates, list):
            copied["candidates"] = [
                {
                    **candidate,
                    "candidate_ref": (
                        f"{source_role.upper()}:{candidate.get('candidate_id')}"
                    ),
                }
                for candidate in raw_candidates
                if isinstance(candidate, dict)
            ]
        contextualized[source_role] = copied
    return contextualized


def _select_bootstrap_shell(
    finalization: dict[str, object], shell_key: str | None
) -> dict[str, object]:
    raw_shells = finalization.get("shells")
    shells = (
        [cast(dict[str, object], item) for item in raw_shells if isinstance(item, dict)]
        if isinstance(raw_shells, list)
        else []
    )
    if not shells:
        # Completion legality remains the only coordinator gate. A permissive raw
        # fallback lets the next Pilot node inspect and repair a weak handoff.
        return finalization
    if shell_key is not None:
        for shell in shells:
            shell_id = str(shell.get("shell_id") or "")
            hashed = hashlib.sha256(shell_id.encode("utf-8")).hexdigest()[:16]
            if shell_key in {shell_id, hashed}:
                return shell
        available = ", ".join(str(item.get("shell_id") or "") for item in shells)
        raise ValueError(
            f"Document2 Pilot shell is unavailable: {shell_key}; available: {available}"
        )
    if len(shells) == 1:
        return shells[0]
    raise Document2PilotShellSelectionRequired([str(item.get("shell_id") or "") for item in shells])


def _role_for_node(node: CodexD2Node) -> CodexResearchAgentRole:
    if node is CodexD2Node.O0_REVIEW_C1:
        return CodexAgentRole.C1
    if node is CodexD2Node.O0_REVIEW_C3:
        return CodexAgentRole.C3
    if node is CodexD2Node.O0_REVIEW_C5:
        return CodexAgentRole.C5
    if node in {
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    }:
        return CodexD2AgentRole.O1
    return CodexD2AgentRole.O0


def _latest_successful_attempt(root: Path, node: CodexD2Node) -> WorkerJob:
    candidates: list[WorkerJob] = []
    for path in (root / "audit" / "jobs").glob("*.json"):
        try:
            job = WorkerJob.model_validate_json(path.read_text(encoding="utf-8"))
            task = json.loads(
                (root / "attempts" / job.attempt_id / "input" / "task.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if job.status == "succeeded" and task.get("node") == node.value:
            candidates.append(job)
    if not candidates:
        raise Document2PilotSourceAttemptUnavailable(
            f"source workspace has no successful {node.value} attempt"
        )
    return max(candidates, key=lambda item: (item.updated_at, item.attempt_id))


def _materialize_pilot_upstream(
    staging: Path,
    upstream_cases: tuple[Document2PilotUpstreamCase, ...],
) -> list[dict[str, object]]:
    if not upstream_cases:
        return []
    destination_root = staging / "context" / "pilot_upstream"
    entries: list[dict[str, object]] = []
    seen: set[CodexD2Node] = set()
    for upstream in upstream_cases:
        if upstream.node in seen:
            raise ValueError(f"duplicate Pilot upstream node: {upstream.node.value}")
        seen.add(upstream.node)
        case_root = upstream.case_root.resolve()
        manifest_path = case_root / "case_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Pilot upstream case: {case_root}") from exc
        if manifest.get("node") != upstream.node.value:
            raise ValueError(f"Pilot upstream node mismatch: {case_root}")
        attempt_id = str(manifest.get("node_attempt_id") or "")
        _identifier(attempt_id, "upstream attempt_id")
        output_root = case_root / "attempts" / attempt_id / "output"
        completion = output_root / "completion.json"
        try:
            json.loads(completion.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Pilot upstream completion is unavailable: {case_root}") from exc
        target = destination_root / upstream.node.value / "output"
        shutil.copytree(output_root, target)
        files = [
            {
                "path": path.relative_to(destination_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(item for item in target.rglob("*") if item.is_file())
        ]
        entries.append(
            {
                "node": upstream.node.value,
                "case_id": str(manifest.get("case_id") or ""),
                "attempt_id": attempt_id,
                "files": files,
            }
        )
    (destination_root / "manifest.json").write_text(
        json.dumps({"schema_version": "d2-pilot-upstream-v1", "entries": entries}, indent=2),
        encoding="utf-8",
    )
    return entries


def _safe_extract(raw: bytes, destination: Path) -> None:
    archive_path = destination.parent / "workspace.zip"
    archive_path.write_bytes(raw)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                pure = PurePosixPath(member.filename.replace("\\", "/"))
                if (
                    pure.is_absolute()
                    or not pure.parts
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or ":" in pure.parts[0]
                ):
                    raise ValueError(f"unsafe worker export path: {member.filename}")
                target = destination.joinpath(*pure.parts).resolve()
                target.relative_to(destination.resolve())
            archive.extractall(destination)
    finally:
        archive_path.unlink(missing_ok=True)


def _keep_only_attempt(root: Path, attempt_id: str) -> None:
    attempts = root / "attempts"
    for path in attempts.iterdir() if attempts.is_dir() else ():
        if path.is_dir() and path.name != attempt_id:
            shutil.rmtree(path)
    shutil.rmtree(root / "audit" / "jobs", ignore_errors=True)


def _protect_inputs(root: Path, attempt_id: str) -> None:
    for directory in (
        root / ".codex",
        root / "context",
        root / "artifacts",
        root / "attempts" / attempt_id / "input",
    ):
        for path in directory.rglob("*") if directory.is_dir() else ():
            if path.is_file():
                path.chmod(stat.S_IREAD)
    for path in (root / "case_manifest.json", root / "PILOT_TASK.md"):
        path.chmod(stat.S_IREAD)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _find(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return cast(object, value[key])
        for child in value.values():
            found = _find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find(child, key)
            if found is not None:
                return found
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _identifier(value: str, label: str) -> None:
    if not value or len(value) > 128 or not all(char.isalnum() or char in "._-" for char in value):
        raise ValueError(f"invalid {label}")
