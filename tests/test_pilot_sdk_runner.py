from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    Turn,
    TurnCompletedNotification,
)

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.pilot.document2_coordinator import Document2PilotCoordinatorEvent
from doxagent.pilot.document2_sdk import _shell_thread, drive
from doxagent.pilot.sdk_runner import (
    PilotSdkRunner,
    _validation_context,
    build_delivery,
    execution_lock,
    read_json,
    write_json,
)
from doxagent.pilot.templates import render_document2_task
from doxagent.workflows.codex_document2.schema import (
    CandidateDiscoveryResultV21,
    ExpectationShell,
)
from tests.test_codex_document2_v21_orchestration import scan, selection
from tests.test_d2_discovery_checkpoint import local_service

REVIEW = {
    "task_summary": "完成候选研究",
    "no_issues_reason": None,
    "issues": [
        {
            "category": "writing_difficulty",
            "severity": "minor",
            "location": "scope",
            "observation": "不确定如何表述边界",
            "impact": "可能过宽",
            "workaround": "按实体限定",
            "suggestion": "给出边界示例",
            "resolved": True,
        }
    ],
}


def case(tmp_path: Path, name="case", node=CodexD2Node.O0_CANDIDATE_C1) -> Path:
    root = tmp_path / name
    local = root / "attempts" / "a"
    for folder in ("input", "audit", "output"):
        (local / folder).mkdir(parents=True)
    (root / ".codex").mkdir()
    (root / ".codex/config.toml").write_text(
        'model="gpt-6.1-sol"\nmodel_reasoning_effort="high"\n'
        '[mcp_servers.data.env]\nDOXAGENT_DATA_MCP_CAPABILITY="signed-secret-example"\n',
        encoding="utf-8",
    )
    version = "document2.v2.1" if node is CodexD2Node.O0_CANDIDATE_C1 else "document2.v2"
    model = CandidateDiscoveryResultV21 if version == "document2.v2.1" else ExpectationShell
    write_json(
        root / "case_manifest.json",
        {
            "schema_version": "codex-research-pilot-case-v2",
            "case_id": name,
            "run_id": "shell-run",
            "node_attempt_id": "a",
            "attempt_id": "a",
            "node": node.value,
            "document_schema_version": version,
        },
    )
    write_json(local / "input/output_schema.json", model.model_json_schema())
    write_json(local / "input/context.json", {})
    (root / "PILOT_TASK.md").write_text(
        render_document2_task(
            case_root=root,
            node=node.value,
            run_id="shell-run",
            attempt_id="a",
        ),
        encoding="utf-8",
    )
    return root


class Handle:
    def __init__(self, client, identifier, result, status="completed", action=None):
        self.client = client
        self.id = identifier
        self.result = result
        self.status = status
        self.action = action

    async def stream(self):
        if self.action:
            self.action()
        if self.status == "hang":
            await asyncio.sleep(10)
        items = [
            {
                "type": "agentMessage",
                "id": "comment",
                "phase": "commentary",
                "text": "读取报告 signed-secret-example",
            },
            {
                "type": "reasoning",
                "id": "reason",
                "summary": ["公开过程摘要"],
                "content": ["PRIVATE-HIDDEN-REASONING"],
            },
            {
                "type": "agentMessage",
                "id": "final",
                "phase": "final_answer",
                "text": json.dumps(self.result),
            },
        ]
        for item in items:
            event = ItemCompletedNotification.model_validate(
                {
                    "threadId": "thread",
                    "turnId": self.id,
                    "completedAtMs": 1,
                    "item": item,
                }
            )
            yield SimpleNamespace(payload=event)
        turn = Turn.model_validate({"id": self.id, "status": self.status, "items": items})
        self.client.turns.append(turn)
        yield SimpleNamespace(payload=TurnCompletedNotification(thread_id="thread", turn=turn))

    async def interrupt(self):
        self.client.interrupted = True


