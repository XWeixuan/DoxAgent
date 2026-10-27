import asyncio
import json
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.capsules import Capsule, CapsuleError


def test_fresh_w3_runtime_import_does_not_load_o4_service():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import doxagent.codex_worker.sdk_runtime; "
            "assert 'doxagent.workflows.codex_monitoring_o4.service' not in sys.modules; "
            "assert 'doxagent.workflows.codex_monitoring_o4.capability' not in sys.modules",
        ],
        check=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_ready", [True, False])
async def test_boot_requires_sdk_ready_and_preserves_import_stages(
    tmp_path, monkeypatch, sdk_ready
):
    values = [
        dict(type="boot_progress", stage="SCHEMA_IMPORT"),
        dict(type="boot_progress", stage="SDK_INITIALIZE"),
        dict(type="boot_ready", state="SDK_READY" if sdk_ready else "PYTHON_READY"),
    ]

    async def receive():
        if sdk_ready and values[0].get("type") == "boot_ready":
            # Real elapsed time, beyond the former 30s cutoff; no model invocation.
            await asyncio.sleep(35)
            values[0]["timing"] = {"sdk_ready_ms": 35000}
        return values.pop(0)

    async def stderr():
        pass

    async def spawn(*args, **kwargs):
        return SimpleNamespace(pid=123, returncode=None)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    capsule = Capsule(tmp_path)
    monkeypatch.setattr(capsule, "receive", receive)
    monkeypatch.setattr(capsule, "_drain_stderr", stderr)
    if sdk_ready:
        await capsule.launch()
        assert capsule.boot_timing["stage"] == "SDK_READY"
        assert capsule.boot_timing["boot_slow"] is True
    else:
        with pytest.raises(CapsuleError, match="SDK_READY"):
            await capsule.launch()
        assert capsule.boot_timing["stage"] == "SDK_INITIALIZE"
        assert capsule.boot_timing["parent_end_reason"] == "CapsuleError"
    assert json.loads(next((tmp_path / "capsules").glob("*/owner.json")).read_text())["pid"] == 123


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["WAIT_BOOT_SLOT", "BOOT_ATTEMPT", "FORK_DISPATCHED", "CHILD_KNOWN"]
)
async def test_cancelled_prepare_awaits_actual_execution_and_rejects_old_owner(tmp_path, stage):
    from tests.test_w3_deterministic_repair import lifecycle, prepare

    entered, cleaned = asyncio.Event(), asyncio.Event()

    class Runtime:
        manages_startup_capacity = True

        async def prepare_fork(self, *args):
            from doxagent.codex_worker.startup_admission import progress

            await progress(stage, **({"thread_id": "child"} if stage == "CHILD_KNOWN" else {}))
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

    value = lifecycle(tmp_path, Runtime())
    await value.prepare(prepare())
    await entered.wait()
    task = value.preparations["w3-fork:one"]
    owner = value.operation("w3-fork:one")["owner_token"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    row = value.operation("w3-fork:one")
    assert bool(row["fork_dispatched_at"]) is (stage == "FORK_DISPATCHED")
    assert int(row["startup_attempts"]) == int(stage == "BOOT_ATTEMPT")
    assert row["thread_id"] == ("child" if stage == "CHILD_KNOWN" else None)
    value._operation_update("w3-fork:one", owner_token="replacement")
    value._operation_update("w3-fork:one", owner=owner, status="COMPLETED")
    assert value.operation("w3-fork:one")["status"] == "CREATING"
    await value.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transient", "permanent"])
async def test_claim_maps_transport_and_retryable_conflict_to_original_prepare(failure):
    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient
    from doxagent.codex_runtime.errors import W3PreparationError

    def handle(request):
        if failure == "transient":
            raise httpx.ReadError("lost claim response", request=request)
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "scope_not_ready",
                    "message": "original owner pending",
                    "retryable": True,
                }
            },
        )

    client = HttpCodexWorkerClient("http://worker", "t" * 32)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://worker", transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(W3PreparationError) as exc:
            await client.claim_w3_ready({"case_id": "case", "record_id": "original"})
        assert exc.value.code == "w3_prepare_pending" and exc.value.retryable
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_fixed_deadline_reserves_cleanup_and_does_not_refresh_on_post(tmp_path):
    import time

    from tests.test_w3_deterministic_repair import lifecycle, prepare

    class Runtime:
        manages_startup_capacity = True
        cleaned = False

        async def prepare_fork(self, *args):
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaned = True

    runtime = Runtime()
    value = lifecycle(tmp_path, runtime)
    value.jobs.w3_prepare_timeout = 15.2
    await value.prepare(prepare())
    task = value.preparations["w3-fork:one"]
    await asyncio.sleep(0.01)
    deadline = value.operation("w3-fork:one")["prepare_deadline_at"]
    await value.prepare(prepare())
    assert value.operation("w3-fork:one")["prepare_deadline_at"] == deadline
    await asyncio.wait_for(task, 1)
    assert runtime.cleaned and value.operation("w3-fork:one")["status"] == "FAILED"
    assert time.time() < float(deadline)
    await value.close()


@pytest.mark.asyncio
async def test_boot_only_failure_skips_queries_and_exhausts_one_shared_budget(tmp_path):
    from tests.test_w3_deterministic_repair import lifecycle, prepare

    class Runtime:
        manages_startup_capacity = True
        calls = 0

        async def prepare_fork(self, *args):
            from doxagent.codex_worker.startup_admission import progress

            await progress("BOOT_ATTEMPT")
            self.calls += 1
            raise TimeoutError("boot before Fork")

    class Transport:
        async def request(self, *args):
            pytest.fail("known boot failure must not query threads")

        async def close(self):
            pass

    runtime = Runtime()
    value = lifecycle(tmp_path, runtime, Transport())
    for _ in range(2):
        await value.prepare(prepare())
        await value.preparations["w3-fork:one"]
        value._operation_update("w3-fork:one", next_retry_at="0")
    row = value.operation("w3-fork:one")
    assert row["status"] == "FAILED" and row["startup_attempts"] == "2"
    await value.prepare(prepare())
    assert not value.preparations and runtime.calls == 2
    await value.close()


@pytest.mark.asyncio
async def test_legacy_unknown_query_failure_never_reforks(tmp_path):
    from tests.test_w3_deterministic_repair import lifecycle, prepare

    class Runtime:
        async def prepare_fork(self, *args):
            pytest.fail("failed recovery query is not proof of no child")

    class Transport:
        async def request(self, *args):
            raise ConnectionError("helper unavailable")

        async def close(self):
            pass

    value = lifecycle(tmp_path, Runtime(), Transport())
    request = prepare()
    with value.jobs.store.connect() as db, db:
        db.execute(
            "INSERT INTO w3_thread_operations(operation_key,request,status) VALUES(?,?,'CREATING')",
            (request.operation_key, json.dumps(request.model_dump(mode="json"), sort_keys=True)),
        )
    await value.prepare(request)
    await value.preparations[request.operation_key]
    row = value.operation(request.operation_key)
    assert row["status"] == "CREATING" and row["fork_dispatched_at"] is None
    await value.close()
