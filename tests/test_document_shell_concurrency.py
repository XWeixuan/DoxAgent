from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from pydantic import BaseModel

from doxagent.codex_runtime.schema import CodexD2Node, CodexD3Node
from doxagent.codex_worker.schema import WorkerJob
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.orchestrator_v21 import PilotPhasePaused
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from tests.test_codex_document2_v21_orchestration import Worker, seed, setup
from tests.test_codex_document3_v21_orchestration import NOW
from tests.test_codex_document3_v21_orchestration import rig as rig  # noqa: F401


def test_document_concurrency_defaults_and_overrides(monkeypatch):
    for name in ("DOXAGENT_CODEX_D2_MAX_CONCURRENCY", "DOXAGENT_CODEX_D3_MAX_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    settings = DoxAgentSettings(_env_file=None)
    assert (settings.codex_d2_max_concurrency, settings.codex_d3_max_concurrency) == (4, 4)
    monkeypatch.setenv("DOXAGENT_CODEX_D2_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("DOXAGENT_CODEX_D3_MAX_CONCURRENCY", "2")
    settings = DoxAgentSettings(_env_file=None)
    assert (settings.codex_d2_max_concurrency, settings.codex_d3_max_concurrency) == (3, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [4, 2])
async def test_d2_six_shells_obey_configured_limit(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("DOXAGENT_CODEX_D2_MAX_CONCURRENCY", str(limit))
    _, workspace, _, _, orchestrator, request = await setup(tmp_path)

    class SixShellWorker(Worker):
        active = 0
        peak = 0

        async def _output(self, req):
            if req.node == CodexD2Node.O1_OPEN_DISCOVERY:
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0.03)
                    return await super()._output(req)
                finally:
                    self.active -= 1
            result = await super()._output(req)
            if req.node == CodexD2Node.O0_FINALIZATION:
                result["shells"] = [seed(f"shell-{i}") for i in range(6)]
            return result

    worker = SixShellWorker(workspace)
    orchestrator._runner._worker.worker = worker
    bundle = await orchestrator.run(request)
    assert worker.peak == limit
    assert len(bundle.shell_outcomes) == 6
    assert Counter(r.node for r in worker.requests)[CodexD2Node.O1_FINALIZATION] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,node", [
    ("discovery", CodexD3Node.O3_DISCOVERY),
    ("build", CodexD3Node.O3_BUILD),
])
async def test_d3_two_tickers_each_reach_four_without_exceeding_limit(
    rig, monkeypatch, phase, node
):
    owners = {f"S{i:04d}": f"Shell {i}" for i in range(6)}

    async def prepare(**_):
        return {
            "files": {}, "manifest": [],
            "topology": {
                "shells": [], "research_owners": owners,
                "fallback_owner": "S0000",
                "owners": {**owners, "GLOBAL": "GLOBAL"},
            },
            "document2_ref": None, "event_library_ref": None,
            "warnings": [], "as_of": NOW.isoformat(),
        }

    monkeypatch.setattr(rig.orchestrator.preparer, "prepare", prepare)
    rig.worker.agenda = {
        "topics": [
            {"name": f"T{i}", "owner": name, "brief": "fixture"}
            for i, name in enumerate(owners.values())
        ],
        "waves": [[f"T{i}"] for i in range(6)],
    }
    if phase == "build":
        # Materialize upstream inputs before timing the concurrent Build launches.
        rig.orchestrator.pilot_stop_after_phase = "planning"
        prepared = await asyncio.gather(*(
            rig.orchestrator.initialize(
                ticker=ticker, as_of=NOW, run_id=f"limit-{phase}-{ticker.lower()}"
            ) for ticker in ("MU", "INTC")
        ), return_exceptions=True)
        assert all(isinstance(result, PilotPhasePaused) for result in prepared), prepared
    rig.orchestrator.pilot_stop_after_phase = phase
    original = rig.worker.run
    active, peak = Counter(), Counter()
    total_peak = 0
    full, release = asyncio.Event(), asyncio.Event()

    async def tracked(request):
        nonlocal total_peak
        if request.node != node:
            return await original(request)
        ticker = request.ticker
        active[ticker] += 1
        peak[ticker] = max(peak[ticker], active[ticker])
        total_peak = max(total_peak, sum(active.values()))
        if active["MU"] == active["INTC"] == 4:
            full.set()
        try:
            await release.wait()
            return await original(request)
        finally:
            active[ticker] -= 1

    monkeypatch.setattr(rig.worker, "run", tracked)
    tasks = [asyncio.create_task(rig.orchestrator.initialize(
        ticker=ticker, as_of=NOW, run_id=f"limit-{phase}-{ticker.lower()}"
    )) for ticker in ("MU", "INTC")]
    try:
        await asyncio.wait_for(full.wait(), timeout=30)
        await asyncio.sleep(0.03)
        assert active == {"MU": 4, "INTC": 4}
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, PilotPhasePaused) for result in results), results
    assert peak == {"MU": 4, "INTC": 4}
    assert total_peak == 8


