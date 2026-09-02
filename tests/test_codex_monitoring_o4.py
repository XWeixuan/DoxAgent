from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AlertSeverity, OperationalAlert
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.workflows.codex_monitoring_o4.capability import (
    TOOLS_BY_NODE,
    O4OperationCapabilityCodec,
)
from doxagent.workflows.codex_monitoring_o4.dispatcher import O4AlertDispatcher
from doxagent.workflows.codex_monitoring_o4.orchestrator import MonitoringO4Orchestrator
from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository
from doxagent.workflows.codex_monitoring_o4.schema import (
    ConfigureCompletion,
    DeliveryItemSettlement,
    DeliveryItemStatus,
    DeliverySettlement,
    MonitoringConfigurationPlan,
    O4Request,
    O4RequestStatus,
    SourceCandidate,
    SourceNeedPlanItem,
    SourceNeedPriority,
    SourceNeedResolution,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


class _FakeRunner:
    def __init__(self, *, new_crawler: bool, delivery_status: DeliveryItemStatus) -> None:
        self.new_crawler = new_crawler
        self.delivery_status = delivery_status
        self.requests: list[O4Request] = []

    async def run(self, request: O4Request) -> BaseModel:
        self.requests.append(request)
        if request.node is CodexMonitoringO4Node.CONFIGURE:
            resolution = (
                SourceNeedResolution.NEW_CRAWLER_REQUIRED
                if self.new_crawler
                else SourceNeedResolution.KEEP_DEFAULT
            )
            plan = MonitoringConfigurationPlan(
                ticker=request.ticker,
                policy_set_id=f"{request.ticker}:policy-set:1",
                policy_set_version=1,
                policy_set_sha256=request.payload["policy_set_sha256"],
                document2_ref="d2-run-1",
                baseline_observed_at=NOW,
                baseline_summary={"sources": 6, "bindings": 0},
                source_needs=[
                    SourceNeedPlanItem(
                        source_need_id="need-1",
                        policy_ids=["p1"],
                        disclosure_actor="issuer",
                        disclosure_channel="IR",
                        observability_target="material updates",
                        priority=SourceNeedPriority.HIGH,
                        resolution=resolution,
                        rationale="covers p1",
                        primary_candidate=(
                            SourceCandidate(
                                candidate_id="ir-page",
                                display_name="Issuer IR",
                                url="https://example.com/ir",
                                evidence=["visited publication history"],
                            )
                            if self.new_crawler
                            else None
                        ),
                    )
                ],
                stopping_rationale="marginal downstream value exhausted",
            )
            return ConfigureCompletion(request_id=request.request_id, plan=plan)
        plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
        return DeliverySettlement(
            request_id=request.request_id,
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            ticker=plan.ticker,
            items=[
                DeliveryItemSettlement(
                    source_need_id="need-1",
                    status=self.delivery_status,
                    constraints=["bounded engineering cycles exhausted"],
                )
            ],
            summary="partial delivery",
        )


def _policy() -> dict[str, object]:
    return {
        "ticker": "MU",
        "policy_set_version": 1,
        "policies": [{"policy_id": "p1"}],
    }


def _orchestrator(
    tmp_path: Path, runner: _FakeRunner
) -> tuple[MonitoringO4Orchestrator, MonitoringO4Repository, MessageBusV2Repository]:
    o4_repository = MonitoringO4Repository(tmp_path / "o4.sqlite3")
    bus_repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(bus_repository)
    bus.bootstrap()
    return (
        MonitoringO4Orchestrator(
            repository=o4_repository,
            runner=runner,
            message_bus=bus,
            message_bus_enabled=True,
        ),
        o4_repository,
        bus_repository,
    )


@pytest.mark.asyncio
async def test_configure_without_new_crawler_starts_default_monitoring(tmp_path: Path) -> None:
    runner = _FakeRunner(new_crawler=False, delivery_status=DeliveryItemStatus.COMPLETED)
    orchestrator, repository, bus_repository = _orchestrator(tmp_path, runner)
    request = orchestrator.submit_configure(
        ticker="MU", policy_set=_policy(), document2={"shells": []}
    )

    result = await orchestrator.process(request)

    assert result.monitoring_started is True
    assert bus_repository.get_ticker_state("MU").status.value == "running"  # type: ignore[union-attr]
    assert {item.source_id for item in bus_repository.list_bindings(ticker="MU")} == {
        "benzinga_news",
        "finnhub_company_news",
    }
    assert [item.node for item in runner.requests] == [CodexMonitoringO4Node.CONFIGURE]
    repository.close()
    bus_repository.close()


@pytest.mark.asyncio
async def test_failed_crawler_delivery_is_degraded_but_never_blocks_bus_start(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.FAILED)
    orchestrator, repository, bus_repository = _orchestrator(tmp_path, runner)
    request = orchestrator.submit_configure(
        ticker="MU", policy_set=_policy(), document2={"shells": []}
    )

    result = await orchestrator.process(request)

    assert result.monitoring_started is True
    assert result.delivery is not None and result.delivery.degraded is True
    assert result.request.node is CodexMonitoringO4Node.DELIVER
    assert result.request.status is O4RequestStatus.DEGRADED
    assert bus_repository.get_ticker_state("MU") is not None
    assert [item.node for item in runner.requests] == [
        CodexMonitoringO4Node.CONFIGURE,
        CodexMonitoringO4Node.DELIVER,
    ]
    repository.close()
    bus_repository.close()


def test_repository_keeps_plan_immutable_and_deduplicates_global_repair(tmp_path: Path) -> None:
    repository = MonitoringO4Repository(tmp_path / "o4.sqlite3")
    first = repository.claim_repair("crawler:v1:transport", "request-a")
    second = repository.claim_repair("crawler:v1:transport", "request-b")

    assert first.owner_request_id == second.owner_request_id == "request-a"
    assert second.linked_request_ids == ["request-b"]
    repository.complete_repair_claim("crawler:v1:transport", "request-a")
    repository.close()


def test_o4_capability_is_node_scoped_and_tamper_evident() -> None:
    codec = O4OperationCapabilityCodec("s" * 40)
    token = codec.issue(
        run_id="monitoring-o4-mu-main",
        request_id="request-1",
        ticker="MU",
        node=CodexMonitoringO4Node.REPAIR,
    )
    claims = codec.verify(token, public_key=codec.public_key)

    assert set(claims.enabled_tool_ids) == TOOLS_BY_NODE[CodexMonitoringO4Node.REPAIR]
    assert "monitoring.hard_delete_source" not in claims.enabled_tool_ids
    with pytest.raises(CapabilityDenied):
        codec.verify(token[:-1] + ("A" if token[-1] != "A" else "B"), public_key=codec.public_key)


def test_dispatcher_only_queues_binding_level_source_poll_failures(tmp_path: Path) -> None:
    runner = _FakeRunner(new_crawler=False, delivery_status=DeliveryItemStatus.COMPLETED)
    orchestrator, repository, bus_repository = _orchestrator(tmp_path, runner)
    binding = orchestrator.message_bus.start_ticker("MU").ticker  # type: ignore[union-attr]
    assert binding == "MU"
    bus_repository.upsert_alert(
        OperationalAlert(
            alert_key="MU:benzinga:failure",
            severity=AlertSeverity.ERROR,
            code="source_poll_failure",
            message="failed",
            source_id="benzinga_news",
            binding_id="MU:benzinga_news",
        )
    )
    bus_repository.upsert_alert(
        OperationalAlert(
            alert_key="benzinga:aggregate",
            severity=AlertSeverity.ERROR,
            code="source_poll_failure_aggregate",
            message="aggregate",
            source_id="benzinga_news",
        )
    )

    class _NoCrawlerAlerts:
        def list_alerts(self, *, open_only: bool):
            assert open_only is True
            return []

    dispatcher = O4AlertDispatcher(
        orchestrator=orchestrator,
        message_bus=orchestrator.message_bus,  # type: ignore[arg-type]
        crawler_plane=_NoCrawlerAlerts(),  # type: ignore[arg-type]
    )
    queued = dispatcher.scan()

    assert len(queued) == 1
    assert queued[0].payload["trigger_json"]["alert_code"] == "source_poll_failure"
    repository.close()
    bus_repository.close()
