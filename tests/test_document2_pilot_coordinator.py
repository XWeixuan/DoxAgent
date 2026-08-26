from __future__ import annotations

import json
from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    Document2PilotCaseRequest,
    Document2PilotShellSelectionRequired,
    Document2PilotSourceAttemptUnavailable,
    Document2PilotUpstreamCase,
    PreparedDocument2PilotCase,
    _materialize_pilot_upstream,
)
from doxagent.pilot.document2_coordinator import (
    Document2PilotCoordinator,
    Document2PilotCoordinatorEvent,
    Document2PilotCoordinatorRequest,
    _dependency_cases,
    _materialize_finalization_override,
)
from doxagent.pilot.templates import render_document2_task
from doxagent.workflows.codex_document2.schema import (
    Document2Checkpoint,
    ShellRunState,
)


class _FakeBuilder:
    def __init__(self, cases_root: Path, *, narrative_available: bool = False) -> None:
        self.cases_root = cases_root
        self.narrative_available = narrative_available
        self.requests: list[Document2PilotCaseRequest] = []

    async def prepare(self, request: Document2PilotCaseRequest) -> PreparedDocument2PilotCase:
        self.requests.append(request)
        if request.node is CodexD2Node.O0_CANDIDATE_NARRATIVE and not self.narrative_available:
            raise Document2PilotSourceAttemptUnavailable(
                "source workspace has no successful d2_o0_candidate_narrative attempt"
            )
        attempt_id = f"attempt-{request.node.value}"
        root = self.cases_root / request.node.value / request.case_id
        output = root / "attempts" / attempt_id / "output"
        output.mkdir(parents=True)
        (root / "PILOT_TASK.md").write_text("task", encoding="utf-8")
        (root / "case_manifest.json").write_text(
            json.dumps(
                {
                    "case_id": request.case_id,
                    "node": request.node.value,
                    "node_attempt_id": attempt_id,
                }
            ),
            encoding="utf-8",
        )
        return PreparedDocument2PilotCase(
            case_root=root,
            source_workspace_run=request.source_workspace_run,
            node=request.node,
            attempt_id=attempt_id,
            input_sha256="sha",
            task_path=root / "PILOT_TASK.md",
        )

    async def aclose(self) -> None:
        return None


class _SelectionBuilder(_FakeBuilder):
    async def prepare(self, request: Document2PilotCaseRequest) -> PreparedDocument2PilotCase:
        if request.shell_key is None:
            self.requests.append(request)
            raise Document2PilotShellSelectionRequired(["shell-a", "shell-b"])
        return await super().prepare(request)


class _NoParentBeforeSelectionBuilder(Document2PilotCaseBuilder):
    def __init__(self, cases_root: Path) -> None:
        self.cases_root = cases_root

    async def _prepare_bootstrap(
        self,
        request: Document2PilotCaseRequest,
        case_root: Path,
    ) -> PreparedDocument2PilotCase:
        del request, case_root
        raise Document2PilotShellSelectionRequired(["shell-a", "shell-b"])


def _checkpoint() -> Document2Checkpoint:
    return Document2Checkpoint(
        run_id="d2-run",
        source_global_run_id="global-run",
        o0_workspace_run_id="o0-workspace",
        shell_runs={
            "shell-key": ShellRunState(
                shell_id="shell-id",
                workspace_run_id="o1-workspace",
            )
        },
    )


def _finish(event: Document2PilotCoordinatorEvent) -> None:
    assert event.case_root is not None
    assert event.node is not None
    completion = (
        event.case_root / "attempts" / f"attempt-{event.node.value}" / "output" / "completion.json"
    )
    completion.write_text(json.dumps({"node": event.node.value}), encoding="utf-8")


