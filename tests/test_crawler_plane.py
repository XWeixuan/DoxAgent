from __future__ import annotations

import asyncio
import json
import shutil
import stat
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.schema import (
    CheckStatus,
    CrawlerAlertStatus,
    CrawlerAlertType,
    CrawlerExecutionRequest,
    CrawlerExecutionStatus,
    CrawlerRetryStatus,
    CrawlerSourceRegistration,
    CrawlerVersionSpec,
    CrawlerVersionStatus,
    NetworkMode,
    new_id,
)
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.dashboard_api import create_app
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.models import AgentName, ResultStatus
from doxagent.persistent_runtime.repository import InMemoryPersistentRuntimeRepository
from doxagent.persistent_runtime.service import PersistentRuntimeExecutionService
from doxagent.runtime_scheduler.api import DashboardStateAPI
from doxagent.runtime_scheduler.repository import InMemoryRuntimeSchedulerRepository
from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.crawler_plane import CrawlerPlaneToolClient
from doxagent.tools.schema import ToolRequest


def _settings(tmp_path: Path) -> DoxAgentSettings:
    return DoxAgentSettings(
        message_bus_v2_enabled=True,
        message_bus_v2_sqlite_path=str(tmp_path / "message_bus.sqlite3"),
        message_bus_v2_adapter_root=str(tmp_path / "adapters"),
        message_bus_v2_content_enrichment_enabled=False,
        crawler_plane_root=str(tmp_path / "crawler-plane"),
        crawler_plane_sqlite_path=str(tmp_path / "crawler-plane" / "crawler_plane.sqlite3"),
        crawler_plane_worker_processes=4,
        crawler_plane_execution_timeout_seconds=15,
    )


def _service(
    settings: DoxAgentSettings,
    *,
    message_bus: MessageBusV2Service | None = None,
    seed_fixtures: bool = True,
) -> CrawlerPlaneService:
    service = build_crawler_plane_service(settings, message_bus=message_bus)
    if not seed_fixtures:
        return service
    fixtures = Path(__file__).parent / "fixtures" / "crawler_packages"
    specs = (
        CrawlerVersionSpec(
            crawler_id="company_ir_reference",
            version=1,
            parameter_schema={
                "type": "object",
                "properties": {
                    "listing_url": {"type": "string", "minLength": 1},
                    "source_name": {"type": "string", "minLength": 1},
                },
                "required": ["listing_url", "source_name"],
                "additionalProperties": False,
            },
        ),
        CrawlerVersionSpec(
            crawler_id="government_policy_reference",
            version=1,
            parameter_schema={
                "type": "object",
                "properties": {
                    "listing_url": {"type": "string", "minLength": 1},
                    "source_name": {"type": "string", "minLength": 1},
                },
                "required": ["listing_url", "source_name"],
                "additionalProperties": False,
            },
        ),
    )
    for spec in specs:
        version = service.create_version(spec)
        assert version.working_path is not None
        shutil.copytree(
            fixtures / spec.crawler_id,
            version.working_path,
            dirs_exist_ok=True,
        )
    return service


async def _install_company_live_transport(service: CrawlerPlaneService) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        body = (
            '<a data-id="A" data-published="2026-09-01T12:00:00Z" href="/news/a">Release A</a>'
            if request.url.path == "/news"
            else "<article>Release A body</article>"
        )
        return httpx.Response(200, text=body, request=request)

    await service.http_client.aclose()
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))


async def _activate_company(service: CrawlerPlaneService) -> None:
    await _install_company_live_transport(service)
    probe = await service.live_probe(
        "company_ir_reference",
        1,
        ticker="MU",
        parameters={
            "listing_url": "https://ir.example.test/news",
            "source_name": "Example IR",
        },
    )
    assert probe.status is CrawlerExecutionStatus.SUCCEEDED
    certification = await service.certify_version("company_ir_reference", 1)
    assert certification.overall is CheckStatus.PASS
    service.promote_version("company_ir_reference", 1)


