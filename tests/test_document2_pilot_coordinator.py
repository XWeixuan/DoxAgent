from __future__ import annotations

import json
from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseRequest,
    Document2PilotSourceAttemptUnavailable,
    Document2PilotUpstreamCase,
    PreparedDocument2PilotCase,
    _materialize_pilot_upstream,
)
from doxagent.pilot.document2_coordinator import (
    Document2PilotCoordinator,
    Document2PilotCoordinatorEvent,
    Document2PilotCoordinatorRequest,
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

    async def prepare(
        self, request: Document2PilotCaseRequest
    ) -> PreparedDocument2PilotCase:
        self.requests.append(request)
        if (
            request.node is CodexD2Node.O0_CANDIDATE_NARRATIVE
            and not self.narrative_available
        ):
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
        event.case_root
        / "attempts"
        / f"attempt-{event.node.value}"
        / "output"
        / "completion.json"
    )
    completion.write_text(json.dumps({"node": event.node.value}), encoding="utf-8")


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
