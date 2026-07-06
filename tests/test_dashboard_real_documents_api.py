from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from doxagent.blackboard import BlackboardService
from doxagent.blackboard.repository import InMemoryBlackboardRepository
from doxagent.dashboard_api import create_app
from doxagent.models import AgentName, DocumentType, ExpectationUnitDocument
from doxagent.monitoring.repository import InMemoryMonitoringRepository
from doxagent.monitoring.service import MonitoringBusService
from doxagent.persistent_runtime import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.schema import KnownEventsPatch, KnownEventsPatchLog
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler import (
    DashboardStateAPI,
    InMemoryRuntimeSchedulerRepository,
    UnifiedRuntimeSchedulerService,
)
from doxagent.runtime_scheduler.documents import WorkflowDocumentProvider
from tests.fixtures.phase1_contracts import (
    NOW,
    expectation_document,
    global_research_document,
    known_events_document,
    monitoring_config_document,
    monitoring_policy_document,
)


def test_dashboard_real_documents_current_versions_and_detail_use_blackboard() -> None:
    client, run_id, _runtime_service = _client_with_real_document_stack()

    current = client.get(
        "/api/dashboard/v1/tickers/NVDA/documents/current?types=document1,document2"
    )

    assert current.status_code == 200
    payload = current.json()["data"]
    assert payload["ticker"] == "NVDA"
    assert payload["document_run_id"] == run_id
    documents = {item["document_type"]: item for item in payload["documents"]}
    assert set(documents) == {"document1", "document2"}
    assert "raw" not in documents["document1"]
    assert documents["document1"]["document_type_label"] == "Document 1: Global Research"
    assert documents["document1"]["cards"][0]["card_id"] == "fundamental_report"
    assert documents["document2"]["cards"][0]["card_id"] == "exp_ai_demand"
    assert documents["document2"]["cards"][0]["fields"][0] == {
        "key": "direction",
        "label": "Direction",
        "value": "bullish",
    }

    versions = client.get("/api/dashboard/v1/tickers/NVDA/documents/document1/versions")

    assert versions.status_code == 200
    version_payload = versions.json()["data"]
    assert version_payload["page"]["has_more"] is False
    version = version_payload["items"][0]
    assert version["document_type"] == "document1"
    assert version["version_status"] == "current"
    assert version["summary"] == "Research section summary."

    detail = client.get(
        f"/api/dashboard/v1/tickers/NVDA/documents/document1/versions/{version['version_id']}"
    )

    assert detail.status_code == 200
    detail_payload = detail.json()["data"]
    assert detail_payload["ticker"] == "NVDA"
    assert detail_payload["version"]["version_id"] == version["version_id"]
    assert detail_payload["document"]["document_type"] == "document1"


def test_dashboard_real_strategy_events_policies_and_errors_are_contract_shaped() -> None:
    client, _run_id, runtime_service = _client_with_real_document_stack()
    runtime_service.repository.save_known_events_patch_log(
        KnownEventsPatchLog(
            source_message_id="std_runtime_known_event",
            ticker="NVDA",
            known_event_id="event_runtime_001",
            source_ref="runtime:std_runtime_known_event",
            change_reason="verified runtime material update",
            patch=KnownEventsPatch(
                event_id="event_runtime_001",
                event_time_or_window="2026-05-29",
                core_fact="Runtime confirmed hyperscaler order update",
                duplicate_detection_keys=["NVDA", "hyperscaler", "order"],
            ),
            changed_at=NOW + timedelta(hours=2),
        )
    )

    document3 = client.get("/api/dashboard/v1/tickers/NVDA/documents/current?types=document3")

    assert document3.status_code == 200
    document3_payload = document3.json()["data"]["documents"][0]
    assert document3_payload["document_type"] == "document3"
    assert document3_payload["cards"] == []

    document3_versions = client.get("/api/dashboard/v1/tickers/NVDA/documents/document3/versions")
    assert document3_versions.status_code == 200
    document3_version_id = document3_versions.json()["data"]["items"][0]["version_id"]
    document3_detail = client.get(
        f"/api/dashboard/v1/tickers/NVDA/documents/document3/versions/{document3_version_id}"
    )
    assert document3_detail.status_code == 200
    detail_cards = document3_detail.json()["data"]["document"]["cards"]
    assert [card["card_id"] for card in detail_cards] == ["known_events", "monitoring_policy"]

    known_events = client.get("/api/dashboard/v1/tickers/NVDA/known-events?limit=10")

    assert known_events.status_code == 200
    known_event_items = known_events.json()["data"]["items"]
    assert {item["event_id"] for item in known_event_items} >= {"event_runtime_001"}
    assert any(item["event_name"] == "Prior earnings release" for item in known_event_items)

    filtered_events = client.get(
        "/api/dashboard/v1/tickers/NVDA/known-events?expectation_id=exp_ai_demand"
    )

    assert filtered_events.status_code == 200
    assert filtered_events.json()["data"]["items"] == [
        item
        for item in known_event_items
        if "exp_ai_demand" in item["related_expectation_ids"]
    ]

    direct_trade_policies = client.get(
        "/api/dashboard/v1/tickers/NVDA/policies?action_type=DTC"
    )

    assert direct_trade_policies.status_code == 200
    policy_items = direct_trade_policies.json()["data"]["items"]
    assert len(policy_items) == 1
    assert policy_items[0]["action_type"] == "DTC"
    assert policy_items[0]["expectation_id"] == "exp_ai_demand"
    assert policy_items[0]["trigger_condition"] == (
        "confirmed order materially above expectation"
    )

    escalate_policies = client.get("/api/dashboard/v1/tickers/NVDA/policies?action_type=EBA")
    assert escalate_policies.status_code == 200
    assert escalate_policies.json()["data"]["items"][0]["action_type"] == "EBA"

    invalid_document_type = client.get(
        "/api/dashboard/v1/tickers/NVDA/documents/document4/versions"
    )
    assert invalid_document_type.status_code == 422
    assert invalid_document_type.json()["error"]["code"] == "INVALID_PARAMS"

    missing_version = client.get(
        "/api/dashboard/v1/tickers/NVDA/documents/document1/versions/missing_version"
    )
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "NOT_FOUND"


