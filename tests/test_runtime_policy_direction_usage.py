from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode
from doxagent.persistent_runtime_v2.policy_consumption import (
    accept_rearm_requests,
    apply_release_authorizations,
    claim_ready_in_transaction,
    consumption_feed,
    read_usage_snapshot,
    usage,
)
from doxagent.persistent_runtime_v2.policy_watch import apply_actions, freeze_followup
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCase,
    RuntimeVersionPin,
    TradeRecord,
    W1NoveltyResult,
    W2PolicyAssessmentResult,
    W3CaseResultV3,
    parse_w3_result,
)
from doxagent.workflows.codex_document3.consumer_contracts import parse_runtime_projection
from doxagent.workflows.codex_document3.runtime_projection import project_policy_set_v3
from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3, PolicyV3
from tests.test_persistent_runtime_v2 import _source
from tests.test_persistent_runtime_w3 import _w3_result

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def policy():
    return PolicyV3(
        policy_id="P1",
        title="scope",
        transmission="economic channel",
        match_scope="news",
        ref=[],
        activation_conditions=[
            dict(
                condition_id="C1" if d == "LONG" else "C2",
                decision=d,
                trigger_layer="MIDDLE",
                criterion="same text legal in both directions",
                calibration=dict(reference_state="baseline", trigger_boundary="increment"),
            )
            for d in ("LONG", "SHORT")
        ],
    )


def fixture_case(identity="case-1"):
    p = PolicySetV3(
        ticker="MU",
        policy_set_version=1,
        as_of=NOW,
        published_at=NOW,
        publication_state="COMPLETE",
        policies=[policy()],
    )
    projection = parse_runtime_projection(project_policy_set_v3(p))
    case = RuntimeCase(
        case_id=identity,
        trading_date=NOW.date(),
        created_at=NOW,
        source=_source().model_copy(update={"source_message_id": identity}),
        version_pin=RuntimeVersionPin(
            event_library_version=7,
            policy_set_version=1,
            runtime_projection_version=1,
            provisional_snapshot_version=0,
        ),
        w1_final=W1NoveltyResult(result="NEW", confidence="normal", reason="new"),
        w2_final=W2PolicyAssessmentResult(
            policy_hit=True,
            policy_related=False,
            policy_ids=["P1"],
            hit_condition_ids=[dict(policy_id="P1", condition_ids=["C1"])],
            reason="hit",
            confidence="normal",
        ),
        frozen_inputs={"projection": projection.model_dump(mode="json")},
    )
    return case, projection


def trade(case, projection, direction="LONG", generation=1):
    c = next(c for c in projection.policies[0].activation_conditions if c.decision == direction)
    from doxagent.workflows.codex_document3.schema import PolicyDecision

    return TradeRecord(
        case_id=case.case_id,
        ticker="MU",
        trading_date=case.trading_date,
        source=case.source,
        executed_policy_id="P1",
        executed_condition_id=c.condition_id,
        executed_condition_revision=c.condition_revision,
        usage_generation=generation,
        policy_set_version=1,
        decision=PolicyDecision(direction),
        w1_result=case.w1_final,
        w2_result=case.w2_final,
    )


def test_concurrent_conditions_share_direction_and_opposite_survives(tmp_path):
    journal = RuntimeJournal(tmp_path / "r.db")

    def claim(i):
        case, projection = fixture_case(str(i))
        with journal.transaction() as db:
            return claim_ready_in_transaction(
                db, case=case, trade=trade(case, projection), intent_id=str(i), now=NOW
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [1, 2]))
    assert sorted(outcomes) == ["DUPLICATE_POLICY", "READY"]
    with journal.transaction() as db:
        assert usage(db, "MU", "P1", "LONG")["consumed"]
        assert not usage(db, "MU", "P1", "SHORT")["consumed"]
        case, p = fixture_case("short")
        assert (
            claim_ready_in_transaction(
                db, case=case, trade=trade(case, p, "SHORT"), intent_id="short", now=NOW
            )
            == "READY"
        )
    assert len(journal.values("policy_consumptions")) == 2


