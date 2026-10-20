import asyncio
from contextlib import closing
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.thread_lifecycle import WorkerThreadLifecycle
from doxagent.codex_worker.w3_ready import W3ReadySlots
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore


class Runtime:
    def __init__(self):
        self.forks = 0

    async def prepare_fork(self, request, cwd, parent):
        self.forks += 1
        assert request.research_assets_mcp_enabled and not request.data_mcp_enabled
        assert request.w3_entry_version == "w3-entry-v5"
        await asyncio.sleep(0.01)
        return "child-" + request.run_id


@pytest.mark.asyncio
async def test_failed_empty_bound_slot_reconciles_original_operation_once(tmp_path):
    from doxagent.codex_worker.w3_ready import ReadySlotError

    class FlakyRuntime(Runtime):
        fail = True

        async def prepare_fork(self, request, cwd, parent):
            if self.fail:
                raise TimeoutError("startup timed out")
            return await super().prepare_fork(request, cwd, parent)

    class Threads:
        async def request(self, method, params):
            assert method == "thread/list"
            return {"data": []}

        async def close(self):
            pass

    runtime = FlakyRuntime()
    jobs = SimpleNamespace(
        store=JobStore(tmp_path), _runtime=runtime, _startup=asyncio.Semaphore(3)
    )
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, LocalWorkspaceStore(tmp_path), Threads())
    with closing(jobs.store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    slots = W3ReadySlots(
        jobs,
        LocalWorkspaceStore(tmp_path),
        SimpleNamespace(w3_ready_luna=0, w3_ready_sol=0),
        SimpleNamespace(ready={}),
    )
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    await slots.configure(
        dict(parent_thread_id="parent", profile=profile, instructions={"AGENTS.md": "public"})
    )
    request = dict(case_id="case", record_id="record", parent_thread_id="parent", profile=profile)
    original = await slots.claim(request)
    await asyncio.gather(*list(slots.tasks.values()))
    runtime.fail = False
    restored = await slots.claim(request)
    assert restored["status"] == "CREATING"
    await asyncio.gather(*list(slots.tasks.values()))
    result = await slots.claim(request)
    assert result["status"] == "COMPLETED" and result["slot_id"] == original["slot_id"]
    assert runtime.forks == 1
    with pytest.raises(ReadySlotError) as exc:
        await slots.claim({**request, "profile": {**profile, "effort": "low"}})
    assert exc.value.code == "w3_ready_identity_conflict"
    await slots.close()
    await jobs.thread_lifecycle.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('model,target', [('gpt-6-luna',2),('gpt-6.1-sol',1)])
async def test_bound_ready_inventory_is_idempotent_concurrent_and_not_per_ticker(tmp_path,model,target):
    workspaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(
        store=JobStore(tmp_path), _runtime=Runtime(), _startup=asyncio.Semaphore(3)
    )
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces)
    with closing(jobs.store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    settings = SimpleNamespace(w3_ready_luna=2, w3_ready_sol=1)
    slots = W3ReadySlots(jobs, workspaces, settings, SimpleNamespace(ready={}))
    profile = dict(model=model, provider=None, effort="medium" if model=='gpt-6.1-sol' else "max")
    config = dict(parent_thread_id="parent", profile=profile, instructions={"AGENTS.md": "public"})
    try:
        key = await slots.configure(config)
        await asyncio.gather(*list(slots.tasks.values()))
        requests = [
            dict(
                case_id=f"case{i}",
                record_id=f"record{i}",
                parent_thread_id="parent",
                profile=profile,
            )
            for i in range(20)
        ]
        values = await asyncio.gather(*(slots.claim(r) for r in requests))
        await asyncio.gather(*list(slots.tasks.values()))
        values = await asyncio.gather(*(slots.claim(r) for r in requests))
        assert len({v["slot_id"] for v in values}) == 20
        assert all(v["status"] == "COMPLETED" for v in values)
        assert (await slots.claim(requests[0]))["slot_id"] == values[0]["slot_id"]
        with closing(jobs.store.connect()) as db:
            count = db.execute(
                "SELECT count(*) FROM w3_ready_slots WHERE scope=? AND state='READY'", (key,)
            ).fetchone()[0]
            assert count == target
            assert (
                db.execute(
                    "SELECT kind FROM w3_thread_registry WHERE thread_id=?",
                    (values[0]["thread_id"],),
                ).fetchone()[0]
                == "CASE"
            )
    finally:
        await slots.close()
        await jobs.thread_lifecycle.close()


@pytest.mark.asyncio
async def test_restart_reclaims_original_bound_slot_and_completed_fork(tmp_path):
    workspaces = LocalWorkspaceStore(tmp_path)
    runtime = Runtime()
    jobs = SimpleNamespace(
        store=JobStore(tmp_path), _runtime=runtime, _startup=asyncio.Semaphore(3)
    )
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces)
    with closing(jobs.store.connect()) as db, db:
        db.execute(
            "INSERT INTO w3_thread_registry VALUES('parent','TEMPLATE',NULL,'mother','ACTIVE')"
        )
    settings = SimpleNamespace(w3_ready_luna=1, w3_ready_sol=1)
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    slots = W3ReadySlots(jobs, workspaces, settings, SimpleNamespace(ready={}))
    await slots.configure(
        dict(parent_thread_id="parent", profile=profile, instructions={"AGENTS.md": "public"})
    )
    await asyncio.gather(*list(slots.tasks.values()))
    request = dict(case_id="case", record_id="record", parent_thread_id="parent", profile=profile)
    original = await slots.claim(request)
    await asyncio.gather(*list(slots.tasks.values()))
    await slots.close()
    before = runtime.forks
    with closing(jobs.store.connect()) as db, db:
        db.execute("UPDATE w3_ready_slots SET thread_id=NULL WHERE id=?", (original["slot_id"],))
    restarted = W3ReadySlots(jobs, workspaces, settings, SimpleNamespace(ready={}))
    try:
        assert (await restarted.claim(request))["status"] == "CREATING"
        await asyncio.gather(*list(restarted.tasks.values()))
        recovered = await restarted.claim(request)
        assert recovered["thread_id"] == original["thread_id"]
        assert recovered["slot_id"] == original["slot_id"] and runtime.forks == before
    finally:
        await restarted.close()
        await jobs.thread_lifecycle.close()


