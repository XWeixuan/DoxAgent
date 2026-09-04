from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.codex_worker.schema import WorkerJob
from doxagent.crawler_plane.schema import CrawlerAlert, CrawlerAlertType
from doxagent.mcp.o4_operations_server import O4OperationsApplication
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AlertSeverity, OperationalAlert, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.models import AgentName, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.monitoring import MonitoringToolClient
from doxagent.tools.schema import ToolRequest, ToolResult
from doxagent.workflows.codex_monitoring_o4.capability import (
    DELIVER_TOOLS,
    TOOLS_BY_NODE,
    O4OperationCapabilityCodec,
)
from doxagent.workflows.codex_monitoring_o4.dispatcher import O4AlertDispatcher
from doxagent.workflows.codex_monitoring_o4.orchestrator import MonitoringO4Orchestrator
from doxagent.workflows.codex_monitoring_o4.policy import (
    DeliveryProgressCoordinator,
    O4CrawlerPromotionPolicy,
    O4MutationPolicy,
    O4PlanFinalizer,
)
from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository
from doxagent.workflows.codex_monitoring_o4.runner import (
    MonitoringO4AgentRunner,
    O4TurnInterrupted,
)
from doxagent.workflows.codex_monitoring_o4.schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliveryItemSettlement,
    DeliveryItemStatus,
    DeliveryProgressState,
    DeliverySettlement,
    DeliveryWorkItemCheckpoint,
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
    assert {
        "crawler_plane.list_retries",
        "crawler_plane.resolve_retry",
        "crawler_plane.reactivate_retry",
    }.issubset(claims.enabled_tool_ids)
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


def test_dispatcher_queues_exhausted_crawler_retry_for_repair(tmp_path: Path) -> None:
    runner = _FakeRunner(new_crawler=False, delivery_status=DeliveryItemStatus.COMPLETED)
    orchestrator, repository, bus_repository = _orchestrator(tmp_path, runner)

    class _CrawlerAlerts:
        def list_alerts(self, *, open_only: bool):
            assert open_only is True
            return [
                CrawlerAlert(
                    alert_key="retry:MU:source:item",
                    crawler_id="source-crawler",
                    source_id="source",
                    binding_id="MU:source",
                    alert_type=CrawlerAlertType.RETRY_EXHAUSTED,
                    message="item retry exhausted",
                    metadata={"retry_id": "retry-1", "item_key": "item"},
                )
            ]

        def get_crawler(self, crawler_id: str):
            assert crawler_id == "source-crawler"
            return SimpleNamespace(active_version=2)

    dispatcher = O4AlertDispatcher(
        orchestrator=orchestrator,
        message_bus=orchestrator.message_bus,  # type: ignore[arg-type]
        crawler_plane=_CrawlerAlerts(),  # type: ignore[arg-type]
    )

    queued = dispatcher.scan()

    assert len(queued) == 1
    assert queued[0].node is CodexMonitoringO4Node.REPAIR
    assert queued[0].payload["trigger_json"]["alert_code"] == "crawler_retry_exhausted"
    assert queued[0].payload["trigger_json"]["metadata"]["retry_id"] == "retry-1"
    repository.close()
    bus_repository.close()


class _Workspace:
    def __init__(self) -> None:
        self.files: dict[tuple[str, str], str] = {}

    async def write_text(self, run_id: str, relative_path: str, content: str):
        self.files[(run_id, relative_path)] = content

    async def read_text(self, run_id: str, relative_path: str):
        if (run_id, relative_path) not in self.files:
            raise FileNotFoundError(relative_path)
        return SimpleNamespace(content=self.files[(run_id, relative_path)])