def test_rearm_cas_only_frozen_direction_and_activation_once(tmp_path):
    journal = RuntimeJournal(tmp_path / "r.db")
    case, p = fixture_case()
    with journal.transaction() as db:
        claim_ready_in_transaction(db, case=case, trade=trade(case, p), intent_id="first", now=NOW)
        feed = {"policy_consumption": consumption_feed(db, "MU")}
        accepted, issues = accept_rearm_requests(
            [dict(policy_id="P1", reason="reassessed")],
            feed=feed,
            upsert_ids={"P1"},
            retire_ids=set(),
            base_ids={"P1"},
            run_id="maintain",
            base_release={"version": 1},
        )
        assert not issues
        claim_ready_in_transaction(
            db, case=case, trade=trade(case, p, "SHORT"), intent_id="later", now=NOW
        )
        receipt = apply_release_authorizations(db, ticker="MU", revision_id="r2", requests=accepted)
        assert receipt["outcomes"][0]["status"] == "APPLIED"
        assert usage(db, "MU", "P1", "LONG")["usage_generation"] == 2
        assert usage(db, "MU", "P1", "SHORT")["consumed"]
        apply_release_authorizations(db, ticker="MU", revision_id="r2", requests=accepted)
        assert usage(db, "MU", "P1", "LONG")["usage_generation"] == 2
        claim_ready_in_transaction(
            db, case=case, trade=trade(case, p, generation=2), intent_id="again", now=NOW
        )
        apply_release_authorizations(db, ticker="MU", revision_id="r3", requests=accepted)
        assert usage(db, "MU", "P1", "LONG")["consumed"]
        assert usage(db, "MU", "P1", "LONG")["usage_generation"] == 2


def watch_result():
    raw = _w3_result().model_dump(mode="json")
    raw.update(
        policy={
            "policy_ids": ["P1"],
            "matched_condition_ids": [{"policy_id": "P1", "condition_ids": ["C1"]}],
            "reason": "hit",
        },
        expert_trade={"evaluated": False, "trade": False, "reason": "defer"},
        result_contract_version="persistent-runtime.w3-result.v3",
        policy_execution={"selected_condition": None, "reason": "wait"},
        watch_actions=[dict(policy_id="P1", action="SET", reason="near-term uncertainty")],
    )
    return W3CaseResultV3.model_validate(raw)


def test_watch_no_extension_expiry_and_ready_cancellation(tmp_path):
    journal = RuntimeJournal(tmp_path / "r.db")
    case, p = fixture_case()
    with journal.transaction() as db:
        case.frozen_inputs["policy_usage"] = read_usage_snapshot(db, "MU", p.policies)
        assert (
            apply_actions(db, case=case, result=watch_result(), now=NOW)["outcomes"][0]["status"]
            == "APPLIED"
        )
        journal_key = encode(["MU", "P1"])
    watch = journal.get("policy_watch", journal_key)
    other, _ = fixture_case("other")
    with journal.transaction() as db:
        other.frozen_inputs["policy_usage"] = read_usage_snapshot(db, "MU", p.policies)
        assert freeze_followup(db, other, NOW) == [watch]
        assert (
            apply_actions(db, case=other, result=watch_result(), now=NOW)["outcomes"][0]["status"]
            == "ALREADY_ACTIVE_NO_EXTENSION"
        )
        claim_ready_in_transaction(db, case=case, trade=trade(case, p), intent_id="ready", now=NOW)
    assert journal.get("policy_watch", journal_key) is None
    late, _ = fixture_case("late")
    with journal.transaction() as db:
        late.frozen_inputs["policy_usage"] = read_usage_snapshot(db, "MU", p.policies)
        assert (
            apply_actions(db, case=late, result=watch_result(), now=NOW + timedelta(minutes=1))[
                "outcomes"
            ][0]["status"]
            == "READY_AFTER_CASE_STARTED"
        )
    assert journal.get("policy_watch", journal_key) is None


def test_watch_local_validation_and_explicit_final_condition():
    raw = watch_result().model_dump(mode="json")
    raw["watch_actions"].append(
        {"policy_id": "P1", "action": "SET", "reason": "x", "expires_at": "illegal"}
    )
    result, issues = parse_w3_result(raw, revision=3)
    assert len(result.watch_actions) == 1 and len(issues) == 1
    raw["policy_execution"]["selected_condition"] = {"policy_id": "P1", "condition_id": "C2"}
    with pytest.raises(ValueError):
        W3CaseResultV3.model_validate(raw)
    from doxagent.persistent_runtime_v2.router import route_w3_result

    assert route_w3_result(result).primary_route == "ADD_TO_DELTA"