class _GovernmentBrowser:
    async def get(self, url: str) -> tuple[int, str, dict[str, str], str]:
        return (
            200,
            url,
            {},
            '<a data-document-id="POL-A" data-revision="1" '
            'data-published="2026-09-01T12:00:00Z" '
            'href="/actions/a">Policy A</a>',
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_new_crawler_plane_registry_is_empty(tmp_path: Path) -> None:
    service = build_crawler_plane_service(_settings(tmp_path))
    try:
        assert service.list_crawlers() == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reference_certification_and_move_only_promotion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)
    try:
        working = service.get_version("company_ir_reference", 1)
        assert working.status is CrawlerVersionStatus.WORKING
        assert working.working_path is not None
        assert not (Path(working.working_path) / "manifest.json").exists()

        await _install_company_live_transport(service)
        probe = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert probe.status is CrawlerExecutionStatus.SUCCEEDED
        certification = await service.certify_version("company_ir_reference", 1)
        assert certification.overall is CheckStatus.PASS
        failure_replay = next(
            item for item in certification.checks if item.check.value == "failure_replay"
        )
        assert failure_replay.status.value == "NOT_APPLICABLE"
        assert certification.regression_count == 0
        service.browser = _GovernmentBrowser()  # type: ignore[assignment]
        government_probe = await service.live_probe(
            "government_policy_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://gov.example.test/actions",
                "source_name": "Example Government",
            },
        )
        assert government_probe.status is CrawlerExecutionStatus.SUCCEEDED
        government = await service.certify_version("government_policy_reference", 1)
        assert government.overall is CheckStatus.PASS
        concurrent = await asyncio.gather(
            *[
                service.execute(
                    CrawlerExecutionRequest(
                        crawler_id="company_ir_reference",
                        version=1,
                        ticker="MU",
                        source_id="cert.company_ir_reference",
                        binding_id=f"parallel:{index}",
                        source_parameters={
                            "listing_url": "https://ir.example.test/news",
                            "source_name": "Example IR",
                        },
                        poll_run_id=new_id("parallel_poll"),
                        network_mode=NetworkMode.REPLAY,
                        cassette_ref="tests/replay.json",
                        commit_checkpoint=False,
                    )
                )
                for index in range(8)
            ]
        )
        assert all(item.status is CrawlerExecutionStatus.SUCCEEDED for item in concurrent)

        promoted = service.promote_version("company_ir_reference", 1)
        assert promoted.status is CrawlerVersionStatus.ACTIVE
        assert promoted.working_path is None
        assert promoted.release_path is not None
        assert Path(promoted.release_path).is_dir()
        assert not Path(working.working_path).exists()
        release_entrypoint = Path(promoted.release_path) / "crawler.py"
        assert not release_entrypoint.stat().st_mode & stat.S_IWUSR
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_certification_rejects_observation_body_contamination(tmp_path: Path) -> None:
    service = _service(_settings(tmp_path))
    try:
        await _install_company_live_transport(service)
        working_path = Path(service.get_version("company_ir_reference", 1).working_path or "")
        cases_path = working_path / "tests" / "cases.json"
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        cases[0]["observation_assertions"][0]["body_forbidden_patterns"] = ["Release A body"]
        cases_path.write_text(json.dumps(cases), encoding="utf-8")
        probe = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert probe.status is CrawlerExecutionStatus.SUCCEEDED

        certification = await service.certify_version("company_ir_reference", 1)

        assert certification.overall is CheckStatus.FAIL
        replay = next(item for item in certification.checks if item.check.value == "replay")
        assert replay.diagnostic == "observation assertion failed: body_forbidden_patterns"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_certification_and_promotion_require_live_probe_of_current_digest(
    tmp_path: Path,
) -> None:
    service = _service(_settings(tmp_path))
    try:
        await _install_company_live_transport(service)
        probe = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert probe.status is CrawlerExecutionStatus.SUCCEEDED
        working_path = Path(service.get_version("company_ir_reference", 1).working_path or "")
        entrypoint = working_path / "crawler.py"
        entrypoint.write_text(
            entrypoint.read_text(encoding="utf-8") + "\n# digest-changing repair\n",
            encoding="utf-8",
        )
        certification = await service.certify_version("company_ir_reference", 1)
        assert certification.overall is CheckStatus.FAIL
        with pytest.raises(ValueError, match="only a CERTIFIED"):
            service.promote_version("company_ir_reference", 1)

        refreshed_probe = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert refreshed_probe.crawler_content_digest == certification.content_digest
        refreshed_certification = await service.certify_version("company_ir_reference", 1)
        assert refreshed_certification.overall is CheckStatus.PASS
        assert refreshed_certification.content_digest == refreshed_probe.crawler_content_digest
        assert (
            service.promote_version("company_ir_reference", 1).status is CrawlerVersionStatus.ACTIVE
        )
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_crawler_poll_lineage_and_checkpoint_ownership(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, message_bus = build_message_bus_v2_service(settings)
    crawler_plane = _service(settings, message_bus=message_bus)

    await _install_company_live_transport(crawler_plane)
    adapters = AdapterRegistry(
        settings,
        adapter_root=settings.message_bus_v2_adapter_root,
        crawler_plane=crawler_plane,
    )
    scheduler = GlobalPollScheduler(message_bus.repository, message_bus, adapters)
    crawler_execution_id = ""
    try:
        probe = await crawler_plane.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert probe.status is CrawlerExecutionStatus.SUCCEEDED
        certification = await crawler_plane.certify_version("company_ir_reference", 1)
        assert certification.overall is CheckStatus.PASS
        crawler_plane.promote_version("company_ir_reference", 1)
        source = crawler_plane.register_crawler_source(
            CrawlerSourceRegistration(
                source_id="example_ir",
                display_name="Example IR",
                crawler_id="company_ir_reference",
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "listing_url": {"type": "string"},
                        "source_name": {"type": "string"},
                    },
                    "required": ["listing_url", "source_name"],
                    "additionalProperties": False,
                },
                scheduler_group="example_ir",
            )
        )
        binding = message_bus.configure_binding(
            ticker="MU",
            source_id=source.source_id,
            source_parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
            actor=UpdateActor.AGENT,
        )

        execution = await scheduler._poll(source, binding, utc_now())

        assert execution.error_code is None
        assert execution.crawler_execution_id is not None
        crawler_execution_id = execution.crawler_execution_id
        assert execution.poll_run_id.startswith("poll_")
        assert message_bus.repository.get_poll_state(binding).checkpoint == {}
        crawler_result = crawler_plane.get_execution(execution.crawler_execution_id)
        assert crawler_result.checkpoint_after == {"seen_ids": ["A"]}
        assert crawler_result.message_bus_telemetry["poll_run_id"] == execution.poll_run_id
        raw = message_bus.repository.list_raw(ticker="MU")
        assert raw[0].metadata["crawler_execution_id"] == execution.crawler_execution_id
    finally:
        await adapters.close()
        await crawler_plane.close()
        message_bus.repository.close()

    _, reopened_bus = build_message_bus_v2_service(settings)
    reopened = _service(settings, message_bus=reopened_bus, seed_fixtures=False)
    try:
        assert reopened.get_crawler("company_ir_reference").active_version == 1
        assert (
            reopened.get_execution(crawler_execution_id).status is CrawlerExecutionStatus.SUCCEEDED
        )
        checkpoint = reopened.repository.get_checkpoint("company_ir_reference", "MU:example_ir")
        assert checkpoint is not None
        assert checkpoint.value == {"seen_ids": ["A"]}
    finally:
        await reopened.close()
        reopened_bus.repository.close()