@pytest.mark.asyncio
async def test_shell_selection_required_is_persisted_without_creating_a_case(
    tmp_path: Path,
) -> None:
    builder = _SelectionBuilder(tmp_path / "cases")
    coordinator = Document2PilotCoordinator(
        builder=builder,  # type: ignore[arg-type]
        coordinators_root=tmp_path / "coordinators",
    )

    event = await coordinator.start(
        Document2PilotCoordinatorRequest(
            coordinator_id="selection-pilot",
            source_d2_run_id="d2pilot-selection",
            source_global_run_id="global-run",
        )
    )

    assert event.status == "selection_required"
    assert event.available_shell_ids == ("shell-a", "shell-b")
    state = coordinator.status("selection-pilot")
    assert state["selection_required"]["available_shell_ids"] == [  # type: ignore[index]
        "shell-a",
        "shell-b",
    ]
    assert all(stage["status"] == "pending" for stage in state["stages"])  # type: ignore[union-attr]
    next_case = json.loads(
        (tmp_path / "coordinators" / "selection-pilot" / "NEXT_CASE.json").read_text(
            encoding="utf-8"
        )
    )
    assert next_case["status"] == "selection_required"
    assert next_case["case_root"] is None
    assert next_case["available_shell_ids"] == ["shell-a", "shell-b"]

    created = await coordinator.advance("selection-pilot", shell_key="shell-b")

    assert created.status == "created"
    assert builder.requests[-1].shell_key == "shell-b"
    assert "selection_required" not in coordinator.status("selection-pilot")


@pytest.mark.asyncio
async def test_builder_does_not_create_node_parent_before_shell_selection(
    tmp_path: Path,
) -> None:
    builder = _NoParentBeforeSelectionBuilder(tmp_path / "cases")
    request = Document2PilotCaseRequest(
        source_workspace_run="workspace-run",
        source_global_run_id="global-run",
        node=CodexD2Node.O1_STATE,
        case_id="o1-state-case",
    )

    with pytest.raises(Document2PilotShellSelectionRequired):
        await builder.prepare(request)

    assert not (tmp_path / "cases" / "document2" / "d2_o1_state").exists()


@pytest.mark.asyncio
async def test_coordinator_creates_only_next_case_and_injects_direct_dependencies(
    tmp_path: Path,
) -> None:
    builder = _FakeBuilder(tmp_path / "cases")
    coordinator = Document2PilotCoordinator(
        builder=builder,  # type: ignore[arg-type]
        coordinators_root=tmp_path / "coordinators",
    )
    event = await coordinator.start(
        Document2PilotCoordinatorRequest(
            coordinator_id="mu-d2-pilot",
            source_d2_run_id="d2-run",
            checkpoint=_checkpoint(),
        )
    )
    assert event.node is CodexD2Node.O0_CANDIDATE_C1
    assert len(builder.requests) == 1
    waiting = await coordinator.advance("mu-d2-pilot")
    assert waiting.status == "waiting"
    assert len(builder.requests) == 1

    for expected in (
        CodexD2Node.O0_CANDIDATE_C3,
        CodexD2Node.O0_CANDIDATE_C5,
    ):
        _finish(event)
        event = await coordinator.advance("mu-d2-pilot")
        assert event.node is expected
        assert builder.requests[-1].upstream_cases == ()

    _finish(event)
    event = await coordinator.advance("mu-d2-pilot")
    assert event.node is CodexD2Node.O0_SYNTHESIS
    assert [item.node for item in builder.requests[-1].upstream_cases] == [
        CodexD2Node.O0_CANDIDATE_C1,
        CodexD2Node.O0_CANDIDATE_C3,
        CodexD2Node.O0_CANDIDATE_C5,
    ]
    state = coordinator.status("mu-d2-pilot")
    narrative = next(
        item
        for item in state["stages"]  # type: ignore[union-attr]
        if item["node"] == CodexD2Node.O0_CANDIDATE_NARRATIVE.value
    )
    assert narrative["status"] == "skipped"


