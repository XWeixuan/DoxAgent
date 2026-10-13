import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from doxagent.codex_worker.schema import WorkerJob
from doxagent.message_bus_v2.source_identity import business_publication_time, resolve_source_name
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.message_pin import apply_pin_actions, pin_snapshot
from doxagent.persistent_runtime_v2.policy_consumption import read_usage_snapshot, usage
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCaseStatus,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    W1NoveltyResult,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.trade_output import LocalTradeSink, TradeOutputService
from doxagent.persistent_runtime_v2.w3_context import case_labels, event_matches
from doxagent.persistent_runtime_v2.w3_contracts import (
    OPERATIONS_PHASE,
    TRADE_PHASE,
    W3_CONTRACT,
    MessagePinAction,
    W3OperationsResult,
    W3TradeDecision,
    parse_w3_operations,
)
from doxagent.persistent_runtime_v2.w3_research_assets import (
    derive_research_assets,
    natural_chapters,
)
from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3
from tests.test_persistent_runtime_v2 import _AnyPolicies, _AnyResponses, _FakeKnownEvents
from tests.test_persistent_runtime_w3 import _Context
from tests.test_runtime_policy_direction_usage import NOW, fixture_case, policy


def decision(kind="EXPERT_TRADE"):
    return W3TradeDecision.model_validate(
        dict(
            decision=kind,
            reason="material new business information",
            policy_trade=dict(policy_id="P1", condition_id="C1")
            if kind == "POLICY_TRADE"
            else None,
            expert_trade=dict(
                direction="LONG", prior_expectation="trial", expectation_delta="production"
            )
            if kind == "EXPERT_TRADE"
            else None,
        )
    )


def operations(*, watch=True, pin=True):
    return W3OperationsResult.model_validate(
        dict(
            novelty=dict(result="NEW", reference_ids=[], reason="new production"),
            policy=dict(
                policy_ids=["P1"],
                matched_condition_ids=[dict(policy_id="P1", condition_ids=["C1"])],
                reason="actual hit",
            ),
            delta_candidates=[
                dict(
                    proposition="Production started.",
                    assertion_state="ACTUAL",
                    subject_time=None,
                    occurrence_date=NOW.date().isoformat(),
                    entities=[],
                )
            ],
            watch_actions=[dict(action="SET", policy_id="P1", reason="follow volumes")]
            if watch
            else [],
            pin_actions=[dict(action="SET", pin_id=None, reason="short-term baseline")]
            if pin
            else [],
        )
    )


class PhaseAgent:
    def __init__(self, kind="EXPERT_TRADE"):
        self.kind = kind
        self.calls = []
        self.forks = 0
        self.fail_operations = False

    def prepare_case(self, case):
        context = asyncio.run(_Context().load(case))
        return context.model_copy(
            update=dict(
                policy_set=PolicySetV3(
                    ticker="MU",
                    policy_set_version=1,
                    as_of=NOW,
                    published_at=NOW,
                    publication_state="COMPLETE",
                    policies=[policy()],
                )
            )
        )

    def run_phase(self, *, case, record, phase, payload, **_):
        self.calls.append((case.case_id, phase, record.get("thread_id"), payload))
        if phase == OPERATIONS_PHASE and self.fail_operations:
            raise RuntimeError("offline operations failure")
        result = (
            {"ready": True}
            if phase == "w3_template"
            else (operations() if phase == OPERATIONS_PHASE else decision(self.kind))
        )
        job = WorkerJob(
            job_id=f"job-{len(self.calls)}",
            run_id=record["workspace"],
            attempt_id=phase,
            status="succeeded",
            thread_id=record.get("thread_id") or "template",
        )
        return result, job

    def register_template(self, _):
        pass

    def fork_thread(self, _):
        self.forks += 1
        return {"thread_id": f"child-{self.forks}"}

    def acknowledge_job(self, _):
        pass

    def thread_status(self, _):
        return dict(active=False, exists=True)

    def close(self):
        pass


