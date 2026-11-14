import json

import pytest

from doxagent.persistent_runtime_v2.case_outcomes import effective_novelty, effective_policy
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.w3_contracts import (
    W3_V6_CONTRACT,
    W3OperationsResultV6,
    parse_w3_operations,
)
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
from doxagent.persistent_runtime_v2.w3_v6 import operations_input, trade_input
from tests.test_w3_v5_execution import setup_case


def test_schema_and_upstream_authority():
    assert parse_w3_operations({"pin_actions": [], "watch_actions": []}, version=W3_V6_CONTRACT)[
        0
    ] == W3OperationsResultV6(pin_actions=[], watch_actions=[])
    for key in ["novelty", "delta_candidates", "policy", "decision"]:
        with pytest.raises(ValueError):
            parse_w3_operations(
                {"pin_actions": [], "watch_actions": [], key: {}}, version=W3_V6_CONTRACT
            )
    case = dict(
        w3_contract_version=W3_V6_CONTRACT,
        w1_final={"result": "NEW"},
        w2_final={"policy_hit": True},
        w3_operations_result={"novelty": {"result": "OLD"}, "policy": {}},
        w3_operations_status="COMMITTED",
    )
    assert effective_novelty(case) == ({"result": "NEW"}, "W1")
    assert effective_policy(case) == ({"policy_hit": True}, "W2")


def test_binding_is_atomic_and_never_reuses(tmp_path):
    j = RuntimeJournal(tmp_path / "r.db")
    r = W3ThreadRegistry(j)
    r.begin_stage("A", "trade_decision")
    r.begin_stage("B", "trade_decision")

    def create(cid):
        return r.create_case(ticker="MU", case_id=cid, compatibility="same", parent_id="public")

    a = create("A")
    r.release("A")
    b = create("B")
    assert a["record_id"] != b["record_id"]
    assert create("A")["record_id"] == a["record_id"]
    assert r.stage("A", "trade_decision")["input_refs"]["record_id"] == a["record_id"]
    assert create("A")["expires_at"] == a["expires_at"]


@pytest.mark.parametrize("action,hit", [("NO_TRADE", True), ("LONG", False)])
def test_v6_commits_without_novelty_and_no_duplicate_actions(tmp_path, action, hit):
    service, j, agent, case, _ = setup_case(tmp_path, action, hit)
    original = agent.run_phase

    def run(**kw):
        result, job = original(**kw)
        if kw["phase"] == "post_decision_operations":
            return W3OperationsResultV6(pin_actions=[], watch_actions=[]), job
        return result, job

    agent.run_phase = run
    case.w3_contract_version = W3_V6_CONTRACT
    service.repository.save_case(case)
    try:
        assert service.process_pending_effects() == 1
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_trade_decision["decision"] == action
        assert saved.w1_final.result == case.w1_final.result
        assert saved.w2_final.policy_hit == hit
        if action == "NO_TRADE":
            assert (
                service.repository._connect()
                .execute("SELECT 1 FROM runtime_v2_archives WHERE case_id=?", (case.case_id,))
                .fetchone()
            )
        assert service.process_pending_effects() == 1
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_operations_status == "COMMITTED"
        assert saved.w3_operations_result == dict(pin_actions=[], watch_actions=[])
        assert len(j.values("w3_operations_applied")) == 1
    finally:
        service.close()


def test_scoped_research_cannot_recover_legacy_policy(tmp_path):
    from tests.test_w3_research_v2 import fixture_store

    store, binding, _ = fixture_store(tmp_path)
    ref = binding
    result = store.call("catalog", dict(context_ref=ref), "d1-d2.v1")
    assert "P1" not in result["body"] and "policy" not in result["body"]
    for obj in [
        dict(kind="policy", id="P1"),
        dict(kind="raw_document", id="policy"),
        dict(kind="reference_event", id="E1"),
    ]:
        with pytest.raises(FileNotFoundError):
            store.call("read", dict(context_ref=ref, objects=[obj]), "d1-d2.v1")


