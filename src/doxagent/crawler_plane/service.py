"""Crawler Plane application service and asset lifecycle."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from doxagent.crawler_plane.assets import CrawlerAssetStore
from doxagent.crawler_plane.health import CrawlerHealthService
from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.runtime import (
    CrawlerWorkerPool,
    ParentNetworkSession,
    PlaywrightBrowserRuntime,
    RequestPermitFactory,
    unlimited_request_permit,
)
from doxagent.crawler_plane.schema import (
    CertificationResult,
    CheckStatus,
    CrawlerAlert,
    CrawlerAlertPolicy,
    CrawlerCheckpoint,
    CrawlerExecutionRequest,
    CrawlerExecutionResult,
    CrawlerExecutionStatus,
    CrawlerPackage,
    CrawlerSourceRegistration,
    CrawlerVersion,
    CrawlerVersionSpec,
    CrawlerVersionStatus,
    ExecutionArtifact,
    NetworkCassette,
    NetworkMode,
    RegressionCase,
    WorkerJob,
    new_id,
    utc_now,
)

if TYPE_CHECKING:
    from doxagent.message_bus_v2.schema import SourceDefinition
    from doxagent.message_bus_v2.service import MessageBusV2Service


class CertificationService(Protocol):
    async def certify(self, crawler_id: str, version: int) -> CertificationResult: ...


class CrawlerPlaneService:
    def __init__(
        self,
        repository: CrawlerPlaneRepository,
        assets: CrawlerAssetStore,
        *,
        worker_pool: CrawlerWorkerPool,
        http_client: httpx.AsyncClient | None = None,
        browser: PlaywrightBrowserRuntime | None = None,
        execution_timeout_seconds: float = 120,
        max_response_bytes: int = 10_000_000,
        message_bus: MessageBusV2Service | None = None,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.worker_pool = worker_pool
        self.http_client = http_client or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._owns_http_client = http_client is None
        self.browser = browser or PlaywrightBrowserRuntime()
        self.execution_timeout_seconds = execution_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.message_bus = message_bus
        self.health = CrawlerHealthService(repository)
        self.certification_service: CertificationService | None = None

    def attach_certification_service(self, value: CertificationService) -> None:
        self.certification_service = value

    def list_crawlers(self) -> list[CrawlerPackage]:
        return self.repository.list_packages()

    def get_crawler(self, crawler_id: str) -> CrawlerPackage:
        value = self.repository.get_package(crawler_id)
        if value is None:
            raise KeyError(f"crawler not found: {crawler_id}")
        return value

    def create_version(
        self,
        spec: CrawlerVersionSpec,
        *,
        base_version: int | None = None,
    ) -> CrawlerVersion:
        package = self.repository.get_package(spec.crawler_id)
        if package is None:
            if spec.version != 1:
                raise ValueError("the first crawler version must be 1")
            package = CrawlerPackage(crawler_id=spec.crawler_id)
        elif spec.version != package.latest_version + 1:
            raise ValueError(f"next crawler version must be {package.latest_version + 1}")
        if self.repository.get_version(spec.crawler_id, spec.version) is not None:
            raise ValueError(f"crawler version already exists: {spec.crawler_id}@{spec.version}")
        base_path: Path | None = None
        if base_version is not None:
            base = self.require_version(spec.crawler_id, base_version)
            stored_path = base.release_path or base.working_path
            if stored_path is None:
                raise ValueError("base crawler version has no package path")
            base_path = Path(stored_path)
        working = self.assets.create_working(spec.crawler_id, spec.version, base=base_path)
        now = utc_now()
        value = CrawlerVersion(
            version_id=f"{spec.crawler_id}:v{spec.version}",
            spec=spec,
            working_path=str(working),
            created_at=now,
            updated_at=now,
        )
        self.repository.save_version(value)
        self.repository.save_package(
            package.model_copy(update={"latest_version": spec.version, "updated_at": now})
        )
        self.health.bootstrap_policies(spec.crawler_id)
        return value

    def require_version(self, crawler_id: str, version: int) -> CrawlerVersion:
        value = self.repository.get_version(crawler_id, version)
        if value is None:
            raise KeyError(f"crawler version not found: {crawler_id}@{version}")
        return value

    def get_version(self, crawler_id: str, version: int) -> CrawlerVersion:
        return self.require_version(crawler_id, version)

    async def certify_version(self, crawler_id: str, version: int) -> CertificationResult:
        if self.certification_service is None:
            raise RuntimeError("Crawler Certification Service is unavailable")
        result = await self.certification_service.certify(crawler_id, version)
        current = self.require_version(crawler_id, version)
        status = (
            CrawlerVersionStatus.CERTIFIED
            if result.overall is CheckStatus.PASS
            else CrawlerVersionStatus.WORKING
        )
        updated = current.model_copy(
            update={
                "status": status,
                "content_digest": result.content_digest,
                "certified_digest": result.content_digest
                if result.overall is CheckStatus.PASS
                else None,
                "certification_run_id": result.certification_run_id,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_version(updated)
        return result

    def get_certification_result(self, run_id: str) -> CertificationResult:
        value = self.repository.get_certification(run_id)
        if value is None:
            raise KeyError(f"certification run not found: {run_id}")
        return value

    def promote_version(
        self,
        crawler_id: str,
        version: int,
        *,
        checkpoint_action: str = "reject",
    ) -> CrawlerVersion:
        candidate = self.require_version(crawler_id, version)
        if candidate.status is not CrawlerVersionStatus.CERTIFIED:
            raise ValueError("only a CERTIFIED crawler version can be promoted")
        if candidate.working_path is None or candidate.certified_digest is None:
            raise ValueError("certified working copy is unavailable")
        digest = self.assets.digest(candidate.working_path)
        if digest != candidate.certified_digest:
            raise ValueError("working copy changed after certification")
        package = self.get_crawler(crawler_id)
        active = (
            self.require_version(crawler_id, package.active_version)
            if package.active_version is not None
            else None
        )
        if active is not None and (
            active.spec.checkpoint_schema_version != candidate.spec.checkpoint_schema_version
        ):
            if checkpoint_action != "reset":
                raise ValueError(
                    "checkpoint schema changed; promote with checkpoint_action='reset'"
                )
            self.repository.reset_checkpoints(crawler_id)
        release = self.assets.promote(crawler_id, version)
        now = utc_now()
        if active is not None:
            self.repository.save_version(
                active.model_copy(
                    update={"status": CrawlerVersionStatus.SUPERSEDED, "updated_at": now}
                )
            )
        promoted = candidate.model_copy(
            update={
                "status": CrawlerVersionStatus.ACTIVE,
                "working_path": None,
                "release_path": str(release),
                "content_digest": digest,
                "updated_at": now,
            }
        )
        self.repository.save_version(promoted)
        self.repository.save_package(
            package.model_copy(update={"active_version": version, "updated_at": now})
        )
        return promoted

    def rollback_version(
        self,
        crawler_id: str,
        version: int,
        *,
        checkpoint_action: str = "reject",
    ) -> CrawlerVersion:
        package = self.get_crawler(crawler_id)
        candidate = self.require_version(crawler_id, version)
        if candidate.release_path is None or not Path(candidate.release_path).is_dir():
            raise ValueError("rollback target is not an immutable release")
        active = (
            self.require_version(crawler_id, package.active_version)
            if package.active_version is not None
            else None
        )
        if active is not None and (
            active.spec.checkpoint_schema_version != candidate.spec.checkpoint_schema_version
        ):
            if checkpoint_action != "reset":
                raise ValueError(
                    "checkpoint schema changed; rollback with checkpoint_action='reset'"
                )
            self.repository.reset_checkpoints(crawler_id)
        now = utc_now()
        if active is not None and active.spec.version != candidate.spec.version:
            self.repository.save_version(
                active.model_copy(
                    update={"status": CrawlerVersionStatus.SUPERSEDED, "updated_at": now}
                )
            )
        restored = candidate.model_copy(
            update={"status": CrawlerVersionStatus.ACTIVE, "updated_at": now}
        )
        self.repository.save_version(restored)
        self.repository.save_package(
            package.model_copy(update={"active_version": version, "updated_at": now})
        )
        return restored

    async def execute(
        self,
        request: CrawlerExecutionRequest,
        *,
        request_permit: RequestPermitFactory = unlimited_request_permit,
    ) -> CrawlerExecutionResult:
        if request.checkpoint_override is not None and request.commit_checkpoint:
            raise ValueError("checkpoint_override requires commit_checkpoint=false")
        package = self.get_crawler(request.crawler_id)
        version_number = request.version or package.active_version
        if version_number is None:
            raise ValueError(f"crawler has no ACTIVE release: {request.crawler_id}")
        version = self.require_version(request.crawler_id, version_number)
        package_path = (
            version.release_path
            if request.version is None
            else (version.working_path or version.release_path)
        )
        if package_path is None:
            raise ValueError("crawler version has no executable package path")
        if request.version is None:
            if version.status is not CrawlerVersionStatus.ACTIVE:
                raise ValueError("production execution requires an ACTIVE crawler release")
            actual_digest = self.assets.digest(package_path)
            if actual_digest != version.content_digest:
                raise RuntimeError("immutable crawler release digest mismatch")
        from doxagent.message_bus_v2.schema import validate_parameter_schema

        validate_parameter_schema(version.spec.parameter_schema, request.source_parameters)
        persisted_checkpoint = self.repository.get_checkpoint(
            request.crawler_id, request.binding_id
        )
        if request.checkpoint_override is not None:
            checkpoint_before = dict(request.checkpoint_override)
        elif persisted_checkpoint is None:
            checkpoint_before = {}
        else:
            if persisted_checkpoint.schema_version != version.spec.checkpoint_schema_version:
                raise ValueError("crawler checkpoint schema is incompatible with active version")
            checkpoint_before = dict(persisted_checkpoint.value)
        execution_id = new_id("crawler_exec")
        started = utc_now()
        running = CrawlerExecutionResult(
            execution_id=execution_id,
            poll_run_id=request.poll_run_id,
            crawler_id=request.crawler_id,
            crawler_version=version_number,
            source_id=request.source_id,
            binding_id=request.binding_id,
            ticker=request.ticker,
            source_parameters=request.source_parameters,
            status=CrawlerExecutionStatus.RUNNING,
            checkpoint_before=checkpoint_before,
            started_at=started,
        )
        self.repository.save_execution(running)
        replay = (
            self._load_cassette(request.cassette_ref, package_path)
            if request.cassette_ref
            else None
        )
        session = ParentNetworkSession(
            execution_id=execution_id,
            crawler_id=request.crawler_id,
            crawler_version=version_number,
            mode=request.network_mode,
            asset_store=self.assets,
            request_permit=request_permit,
            client=self.http_client,
            browser=self.browser,
            replay=replay,
            max_response_bytes=self.max_response_bytes,
        )
        job = WorkerJob(
            job_id=new_id("crawler_job"),
            execution_id=execution_id,
            package_path=package_path,
            entrypoint=version.spec.entrypoint,
            ticker=request.ticker,
            parameters=request.source_parameters,
            checkpoint=checkpoint_before,
        )
        start_clock = time.monotonic()
        try:
            child = await self.worker_pool.submit(
                job, session, timeout_seconds=self.execution_timeout_seconds
            )
            if not child.ok:
                raise RuntimeError(f"{child.error_code}: {child.error_message}")
            from doxagent.crawler_plane.schema import CrawlerRunOutput

            output = CrawlerRunOutput.model_validate(child.output)
            finished = utc_now()
            result = running.model_copy(
                update={
                    "status": CrawlerExecutionStatus.SUCCEEDED,
                    "observations": output.observations,
                    "diagnostics": {
                        **output.diagnostics,
                        **self._transport_diagnostics(session),
                    },
                    "checkpoint_after": output.next_checkpoint,
                    "finished_at": finished,
                    "latency_ms": max(0, int((time.monotonic() - start_clock) * 1000)),
                    "request_count": len(session.exchanges),
                    "response_bytes": session.response_bytes,
                }
            )
            if request.commit_checkpoint:
                self.repository.save_checkpoint(
                    CrawlerCheckpoint(
                        crawler_id=request.crawler_id,
                        binding_id=request.binding_id,
                        schema_version=version.spec.checkpoint_schema_version,
                        value=output.next_checkpoint,
                    )
                )
            if request.network_mode is NetworkMode.RECORD:
                result = self._persist_success_lineage(
                    result,
                    session,
                    preserve_bodies=request.preserve_response_bodies,
                )
            else:
                result = result.model_copy(
                    update={
                        "cassette_ref": request.cassette_ref,
                        "artifact_refs": [item.artifact_id for item in session.artifacts],
                    }
                )
        except TimeoutError as exc:
            result = self._failed_result(
                running,
                session,
                start_clock,
                CrawlerExecutionStatus.TIMED_OUT,
                "execution_timeout",
                str(exc) or "crawler execution timed out",
                request,
            )
        except Exception as exc:
            result = self._failed_result(
                running,
                session,
                start_clock,
                CrawlerExecutionStatus.FAILED,
                type(exc).__name__,
                str(exc),
                request,
            )
        self.repository.save_execution(result)
        for artifact in session.artifacts:
            self.repository.save_artifact(artifact)
        self.health.evaluate(result)
        return result

    async def live_probe(
        self,
        crawler_id: str,
        version: int,
        *,
        ticker: str,
        parameters: dict[str, Any],
        baseline_cassette_ref: str | None = None,
    ) -> CrawlerExecutionResult:
        result = await self.execute(
            CrawlerExecutionRequest(
                crawler_id=crawler_id,
                version=version,
                ticker=ticker,
                source_id=f"probe.{crawler_id}",
                binding_id=f"probe:{crawler_id}",
                source_parameters=parameters,
                poll_run_id=new_id("live_probe"),
                commit_checkpoint=False,
                preserve_response_bodies=True,
            )
        )
        baseline_match: bool | None = None
        if baseline_cassette_ref is not None:
            crawler_version = self.require_version(crawler_id, version)
            package_path = crawler_version.working_path or crawler_version.release_path
            if package_path is None:
                raise ValueError("crawler version has no package path")
            baseline = self._load_cassette(baseline_cassette_ref, package_path)
            recorded = (
                self.repository.get_cassette(result.cassette_ref)
                if result.cassette_ref is not None
                else None
            )
            if baseline is None or recorded is None:
                raise ValueError("live probe cassette comparison is unavailable")
            baseline_shape = [
                (item.transport, item.method, item.request_url, item.status_code // 100)
                for item in baseline.exchanges
            ]
            recorded_shape = [
                (item.transport, item.method, item.request_url, item.status_code // 100)
                for item in recorded.exchanges
            ]
            baseline_match = baseline_shape == recorded_shape
        updated = result.model_copy(
            update={
                "diagnostics": {
                    **result.diagnostics,
                    "live_probe": {
                        "connected": result.request_count > 0,
                        "produced_observations": bool(result.observations),
                        "baseline_cassette_match": baseline_match,
                    },
                }
            }
        )
        self.repository.save_execution(updated)
        return updated

    def get_execution(self, execution_id: str) -> CrawlerExecutionResult:
        value = self.repository.get_execution(execution_id)
        if value is None:
            raise KeyError(f"crawler execution not found: {execution_id}")
        return value

    def get_execution_artifacts(self, execution_id: str) -> list[ExecutionArtifact]:
        self.get_execution(execution_id)
        return self.repository.list_artifacts(execution_id)

    def get_alert_policy(self, policy_key: str) -> CrawlerAlertPolicy:
        value = self.repository.get_alert_policy(policy_key)
        if value is None:
            raise KeyError(f"crawler alert policy not found: {policy_key}")
        return value

    def update_alert_policy(self, policy: CrawlerAlertPolicy) -> CrawlerAlertPolicy:
        self.get_crawler(policy.crawler_id)
        self.repository.save_alert_policy(policy.model_copy(update={"updated_at": utc_now()}))
        return self.get_alert_policy(policy.policy_key)

    def list_alerts(
        self, *, crawler_id: str | None = None, open_only: bool = False
    ) -> list[CrawlerAlert]:
        return self.repository.list_alerts(crawler_id=crawler_id, open_only=open_only)

    def get_alert(self, alert_id: str) -> CrawlerAlert:
        value = self.repository.get_alert(alert_id)
        if value is None:
            raise KeyError(f"crawler alert not found: {alert_id}")
        return value

    def resolve_alert(self, alert_id: str) -> CrawlerAlert:
        value = self.repository.resolve_alert(alert_id)
        if value is None:
            raise KeyError(f"crawler alert not found: {alert_id}")
        return value

    def register_crawler_source(self, value: CrawlerSourceRegistration) -> SourceDefinition:
        if self.message_bus is None:
            raise RuntimeError("Message Bus v2 application service is unavailable")
        package = self.get_crawler(value.crawler_id)
        if package.active_version is None:
            raise ValueError("crawler source registration requires an ACTIVE release")
        from doxagent.message_bus_v2.schema import (
            PollingConfig,
            SchedulerConstraints,
            SourceDefinition,
            SourceKind,
            StreamingConfig,
            UpdateActor,
        )

        source = SourceDefinition(
            source_id=value.source_id,
            display_name=value.display_name,
            kind=SourceKind.CRAWLER,
            adapter_ref=f"crawler:{value.crawler_id}",
            parameter_schema=value.parameter_schema,
            default_parameters=value.default_parameters,
            default_polling_config=PollingConfig.model_validate(value.default_polling_config),
            default_streaming_config=StreamingConfig.model_validate(value.default_streaming_config),
            scheduler_group=value.scheduler_group,
            scheduler_constraints=SchedulerConstraints.model_validate(value.scheduler_constraints),
            updated_by=UpdateActor.AGENT,
            updated_reason=f"Crawler Plane active {value.crawler_id}@v{package.active_version}",
        )
        current = self.message_bus.repository.get_source(source.source_id)
        if current is None:
            return self.message_bus.register_source(source)
        patch = source.model_dump(
            exclude={
                "source_id",
                "version",
                "created_at",
                "updated_at",
                "updated_by",
                "updated_reason",
            }
        )
        return self.message_bus.update_source(
            source.source_id,
            patch,
            actor=UpdateActor.AGENT,
            reason=source.updated_reason,
        )

    def record_message_bus_telemetry(self, execution_id: str, telemetry: dict[str, Any]) -> None:
        current = self.get_execution(execution_id)
        updated = current.model_copy(update={"message_bus_telemetry": telemetry})
        self.repository.save_execution(updated)
        self.health.evaluate(updated)

    def add_failure_to_regression(self, execution_id: str) -> RegressionCase:
        execution = self.get_execution(execution_id)
        if execution.status is CrawlerExecutionStatus.SUCCEEDED or not execution.cassette_ref:
            raise ValueError("only a failed execution with a cassette can become a regression case")
        value = RegressionCase(
            crawler_id=execution.crawler_id,
            source_execution_id=execution.execution_id,
            cassette_ref=execution.cassette_ref,
            checkpoint=execution.checkpoint_before,
            parameters=execution.source_parameters,
        )
        self.repository.save_regression(value)
        return value

    async def close(self) -> None:
        await self.worker_pool.close()
        await self.browser.close()
        if self._owns_http_client:
            await self.http_client.aclose()
        self.repository.close()

    def _load_cassette(self, ref: str | None, package_path: str) -> NetworkCassette | None:
        if ref is None:
            return None
        persisted = self.repository.get_cassette(ref)
        if persisted is not None:
            return persisted
        candidate = (Path(package_path) / ref).resolve()
        return self.assets.load_cassette(str(candidate))

    def _persist_success_lineage(
        self,
        result: CrawlerExecutionResult,
        session: ParentNetworkSession,
        *,
        preserve_bodies: bool,
    ) -> CrawlerExecutionResult:
        cassette = session.cassette()
        if not preserve_bodies:
            cassette = cassette.model_copy(
                update={
                    "exchanges": [
                        item.model_copy(update={"response_body": None})
                        for item in cassette.exchanges
                    ]
                }
            )
        saved = self.assets.save_cassette(cassette)
        self.repository.save_cassette(saved)
        return result.model_copy(
            update={
                "cassette_ref": saved.cassette_id,
                "artifact_refs": [item.artifact_id for item in session.artifacts],
            }
        )

    def _failed_result(
        self,
        running: CrawlerExecutionResult,
        session: ParentNetworkSession,
        start_clock: float,
        status: CrawlerExecutionStatus,
        code: str,
        message: str,
        request: CrawlerExecutionRequest,
    ) -> CrawlerExecutionResult:
        cassette = self.assets.save_cassette(session.cassette())
        self.repository.save_cassette(cassette)
        bundle = {
            "execution": running.model_dump(mode="json"),
            "source_parameters": request.source_parameters,
            "checkpoint": running.checkpoint_before,
            "cassette_ref": cassette.cassette_id,
            "error_code": code,
            "error_message": message[:2000],
        }
        artifact = self.assets.save_artifact(
            running.execution_id,
            "failure_bundle",
            "failure_bundle.json",
            json.dumps(bundle, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"),
        )
        session.artifacts.append(artifact)
        return running.model_copy(
            update={
                "status": status,
                "diagnostics": self._transport_diagnostics(session),
                "finished_at": utc_now(),
                "latency_ms": max(0, int((time.monotonic() - start_clock) * 1000)),
                "request_count": len(session.exchanges),
                "response_bytes": session.response_bytes,
                "error_code": code,
                "error_message": message[:2000],
                "cassette_ref": cassette.cassette_id,
                "artifact_refs": [item.artifact_id for item in session.artifacts],
                "checkpoint_after": running.checkpoint_before,
            }
        )

    @staticmethod
    def _transport_diagnostics(session: ParentNetworkSession) -> dict[str, Any]:
        failures = [item.status_code for item in session.exchanges if item.status_code >= 400]
        return {
            "transport_failure_count": len(failures),
            "transport_status_codes": failures,
        }


__all__ = ["CrawlerPlaneService"]