class _Worker:
    def __init__(self, digest: str) -> None:
        self.digest = digest
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        plan = MonitoringConfigurationPlan(
            ticker="MU",
            policy_set_id="MU:policy-set:1",
            policy_set_version=1,
            policy_set_sha256=self.digest,
            document2_ref="d2-run-1",
            baseline_observed_at=NOW,
            baseline_summary={},
            source_needs=[
                SourceNeedPlanItem(
                    source_need_id="need-1",
                    policy_ids=["p1"],
                    disclosure_actor="issuer",
                    disclosure_channel="IR",
                    observability_target="updates",
                    resolution=SourceNeedResolution.KEEP_DEFAULT,
                    rationale="default news coverage",
                )
            ],
            stopping_rationale="complete",
        )
        return WorkerJob(
            job_id=f"job-{request.attempt_id}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id="thread-mu",
            final_response=ConfigureCompletion(
                request_id=request.attempt_id, plan=plan
            ).model_dump_json(),
        )

    async def cancel(self, job_id: str):
        return None


@pytest.mark.asyncio
async def test_runner_reuses_one_ticker_thread_and_enables_only_o4_operations(
    tmp_path: Path,
) -> None:
    policy = _policy()
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    repository = MonitoringO4Repository(tmp_path / "o4.sqlite3")
    worker = _Worker(digest)
    runner = MonitoringO4AgentRunner(
        worker=worker,  # type: ignore[arg-type]
        workspace=_Workspace(),  # type: ignore[arg-type]
        repository=repository,
    )
    requests = [
        O4Request(
            ticker="MU",
            node=CodexMonitoringO4Node.CONFIGURE,
            payload={
                "policy_set_json": policy,
                "document2_json": {"shells": []},
                "policy_set_sha256": digest,
            },
            dedupe_key=f"configure-{index}",
        )
        for index in range(2)
    ]

    await runner.run(requests[0])
    await runner.run(requests[1])

    assert worker.requests[0].thread_id is None
    assert worker.requests[1].thread_id == "thread-mu"
    assert all(item.o4_operations_enabled for item in worker.requests)
    assert all(item.data_mcp_enabled is False for item in worker.requests)
    assert all(item.allow_subagents is False for item in worker.requests)
    assert all(item.model == "gpt-5.6-sol" and item.effort == "high" for item in worker.requests)
    repository.close()


def test_o4_operations_mcp_rejects_cross_ticker_input(tmp_path: Path) -> None:
    run_root = tmp_path / "monitoring-o4-mu-main"
    run_root.mkdir()
    codec = O4OperationCapabilityCodec("x" * 40)
    claims = codec.verify(
        codec.issue(
            run_id=run_root.name,
            request_id="request-1",
            ticker="MU",
            node=CodexMonitoringO4Node.CONFIGURE,
        ),
        public_key=codec.public_key,
    )
    application = O4OperationsApplication(
        claims=claims,
        cwd=run_root,
        settings=DoxAgentSettings(
            DOXAGENT_MESSAGE_BUS_V2_ENABLED=True,
            DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH=str(tmp_path / "bus.sqlite3"),
            DOXAGENT_CRAWLER_PLANE_ROOT=str(tmp_path / "crawler"),
            DOXAGENT_CRAWLER_PLANE_SQLITE_PATH=str(tmp_path / "crawler.sqlite3"),
        ),
    )

    result = application.call("monitoring_get_ticker_config", {"ticker": "NVDA"})

    assert result["ok"] is False
    assert result["error"]["code"] == "ticker_scope_violation"
    assert "crawler_plane_execute" not in application.by_mcp_name