def runtime(tmp_path, kind="EXPERT_TRADE"):
    clock = [NOW]
    journal = RuntimeJournal(tmp_path / "r.db", clock=lambda: clock[0])
    repo = SQLitePersistentRuntimeV2Repository(journal.path)
    agent = PhaseAgent(kind)
    service = PersistentRuntimeV2Service(
        repository=repo,
        journal=journal,
        responses=_AnyResponses(),
        known_events=_FakeKnownEvents(),
        policies=_AnyPolicies(),
        w3_agent=agent,
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
    )
    return service, journal, agent, clock


def enqueue(service, identity="case-1"):
    case, projection = fixture_case(identity)
    case.w3_contract_version = W3_CONTRACT
    case.orchestration_revision = 3
    case.status = RuntimeCaseStatus.PENDING_W3
    case.route = RuntimeRouteDecision(
        primary_route="W3", side_effects=[RuntimeSideEffect.ROUTE_TO_W3], reason="test"
    )
    case.frozen_inputs["w3_assets"] = {"w3/agent.md": "offline common role"}
    with service.journal.transaction() as db:
        case.frozen_inputs["policy_usage"] = read_usage_snapshot(db, "MU", projection.policies)
    service.repository.save_case(case)
    service._enqueue_route_effects(case, "")
    return case


@pytest.mark.parametrize("kind", ["NO_TRADE", "POLICY_TRADE", "EXPERT_TRADE"])
def test_trade_is_durable_before_operations_and_operations_cannot_retrade(tmp_path, kind):
    service, journal, agent, _ = runtime(tmp_path, kind)
    try:
        case = enqueue(service)
        assert service.process_pending_effects() == 1
        saved = service.repository.get_case(case.case_id)
        assert saved.status == RuntimeCaseStatus.COMPLETED
        assert saved.w3_operations_status == "PENDING"
        assert not service.repository.list_provisional("MU", case.trading_date)
        intents = journal.values("trade_intents")
        assert len(intents) == (kind != "NO_TRADE")
        if intents:
            assert intents[0]["status"] == "READY"
            assert asyncio.run(TradeOutputService(journal).deliver(LocalTradeSink(journal))) == 1
        assert service.process_pending_effects() == 1
        assert len(journal.values("trade_intents")) == len(intents)
        assert service.repository.get_case(case.case_id).w3_operations_status == "COMMITTED"
        assert len(service.repository.list_provisional("MU", case.trading_date)) == 1
        with journal.transaction(write=False) as db:
            assert usage(db, "MU", "P1", "LONG")["consumed"] == (kind == "POLICY_TRADE")
            assert pin_snapshot(db, "MU", NOW)["used"] == 1
        phases = [p for _, p, _, _ in agent.calls]
        assert phases == ["w3_template", TRADE_PHASE, OPERATIONS_PHASE]
        assert agent.calls[-1][2] == agent.calls[-2][2] == "child-1"
    finally:
        service.close()