def test_v6_real_runner_injects_operations_only_at_phase(tmp_path):
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
    from tests.test_persistent_runtime_w3 import _Context, _Workspace
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_case_models import ModelWorker
    from tests.test_w3_v4 import PhaseAgent
    from tests.test_w3_v5_contracts import direction

    class Worker(ModelWorker):
        async def bind_w3_context(self, cid, phase, manifest):
            assert manifest["research_scope"] == "d1-d2.v1"
            assert set(manifest["components"]) <= {"d1", "d2"}
            assert not {"events", "history", "state", "policy_usage"} & manifest.keys()
            return dict(context_ref="frozen", context_manifest_sha="hash", direct_objects=[])

        async def run(self, request):
            job = await super().run(request)
            value = (
                direction("NO_TRADE")
                if request.task_phase == "trade_decision"
                else dict(pin_actions=[], watch_actions=[])
            )
            return job.model_copy(update=dict(final_response=json.dumps(value)))

    case, _ = fixture_case()
    case.w3_contract_version = W3_V6_CONTRACT
    case.w2_final = case.w2_final.model_copy(
        update=dict(policy_hit=False, policy_related=False, policy_ids=[], hit_condition_ids=[])
    )
    case.frozen_inputs["w3_assets"] = {
        "w3/AGENTS.md": "shared",
        "w3/skills/skill.md": "trade-only",
        "w3/operations.md": "OPERATIONS_FROZEN_TEXT",
    }
    case.frozen_inputs["w3_contracts"] = {"w3_entry_version": "w3-entry-v6"}
    j = RuntimeJournal(tmp_path / "r.db")
    w = Worker()
    runner = CodexW3AgentRunner(
        worker=w,
        workspace=_Workspace(),
        context_provider=_Context(),
        prompt_root=tmp_path,
        journal=j,
        timeout_seconds=30,
    )
    context = PhaseAgent().prepare_case(case).model_copy(update={"components": {}})
    try:
        for phase, payload in [
            (
                "trade_decision",
                dict(
                    ticker="MU",
                    cutoff_at="2026-11-12T12:00:00Z",
                    message={},
                    w1={},
                    policies=[],
                    additional_tags=[],
                    pins={},
                    watch={},
                    case_type="OLD",
                    trading_context={},
                ),
            ),
            (
                "post_decision_operations",
                dict(
                    submitted_decision=direction("NO_TRADE"),
                    output_disposition="NO_TRADE",
                    watch={"items": []},
                    pins={"items": []},
                    candidate_eligibility={"hidden": True},
                ),
            ),
        ]:
            runner.run_phase(
                case=case,
                record=dict(record_id="v6", workspace="v6", thread_id="child"),
                phase=phase,
                payload=payload,
                context=context,
            )
            req = w.requests[-1]
            assert req.w3_entry_version == "w3-entry-v6"
            if phase == "trade_decision":
                assert "OPERATIONS_FROZEN_TEXT" not in req.prompt
                task = json.loads(req.prompt)
                assert not {"case_type", "watch", "trading_context"} & task["input"].keys()
            else:
                assert req.prompt.startswith("OPERATIONS_FROZEN_TEXT\n\n")
                task = json.loads(req.prompt.split("\n\n", 1)[1])
                assert set(req.output_schema["properties"]) == {"pin_actions", "watch_actions"}
            assert set(task) == {"phase", "input", "context_ref", "instruction", "output_fields"}
    finally:
        runner.close()


def test_new_w3_has_sibling_capture_and_capture_failure_is_auxiliary(tmp_path):
    from doxagent.persistent_runtime_v2.router import route_runtime_case
    from doxagent.persistent_runtime_v2.schema import RuntimeEffectStatus

    service, j, agent, case, _ = setup_case(tmp_path)
    case.w3_contract_version = W3_V6_CONTRACT
    case.route = route_runtime_case(
        case.w1_final, case.w2_final, w3_contract_version=case.w3_contract_version
    )
    service.repository.save_case(case)
    try:
        service._enqueue_route_effects(case, "original-w1-response")
        effects = service.repository.list_effects(case.case_id)
        types = {e.effect_type.value for e in effects}
        assert {"route_to_w3", "emit_delta"} <= types
        r3 = next(e for e in effects if e.effect_type.value == "emit_delta")
        assert r3.payload["previous_response_id"] == "original-w1-response"
        service.repository.save_effect(
            r3.model_copy(
                update=dict(status=RuntimeEffectStatus.FAILED, last_error="blocked provider")
            )
        )
        service._refresh_case_completion(case.case_id)
        assert service.repository.get_case(case.case_id).status.value not in {
            "FAILED",
            "UNAVAILABLE",
        }
        trade = next(
            e
            for e in service.repository.claim_effects(limit=20)
            if e.effect_type.value == "route_to_w3"
        )
        assert service._process_claimed_effect(trade) == 1
        assert service.repository.get_case(case.case_id).w3_trade_decision["decision"] == "NO_TRADE"
    finally:
        service.close()


