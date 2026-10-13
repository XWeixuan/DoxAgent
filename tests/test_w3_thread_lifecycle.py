from types import SimpleNamespace

import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.schema import ThreadForkRequest, WorkerJob, WorkerRunRequest
from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore


def request(**updates):
    return WorkerRunRequest(
        workflow_version="persistent_runtime_w3_v1",
        ticker="MU",
        node="persistent_runtime_w3",
        agent_role="w3_duty_trading_expert",
        run_id="w3-template-test",
        attempt_id="init",
        prompt="synthetic",
        output_schema={},
        task_phase="w3_template",
        **updates,
    )


class Transport:
    def __init__(self):
        self.threads = {"mother": {"id": "mother", "status": {"type": "idle"}}}
        self.forks = 0
        self.lose_response = True

    async def request(self, method, params):
        if method == "thread/list":
            return {"data": [t for t in self.threads.values() if t.get("cwd") == params["cwd"]]}
        if method == "thread/fork":
            self.forks += 1
            thread = dict(
                id="child",
                cwd=params["cwd"],
                forkedFromId=params["threadId"],
                status={"type": "idle"},
            )
            self.threads["child"] = thread
            if self.lose_response:
                self.lose_response = False
                raise ConnectionError("response lost after persistent fork")
            return {"thread": thread}
        if method == "thread/read":
            if params["threadId"] not in self.threads:
                raise ValueError("thread not found")
            return {"thread": self.threads[params["threadId"]]}
        if method == "thread/delete":
            del self.threads[params["threadId"]]
            return {}
        raise AssertionError(method)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_fork_recovers_lost_response_and_protects_active_and_template(tmp_path):
    store = JobStore(tmp_path)
    workspace = LocalWorkspaceStore(tmp_path)
    template = WorkerJob(
        job_id="init-job",
        run_id="w3-template-test",
        attempt_id="init",
        status="succeeded",
        thread_id="mother",
        final_response='{"ready":true}',
    )
    store.save(template, request())
    released = []

    async def release(thread_id):
        released.append(thread_id)

    jobs = SimpleNamespace(
        store=store, get=store.get, _runtime=SimpleNamespace(release_thread=release)
    )
    transport = Transport()
    lifecycle = WorkerThreadLifecycle(jobs, workspace, transport)
    await lifecycle.register_template("init-job")
    fork = ThreadForkRequest(
        parent_thread_id="mother", run_id="w3-case-test", idempotency_key="fork-one"
    )
    with pytest.raises(ConnectionError):
        await lifecycle.fork(fork)
    assert (await lifecycle.fork(fork))["thread_id"] == "child"
    assert (await lifecycle.fork(fork))["thread_id"] == "child"
    assert transport.forks == 1
    with pytest.raises(ValueError):
        await lifecycle.fork(fork.model_copy(update={"run_id": "w3-case-other"}))
    with pytest.raises(ValueError):
        await lifecycle.delete("mother")
    transport.threads["child"]["status"] = {"type": "active"}
    with pytest.raises(ValueError):
        await lifecycle.delete("child")
    assert workspace.ensure_run("w3-case-test").exists()
    transport.threads["child"]["status"] = {"type": "idle"}
    assert (await lifecycle.delete("child"))["status"] == "DELETED"
    assert (await lifecycle.delete("child"))["status"] == "DELETED"
    assert released == ["child"]
    assert "mother" in transport.threads
    assert not (tmp_path / "w3-case-test").exists()


def test_ack_retains_idempotency_receipt_and_refuses_other_workflows(tmp_path):
    store = JobStore(tmp_path)
    req = request().model_copy(update={"task_phase": "trade_decision"})
    job = WorkerJob(
        job_id="job1",
        run_id=req.run_id,
        attempt_id=req.attempt_id,
        status="succeeded",
        final_response='{"decision":"NO_TRADE"}',
    )
    store.save(job, req)
    store.event(job.job_id, "tool", {"large": "payload"})
    store.acknowledge(job.job_id)
    assert store.get(job.job_id).status == "succeeded"
    assert store.get(job.job_id).final_response is None
    assert store.request(job.job_id) is None
    assert store.events(job.job_id, -1) == []
    store.acknowledge(job.job_id)
    store.save(
        job.model_copy(update={"job_id": "legacy"}), req.model_copy(update={"task_phase": None})
    )
    with pytest.raises(ValueError):
        store.acknowledge("legacy")