class NativeResponses:
    model = "fixture"

    def __init__(self):
        self.calls = []
        self.old = False
        self.related = False

    def complete(self, request):
        from doxagent.persistent_runtime_v2.schema import (
            W1NoveltyResult,
            W1Round1Result,
            W2NovelFactAlignmentResult,
            W2Round1RecallResult,
        )
        from doxagent.persistent_runtime_v2.transport import RuntimeResponsesResult
        from tests.test_persistent_runtime_v2 import _FakeResponses

        self.calls.append(request)
        if request.output_model is W1Round1Result and self.old:
            value = request.output_model(event_ids=["E1"])
        elif request.output_model is W2Round1RecallResult:
            value = request.output_model(
                candidate_policy_ids=[
                    p["policy_id"] for p in request.payload["runtime_policy_projection"]["policies"]
                ]
            )
        elif request.output_model is W2PolicyAssessmentResult:
            value = request.output_model(
                policy_hit=not self.related,
                policy_related=self.related,
                policy_ids=[] if self.related else ["P1"],
                hit_condition_ids=[]
                if self.related
                else [dict(policy_id="P1", condition_ids=["C1", "C2"])],
                matched_condition_ids=[dict(policy_id="P1", condition_ids=["C2"])]
                if self.related
                else [],
                confidence="normal",
                reason="fixture",
            )
        elif request.output_model is W1NoveltyResult:
            value = request.output_model(
                result="OLD" if self.old else "NEW",
                confidence="normal",
                reason="fixture",
                reference_ids=["E1"] if self.old else [],
            )
        elif request.output_model is W2NovelFactAlignmentResult:
            value = request.output_model.model_validate(
                {"condition_results": [{"condition_ids": ["C1", "C2"], "is_new": True}]}
            )
        else:
            return _FakeResponses().complete(request)
        return RuntimeResponsesResult(
            value=value,
            response_id="response-" + str(len(self.calls)),
            latency_ms=1,
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cached_input_tokens=0,
        )


class NativePolicies:
    def __init__(self):
        self.projection = fixture_case()[1]

    def current_projection(self, ticker):
        return self.projection

    def details(self, ticker, version, ids):
        from doxagent.workflows.codex_document3.consumer_contracts import PolicyDetailSnapshotV3

        return PolicyDetailSnapshotV3(
            ticker=ticker, policy_set_version=version, requested_policy_ids=ids, policies=[policy()]
        )


class NativeW3:
    selected = "C1"

    def run(self, *, case, w3_case, slot):
        result = watch_result().model_dump(mode="json")
        result["w3_case_id"] = w3_case.w3_case_id
        result["policy"]["matched_condition_ids"][0]["condition_ids"] = ["C1", "C2"]
        if self.selected:
            result["policy_execution"]["selected_condition"] = {
                "policy_id": "P1",
                "condition_id": self.selected,
            }
            result["watch_actions"] = []
        return W3CaseResultV3.model_validate(result), "fixture", None

    def close(self):
        pass


def native_runtime(tmp_path):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents

    journal = RuntimeJournal(tmp_path / "native.db", clock=lambda: NOW)
    runtime = PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(journal.path),
        journal=journal,
        responses=NativeResponses(),
        policies=NativePolicies(),
        known_events=_FakeKnownEvents(),
        w3_agent=NativeW3(),
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
    )
    runtime.execution_bundles.publish(runtime.prompts, orchestration_revision=3)
    return runtime, journal