@pytest.mark.asyncio
async def test_old_version_recovery_cannot_retire_current_public_inventory(tmp_path):
    workspaces = LocalWorkspaceStore(tmp_path)
    jobs = SimpleNamespace(
        store=JobStore(tmp_path), _runtime=Runtime(), _startup=asyncio.Semaphore(3)
    )
    jobs.thread_lifecycle = WorkerThreadLifecycle(jobs, workspaces)
    with closing(jobs.store.connect()) as db, db:
        for parent in ("current", "previous"):
            db.execute(
                "INSERT INTO w3_thread_registry VALUES(?,'TEMPLATE',NULL,?,'ACTIVE')",
                (parent, parent),
            )
    slots = W3ReadySlots(
        jobs,
        workspaces,
        SimpleNamespace(w3_ready_luna=1, w3_ready_sol=1),
        SimpleNamespace(ready={}),
    )
    profile = dict(model="gpt-6-luna", provider=None, effort="max")
    try:
        key = await slots.configure(
            dict(parent_thread_id="current", profile=profile, instructions={"AGENTS.md": "new"})
        )
        await asyncio.gather(*list(slots.tasks.values()))
        await slots.configure(
            dict(
                parent_thread_id="previous",
                profile=profile,
                instructions={"AGENTS.md": "old"},
                active_public=False,
            )
        )
        with closing(jobs.store.connect()) as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM w3_ready_slots WHERE scope=? AND state='READY'", (key,)
                ).fetchone()[0]
                == 1
            )
    finally:
        await slots.close()
        await jobs.thread_lifecycle.close()


@pytest.mark.asyncio
async def test_v5_attempt_and_cutoff_changes_keep_prepared_sdk_and_mcp(tmp_path):
    from datetime import timedelta

    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from doxagent.settings import DoxAgentSettings
    from tests.test_w3_thread_lifecycle import request

    calls = []

    class Thread:
        id = "child"

        async def turn(self, *args, **kwargs):
            calls.append("turn")
            return SimpleNamespace(id="turn")

    class Client:
        async def thread_fork(self, *args, **kwargs):
            calls.append("fork")
            assert kwargs["config"]["mcp_servers.w3_research.enabled"]
            for server in ("data", "source_capture", "o4_operations", "d2_discovery"):
                prefix = f"mcp_servers.{server}."
                config = kwargs["config"]
                assert config[prefix + "enabled"] is False
                assert config[prefix + "command"]
                assert config[prefix + "args"][:1] == ["-m"]
                assert config[prefix + "args"][1].startswith("doxagent.")
                assert not any(k.startswith(prefix + "env.") for k in config)
            return Thread()

        async def thread_resume(self, *args, **kwargs):
            calls.append("resume")
            return Thread()

        async def close(self):
            pass

    runtime = OpenAICodexRuntime(settings=DoxAgentSettings(codex_capability_secret="s" * 32))
    await runtime._client.close()
    runtime._client = Client()
    req = request(data_mcp_enabled=False).model_copy(
        update=dict(
            w3_entry_version="w3-entry-v5",
            research_assets_mcp_enabled=True,
            task_phase="trade_decision",
            web_search_mode="disabled",
        )
    )
    try:
        assert await runtime.prepare_fork(req, tmp_path, "mother") == "child"
        await runtime.start(req.model_copy(update=dict(thread_id="child")), tmp_path)
        await runtime.start(
            req.model_copy(
                update=dict(
                    thread_id="child",
                    attempt_id="operations",
                    task_phase="post_decision_operations",
                    cutoff_at=req.cutoff_at + timedelta(days=1),
                )
            ),
            tmp_path,
        )
        assert calls == ["fork", "turn", "turn"]
    finally:
        await runtime.close()
