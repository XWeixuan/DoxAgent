import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.cdecr_integration.contracts import CDECRWorkflowResult, RuntimeRegistryBinding
from doxagent.ticker_initialization import InitializationRepository, NodeContext, NodeSpec
from doxagent.ticker_initialization.cdecr_process import execute_cdecr, run_child
from doxagent.ticker_initialization.schema import LeaseLost


def setup(tmp_path):
    repo = InitializationRepository(tmp_path / "control.db")
    repo.submit("MU", datetime.now(UTC), [NodeSpec(key="cdecr", block="CDECR")])
    lease = repo.claim("owner")
    node = repo.begin(lease, "cdecr", {})
    context = NodeContext(repo, lease, node)
    binding = RuntimeRegistryBinding(
        ticker="MU", market="US", runtime_scope="US:MU", registry_path=str(tmp_path / "registry.db")
    )
    result = CDECRWorkflowResult(
        market="US",
        ticker="MU",
        runtime_scope="US:MU",
        status="FINALIZED",
        message_ids=[],
        document_count=0,
        eligible_document_count=0,
        epoch_id="offline-epoch",
        completed_at=datetime.now(UTC),
    )
    payload = {
        "control": str(repo.path),
        "lease": lease.model_dump(),
        "binding": binding.model_dump(),
        "message_ids": [],
        "as_of": datetime.now(UTC).isoformat(),
        "output": str(tmp_path / "result.json"),
    }
    return context, binding, result, payload


def test_owned_child_uses_frozen_inputs_and_atomic_output(tmp_path):
    context, binding, result, payload = setup(tmp_path)
    calls = []

    def factory(actual_binding):
        assert actual_binding == binding

        def run(messages, *, as_of):
            calls.append((messages, as_of))
            return result

        return None, SimpleNamespace(run=run)

    run_child(payload, runner_factory=factory)
    assert len(calls) == 1
    assert CDECRWorkflowResult.model_validate_json((tmp_path / "result.json").read_text()) == result
    payload["lease"]["token"] += 1
    with pytest.raises(LeaseLost):
        run_child(payload, runner_factory=factory)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_parent_cancellation_terminates_child_before_returning(tmp_path, monkeypatch):
    context, binding, result, payload = setup(tmp_path)
    started = asyncio.Event()
    stopped = asyncio.Event()

    class Process:
        returncode = None
        terminated = False

        async def wait(self):
            started.set()
            await stopped.wait()
            self.returncode = -15
            return self.returncode

        def terminate(self):
            self.terminated = True
            stopped.set()

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(execute_cdecr(context, binding, [], datetime.now(UTC)))
    await started.wait()
    context.repository.heartbeat(context.lease)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and process.returncode == -15