def test_saved_decision_survives_output_failure_without_new_model_call(tmp_path, monkeypatch):
    service, journal, agent, _ = runtime(tmp_path)
    try:
        case = enqueue(service)
        original = TradeOutputService.record_in_transaction
        calls = [0]

        def flaky(self, *args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError("output transaction failed")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(TradeOutputService, "record_in_transaction", flaky)
        assert service.process_pending_effects() == 0
        assert not journal.values("trade_intents")

        def unavailable_context(_):
            raise AssertionError("saved decisions must not reread research or rerun the model")

        agent.prepare_case = unavailable_context
        assert service.process_pending_effects() == 1
        assert len(journal.values("trade_intents")) == 1
        assert sum(phase == TRADE_PHASE for _, phase, _, _ in agent.calls) == 1
        assert (
            service.repository.get_case(case.case_id).w3_trade_decision["decision"]
            == "EXPERT_TRADE"
        )
    finally:
        service.close()


def test_failed_operations_do_not_fail_or_repeat_committed_trade(tmp_path):
    service, journal, agent, _ = runtime(tmp_path)
    try:
        case = enqueue(service)
        service.process_pending_effects()
        agent.fail_operations = True
        for _ in range(3):
            service.process_pending_effects()
        saved = service.repository.get_case(case.case_id)
        assert saved.status == RuntimeCaseStatus.COMPLETED
        assert saved.w3_operations_status == "FAILED"
        assert len(journal.values("trade_intents")) == 1
        assert sum(p == TRADE_PHASE for _, p, _, _ in agent.calls) == 1
        assert service._v4_execution().registry.owned(case.case_id) is None
    finally:
        service.close()


def test_busy_policy_thread_forks_and_idle_policy_thread_resumes(tmp_path):
    service, _, agent, _ = runtime(tmp_path)
    try:
        enqueue(service, "one")
        service.process_pending_effects()
        enqueue(service, "two")
        effects = service.repository.claim_effects(limit=10)
        second = next(e for e in effects if e.case_id == "two")
        service._process_claimed_effect(second)
        assert agent.forks == 2
        for effect in effects:
            if effect.case_id == "one":
                service._process_claimed_effect(effect)
        enqueue(service, "three")
        for effect in service.repository.claim_effects(limit=10):
            if effect.case_id == "three":
                service._process_claimed_effect(effect)
        assert agent.forks == 2
        assert (
            next(t for cid, p, t, _ in agent.calls if cid == "three" and p == TRADE_PHASE)
            == "child-1"
        )
    finally:
        service.close()


def test_old_entry_and_information_tags_are_independent():
    case, _ = fixture_case()
    case.w1_final = W1NoveltyResult(
        result="OLD", confidence="normal", reference_ids=["E1"], reason="old"
    )
    assert (
        route_runtime_case(
            case.w1_final, case.w2_final, w3_contract_version=W3_CONTRACT
        ).primary_route.value
        == "ARCHIVE"
    )
    case.w1_final = W1NoveltyResult(
        result="OLD", confidence="low", reference_ids=["E1"], reason="old"
    )
    assert (
        route_runtime_case(
            case.w1_final, case.w2_final, w3_contract_version=W3_CONTRACT
        ).primary_route.value
        == "W3"
    )
    labels = case_labels(
        case, history_matches=[dict(events=[], policies=["P1"])], watch_followup=True
    )
    assert labels == dict(
        case_type="POLICY_HIT_REVIEW",
        additional_tags=["W1_LOW_CONFIDENCE", "WATCH_FOLLOWUP", "RECENT_TRADE"],
    )
    case.w1_final = W1NoveltyResult(
        result="NEW", confidence="normal", reference_ids=["E1"], reason="prior"
    )
    assert event_matches(case.model_dump(mode="json")) == []


def test_expiry_does_not_renew_on_operations_or_retry_and_deletes_only_child(tmp_path):
    service, journal, agent, clock = runtime(tmp_path)
    deleted = []
    agent.delete_thread = lambda tid: deleted.append(tid)
    try:
        case = enqueue(service)
        service.process_pending_effects()
        record = service._v4_execution().registry.owned(case.case_id)
        expiry = record["expires_at"]
        clock[0] += timedelta(hours=47)
        service.process_pending_effects()
        with journal.transaction(write=False) as db:
            row = db.execute(
                "SELECT expires_at FROM runtime_v2_w3_threads WHERE thread_id='child-1'"
            ).fetchone()
        assert row[0] == expiry
        clock[0] += timedelta(hours=2)
        service._v4_execution().maintain()
        assert deleted == ["child-1"]
        assert len(journal.values("trade_intents")) == 1
        assert service.repository.get_case(case.case_id).w3_trade_decision
        with journal.transaction(write=False) as db:
            assert (
                db.execute(
                    "SELECT status FROM runtime_v2_w3_threads WHERE kind='TEMPLATE'"
                ).fetchone()[0]
                == "READY"
            )
    finally:
        service.close()


def test_history_has_no_count_cap_and_freezes_execution_state(tmp_path):
    from doxagent.persistent_runtime_v2.policy_consumption import put_value
    from doxagent.persistent_runtime_v2.w3_context import freeze_history, history_payload

    service, journal, agent, clock = runtime(tmp_path)
    try:
        enqueue(service, "history-source")
        service.process_pending_effects()
        original = journal.values("trade_intents")[0]
        with journal.transaction() as db:
            for i in range(120):
                put_value(db, "trade_intents", f"historical-{i}", original)
        clock[0] += timedelta(seconds=1)
        current = enqueue(service, "current")
        membership = freeze_history(journal, current, clock[0])
        items, matches = history_payload(journal, service.repository, current, membership)
        assert len(items) == 121
        assert len(matches) == 121
        with journal.transaction() as db:
            put_value(db, "trade_receipts", "historical-0", {"status": "DELIVERED"})
        second, _ = history_payload(journal, service.repository, current, membership)
        assert second == items
        assert all(item["message"]["body"] for item in items)
    finally:
        service.close()


def test_pending_operations_do_not_hold_daily_maintenance(tmp_path):
    from doxagent.persistent_runtime_v2.bounded_inputs import unfinished

    service, journal, _, _ = runtime(tmp_path)
    try:
        case = enqueue(service)
        service.process_pending_effects()
        task = {"ticker": "MU", "inputs": {"case_ids": [case.case_id]}}
        assert not unfinished(service.repository, journal, task)
        assert any(
            e.effect_type is RuntimeSideEffect.W3_POST_DECISION and e.status.value == "PENDING"
            for e in service.repository.list_effects(case.case_id)
        )
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["POLICY_TRADE", "EXPERT_TRADE"])
def test_admitted_old_case_can_trade_and_closed_case_only_creates_a_candidate(tmp_path, kind):
    service, journal, _, _ = runtime(tmp_path, kind)
    try:
        case = enqueue(service, "old-low")
        case.w1_final = W1NoveltyResult(
            result="OLD", confidence="low", reference_ids=["E1"], reason="uncertain old"
        )
        journal.set(
            "round_inputs",
            f"{case.case_id}:W1:R2",
            {
                "payload": {
                    "event_details": {
                        "canonical_events": [
                            {
                                "event_id": "E1",
                                "canonical_summary": "known production baseline",
                                "facts": [],
                            }
                        ],
                        "provisional_events": [],
                    }
                },
            },
        )
        service.repository.save_case(case)
        assert service.process_pending_effects() == 1
        assert journal.values("trade_intents")[0]["status"] == "READY"
        closed = enqueue(service, "closed")
        closed.runtime_mode = "CLOSED"
        closed.closed_cycle_id = "weekend-test"
        service.repository.save_case(closed)
        service.process_pending_effects()
        assert len(journal.values("trade_intents")) == 1
        assert len(journal.values("candidates")) == 1
        assert journal.values("candidates")[0]["trade"]["w3_trade_decision"]["decision"] == kind
    finally:
        service.close()


