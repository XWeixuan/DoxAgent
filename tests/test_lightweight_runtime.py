"""Mode isolation and the existing durable execution boundaries, without live services."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from doxagent.event_library.contracts import CanonicalEvent, CanonicalFact
from doxagent.event_library.provider import EventDetailSnapshot
from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
from doxagent.persistent_runtime_v2.projection import RuntimeTerminalProjection
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.providers import RuntimeInputSnapshot
from doxagent.persistent_runtime_v2.schema import (
    RuntimeVersionPin,
    W1NoveltyResult,
    W1Round1Result,
    W3ContextVersionPin,
)
from doxagent.persistent_runtime_v2.w3 import W3PreparedContext, research_pin_key
from doxagent.persistent_runtime_v2.w3_contracts import W3_V6_CONTRACT, W3OperationsResultV6
from doxagent.persistent_runtime_v2.w3_v6 import operations_input, trade_input
from doxagent.running_mode import RunningMode
from doxagent.ticker_initialization.catalog import default_plan
from doxagent.v2_control.repository import ControlError, ControlRepository
from doxagent.v2_control.running_mode_switch import active_switch, assert_quiet
from tests.test_persistent_runtime_v2 import _FakeResponses, _source
from tests.test_w3_v4 import runtime
from tests.test_w3_v5_execution import UnifiedAgent


def light_runtime(tmp_path, verdict="NEW", confidence="normal"):
    service, journal, _, clock = runtime(tmp_path)

    class Responses(_FakeResponses):
        def complete(self, request):
            result = super().complete(request)
            if request.output_model is W1Round1Result:
                result = replace(result, value=W1Round1Result(event_ids=["E1"]))
            if request.output_model is W1NoveltyResult:
                result = replace(
                    result,
                    value=W1NoveltyResult(
                        result=verdict, confidence=confidence, reference_ids=["E1"], reason="clear"
                    ),
                )
            return result

    service.responses = Responses()
    event = CanonicalEvent(
        event_id="E1",
        ticker="MU",
        title="Known qualification",
        event_type="CUSTOMER_MILESTONE",
        occurred_at="2026-08-29",
        occurrence_time_precision="DAY",
        status="ACTIVE",
        canonical_summary="Known fact.",
        known_event_summary="Known fact.",
        is_important=True,
        include_in_reference_view=True,
        related_event_ids=[],
        supersedes_event_id=None,
        derived_from_event_ids=[],
        facts=[
            CanonicalFact(
                fact_id="F1",
                proposition="Known fact.",
                assertion_state="ACTUAL",
                subject_time=None,
                fact_occurred_at="2026-08-29",
                fact_occurrence_time_precision="DAY",
            )
        ],
    )
    service.known_events.details = lambda ticker, version, event_ids: EventDetailSnapshot(
        ticker=ticker,
        version=version,
        requested_event_ids=event_ids,
        events=[event],
        missing_event_ids=[],
    )
    agent = UnifiedAgent("LONG")
    agent.prepare_case = lambda case: W3PreparedContext(
        version_pin=W3ContextVersionPin(
            running_mode="LIGHTWEIGHT",
            document1_run_id="d1",
            event_library_version=case.version_pin.event_library_version,
        ),
        document1="D1",
        document2_publication_state="NOT_APPLICABLE",
        context_cutoffs={"D1": None},
    )
    original = agent.run_phase

    def run(**kw):
        result, job = original(**kw)
        if kw["phase"] == "post_decision_operations":
            result = W3OperationsResultV6(
                watch_actions=[dict(action="SET", policy_id="P1", reason="ignored")], pin_actions=[]
            )
        return result, job

    agent.run_phase = run
    service._w3_agent = agent
    assets = {
        key: "shared immutable instructions"
        for key in ("w3/AGENTS.md", "w3/skills/skill.md", "w3/skills/operations.md")
    }
    service.execution_bundles = ExecutionBundles(journal)
    service.execution_bundles.publish(
        RuntimeV2PromptSet.load(),
        assets=assets,
        orchestration_revision=3,
        w3_contract_version=W3_V6_CONTRACT,
    )
    service.input_snapshot_loader = lambda ticker: RuntimeInputSnapshot(
        index=service.known_events.current_index(ticker),
        projection=None,
        running_mode=RunningMode.LIGHTWEIGHT,
        document1_run_id="d1",
        activation_revision_id="light",
    )
    return service, journal, clock


@pytest.mark.parametrize("verdict", ["NEW", "OLD"])
@pytest.mark.parametrize("confidence", ["normal", "low"])
@pytest.mark.parametrize("mode", ["REALTIME", "CLOSED"])
def test_light_w1_route_and_durable_replay(tmp_path, verdict, confidence, mode):
    service, journal, clock = light_runtime(tmp_path, verdict, confidence)
    try:
        source = _source().model_copy(update={"message_bus_event_time": clock[0]})
        case = service.execute_message(source, mode=mode)
        assert case.route is not None, (case.error_code, case.error_message)
        assert case.route.primary_route == ("W3" if verdict == "NEW" else "ARCHIVE"), (
            case.model_dump()
        )
        assert case.w2_final is None and case.w2_alignment is None and not case.first_round_tags
        assert case.w2_contract_version is None
        assert "projection" not in case.frozen_inputs
        assert case.version_pin.policy_set_version is None
        assert all(
            r.output_model in {W1Round1Result, W1NoveltyResult} for r in service.responses.calls
        )
        count = len(service.responses.calls)
        assert service.execute_message(source, mode=mode).case_id == case.case_id
        assert len(service.responses.calls) == count
        terminal = RuntimeTerminalProjection.from_case(case, model_turn_count=count)
        assert terminal.policy_set_version is None and terminal.running_mode == "LIGHTWEIGHT"
        assert service.process_pending_effects() == 1, [
            (e.last_error, e.status) for e in service.repository.list_effects(case.case_id)
        ]
        if verdict == "NEW":
            assert service.process_pending_effects() == 1
            saved = service.repository.get_case(case.case_id)
            assert saved.w3_operations_status == "COMMITTED"
            assert saved.w3_trade_decision["decision"] == "LONG"
            applied = journal.get("w3_operations_applied", case.case_id)
            assert applied["watch"][0]["status"] == "IGNORED_LIGHTWEIGHT"
            assert not journal.values("policy_watch")
            with journal.transaction(write=False) as db:
                assert not db.execute(
                    "SELECT 1 FROM runtime_v2_badcases WHERE case_id=?", (case.case_id,)
                ).fetchone()
                assert not db.execute(
                    "SELECT 1 FROM runtime_v2_w3_coverage_gaps WHERE case_id=?", (case.case_id,)
                ).fetchone()
        else:
            assert service.repository.get_case(case.case_id).status == "COMPLETED"
    finally:
        service.close()


def test_light_dag_and_contracts():
    light = default_plan(running_mode=RunningMode.LIGHTWEIGHT, o4_delivery_enabled=True)
    assert not {n.block for n in light} & {"D2", "D3", "O4"}
    assert next(n for n in light if n.key == "o2").dependencies == ["d1", "cdecr"]
    assert {n.key for n in light} >= {"monitoring.prepare", "bus.ready", "runtime.ready"}
    with pytest.raises(ValidationError):
        RuntimeVersionPin(event_library_version=1, provisional_snapshot_version=0)
    with pytest.raises(ValidationError):
        RuntimeVersionPin(
            running_mode="LIGHTWEIGHT",
            document1_run_id="d1",
            document2_run_id="d2",
            event_library_version=1,
            provisional_snapshot_version=0,
        )
    with pytest.raises(ValidationError):
        W3ContextVersionPin(
            running_mode="LIGHTWEIGHT",
            document1_run_id="d1",
            document2_run_id="d2",
            event_library_version=1,
        )


def test_light_model_input_uses_existing_fields_only(tmp_path):
    service, journal, clock = light_runtime(tmp_path)
    try:
        case = service.execute_message(_source())
        raw = dict(
            ticker="MU",
            message={},
            w1={},
            additional_tags=[],
            pins={},
            contexts=[{"name": "D1"}, {"name": "D2"}],
            w2={"secret": 1},
            policies=[{}],
            running_mode="LIGHTWEIGHT",
        )
        visible = trade_input(case, raw, None)
        assert set(visible) == {
            "ticker",
            "message",
            "w1",
            "additional_tags",
            "pins",
            "contexts",
            "recent_trade",
        }
        assert visible["contexts"] == [{"name": "D1"}]
        ops = operations_input(
            case,
            dict(
                submitted_decision={},
                output_disposition="READY",
                pins={},
                watch={},
                policy_targets=[],
            ),
        )
        assert set(ops) == {"submitted_decision", "output_disposition", "pins"}
        full = case.model_copy(
            update={
                "version_pin": RuntimeVersionPin(
                    document1_run_id="d1",
                    document2_run_id="d2",
                    event_library_version=7,
                    provisional_snapshot_version=0,
                    policy_set_version=1,
                    runtime_projection_version=1,
                )
            }
        )
        assert research_pin_key(case) != research_pin_key(full)
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["CASE", "SWEEP", "MAINTENANCE", "SELECTION"])
def test_switch_blocks_all_pending_task_types_including_future_due(tmp_path, kind):
    service, journal, clock = light_runtime(tmp_path)
    try:
        journal.put_task("unfinished", "MU", kind, {}, due_at=clock[0] + timedelta(days=3))
        with pytest.raises(ControlError, match="MODE_SWITCH_BUSY:unfinished"):
            assert_quiet(journal, "MU")
        assert_quiet(journal, "OTHER")
    finally:
        service.close()


def test_conversion_gate_does_not_change_ordinary_pause(tmp_path):
    service, journal, clock = light_runtime(tmp_path)
    try:
        control = ControlRepository(journal)
        control.migrate()
        op = control.submit(
            "MU",
            "START",
            actor="test",
            key="start-light-test",
            body={"monitor_mode": "MESSAGE_MONITORING", "initialization": "REUSE_ACTIVE"},
        )
        control.settle(op["id"])
        state = control.get("MU")
        pause = control.submit(
            "MU",
            "PAUSE",
            actor="test",
            key="pause-light-test",
            body={},
            expected=str(state["revision"]),
        )
        control.settle(pause["id"])
        journal.put_task("task", "MU", "MAINTENANCE", {})
        assert journal.claim("task")  # ordinary FULL pause claim semantics unchanged
        state = control.get("MU")
        op = control.submit(
            "MU",
            "RESTART",
            actor="test",
            key="switch-light-test",
            body={"initialization": "REUSE_ACTIVE", "running_mode": "LIGHTWEIGHT"},
            expected=str(state["revision"]),
        )
        with journal.transaction(write=False) as db:
            assert active_switch(db, "MU") and not active_switch(db, "OTHER")
        assert journal.claim("task") is None
        with pytest.raises(ControlError, match="MODE_SWITCH_IN_PROGRESS"):
            journal.put_task("blocked", "MU", "SELECTION", {})
        journal.put_task("other", "OTHER", "SELECTION", {})
        assert journal.claim("other")
        with pytest.raises(ControlError, match="MODE_SWITCH_IN_PROGRESS"):
            control.admit("new", "MU")
    finally:
        service.close()


def test_light_research_provider_never_reads_policy_or_d2_and_separates_cache(tmp_path):
    from unittest.mock import Mock

    from doxagent.persistent_runtime_v2.w3 import PublishedW3ContextProvider
    from tests.test_persistent_runtime_w3 import _PublishedContextRuntime

    source = _PublishedContextRuntime()
    policies, events = Mock(), Mock()
    provider = PublishedW3ContextProvider(
        runtime_repository=source, policy_repository=policies, event_library_reader=events
    )
    case = SimpleNamespace(
        ticker="MU",
        w3_contract_version=W3_V6_CONTRACT,
        version_pin=RuntimeVersionPin(
            running_mode="LIGHTWEIGHT",
            document1_run_id="d1-mu",
            event_library_version=7,
            provisional_snapshot_version=0,
        ),
    )
    first = asyncio.run(provider.load(case))
    second = asyncio.run(provider.load(case))
    assert first == second and source.current_document2_reads == 0
    assert source.document_reads == 1
    assert first.document2 is None and first.policy_set is None
    assert first.research_identity and first.version_pin.running_mode == "LIGHTWEIGHT"
    assert not policies.mock_calls and not events.mock_calls


def test_light_o2_maintenance_reuses_receipt_and_never_dispatches_o3(tmp_path):
    from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
    from tests.test_runtime_orchestration_recovery import maintenance_fixture

    service, journal, settings, control = maintenance_fixture(tmp_path)
    # Install the equivalent light base into this isolated prebuilt fixture.
    with control._write() as db:
        base = control.active_revision("MU")
        base["running_mode"] = "LIGHTWEIGHT"
        base["artifacts"].pop("document2")
        base["artifacts"].pop("document3")
        db.execute(
            "UPDATE activation_revisions SET payload=? WHERE revision_id='base'",
            (json.dumps(base),),
        )
    calls = []

    class O2:
        async def run(self, **kwargs):
            calls.append("o2")
            return None, None, None, {}

    def unexpected(*args):
        pytest.fail("light must not construct O3")

    maintain = RuntimeMaintenance(
        settings, service, journal, o2_factory=lambda *args: O2(), o3_factory=unexpected
    )
    task = journal.claim("maintain")
    try:
        result = asyncio.run(maintain(task))
        active = control.active_revision("MU")
        assert active["running_mode"] == "LIGHTWEIGHT"
        assert set(active["artifacts"]) == {
            "document1",
            "event_library",
            "monitoring_configuration",
        }
        assert result["revision_id"] == active["revision_id"]
        assert asyncio.run(maintain(task))["reconciled"]
        assert calls == ["o2"]
        assert not journal.values("reference_deliveries")
    finally:
        service.close()


def test_switch_rejects_unknown_worker_but_not_settled_history(tmp_path):
    service, journal, _ = light_runtime(tmp_path)
    try:
        journal.set("worker_requests", "remote", {"ticker": "MU"})
        with pytest.raises(ControlError, match="MODE_SWITCH_BUSY:remote"):
            assert_quiet(journal, "MU")
        journal.set(
            "worker_receipts", "remote", {"status": "succeeded", "execution_phase": "SETTLED"}
        )
        assert_quiet(journal, "MU")
        journal.set("worker_receipts", "remote", {"status": "failed", "execution_phase": "CLEANUP"})
        with pytest.raises(ControlError, match="MODE_SWITCH_BUSY:remote"):
            assert_quiet(journal, "MU")
    finally:
        service.close()


def test_light_activation_and_replacement_cannot_restore_policy(tmp_path):
    from datetime import UTC, datetime

    from doxagent.ticker_initialization.operations import replace_artifact, submit_activation
    from doxagent.ticker_initialization.repository import InitializationRepository
    from doxagent.ticker_initialization.schema import InitializationError, NodeSpec

    control = InitializationRepository(tmp_path / "control.db")
    run = control.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="activation", block="ACTIVATION")],
        running_mode="LIGHTWEIGHT",
    )
    lease = control.claim("test")
    control.stage_revision(
        lease,
        "light",
        {
            "document1": {"run_id": "d1"},
            "event_library": {"version": 1},
            "monitoring_configuration": {"initialization_id": "original"},
        },
    )
    control.activate(lease, "light")
    with pytest.raises(InitializationError, match="does not accept"):
        replace_artifact(
            control, "MU", role="document3", reference={"version": 1}, reason="forbidden"
        )
    with pytest.raises(InitializationError, match="reverse"):
        submit_activation(
            control,
            "MU",
            artifacts={},
            reason="forbidden",
            cutoff=run.research_cutoff_at,
            running_mode="FULL",
        )
    with pytest.raises(InitializationError, match="forbids"):
        submit_activation(
            control,
            "MU",
            artifacts={"document2": {}},
            reason="forbidden",
            cutoff=run.research_cutoff_at,
        )


def test_switch_remains_pending_after_commit_until_run_and_both_acks(tmp_path):
    from doxagent.ticker_initialization import (
        InitializationRepository,
        InitializationWorker,
        NodeResult,
        NodeSpec,
    )
    from doxagent.v2_control.running_mode_switch import drive_switch
    from doxagent.v2_control.service import ControlService

    service, journal, _ = light_runtime(tmp_path)
    control = ControlRepository(journal)
    control.migrate()
    init = InitializationRepository(tmp_path / "initialization.db")
    init.submit("MU", journal.clock(), [NodeSpec(key="base", block="ACTIVATION")])
    first_lease = init.claim("test")
    refs = {
        "document1": {"run_id": "d1"},
        "document2": {"run_id": "d2"},
        "document3": {"version": 1},
        "event_library": {"version": 7},
        "monitoring_configuration": {"initialization_id": "existing-config"},
    }
    init.stage_revision(first_lease, "base", refs)
    init.activate(first_lease, "base")
    init.finish(first_lease, error="fixture finished")
    op = control.submit(
        "MU",
        "START",
        actor="test",
        key="start-switch-1",
        body={"monitor_mode": "MESSAGE_MONITORING"},
    )
    control.settle(op["id"], activation_id="base")
    original = control.get("MU")
    pause = control.submit(
        "MU",
        "PAUSE",
        actor="test",
        key="pause-switch-1",
        body={},
        expected=str(original["revision"]),
    )
    control.settle(pause["id"])
    paused = control.get("MU")
    op = control.submit(
        "MU",
        "RESTART",
        actor="test",
        key="switch-operation-1",
        body={"running_mode": "LIGHTWEIGHT", "initialization": "REUSE_ACTIVE"},
        expected=str(paused["revision"]),
    )
    driver = SimpleNamespace(repository=control, initialization=init, _mirrors=lambda *args: None)
    try:
        assert not drive_switch(driver, op, control.get("MU"), init.active_revision("MU"))
        run = init.by_control_operation(op["id"])
        assert run.running_mode == "LIGHTWEIGHT"
        assert run.control_epoch == op["epoch"]
        assert op["steps"]["running_mode_switch"] == run.initialization_id
        lease = init.claim("test")
        revision = init.stage_revision(
            lease,
            run.initialization_id + "-activation",
            {k: v for k, v in refs.items() if k not in {"document2", "document3"}},
        )
        init.activate(lease, revision["revision_id"])
        # The head is already LIGHTWEIGHT; step must keep waiting on the same switch.
        assert not ControlService.step(driver, op)
        assert not control.get("MU")["analysis_allowed"]

        # Isolated fake consumers complete the original durable DAG and report ACKs.
        class Adapter:
            async def reconcile(self, context):
                return None

            async def execute(self, context):
                if context.node.key == "activation.prepare":
                    return NodeResult(artifacts={"activation_revision": revision})
                if context.node.key == "activation.commit":
                    init.activate(context.lease, revision["revision_id"])
                return NodeResult(artifacts={})

        worker = InitializationWorker(init, lambda _: Adapter())
        asyncio.run(worker.run_claimed(lease))
        assert not ControlService.step(driver, op)
        init.acknowledge_revision("MU", revision["revision_id"], "bus")
        assert not ControlService.step(driver, op)
        init.acknowledge_revision("MU", revision["revision_id"], "runtime")
        assert ControlService.step(driver, op)
        state = control.get("MU")
        assert state["analysis_allowed"] and state["mode"] == original["mode"]
        assert state["mode_effective_at"] == original["mode_effective_at"]
        assert state["initialization_id"] == original["initialization_id"]
        assert state["removal_epoch"] == original["removal_epoch"]
        assert (
            init.active_revision("MU")["artifacts"]["monitoring_configuration"]
            == refs["monitoring_configuration"]
        )
        assert control.operation(op["id"])["state"] == "SUCCEEDED"
    finally:
        service.close()


def test_legacy_full_staging_receipt_remains_byte_identical(tmp_path):
    from datetime import UTC, datetime

    from doxagent.ticker_initialization import InitializationRepository, NodeSpec

    control = InitializationRepository(tmp_path / "initialization.db")
    control.submit("MU", datetime.now(UTC), [NodeSpec(key="activation", block="ACTIVATION")])
    lease = control.claim("test")
    revision = control.stage_revision(lease, "legacy", {})
    revision.pop("running_mode")
    original = json.dumps(revision)
    with control._write() as db:
        db.execute(
            "UPDATE activation_revisions SET payload=? WHERE revision_id='legacy'", (original,)
        )
    assert "running_mode" not in control.stage_revision(lease, "legacy", {})
    with control._connection() as db:
        assert (
            db.execute(
                "SELECT payload FROM activation_revisions WHERE revision_id='legacy'"
            ).fetchone()[0]
            == original
        )


def test_light_closed_selection_uses_no_policy_and_releases_once(tmp_path):
    from doxagent.persistent_runtime_v2.selection import SelectionResult, WeekendSelection
    from doxagent.semantic_clock import semantic_day

    service, journal, clock = light_runtime(tmp_path)
    try:
        service.execute_message(
            _source().model_copy(update={"message_bus_event_time": clock[0]}), mode="CLOSED"
        )
        service.process_pending_effects()
        service.process_pending_effects()
        candidates = journal.values("candidates")
        assert len(candidates) == 1
        candidate = candidates[0]
        journal.put_task(
            "selection",
            "MU",
            "SELECTION",
            {"day": semantic_day(clock[0]).isoformat(), "sweep_id": "closed"},
        )
        journal.set(
            "selection_snapshot", "selection", {"candidate_ids": [candidate["candidate_id"]]}
        )
        calls = []

        async def judge(payload):
            calls.append(payload)
            assert "projection" not in payload
            assert "running_mode" not in json.dumps(payload)
            assert "w2_result" not in json.dumps(payload)
            return SelectionResult(
                selection_id="selection", candidate_id=candidate["candidate_id"], reason="chosen"
            )

        selection = WeekendSelection(None, service, journal, judge=judge)
        task = journal.claim("selection")
        asyncio.run(selection(task))
        asyncio.run(selection(task))
        assert len(calls) == 1
        assert journal.get("candidates", candidate["candidate_id"])["status"] == "RELEASED"
        assert not service.repository.list_consumed_policy_revisions("MU")
        assert len(journal.values("trade_intents")) == 1
    finally:
        service.close()


def test_light_pin_and_fact_operations_remain_idempotent(tmp_path):
    service, journal, clock = light_runtime(tmp_path)
    original = service._w3_agent.run_phase

    def run(**kw):
        result, job = original(**kw)
        if kw["phase"] == "post_decision_operations":
            result = W3OperationsResultV6(
                watch_actions=[],
                pin_actions=[dict(action="SET", pin_id=None, reason="short baseline")],
                delta_candidates=[
                    dict(
                        proposition="A new fact",
                        assertion_state="ACTUAL",
                        occurrence_date=clock[0].date().isoformat(),
                        entities=["MU"],
                    )
                ],
            )
        return result, job

    service._w3_agent.run_phase = run
    try:
        case = service.execute_message(_source())
        service.process_pending_effects()
        service.process_pending_effects()
        assert len(journal.values("message_pins")) == 1
        facts = service.repository.list_daily_candidates("MU", case.trading_date)
        assert len(facts) == 1 and facts[0].originating_node == "W3"
        service._v4_execution().operations(service.repository.get_case(case.case_id))
        assert len(journal.values("message_pins")) == 1
        assert len(service.repository.list_daily_candidates("MU", case.trading_date)) == 1
    finally:
        service.close()


def test_light_does_not_inherit_previous_full_w3_entry(tmp_path):
    service, journal, _ = light_runtime(tmp_path)
    try:
        current = journal.get("execution", "active")
        legacy = service.execution_bundles.publish(RuntimeV2PromptSet.load(), activate=False)
        journal.set("w3_entry_active", "MU", legacy)
        journal.set("w3_entry", "previous", legacy)
        case = service.execute_message(_source())
        assert case.w3_contract_version == W3_V6_CONTRACT
        assert case.frozen_inputs["w3_execution_bundle_id"] == current
        assert case.route.primary_route == "W3"
    finally:
        service.close()


def test_light_read_projection_and_api_absence_are_explicit(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.api_v2.dto import coverage, validate
    from doxagent.v2_read.formal import FormalProjectors
    from doxagent.v2_read.libraries import LibraryIndexer
    from doxagent.v2_read.repository import ReadStore
    from tests.v2_backend.test_api import OfflineAuth

    service, journal, _ = light_runtime(tmp_path)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "test",
        "source",
        [
            dict(
                kind="native:source_definitions",
                ticker="",
                id="benzinga",
                data={"display_name": "Benzinga", "kind": "api"},
            )
        ],
    )
    mapper = FormalProjectors(store)
    document = dict(
        run_id="d1",
        artifact_id="d1-doc",
        content_sha256="a" * 64,
        schema_version="d1",
        published_at="2026-10-10T00:00:00Z",
    )
    mapper.artifacts = SimpleNamespace(
        ensure=lambda identity, *args, **kwargs: [
            dict(kind="document_ref", ticker="MU", id=identity, data=document)
        ]
    )
    library = dict(
        library_snapshot_id="events-7",
        library_version=7,
        published_at="2026-10-10T00:00:00Z",
        content_sha256="b" * 64,
    )
    monkeypatch.setattr(LibraryIndexer, "ensure", lambda *args, **kwargs: (library, []))
    revision = dict(
        revision_id="light",
        ticker="MU",
        running_mode="LIGHTWEIGHT",
        artifacts={
            "document1": {"run_id": "d1"},
            "event_library": {"version": 7, "root": str(tmp_path)},
            "monitoring_configuration": {"initialization_id": "config"},
        },
    )
    records = mapper.activation(revision)
    active = next(r["data"] for r in records if r["kind"] == "activation_revision")
    assert active["policy_set"] is None and active["document2"] is None
    validate("Activation", active)
    case = service.execute_message(_source())
    seq = store.ingest(
        "test",
        "light",
        [
            *records,
            dict(
                kind="native:runtime_v2_cases",
                ticker="MU",
                id=case.case_id,
                data=case.model_dump(mode="json"),
            ),
            *mapper.case(case.model_dump(mode="json"), 1),
        ],
    )
    control = ControlRepository(journal)
    control.migrate()
    try:
        with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
            for page, endpoint in [
                ("EXPECTATIONS", "expectations/current"),
                ("POLICIES", "policies/context"),
                ("RUNTIME", f"runtime/cases/{case.case_id}"),
            ]:
                view = store.save_token(
                    "developer",
                    page,
                    dict(
                        seq=seq,
                        as_of="2026-10-10T12:00:00Z",
                        wire={
                            "page": page,
                            "ticker": "MU",
                            "activation": {
                                "state": "AVAILABLE",
                                "data": active,
                                "reason": None,
                                "coverage": coverage(),
                            },
                        },
                    ),
                    view=True,
                    now=datetime.now(UTC),
                )
                response = client.get(
                    PREFIX + "/tickers/MU/" + endpoint,
                    params={"view_id": view},
                    headers={"Authorization": "Bearer offline"},
                )
                assert response.status_code == 200, response.text
                data = response.json()["data"]
                if page != "RUNTIME":
                    assert data["state"] == "NOT_PRODUCED" and data["reason"] == "NOT_APPLICABLE"
                else:
                    assert data["data"]["w2"]["reason"] == "NOT_APPLICABLE"
                    assert data["data"]["policy_set"] is None
                    assert data["data"]["summary"]["first_round_shape"] == "W1_ONLY"
                    assert data["data"]["summary"]["final_policy_hit"]["value"] is None
    finally:
        service.close()


def test_failed_switch_keeps_pause_and_closes_candidate_admission(tmp_path):
    service, journal, _ = light_runtime(tmp_path)
    control = ControlRepository(journal)
    control.migrate()
    try:
        op = control.submit(
            "MU",
            "START",
            actor="test",
            key="start-failure-1",
            body={"monitor_mode": "MESSAGE_MONITORING"},
        )
        control.settle(op["id"], activation_id="full")
        state = control.get("MU")
        pause = control.submit(
            "MU",
            "PAUSE",
            actor="test",
            key="pause-failure-1",
            body={},
            expected=str(state["revision"]),
        )
        control.settle(pause["id"])
        state = control.get("MU")
        switch = control.submit(
            "MU",
            "RESTART",
            actor="test",
            key="switch-failure-1",
            body={"running_mode": "LIGHTWEIGHT"},
            expected=str(state["revision"]),
        )
        assert control.get("MU")["admission_allowed"]
        control.fail(switch["id"], "MODE_SWITCH_BUSY:original-task")
        state = control.get("MU")
        assert state["status"] == "PAUSED" and not state["analysis_allowed"]
        assert not state["admission_allowed"] and not state["initialization_allowed"]
        assert not state["new_intent_allowed"]
    finally:
        service.close()


def test_light_initialization_progress_has_only_its_four_real_stages(tmp_path):
    from doxagent.v2_read.initialization import progress
    from doxagent.v2_read.repository import ReadStore

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    run = dict(
        ticker="MU",
        initialization_id="light",
        status="RUNNING",
        state_seq=1,
        manual_resume_required=False,
        running_mode="LIGHTWEIGHT",
    )
    value = progress(store, run, {"kind": "native:initialization_runs"})["data"]
    assert value["running_mode"] == "LIGHTWEIGHT"
    assert [s["step_key"] for s in value["steps"]] == [
        "RESEARCH",
        "EVENT_LIBRARY",
        "SOURCES_ACTIVATION",
        "START_RUNTIME",
    ]
    run.pop("running_mode")
    legacy = progress(store, run, {"kind": "native:initialization_runs"})["data"]
    assert len(legacy["steps"]) == 6 and "running_mode" not in legacy


def test_full_maintenance_feed_does_not_inject_running_mode_into_research():
    from doxagent.persistent_runtime_v2.schema import O3MaintenanceFeedV2, TradeRecord
    from tests.test_runtime_policy_direction_usage import fixture_case

    case, _ = fixture_case("metadata")
    trade = TradeRecord(
        case_id=case.case_id,
        ticker="MU",
        trading_date=case.trading_date,
        source=case.source,
        decision="LONG",
        decision_origin="W3",
        w3_case_id="w3",
        policy_set_version=case.version_pin.policy_set_version,
        w1_result=case.w1_final,
        w2_result=case.w2_final,
    )
    # Only test the serialized input boundary; unrelated feed fields are absent.
    feed = O3MaintenanceFeedV2.model_construct(
        trade_records=[trade], trade_candidates=[{"trade": trade.model_dump(mode="json")}]
    )
    visible = feed.model_dump(mode="json")
    assert "running_mode" not in json.dumps(visible)
    assert "running_mode" in trade.model_dump(mode="json")


def test_effect_claim_gate_is_ticker_specific_and_conversion_only(tmp_path):
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect, RuntimeEffectStatus

    service, journal, _ = light_runtime(tmp_path)
    control = ControlRepository(journal)
    control.migrate()
    # Admit both before applying the operator pause.
    op = control.submit(
        "MU",
        "START",
        actor="test",
        key="effect-start-1",
        body={"monitor_mode": "MESSAGE_MONITORING"},
    )
    control.settle(op["id"], activation_id="full")
    try:
        case = service.execute_message(_source())
        other = case.model_copy(
            update={
                "case_id": "other",
                "source": case.source.model_copy(
                    update={
                        "source_message_id": "other",
                        "snapshot": case.source.snapshot.model_copy(update={"ticker": "OTHER"}),
                    }
                ),
            }
        )
        service.repository.save_case(other)
        service.repository.enqueue_effect(
            RuntimeEffect(
                case_id="other", effect_type="archive_message", idempotency_key="other-archive"
            )
        )
        state = control.get("MU")
        pause = control.submit(
            "MU",
            "PAUSE",
            actor="test",
            key="effect-pause-1",
            body={},
            expected=str(state["revision"]),
        )
        control.settle(pause["id"])
        claimed = service.repository.claim_effects()
        assert {e.case_id for e in claimed} == {case.case_id, "other"}
        for effect in claimed:
            service.repository.save_effect(
                effect.model_copy(update={"status": RuntimeEffectStatus.PENDING})
            )
        state = control.get("MU")
        control.submit(
            "MU",
            "RESTART",
            actor="test",
            key="effect-switch-1",
            body={"running_mode": "LIGHTWEIGHT"},
            expected=str(state["revision"]),
        )
        assert {e.case_id for e in service.repository.claim_effects()} == {"other"}
    finally:
        service.close()


def test_switch_bus_admission_never_reinstalls_or_adds_bindings(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.schema import TickerMonitoringStatus, UpdateActor
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.ticker_initialization import consumers
    from doxagent.ticker_initialization.repository import InitializationRepository
    from doxagent.v2_control.mirror import apply
    from tests.test_ticker_initialization_activation import stage

    control = InitializationRepository(tmp_path / "control.db")
    _, _, config, _ = stage(control, tmp_path / "bus.db")
    bus = MessageBusV2Service(config.live)
    consumers.admit_bus_revisions(control, bus)
    bus.set_ticker_status("MU", TickerMonitoringStatus.PAUSED, actor=UpdateActor.USER)
    before = [b.model_dump(mode="json") for b in bus.repository.list_bindings(ticker="MU")]
    with bus.repository.transaction() as db:
        apply(
            db,
            {
                "ticker": "MU",
                "epoch": 1,
                "revision": 1,
                "analysis_allowed": False,
                "admission_allowed": True,
            },
        )

    def unexpected(*args, **kwargs):
        pytest.fail("switch must retain the installed configuration and bindings")

    monkeypatch.setattr(consumers, "CandidateConfiguration", unexpected)
    monkeypatch.setattr(bus, "start_ticker", unexpected)
    revision = {**control.active_revision("MU"), "running_mode": "LIGHTWEIGHT"}
    consumers._admit_bus(control, bus, revision)
    assert bus.repository.get_ticker_state("MU").status == TickerMonitoringStatus.RUNNING
    assert before == [b.model_dump(mode="json") for b in bus.repository.list_bindings(ticker="MU")]


def test_switch_uses_new_york_weekend_and_real_closed_semantic_day(tmp_path):
    from datetime import UTC, datetime

    from doxagent.v2_control.running_mode_switch import require_closed_weekend

    service, journal, clock = light_runtime(tmp_path)
    try:
        clock[0] = datetime(
            2026, 10, 10, 5, tzinfo=UTC
        )  # Saturday 01:00 ET, still Friday semantics.
        with pytest.raises(ControlError, match="MODE_SWITCH_REQUIRES_CLOSED"):
            require_closed_weekend(journal)
        clock[0] = datetime(2026, 10, 10, 12, tzinfo=UTC)
        require_closed_weekend(journal)
        clock[0] = datetime(2026, 10, 9, 22, tzinfo=UTC)
        with pytest.raises(ControlError, match="MODE_SWITCH_REQUIRES_WEEKEND"):
            require_closed_weekend(journal)
    finally:
        service.close()


def test_light_sweep_recovery_uses_its_frozen_bundle_mode(tmp_path):
    from doxagent.persistent_runtime_v2.maintenance_recovery import recover_failed

    service, journal, _ = light_runtime(tmp_path)
    try:
        for identity, mode in [("light-sweep", "LIGHTWEIGHT"), ("full-sweep", "FULL")]:
            journal.put_task(identity, "MU", "SWEEP", {})
            task = journal.claim(identity)
            journal.set("sweep_bundle", identity, {"inputs": {"running_mode": mode}})
            journal.fail(task, RuntimeError("owned sdk process exited"), retryable=False)
            # Simulate historical FAILED receipts: current transient failures cool down
            # as PENDING directly, while recover_failed also handles older stored rows.
            with journal.transaction() as db:
                db.execute(
                    "UPDATE runtime_tasks SET status='FAILED',due_at=? WHERE id=?",
                    (journal.clock().isoformat(), identity),
                )
        recover_failed(journal, "MU", running_mode="LIGHTWEIGHT")
        assert journal.get_task("light-sweep")["status"] == "PENDING"
        assert journal.get_task("full-sweep")["status"] == "FAILED"
    finally:
        service.close()


def test_frozen_full_selection_never_queries_model_after_mode_change(tmp_path):
    from datetime import UTC, datetime

    from doxagent.persistent_runtime_v2.selection import WeekendSelection
    from tests.test_runtime_orchestration_execution import runtime_at

    service, journal = runtime_at(tmp_path, [datetime(2026, 9, 6, 12, tzinfo=UTC)])
    try:
        service.execute_message(_source(), mode="CLOSED", closed_cycle_id="2026-09-05")
        service.process_pending_effects()
        candidate = journal.values("candidates")[0]
        service.input_snapshot_loader = lambda ticker: RuntimeInputSnapshot(
            service.known_events.current_index(ticker),
            None,
            document1_run_id="d1",
            running_mode="LIGHTWEIGHT",
        )
        journal.put_task("selection", "MU", "SELECTION", {"day": "2026-09-06", "sweep_id": "sweep"})
        journal.set(
            "selection_snapshot", "selection", {"candidate_ids": [candidate["candidate_id"]]}
        )
        task = journal.claim("selection")
        journal.checkpoint(
            task, judgment_inputs={"selection_id": "selection", "candidates": [candidate]}
        )

        async def unexpected(payload):
            pytest.fail("old FULL selection must not reach the model")

        with pytest.raises(ValueError, match="selection running mode changed"):
            asyncio.run(WeekendSelection(None, service, journal, judge=unexpected)(task))
    finally:
        service.close()
