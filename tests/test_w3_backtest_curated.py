import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.w3_backtest.curated import parse_catalog
from doxagent.w3_backtest.decision_runner import DecisionReplayRunner
from doxagent.w3_backtest.market import freeze_market


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_catalog_import_skips_mapping_rows_and_previous_noise(tmp_path):
    catalog = tmp_path / "catalog.md"
    previous = tmp_path / "previous"
    write(previous / "inputs/NOISE-01/case.json", dict(original_case_id="noise"))
    lines = ["| No | Case | Title | Action | Tags |"]
    for i in range(59):
        key = f"CASE-{i:02}" if i < 48 else f"ADD-X-{i:02}"
        target = "LONG" if i < 4 else "SHORT" if i < 7 else "移除" if 33 <= i < 48 else "NO_TRADE"
        tags = "时效性、边缘案例" if i < 9 else "边缘案例" if i < 12 else "—"
        lines.append(f"| {i + 1} | {key} | news | {target} | {tags} |")
    lines.append("| Case | Title | Runtime ID |")
    for i in range(48, 59):
        lines.append(f"| ADD-X-{i:02} | news | `case_{i:032x}` |")
    catalog.write_text("\n".join(lines), encoding="utf-8")
    rows = parse_catalog(catalog, previous)
    assert sum(bool(r["original_case_id"]) for r in rows) == 11
    active = [r for r in rows if r["included"]]
    assert len(rows) == 59 and len(active) == 44
    assert not any(r["case_key"].startswith("NOISE-") for r in active)
    assert sum(r["target"] == "LONG" for r in active) == 4
    assert sum(r["target"] == "SHORT" for r in active) == 3
    assert sum("时效性" in r["tags"] for r in active) == 9


def test_completed_prices_only():
    cutoff = "2026-10-27T16:02:00+00:00"

    def bar(at):
        return dict(date=at, open=1, high=2, low=1, close=2, volume=3)

    rows = dict(
        daily=[
            (
                "SMART",
                dict(bars=[bar("2026-10-26T04:00:00+00:00"), bar("2026-10-27T04:00:00+00:00")]),
            )
        ],
        hourly=[
            (
                "SMART",
                dict(bars=[bar("2026-10-27T15:00:00+00:00"), bar("2026-10-27T16:00:00+00:00")]),
            )
        ],
        fine=[
            (
                "SMART",
                dict(bars=[bar("2026-10-27T15:55:00+00:00"), bar("2026-10-27T16:00:00+00:00")]),
            )
        ],
    )
    view = freeze_market(rows, cutoff, "INTC")
    assert all(len(v["rows"]) == 1 and not v["forming"] for v in view["series"].values())
    assert "gaps" not in json.dumps(view)