@pytest.mark.asyncio
async def test_d2_o0_budget_is_independent_per_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("DOXAGENT_CODEX_D2_MAX_CONCURRENCY", "4")
    _, _, _, _, orchestrator, _ = await setup(tmp_path)
    active = Counter()
    full, release = asyncio.Event(), asyncio.Event()

    async def turn(ticker):
        active[ticker] += 1
        if active["MU"] == active["INTC"] == 4:
            full.set()
        try:
            await release.wait()
        finally:
            active[ticker] -= 1

    tasks = [asyncio.create_task(orchestrator._bounded_o0(
        ticker, lambda ticker=ticker: turn(ticker)
    )) for ticker in ("MU", "INTC") for _ in range(6)]
    try:
        await asyncio.wait_for(full.wait(), timeout=5)
        await asyncio.sleep(0.03)
        assert active == {"MU": 4, "INTC": 4}
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [4, 2])
async def test_d3_legacy_runner_honors_per_ticker_setting(monkeypatch, limit):
    monkeypatch.setenv("DOXAGENT_CODEX_D3_MAX_CONCURRENCY", str(limit))
    active, peak = Counter(), Counter()
    full, release = asyncio.Event(), asyncio.Event()

    class Receipt(BaseModel):
        completed: bool

    class WorkerStub:
        async def run(self, request):
            ticker = request.ticker
            active[ticker] += 1
            peak[ticker] = max(peak[ticker], active[ticker])
            if active["MU"] == active["INTC"] == limit:
                full.set()
            try:
                await release.wait()
                return WorkerJob(
                    job_id=request.attempt_id, run_id=request.run_id,
                    attempt_id=request.attempt_id, status="succeeded",
                    final_response='{"completed":true}',
                )
            finally:
                active[ticker] -= 1

    runner = Document3AgentRunner(
        worker=WorkerStub(), workspace=None, model="fixture", model_provider=None
    )

    async def prepare(**_):
        return Receipt.model_json_schema()

    monkeypatch.setattr(runner, "prepare_node_contracts", prepare)
    tasks = [asyncio.create_task(runner._run_with_resume(
        run_id=f"legacy-{ticker}-{i}", ticker=ticker, cutoff_at=NOW,
        node=CodexD3Node.O3_TRIGGER_CALIBRATION, output_model=Receipt,
        max_attempts=1, required_context_paths=(), instruction="fixture",
    )) for ticker in ("MU", "INTC") for i in range(6)]
    try:
        await asyncio.wait_for(full.wait(), timeout=5)
        await asyncio.sleep(0.03)
        assert active == {"MU": limit, "INTC": limit}
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(result.completed for result, _ in results)
    assert peak == {"MU": limit, "INTC": limit}
