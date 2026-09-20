"""Restartable controller around the existing initialization workflow."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.schema import InitializationError, RunStatus
from doxagent.v2_control.repository import ControlError, ControlRepository

from .containers import ContainerSpec, DockerRuntime
from .context import build_failure_context, write_context
from .git_workspace import GitWorkspaceManager, IncidentWorkspace
from .issues import record_agent_report, record_execution, render_to
from .repository import RepairRepository
from .schema import (
    IncidentPhase,
    IncidentStatus,
    RepairAgentReport,
    RepairIncident,
    RoundStatus,
)


class CandidateVerificationFailed(RuntimeError):
    """A candidate failed deterministic tests and must return to the same thread."""


class Guardian:
    """Advance each incident by one idempotent phase per tick."""

    def __init__(
        self,
        initialization: InitializationRepository,
        control: ControlRepository,
        *,
        repair_root: Path,
        source_repository: str,
        production_container: str,
        agent_image: str,
        codex_auth_file: Path | None = None,
        docker: DockerRuntime | None = None,
        workspaces: GitWorkspaceManager | None = None,
    ) -> None:
        self.initialization = initialization
        self.repairs = RepairRepository(initialization)
        self.control = control
        self.root = repair_root.resolve()
        self.production_container = production_container
        self.agent_image = agent_image
        self.codex_auth_file = (codex_auth_file or (self.root / "auth.json")).resolve()
        self.docker = docker or DockerRuntime()
        self.workspaces = workspaces or GitWorkspaceManager(self.root, source_repository)
        self.issue_path = self.root / "init-issue.md"

    def initialize_watermark(self) -> datetime:
        path = self.root / "guardian-watermark.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(data["created_after"])
        watermark = datetime.now(UTC)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"created_after": watermark.isoformat()}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return watermark

    def adopt(self, initialization_id: str) -> RepairIncident:
        run = self.initialization.get(initialization_id)
        metadata = self._production_source()
        return self.repairs.open_incident(
            initialization_id,
            expected_state_seq=run.state_seq,
            source_image_id=metadata["image_id"],
            source_revision=metadata["revision"],
            source_hash=metadata.get("source_hash"),
        )

    def tick(self, *, created_after: datetime | None = None) -> None:
        watermark = created_after or self.initialize_watermark()
        source: dict[str, str] | None = None
        for initialization_id in self.repairs.failed_candidates(created_after=watermark):
            try:
                source = source or self._production_source()
                run = self.initialization.get(initialization_id)
                self.repairs.open_incident(
                    initialization_id,
                    expected_state_seq=run.state_seq,
                    source_image_id=source["image_id"],
                    source_revision=source["revision"],
                    source_hash=source.get("source_hash"),
                )
            except (InitializationError, RuntimeError, ValueError) as exc:
                # Candidate state may have moved between scan and open; next tick re-evaluates it.
                if self.repairs.for_initialization(initialization_id) is not None:
                    incident = self.repairs.for_initialization(initialization_id)
                    assert incident is not None
                    self.repairs.update_incident(incident.incident_id, last_error=str(exc)[:1000])
        for incident in self.repairs.incidents(include_terminal=False):
            try:
                self._advance(incident)
            except Exception as exc:
                self.repairs.update_incident(incident.incident_id, last_error=str(exc)[:1000])
        render_to(self.repairs, self.issue_path)

    def _advance(self, incident: RepairIncident) -> None:
        incident = self.repairs.get(incident.incident_id)
        run = self.initialization.get(incident.initialization_id)
        if incident.status is not IncidentStatus.ACTIVE:
            return
        if incident.phase is IncidentPhase.EXECUTE:
            self._collect_executor(incident)
            return
        if run.status is RunStatus.SUCCEEDED:
            self.repairs.update_incident(
                incident.incident_id,
                status=IncidentStatus.SUCCEEDED,
                phase=IncidentPhase.RECORD,
                last_error=None,
            )
            return
        if incident.phase is IncidentPhase.CONTEXT:
            control_state = self.control.get(incident.ticker)
            if control_state is None:
                raise RuntimeError("V2 control state is unavailable for repair initialization")
            if control_state.get("removed") or (
                control_state.get("initialization_id") != incident.initialization_id
            ):
                self.repairs.update_incident(
                    incident.incident_id,
                    status=IncidentStatus.CANCELLED,
                    phase=IncidentPhase.RECORD,
                    last_error="REPAIR_CONTROL_OWNERSHIP_LOST",
                )
                return
            self._start_agent(incident, run.state_seq)
        elif incident.phase is IncidentPhase.CODING:
            self._collect_agent(incident)
        elif incident.phase is IncidentPhase.QUEUE:
            self._start_executor(incident)

    def _workspace(self, incident: RepairIncident) -> IncidentWorkspace:
        if not incident.source_revision:
            raise RuntimeError("repair incident has no verified production source revision")
        if not incident.source_hash:
            raise RuntimeError("repair incident has no verified production source hash")
        workspace = self.workspaces.prepare(incident.incident_id, incident.source_revision)
        actual_hash = self.workspaces.revision_sha256(incident.source_revision)
        if actual_hash != incident.source_hash:
            raise RuntimeError("deployed source hash does not match the fetched source revision")
        return workspace

    def _start_agent(self, incident: RepairIncident, state_seq: int) -> None:
        if not self.codex_auth_file.is_file() or self.codex_auth_file.stat().st_size == 0:
            raise RuntimeError(
                "repair Codex auth template is missing; provision the configured auth.json"
            )
        repair_round = self.repairs.start_round(incident.incident_id, failed_state_seq=state_seq)
        workspace = self._workspace(incident)
        self.repairs.update_incident(
            incident.incident_id,
            worktree_path=str(workspace.path),
            branch_name=workspace.branch,
            phase=IncidentPhase.CODING,
            last_error=None,
        )
        local_root = workspace.path / ".initialization-repair" / repair_round.round_id
        context_path = local_root / "failure-context.json"
        write_context(
            context_path,
            build_failure_context(self.initialization, self.repairs, incident, repair_round),
        )
        name = f"doxagent-repair-agent-{repair_round.round_id}"
        code_home = self.root / "incidents" / incident.incident_id / "codex-home"
        code_home.mkdir(parents=True, exist_ok=True)
        incident_auth = code_home / "auth.json"
        if not incident_auth.is_file():
            shutil.copy2(self.codex_auth_file, incident_auth)
            incident_auth.chmod(0o600)
        command = [
            "doxagent-initialization-repair",
            "agent",
            "--incident-id",
            incident.incident_id,
            "--round-id",
            repair_round.round_id,
            "--worktree",
            str(workspace.path),
            "--context",
            str(context_path),
            "--receipt",
            str(local_root / "agent-receipt.json"),
            "--report",
            str(local_root / "agent-report.json"),
            "--codex-home",
            str(code_home),
        ]
        if incident.thread_id:
            command.extend(["--thread-id", incident.thread_id])
        try:
            state = self.docker.ensure_started(
                ContainerSpec(
                    name=name,
                    image=self.agent_image,
                    command=command,
                    labels=self._labels(incident, repair_round.round_id, "agent"),
                    volumes=[
                        f"{self.workspaces.bare}:{self.workspaces.bare}:ro",
                        f"{workspace.path}:{workspace.path}:rw",
                        f"{code_home}:{code_home}:rw",
                    ],
                    workdir=str(workspace.path),
                    memory="15g",
                    memory_swap="16g",
                    pids_limit=4096,
                )
            )
        except Exception:
            self.repairs.update_incident(incident.incident_id, phase=IncidentPhase.CONTEXT)
            raise
        self.repairs.update_round(
            repair_round.round_id,
            status=RoundStatus.AGENT_RUNNING,
            agent_container=state.identity,
        )

    def _collect_agent(self, incident: RepairIncident) -> None:
        if not incident.current_round_id:
            raise RuntimeError("CODING incident has no current round")
        repair_round = self.repairs.round(incident.current_round_id)
        workspace = self._workspace(incident)
        local_root = workspace.path / ".initialization-repair" / repair_round.round_id
        if repair_round.status is RoundStatus.VERIFIED:
            self._queue_verified_round(incident, repair_round.round_id)
            return
        receipt_path = local_root / "agent-receipt.json"
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("incident_id") != incident.incident_id:
                raise RuntimeError("agent receipt incident mismatch")
            turn_ids = list(repair_round.thread_turn_ids)
            if receipt.get("turn_id"):
                turn_ids = list(dict.fromkeys([*turn_ids, str(receipt["turn_id"])]))
            self.repairs.update_incident(incident.incident_id, thread_id=str(receipt["thread_id"]))
            self.repairs.update_round(repair_round.round_id, thread_turn_ids=turn_ids)
            repair_round = self.repairs.round(repair_round.round_id)
        name = f"doxagent-repair-agent-{repair_round.round_id}"
        state = self.docker.state(name)
        if state is None or state.status == "created":
            run = self.initialization.get(incident.initialization_id)
            self._start_agent(incident, run.state_seq)
            return
        if state.status == "running":
            return
        log_path = local_root / "agent.log"
        log_path.write_text(self.docker.logs(name)[-200_000:], encoding="utf-8")
        if state.exit_code != 0:
            payload = dict(repair_round.payload)
            raw_retries = payload.get("agent_infra_retries", 0)
            retries = (raw_retries if isinstance(raw_retries, int) else 0) + 1
            payload["agent_infra_retries"] = retries
            self.repairs.update_round(repair_round.round_id, payload=payload)
            self.docker.remove(name)
            if retries >= 3:
                self.repairs.update_incident(
                    incident.incident_id,
                    status=IncidentStatus.HUMAN_REQUIRED,
                    phase=IncidentPhase.RECORD,
                    last_error=f"REPAIR_AGENT_INFRA_RETRIES_EXHAUSTED:{state.exit_code}",
                )
            else:
                self.repairs.update_incident(
                    incident.incident_id,
                    phase=IncidentPhase.CONTEXT,
                    last_error=f"REPAIR_AGENT_EXITED:{state.exit_code}",
                )
            return
        report_path = local_root / "agent-report.json"
        if not report_path.is_file():
            raise RuntimeError("repair agent exited without report")
        report = RepairAgentReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        changed = {
            path.replace("\\", "/")
            for path in self.workspaces.changed_files(workspace)
            if not path.replace("\\", "/").startswith(".initialization-repair/")
        }
        if not changed:
            raise RuntimeError("repair agent produced no candidate code or prompt change")
        undocumented = changed - set(report.changed_files)
        if undocumented:
            raise RuntimeError(
                "repair report omitted changed files: " + ",".join(sorted(undocumented))
            )
        excluded = [
            path
            for path in changed
            if path.startswith("src/doxagent/codex_worker/")
            or "/mcp/" in path
            or Path(path).name.startswith("mcp_")
            or Path(path).name.endswith("_mcp.py")
        ]
        if excluded:
            raise RuntimeError(
                "repair changed excluded Worker/MCP implementation: " + ",".join(sorted(excluded))
            )
        try:
            verification = self._validate_candidate(workspace, repair_round.round_id, local_root)
        except CandidateVerificationFailed as exc:
            self._request_verification_followup(
                incident,
                repair_round.round_id,
                local_root,
                turn_id=repair_round.thread_turn_ids[-1] if repair_round.thread_turn_ids else None,
                error=str(exc),
            )
            self.docker.remove(name)
            return
        if verification is None:
            return
        commit = self.workspaces.commit(
            workspace,
            f"fix(initialization): repair {incident.incident_id} round {repair_round.seq}",
        )
        source_hash = self.workspaces.source_sha256(workspace)
        image_tag = f"doxagent-init-repair:{repair_round.round_id}"
        image_id = self.docker.build(
            context=workspace.path,
            dockerfile=workspace.path / "Dockerfile.v2",
            tag=image_tag,
            build_args={
                "DOXAGENT_BUILD_COMMIT": commit,
                "DOXAGENT_SOURCE_SHA256": source_hash,
            },
            labels={
                "org.opencontainers.image.revision": commit,
                "doxagent.source.sha256": source_hash,
                "doxagent.repair.incident": incident.incident_id,
                "doxagent.repair.round": repair_round.round_id,
            },
        )
        current = self.repairs.get(incident.incident_id)
        current_round = self.repairs.round(repair_round.round_id)
        turn_id = current_round.thread_turn_ids[-1] if current_round.thread_turn_ids else None
        record_agent_report(
            self.repairs,
            entry_id=f"{repair_round.round_id}:agent",
            incident_id=incident.incident_id,
            round_id=repair_round.round_id,
            report=report,
            node_ordinals=repair_round.node_ordinals,
            ticker=incident.ticker,
            initialization_id=incident.initialization_id,
            thread_id=current.thread_id,
            turn_id=turn_id,
            commit=commit,
            image_id=image_id,
        )
        self.repairs.update_round(
            repair_round.round_id,
            status=RoundStatus.VERIFIED,
            repair_commit=commit,
            image_id=image_id,
            result=image_tag,
            tests=[verification],
        )
        self.docker.remove(name)
        self._queue_verified_round(incident, repair_round.round_id)

    def _queue_verified_round(self, incident: RepairIncident, round_id: str) -> None:
        repair_round = self.repairs.round(round_id)
        if repair_round.status is not RoundStatus.VERIFIED:
            raise RuntimeError("repair round is not ready to queue")
        control_state = self.control.get(incident.ticker)
        if (
            control_state is None
            or control_state.get("initialization_id") != incident.initialization_id
        ):
            raise RuntimeError("V2 control no longer points to repair initialization")
        if not control_state.get("initialization_failed"):
            return
        try:
            operation = self.control.submit_repair_resume(
                incident.ticker,
                incident.initialization_id,
                incident.incident_id,
                repair_round.round_id,
                expected=str(control_state["revision"]),
            )
        except ControlError as exc:
            if exc.code in {"TICKER_REMOVED", "RESOURCE_NOT_FOUND"}:
                self.repairs.update_incident(
                    incident.incident_id,
                    status=IncidentStatus.CANCELLED,
                    phase=IncidentPhase.RECORD,
                    last_error=f"REPAIR_CONTROL_OWNERSHIP_LOST:{exc.code}",
                )
                return
            raise
        self.repairs.update_round(
            repair_round.round_id,
            status=RoundStatus.QUEUED,
            control_operation_id=operation["id"],
        )
        self.repairs.update_incident(
            incident.incident_id,
            phase=IncidentPhase.QUEUE,
            control_epoch=operation["epoch"],
            last_error=None,
        )

    def _validate_candidate(
        self, workspace: IncidentWorkspace, round_id: str, local_root: Path
    ) -> dict[str, object] | None:
        baseline_tests = [
            "tests/test_initialization_repair_repository.py",
            "tests/test_ticker_initialization_operations.py",
            "tests/test_ticker_initialization_substeps.py",
            "tests/test_ticker_initialization_substep_recovery.py",
            "tests/test_ticker_initialization_control.py",
            "tests/test_ticker_initialization_history.py",
            "tests/test_ticker_initialization_fault_matrix.py",
            "tests/test_ticker_initialization_activation.py",
            "tests/test_ticker_initialization_cdecr_prebuilt.py",
            "tests/test_ticker_initialization_worker_snapshots.py",
            "tests/v2_backend/test_control.py",
            "tests/v2_backend/test_initialization_projection.py",
        ]
        changed_tests = [
            path.replace("\\", "/")
            for path in self.workspaces.changed_files(workspace)
            if path.replace("\\", "/").startswith("tests/")
            and path.replace("\\", "/").endswith(".py")
        ]
        tests = list(dict.fromkeys([*baseline_tests, *changed_tests]))
        missing = [path for path in tests if not (workspace.path / path).is_file()]
        if missing:
            raise RuntimeError("candidate verification tests are missing: " + ",".join(missing))
        name = f"doxagent-repair-verify-{round_id}"
        state = self.docker.ensure_started(
            ContainerSpec(
                name=name,
                image=self.agent_image,
                command=[
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    *tests,
                ],
                labels={
                    "doxagent.repair.round": round_id,
                    "doxagent.repair.role": "verify",
                },
                environment={"PYTHONPATH": str(workspace.path / "src")},
                volumes=[f"{workspace.path}:{workspace.path}:rw"],
                workdir=str(workspace.path),
                memory="15g",
                memory_swap="16g",
                pids_limit=4096,
            )
        )
        if state.status == "running":
            return None
        log = self.docker.logs(name)[-200_000:]
        (local_root / "verification.log").write_text(log, encoding="utf-8")
        if state.exit_code != 0:
            self.docker.remove(name)
            raise CandidateVerificationFailed(
                f"candidate verification failed with exit code {state.exit_code}; "
                "read .initialization-repair/"
                f"{round_id}/verification.log and fix the failures"
            )
        self.docker.remove(name)
        return {
            "kind": "guardian_independent_pytest",
            "paths": tests,
            "exit_code": state.exit_code,
            "log": str(local_root / "verification.log"),
        }

    def _request_verification_followup(
        self,
        incident: RepairIncident,
        round_id: str,
        local_root: Path,
        *,
        turn_id: str | None,
        error: str,
    ) -> None:
        feedback = local_root / "verification-feedback.json"
        payload = {
            "incident_id": incident.incident_id,
            "round_id": round_id,
            "for_turn_id": turn_id,
            "status": "pending",
            "error": error,
            "verification_log": str(local_root / "verification.log"),
        }
        temporary = feedback.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(feedback)
        repair_round = self.repairs.round(round_id)
        round_payload = dict(repair_round.payload)
        raw_attempts = round_payload.get("verification_failures", 0)
        round_payload["verification_failures"] = (
            raw_attempts if isinstance(raw_attempts, int) else 0
        ) + 1
        self.repairs.update_round(
            round_id,
            status=RoundStatus.STARTED,
            payload=round_payload,
        )
        self.repairs.update_incident(
            incident.incident_id,
            phase=IncidentPhase.CONTEXT,
            last_error=error,
        )

    def _start_executor(self, incident: RepairIncident) -> None:
        if not incident.current_round_id:
            raise RuntimeError("QUEUE incident has no current round")
        repair_round = self.repairs.round(incident.current_round_id)
        if not repair_round.control_operation_id or not repair_round.image_id:
            raise RuntimeError("queued repair lacks control operation or image")
        operation = self.control.operation(repair_round.control_operation_id)
        if operation["state"] in {"ACCEPTED", "RUNNING"}:
            return
        if operation["state"] != "SUCCEEDED":
            error = str(operation.get("error") or "UNKNOWN")
            self.repairs.update_incident(
                incident.incident_id,
                status=(
                    IncidentStatus.CANCELLED
                    if error == "OPERATION_SUPERSEDED"
                    else IncidentStatus.HUMAN_REQUIRED
                ),
                phase=IncidentPhase.RECORD,
                last_error=f"REPAIR_CONTROL_FAILED:{error}",
            )
            return
        control_state = self.control.get(incident.ticker)
        if (
            control_state is None
            or control_state.get("initialization_id") != incident.initialization_id
            or control_state.get("removed")
            or control_state.get("epoch") != operation.get("epoch")
            or not control_state.get("initialization_allowed")
        ):
            self.repairs.update_incident(
                incident.incident_id,
                status=IncidentStatus.CANCELLED,
                phase=IncidentPhase.RECORD,
                last_error="REPAIR_CONTROL_SUPERSEDED_BEFORE_EXECUTION",
            )
            return
        template = self.docker.production_template(self.production_container)
        tag = repair_round.result
        if not tag:
            raise RuntimeError("repair candidate image tag missing")
        self.docker.verify_image(
            tag,
            expected_id=repair_round.image_id,
            labels={
                "org.opencontainers.image.revision": repair_round.repair_commit or "",
                "doxagent.repair.incident": incident.incident_id,
                "doxagent.repair.round": repair_round.round_id,
            },
        )
        name = f"doxagent-repair-exec-{repair_round.round_id}"
        execution_identity = f"repair:{repair_round.round_id}"
        executor_environment = {
            **template["environment"],
            "DOXAGENT_CDECR_EXECUTION_MODE": "REMOTE_EXECUTOR",
            "DOXAGENT_CDECR_DISPATCH_IDENTITY": execution_identity,
        }
        cdecr_state = None
        if any(
            key == "cdecr" or key.startswith("cdecr.") or ".native:" in key
            for key in repair_round.target_nodes
        ):
            cdecr_name = f"doxagent-repair-cdecr-{repair_round.round_id}"
            cdecr_state = self.docker.ensure_started(
                ContainerSpec(
                    name=cdecr_name,
                    image=tag,
                    command=[
                        "python",
                        "-m",
                        "doxagent.ticker_initialization.cdecr_executor",
                        "--once",
                        "--wait-seconds",
                        "7200",
                        "--identity",
                        execution_identity,
                    ],
                    labels=self._labels(incident, repair_round.round_id, "cdecr-executor"),
                    environment=executor_environment,
                    volumes=template["volumes"],
                    networks=template["networks"],
                    user=template["user"],
                    memory="6g",
                    memory_swap="7g",
                    pids_limit=512,
                )
            )
        state = self.docker.ensure_started(
            ContainerSpec(
                name=name,
                image=tag,
                command=[
                    "doxagent-ticker-init",
                    "--database",
                    str(self.initialization.path),
                    "repair-execute",
                    "--initialization-id",
                    incident.initialization_id,
                    "--incident-id",
                    incident.incident_id,
                    "--round-id",
                    repair_round.round_id,
                ],
                labels=self._labels(incident, repair_round.round_id, "executor"),
                environment=executor_environment,
                volumes=template["volumes"],
                networks=template["networks"],
                user=template["user"],
                memory="4g",
                memory_swap="5g",
                pids_limit=256,
            )
        )
        self.repairs.update_round(
            repair_round.round_id,
            status=RoundStatus.EXECUTING,
            executor_container=state.identity,
            cdecr_executor_container=cdecr_state.identity if cdecr_state else None,
        )
        self.repairs.update_incident(incident.incident_id, phase=IncidentPhase.EXECUTE)

    def _collect_executor(self, incident: RepairIncident) -> None:
        if not incident.current_round_id:
            raise RuntimeError("EXECUTE incident has no current round")
        repair_round = self.repairs.round(incident.current_round_id)
        name = f"doxagent-repair-exec-{repair_round.round_id}"
        state = self.docker.state(name)
        if state is None:
            self.repairs.update_incident(
                incident.incident_id,
                status=IncidentStatus.HUMAN_REQUIRED,
                phase=IncidentPhase.RECORD,
                last_error="EXECUTOR_CONTAINER_MISSING",
            )
            return
        if state.status == "running":
            return
        if repair_round.cdecr_executor_container:
            cdecr_name = f"doxagent-repair-cdecr-{repair_round.round_id}"
            cdecr_state = self.docker.state(cdecr_name)
            if cdecr_state is not None and cdecr_state.status == "running":
                self.docker.stop(cdecr_name)
                cdecr_state = self.docker.state(cdecr_name)
            if cdecr_state is not None:
                round_root = (
                    self.root
                    / "incidents"
                    / incident.incident_id
                    / "rounds"
                    / repair_round.round_id
                )
                round_root.mkdir(parents=True, exist_ok=True)
                (round_root / "cdecr-executor.log").write_text(
                    self.docker.logs(cdecr_name)[-200_000:], encoding="utf-8"
                )
                if cdecr_state.exit_code not in {0, 143}:
                    self.repairs.update_incident(
                        incident.incident_id,
                        status=IncidentStatus.HUMAN_REQUIRED,
                        phase=IncidentPhase.RECORD,
                        last_error=f"CDECR_EXECUTOR_EXITED:{cdecr_state.exit_code}",
                    )
                    return
        round_root = (
            self.root / "incidents" / incident.incident_id / "rounds" / repair_round.round_id
        )
        round_root.mkdir(parents=True, exist_ok=True)
        (round_root / "executor.log").write_text(
            self.docker.logs(name)[-200_000:], encoding="utf-8"
        )
        run = self.initialization.get(incident.initialization_id)
        if run.status is RunStatus.RUNNING:
            self.repairs.update_incident(
                incident.incident_id,
                status=IncidentStatus.HUMAN_REQUIRED,
                phase=IncidentPhase.RECORD,
                last_error="EXECUTOR_EXITED_WITH_NONTERMINAL_RUN",
            )
            return
        nodes = {node.key: node for node in self.initialization.nodes(run.initialization_id)}
        crossed = [
            key
            for key in repair_round.target_nodes
            if key in nodes and nodes[key].status == "SUCCEEDED"
        ]
        failed = [key for key, node in nodes.items() if node.status == "FAILED"]
        result = f"run={run.status}; crossed={crossed}; failed={failed}"
        self.repairs.finish_round(repair_round.round_id, result=result, exit_code=state.exit_code)
        record_execution(
            self.repairs,
            entry_id=f"{repair_round.round_id}:execution",
            incident_id=incident.incident_id,
            round_id=repair_round.round_id,
            content={
                "ticker": incident.ticker,
                "initialization_id": incident.initialization_id,
                "execution_result": result,
                "crossed_nodes": crossed,
                "failed_nodes": failed,
                "exit_code": state.exit_code,
                "remaining_items": failed or [],
            },
        )
        self.docker.remove(name)
        if repair_round.cdecr_executor_container:
            self.docker.remove(f"doxagent-repair-cdecr-{repair_round.round_id}")

    def _production_source(self) -> dict[str, str]:
        payload = self.docker.inspect(self.production_container)
        if payload is None:
            raise RuntimeError("production initialization container is unavailable")
        labels = payload.get("Config", {}).get("Labels", {}) or {}
        revision = labels.get("org.opencontainers.image.revision")
        if not revision or revision == "unversioned":
            raise RuntimeError("production image has no verified source revision label")
        source_hash = labels.get("doxagent.source.sha256")
        if not source_hash:
            raise RuntimeError("production image has no verified source hash label")
        return {
            "image_id": str(payload["Image"]),
            "revision": str(revision),
            "source_hash": str(source_hash),
        }

    @staticmethod
    def _labels(incident: RepairIncident, round_id: str, role: str) -> dict[str, str]:
        return {
            "doxagent.repair.incident": incident.incident_id,
            "doxagent.repair.run": incident.initialization_id,
            "doxagent.repair.round": round_id,
            "doxagent.repair.role": role,
        }