def test_filled_history_uses_entry_partial_fills_corrections_and_freezes(tmp_path):
    from datetime import timedelta
    from types import SimpleNamespace

    from doxagent.persistent_runtime_v2.w3_context import case_labels
    from doxagent.persistent_runtime_v2.w3_v6 import freeze_recent_trade, history_matches
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_v5_contracts import direction

    case, _ = fixture_case()
    j = RuntimeJournal(tmp_path / "r.db")
    now = case.source.message_bus_event_time
    with j.transaction() as db:
        db.executescript(
            "CREATE TABLE te_executions(id TEXT PRIMARY KEY, ticker TEXT, payload TEXT); "
            "CREATE TABLE te_jobs(id TEXT PRIMARY KEY, execution_id TEXT,leg TEXT,ticker TEXT); "
            "CREATE TABLE te_attempts(id TEXT PRIMARY KEY,job_id TEXT,account TEXT,payload TEXT); "
            "CREATE INDEX te_attempts_job ON te_attempts(job_id); "
            "CREATE TABLE te_fills(account TEXT,family TEXT,correction INTEGER,payload TEXT,attempt_id TEXT,con_id INTEGER); "
            "CREATE INDEX te_fills_scope ON te_fills(account,con_id);"
        )
        for cid, leg, qty, delta, correction in [
            ("good", "ENTRY", 1, -1, 0),
            ("zero", "ENTRY", 0, -1, 0),
            ("future", "ENTRY", 1, 1, 0),
            ("old", "ENTRY", 1, -49, 0),
            ("exit", "EXIT", 2, -1, 0),
            ("cancelled", "ENTRY", 2, -1, 0),
            ("cancelled", "ENTRY", 0, -1, 1),
            ("corrected_future", "ENTRY", 2, -1, 0),
            ("corrected_future", "ENTRY", 2, 1, 1),
            ("lower_boundary", "ENTRY", 1, -48, 0),
            ("upper_boundary", "ENTRY", 1, 0, 0),
        ]:
            if correction == 0:
                db.execute(
                    "INSERT INTO te_executions VALUES(?,?,?)",
                    (cid, case.ticker, json.dumps({"intent": {"trade": {"case_id": cid}}})),
                )
                db.execute("INSERT INTO te_jobs VALUES(?,?,?,?)", (cid, cid, leg, case.ticker))
                db.execute(
                    "INSERT INTO te_attempts VALUES(?,?,?,?)",
                    (cid, cid, "account", json.dumps(dict(contract=dict(con_id=1)))),
                )
            db.execute(
                "INSERT INTO te_fills VALUES(?,?,?,?,?,?)",
                (
                    "account",
                    cid,
                    correction,
                    json.dumps(dict(time=(now + timedelta(hours=delta)).isoformat(), quantity=qty)),
                    cid,
                    1,
                ),
            )
    calls = []

    def get(cid):
        calls.append(cid)
        old = case.model_copy(deep=True)
        old.w3_trade_decision = direction("LONG")
        return old

    repo = SimpleNamespace(get_case=get)
    result = freeze_recent_trade(j, repo, case)
    assert calls == ["good", "lower_boundary"] and len(result) == 2
    assert set(result[0]) == {"message", "w3_decision"}
    with j.transaction() as db:
        db.execute(
            "UPDATE te_fills SET payload=? WHERE family='good'",
            (json.dumps(dict(time=now.isoformat(), quantity=0)),),
        )
    assert freeze_recent_trade(j, repo, case) == result and calls == ["good", "lower_boundary"]
    snapshot = j.get("w3_recent_filled_snapshot", case.case_id)
    assert [m["case_id"] for m in snapshot["members"]] == calls
    assert len(snapshot["members"][0]["fills"]) == 1
    matches = history_matches(j, repo, case)
    assert (
        "RECENT_TRADE"
        in case_labels(case, history_matches=matches, watch_followup=False)["additional_tags"]
    )
    assert all(
        set(item["w3_decision"]) == {"decision", "reason", "prior_expectation", "expectation_delta"}
        for item in result
    )