def test_dashboard_real_documents_current_is_scheduler_bound_until_manual_activation() -> None:
    scheduler, runtime_service, blackboard, original_run_id = (
        _scheduler_with_seeded_blackboard_objects()
    )
    scheduler.start_ticker("NVDA", now=NOW + timedelta(hours=1))
    newer_run = _add_blackboard_run(
        blackboard,
        created_at=NOW + timedelta(hours=2),
        research_summary="Newer Blackboard summary that is not active yet.",
        known_event_text="Newer known event should not be active yet.",
    )
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )

    current = client.get("/api/dashboard/v1/tickers/NVDA/documents/current?types=document1")

    assert current.status_code == 200
    current_payload = current.json()["data"]
    assert current_payload["document_run_id"] == original_run_id
    assert current_payload["documents"][0]["cards"][0]["summary"] == "Research section summary."

    versions = client.get("/api/dashboard/v1/tickers/NVDA/documents/document1/versions")

    assert versions.status_code == 200
    items = versions.json()["data"]["items"]
    by_run_id = {item["document_run_id"]: item for item in items}
    assert by_run_id[original_run_id]["version_status"] == "current"
    assert by_run_id[newer_run.run_id]["version_status"] == "historical"
    assert by_run_id[original_run_id]["reason_label"] == "workflow_generated"
    assert by_run_id[original_run_id]["reason_text"]
    assert by_run_id[original_run_id]["updated_by_label"]
    current_events = client.get("/api/dashboard/v1/tickers/NVDA/known-events")
    assert current_events.status_code == 200
    assert current_events.json()["data"]["items"][0]["event_name"] == "Prior earnings release"

    activated = client.post(
        "/api/dashboard/v1/tickers/NVDA/documents/activate",
        json={
            "document_run_id": newer_run.run_id,
            "reason": "Use reviewed research for dashboard validation.",
        },
    )

    assert activated.status_code == 200
    state = scheduler.repository.get_state("NVDA")
    assert state is not None
    assert state.document_run_id == newer_run.run_id
    assert state.document_status is not None
    assert state.document_status.blackboard_run_id == newer_run.run_id
    assert state.last_monitoring_config_version is not None
    assert scheduler.monitoring_service.repository.list_bindings(ticker="NVDA")
    assert runtime_service.repository.list_known_events(ticker="NVDA") == []

    after_current = client.get("/api/dashboard/v1/tickers/NVDA/documents/current?types=document1")

    assert after_current.status_code == 200
    after_payload = after_current.json()["data"]
    assert after_payload["document_run_id"] == newer_run.run_id
    assert (
        after_payload["documents"][0]["cards"][0]["summary"]
        == "Newer Blackboard summary that is not active yet."
    )
    after_events = client.get("/api/dashboard/v1/tickers/NVDA/known-events")
    assert after_events.status_code == 200
    assert (
        after_events.json()["data"]["items"][0]["event_name"]
        == "Newer known event should not be active yet."
    )

    after_versions = client.get("/api/dashboard/v1/tickers/NVDA/documents/document1/versions")

    assert after_versions.status_code == 200
    after_items = after_versions.json()["data"]["items"]
    after_by_run_id = {item["document_run_id"]: item for item in after_items}
    assert after_by_run_id[newer_run.run_id]["version_status"] == "current"
    assert after_by_run_id[newer_run.run_id]["reason_label"] == "manual_activated"
    assert "Use reviewed research" in after_by_run_id[newer_run.run_id]["reason_text"]
    assert after_by_run_id[original_run_id]["version_status"] == "historical"
    assert any(
        event.event_type == "document_run_manual_activated"
        and event.payload["document_run_id"] == newer_run.run_id
        for event in scheduler.repository.list_audit_events(ticker="NVDA")
    )