class Client:
    def __init__(self, results):
        self.results = list(results)
        self.starts = []
        self.resumes = []
        self.calls = []
        self.turns = []
        self.interrupted = False
        self.id = "shared-thread"

    async def thread_start(self, **kwargs):
        self.starts.append(kwargs)
        return self

    async def thread_resume(self, identifier, **kwargs):
        assert identifier == self.id
        self.resumes.append(kwargs)
        return self

    async def turn(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        value = self.results.pop(0)
        options = value if isinstance(value, tuple) else (value, "completed", None)
        return Handle(self, f"turn-{len(self.calls)}", *options)

    async def read(self, **kwargs):
        return SimpleNamespace(thread=SimpleNamespace(turns=self.turns))


async def test_sdk_research_review_delivery_and_completed_reuse(tmp_path):
    root = case(tmp_path)
    client = Client([{"candidates": [], "warnings": []}, REVIEW])
    runner = PilotSdkRunner(client=client)
    receipt = await runner.run_case(root)
    assert receipt["status"] == "completed"
    assert len(client.starts) == 1 and len(client.calls) == 2
    config = client.starts[0]["config"]
    assert config["mcp_servers.data.env.DOXAGENT_DATA_MCP_CAPABILITY"] == "signed-secret-example"
    assert "mcp_servers.d2_discovery.enabled" not in config
    audit = root / "attempts/a/audit"
    issues = (audit / "pilot_issues.md").read_text(encoding="utf-8")
    assert "writing_difficulty" in issues and "不确定如何表述边界" in issues
    process = (audit / "pilot_process.jsonl").read_text(encoding="utf-8")
    assert "公开过程摘要" in process
    assert all("observed_at" in json.loads(line) for line in process.splitlines())
    assert "PRIVATE-HIDDEN-REASONING" not in process
    assert "signed-secret-example" not in process
    await runner.run_case(root)
    assert len(client.calls) == 2
    delivery = build_delivery(tmp_path / "delivery", [root], status="completed")
    with zipfile.ZipFile(delivery["artifacts"]) as archive:
        assert "case/output/completion.json" in archive.namelist()
        assert "case/audit/pilot_issues.md" in archive.namelist()
        assert not any("input" in name or ".codex" in name for name in archive.namelist())
        assert all(
            b"signed-secret-example" not in archive.read(name) for name in archive.namelist()
        )


async def test_review_failure_retries_only_review(tmp_path):
    root = case(tmp_path)
    client = Client([{"candidates": [], "warnings": []}, (REVIEW, "failed", None), REVIEW])
    receipt = await PilotSdkRunner(client=client).run_case(root)
    assert receipt["status"] == "completed"
    assert len(client.calls) == 3 and len(client.starts) == 1
    assert "独立 Pilot 复盘" in client.calls[-1][0]
    assert receipt["review_attempts"] == 1


async def test_review_cannot_change_business_output(tmp_path):
    root = case(tmp_path)
    output = root / "attempts/a/output/completion.json"
    client = Client(
        [
            {"candidates": [], "warnings": []},
            (
                REVIEW,
                "completed",
                lambda: write_json(output, {"candidates": [], "warnings": ["changed"]}),
            ),
        ]
    )
    with pytest.raises(ValueError, match="modified formal outputs"):
        await PilotSdkRunner(client=client).run_case(root)
    receipt = read_json(root / "attempts/a/audit/pilot_sdk_receipt.json")
    assert receipt["status"] == "failed"


async def test_schema_failure_is_diagnosed_without_research_repeat(tmp_path):
    root = case(tmp_path)
    client = Client([{"candidates": "invalid"}, REVIEW])
    receipt = await PilotSdkRunner(client=client).run_case(root)
    assert receipt["status"] == "completed" and len(client.calls) == 2
    assert read_json(root / "attempts/a/output/accepted.json")["candidates"] == []
    metadata = read_json(root / "attempts/a/audit/acceptance.json")
    assert any(d["code"] == "upstream_fallback" for d in metadata["diagnostics"])
    assert '"invalid"' in (root / "attempts/a/audit/research_sdk_reply.txt").read_text()


async def test_driver_crash_adopts_completed_sdk_turn_without_research_repeat(tmp_path):
    root = case(tmp_path)
    client = Client([{"candidates": [], "warnings": []}, REVIEW])
    runner = PilotSdkRunner(client=client)
    original = runner._accept_phase
    first = True

    def crash(root, manifest, receipt, final):
        nonlocal first
        if first:
            first = False
            receipt["status"] = "running"
            raise RuntimeError("driver crashed")
        original(root, manifest, receipt, final)

    runner._accept_phase = crash
    with pytest.raises(RuntimeError, match="crashed"):
        await runner.run_case(root)
    await runner.run_case(root)
    assert len(client.calls) == 2


async def test_unsettled_turn_is_not_duplicated_and_timeout_interrupts(tmp_path):
    root = case(tmp_path)
    client = Client([({}, "hang", None)])
    runner = PilotSdkRunner(client=client, timeout_seconds=0.01)
    with pytest.raises(TimeoutError):
        await runner.run_case(root)
    assert client.interrupted
    with pytest.raises(RuntimeError, match="unsettled"):
        await runner.run_case(root, retry=True)
    assert len(client.calls) == 1


async def test_o1_cases_share_thread_and_replace_case_context(tmp_path):
    one = case(tmp_path, "one", CodexD2Node.O1_STATE)
    two = case(tmp_path, "two", CodexD2Node.O1_REALIZATION)
    output = {"shell_id": "shell", "core_question": "q", "boundary_rule": "b", "units": []}
    client = Client([output, REVIEW, output, REVIEW])
    runner = PilotSdkRunner(client=client)
    await runner.run_case(one)
    state = {"stages": [{"case_root": str(one)}, {"case_root": str(two)}]}
    thread = _shell_thread(state, two)
    assert thread == client.id
    await runner.run_case(two, thread_id=thread)
    assert len(client.starts) == 1 and len(client.resumes) == 1
    assert client.resumes[0]["cwd"] == str(two.resolve())
    assert client.resumes[0]["config"]["sandbox_workspace_write.writable_roots"] == [str(two)]


def test_process_lock_rejects_concurrent_driver(tmp_path):
    with execution_lock(tmp_path / "lock"):
        with pytest.raises(RuntimeError, match="already"):
            with execution_lock(tmp_path / "lock"):
                pass
    with execution_lock(tmp_path / "lock"):
        pass


async def test_headless_driver_requires_review_before_advance(tmp_path):
    root = case(tmp_path)
    state = {"stages": [{"case_root": str(root), "status": "active"}]}

    class Coordinator:
        def status(self, identifier):
            return state

        async def advance(self, identifier, **kwargs):
            receipt = read_json(root / "attempts/a/audit/pilot_sdk_receipt.json")
            assert receipt["status"] == "completed"
            return Document2PilotCoordinatorEvent(status="completed", coordinator_root=tmp_path)

    runner = PilotSdkRunner(client=Client([{"candidates": [], "warnings": []}, REVIEW]))
    result = await drive(Coordinator(), runner, "id", coordinator_root=tmp_path / "coordinator")
    assert result["status"] == "completed" and result["nodes_executed"] == 1


async def test_frozen_input_change_prevents_reuse(tmp_path):
    root = case(tmp_path)
    client = Client([{"candidates": [], "warnings": []}, REVIEW])
    runner = PilotSdkRunner(client=client)
    await runner.run_case(root)
    write_json(root / "attempts/a/input/context.json", {"changed": True})
    with pytest.raises(ValueError, match="frozen inputs changed"):
        await runner.run_case(root)
    assert len(client.calls) == 2


async def test_open_discovery_single_turn_checkpoint_and_aggregate(tmp_path):
    from doxagent.workflows.codex_document2.schema import OpenDiscoveryCompletionV21

    service = local_service(tmp_path, pilot=True)
    root = service.root
    local = root / "attempts/attempt-1"
    (local / "audit").mkdir()
    (local / "output").mkdir()
    (root / ".codex").mkdir()
    (root / ".codex/config.toml").write_text('model="gpt-6.1-sol"', encoding="utf-8")
    manifest = read_json(root / "case_manifest.json")
    manifest["document_schema_version"] = "document2.v2.1"
    write_json(root / "case_manifest.json", manifest)
    write_json(local / "input/output_schema.json", OpenDiscoveryCompletionV21.model_json_schema())
    result = {"scan_sha256": "", "selection": selection()}

    def commit():
        result["scan_sha256"] = service.commit(scan())["scan_sha256"]

    client = Client([(result, "completed", commit), REVIEW])
    await PilotSdkRunner(client=client).run_case(root)
    assert len(client.calls) == 2  # One Discovery research turn, one audit-only review.
    assert client.starts[0]["config"]["mcp_servers.d2_discovery.enabled"] is True
    assert (
        client.starts[0]["config"][
            "mcp_servers.d2_discovery.tools.commit_open_discovery_scan.approval_mode"
        ]
        == "approve"
    )
    aggregate = read_json(local / "output/open_discovery_result.json")
    assert aggregate["checkpoint"]["scan_sha256"] == result["scan_sha256"]
    assert aggregate["selection"]["selections"]


async def test_missing_review_is_not_silently_treated_as_no_issues(tmp_path):
    root = case(tmp_path)
    no_issues = {"task_summary": "完成", "issues": [], "no_issues_reason": None}
    client = Client([{"candidates": [], "warnings": []}, no_issues, no_issues])
    receipt = await PilotSdkRunner(client=client).run_case(root)
    assert receipt["status"] == "completed" and receipt["review_unavailable"]
    assert (root / "attempts/a/audit/review_unavailable.json").exists()
    assert len(client.calls) == 3


def test_validation_uses_frozen_pilot_candidates_not_old_source_context(tmp_path):
    root = case(tmp_path)
    manifest = read_json(root / "case_manifest.json")
    manifest["node"] = CodexD2Node.O0_SYNTHESIS.value
    context_path = root / "attempts/a/input/context.json"
    write_json(context_path, {"candidate_sets": {"old": {"candidates": []}}})
    upstream = root / "context/pilot_upstream"
    node = CodexD2Node.O0_CANDIDATE_C1
    write_json(upstream / "manifest.json", {"entries": [{"node": node.value}]})
    write_json(
        upstream / node.value / "output/completion.json",
        {
            "candidates": [
                {"name": "Pilot subject", "scope": "scope", "why_material": "reason", "ref": []}
            ],
            "warnings": [],
        },
    )
    result = _validation_context(root, manifest)
    assert "old" not in result["candidate_sets"]
    assert result["candidate_sets"]["c1"]["candidates"][0]["name"] == "Pilot subject"
    assert read_json(context_path)["candidate_sets"] == {"old": {"candidates": []}}


async def test_driver_selection_required_exports_without_model_calls(tmp_path):
    state = {"stages": [], "selection_required": {"available_shell_ids": ["a", "b"]}}

    class Coordinator:
        def status(self, identifier):
            return state

        async def advance(self, identifier, **kwargs):
            return Document2PilotCoordinatorEvent(
                status="selection_required", coordinator_root=tmp_path
            )

    client = Client([])
    result = await drive(
        Coordinator(), PilotSdkRunner(client=client), "id", coordinator_root=tmp_path
    )
    assert result["selection_required"]["available_shell_ids"] == ["a", "b"]
    assert not client.starts