@pytest.mark.parametrize("time_awareness_enabled", [False, True])
def test_shared_mother_fresh_children_and_no_operations(
    tmp_path, monkeypatch, time_awareness_enabled
):
    from doxagent.persistent_runtime_v2.w3_time_awareness import time_context
    from doxagent.w3_backtest import decision_runner as module

    origins = {}
    before = datetime.now(UTC)

    class Worker:
        async def prepare_w3_research(self, ticker, components):
            assert set(components) == {"d1", "d2"}
            assert bool(origins) is time_awareness_enabled
            return components

        async def bind_w3_context(self, case_id, phase, manifest):
            assert not ({"history", "state", "policy_usage"} & manifest.keys())
            assert (case_id in origins) is time_awareness_enabled
            return dict(context_ref=case_id)

    class Agent:
        phases = []
        parents = []

        def __init__(self, **kwargs):
            self.journal = kwargs["journal"]
            assert kwargs["time_awareness_enabled"] is time_awareness_enabled

        def begin_trade_timing(self, case_id):
            origins[case_id] = time_context(self.journal, case_id, start=True)
            return origins[case_id]

        def _wait_v4(self, awaitable):
            return asyncio.run(awaitable)

        def run_phase(self, **kwargs):
            phase = kwargs["phase"]
            self.phases.append(phase)
            payload = kwargs["payload"]
            assert not (
                {"gold", "tags", "deadline_at", "watch", "pins", "policies",
                 "started_at_utc", "time_awareness"} & payload.keys()
            )
            if phase == "w3_template":
                assert not origins
                assert payload == dict(template_format="w3-entry-v5")
                return {"ready": True}, SimpleNamespace(job_id="mother", thread_id="mother")
            return dict(
                decision="LONG", reason="r", prior_expectation="p", expectation_delta="d"
            ), SimpleNamespace(
                job_id=kwargs["case"].case_id, thread_id=kwargs["record"]["thread_id"]
            )

        def register_template(self, _):
            pass

        def acknowledge_job(self, _):
            pass

        def prepare_thread(self, request):
            self.parents.append(request.parent_thread_id)
            return dict(thread_id=request.run_id)

        def close(self):
            pass

    monkeypatch.setattr(module, "CodexW3AgentRunner", Agent)
    monkeypatch.setattr(module, "native_tool_audit", lambda *_: {})
    dataset = tmp_path / "dataset"
    prompts = tmp_path / "prompts"
    root = tmp_path / "run"
    for name in ("AGENTS.md", "skills/skill.md", "backtest.md"):
        p = prompts / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("instructions")
    write(prompts / "contract.json", dict(w3_contract_version="persistent-runtime.w3.v5"))
    for key in ("C1", "C2"):
        raw = dict(
            case_key=key,
            ticker="INTC",
            as_of="2026-10-27T16:02:00+00:00",
            research_key="r",
            message=dict(title="news"),
            w1={},
            w2={},
        )
        write(dataset / "inputs" / key / "case.json", raw)
        folder = dataset / "inputs" / key / "evidence"
        write(folder / "index.json", dict(sources=[]))
        write(
            folder / "market_view.json",
            dict(
                observed_at=raw["as_of"],
                series={n: dict(rows=[[1]]) for n in ("daily", "hourly", "fine")},
            ),
        )
    research = dataset / "research/r"
    research.mkdir(parents=True)
    (research / "document1.md").write_text("d1")
    (research / "document2.json").write_text("{}")
    runner = DecisionReplayRunner(
        dataset, root, prompt_root=prompts, worker_factory=lambda _: (Worker(), None),
        time_awareness_enabled=time_awareness_enabled,
    )
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda key: runner.run_case(dataset, key, root), ("C1", "C2")))
        assert all(
            r["status"] == "SUCCEEDED" and r["operations_status"] == "NOT_REQUESTED"
            for r in results
        )
        assert len({r["thread_id"] for r in results}) == 2
        assert Agent.phases.count("w3_template") == 1 and Agent.phases.count("trade_decision") == 2
        assert Agent.parents == ["mother", "mother"]
        if time_awareness_enabled:
            assert len(origins) == 2
            for r in results:
                at = datetime.fromisoformat(r["time_awareness"]["started_at_utc"])
                assert before <= at <= datetime.now(UTC)
                assert r["cutoff_at"] == "2026-10-27T16:02:00+00:00"
                assert time_context(runner.journal, r["replay_case_id"], start=True) == origins[
                    r["replay_case_id"]
                ]
        else:
            assert all("time_awareness" not in r for r in results)
        with runner.journal.transaction(write=False) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM runtime_v2_w3_stages "
                    "WHERE phase='post_decision_operations'"
                ).fetchone()[0]
                == 0
            )
    finally:
        runner.close()


def test_private_runtime_keeps_each_prepared_child():
    from doxagent.w3_backtest.runner import _ReplayRuntime

    class Native:
        _prepared_thread = None

        async def prepare_fork(self, request, cwd, parent):
            self._prepared_thread = (request.thread_id, "scope", {})
            return request.thread_id

        async def start(self, request, cwd):
            assert self._prepared_thread[0] == request.thread_id
            self.receipt_callback(request.thread_id, "turn")
            return request.thread_id

    async def run():
        bridge = _ReplayRuntime(Native())
        a = SimpleNamespace(thread_id="A")
        b = SimpleNamespace(thread_id="B")
        await bridge.prepare_fork(a, None, "mother")
        await bridge.prepare_fork(b, None, "mother")
        assert await bridge.start(a, None) == "A"
        assert await bridge.start(b, None) == "B"
        assert not bridge._prepared

    asyncio.run(run())


def test_date_message_does_not_expose_placeholder_time():
    from doxagent.w3_backtest.decision_runner import agent_payload

    raw = dict(
        ticker="NBIS",
        as_of="2026-10-22T11:05:00Z",
        message=dict(
            title="financing", publication_time_basis="DATE", published_at="2026-10-22T16:00:00Z"
        ),
    )
    assert agent_payload(raw)["message"]["published_at"] == "2026-10-22"
    assert raw["message"]["published_at"] == "2026-10-22T16:00:00Z"