def test_native_r1_r2_w3_ready_direction_and_delta_survive_rejection(tmp_path):
    runtime, journal = native_runtime(tmp_path)
    try:
        for identity, selected in [("one", "C1"), ("repeat-long", "C1"), ("short", "C2")]:
            runtime._w3_agent.selected = selected
            case = runtime.execute_message(
                _source().model_copy(update={"source_message_id": identity})
            )
            runtime.process_pending_effects()
            assert runtime.repository.get_case(case.case_id).technical_status.value == "OK", (
                runtime.repository.get_case(case.case_id).error_message
            )
            assert runtime.repository.list_provisional("MU", case.trading_date)
        assert sorted(v["status"] for v in journal.values("trade_intents")) == [
            "DUPLICATE_POLICY",
            "READY",
            "READY",
        ]
        assert len(journal.values("policy_consumptions")) == 2
        r1 = next(
            r for r in runtime.responses.calls if r.output_model.__name__ == "W2Round1RecallResult"
        )
        assert set(r1.payload["runtime_policy_projection"]["policies"][0]) == {
            "policy_id",
            "title",
            "match_scope",
        }
        r2 = next(r for r in runtime.responses.calls if r.output_model is W2PolicyAssessmentResult)
        assert r2.payload["policy_details"]["policies"][0] == policy().model_dump(mode="json")
        assert r2.payload["policy_details"]["policy_usage"][0]["executable_directions"] == [
            "LONG",
            "SHORT",
        ]
        assert not runtime._without_consumed_policies(runtime.policies.projection).policies
    finally:
        runtime.close()


def test_output_guard_head_race_is_retryable_and_stale_generation_rejected(tmp_path):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.trade_output import TradeOutputService

    journal = RuntimeJournal(tmp_path / "guard.db", clock=lambda: NOW)
    repository = SQLitePersistentRuntimeV2Repository(journal.path)
    case, p = fixture_case()
    repository.save_case(case)
    case.version_pin.activation_revision_id = "r1"
    case.frozen_inputs["policy_output_guard"] = {"revision_id": "r1"}
    with journal.transaction() as db:
        apply_release_authorizations(db, ticker="MU", revision_id="r2", requests=[])
    output = TradeOutputService(journal)
    assert output.record(case, trade(case, p)) == "POLICY_AUTHORIZATION_NOT_ADMITTED"
    assert not journal.values("trade_intents") and not journal.values("policy_consumptions")
    case.frozen_inputs["policy_output_guard"] = {"revision_id": "r2"}
    assert output.record(case, trade(case, p)) == "READY"
    assert output.record(case, trade(case, p)) == "READY"
    assert len(journal.values("policy_consumptions")) == 1


def test_old_claim_migration_requires_proven_direction_and_keeps_gap(tmp_path):
    from doxagent.persistent_runtime_v2.policy_consumption import (
        import_legacy_consumptions,
        unresolved_migration,
    )
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.schema import PolicyActivationRecord

    journal = RuntimeJournal(tmp_path / "migrate.db")
    repository = SQLitePersistentRuntimeV2Repository(journal.path)
    case, p = fixture_case()
    repository.save_case(case)
    repository.claim_policy_activation(
        PolicyActivationRecord(
            case_id=case.case_id,
            source_message_id=case.source.source_message_id,
            ticker="MU",
            policy_id="P1",
            activation_revision="ar_" + "a" * 24,
            policy_set_version=1,
        )
    )
    with journal.transaction() as db:
        import_legacy_consumptions(db, ticker="MU", policy_ids={"P1"})
    with journal.transaction() as db:
        assert unresolved_migration(db, "MU", "P1")
        assert read_usage_snapshot(db, "MU", p.policies)[0]["executable_directions"] == []
    repository.save_trade(trade(case, p, "SHORT"))
    with journal.transaction() as db:
        import_legacy_consumptions(db, ticker="MU", policy_ids={"P1"})
        assert not unresolved_migration(db, "MU", "P1")
        assert usage(db, "MU", "P1", "SHORT")["consumed"]
        assert not usage(db, "MU", "P1", "LONG")["consumed"]