@pytest.mark.asyncio
async def test_partial_poll_retries_items_and_maps_message_bus_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, message_bus = build_message_bus_v2_service(settings)
    crawler_plane = _service(settings, message_bus=message_bus)
    await _activate_company(crawler_plane)
    fail_b = True

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/news":
            body = (
                '<a data-id="A" data-published="2026-09-01T12:00:00Z" '
                'href="/news/a">Release A</a>'
                '<a data-id="B" data-published="2026-09-02T12:00:00Z" '
                'href="/news/b">Release B</a>'
                '<a data-id="C" data-published="2026-09-03T12:00:00Z" '
                'href="/news/c">Release C</a>'
            )
            return httpx.Response(200, text=body, request=request)
        if request.url.path == "/news/b" and fail_b:
            return httpx.Response(503, text="temporarily unavailable", request=request)
        return httpx.Response(
            200,
            text=f"<article>{request.url.path} body</article>",
            request=request,
        )

    await crawler_plane.http_client.aclose()
    crawler_plane.http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    adapters = AdapterRegistry(
        settings,
        adapter_root=settings.message_bus_v2_adapter_root,
        crawler_plane=crawler_plane,
    )
    scheduler = GlobalPollScheduler(message_bus.repository, message_bus, adapters)
    try:
        source = crawler_plane.register_crawler_source(
            CrawlerSourceRegistration(
                source_id="partial_ir",
                display_name="Partial IR",
                crawler_id="company_ir_reference",
                parameter_schema={
                    "type": "object",
                    "properties": {
                        "listing_url": {"type": "string"},
                        "source_name": {"type": "string"},
                    },
                    "required": ["listing_url", "source_name"],
                    "additionalProperties": False,
                },
                scheduler_group="partial_ir",
            )
        )
        binding = message_bus.configure_binding(
            ticker="MU",
            source_id=source.source_id,
            source_parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
            actor=UpdateActor.AGENT,
        )

        first_poll = await scheduler._poll(source, binding, utc_now())
        first_execution = crawler_plane.get_execution(first_poll.crawler_execution_id or "")
        assert first_execution.status is CrawlerExecutionStatus.PARTIAL
        assert [item.external_id for item in first_execution.observations] == ["A", "C"]
        assert [item.item_key for item in first_execution.item_failures] == ["B"]
        assert first_execution.diagnostics["quality_summary"]["observation_count"] == 2
        assert crawler_plane.repository.get_checkpoint(
            "company_ir_reference", binding.binding_id
        ).value == {"seen_ids": ["A", "B", "C"]}
        retry = crawler_plane.list_retries(binding_id=binding.binding_id)[0]
        assert retry.status is CrawlerRetryStatus.PENDING
        assert retry.attempt_count == 1
        assert message_bus.repository.get_poll_state(binding).status.value == "partial"
        failures = message_bus.repository.list_failures(ticker="MU")
        assert failures[0].original_payload["item_key"] == "B"

        fail_b = False
        second_poll = await scheduler._poll(source, binding, utc_now())
        second_execution = crawler_plane.get_execution(second_poll.crawler_execution_id or "")
        assert second_execution.status is CrawlerExecutionStatus.SUCCEEDED
        assert second_execution.completed_retry_keys == ["B"]
        resolved = crawler_plane.list_retries(binding_id=binding.binding_id)[0]
        assert resolved.status is CrawlerRetryStatus.RESOLVED
        assert message_bus.repository.get_poll_state(binding).status.value == "succeeded"
        item_alert = next(
            item
            for item in crawler_plane.list_alerts(crawler_id="company_ir_reference")
            if item.alert_type is CrawlerAlertType.ITEM_FAILURE
        )
        assert item_alert.status is CrawlerAlertStatus.RESOLVED

        crawler_plane.reactivate_retry(resolved.retry_id)
        fail_b = True
        for _ in range(3):
            await scheduler._poll(source, binding, utc_now())
        exhausted = crawler_plane.list_retries(binding_id=binding.binding_id)[0]
        assert exhausted.status is CrawlerRetryStatus.EXHAUSTED
        assert exhausted.attempt_count == 3
        repeated_failures = message_bus.repository.list_failures(ticker="MU")
        assert len(repeated_failures) == 1
        assert repeated_failures[0].repeat_count == 4
        exhausted_alert = next(
            item
            for item in crawler_plane.list_alerts(crawler_id="company_ir_reference")
            if item.alert_type is CrawlerAlertType.RETRY_EXHAUSTED
        )
        assert exhausted_alert.status is CrawlerAlertStatus.OPEN
        retry_tools = CrawlerPlaneToolClient(service=crawler_plane)
        listed = retry_tools.for_tool("crawler_plane.list_retries").call(
            ToolRequest(
                tool_name="crawler_plane.list_retries",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"binding_id": binding.binding_id, "status": "EXHAUSTED"},
            )
        )
        assert listed.output["retries"][0]["retry_id"] == exhausted.retry_id
        confirmed = retry_tools.for_tool("crawler_plane.resolve_retry").call(
            ToolRequest(
                tool_name="crawler_plane.resolve_retry",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"retry_id": exhausted.retry_id},
            )
        )
        assert confirmed.output["status"] == "RESOLVED"
        reactivated = retry_tools.for_tool("crawler_plane.reactivate_retry").call(
            ToolRequest(
                tool_name="crawler_plane.reactivate_retry",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"retry_id": exhausted.retry_id},
            )
        )
        assert reactivated.output["status"] == "PENDING"
    finally:
        await adapters.close()
        await crawler_plane.close()
        message_bus.repository.close()


