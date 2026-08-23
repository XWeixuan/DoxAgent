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

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    CodexAgentRole,
    CodexD2AgentRole,
    CodexD2Node,
    CodexResearchAgentRole,
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


@dataclass(frozen=True)
class Document2PilotCaseRequest:
    source_workspace_run: str
    node: CodexD2Node
    case_id: str
    capability_hours: int = DEFAULT_PILOT_CAPABILITY_HOURS


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

    async def prepare(
        self, request: Document2PilotCaseRequest
    ) -> PreparedDocument2PilotCase:
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
            prepared = self._materialize(staging, case_root, request, job)
            os.replace(staging, case_root)
        return PreparedDocument2PilotCase(
            case_root=case_root,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=prepared.attempt_id,
            input_sha256=prepared.input_sha256,
            task_path=case_root / "PILOT_TASK.md",
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _materialize(
        self,
        staging: Path,
        installed_root: Path,
        request: Document2PilotCaseRequest,
        job: WorkerJob,
    ) -> PreparedDocument2PilotCase:
        attempt_root = staging / "attempts" / job.attempt_id
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
        _keep_only_attempt(staging, job.attempt_id)
        shutil.rmtree(attempt_root / "output", ignore_errors=True)
        (attempt_root / "output").mkdir(parents=True)
        (attempt_root / "audit").mkdir(parents=True, exist_ok=True)
        (attempt_root / "audit" / "pilot_issues.md").write_text("", encoding="utf-8")
        snapshot = staging / "artifacts" / "snapshots" / f"{job.attempt_id}.json"
        snapshot.unlink(missing_ok=True)
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
            node_attempt_id=job.attempt_id,
            agent_role=role,
            ticker=ticker,
            cutoff_at=cutoff,
            enabled_tool_ids=canonical_tools,
            ttl_seconds=request.capability_hours * 3600,
            pilot_case_id=request.case_id,
        )
        input_sha = _tree_hash(input_root)
        manifest = {
            "schema_version": "codex-research-pilot-case-v2",
            "case_id": request.case_id,
            "profile": "quality",
            "source_run_id": request.source_workspace_run,
            "run_id": request.source_workspace_run,
            "node": request.node.value,
            "workflow_version": CODEX_DOCUMENT2_WORKFLOW_VERSION,
            "research_lane": ResearchLane.DOCUMENT2.value,
            "agent_role": role.value,
            "node_attempt_id": job.attempt_id,
            "attempt_id": job.attempt_id,
            "ticker": ticker,
            "cutoff_at": cutoff.isoformat(),
            "input_sha256": input_sha,
            "enabled_canonical_tools": canonical_tools,
            "enabled_data_tools": enabled_tools,
            "capability_expires_after_hours": request.capability_hours,
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
                attempt_id=job.attempt_id,
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
                attempt_id=job.attempt_id,
            ),
            encoding="utf-8",
        )
        _protect_inputs(staging, job.attempt_id)
        return PreparedDocument2PilotCase(
            case_root=staging,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=job.attempt_id,
            input_sha256=input_sha,
            task_path=staging / "PILOT_TASK.md",
        )


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
        raise ValueError(f"source workspace has no successful {node.value} attempt")
    return max(candidates, key=lambda item: (item.updated_at, item.attempt_id))


def _safe_extract(raw: bytes, destination: Path) -> None:
    archive_path = destination.parent / "workspace.zip"
    archive_path.write_bytes(raw)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                pure = PurePosixPath(member.filename.replace("\\", "/"))
                if pure.is_absolute() or not pure.parts or any(
                    part in {"", ".", ".."} for part in pure.parts
                ) or ":" in pure.parts[0]:
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