def test_old_related_watch_freezes_route_and_uses_revalidation(tmp_path):
    runtime, journal = native_runtime(tmp_path)
    try:
        from doxagent.event_library.contracts import CanonicalEvent
        from doxagent.event_library.provider import EventDetailSnapshot

        event = CanonicalEvent(
            event_id="E1",
            ticker="MU",
            title="Prior customer event",
            event_type="CUSTOMER_MILESTONE",
            occurred_at="2026-10-09",
            occurrence_time_precision="DAY",
            status="ACTIVE",
            canonical_summary="Prior",
            known_event_summary="Prior event",
            is_important=False,
            include_in_reference_view=True,
            related_event_ids=[],
            supersedes_event_id=None,
            derived_from_event_ids=[],
            facts=[
                dict(fact_id="F1", proposition="Prior customer event", assertion_state="ACTUAL")
            ],
        )
        runtime.known_events.details = lambda ticker, version, ids: EventDetailSnapshot(
            ticker=ticker, version=version, requested_event_ids=ids, events=[event]
        )
        runtime.responses.old = True
        runtime.responses.related = True
        journal.set(
            "policy_watch",
            encode(["MU", "P1"]),
            dict(
                ticker="MU",
                policy_id="P1",
                source_case_id="prior",
                reason="pending confirmation",
                start_semantic_day=NOW.date().isoformat(),
                expires_at=(NOW + timedelta(hours=18)).isoformat(),
            ),
        )
        case = runtime.execute_message(_source())
        assert case.technical_status.value == "OK", case.error_message
        assert case.w1_final.result.value == "OLD"
        assert case.w2_final.policy_related and not case.w2_final.policy_hit
        assert (
            case.route.primary_route.value == "W3" and "POLICY_WATCH_FOLLOWUP" in case.route.reason
        )
        assert runtime._w3_mode(case).value == "REVALIDATE_THEN_EVALUATE"
        assert len(case.frozen_inputs["policy_watch_followup"]) == 1
        journal.set("policy_watch", encode(["MU", "P1"]), None)
        assert (
            freeze_followup(None, case, NOW + timedelta(days=5))
            == case.frozen_inputs["policy_watch_followup"]
        )
        assert [e.effect_type.value for e in runtime.repository.list_effects(case.case_id)] == [
            "route_to_w3"
        ]
    finally:
        runtime.close()


