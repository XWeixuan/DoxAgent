"""Pilot-only sequential coordinator for Document2 Codex App cases."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    Document2PilotCaseRequest,
    Document2PilotSourceAttemptUnavailable,
    Document2PilotUpstreamCase,
)
from doxagent.workflows.codex_document2.runner import logical_workspace_id
from doxagent.workflows.codex_document2.schema import Document2Checkpoint

_STATE_SCHEMA = "d2-pilot-coordinator-v1"
StageStatus = Literal["pending", "active", "completed", "skipped"]


@dataclass(frozen=True)
class Document2PilotCoordinatorRequest:
    coordinator_id: str
    source_d2_run_id: str
    checkpoint: Document2Checkpoint | None = None
    source_global_run_id: str | None = None
    shell_key: str | None = None
    capability_hours: int = 24 * 365 * 10


@dataclass(frozen=True)
class Document2PilotCoordinatorEvent:
    status: Literal["created", "waiting", "completed"]
    coordinator_root: Path
    node: CodexD2Node | None = None
    case_root: Path | None = None
    task_path: Path | None = None


class Document2PilotCoordinator:
    """Creates one Pilot case at a time without touching the real D2 runtime."""

    def __init__(
        self,
        *,
        builder: Document2PilotCaseBuilder,
        coordinators_root: str | Path,
    ) -> None:
        self._builder = builder
        self._root = Path(coordinators_root).resolve()

    async def start(
        self, request: Document2PilotCoordinatorRequest
    ) -> Document2PilotCoordinatorEvent:
        _identifier(request.coordinator_id, "coordinator_id")
        _identifier(request.source_d2_run_id, "source_d2_run_id")
        if request.checkpoint is None:
            if request.source_global_run_id is None:
                raise ValueError(
                    "source_global_run_id is required when no source D2 checkpoint exists"
                )
            _identifier(request.source_global_run_id, "source_global_run_id")
        elif (
            request.source_global_run_id is not None
            and request.source_global_run_id != request.checkpoint.source_global_run_id
        ):
            raise ValueError("source_global_run_id does not match the D2 checkpoint")
        root = self._root / request.coordinator_id
        if root.exists():
            raise FileExistsError(f"Pilot coordinator already exists: {root}")
        root.mkdir(parents=True)
        stages = _build_stages(request)
        state: dict[str, object] = {
            "schema_version": _STATE_SCHEMA,
            "coordinator_id": request.coordinator_id,
            "source_d2_run_id": request.source_d2_run_id,
            "source_global_run_id": (
                request.source_global_run_id
                or cast(Document2Checkpoint, request.checkpoint).source_global_run_id
            ),
            "bootstrap_from_global_research": request.checkpoint is None,
            "shell_key": request.shell_key,
            "status": "active",
            "capability_hours": request.capability_hours,
            "created_at": _now(),
            "updated_at": _now(),
            "stages": stages,
        }
        _write_state(root, state)
        return await self.advance(request.coordinator_id)

    async def advance(
        self, coordinator_id: str, *, shell_key: str | None = None
    ) -> Document2PilotCoordinatorEvent:
        root, state = self._load(coordinator_id)
        if shell_key is not None:
            _identifier(shell_key, "shell_key")
            state["shell_key"] = shell_key
            state["updated_at"] = _now()
            _write_state(root, state)
        stages = _stages(state)
        active = next((stage for stage in stages if stage["status"] == "active"), None)
        if active is not None:
            if not _completion_ready(active):
                return _event("waiting", root, active)
            active["status"] = "completed"
            active["output_sha256"] = _tree_hash(_output_root(active))
            active["completed_at"] = _now()
            state["updated_at"] = _now()
            _write_state(root, state)

        while True:
            pending = next((stage for stage in stages if stage["status"] == "pending"), None)
            if pending is None:
                state["status"] = "completed"
                state["updated_at"] = _now()
                _write_state(root, state)
                return Document2PilotCoordinatorEvent(
                    status="completed", coordinator_root=root
                )
            dependencies = _dependency_cases(stages, pending)
            request = Document2PilotCaseRequest(
                source_workspace_run=str(pending["source_workspace_run"]),
                node=CodexD2Node(str(pending["node"])),
                case_id=str(pending["case_id"]),
                capability_hours=int(str(state["capability_hours"])),
                upstream_cases=dependencies,
                source_global_run_id=(
                    str(state["source_global_run_id"])
                    if bool(state.get("bootstrap_from_global_research"))
                    else None
                ),
                shell_key=(
                    str(state["shell_key"])
                    if state.get("shell_key") is not None
                    else None
                ),
            )
            try:
                prepared = await self._builder.prepare(request)
            except Document2PilotSourceAttemptUnavailable as exc:
                if bool(pending["optional"]):
                    pending["status"] = "skipped"
                    pending["skip_reason"] = str(exc)
                    pending["completed_at"] = _now()
                    state["updated_at"] = _now()
                    _write_state(root, state)
                    continue
                raise
            pending["status"] = "active"
            pending["case_root"] = str(prepared.case_root)
            pending["attempt_id"] = prepared.attempt_id
            pending["task_path"] = str(prepared.task_path)
            pending["created_at"] = _now()
            state["updated_at"] = _now()
            _write_state(root, state)
            _write_next_case(root, pending)
            return _event("created", root, pending)

    async def watch(
        self,
        coordinator_id: str,
        *,
        poll_seconds: float = 5.0,
    ) -> Document2PilotCoordinatorEvent:
        if poll_seconds < 0.5:
            raise ValueError("poll_seconds must be at least 0.5")
        while True:
            event = await self.advance(coordinator_id)
            if event.status in {"created", "completed"}:
                return event
            await asyncio.sleep(poll_seconds)

    def status(self, coordinator_id: str) -> dict[str, object]:
        _, state = self._load(coordinator_id)
        return state

    async def aclose(self) -> None:
        await self._builder.aclose()

    def _load(self, coordinator_id: str) -> tuple[Path, dict[str, object]]:
        _identifier(coordinator_id, "coordinator_id")
        root = self._root / coordinator_id
        try:
            state = json.loads((root / "coordinator_state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Pilot coordinator state: {root}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != _STATE_SCHEMA:
            raise ValueError(f"unsupported Pilot coordinator state: {root}")
        return root, state


def _build_stages(request: Document2PilotCoordinatorRequest) -> list[dict[str, object]]:
    checkpoint = request.checkpoint
    if checkpoint is None:
        return _build_bootstrap_stages(request)
    if checkpoint.run_id != request.source_d2_run_id:
        raise ValueError("Document2 checkpoint run_id does not match source_d2_run_id")
    shell_items = list(checkpoint.shell_runs.items())
    if request.shell_key is not None:
        shell_items = [
            item
            for item in shell_items
            if item[0] == request.shell_key or item[1].shell_id == request.shell_key
        ]
        if not shell_items:
            raise ValueError(f"Document2 shell is unavailable: {request.shell_key}")
    if len(shell_items) != 1:
        available = ", ".join(
            f"{key}={state.shell_id}" for key, state in checkpoint.shell_runs.items()
        )
        raise ValueError(
            "exactly one Document2 shell must be selected for a 13-node Pilot; "
            f"available shells: {available or 'none'}"
        )
    o1_workspace = shell_items[0][1].workspace_run_id
    source_global = checkpoint.source_global_run_id
    o0_workspace = checkpoint.o0_workspace_run_id
    run_id = request.source_d2_run_id
    specs: list[tuple[CodexD2Node, str, tuple[CodexD2Node, ...], bool]] = [
        (
            CodexD2Node.O0_CANDIDATE_C1,
            logical_workspace_id(run_id, "o0-candidate-c1"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_C3,
            logical_workspace_id(run_id, "o0-candidate-c3"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_C5,
            logical_workspace_id(run_id, "o0-candidate-c5"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_NARRATIVE,
            logical_workspace_id(run_id, "o0-candidate-narrative"),
            (),
            True,
        ),
        (
            CodexD2Node.O0_SYNTHESIS,
            o0_workspace,
            (
                CodexD2Node.O0_CANDIDATE_C1,
                CodexD2Node.O0_CANDIDATE_C3,
                CodexD2Node.O0_CANDIDATE_C5,
                CodexD2Node.O0_CANDIDATE_NARRATIVE,
            ),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C1,
            source_global,
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C3,
            source_global,
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C5,
            source_global,
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_FINALIZATION,
            o0_workspace,
            (
                CodexD2Node.O0_SYNTHESIS,
                CodexD2Node.O0_REVIEW_C1,
                CodexD2Node.O0_REVIEW_C3,
                CodexD2Node.O0_REVIEW_C5,
            ),
            False,
        ),
        (
            CodexD2Node.O1_STATE,
            o1_workspace,
            (CodexD2Node.O0_FINALIZATION,),
            False,
        ),
        (
            CodexD2Node.O1_REALIZATION,
            o1_workspace,
            (CodexD2Node.O1_STATE,),
            False,
        ),
        (
            CodexD2Node.O1_GAPS,
            o1_workspace,
            (CodexD2Node.O1_REALIZATION,),
            False,
        ),
        (
            CodexD2Node.O1_FINALIZATION,
            o1_workspace,
            (CodexD2Node.O1_GAPS,),
            False,
        ),
    ]
    return _stage_records(request.coordinator_id, specs)


def _build_bootstrap_stages(
    request: Document2PilotCoordinatorRequest,
) -> list[dict[str, object]]:
    run_id = request.source_d2_run_id
    o0_workspace = logical_workspace_id(run_id, "o0-synthesis")
    o1_workspace = logical_workspace_id(run_id, "shell-pilot")
    specs: list[tuple[CodexD2Node, str, tuple[CodexD2Node, ...], bool]] = [
        (
            CodexD2Node.O0_CANDIDATE_C1,
            logical_workspace_id(run_id, "o0-candidate-c1"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_C3,
            logical_workspace_id(run_id, "o0-candidate-c3"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_C5,
            logical_workspace_id(run_id, "o0-candidate-c5"),
            (),
            False,
        ),
        (
            CodexD2Node.O0_CANDIDATE_NARRATIVE,
            logical_workspace_id(run_id, "o0-candidate-narrative"),
            (),
            True,
        ),
        (
            CodexD2Node.O0_SYNTHESIS,
            o0_workspace,
            (
                CodexD2Node.O0_CANDIDATE_C1,
                CodexD2Node.O0_CANDIDATE_C3,
                CodexD2Node.O0_CANDIDATE_C5,
                CodexD2Node.O0_CANDIDATE_NARRATIVE,
            ),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C1,
            logical_workspace_id(run_id, "o0-review-c1"),
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C3,
            logical_workspace_id(run_id, "o0-review-c3"),
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_REVIEW_C5,
            logical_workspace_id(run_id, "o0-review-c5"),
            (CodexD2Node.O0_SYNTHESIS,),
            False,
        ),
        (
            CodexD2Node.O0_FINALIZATION,
            o0_workspace,
            (
                CodexD2Node.O0_SYNTHESIS,
                CodexD2Node.O0_REVIEW_C1,
                CodexD2Node.O0_REVIEW_C3,
                CodexD2Node.O0_REVIEW_C5,
            ),
            False,
        ),
        (
            CodexD2Node.O1_STATE,
            o1_workspace,
            (CodexD2Node.O0_FINALIZATION,),
            False,
        ),
        (
            CodexD2Node.O1_REALIZATION,
            o1_workspace,
            (CodexD2Node.O1_STATE,),
            False,
        ),
        (
            CodexD2Node.O1_GAPS,
            o1_workspace,
            (CodexD2Node.O1_REALIZATION,),
            False,
        ),
        (
            CodexD2Node.O1_FINALIZATION,
            o1_workspace,
            (CodexD2Node.O1_GAPS,),
            False,
        ),
    ]
    return _stage_records(request.coordinator_id, specs)


def _stage_records(
    coordinator_id: str,
    specs: list[tuple[CodexD2Node, str, tuple[CodexD2Node, ...], bool]],
) -> list[dict[str, object]]:
    stages: list[dict[str, object]] = []
    for index, (node, workspace, dependencies, optional) in enumerate(specs, start=1):
        suffix = node.value.removeprefix("d2_")
        stages.append(
            {
                "index": index,
                "node": node.value,
                "source_workspace_run": workspace,
                "dependencies": [item.value for item in dependencies],
                "optional": optional,
                "status": "pending",
                "case_id": f"{coordinator_id}-{index:02d}-{suffix}",
            }
        )
    return stages


def _stages(state: dict[str, object]) -> list[dict[str, object]]:
    value = state.get("stages")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Pilot coordinator stages are invalid")
    return cast(list[dict[str, object]], value)


def _completion_ready(stage: dict[str, object]) -> bool:
    completion = _output_root(stage) / "completion.json"
    try:
        json.loads(completion.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return True


def _output_root(stage: dict[str, object]) -> Path:
    return Path(str(stage["case_root"])) / "attempts" / str(stage["attempt_id"]) / "output"


def _dependency_cases(
    stages: list[dict[str, object]], pending: dict[str, object]
) -> tuple[Document2PilotUpstreamCase, ...]:
    raw_dependencies = pending.get("dependencies")
    if not isinstance(raw_dependencies, list):
        raise ValueError("Pilot coordinator dependencies are invalid")
    dependency_nodes = {str(item) for item in raw_dependencies}
    dependencies: list[Document2PilotUpstreamCase] = []
    for stage in stages:
        if str(stage["node"]) not in dependency_nodes or stage["status"] == "skipped":
            continue
        if stage["status"] != "completed":
            raise RuntimeError(f"Pilot dependency is not completed: {stage['node']}")
        dependencies.append(
            Document2PilotUpstreamCase(
                node=CodexD2Node(str(stage["node"])),
                case_root=Path(str(stage["case_root"])),
            )
        )
    return tuple(dependencies)


def _event(
    status: Literal["created", "waiting"],
    root: Path,
    stage: dict[str, object],
) -> Document2PilotCoordinatorEvent:
    return Document2PilotCoordinatorEvent(
        status=status,
        coordinator_root=root,
        node=CodexD2Node(str(stage["node"])),
        case_root=Path(str(stage["case_root"])),
        task_path=Path(str(stage["task_path"])),
    )


def _write_state(root: Path, state: dict[str, object]) -> None:
    path = root / "coordinator_state.json"
    temporary = root / ".coordinator_state.json.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _write_next_case(root: Path, stage: dict[str, object]) -> None:
    (root / "NEXT_CASE.json").write_text(
        json.dumps(
            {
                "node": stage["node"],
                "case_root": stage["case_root"],
                "task_path": stage["task_path"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identifier(value: str, label: str) -> None:
    if not value or len(value) > 80 or not all(char.isalnum() or char in "._-" for char in value):
        raise ValueError(f"invalid {label}")
