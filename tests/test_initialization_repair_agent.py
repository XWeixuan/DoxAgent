from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from doxagent.initialization_repair.agent import MODEL, RepairAgent

REPORT = {
    "root_cause": "bug",
    "evidence": ["test"],
    "changed_files": {"src/file.py": "fixed"},
    "tests": [{"command": "pytest", "result": "passed"}],
    "downstream_implications": ["covered"],
    "remaining_items": [],
}


class Handle:
    id = "turn-1"

    async def run(self):
        return SimpleNamespace(final_response=json.dumps(REPORT))


class FollowupHandle(Handle):
    id = "turn-2"


class Thread:
    def __init__(self, identity="thread-1") -> None:
        self.id = identity
        self.turn_arguments = None

    async def turn(self, prompt, **kwargs):
        self.turn_arguments = (prompt, kwargs)
        return Handle()


class Client:
    def __init__(self) -> None:
        self.thread = Thread()
        self.started = 0
        self.resumed = 0

    async def thread_start(self, **kwargs):
        self.started += 1
        self.start_arguments = kwargs
        return self.thread

    async def thread_resume(self, thread_id, **kwargs):
        self.resumed += 1
        assert thread_id == self.thread.id
        self.resume_arguments = kwargs
        return self.thread


class FollowupThread(Thread):
    async def read(self, **kwargs):
        prior = SimpleNamespace(
            id="turn-1",
            status=SimpleNamespace(value="completed"),
            items=[
                SimpleNamespace(root=SimpleNamespace(type="agentMessage", text=json.dumps(REPORT)))
            ],
        )
        return SimpleNamespace(thread=SimpleNamespace(turns=[prior]))

    async def turn(self, prompt, **kwargs):
        self.turn_arguments = (prompt, kwargs)
        return FollowupHandle()


class FollowupClient(Client):
    def __init__(self) -> None:
        super().__init__()
        self.thread = FollowupThread()


@pytest.mark.asyncio
async def test_agent_persists_thread_and_turn_before_structured_report(tmp_path):
    worktree = tmp_path / "worktree"
    context = worktree / ".initialization-repair" / "round" / "context.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}", encoding="utf-8")
    developer = worktree / "agent.md"
    developer.write_text("instructions", encoding="utf-8")
    client = Client()
    receipt = context.parent / "receipt.json"
    report_path = context.parent / "report.json"
    report, thread_id, turn_id = await RepairAgent(
        developer_prompt=developer,
        codex_home=tmp_path / "codex-home",
        client=client,  # type: ignore[arg-type]
    ).run(
        incident_id="incident",
        round_id="round",
        worktree=worktree,
        context_path=context,
        receipt_path=receipt,
        report_path=report_path,
        thread_id=None,
    )
    assert client.started == 1 and client.resumed == 0
    assert client.start_arguments["model"] == MODEL
    assert client.thread.turn_arguments[1]["effort"].value == "medium"
    assert json.loads(receipt.read_text(encoding="utf-8")) == {
        "incident_id": "incident",
        "round_id": "round",
        "thread_id": thread_id,
        "turn_id": turn_id,
    }
    assert report.root_cause == "bug"
    assert json.loads(report_path.read_text(encoding="utf-8"))["changed_files"] == {
        "src/file.py": "fixed"
    }


@pytest.mark.asyncio
async def test_agent_resumes_same_thread_without_fallback(tmp_path):
    worktree = tmp_path / "worktree"
    context = worktree / "context.json"
    worktree.mkdir()
    context.write_text("{}", encoding="utf-8")
    developer = worktree / "agent.md"
    developer.write_text("instructions", encoding="utf-8")
    client = Client()
    await RepairAgent(
        developer_prompt=developer,
        codex_home=tmp_path / "codex-home",
        client=client,  # type: ignore[arg-type]
    ).run(
        incident_id="incident",
        round_id="round",
        worktree=worktree,
        context_path=context,
        receipt_path=worktree / "receipt.json",
        report_path=worktree / "report.json",
        thread_id="thread-1",
    )
    assert client.started == 0 and client.resumed == 1
    assert client.resume_arguments["model"] == MODEL


@pytest.mark.asyncio
async def test_failed_independent_verification_continues_same_round_and_thread(tmp_path):
    worktree = tmp_path / "worktree"
    round_root = worktree / ".initialization-repair" / "round"
    round_root.mkdir(parents=True)
    context = round_root / "context.json"
    context.write_text("{}", encoding="utf-8")
    developer = worktree / "agent.md"
    developer.write_text("instructions", encoding="utf-8")
    receipt = round_root / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "incident_id": "incident",
                "round_id": "round",
                "thread_id": "thread-1",
                "turn_id": "turn-1",
            }
        ),
        encoding="utf-8",
    )
    feedback = round_root / "verification-feedback.json"
    feedback.write_text(
        json.dumps(
            {
                "incident_id": "incident",
                "round_id": "round",
                "for_turn_id": "turn-1",
                "status": "pending",
                "verification_log": "verification.log",
            }
        ),
        encoding="utf-8",
    )
    client = FollowupClient()
    _, thread_id, turn_id = await RepairAgent(
        developer_prompt=developer,
        codex_home=tmp_path / "codex-home",
        client=client,  # type: ignore[arg-type]
    ).run(
        incident_id="incident",
        round_id="round",
        worktree=worktree,
        context_path=context,
        receipt_path=receipt,
        report_path=round_root / "report.json",
        thread_id="thread-1",
    )
    assert (thread_id, turn_id) == ("thread-1", "turn-2")
    assert "independent verification failed" in client.thread.turn_arguments[0]
    assert json.loads(feedback.read_text(encoding="utf-8")) == {
        "incident_id": "incident",
        "round_id": "round",
        "for_turn_id": "turn-1",
        "status": "handled",
        "verification_log": "verification.log",
        "handled_by_turn_id": "turn-2",
    }
