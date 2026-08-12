"""Generate an isolated Codex App Pilot case from a successful worker run."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.schema import CodexD1Node, utc_now
from doxagent.codex_worker.schema import WorkerJob
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.data_server import GUIDE_TOOL_NAME, READ_TOOL_NAME
from doxagent.pilot.case_workspace import PilotCaseWorkspace
from doxagent.pilot.templates import render_config, render_task
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document1.attempt_bundle import AttemptBundleSeeder
from doxagent.workflows.codex_document1.node_runner import role_for_node


@dataclass(frozen=True)
class PilotCaseRequest:
    source_run: str
    node: CodexD1Node
    case_id: str
    capability_hours: int = 8


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
        if not 1 <= request.capability_hours <= 24:
            raise ValueError("capability_hours must be between 1 and 24")
        case_root = self.cases_root / request.node.value / request.case_id
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
            self.repo_root / "codex_assets" / "document1_v2",
        )
        seeded = await seeder.seed(
            run_id=request.source_run,
            node=request.node,
            attempt_id=attempt.attempt_id,
            context_payload=payload,
            horizontal=horizontal,
        )
        role = role_for_node(request.node)
        policy = DataToolPolicyRegistry()
        canonical_tools = sorted(policy.allowed_tools(request.node, role))
        contracts = build_data_tool_contracts(default_real_tool_registry(self.settings))
        enabled_tools = [GUIDE_TOOL_NAME, READ_TOOL_NAME]
        enabled_tools.extend(
            contract.mcp_name
            for tool_id in canonical_tools
            if (contract := contracts.get(tool_id)) is not None and contract.exposed
        )
        codec = DataCapabilityCodec(self.settings.codex_capability_secret or "")
        capability = codec.issue(
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
            "schema_version": "codex-d1-pilot-case-v1",
            "case_id": request.case_id,
            "source_run_id": request.source_run,
            "run_id": request.source_run,
            "node": request.node.value,
            "agent_role": role.value,
            "attempt_id": attempt.attempt_id,
            "ticker": ticker,
            "cutoff_at": cutoff.isoformat(),
            "input_sha256": seeded.input_sha256,
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
                run_id=request.source_run,
                attempt_id=attempt.attempt_id,
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
            return value[key]
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


def _probe_for_node(node: CodexD1Node, enabled: list[str]) -> tuple[str, dict[str, object]]:
    preferences = {
        CodexD1Node.C1: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.C2: (
            "fred_series_observations",
            {"series_ids": ["FEDFUNDS"], "limit": 2},
        ),
        CodexD1Node.C3: ("sec_issuer_filings", {"forms": ["10-K"], "limit": 1}),
        CodexD1Node.O4_B: ("market_quote_snapshot", {}),
        CodexD1Node.O4_A: ("market_quote_snapshot", {}),
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