@pytest.mark.asyncio
async def test_failure_persists_replay_cassette_and_failure_bundle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = _service(settings)

    async def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked", request=request)

    await service.http_client.aclose()
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    try:
        result = await service.live_probe(
            "company_ir_reference",
            1,
            ticker="MU",
            parameters={
                "listing_url": "https://ir.example.test/news",
                "source_name": "Example IR",
            },
        )
        assert result.status is CrawlerExecutionStatus.FAILED
        assert result.cassette_ref is not None
        cassette = service.repository.get_cassette(result.cassette_ref)
        assert cassette is not None
        assert cassette.exchanges[0].status_code == 403
        assert cassette.exchanges[0].response_body == "blocked"
        artifacts = service.get_execution_artifacts(result.execution_id)
        assert any(item.kind == "failure_bundle" for item in artifacts)
        regression = service.add_failure_to_regression(result.execution_id)
        assert regression.parameters["listing_url"] == "https://ir.example.test/news"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_o4_tools_use_the_crawler_plane_application_service(tmp_path: Path) -> None:
    service = _service(_settings(tmp_path))
    await _install_company_live_transport(service)
    tools = CrawlerPlaneToolClient(service=service)
    try:
        result = tools.for_tool("crawler_plane.get").call(
            ToolRequest(
                tool_name="crawler_plane.get",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference"},
            )
        )
        assert result.status is ResultStatus.SUCCEEDED
        assert result.output["crawler"]["crawler_id"] == "company_ir_reference"
        working_path = result.output["versions"][0]["working_path"]
        assert Path(working_path).parts[-2:] == ("company_ir_reference", "v1")
        probe = tools.for_tool("crawler_plane.live_probe").call(
            ToolRequest(
                tool_name="crawler_plane.live_probe",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={
                    "crawler_id": "company_ir_reference",
                    "version": 1,
                    "parameters": {
                        "listing_url": "https://ir.example.test/news",
                        "source_name": "Example IR",
                    },
                },
            )
        )
        assert probe.status is ResultStatus.SUCCEEDED
        certified = tools.for_tool("crawler_plane.certify").call(
            ToolRequest(
                tool_name="crawler_plane.certify",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference", "version": 1},
            )
        )
        assert certified.status is ResultStatus.SUCCEEDED
        assert certified.output["overall"] == "PASS"
        promoted = tools.for_tool("crawler_plane.promote").call(
            ToolRequest(
                tool_name="crawler_plane.promote",
                ticker="MU",
                agent_name=AgentName.O4_MARKET_TRACE,
                input={"crawler_id": "company_ir_reference", "version": 1},
            )
        )
        assert promoted.status is ResultStatus.SUCCEEDED
        assert promoted.output["status"] == "ACTIVE"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_human_api_uses_the_same_crawler_plane_service(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _, message_bus = build_message_bus_v2_service(settings)
    crawler_plane = _service(settings, message_bus=message_bus)
    legacy_runtime = PersistentRuntimeExecutionService.from_settings(settings)
    legacy_runtime.repository = InMemoryPersistentRuntimeRepository()
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        document_provider=cast(Any, object()),
        monitoring_service=None,
        runtime_service=legacy_runtime,
        message_bus_v2_service=message_bus,
        crawler_plane_service=crawler_plane,
        message_bus_v2_enabled=True,
    )
    try:
        client = TestClient(
            create_app(
                mode="real",
                auth_mode="mock-open",
                dashboard_api=DashboardStateAPI(scheduler),
            )
        )
        listed = client.get("/api/dashboard/v1/crawler-plane/crawlers")
        assert listed.status_code == 200
        assert len(listed.json()["data"]["crawlers"]) == 2
        fetched = client.get("/api/dashboard/v1/crawler-plane/crawlers/company_ir_reference")
        assert fetched.status_code == 200
        fetched_path = Path(fetched.json()["data"]["versions"][0]["working_path"])
        assert fetched_path.parts[-2:] == ("company_ir_reference", "v1")
        retries = client.get("/api/dashboard/v1/crawler-plane/retries")
        assert retries.status_code == 200
        assert retries.json()["data"]["retries"] == []
        invalid_status = client.get("/api/dashboard/v1/crawler-plane/retries?status=UNKNOWN")
        assert invalid_status.status_code == 422
    finally:
        await crawler_plane.close()
        message_bus.repository.close()
