"""O1 same-Shell thread continuity across real Runner and SDK branches."""

from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexD2AgentRole, CodexD2Node
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from tests.test_codex_document2_v21_orchestration import seed, setup
from tests.test_codex_runtime_v2 import _AsyncSdkClient


class ThreadClient(_AsyncSdkClient):
    def __init__(self):
        super().__init__()
        self.threads = {}
        self.starts = []
        self.resumes = []

    async def thread_start(self, **kwargs):
        thread = type(self.thread)()
        thread.id = f"sdk-{len(self.starts) + 1}"
        self.threads[thread.id] = thread
        self.starts.append(kwargs)
        return thread

    async def thread_resume(self, thread_id, **kwargs):
        self.resumes.append((thread_id, kwargs))
        return self.threads[thread_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("retry,two_shells", [(False, False), (True, False), (False, True)])
async def test_one_sdk_thread_with_distinct_o1_tasks(tmp_path, monkeypatch, retry, two_shells):
    tmp_path = tmp_path.parent / f"ot{int(retry)}{int(two_shells)}"
    tmp_path.mkdir()
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=retry)
    sdk = ThreadClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *_, **__: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    original = worker.run
    original_output = worker._output
    tasks = []

    async def output(req):
        value = await original_output(req)
        if two_shells and req.node == CodexD2Node.O0_FINALIZATION:
            value["shells"].append(seed("supply"))
        return value

    monkeypatch.setattr(worker, "_output", output)

    async def run(req):
        if req.agent_role != CodexD2AgentRole.O1:
            return await original(req)
        # Exercise production SDK start/resume configuration without a model or MCP process.
        handle = await runtime.start(
            req.model_copy(update={"data_mcp_enabled": False}),
            Path(ws.store.root) / req.run_id,
        )
        await handle.run()
        tasks.append(req)
        return (await original(req)).model_copy(update={"thread_id": handle.thread_id})

    monkeypatch.setattr(worker, "run", run)
    result = await orch.run(request)
    assert result.publication_state == ("PARTIAL" if retry else "COMPLETE"), (
        result.checkpoint.shell_runs
    )
    assert len(sdk.starts) == (2 if two_shells else 1)
    assert len(sdk.resumes) == (8 if two_shells else 4)
    states = result.checkpoint.shell_runs.values()
    assert len({state.thread_id for state in states}) == len(sdk.starts)
    for state in states:
        shell_tasks = [task for task in tasks if task.run_id == state.workspace_run_id]
        assert shell_tasks[0].thread_id is None
        assert {task.thread_id for task in shell_tasks[1:]} == {state.thread_id}
    for thread_id, config in sdk.resumes:
        assert config["cwd"] == sdk.starts[int(thread_id.split("-")[1]) - 1]["cwd"]
    assert len({task.attempt_id for task in tasks}) == len(tasks)
    assert len({task.prompt for task in tasks}) == len(tasks)
    assert all("Read attempts/" in task.prompt for task in tasks)
    for task in tasks:
        assert (
            Path(ws.store.root) / task.run_id / "attempts" / task.attempt_id / "input/task.json"
        ).is_file()


@pytest.mark.asyncio
async def test_first_stage_failure_keeps_thread_for_later_resume(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=True)
    orch._max_attempts = 1
    partial = await orch.run(request)
    state = next(iter(partial.checkpoint.shell_runs.values()))
    assert partial.publication_state == "PARTIAL"
    assert state.thread_id == f"o1-{state.workspace_run_id}"
    expected_thread = state.thread_id
    state.thread_id = None
    repo.save_bundle(partial)
    result = await orch.run(request)
    assert result.publication_state == "PARTIAL"
    tasks = [req for req in worker.requests if req.agent_role == CodexD2AgentRole.O1]
    assert tasks[0].thread_id is None
    assert {task.thread_id for task in tasks[1:]} == {expected_thread}
    assert worker.scan_commits == 1