def test_v4_bundle_requires_external_assets_and_can_be_published_without_activation(tmp_path):
    from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
    from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet

    journal = RuntimeJournal(tmp_path / "bundles.db")
    bundles = ExecutionBundles(journal)
    prompts = RuntimeV2PromptSet(*["synthetic"] * 7)
    old = bundles.publish(prompts, orchestration_revision=3)
    with pytest.raises(ValueError):
        bundles.publish(prompts, orchestration_revision=3, w3_contract_version=W3_CONTRACT)
    assets = {
        "w3/AGENTS.md": "external",
        "w3/skills/skill.md": "external",
    }
    new = bundles.publish(
        prompts,
        assets=assets,
        orchestration_revision=3,
        w3_contract_version=W3_CONTRACT,
        activate=False,
    )
    assert journal.get("execution", "active") == old
    assert bundles.get(new)["contracts"]["w3_contract_version"] == W3_CONTRACT


def test_pin_cancel_cannot_delete_a_recreated_pin_for_the_same_message(tmp_path):
    journal = RuntimeJournal(tmp_path / "pins.db")
    first, _ = fixture_case("first")
    second = first.model_copy(update={"case_id": "second"})
    action = MessagePinAction(action="SET", pin_id=None, reason="baseline")
    with journal.transaction() as db:
        apply_pin_actions(db, case=first, actions=[action], frozen={"items": []}, now=NOW)
        old = pin_snapshot(db, "MU", NOW)
        apply_pin_actions(
            db, case=second, actions=[action], frozen={"items": []}, now=NOW + timedelta(hours=25)
        )
        cancel = MessagePinAction(action="CANCEL", pin_id=old["items"][0]["pin_id"], reason="stale")
        result = apply_pin_actions(
            db, case=first, actions=[cancel], frozen=old, now=NOW + timedelta(hours=25)
        )
        current = pin_snapshot(db, "MU", NOW + timedelta(hours=25))
    assert result[0]["status"] == "STALE_OR_NOT_VISIBLE"
    assert current["used"] == 1
    assert current["items"][0]["pin_id"] != old["items"][0]["pin_id"]


