"""Execution-level acceptance of the two-stage, dual OPEN scheduler."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from doxagent.codex_runtime.concurrency import TickerConcurrency
from doxagent.codex_runtime.schema import CodexD3Node
from doxagent.codex_worker.schema import WorkerJob
from doxagent.workflows.codex_document3.orchestrator_v21 import PilotPhasePaused
from doxagent.workflows.codex_document3.state_v21 import canonical, digest
from doxagent.workflows.codex_document3.validation_v21 import normalize_agenda
from tests.test_codex_document3_v21_orchestration import NOW

pytest_plugins = ["tests.test_codex_document3_v21_orchestration"]


async def with_shells(rig, monkeypatch, count):
    prepared = await rig.orchestrator.preparer.prepare(ticker="MU", as_of=NOW)
    topo = prepared["topology"]
    for i in range(1, count + 1):
        slot = f"S{i:04d}"
        path = f"context/document3/v21/d2/shell-{i:04d}.json"
        shell = dict(slot=slot, name=f"Shell {i}", path=path, units=[])
        topo["shells"].append(shell)
        topo["shell_owners"].append(slot)
        for key in ("research_owners", "owners"):
            topo[key][slot] = shell["name"]
        topo["owner_profiles"][slot] = dict(kind="shell", primary_materials=[dict(ref=path)])
        prepared["files"][path] = canonical(shell)

    async def prepare(**_):
        return prepared

    monkeypatch.setattr(rig.orchestrator.preparer, "prepare", prepare)
    rig.orchestrator.runner.shell_concurrency = TickerConcurrency(2)


async def task_for(rig, request):
    path = re.search(r"Read task file (\S+),", request.prompt)[1]
    return json.loads((await rig.workspace.read_text(request.run_id, path)).content)


@pytest.mark.asyncio
async def test_shell_barrier_full_main_late_pack_and_isolated_open_assets(rig, monkeypatch):
    await with_shells(rig, monkeypatch, 3)
    original = rig.worker.run
    started, finished, tasks, packs = [], [], {}, {}
    two_shells, third_shell, both_open = (asyncio.Event() for _ in range(3))
    release_first, release_third, release_open = (asyncio.Event() for _ in range(3))

    async def controlled(request):
        task = await task_for(rig, request)
        owner = task["owner"]
        tasks[owner] = task
        started.append(owner)
        if owner.startswith("S"):
            if len(started) == 2:
                two_shells.set()
            if owner == "S0003":
                third_shell.set()
                await release_third.wait()
            else:
                await release_first.wait()
        else:
            assert set(finished) == {"S0001", "S0002", "S0003"}
            index = next(
                m for m in task["read_mapping"] if m["ref"] == task["prior_shell_discovery"]
            )
            packs[owner] = json.loads(
                (await rig.workspace.read_text(request.run_id, index["local_path"])).content
            )
            if len(packs) == 2:
                both_open.set()
            await release_open.wait()
        result = await original(request)
        await rig.workspace.write_text(
            request.run_id,
            task["output_paths"][1],
            "\n" + canonical(dict(name=f"{owner} late", lead="same-round late signal")) + "\n",
        )
        finished.append(owner)
        return result

    monkeypatch.setattr(rig.worker, "run", controlled)
    rig.orchestrator.pilot_stop_after_phase = "discovery"
    running = asyncio.create_task(
        rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="barrier")
    )
    await asyncio.wait_for(two_shells.wait(), 30)
    assert set(started) == {"S0001", "S0002"}
    release_first.set()
    await asyncio.wait_for(third_shell.wait(), 30)
    assert not packs
    release_third.set()
    await asyncio.wait_for(both_open.wait(), 30)
    assert packs["OPEN_RESEARCH"] == packs["OPEN_EVENT"]
    assert len(packs["OPEN_EVENT"]["leads"]) == 6
    assert len(packs["OPEN_EVENT"]["diagnostics"]) == 3
    release_open.set()
    with pytest.raises(PilotPhasePaused):
        await running
    run = rig.state.run("barrier")
    assert len(run["scan"]) == 10
    assert any(x["ref"].endswith("S0003.late.jsonl#L2") for x in run["scan"])
    assert all(x["ref"].endswith("#L2") for x in run["scan"])
    assert "GLOBAL" not in tasks
    for owner, task in tasks.items():
        mapping = [m["ref"] for m in task["read_mapping"]]
        assets = (
            rig.workspace.local.root
            / run["owner_workspaces"][owner]
            / "context/document3/v21/assets"
        )
        assert (assets / "initialize_discovery.md").is_file()
        assert (assets / "initialize_discovery_open.md").is_file() == owner.startswith("OPEN_")
        assert any("Open_Event_Atlas_General.md" in p for p in mapping) == (owner == "OPEN_EVENT")
    assert run["owner_workspaces"]["OPEN_EVENT"] != run["owner_workspaces"]["OPEN_RESEARCH"]
    assert tasks["OPEN_EVENT"]["atlas_profile"]["sector_code"] == "L1-01"
    for owner in ("OPEN_RESEARCH", "OPEN_EVENT"):
        snapshot = rig.state.task("barrier", f"discovery:{owner}")
        assert snapshot["job"]["thread_id"].endswith(owner.lower())
        assert tasks[owner]["shell_discovery_sha256"] == run["shell_discovery_input"]["sha256"]


@pytest.mark.asyncio
async def test_open_interruption_keeps_shell_pack_and_frozen_atlas(rig, monkeypatch):
    await with_shells(rig, monkeypatch, 1)
    original = rig.orchestrator._turn
    research_done = asyncio.Event()
    calls = []

    async def interrupt(run_id, owner, phase, *args, **kwargs):
        calls.append(owner)
        if owner == "OPEN_EVENT":
            await research_done.wait()
            raise asyncio.CancelledError()
        result = await original(run_id, owner, phase, *args, **kwargs)
        if owner == "OPEN_RESEARCH":
            research_done.set()
        return result

    monkeypatch.setattr(rig.orchestrator, "_turn", interrupt)
    with pytest.raises(asyncio.CancelledError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-open")
    frozen = rig.state.run("resume-open")
    before = len(rig.worker.requests)
    Path(rig.assets["open_atlas_general"]).write_text("new atlas bytes", encoding="utf8")
    monkeypatch.setattr(rig.orchestrator, "_turn", original)
    rig.orchestrator.pilot_stop_after_phase = "discovery"
    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-open")
    assert len(rig.worker.requests) == before + 1
    resumed = rig.state.run("resume-open")
    assert resumed["shell_discovery_input"] == frozen["shell_discovery_input"]
    assert resumed["assets"]["open_atlas_general"]["content"] == "fixture external asset"
    task = await task_for(rig, rig.worker.requests[-1])
    atlas = next(
        m for m in task["read_mapping"] if m["ref"].endswith("Open_Event_Atlas_General.md")
    )
    assert (
        await rig.workspace.read_text(rig.worker.requests[-1].run_id, atlas["local_path"])
    ).content == "fixture external asset"
    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="new-atlas")
    assert (
        rig.state.run("new-atlas")["assets"]["open_atlas_general"]["content"] == "new atlas bytes"
    )
    broken = dict(resumed["shell_discovery_input"])
    broken["sha256"] = digest("tampered")
    rig.state.update("resume-open", shell_discovery_input=broken)
    with pytest.raises(ValueError, match="Shell Discovery input integrity"):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-open")


def test_real_owner_routes_and_no_third_open_queue():
    owners = {
        "S0001": "same",
        "S0002": "same",
        "OPEN_RESEARCH": "OPEN_RESEARCH",
        "OPEN_EVENT": "OPEN_EVENT",
    }
    raw = {
        "topics": [
            dict(name=f"t{i}", owner=o, brief="x")
            for i, o in enumerate(
                [
                    "S0001",
                    "same::shell-0002",
                    "OPEN_RESEARCH",
                    "OPEN_EVENT",
                    "OPEN",
                    "GLOBAL",
                    "unknown",
                    "same",
                ]
            )
        ],
        "waves": [],
    }
    agenda, warnings = normalize_agenda(raw, owners, {"same::shell-0002": "S0002"})
    assert [t.owner for t in agenda.topics] == [
        "S0001",
        "S0002",
        "OPEN_RESEARCH",
        "OPEN_EVENT",
        *(["OPEN_RESEARCH"] * 4),
    ]
    assert len(warnings) >= 4
    assert all(t.owner in owners for t in agenda.topics)
    by_name = {t.name: t.owner for t in agenda.topics}
    assert all(
        sum(by_name[n] == owner for n in wave) <= 3 for wave in agenda.waves for owner in owners
    )


@pytest.mark.asyncio
async def test_failed_shell_still_finishes_barrier_and_open_build_reuses_threads(rig, monkeypatch):
    await with_shells(rig, monkeypatch, 1)
    rig.orchestrator.runner.shell_concurrency = TickerConcurrency(1)
    original = rig.worker.run
    failures, owner_tasks = [], {}
    rig.worker.agenda = {
        "topics": [
            dict(name=name, owner=owner, brief="fixture")
            for name, owner in (("T1", "OPEN_RESEARCH"), ("TE", "OPEN_EVENT"))
        ],
        "waves": [["T1"], ["TE"]],
    }

    async def controlled(request):
        task = await task_for(rig, request)
        owner = task["owner"]
        if owner == "S0001":
            failures.append(request.attempt_id)
            return WorkerJob(
                job_id=request.attempt_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                error_message="fixture outage",
            )
        if request.node == CodexD3Node.O3_DISCOVERY:
            assert len(failures) == 2
            index = next(
                m for m in task["read_mapping"] if m["ref"] == task["prior_shell_discovery"]
            )
            pack = json.loads(
                (await rig.workspace.read_text(request.run_id, index["local_path"])).content
            )
            assert pack["producers"][0]["status"] == "FAILED"
            assert pack["missing"] == ["Discovery unavailable:S0001"]
            assert not pack["leads"]
        owner_tasks[(owner, request.node)] = (request, task)
        return await original(request)

    monkeypatch.setattr(rig.worker, "run", controlled)
    rig.orchestrator.pilot_stop_after_phase = "build"
    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="failed-shell")
    run = rig.state.run("failed-shell")
    assert run["missing"] == ["Discovery unavailable:S0001"]
    assert len(run["scan"]) == 2
    for owner in ("OPEN_RESEARCH", "OPEN_EVENT"):
        discovery_request, _ = owner_tasks[(owner, CodexD3Node.O3_DISCOVERY)]
        build_request, task = owner_tasks[(owner, CodexD3Node.O3_BUILD)]
        assert discovery_request.run_id == build_request.run_id
        assert build_request.thread_id == f"thread-{discovery_request.run_id}"
        assert any("Open_Event_Atlas_General.md" in m["ref"] for m in task["read_mapping"]) == (
            owner == "OPEN_EVENT"
        )
        assert all(t["owner"] == owner for t in task["topics"])
    assert ("GLOBAL", CodexD3Node.O3_DISCOVERY) not in owner_tasks
    assert ("GLOBAL", CodexD3Node.O3_BUILD) not in owner_tasks


def test_maintain_has_no_atlas_dependency(rig):
    rig.orchestrator.runner.assets.pop("open_atlas_general")
    rig.orchestrator.runner.assets.pop("open_atlas_l1_01")
    assert set(rig.orchestrator.runner.frozen_assets("maintain")) == {"role", "common", "maintain"}


@pytest.mark.asyncio
async def test_shell_interruption_reuses_completed_shell_and_blocks_old_active_revision(
    rig, monkeypatch
):
    await with_shells(rig, monkeypatch, 2)
    original = rig.orchestrator._turn
    first_done = asyncio.Event()

    async def interrupt(run_id, owner, phase, *args, **kwargs):
        if owner == "S0002":
            await first_done.wait()
            raise asyncio.CancelledError()
        result = await original(run_id, owner, phase, *args, **kwargs)
        first_done.set()
        return result

    monkeypatch.setattr(rig.orchestrator, "_turn", interrupt)
    with pytest.raises(asyncio.CancelledError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-shell")
    assert len(rig.worker.requests) == 1
    assert "shell_discovery_input" not in rig.state.run("resume-shell")
    rig.state.update("resume-shell", orchestration_revision=2)
    with pytest.raises(ValueError, match="requires a new run"):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-shell")
    assert len(rig.worker.requests) == 1
    rig.state.update("resume-shell", orchestration_revision=3)
    monkeypatch.setattr(rig.orchestrator, "_turn", original)
    rig.orchestrator.pilot_stop_after_phase = "discovery"
    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="resume-shell")
    assert len(rig.worker.requests) == 4
    assert rig.state.task("resume-shell", "discovery:S0001")["attempt_count"] == 1