class _NoopWorkflow:
    def __init__(self, blackboard: BlackboardService) -> None:
        self.blackboard = blackboard

    def run(self, ticker: str) -> None:
        return None


def _client_with_real_document_stack() -> tuple[
    TestClient,
    str,
    PersistentRuntimeExecutionService,
]:
    scheduler, runtime_service, run_id = _scheduler_with_seeded_blackboard()
    scheduler.start_ticker("NVDA", now=NOW + timedelta(hours=1))
    client = TestClient(
        create_app(
            mode="real",
            auth_mode="mock-open",
            dashboard_api=DashboardStateAPI(scheduler),
        )
    )
    return client, run_id, runtime_service


def _scheduler_with_seeded_blackboard() -> tuple[
    UnifiedRuntimeSchedulerService,
    PersistentRuntimeExecutionService,
    str,
]:
    scheduler, runtime_service, _blackboard, run_id = _scheduler_with_seeded_blackboard_objects()
    return scheduler, runtime_service, run_id


def _scheduler_with_seeded_blackboard_objects() -> tuple[
    UnifiedRuntimeSchedulerService,
    PersistentRuntimeExecutionService,
    BlackboardService,
    str,
]:
    blackboard = BlackboardService(InMemoryBlackboardRepository())
    run = _add_blackboard_run(blackboard, created_at=NOW)
    document_provider = WorkflowDocumentProvider(
        workflow=_NoopWorkflow(blackboard),  # type: ignore[arg-type]
        blackboard=blackboard,
        max_age=timedelta(days=365),
    )
    runtime_service = PersistentRuntimeExecutionService(InMemoryPersistentRuntimeRepository())
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=document_provider,
        monitoring_service=MonitoringBusService(InMemoryMonitoringRepository()),
        runtime_service=runtime_service,
    )
    return scheduler, runtime_service, blackboard, run.run_id


def _add_blackboard_run(
    blackboard: BlackboardService,
    *,
    created_at,
    research_summary: str = "Research section summary.",
    known_event_text: str = "Prior earnings release",
):
    run = blackboard.start_run("NVDA", AgentName.SYSTEM)
    run = run.model_copy(update={"created_at": created_at}, deep=True)
    run.belief_state.documents = _document_buckets(
        created_at=created_at,
        research_summary=research_summary,
        known_event_text=known_event_text,
    )
    return blackboard.repository.save(run)


def _document_buckets(
    *,
    created_at=NOW,
    research_summary: str = "Research section summary.",
    known_event_text: str = "Prior earnings release",
) -> dict[DocumentType, dict[str, Any]]:
    global_document = global_research_document()
    global_document = global_document.model_copy(
        update={
            "created_at": created_at,
            "fundamental_report": global_document.fundamental_report.model_copy(
                update={"summary": research_summary},
                deep=True,
            ),
        },
        deep=True,
    )
    expectation = ExpectationUnitDocument.model_validate(expectation_document())
    expectation = expectation.model_copy(update={"created_at": created_at}, deep=True)
    known_events = known_events_document()
    known_events = known_events.model_copy(
        update={
            "created_at": created_at,
            "events": [
                known_events.events[0].model_copy(
                    update={
                        "core_fact": known_event_text,
                        "description": known_event_text,
                    },
                    deep=True,
                )
            ],
        },
        deep=True,
    )
    monitoring_config = monitoring_config_document()
    monitoring_config = monitoring_config.model_copy(update={"created_at": created_at}, deep=True)
    monitoring_policy = monitoring_policy_document()
    monitoring_policy = monitoring_policy.model_copy(update={"created_at": created_at}, deep=True)
    return {
        DocumentType.GLOBAL_RESEARCH: {
            global_document.document_id: {"document": global_document.model_dump(mode="json")}
        },
        DocumentType.EXPECTATION_UNIT: {
            expectation.document_id: {"document": expectation.model_dump(mode="json")}
        },
        DocumentType.KNOWN_EVENTS: {
            known_events.document_id: {"document": known_events.model_dump(mode="json")}
        },
        DocumentType.MONITORING_CONFIG: {
            monitoring_config.document_id: {
                "document": monitoring_config.model_dump(mode="json")
            }
        },
        DocumentType.MONITORING_POLICY: {
            monitoring_policy.document_id: {
                "document": monitoring_policy.model_dump(mode="json")
            }
        },
    }
