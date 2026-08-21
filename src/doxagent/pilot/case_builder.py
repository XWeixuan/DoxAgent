"""Generate an isolated Codex App Pilot case from a successful worker run."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.schema import (
    CODEX_D1_WORKFLOW_VERSION,
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    CODEX_MARKET_SITUATION_WORKFLOW_VERSION,
    CodexD1Node,
    CodexWorkflowVersion,
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
from doxagent.pilot.case_workspace import PilotCaseWorkspace
from doxagent.pilot.templates import render_config, render_task
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document1.attempt_bundle import AttemptBundleSeeder
from doxagent.workflows.codex_document1.node_runner import role_for_node
from doxagent.workflows.codex_document1.schema import NodeOutput

_MANUAL_CITATION = re.compile(r"【cite:O[1-9]\d*】")
_MANUAL_CITATION_REPLACEMENT = "[上游引用需在当前 attempt 重新核验]"
_MAX_MANUAL_UPSTREAM_BYTES = 2 * 1024 * 1024
DEFAULT_PILOT_CAPABILITY_HOURS = 24 * 365 * 10
MAX_PILOT_CAPABILITY_HOURS = DEFAULT_PILOT_CAPABILITY_HOURS
C4_PRE_SCAN_UPSTREAM_FILE = "c4_pre_scan.json"
# Deprecated historical filename retained only for reproducible legacy cases.
# New lane cases never request or emit this ambiguous double-extension name.
UNIFIED_C4_UPSTREAM_FILE = "c4_finalization.json.json"
LEGACY_MANUAL_UPSTREAM_FILES: dict[CodexD1Node, tuple[str, ...]] = {
    CodexD1Node.C1: (UNIFIED_C4_UPSTREAM_FILE,),
    CodexD1Node.C3: (UNIFIED_C4_UPSTREAM_FILE,),
    CodexD1Node.C4_PRE_SCAN: (UNIFIED_C4_UPSTREAM_FILE,),
    CodexD1Node.C4_ENRICHMENT: (UNIFIED_C4_UPSTREAM_FILE, "c1.md", "c3.md"),
    CodexD1Node.C4_FINALIZATION: (UNIFIED_C4_UPSTREAM_FILE,),
    CodexD1Node.O4_A: (UNIFIED_C4_UPSTREAM_FILE, "c1.md", "c3.md"),
}
GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES: dict[CodexD1Node, tuple[str, ...]] = {
    CodexD1Node.C1: (C4_PRE_SCAN_UPSTREAM_FILE,),
    CodexD1Node.C3: (C4_PRE_SCAN_UPSTREAM_FILE,),
    CodexD1Node.C5: ("c1.md", "c3.md"),
    CodexD1Node.C4_ENRICHMENT: (
        C4_PRE_SCAN_UPSTREAM_FILE,
        "c1.md",
        "c3.md",
        "c5.md",
    ),
}


@dataclass(frozen=True)
class PilotCaseRequest:
    source_run: str
    node: CodexD1Node
    case_id: str
    capability_hours: int = DEFAULT_PILOT_CAPABILITY_HOURS
    profile: Literal["functional", "quality"] = "functional"
    upstream_dir: Path | None = None
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1


@dataclass(frozen=True)
class ManualUpstreamImport:
    source_dir: Path
    files: dict[str, str]
    source_sha256: dict[str, str]
    injected_sha256: dict[str, str]

    def manifest(self) -> dict[str, object]:
        return {
            "mode": "pilot_override",
            "set_id": self.source_dir.name,
            "files": {
                name: {
                    "source_sha256": self.source_sha256[name],
                    "injected_sha256": self.injected_sha256[name],
                }
                for name in sorted(self.files)
            },
            "citation_policy": "context_only_reverify",
        }


@dataclass(frozen=True)
class PreparedPilotCase:
    case_root: Path
    source_run: str
    node: CodexD1Node
    attempt_id: str
    input_sha256: str
    task_path: Path


class PilotCaseBuilder:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        cases_root: str | Path,
        python: str | Path,
        runtime_env_file: str | Path,
        settings: DoxAgentSettings | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.cases_root = Path(cases_root).resolve()
        self.python = Path(python).resolve()
        self.runtime_env_file = Path(runtime_env_file).resolve()
        self.settings = settings or DoxAgentSettings()
        if not self.settings.codex_worker_bearer_token:
            raise ValueError("DOXAGENT_CODEX_WORKER_BEARER_TOKEN is required")
        if not self.settings.codex_capability_secret:
            raise ValueError("DOXAGENT_CODEX_CAPABILITY_SECRET is required")
        self._client = HttpCodexWorkerClient(
            self.settings.codex_worker_base_url,
            self.settings.codex_worker_bearer_token,
            capability_secret=self.settings.codex_capability_secret,
        )

    async def prepare(self, request: PilotCaseRequest) -> PreparedPilotCase:
        _validate_identifier(request.source_run, "source_run")
        _validate_identifier(request.case_id, "case_id")
        if not 1 <= request.capability_hours <= MAX_PILOT_CAPABILITY_HOURS:
            raise ValueError(f"capability_hours must be between 1 and {MAX_PILOT_CAPABILITY_HOURS}")
        if request.profile == "quality" and request.node not in QUALITY_PILOT_NODES:
            raise ValueError("the quality Pilot profile does not support this research node")
        case_parent = (
            self.cases_root / request.node.value
            if request.research_lane is ResearchLane.LEGACY_DOCUMENT1
            else self.cases_root / request.research_lane.value / request.node.value
        )
        case_root = case_parent / request.case_id
        if case_root.exists():
            raise FileExistsError(f"Pilot case already exists: {case_root}")
        case_root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{request.case_id}-",
            dir=case_root.parent,
        ) as temporary:
            staging = Path(temporary) / request.case_id
            staging.mkdir()
            initial = await self._client.export_workspace(request.source_run)
            _safe_extract(initial, staging)
            attempt = _latest_successful_attempt(staging, request.node)
            shutil.rmtree(staging)
            staging.mkdir()
            complete = await self._client.export_workspace(
                request.source_run,
                control_attempt_id=attempt.attempt_id,
            )
            _safe_extract(complete, staging)
            _assert_control_store(staging, request.source_run, attempt.attempt_id)
            prepared = await self._materialize(
                staging,
                case_root,
                request,
                attempt,
            )
            os.replace(staging, case_root)
        return PreparedPilotCase(
            case_root=case_root,
            source_run=request.source_run,
            node=request.node,
            attempt_id=prepared.attempt_id,
            input_sha256=prepared.input_sha256,
            task_path=case_root / "PILOT_TASK.md",
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _materialize(
        self,
        staging: Path,
        installed_case_root: Path,
        request: PilotCaseRequest,
        attempt: WorkerJob,
    ) -> PreparedPilotCase:
        attempt_root = staging / "attempts" / attempt.attempt_id
        context_path = attempt_root / "input" / "context.json"
        try:
            context_outer = json.loads(context_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("successful source attempt has no valid context.json") from exc
        if context_outer.get("node") != request.node.value:
            raise ValueError("source attempt node does not match requested node")
        payload = context_outer.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("source attempt context payload is invalid")
        payload = deepcopy(payload)
        workflow_version, research_lane, asset_root = _pilot_identity(request)
        context_lane = context_outer.get("research_lane")
        if context_lane is not None and context_lane != research_lane.value:
            raise ValueError("source attempt research lane does not match requested lane")
        if request.profile == "quality":
            payload = _quality_payload(request.node, payload, request.research_lane)
        manual_upstream = _load_manual_upstream(
            request.node, request.upstream_dir, request.research_lane
        )
        if manual_upstream is not None:
            payload = _apply_manual_upstream(
                request.node,
                payload,
                manual_upstream.files,
                request.research_lane,
            )
        horizontal_path = attempt_root / "input" / "horizontal.json"
        horizontal: dict[str, object] | None = None
        if horizontal_path.is_file():
            candidate = json.loads(horizontal_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                horizontal = candidate
        ticker = str(_find_value(payload, "ticker") or "").upper()
        if not ticker:
            raise ValueError("source attempt context does not contain ticker")
        cutoff = _parse_datetime(_find_value(payload, "cutoff_at")) or utc_now()

        _remove_other_attempts(staging, attempt.attempt_id)
        _clear_current_node_outputs(staging, request.node, attempt.attempt_id)
        shutil.rmtree(attempt_root / "input", ignore_errors=True)
        (attempt_root / "audit").mkdir(parents=True, exist_ok=True)
        (attempt_root / "audit" / "pilot_issues.md").write_text("", encoding="utf-8")

        workspace = PilotCaseWorkspace(staging)
        seeder = AttemptBundleSeeder(
            workspace,
            self.repo_root / asset_root,
        )
        seeded = await seeder.seed(
            run_id=request.source_run,
            node=request.node,
            attempt_id=attempt.attempt_id,
            context_payload=payload,
            horizontal=horizontal,
            manual_upstream=(manual_upstream.files if manual_upstream is not None else None),
            workflow_version=workflow_version,
            research_lane=research_lane,
        )
        role = role_for_node(request.node)
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
            workflow_version=workflow_version,
            research_lane=research_lane,
            run_id=request.source_run,
            node_id=request.node,
            node_attempt_id=attempt.attempt_id,
            agent_role=role,
            ticker=ticker,
            cutoff_at=cutoff,
            enabled_tool_ids=canonical_tools,
            ttl_seconds=request.capability_hours * 3600,
            pilot_case_id=request.case_id,
        )
        probe_name, probe_args = _probe_for_node(request.node, enabled_tools)
        manifest = {
            "schema_version": "codex-research-pilot-case-v2",
            "case_id": request.case_id,
            "profile": request.profile,
            "source_run_id": request.source_run,
            "run_id": request.source_run,
            "node": request.node.value,
            "workflow_version": workflow_version,
            "research_lane": research_lane.value,
            "agent_role": role.value,
            "node_attempt_id": attempt.attempt_id,
            # Compatibility alias for storage/runtime DTOs that have not yet
            # renamed their persistence field. Validators require equality.
            "attempt_id": attempt.attempt_id,
            "ticker": ticker,
            "cutoff_at": cutoff.isoformat(),
            "input_sha256": seeded.input_sha256,
            "manual_upstream": (
                manual_upstream.manifest() if manual_upstream is not None else None
            ),
            "python": str(self.python),
            "repo_root": str(self.repo_root),
            "enabled_canonical_tools": canonical_tools,
            "enabled_data_tools": enabled_tools,
            "doctor_probe": {"name": probe_name, "arguments": probe_args},
            "capability_expires_after_hours": request.capability_hours,
            "created_at": utc_now().isoformat(),
        }
        (staging / "case_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        config_root = staging / ".codex"
        config_root.mkdir(parents=True, exist_ok=True)
        (config_root / "config.toml").write_text(
            render_config(
                python=self.python,
                case_root=installed_case_root,
                run_id=request.source_run,
                attempt_id=attempt.attempt_id,
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
            render_task(
                case_root=installed_case_root,
                node=request.node.value,
                research_lane=research_lane.value,
                run_id=request.source_run,
                attempt_id=attempt.attempt_id,
                profile=request.profile,
                manual_upstream_paths=seeded.manual_upstream_paths,
            ),
            encoding="utf-8",
        )
        _make_inputs_read_only(staging, attempt.attempt_id)
        return PreparedPilotCase(
            case_root=staging,
            source_run=request.source_run,
            node=request.node,
            attempt_id=attempt.attempt_id,
            input_sha256=seeded.input_sha256,
            task_path=staging / "PILOT_TASK.md",
        )


def prepare_case_sync(builder: PilotCaseBuilder, request: PilotCaseRequest) -> PreparedPilotCase:
    async def execute() -> PreparedPilotCase:
        try:
            return await builder.prepare(request)
        finally:
            await builder.aclose()

    return asyncio.run(execute())


def _pilot_identity(
    request: PilotCaseRequest,
) -> tuple[CodexWorkflowVersion, ResearchLane, Path]:
    lane = request.research_lane
    if lane is ResearchLane.LEGACY_DOCUMENT1:
        if request.node in {CodexD1Node.C5, CodexD1Node.O4}:
            raise ValueError("C5/O4 require an explicit new research lane")
        return CODEX_D1_WORKFLOW_VERSION, lane, Path("codex_assets/document1_v2")
    if lane is ResearchLane.GLOBAL_RESEARCH:
        allowed = {
            CodexD1Node.C4_PRE_SCAN,
            CodexD1Node.C1,
            CodexD1Node.C3,
            CodexD1Node.C5,
            CodexD1Node.C4_ENRICHMENT,
        }
        if request.node not in allowed:
            raise ValueError("node does not belong to the Global Research lane")
        return (
            CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
            lane,
            Path("codex_assets/global_research_v1"),
        )
    if lane is ResearchLane.MARKET_SITUATION_RESEARCH:
        if request.node not in {CodexD1Node.C2, CodexD1Node.O4}:
            raise ValueError("node does not belong to the Market Situation lane")
        return (
            CODEX_MARKET_SITUATION_WORKFLOW_VERSION,
            lane,
            Path("codex_assets/market_situation_v1"),
        )
    raise ValueError(f"unsupported Pilot research lane: {lane}")


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
                try:
                    target.relative_to(destination.resolve())
                except ValueError as exc:
                    raise ValueError(f"worker export escaped case: {member.filename}") from exc
            archive.extractall(destination)
    finally:
        archive_path.unlink(missing_ok=True)


def _latest_successful_attempt(root: Path, node: CodexD1Node) -> WorkerJob:
    values: list[WorkerJob] = []
    for path in (root / "audit" / "jobs").glob("*.json"):
        try:
            job = WorkerJob.model_validate_json(path.read_text(encoding="utf-8"))
            task_path = root / "attempts" / job.attempt_id / "input" / "task.json"
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if job.status == "succeeded" and task.get("node") == node.value:
            values.append(job)
    if not values:
        raise ValueError(f"source run has no successful {node.value} attempt")
    return max(values, key=lambda item: (item.updated_at, item.created_at, item.attempt_id))


def _remove_other_attempts(root: Path, attempt_id: str) -> None:
    attempts = root / "attempts"
    for path in attempts.iterdir() if attempts.is_dir() else ():
        if path.is_dir() and path.name != attempt_id:
            shutil.rmtree(path)
    jobs = root / "audit" / "jobs"
    if jobs.is_dir():
        shutil.rmtree(jobs)


def _clear_current_node_outputs(root: Path, node: CodexD1Node, attempt_id: str) -> None:
    attempt_output = root / "attempts" / attempt_id / "output"
    shutil.rmtree(attempt_output, ignore_errors=True)
    attempt_output.mkdir(parents=True, exist_ok=True)
    node_artifacts = root / "artifacts" / node.value
    if node_artifacts.is_dir():
        shutil.rmtree(node_artifacts)
    for path in (root / "artifacts").rglob("*") if (root / "artifacts").is_dir() else ():
        if path.is_file() and attempt_id in path.as_posix():
            path.unlink()
    current_catalog = root / "context" / "data_tool_catalog" / f"{attempt_id}.md"
    if current_catalog.is_file():
        current_catalog.chmod(stat.S_IWRITE)
        current_catalog.unlink()


def _assert_control_store(root: Path, run_id: str, attempt_id: str) -> None:
    database = root / ".control" / run_id / attempt_id / "observations.sqlite3"
    if not database.is_file():
        raise ValueError("worker export did not contain the attempt Observation Store")


def _make_inputs_read_only(root: Path, attempt_id: str) -> None:
    protected = [
        root / ".codex",
        root / "context",
        root / "artifacts",
        root / "attempts" / attempt_id / "input",
    ]
    for directory in protected:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
    for path in (root / "case_manifest.json", root / "PILOT_TASK.md"):
        if path.is_file():
            path.chmod(stat.S_IREAD)


def _find_value(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return cast(object, value[key])
        for child in value.values():
            found = _find_value(child, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
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


def _c1_quality_payload(payload: dict[str, object]) -> dict[str, object]:
    payload["research_brief"] = (
        "C1 research-quality Pilot. Produce a decision-useful, evidence-led company "
        "fundamental report that fully follows the injected six-section fundamental-research "
        "skill. Do not optimize for brevity and do not inherit functional-smoke coverage "
        "limits. Use governed Data MCP evidence first; when the governed catalog is insufficient, "
        "use native web research and persist every cited public source through Source Capture MCP. "
        "Compare the latest principal reporting cycle with appropriate prior periods, separate "
        "reported facts, management expectations, sell-side expectations and C1 inference, and "
        "complete drivers, transmission chains, candidate fundamental-factor gaps and Unknowns."
    )
    base = payload.get("base_context")
    quality_context = dict(base) if isinstance(base, dict) else {}
    quality_context["smoke_mode"] = False
    quality_context["quality_acceptance"] = True
    quality_context.pop("horizontal_collection", None)
    payload["base_context"] = quality_context
    return payload


QUALITY_PILOT_NODES = frozenset(
    {
        CodexD1Node.C1,
        CodexD1Node.C3,
        CodexD1Node.C2,
        CodexD1Node.C5,
        CodexD1Node.O4,
        CodexD1Node.C4_PRE_SCAN,
        CodexD1Node.C4_ENRICHMENT,
        # Historical profiles remain callable only with lane=legacy_document1.
        CodexD1Node.O4_A,
        CodexD1Node.O4_B,
        CodexD1Node.C4_FINALIZATION,
    }
)


def _quality_payload(
    node: CodexD1Node,
    payload: dict[str, object],
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
) -> dict[str, object]:
    if node is CodexD1Node.C1:
        return _c1_quality_payload(payload)
    briefs = {
        CodexD1Node.C3: (
            "C3 research-quality Pilot. Produce a decision-useful, evidence-led industry "
            "and value-chain report that fully follows the injected six-section "
            "industry-research skill. Do not optimize for brevity or inherit functional-"
            "smoke coverage limits. Select the target's material external lines, test "
            "actor words against actions and constraints, reconstruct allocation and "
            "target transmission, and preserve milestone proof boundaries and Unknowns."
        ),
        CodexD1Node.C5: (
            "C5 research-quality Pilot. Produce the five-section market-implied "
            "expectations report under the market-implied-expectations skill, using Chinese "
            "section titles and table headers. Use frozen C1/C3 inputs as economic starting "
            "points without redoing them; do not read or inherit Market Situation O4. "
            "Concentrate on recent "
            "repricing drivers and the business, financial, and duration conditions current "
            "price requires. When evidence is sparse, prefer a shorter conditional judgment "
            "to availability, provider, confidence, or identifiability audits."
        ),
        CodexD1Node.O4_A: (
            "O4-A legacy research-quality Pilot. Produce the full market-implied "
            "expectations report under the injected skill without functional-smoke limits."
        ),
        CodexD1Node.O4_B: (
            "O4-B legacy research-quality Pilot. Produce the full price and market-trace "
            "report under the injected legacy contract without functional-smoke limits."
        ),
        CodexD1Node.C4_PRE_SCAN: (
            "C4 quality Pilot, pre-scan turn. Fully execute the entity-map refresh and "
            "direct-future-node scan required by the injected skill. Quality means precise "
            "entity identity, direct target linkage, reliable sources, normalized time and "
            "valid five-field public artifacts; it does not mean producing a long report."
        ),
        CodexD1Node.C4_ENRICHMENT: (
            "C4 quality Pilot, enrichment turn. Use the frozen pre-scan and C1/C3/C5 research "
            "only to narrow searches, then validate, update, add and deduplicate directly "
            "observable future matters under the injected skill and five-field contract. "
            "Return the complete merged C4 snapshot; no later finalization turn exists."
        ),
        CodexD1Node.C4_FINALIZATION: (
            "C4 legacy quality Pilot, finalization turn. Validate and return the complete "
            "five-field entity-relation and future-node snapshot required by the injected "
            "legacy Document 1 contract."
        ),
        CodexD1Node.C2: (
            "C2 research-quality Pilot. Produce a current, evidence-led Market Situation "
            "macro report covering growth, inflation, policy, rates, credit, liquidity, "
            "currency, broad-market transmission, and material uncertainty."
        ),
        CodexD1Node.O4: (
            "O4 research-quality Pilot. Produce an evidence-led Market Situation price report "
            "covering current snapshot, multi-window returns, repricing intervals, relative "
            "performance, volatility, volume, liquidity, positioning and data-quality limits."
        ),
    }
    if node is CodexD1Node.C4_ENRICHMENT and research_lane is ResearchLane.LEGACY_DOCUMENT1:
        briefs[node] = (
            "C4 legacy quality Pilot, enrichment turn. Use the frozen pre-scan and C1/C3 "
            "research to add supported future matters under the injected legacy contract; "
            "return the structured enrichment expected by its later legacy merge turn."
        )
    payload["research_brief"] = briefs[node]
    base = payload.get("base_context")
    quality_context = dict(base) if isinstance(base, dict) else {}
    quality_context["smoke_mode"] = False
    quality_context["quality_acceptance"] = True
    quality_context.pop("horizontal_collection", None)
    payload["base_context"] = quality_context
    _sanitize_unverified_c4_pre_scan(payload)
    return payload


def _load_manual_upstream(
    node: CodexD1Node,
    upstream_dir: Path | None,
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
) -> ManualUpstreamImport | None:
    if upstream_dir is None:
        return None
    source_dir = upstream_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"manual upstream directory does not exist: {source_dir}")
    files: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    injected_hashes: dict[str, str] = {}
    upstream_files = (
        LEGACY_MANUAL_UPSTREAM_FILES
        if research_lane is ResearchLane.LEGACY_DOCUMENT1
        else GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES
    )
    for name in upstream_files.get(node, ()):
        path = source_dir / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if not raw or not raw.strip():
            raise ValueError(f"manual upstream file is empty: {name}")
        if len(raw) > _MAX_MANUAL_UPSTREAM_BYTES:
            raise ValueError(f"manual upstream file exceeds 2 MiB: {name}")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"manual upstream file is not UTF-8: {name}") from exc
        if name.endswith(".json"):
            try:
                parsed = json.loads(text)
                validated = NodeOutput.model_validate(parsed).model_dump(mode="json", by_alias=True)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"manual upstream NodeOutput JSON is invalid: {name}") from exc
            validated["observation_candidates"] = []
            warnings = list(validated.get("warnings") or [])
            warnings.append("manual_pilot_override_no_evidence_rebind")
            validated["warnings"] = list(dict.fromkeys(warnings))
            injected = json.dumps(_sanitize_manual_value(validated), ensure_ascii=False, indent=2)
        else:
            injected = _MANUAL_CITATION.sub(_MANUAL_CITATION_REPLACEMENT, text)
        files[name] = injected
        source_hashes[name] = hashlib.sha256(raw).hexdigest()
        injected_hashes[name] = hashlib.sha256(injected.encode("utf-8")).hexdigest()
    if not files:
        return None
    return ManualUpstreamImport(
        source_dir=source_dir,
        files=files,
        source_sha256=source_hashes,
        injected_sha256=injected_hashes,
    )


def _apply_manual_upstream(
    node: CodexD1Node,
    payload: dict[str, object],
    files: dict[str, str],
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
) -> dict[str, object]:
    if research_lane is ResearchLane.LEGACY_DOCUMENT1 and node is CodexD1Node.O4_A:
        payload = _isolate_o4_a_payload(payload)

    def structured(name: str) -> dict[str, Any]:
        parsed = json.loads(files[name])
        if not isinstance(parsed, dict):
            raise ValueError(f"manual upstream structured output is not an object: {name}")
        return cast(dict[str, Any], parsed)

    def report(name: str) -> dict[str, object]:
        return {
            "status": "completed",
            "summary": f"Manually injected Pilot upstream report: {name}",
            "report_markdown": files[name],
            "warnings": ["manual_pilot_override_no_evidence_rebind"],
            "observation_candidates": [],
            "entity_relations": [],
            "future_nodes": [],
            "metadata": {},
        }

    if research_lane is ResearchLane.LEGACY_DOCUMENT1 and UNIFIED_C4_UPSTREAM_FILE in files:
        c4_output = structured(UNIFIED_C4_UPSTREAM_FILE)
        if node is CodexD1Node.C4_FINALIZATION:
            payload["enriched_c4"] = c4_output
        elif node is CodexD1Node.O4_A:
            payload["known_future_nodes"] = list(c4_output.get("future_nodes") or [])
        else:
            payload["c4_pre_scan"] = c4_output
    elif C4_PRE_SCAN_UPSTREAM_FILE in files:
        c4_output = structured(C4_PRE_SCAN_UPSTREAM_FILE)
        if node in {CodexD1Node.C1, CodexD1Node.C3}:
            c4_output["future_nodes"] = []
        payload["c4_pre_scan"] = c4_output
    if node is CodexD1Node.C4_ENRICHMENT:
        if "c1.md" in files:
            payload["c1_report"] = report("c1.md")
        if "c3.md" in files:
            payload["c3_report"] = report("c3.md")
        if "c5.md" in files:
            payload["c5_report"] = report("c5.md")
    elif node in {CodexD1Node.C5, CodexD1Node.O4_A}:
        for key in ("c1", "c3"):
            name = f"{key}.md"
            if name in files:
                payload[key] = report(name)
    if any(name in files for name in ("c1.md", "c3.md")):
        payload["agent_observations"] = []
    payload["manual_upstream_pilot_override"] = {
        "files": sorted(files),
        "precedence": "manual_over_source_run",
        "citation_policy": "context_only_reverify",
    }
    return payload


def _isolate_o4_a_payload(payload: dict[str, object]) -> dict[str, object]:
    for key in ("o4_b", "c2", "known_future_nodes"):
        payload.pop(key, None)
    observations = payload.get("agent_observations")
    if isinstance(observations, list):
        payload["agent_observations"] = [
            item
            for item in observations
            if isinstance(item, dict) and item.get("origin_node") in {"c1", "c3"}
        ]
    return payload


def _sanitize_manual_value(value: Any) -> Any:
    if isinstance(value, str):
        return _MANUAL_CITATION.sub(_MANUAL_CITATION_REPLACEMENT, value)
    if isinstance(value, list):
        return [_sanitize_manual_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_manual_value(item) for key, item in value.items()}
    return value


def _sanitize_unverified_c4_pre_scan(payload: dict[str, object]) -> None:
    pre_scan = payload.get("c4_pre_scan")
    if not isinstance(pre_scan, dict):
        return
    rendered = json.dumps(pre_scan, ensure_ascii=False, default=str).lower()
    observations = pre_scan.get("observation_candidates")
    has_observations = isinstance(observations, list) and bool(observations)
    unverified_markers = (
        "未进行外部研究",
        "待补充经验证来源",
        "来源及发布日期待后续补充",
        "待后续研究确认",
        "待补充（预扫描）",
        "unverified",
        "source pending",
    )
    mojibake_markers = ("\ufffd", "鈥?", "Ã¢", "â€™", "æœª", "å¾…")
    if has_observations and not any(marker in rendered for marker in mojibake_markers):
        return
    if not any(marker.lower() in rendered for marker in unverified_markers + mojibake_markers):
        return
    payload["c4_pre_scan"] = {
        "status": "unavailable",
        "summary": None,
        "report_markdown": "",
        "warnings": [
            "C4 pre-scan contained no source-backed observations or failed the UTF-8 "
            "readability gate; placeholder relations and future nodes were removed."
        ],
        "observation_candidates": [],
        "entity_relations": [],
        "future_nodes": [],
        "metadata": {
            "availability": "unavailable",
            "reason": "no_verified_source_backing_or_text_encoding_failure",
            "text_encoding": "utf-8",
        },
    }


def _probe_for_node(node: CodexD1Node, enabled: list[str]) -> tuple[str, dict[str, object]]:
    preferences = {
        CodexD1Node.C1: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.C2: (
            "fred_series_observations",
            {"series_ids": ["FEDFUNDS"], "limit": 2},
        ),
        CodexD1Node.C3: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.C4_PRE_SCAN: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.C4_ENRICHMENT: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.O4_B: ("market_quote_snapshot", {}),
        CodexD1Node.O4_A: ("market_quote_snapshot", {}),
        CodexD1Node.C5: ("market_quote_snapshot", {}),
        CodexD1Node.O4: ("market_quote_snapshot", {}),
    }
    preferred = preferences.get(node)
    if preferred is not None and preferred[0] in enabled:
        return preferred
    semantic = next(name for name in enabled if name not in {GUIDE_TOOL_NAME, READ_TOOL_NAME})
    return semantic, {}


def _validate_identifier(value: str, label: str) -> None:
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(f"invalid {label}")