def test_completed_w3_result_retries_output_without_research_again(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.service import RuntimeInputUnavailable
    from doxagent.persistent_runtime_v2.trade_output import TradeOutputService

    runtime, journal = native_runtime(tmp_path)
    calls = []
    original_run = runtime._w3_agent.run
    original_record = TradeOutputService.record

    def run(**kwargs):
        calls.append(kwargs["case"].case_id)
        return original_run(**kwargs)

    runtime._w3_agent.run = run
    try:
        case = runtime.execute_message(_source())
        monkeypatch.setattr(
            TradeOutputService, "record", lambda *a, **k: "POLICY_AUTHORIZATION_NOT_ADMITTED"
        )
        with pytest.raises(RuntimeInputUnavailable):
            runtime._execute_w3(case)
        checkpoint = runtime.repository.get_w3_case(case.case_id)
        assert checkpoint.result is not None
        assert not journal.values("trade_intents")
        delta_count = len(runtime.repository.list_provisional("MU", case.trading_date))
        assert delta_count > 0
        monkeypatch.setattr(TradeOutputService, "record", original_record)
        runtime._execute_w3(case)
        assert len(calls) == 1
        assert len(journal.values("policy_consumptions")) == 1
        assert len(runtime.repository.list_provisional("MU", case.trading_date)) == delta_count
    finally:
        runtime.close()


def test_native_in_memory_runtime_freezes_complete_direction_eligibility(tmp_path):
    from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository

    runtime, _ = native_runtime(tmp_path)
    runtime.journal = None
    runtime.repository = InMemoryPersistentRuntimeV2Repository()
    try:
        case = runtime.execute_message(_source())
        assert case.technical_status.value == "OK", case.error_message
        assert case.frozen_inputs["policy_usage"][0]["executable_directions"] == ["LONG", "SHORT"]
        runtime._execute_w3(case)
        assert runtime.repository.direction_usage("MU", "P1", "LONG")["consumed"]
        assert not runtime.repository.direction_usage("MU", "P1", "SHORT")["consumed"]
    finally:
        runtime.close()


def test_native_projection_rejects_legacy_execution_bundle_without_mutating_history(tmp_path):
    from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
    from doxagent.persistent_runtime_v2.service import RuntimeInputUnavailable

    runtime, journal = native_runtime(tmp_path)
    bundles = ExecutionBundles(journal)
    runtime.execution_bundles = bundles
    old = bundles.publish(runtime.prompts, orchestration_revision=2)
    before = bundles.get(old)
    try:
        with pytest.raises(RuntimeInputUnavailable, match="revision 3"):
            runtime.execute_message(_source())
        assert bundles.get(old) == before
        bundles.publish(runtime.prompts, orchestration_revision=3)
        assert runtime.execute_message(_source()).orchestration_revision == 3
    finally:
        runtime.close()


def test_direction_claim_rolls_back_with_output_transaction(tmp_path):
    journal = RuntimeJournal(tmp_path / "rollback.db")
    case, p = fixture_case()
    key = encode(["MU", "P1"])
    journal.set("policy_watch", key, {"source_case_id": "previous"})
    with pytest.raises(RuntimeError, match="after claim"):
        with journal.transaction() as db:
            assert (
                claim_ready_in_transaction(
                    db, case=case, trade=trade(case, p), intent_id="rollback", now=NOW
                )
                == "READY"
            )
            raise RuntimeError("after claim")
    with journal.transaction(write=False) as db:
        assert not usage(db, "MU", "P1", "LONG")["consumed"]
    assert not journal.values("policy_consumptions")
    assert journal.get("policy_watch", key) == {"source_case_id": "previous"}


def test_condition_revision_ignores_sibling_edits_and_rejects_own_rule_changes():
    from doxagent.workflows.codex_document3.runtime_projection import project_policy_set_v3

    base = PolicySetV3(
        ticker="MU",
        policy_set_version=1,
        as_of=NOW,
        published_at=NOW,
        publication_state="COMPLETE",
        policies=[policy()],
    )
    old = project_policy_set_v3(base)["policies"][0]["activation_conditions"]
    sibling = base.model_copy(deep=True)
    sibling.policies[0].activation_conditions[1].criterion = "New independent short boundary"
    revised = project_policy_set_v3(sibling)["policies"][0]["activation_conditions"]
    assert old[0]["condition_revision"] == revised[0]["condition_revision"]
    assert old[1]["condition_revision"] != revised[1]["condition_revision"]


@pytest.mark.parametrize(
    "change,expected",
    [
        ("unchanged", "RELEASED"),
        ("sibling", "RELEASED"),
        ("selected", "RELEASE_REJECTED"),
        ("generation", "DUPLICATE_POLICY"),
    ],
)
def test_native_closed_candidate_freezes_only_at_valid_release(tmp_path, change, expected):
    import asyncio

    from doxagent.persistent_runtime_v2.policy_consumption import put_value
    from doxagent.persistent_runtime_v2.providers import RuntimeInputSnapshot
    from doxagent.persistent_runtime_v2.selection import SelectionResult, WeekendSelection
    from doxagent.semantic_clock import semantic_day

    runtime, journal = native_runtime(tmp_path)
    key = encode(["MU", "P1"])
    journal.set(
        "policy_watch",
        key,
        dict(
            ticker="MU",
            policy_id="P1",
            source_case_id="prior",
            reason="confirm",
            expires_at=(NOW + timedelta(days=1)).isoformat(),
        ),
    )
    try:
        runtime.execute_message(_source(), mode="CLOSED", closed_cycle_id="cycle")
        runtime.process_pending_effects()
        candidate = journal.values("candidates")[0]
        assert not journal.values("policy_consumptions") and journal.get("policy_watch", key)
        current = runtime.policies.projection.model_copy(deep=True)
        if change in {"sibling", "selected"}:
            index = 1 if change == "sibling" else 0
            current.policies[0].activation_conditions[index].condition_revision = "a" * 64
        elif change == "generation":
            with journal.transaction() as db:
                put_value(
                    db,
                    "policy_direction_usage",
                    encode(["MU", "P1", "LONG"]),
                    dict(usage(db, "MU", "P1", "LONG"), usage_generation=2),
                )
        runtime.input_snapshot_loader = lambda ticker: RuntimeInputSnapshot(
            runtime.known_events.current_index(ticker), current
        )
        journal.put_task(
            "selection",
            "MU",
            "SELECTION",
            {"day": semantic_day(journal.clock()).isoformat(), "sweep_id": "cycle"},
        )
        journal.set(
            "selection_snapshot", "selection", {"candidate_ids": [candidate["candidate_id"]]}
        )

        async def judge(payload):
            return SelectionResult(
                selection_id="selection",
                candidate_id=candidate["candidate_id"],
                reason="explicit winner",
            )

        selection = WeekendSelection(None, runtime, journal, judge=judge)
        task = journal.claim("selection")
        asyncio.run(selection(task))
        assert journal.get("candidates", candidate["candidate_id"])["status"] == expected
        assert len(journal.values("policy_consumptions")) == (1 if expected == "RELEASED" else 0)
        assert (journal.get("policy_watch", key) is None) == (expected == "RELEASED")
    finally:
        runtime.close()