def test_bundle_requires_real_ops_asset_and_freezes_three_files(tmp_path):
    from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
    from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet

    j = RuntimeJournal(tmp_path / "r.db")
    bundles = ExecutionBundles(j)
    prompts = RuntimeV2PromptSet(*(["offline"] * 7))
    assets = {
        "w3/AGENTS.md": "common",
        "w3/skills/skill.md": "trade",
        "w3/operations.md": "W3_OPERATIONS_PLACEHOLDER",
    }
    with pytest.raises(ValueError, match="placeholder"):
        bundles.publish(
            prompts, assets=assets, orchestration_revision=3, w3_contract_version=W3_V6_CONTRACT
        )
    assets["w3/operations.md"] = "ops-only"
    key = bundles.publish(
        prompts,
        assets=assets,
        orchestration_revision=3,
        w3_contract_version=W3_V6_CONTRACT,
        activate=False,
    )
    assets["w3/operations.md"] = "later"
    saved = bundles.get(key)
    assert saved["assets"]["w3/operations.md"] == "ops-only"
    assert saved["contracts"]["w3_entry_version"] == "w3-entry-v6"
    assert set(saved["contracts"]["w3_operations_schema"]["properties"]) == {
        "pin_actions",
        "watch_actions",
    }


@pytest.mark.asyncio
async def test_scoped_http_credential_cannot_read_old_context_policy(tmp_path):
    import httpx

    from doxagent.mcp.w3_research_server import ResearchMcp
    from tests.test_w3_research_v2 import fixture_store

    store, ref, _ = fixture_store(tmp_path, length=10)
    mcp = ResearchMcp(store, "s" * 32)
    async with mcp.manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp), base_url="http://localhost"
        ) as client:
            headers = {
                "Authorization": "Bearer " + mcp.scoped_token,
                "Accept": "application/json, text/event-stream",
            }
            init = await client.post(
                "/",
                headers=headers,
                json=dict(
                    jsonrpc="2.0",
                    id=1,
                    method="initialize",
                    params=dict(
                        protocolVersion="2025-03-26",
                        capabilities={},
                        clientInfo=dict(name="v6-test", version="1"),
                    ),
                ),
            )
            assert init.status_code == 200
            headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
            for kind, oid in [("policy", "P1"), ("raw_document", "policy")]:
                result = await client.post(
                    "/",
                    headers=headers,
                    json=dict(
                        jsonrpc="2.0",
                        id=2,
                        method="tools/call",
                        params=dict(
                            name="read",
                            arguments=dict(context_ref=ref, objects=[dict(kind=kind, id=oid)]),
                        ),
                    ),
                )
                assert result.json()["result"]["isError"] is True


def test_v6_read_projection_uses_w1_w2_without_ops_novelty(tmp_path):
    from doxagent.v2_read.projectors import DomainProjectors
    from doxagent.v2_read.repository import ReadStore
    from tests.test_w3_v5_execution import setup_case

    service, _, _, case, _ = setup_case(tmp_path, action="NO_TRADE", hit=True)
    try:
        case.w3_contract_version = W3_V6_CONTRACT
        case.w3_trade_decision = dict(
            decision="NO_TRADE",
            reason="independent",
            prior_expectation=None,
            expectation_delta=None,
        )
        case.w3_operations_result = dict(pin_actions=[], watch_actions=[])
        case.w3_operations_status = "COMMITTED"
        store = ReadStore(tmp_path / "read.db")
        store.migrate()
        store.ingest(
            "fixture",
            "source",
            [
                dict(
                    kind="native:source_definitions",
                    ticker="",
                    id=case.source.source_id,
                    data=dict(display_name="Synthetic", kind="api"),
                )
            ],
        )
        projected = DomainProjectors(store).case(case.model_dump(mode="json"), 1)
        summary = projected[0]["data"]
        assert summary["w3_decision"]["value"] == "NO_TRADE"
        assert summary["final_novelty_source"] == "W1" and summary["final_policy_source"] == "W2"
        assert summary["final_policy_hit"]["value"] is True
        reasons = next(r["data"] for r in projected if r["kind"] == "case_reasoning")
        assert not reasons.get("w3_policy") and not reasons.get("w3_novelty")
    finally:
        service.close()