def test_pin_capacity_is_atomic_and_pin_expiry_is_not_session_ttl(tmp_path):
    journal = RuntimeJournal(tmp_path / "r.db")
    action = MessagePinAction(action="SET", pin_id=None, reason="main line")

    def set_pin(i):
        case, _ = fixture_case(str(i))
        case.source = case.source.model_copy(update={"stream_item_id": f"stream-{i}"})
        with journal.transaction() as db:
            return apply_pin_actions(
                db, case=case, actions=[action], frozen={"items": []}, now=NOW
            )[0]["status"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(set_pin, range(3)))
    assert sorted(statuses) == ["APPLIED", "APPLIED", "CAPACITY_FULL"]
    with journal.transaction() as db:
        assert pin_snapshot(db, "MU", NOW + timedelta(hours=24))["used"] == 0


def test_operations_reject_transaction_fields_but_isolate_bad_actions():
    raw = operations().model_dump(mode="json")
    raw["pin_actions"].append(dict(action="SET", pin_id="arbitrary-history", reason="bad"))
    parsed, issues = parse_w3_operations(raw)
    assert len(parsed.pin_actions) == 1 and len(issues) == 1
    raw["decision"] = "EXPERT_TRADE"
    with pytest.raises(ValidationError):
        parse_w3_operations(raw)
    with pytest.raises(ValidationError):
        W3TradeDecision.model_validate(
            dict(
                decision="NO_TRADE",
                reason="bad",
                policy_trade={"policy_id": "P1", "condition_id": "C1"},
                expert_trade=None,
            )
        )


def test_research_slices_preserve_code_fences_and_complete_unit_policy():
    d1 = "# Report\nIntro\n## First\nFull text\n~~~python\n# not heading\n~~~\n## Second\nEnd\n"
    assert "".join(text for _, text in natural_chapters(d1)) == d1
    doc = SimpleNamespace(
        model_dump=lambda **_: dict(
            shells=[
                dict(
                    name="Supply",
                    units=[
                        dict(
                            name="Capacity",
                            state={"full": "state"},
                            expectation_baseline=["baseline"],
                            realization_factors=["factor"],
                            potential_gaps=["gap"],
                        )
                    ],
                )
            ]
        )
    )
    policy_set = PolicySetV3(
        ticker="MU",
        policy_set_version=1,
        as_of=NOW,
        published_at=NOW,
        publication_state="COMPLETE",
        policies=[policy()],
    )
    context = SimpleNamespace(document1=d1, document2=doc, policy_set=policy_set)
    _, files, index = derive_research_assets(context, policy_origins={"P1": ["Supply"]})
    unit = json.loads(files[index["document2"][0]["path"]])
    assert unit["expectation_baseline"] == ["baseline"]
    assert index["document2"][0]["derived_locator"]
    sliced = json.loads(files[index["document3"][0]["path"]])
    assert sliced == policy().model_dump(mode="json")
    assert "/000_unassigned/" not in index["document3"][0]["path"]


def test_source_identity_and_date_precision():
    assert resolve_source_name(publisher_name="Reuters", url="https://copy.test/a") == "reuters"
    assert resolve_source_name(url="https://www.example.co.uk/a") == "example.co.uk"
    assert business_publication_time(NOW, "DATE") == NOW.date().isoformat()
    assert business_publication_time(NOW, "UNKNOWN_FIRST_SEEN") is None
    from doxagent.message_bus_v2.compiler import format_member

    member = SimpleNamespace(
        source_name=None,
        source="collector task",
        published_at=NOW,
        publication_time_basis="UNKNOWN_FIRST_SEEN",
        title=None,
        body="original body",
        url=None,
    )
    compiled = format_member(member, display_index=1)
    assert "source: null" in compiled
    assert "published_at: null" in compiled
    assert "collector task" not in compiled