def test_o4_operations_mcp_refreshes_node_capability_on_a_persistent_server(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "monitoring-o4-mu-main"
    run_root.mkdir()
    codec = O4OperationCapabilityCodec("x" * 40)
    token_box = {
        "value": codec.issue(
            run_id=run_root.name,
            request_id="request-configure",
            ticker="MU",
            node=CodexMonitoringO4Node.CONFIGURE,
        )
    }
    application = O4OperationsApplication(
        claims=codec.verify(token_box["value"], public_key=codec.public_key),
        capability_loader=lambda: codec.verify(token_box["value"], public_key=codec.public_key),
        cwd=run_root,
        settings=DoxAgentSettings(
            DOXAGENT_MESSAGE_BUS_V2_ENABLED=True,
            DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH=str(tmp_path / "bus.sqlite3"),
            DOXAGENT_CRAWLER_PLANE_ROOT=str(tmp_path / "crawler"),
            DOXAGENT_CRAWLER_PLANE_SQLITE_PATH=str(tmp_path / "crawler.sqlite3"),
        ),
    )

    assert "crawler_plane_execute" not in application.by_mcp_name
    token_box["value"] = codec.issue(
        run_id=run_root.name,
        request_id="request-deliver",
        ticker="MU",
        node=CodexMonitoringO4Node.DELIVER,
    )

    assert "crawler_plane_execute" in application.by_mcp_name
    assert "monitoring_hard_delete_source" not in application.by_mcp_name
    assert application.claims.node is CodexMonitoringO4Node.DELIVER
    assert set(application.claims.enabled_tool_ids) == DELIVER_TOOLS


@pytest.mark.asyncio
async def test_orchestrator_configure_to_deliver_reuses_thread_and_mcp_capability(
    tmp_path: Path,
) -> None:
    policy = _policy()
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_root = tmp_path / "monitoring-o4-mu-main"
    run_root.mkdir()
    codec = O4OperationCapabilityCodec("x" * 40)
    token_box = {
        "value": codec.issue(
            run_id=run_root.name,
            request_id="bootstrap-configure",
            ticker="MU",
            node=CodexMonitoringO4Node.CONFIGURE,
        )
    }
    application = O4OperationsApplication(
        claims=codec.verify(token_box["value"], public_key=codec.public_key),
        capability_loader=lambda: codec.verify(token_box["value"], public_key=codec.public_key),
        cwd=run_root,
        settings=DoxAgentSettings(
            DOXAGENT_MESSAGE_BUS_V2_ENABLED=True,
            DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH=str(tmp_path / "operations-bus.sqlite3"),
            DOXAGENT_CRAWLER_PLANE_ROOT=str(tmp_path / "operations-crawler"),
            DOXAGENT_CRAWLER_PLANE_SQLITE_PATH=str(tmp_path / "operations-crawler.sqlite3"),
        ),
    )

    class _TransitionWorker:
        def __init__(self) -> None:
            self.requests = []
            self.plan: MonitoringConfigurationPlan | None = None
            self.application_ids: list[int] = []

        async def run(self, request):
            self.requests.append(request)
            token_box["value"] = codec.issue(
                run_id=request.run_id,
                request_id=request.attempt_id,
                ticker=request.ticker,
                node=request.node,
            )
            self.application_ids.append(id(application))
            if request.node is CodexMonitoringO4Node.CONFIGURE:
                assert request.thread_id is None
                assert "crawler_plane_execute" not in application.by_mcp_name
                self.plan = MonitoringConfigurationPlan(
                    ticker="MU",
                    policy_set_id="MU:policy-set:1",
                    policy_set_version=1,
                    policy_set_sha256=digest,
                    document2_ref="d2-run-1",
                    baseline_observed_at=NOW,
                    baseline_summary={},
                    source_needs=[
                        SourceNeedPlanItem(
                            source_need_id="need-1",
                            policy_ids=["p1"],
                            disclosure_actor="issuer",
                            disclosure_channel="IR",
                            observability_target="updates",
                            resolution=SourceNeedResolution.NEW_CRAWLER_REQUIRED,
                            rationale="missing source-specific crawler",
                            primary_candidate=SourceCandidate(
                                candidate_id="issuer-ir",
                                display_name="Issuer IR",
                                url="https://example.test/ir",
                                evidence=["inspected publication surface"],
                            ),
                        )
                    ],
                    stopping_rationale="closed worklist",
                )
                response = ConfigureCompletion(
                    request_id=request.attempt_id,
                    plan=self.plan,
                )
            else:
                assert request.thread_id == "thread-mu"
                assert application.claims.node is CodexMonitoringO4Node.DELIVER
                assert "crawler_plane_execute" in application.by_mcp_name
                assert "crawler_plane_add_regression" in application.by_mcp_name
                assert self.plan is not None
                response = DeliverySettlement(
                    request_id=request.attempt_id,
                    plan_id=self.plan.plan_id,
                    plan_version=self.plan.plan_version,
                    ticker="MU",
                    items=[
                        DeliveryItemSettlement(
                            source_need_id="need-1",
                            status=DeliveryItemStatus.COMPLETED,
                        )
                    ],
                    summary="delivered",
                )
            return WorkerJob(
                job_id=f"job-{request.attempt_id}",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="succeeded",
                thread_id="thread-mu",
                final_response=response.model_dump_json(),
            )

        async def cancel(self, job_id: str):
            return None

    repository = MonitoringO4Repository(tmp_path / "o4-transition.sqlite3")
    worker = _TransitionWorker()
    runner = MonitoringO4AgentRunner(
        worker=worker,  # type: ignore[arg-type]
        workspace=_Workspace(),  # type: ignore[arg-type]
        repository=repository,
    )
    bus_repository = MessageBusV2Repository(tmp_path / "transition-bus.sqlite3")
    bus = MessageBusV2Service(bus_repository)
    bus.bootstrap()
    orchestrator = MonitoringO4Orchestrator(
        repository=repository,
        runner=runner,
        message_bus=bus,
        message_bus_enabled=True,
    )
    try:
        result = await orchestrator.process(
            orchestrator.submit_configure(
                ticker="MU",
                policy_set=policy,
                document2={"shells": []},
            )
        )

        assert result.monitoring_started is True
        assert [item.node for item in worker.requests] == [
            CodexMonitoringO4Node.CONFIGURE,
            CodexMonitoringO4Node.DELIVER,
        ]
        assert [item.run_id for item in worker.requests] == [run_root.name, run_root.name]
        assert worker.application_ids == [id(application), id(application)]
        assert repository.get_thread("MU").thread_id == "thread-mu"  # type: ignore[union-attr]
    finally:
        repository.close()
        bus_repository.close()


def test_binding_patch_without_parameters_preserves_existing_parameter_object(
    tmp_path: Path,
) -> None:
    repository = MessageBusV2Repository(tmp_path / "bus.sqlite3")
    bus = MessageBusV2Service(repository)
    bus.bootstrap()
    bus.start_ticker("MU")
    bus.update_binding(
        "MU:benzinga_news",
        {"source_parameters": {"search_terms": ["Micron"]}},
        actor=UpdateActor.AGENT,
    )
    client = MonitoringToolClient(service=bus).for_tool("monitoring.update_ticker_config")

    result = client.call(
        ToolRequest(
            tool_name="monitoring.update_ticker_config",
            ticker="MU",
            agent_name=AgentName.O4_MARKET_TRACE,
            input={"source_id": "benzinga_news", "enabled": False},
        )
    )

    assert result.succeeded
    binding = repository.get_binding("MU:benzinga_news")
    assert binding is not None
    assert binding.source_parameters == {"search_terms": ["Micron"]}
    assert binding.enabled is False
    repository.close()


def _delivery_plan(*, priority: SourceNeedPriority = SourceNeedPriority.NORMAL):
    return MonitoringConfigurationPlan(
        ticker="MU",
        policy_set_id="MU:policy-set:1",
        policy_set_version=1,
        policy_set_sha256="digest",
        document2_ref="d2-run-1",
        baseline_observed_at=NOW,
        baseline_summary={},
        source_needs=[
            SourceNeedPlanItem(
                source_need_id="need-1",
                policy_ids=["p1"],
                disclosure_actor="issuer",
                disclosure_channel="IR",
                observability_target="updates",
                priority=priority,
                resolution=SourceNeedResolution.NEW_CRAWLER_REQUIRED,
                rationale="missing source-specific crawler",
                primary_candidate=SourceCandidate(
                    candidate_id="primary",
                    display_name="Primary IR",
                    url="https://primary.example.test/ir",
                    evidence=["inspected primary publication surface"],
                ),
                alternative_candidates=[
                    SourceCandidate(
                        candidate_id="alternative",
                        display_name="Alternative IR",
                        url="https://alternative.example.test/ir",
                        evidence=["inspected equivalent publication surface"],
                    )
                ],
            )
        ],
        stopping_rationale="closed candidate set",
    )


def test_priority_is_compatible_but_finalized_to_normal() -> None:
    old_plan = _delivery_plan(priority=SourceNeedPriority.HIGH)

    parsed = MonitoringConfigurationPlan.model_validate_json(old_plan.model_dump_json())
    finalized = O4PlanFinalizer(mutation_policy=O4MutationPolicy()).finalize(parsed)

    assert parsed.source_needs[0].priority is SourceNeedPriority.HIGH
    assert finalized.source_needs[0].priority is SourceNeedPriority.NORMAL


def test_progress_coordinator_resets_progress_and_exhausts_after_four_stalls() -> None:
    plan = _delivery_plan()
    coordinator = DeliveryProgressCoordinator()
    previous = DeliveryCheckpoint(
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        ticker=plan.ticker,
        items=[
            DeliveryWorkItemCheckpoint(
                source_need_id="need-1",
                candidate_id="primary",
                status=DeliveryItemStatus.IN_PROGRESS,
                progress_state=DeliveryProgressState.STALLED,
                consecutive_stalled_cycles=2,
                latest_blocker="detail 403",
            )
        ],
    )
    progress = DeliveryCheckpoint(
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        ticker=plan.ticker,
        items=[
            DeliveryWorkItemCheckpoint(
                source_need_id="need-1",
                candidate_id="primary",
                status=DeliveryItemStatus.IN_PROGRESS,
                progress_state=DeliveryProgressState.PROGRESSING,
                latest_execution_id="crawler_exec_new",
                latest_evidence_refs=["cassette_new"],
                latest_blocker="date parser",
                next_hypothesis="normalize source timezone",
            )
        ],
    )
    committed = coordinator.commit(plan=plan, previous=previous, submitted=progress)
    assert committed.items[0].consecutive_stalled_cycles == 0

    repeated_run = progress.model_copy(
        update={
            "items": [
                progress.items[0].model_copy(
                    update={"latest_execution_id": "crawler_exec_same_blocker"}
                )
            ]
        }
    )
    repeated = coordinator.commit(plan=plan, previous=committed, submitted=repeated_run)
    assert repeated.items[0].progress_state is DeliveryProgressState.STALLED
    assert repeated.items[0].consecutive_stalled_cycles == 1

    current = committed
    for expected in range(1, 4):
        stalled = current.model_copy(
            update={
                "items": [
                    current.items[0].model_copy(
                        update={"progress_state": DeliveryProgressState.STALLED}
                    )
                ]
            }
        )
        current = coordinator.commit(plan=plan, previous=current, submitted=stalled)
        assert current.items[0].candidate_id == "primary"
        assert current.items[0].consecutive_stalled_cycles == expected

    fourth = current.model_copy(
        update={
            "items": [
                current.items[0].model_copy(
                    update={"progress_state": DeliveryProgressState.STALLED}
                )
            ]
        }
    )
    advanced = coordinator.commit(plan=plan, previous=current, submitted=fourth)
    assert advanced.items[0].candidate_id == "alternative"
    assert advanced.items[0].exhausted_candidate_ids == ["primary"]
    assert advanced.items[0].consecutive_stalled_cycles == 0

    infeasible = advanced.model_copy(
        update={
            "items": [
                advanced.items[0].model_copy(
                    update={"progress_state": DeliveryProgressState.INFEASIBLE}
                )
            ]
        }
    )
    exhausted = coordinator.commit(plan=plan, previous=advanced, submitted=infeasible)
    assert exhausted.items[0].delivery_stage == "ALL_CANDIDATES_EXHAUSTED"
    assert exhausted.items[0].exhausted_candidate_ids == ["primary", "alternative"]


def _operations_application(tmp_path: Path, node: CodexMonitoringO4Node):
    run_root = tmp_path / "monitoring-o4-mu-main"
    run_root.mkdir(parents=True, exist_ok=True)
    codec = O4OperationCapabilityCodec("z" * 40)
    claims = codec.verify(
        codec.issue(
            run_id=run_root.name,
            request_id=f"request-{node.value}",
            ticker="MU",
            node=node,
        ),
        public_key=codec.public_key,
    )
    return O4OperationsApplication(
        claims=claims,
        cwd=run_root,
        settings=DoxAgentSettings(
            DOXAGENT_MESSAGE_BUS_V2_ENABLED=True,
            DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH=str(tmp_path / "policy-bus.sqlite3"),
            DOXAGENT_CRAWLER_PLANE_ROOT=str(tmp_path / "policy-crawler"),
            DOXAGENT_CRAWLER_PLANE_SQLITE_PATH=str(tmp_path / "policy-crawler.sqlite3"),
        ),
    )


def test_o4_mutation_policy_canonicalizes_all_message_bus_paths_and_tikhub_alias_cap(
    tmp_path: Path,
) -> None:
    application = _operations_application(tmp_path, CodexMonitoringO4Node.CONFIGURE)
    registered = application.call(
        "monitoring_register_source",
        {
            "source_id": "custom_standard",
            "display_name": "Custom Standard",
            "kind": "api",
            "adapter_ref": "builtin:newswire_rss",
            "scheduler_group": "custom",
            "default_polling_config": {
                "target_interval_seconds": 3600,
                "alert_after_seconds": 7200,
            },
        },
    )
    assert registered["ok"] is True
    assert registered["output"]["default_polling_config"]["target_interval_seconds"] == 60
    assert registered["output"]["default_polling_config"]["alert_after_seconds"] == 1800

    updated = application.call(
        "monitoring_update_source",
        {
            "source_id": "custom_standard",
            "patch": {
                "default_polling_config": {
                    "target_interval_seconds": 999,
                    "alert_after_seconds": 999,
                }
            },
        },
    )
    assert updated["ok"] is True
    assert updated["output"]["default_polling_config"]["target_interval_seconds"] == 60

    tikhub = application.call(
        "monitoring_update_ticker_config",
        {
            "source_id": "tikhub_x_user_posts",
            "source_parameters": {"usernames": ["issuer", "competitor"]},
            "polling": {"target_interval_seconds": 3600},
        },
    )
    assert tikhub["ok"] is True
    assert tikhub["output"]["binding"]["polling"]["target_interval_seconds"] == 600
    assert tikhub["output"]["binding"]["polling"]["alert_after_seconds"] == 1800

    alias = application.call(
        "monitoring_register_source",
        {
            "source_id": "executive_posts",
            "display_name": "Executive Posts",
            "kind": "api",
            "adapter_ref": "builtin:tikhub_x_user_posts",
            "parameter_schema": {
                "type": "object",
                "properties": {
                    "usernames": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 2,
                    }
                },
                "required": ["usernames"],
                "additionalProperties": False,
            },
            "scheduler_group": "tikhub",
        },
    )
    assert alias["ok"] is True
    denied = application.call(
        "monitoring_update_ticker_config",
        {
            "source_id": "executive_posts",
            "source_parameters": {"usernames": ["third"]},
        },
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "o4_mutation_policy_denied"

    profile = application.call(
        "monitoring_update_default_profile",
        {
            "profile_id": "o4-test",
            "entries": [
                {
                    "source_id": "custom_standard",
                    "source_parameters": {},
                    "polling": {"target_interval_seconds": 3600},
                },
                {
                    "source_id": "tikhub_x_user_posts",
                    "source_parameters": {"usernames": ["issuer", "competitor"]},
                    "polling": {"target_interval_seconds": 3600},
                },
            ],
            "reason": "test O4 canonicalization",
        },
    )
    assert profile["ok"] is True
    intervals = {
        entry["source_id"]: entry["polling"]["target_interval_seconds"]
        for entry in profile["output"]["entries"]
    }
    assert intervals == {"custom_standard": 60, "tikhub_x_user_posts": 600}


def test_non_o4_message_bus_write_retains_generic_polling_configuration(
    tmp_path: Path,
) -> None:
    repository = MessageBusV2Repository(tmp_path / "generic-bus.sqlite3")
    bus = MessageBusV2Service(repository)
    bus.bootstrap()
    result = (
        MonitoringToolClient(service=bus)
        .for_tool("monitoring.update_ticker_config")
        .call(
            ToolRequest(
                tool_name="monitoring.update_ticker_config",
                ticker="MU",
                agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
                input={
                    "source_id": "benzinga_news",
                    "polling": {
                        "target_interval_seconds": 300,
                        "alert_after_seconds": 900,
                    },
                },
            )
        )
    )
    assert result.succeeded
    assert result.output["binding"]["polling"]["target_interval_seconds"] == 300
    assert result.output["binding"]["polling"]["alert_after_seconds"] == 900
    repository.close()


def test_o4_crawler_registration_and_promotion_policies_are_boundary_only(
    tmp_path: Path,
) -> None:
    class _Registry:
        def __init__(self, package_path: Path) -> None:
            self.package_path = package_path
            self.mutations: list[ToolRequest] = []

        def names(self):
            return {
                "crawler_plane.get",
                "crawler_plane.promote",
                "crawler_plane.register_source",
            }

        def call(self, request: ToolRequest, _permissions):
            if request.tool_name == "crawler_plane.get":
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.SUCCEEDED,
                    output={
                        "crawler": {"crawler_id": "issuer_ir"},
                        "versions": [
                            {
                                "spec": {"version": 1},
                                "status": "CERTIFIED",
                                "working_path": str(self.package_path),
                            }
                        ],
                    },
                )
            self.mutations.append(request)
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output=dict(request.input),
            )

    package_path = tmp_path / "working" / "issuer_ir" / "v1"
    cases_path = package_path / "tests" / "cases.json"
    cases_path.parent.mkdir(parents=True)
    cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "replay",
                    "observation_assertions": [{"external_id": "release-1"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    application = _operations_application(tmp_path, CodexMonitoringO4Node.DELIVER)
    registry = _Registry(package_path)
    application.registry = registry  # type: ignore[assignment]

    rejected = application.call("crawler_plane_promote", {"crawler_id": "issuer_ir", "version": 1})
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "o4_mutation_policy_denied"
    assert O4CrawlerPromotionPolicy.error_message in rejected["error"]["message"]

    cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": "replay",
                    "observation_assertions": [
                        {"external_id": "release-1", "body_contains": "Micron"}
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    promoted = application.call("crawler_plane_promote", {"crawler_id": "issuer_ir", "version": 1})
    assert promoted["ok"] is True

    configured = _operations_application(tmp_path / "registration", CodexMonitoringO4Node.CONFIGURE)
    registration_registry = _Registry(package_path)
    configured.registry = registration_registry  # type: ignore[assignment]
    registered = configured.call(
        "crawler_plane_register_source",
        {
            "source_id": "issuer_ir",
            "display_name": "Issuer IR",
            "crawler_id": "issuer_ir",
            "scheduler_group": "issuer.example",
            "default_polling_config": {"target_interval_seconds": 3600},
        },
    )
    assert registered["ok"] is True
    assert registered["output"]["default_polling_config"] == {
        "target_interval_seconds": 60,
        "alert_after_seconds": 1800,
    }


@pytest.mark.asyncio
async def test_interrupted_delivery_queues_continuation_without_blocking_message_bus(
    tmp_path: Path,
) -> None:
    class _InterruptedRunner:
        async def run(self, request: O4Request):
            raise O4TurnInterrupted("worker timeout", checkpoint_committed=True)

    repository = MonitoringO4Repository(tmp_path / "continuation-o4.sqlite3")
    bus_repository = MessageBusV2Repository(tmp_path / "continuation-bus.sqlite3")
    bus = MessageBusV2Service(bus_repository)
    bus.bootstrap()
    plan = _delivery_plan()
    repository.save_plan(plan)
    repository.save_delivery_checkpoint(
        DeliveryCheckpoint(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            ticker=plan.ticker,
            items=[
                DeliveryWorkItemCheckpoint(
                    source_need_id="need-1",
                    candidate_id="primary",
                    status=DeliveryItemStatus.IN_PROGRESS,
                    latest_execution_id="crawler_exec_partial",
                    latest_evidence_refs=["cassette_partial"],
                )
            ],
        )
    )
    request = repository.enqueue(
        O4Request(
            ticker="MU",
            node=CodexMonitoringO4Node.DELIVER,
            payload={"plan_json": plan.model_dump(mode="json")},
            dedupe_key="deliver-interrupted",
        )
    )
    orchestrator = MonitoringO4Orchestrator(
        repository=repository,
        runner=_InterruptedRunner(),  # type: ignore[arg-type]
        message_bus=bus,
        message_bus_enabled=True,
    )

    result = await orchestrator.process(request)

    assert result.request.status is O4RequestStatus.INTERRUPTED
    assert result.monitoring_started is True
    assert bus_repository.get_ticker_state("MU") is not None
    queued = repository.list_requests(status=O4RequestStatus.PENDING)
    assert len(queued) == 1
    assert queued[0].logical_request_id == request.logical_request_id
    assert queued[0].continuation_seq == 1
    checkpoint = repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
    assert checkpoint is not None
    assert checkpoint.items[0].status is DeliveryItemStatus.IN_PROGRESS
    assert repository.acquire_ticker_lease("MU", "after-interruption") is True
    repository.release_ticker_lease("MU", "after-interruption")
    repository.close()
    bus_repository.close()


@pytest.mark.asyncio
async def test_runner_commits_progressive_checkpoint_before_reporting_interruption(
    tmp_path: Path,
) -> None:
    plan = _delivery_plan()
    repository = MonitoringO4Repository(tmp_path / "runner-checkpoint.sqlite3")
    initial = DeliveryCheckpoint(
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        ticker=plan.ticker,
        items=[
            DeliveryWorkItemCheckpoint(
                source_need_id="need-1",
                candidate_id="primary",
                status=DeliveryItemStatus.IN_PROGRESS,
            )
        ],
    )
    repository.save_delivery_checkpoint(initial)
    workspace = _Workspace()

    class _InterruptingWorker:
        async def run(self, worker_request):
            checkpoint = initial.model_copy(
                update={
                    "items": [
                        initial.items[0].model_copy(
                            update={
                                "progress_state": DeliveryProgressState.PROGRESSING,
                                "latest_execution_id": "crawler_exec_new",
                                "latest_evidence_refs": ["cassette_new"],
                                "latest_blocker": "detail 403",
                                "next_hypothesis": "try browser transport",
                            }
                        )
                    ]
                }
            )
            workspace.files[
                (
                    worker_request.run_id,
                    f"requests/{worker_request.attempt_id}/delivery_checkpoint.json",
                )
            ] = checkpoint.model_dump_json(indent=2)
            return WorkerJob(
                job_id="job-interrupted",
                run_id=worker_request.run_id,
                attempt_id=worker_request.attempt_id,
                status="failed",
                thread_id="thread-mu",
                error_message="SDK timeout",
            )

        async def cancel(self, job_id: str):
            return None

    runner = MonitoringO4AgentRunner(
        worker=_InterruptingWorker(),  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        repository=repository,
    )
    request = O4Request(
        ticker="MU",
        node=CodexMonitoringO4Node.DELIVER,
        payload={"plan_json": plan.model_dump(mode="json")},
        dedupe_key="runner-interrupted",
    )

    with pytest.raises(O4TurnInterrupted) as captured:
        await runner.run(request)

    assert captured.value.checkpoint_committed is True
    committed = repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
    assert committed is not None
    assert committed.items[0].latest_execution_id == "crawler_exec_new"
    assert committed.items[0].consecutive_stalled_cycles == 0
    repository.close()