def test_late_capture_cannot_overwrite_committed_w3(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus, W1FactExtractionResult
    from tests.test_w3_v4 import operations
    from tests.test_w3_v5_contracts import direction

    service, j, _, case, _ = setup_case(tmp_path)
    case.w3_contract_version = W3_V6_CONTRACT
    service.repository.save_case(case)
    extraction = W1FactExtractionResult(
        candidates=operations(watch=False, pin=False).delta_candidates
    )

    def model(**kwargs):
        current = service.repository.get_case(case.case_id)
        service.repository.save_case(
            current.model_copy(
                update=dict(
                    status=RuntimeCaseStatus.COMPLETED,
                    w3_trade_decision=direction("LONG"),
                    w3_operations_result=dict(pin_actions=[], watch_actions=[]),
                    w3_operations_status="COMMITTED",
                )
            )
        )
        return extraction, "response"

    monkeypatch.setattr(service, "_call_with_retry", model)
    monkeypatch.setattr(
        service, "_known_for_case", lambda c: SimpleNamespace(max_event_numeric_id=lambda *args: 1)
    )
    try:
        service._execute_r3(case, SimpleNamespace(payload={}))
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_trade_decision["decision"] == "LONG"
        assert saved.w3_operations_status == "COMMITTED"
        assert saved.status == RuntimeCaseStatus.COMPLETED
        assert saved.w1_extraction == extraction
        # Completion/recovery can still hold the pre-R3 Case snapshot. Its
        # ordinary whole-row save must retain the independently committed facts.
        service.repository.save_case(saved.model_copy(update={"w1_extraction": None}))
        assert service.repository.get_case(case.case_id).w1_extraction == extraction
    finally:
        service.close()


@pytest.mark.parametrize("value", [None, {}, False, "[]"])
def test_v6_invalid_root_action_containers_are_not_silently_empty(value):
    with pytest.raises(ValueError):
        parse_w3_operations(dict(pin_actions=value, watch_actions=[]), version=W3_V6_CONTRACT)


def test_policy_titles_conditions_and_actions_share_frozen_mapping():
    import copy

    from doxagent.persistent_runtime_v2.schema import W2PolicyAssessmentResultV2
    from tests.test_runtime_policy_direction_usage import fixture_case

    case, _ = fixture_case()
    case.w2_final = W2PolicyAssessmentResultV2(
        policy_hit=True,
        policy_related=True,
        policy_ids=["P1"],
        related_policy_ids=["P2"],
        hit_condition_ids=[dict(policy_id="P1", condition_ids=["C1"])],
        confidence="normal",
        reason="not visible upstream reason",
    )
    rows = [
        dict(
            policy_id=pid,
            title="same",
            transmission="research",
            decision="SHORT",
            match_scope="hidden",
            activation_conditions=[
                dict(
                    condition_id="C1",
                    criterion="fact",
                    decision="LONG",
                    calibration=dict(reference_state="baseline", trigger_boundary="hidden"),
                )
            ],
        )
        for pid in ["P1", "P2"]
    ]
    case.frozen_inputs["projection"] = {"policies": copy.deepcopy(rows)}
    payload = dict(
        ticker="MU",
        cutoff_at="cutoff",
        message={},
        w1={},
        additional_tags=[],
        pins={},
        policies=rows,
    )
    original = copy.deepcopy(payload)
    result = trade_input(case, payload, [])
    assert payload == original
    assert result["w2"]["policy_matches"]["hit"] == [
        dict(policy_title="same〔1〕", condition_ids=["C1"])
    ]
    assert result["w2"]["policy_matches"]["related"] == [
        dict(policy_title="same〔2〕", condition_ids=None)
    ]
    assert "P1" not in json.dumps(result) and "hidden" not in json.dumps(result)
    assert all(p["cutoff"] is None for p in result["policies"])
    targets = operations_input(
        case,
        dict(submitted_decision={}, output_disposition="NO_TRADE", pins={}, watch={"items": []}),
    )["policy_targets"]
    assert [t["title"] for t in targets] == [p["title"] for p in result["policies"]]
    removed = operations_input(
        case,
        dict(
            submitted_decision={},
            output_disposition="NO_TRADE",
            pins={},
            watch={"items": [{"policy_id": "removed"}]},
        ),
    )
    assert dict(policy_id="removed", title=None) in removed["policy_targets"]
    case.w2_final.hit_condition_ids[0].condition_ids = ["UNKNOWN"]
    with pytest.raises(ValueError, match="Condition missing"):
        trade_input(case, payload, [])


def test_replay_without_fills_never_promotes_old_intents_or_tags(tmp_path):
    from doxagent.persistent_runtime_v2.w3_context import case_labels
    from doxagent.persistent_runtime_v2.w3_v6 import freeze_recent_trade, history_matches
    from tests.test_runtime_policy_direction_usage import fixture_case

    case, _ = fixture_case()
    case.frozen_inputs["w3_replay"] = dict(
        recent_trade_intents=[dict(case_id="intent-only")],
        history_matches=[dict(events=[], policies=list(case.w2_final.policy_ids))],
    )
    j = RuntimeJournal(tmp_path / "r.db")
    assert freeze_recent_trade(j, None, case) == []
    assert history_matches(j, None, case) == []
    assert (
        "RECENT_TRADE"
        not in case_labels(case, history_matches=[], watch_followup=False)["additional_tags"]
    )


def test_seven_blocked_r3_bodies_leave_ack_capacity_and_realtime_dispatch(tmp_path, monkeypatch):
    from threading import Event, Lock

    from doxagent.persistent_runtime_v2.schema import (
        RuntimeEffect,
        RuntimeEffectStatus,
        RuntimeSideEffect,
    )

    service, _, _, case, _ = setup_case(tmp_path)
    release, all_started, realtime, ack = Event(), Event(), Event(), Event()
    lock, started = Lock(), []
    trade = service.repository.list_effects(case.case_id)[0]
    service.repository.save_effect(
        trade.model_copy(update=dict(status=RuntimeEffectStatus.COMPLETED))
    )
    for i in range(8):
        service.repository.enqueue_effect(
            RuntimeEffect(
                case_id=case.case_id,
                effect_type=RuntimeSideEffect.EMIT_DELTA,
                idempotency_key=f"blocked-r3:{i}",
                available_at=service.journal.clock(),
            )
        )

    def process(effect, **kwargs):
        if effect.effect_type is RuntimeSideEffect.ROUTE_TO_W3:
            realtime.set()
        else:
            with lock:
                started.append(effect.effect_id)
                if len(started) == 7:
                    all_started.set()
            release.wait(5)
        return 1

    monkeypatch.setattr(service, "_process_claimed_effect", process)
    try:
        assert service.dispatch_pending_effects(limit=20) == 7
        assert all_started.wait(2)
        service._effect_executor.submit(ack.set)
        assert ack.wait(1), "short ACK must retain a worker slot"
        service.repository.save_effect(trade)
        assert service.dispatch_pending_effects(limit=20) == 1
        assert realtime.wait(1), "realtime W3 must bypass the occupied shared pool"
        assert len(started) == 7
    finally:
        release.set()
        service.close()


def test_v6_replay_freezes_operations_and_isolates_research(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
    from tests.test_w3_backtest_runner import fixture

    dataset, runner, worker, _ = fixture(tmp_path, monkeypatch, contract=W3_V6_CONTRACT)
    (runner.prompt_root / "operations.md").write_text("FROZEN_OPERATIONS", encoding="utf8")
    store = W3ResearchStore(tmp_path / "research")

    async def prepare(ticker, components):
        assert set(components) <= {"d1", "d2"}
        return store.install(components, ticker)

    async def bind(cid, phase, manifest):
        return store.bind(cid, phase, manifest)

    async def configure(config):
        return {"ready": True}

    async def claim(request):
        return dict(thread_id="v6-child", run_id="v6-workspace")

    async def market(manifest):
        assert manifest["mode"] == "OFFLINE_REPLAY"

    original = worker.run

    async def run(request):
        job = await original(request)
        if request.task_phase == "post_decision_operations":
            return job.model_copy(
                update=dict(final_response=json.dumps(dict(pin_actions=[], watch_actions=[])))
            )
        return job

    worker.prepare_w3_research = prepare
    worker.bind_w3_context = bind
    worker.configure_w3_ready = configure
    worker.claim_w3_ready = claim
    worker.start_w3_market = market
    worker.run = run
    result = runner.run_case(dataset, "C1", tmp_path / "run")
    assert result["status"] == "SUCCEEDED", result["error"]
    trade, ops = worker.requests[-2:]
    assert trade.thread_id == ops.thread_id == "offline-child"
    assert ops.prompt.startswith("FROZEN_OPERATIONS\n\n")
    assert "FROZEN_OPERATIONS" not in trade.prompt
    assert not trade.data_mcp_enabled and trade.web_search_mode == "disabled"
    assert json.loads(trade.prompt)["input"]["recent_trade"] == []
    assert "RECENT_TRADE" not in result["labels"]["additional_tags"]