def test_weekend_market_is_empty_without_fabricated_bars():
    raw = dict(
        daily=[
            ("SMART", dict(bars=[dict(date="20260925", open=1, high=2, low=1, close=2, volume=3)]))
        ],
        hourly=[("SMART", dict(bars=[]))],
        fine=[("SMART", dict(bars=[]))],
    )
    view = freeze_market(raw, "2026-09-27T15:23:01Z", "INTC")
    assert view["series"]["daily"]["rows"]
    assert view["series"]["hourly"]["session_state"] == "MARKET_CLOSED"
    assert view["series"]["fine"]["rows"] == []


def test_offline_backtest_contract_is_loaded_with_mother(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner, W3ExecutionEnvironment
    from doxagent.persistent_runtime_v2.w3_models import freeze_case_profile
    from tests.test_persistent_runtime_w3 import _Context, _Workspace
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_case_models import ModelWorker

    case, _ = fixture_case()
    case.frozen_inputs["w3_assets"] = {
        "w3/AGENTS.md": "agents",
        "w3/skills/skill.md": "business skill",
        "w3/backtest.md": "中文回测规则：固定as_of，信任资料，只做决策。",
    }
    case.frozen_inputs["w3_replay"] = dict(cutoff_at=case.source.published_at.isoformat())
    freeze_case_profile(case)
    workspace = _Workspace()
    worker = ModelWorker()
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=workspace,
        context_provider=_Context(),
        prompt_root=tmp_path,
        journal=RuntimeJournal(tmp_path / "r.db"),
        environment=W3ExecutionEnvironment.offline(),
        timeout_seconds=30,
    )
    try:
        runner.run_phase(
            case=case,
            record=dict(record_id="mother", workspace="mother", thread_id=None),
            phase="w3_template",
            payload=dict(template_format="instructions-only-v1"),
        )
        assert workspace.files["mother:backtest.md"].startswith("中文回测规则")
        assert "中文回测规则" in worker.requests[0].prompt
        assert "只做决策" in worker.requests[0].prompt
    finally:
        runner.close()


def test_curated_import_does_not_overwrite_legacy(tmp_path):
    import pytest

    from doxagent.w3_backtest.cli import parser
    from doxagent.w3_backtest.curated import prepare_curated

    write(tmp_path / "manifest.json", dict(schema_version="w3-backtest.dataset.v1"))
    with pytest.raises(ValueError, match="legacy"):
        prepare_curated(tmp_path)
    args = parser().parse_args(["prepare-curated"])
    assert args.dataset == "w3_curated_20261030"


def test_report_uses_revised_active_selection(tmp_path):
    from doxagent.w3_backtest.report import render_report

    dataset = tmp_path / "dataset"
    run = tmp_path / "run"
    write(
        dataset / "manifest.json",
        dict(schema_version="w3-backtest.dataset.v2", cases=[dict(case_key="C1")]),
    )
    write(dataset / "inputs/C1/case.json", dict(case_key="C1"))
    (dataset / "gold.jsonl").write_text(
        json.dumps(dict(case_key="C1", expected_action="NO_TRADE")) + "\n"
    )
    write(run / "run_manifest.json", dict(dataset_path=str(dataset)))
    for key in ("C1", "REMOVED"):
        write(
            run / "cases" / key / "result.json",
            dict(
                case_key=key,
                status="SUCCEEDED",
                decision=dict(decision="NO_TRADE"),
                evaluation_scope="w3_decision_only",
                started_at="2026-10-30T00:00:00Z",
                finished_at="2026-10-30T00:01:00Z",
            ),
        )
    result = render_report(run, dataset)
    assert result["cases"] == 1 and result["correct"] == 1
    assert result["excluded_cases"] == ["REMOVED"]


def test_user_exclusion_survives_curated_reimport(tmp_path, monkeypatch):
    from doxagent.w3_backtest import curated

    catalog = tmp_path / "source.md"
    catalog.write_text("source", encoding="utf-8")
    root = tmp_path / "dataset"
    write(root / "manifest.json", dict(schema_version=curated.SCHEMA))
    write(
        root / "preparation/selection_overrides.json",
        {"BE-02-04": dict(included=False, reason="user removed")},
    )
    monkeypatch.setattr(
        curated,
        "parse_catalog",
        lambda *_: [dict(case_key="BE-02-04", included=True, target="NO_TRADE")],
    )
    monkeypatch.setattr(curated, "snapshot_production_prompts", lambda *_: None)
    result = curated.prepare_curated(root, catalog=catalog, previous=tmp_path / "previous")
    assert result["case_count"] == 0
    row = json.loads((root / "catalog.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["included"] is False and row["target"] == "移除"
