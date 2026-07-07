from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from doxagent.agents.runtime.runner import ModelGatewayAgentRunner
from doxagent.gateway import (
    GatewayError,
    MessageRole,
    MockModelClient,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelUsage,
    ProviderName,
)
from doxagent.model_usage import (
    InMemoryModelUsageRepository,
    ModelPricingCatalog,
    ModelUsageCostService,
    ModelUsageEvent,
    ModelUsageRecorder,
)
from doxagent.model_usage.pricing import DEFAULT_PRICING_PATH
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentTask,
    RunMetadata,
    TaskType,
)


def _request() -> ModelRequest:
    return ModelRequest(
        provider=ProviderName.BAILIAN,
        model="qwen3.7-max",
        messages=[ModelMessage(role=MessageRole.USER, content="hello")],
        metadata={
            "ticker": "MU",
            "run_id": "run_usage_001",
            "workflow_node": "persistent_runtime_execution",
            "runtime_node": "W1",
            "agent_name": "W1",
            "task_type": "runtime_w1_novelty",
            "source_message_id": "std_mu_001",
        },
    )


@pytest.mark.asyncio
async def test_gateway_success_writes_model_usage_event() -> None:
    repository = InMemoryModelUsageRepository()
    gateway = ModelGateway(
        MockModelClient(usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120)),
        usage_recorder=ModelUsageRecorder(repository),
    )

    response = await gateway.complete(_request())

    assert response.succeeded
    events = repository.list_events(ticker="MU")
    assert len(events) == 1
    event = events[0]
    assert event.provider == "mock"
    assert event.model == "qwen3.7-max"
    assert event.status == "succeeded"
    assert event.input_tokens == 100
    assert event.output_tokens == 20
    assert event.total_tokens == 120
    assert event.ticker == "MU"
    assert event.run_id == "run_usage_001"
    assert event.workflow_node == "persistent_runtime_execution"
    assert event.runtime_node == "W1"
    assert event.source_message_id == "std_mu_001"


@pytest.mark.asyncio
async def test_gateway_recorder_failure_does_not_break_model_response() -> None:
    class FailingRecorder:
        def record_response(self, request: ModelRequest, response: object) -> None:
            raise RuntimeError("sqlite locked")

    gateway = ModelGateway(
        MockModelClient(usage=ModelUsage(input_tokens=1, output_tokens=2, total_tokens=3)),
        usage_recorder=FailingRecorder(),
    )

    response = await gateway.complete(_request())

    assert response.succeeded
    assert response.usage is not None
    assert response.usage.total_tokens == 3


@pytest.mark.asyncio
async def test_gateway_failed_response_is_recorded_without_usage() -> None:
    repository = InMemoryModelUsageRepository()
    gateway = ModelGateway(
        MockModelClient(
            failures=[
                GatewayError(
                    code="invalid_request",
                    message="bad request",
                    retryable=False,
                    provider=ProviderName.BAILIAN,
                )
            ]
        ),
        usage_recorder=ModelUsageRecorder(repository),
    )

    response = await gateway.complete(_request())

    assert response.error is not None
    event = repository.list_events(ticker="MU")[0]
    assert event.status == "failed"
    assert event.error_code == "invalid_request"
    assert event.total_tokens == 0


def test_agent_runner_metadata_includes_runtime_usage_dimensions() -> None:
    runner = ModelGatewayAgentRunner()
    task = AgentTask(
        task_id="task_usage_001",
        ticker="MU",
        agent_name=AgentName.W1_RUNTIME_NOVELTY,
        task_type=TaskType.RUNTIME_W1_NOVELTY,
        input_context={
            "source_message": {
                "source_message_id": "std_mu_runtime_001",
                "ticker": "MU",
            }
        },
        required_output_schema="W1Result",
        permissions=AgentPermissions(),
        run_metadata=RunMetadata(
            run_id="run_usage_runtime",
            ticker="MU",
            workflow_node="persistent_runtime_execution",
            created_at=datetime.now(UTC),
        ),
    )

    metadata = runner._metadata(task)

    assert metadata["ticker"] == "MU"
    assert metadata["runtime_node"] == "W1"
    assert metadata["source_message_id"] == "std_mu_runtime_001"
    assert metadata["workflow_node"] == "persistent_runtime_execution"


def test_bailian_pricing_applies_discount_and_cny_usd_rate() -> None:
    catalog = _default_catalog()
    event = ModelUsageEvent(
        provider="bailian",
        model="qwen3.7-max",
        status="succeeded",
        input_tokens=1_000_000,
        output_tokens=500_000,
        total_tokens=1_500_000,
        ticker="MU",
    )

    price = catalog.price(event)

    assert price is not None
    assert price.cost_cny == pytest.approx(13.5)
    assert price.cost_usd == pytest.approx(13.5 / 6.8)


def test_unknown_pricing_stays_partial_and_unestimated() -> None:
    repository = InMemoryModelUsageRepository(
        [
            ModelUsageEvent(
                provider="bailian",
                model="unknown-model",
                status="succeeded",
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                ticker="MU",
            )
        ]
    )
    service = ModelUsageCostService(repository, pricing_catalog=_default_catalog())

    records = service.cost_records(ticker="MU")

    assert len(records) == 1
    assert records[0]["cost_usd"] is None
    assert records[0]["pricing_status"] == "missing_price"


def test_model_usage_repository_period_filtering_and_details_shape() -> None:
    now = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    repository = InMemoryModelUsageRepository(
        [
            ModelUsageEvent(
                provider="bailian",
                model="qwen3.7-max",
                status="retried",
                input_tokens=1_000_000,
                output_tokens=500_000,
                total_tokens=1_500_000,
                retry_count=1,
                ticker="MU",
                runtime_node="O3",
                source_message_id="std_mu_today",
                created_at=now,
            ),
            ModelUsageEvent(
                provider="bailian",
                model="qwen3.7-max",
                status="succeeded",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                ticker="MU",
                runtime_node="W1",
                created_at=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
            ),
        ]
    )
    service = ModelUsageCostService(repository, pricing_catalog=_default_catalog())

    records = service.cost_records(
        ticker="MU",
        start_time=datetime(2026, 6, 24, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )

    assert len(records) == 1
    assert records[0]["node"] == "O3"
    assert records[0]["is_retry"] is True
    assert records[0]["cost_usd"] == pytest.approx(round(13.5 / 6.8, 6))
    assert records[0]["source_ref"]["source_message_id"] == "std_mu_today"


def _default_catalog() -> ModelPricingCatalog:
    config = json.loads(DEFAULT_PRICING_PATH.read_text(encoding="utf-8"))
    return ModelPricingCatalog(config, discount_rate=0.45, cny_usd_rate=6.8)