@pytest.mark.asyncio
async def test_coordinator_bootstraps_first_case_from_global_research(
    tmp_path: Path,
) -> None:
    builder = _FakeBuilder(tmp_path / "cases")
    coordinator = Document2PilotCoordinator(
        builder=builder,  # type: ignore[arg-type]
        coordinators_root=tmp_path / "coordinators",
    )
    event = await coordinator.start(
        Document2PilotCoordinatorRequest(
            coordinator_id="mu-d2-bootstrap",
            source_d2_run_id="d2pilot-mu-d2-bootstrap",
            source_global_run_id="mu-global-formal",
        )
    )
    assert event.node is CodexD2Node.O0_CANDIDATE_C1
    assert len(builder.requests) == 1
    assert builder.requests[0].source_global_run_id == "mu-global-formal"
    state = coordinator.status("mu-d2-bootstrap")
    assert state["bootstrap_from_global_research"] is True
    assert state["source_global_run_id"] == "mu-global-formal"
    stages = {item["node"]: item for item in state["stages"]}  # type: ignore[union-attr]
    assert stages[CodexD2Node.O1_REALIZATION.value]["dependencies"] == [
        CodexD2Node.O0_FINALIZATION.value,
        CodexD2Node.O1_STATE.value,
    ]
    assert stages[CodexD2Node.O1_GAPS.value]["dependencies"] == [
        CodexD2Node.O0_FINALIZATION.value,
        CodexD2Node.O1_REALIZATION.value,
    ]
    assert stages[CodexD2Node.O1_FINALIZATION.value]["dependencies"] == [
        CodexD2Node.O0_FINALIZATION.value,
        CodexD2Node.O1_GAPS.value,
    ]


def test_upstream_materialization_copies_every_output_file(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    output = upstream / "attempts" / "attempt-1" / "output"
    output.mkdir(parents=True)
    (output / "completion.json").write_text('{"ok": true}', encoding="utf-8")
    (output / "notes.md").write_text("all output", encoding="utf-8")
    (upstream / "case_manifest.json").write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "node": CodexD2Node.O0_SYNTHESIS.value,
                "node_attempt_id": "attempt-1",
            }
        ),
        encoding="utf-8",
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    entries = _materialize_pilot_upstream(
        staging,
        (
            Document2PilotUpstreamCase(
                node=CodexD2Node.O0_SYNTHESIS,
                case_root=upstream,
            ),
        ),
    )
    copied = staging / "context" / "pilot_upstream" / "d2_o0_synthesis" / "output"
    assert (copied / "completion.json").is_file()
    assert (copied / "notes.md").read_text(encoding="utf-8") == "all output"
    assert len(entries[0]["files"]) == 2  # type: ignore[arg-type]


def test_document2_task_explains_pilot_upstream_precedence(tmp_path: Path) -> None:
    task = render_document2_task(
        case_root=tmp_path,
        node=CodexD2Node.O1_REALIZATION.value,
        run_id="run",
        attempt_id="attempt",
        has_pilot_upstream=True,
    )
    assert "context/pilot_upstream/manifest.json" in task
    assert "具有优先权" in task
    assert "旧 attempt 的 O#" in task


def test_recompiled_finalization_replaces_only_the_o1_dependency(tmp_path: Path) -> None:
    original = tmp_path / "original-o0"
    original.mkdir()
    source = tmp_path / "recompiled.json"
    source.write_text(
        json.dumps(
            {
                "shells": [
                    {
                        "shell_id": "compressed-shell",
                        "core_question": "What changes the earnings path?",
                        "boundary_rule": "Keep one coherent earnings-cycle boundary.",
                        "units": [
                            {
                                "expectation_id": "earnings-path",
                                "proposition": "Earnings remain above the prior cycle.",
                                "horizon": "next 12 months",
                            }
                        ],
                    }
                ],
                "finalization_note": ["Recompiled for O1."],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    coordinator_root = tmp_path / "coordinator"
    coordinator_root.mkdir()
    override = _materialize_finalization_override(coordinator_root, source)
    override_case = Document2PilotUpstreamCase(
        node=CodexD2Node.O0_FINALIZATION,
        case_root=Path(str(override["case_root"])),
    )
    dependencies = _dependency_cases(
        [
            {
                "node": CodexD2Node.O0_FINALIZATION.value,
                "status": "completed",
                "case_root": str(original),
            }
        ],
        {"dependencies": [CodexD2Node.O0_FINALIZATION.value]},
        finalization_override=override_case,
    )

    assert dependencies == (override_case,)
    assert original.is_dir()
    completion = (
        override_case.case_root
        / "attempts"
        / json.loads(
            (override_case.case_root / "case_manifest.json").read_text(encoding="utf-8")
        )["node_attempt_id"]
        / "output"
        / "completion.json"
    )
    assert json.loads(completion.read_text(encoding="utf-8"))["shells"][0]["shell_id"] == (
        "compressed-shell"
    )
