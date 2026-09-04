"""Deterministic replay-based crawler certification."""

from __future__ import annotations

import json
from pathlib import Path

from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.schema import (
    CertificationCase,
    CertificationCheck,
    CertificationCheckResult,
    CertificationResult,
    CheckStatus,
    CrawlerExecutionRequest,
    CrawlerExecutionResult,
    CrawlerExecutionStatus,
    CrawlerVersion,
    NetworkMode,
    new_id,
    utc_now,
)
from doxagent.crawler_plane.service import CrawlerPlaneService


class CrawlerCertificationService:
    def __init__(self, service: CrawlerPlaneService, repository: CrawlerPlaneRepository) -> None:
        self.service = service
        self.repository = repository

    async def certify(self, crawler_id: str, version: int) -> CertificationResult:
        started = utc_now()
        value = self.service.require_version(crawler_id, version)
        package_path = value.working_path
        checks: list[CertificationCheckResult] = []
        digest = value.content_digest or "unavailable"
        cases: list[CertificationCase] = []
        try:
            if value.status.value not in {"WORKING", "CERTIFIED"} or package_path is None:
                raise ValueError("certification requires a working crawler version")
            digest = self.service.assets.digest(package_path)
            relative, _ = value.spec.entrypoint.rsplit(":", 1)
            if not (Path(package_path) / relative).is_file():
                raise ValueError(f"entrypoint file not found: {relative}")
            cases_path = Path(package_path) / "tests" / "cases.json"
            payload = json.loads(cases_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("tests/cases.json must contain an array")
            cases = [CertificationCase.model_validate(item) for item in payload]
            required_kinds = {
                "replay",
                "temporal",
                "synthetic",
                "malformed",
                "duplicate_revision",
            }
            present_kinds = {item.kind for item in cases}
            if not ({"partial", "failure"} & present_kinds):
                raise ValueError("certification requires a partial or failure case")
            missing = required_kinds - present_kinds
            if missing:
                raise ValueError(
                    f"certification cases missing required kind(s): {', '.join(sorted(missing))}"
                )
            live_replay = next(
                (item for item in cases if item.kind == "replay" and item.live_derived),
                None,
            )
            if live_replay is None:
                raise ValueError("certification requires a live-derived replay cassette")
            if live_replay.cassette_refs[0] != "$live_probe":
                raise ValueError("live-derived replay must use the $live_probe cassette")
            self._live_probe_cassette_ref(value, digest)
            checks.append(self._pass(CertificationCheck.CONTRACT))
        except Exception as exc:
            checks.append(self._fail(CertificationCheck.CONTRACT, str(exc)))

        if checks[0].status is CheckStatus.PASS:
            checks.append(
                await self._simple_kind(value, cases, "replay", CertificationCheck.REPLAY)
            )
            checks.append(await self._temporal(value, cases))
            checks.append(
                await self._simple_kind(
                    value, cases, "synthetic", CertificationCheck.SYNTHETIC_INCREMENT
                )
            )
            checks.append(await self._package_failures(value, cases))
            checks.append(await self._determinism(value, cases))
            failure_replay, regression_count = await self._failure_replay(value)
            checks.append(failure_replay)
        else:
            regression_count = len(self.repository.list_regressions(crawler_id))
            for check in (
                CertificationCheck.REPLAY,
                CertificationCheck.TEMPORAL_REPLAY,
                CertificationCheck.SYNTHETIC_INCREMENT,
                CertificationCheck.PACKAGE_FAILURES,
                CertificationCheck.DETERMINISM,
                CertificationCheck.FAILURE_REPLAY,
            ):
                checks.append(self._fail(check, "contract validation failed"))
        overall = (
            CheckStatus.PASS
            if all(item.status is not CheckStatus.FAIL for item in checks)
            else CheckStatus.FAIL
        )
        result = CertificationResult(
            crawler_id=crawler_id,
            crawler_version=version,
            content_digest=digest,
            overall=overall,
            checks=checks,
            regression_count=regression_count,
            started_at=started,
            finished_at=utc_now(),
        )
        self.repository.save_certification(result)
        return result

    async def _simple_kind(
        self,
        version: CrawlerVersion,
        cases: list[CertificationCase],
        kind: str,
        check: CertificationCheck,
    ) -> CertificationCheckResult:
        selected = [item for item in cases if item.kind == kind]
        if not selected:
            return self._fail(check, f"no {kind} certification case")
        for case in selected:
            result = await self._execute_case(
                version, case, case.cassette_refs[0], case.initial_checkpoint
            )
            failure = self._case_failure(check, case, result)
            if failure is not None:
                return failure
        return self._pass(check)

    async def _temporal(
        self, version: CrawlerVersion, cases: list[CertificationCase]
    ) -> CertificationCheckResult:
        selected = [item for item in cases if item.kind == "temporal"]
        if not selected:
            return self._fail(CertificationCheck.TEMPORAL_REPLAY, "no temporal case")
        for case in selected:
            if len(case.cassette_refs) != 2:
                return self._fail(
                    CertificationCheck.TEMPORAL_REPLAY,
                    "temporal case requires exactly T0 and T1 cassettes",
                    case,
                )
            first = await self._execute_case(
                version, case, case.cassette_refs[0], case.initial_checkpoint
            )
            if first.status is not CrawlerExecutionStatus.SUCCEEDED:
                return self._fail(
                    CertificationCheck.TEMPORAL_REPLAY,
                    first.error_message or "T0 failed",
                    case,
                    first,
                )
            second = await self._execute_case(
                version, case, case.cassette_refs[1], first.checkpoint_after
            )
            failure = self._case_failure(
                CertificationCheck.TEMPORAL_REPLAY,
                case,
                second,
                cassette_ref=case.cassette_refs[1],
            )
            if failure is not None:
                return failure
            repeated = await self._execute_case(
                version, case, case.cassette_refs[1], second.checkpoint_after
            )
            if repeated.status is not CrawlerExecutionStatus.SUCCEEDED or repeated.observations:
                return self._fail(
                    CertificationCheck.TEMPORAL_REPLAY,
                    "T1 emitted observations again after checkpoint advancement",
                    case,
                    repeated,
                )
        return self._pass(CertificationCheck.TEMPORAL_REPLAY)

    async def _package_failures(
        self, version: CrawlerVersion, cases: list[CertificationCase]
    ) -> CertificationCheckResult:
        for kind in ("partial", "failure", "malformed", "duplicate_revision"):
            selected = [item for item in cases if item.kind == kind]
            if kind in {"partial", "failure"} and not selected:
                continue
            if not selected:
                return self._fail(
                    CertificationCheck.PACKAGE_FAILURES,
                    f"no {kind} certification case",
                )
            for case in selected:
                result = await self._execute_case(
                    version, case, case.cassette_refs[0], case.initial_checkpoint
                )
                failure = self._case_failure(
                    CertificationCheck.PACKAGE_FAILURES, case, result
                )
                if failure is not None:
                    return failure
        return self._pass(CertificationCheck.PACKAGE_FAILURES)

    async def _determinism(
        self, version: CrawlerVersion, cases: list[CertificationCase]
    ) -> CertificationCheckResult:
        selected = next((item for item in cases if item.kind in {"replay", "synthetic"}), None)
        if selected is None:
            return self._fail(CertificationCheck.DETERMINISM, "no replayable case")
        first = await self._execute_case(
            version, selected, selected.cassette_refs[0], selected.initial_checkpoint
        )
        second = await self._execute_case(
            version, selected, selected.cassette_refs[0], selected.initial_checkpoint
        )
        first_projection = {
            "observations": [item.model_dump(mode="json") for item in first.observations],
            "checkpoint": first.checkpoint_after,
        }
        second_projection = {
            "observations": [item.model_dump(mode="json") for item in second.observations],
            "checkpoint": second.checkpoint_after,
        }
        if (
            first.status is not CrawlerExecutionStatus.SUCCEEDED
            or first_projection != second_projection
        ):
            return CertificationCheckResult(
                check=CertificationCheck.DETERMINISM,
                status=CheckStatus.FAIL,
                test_case=selected.case_id,
                expected=first_projection,
                actual=second_projection,
                cassette_ref=selected.cassette_refs[0],
                diagnostic="identical replay inputs produced different outputs",
            )
        return self._pass(CertificationCheck.DETERMINISM)

    async def _failure_replay(
        self, version: CrawlerVersion
    ) -> tuple[CertificationCheckResult, int]:
        crawler_id = version.spec.crawler_id
        cases = self.repository.list_regressions(crawler_id)
        if not cases:
            return (
                CertificationCheckResult(
                    check=CertificationCheck.FAILURE_REPLAY,
                    status=CheckStatus.NOT_APPLICABLE,
                    actual={"regression_count": 0},
                    diagnostic="no retained regression case",
                ),
                0,
            )
        for case in cases:
            request = CrawlerExecutionRequest(
                crawler_id=crawler_id,
                version=version.spec.version,
                ticker="CERT",
                source_id=f"cert.{crawler_id}",
                binding_id=f"cert:{case.regression_id}",
                source_parameters=case.parameters,
                poll_run_id=new_id("cert_poll"),
                network_mode=NetworkMode.REPLAY,
                cassette_ref=case.cassette_ref,
                commit_checkpoint=False,
                checkpoint_override=case.checkpoint,
            )
            result = await self.service.execute(request)
            if result.status is not CrawlerExecutionStatus.SUCCEEDED:
                return (
                    self._fail(
                        CertificationCheck.FAILURE_REPLAY,
                        result.error_message or "historical failure still reproduces",
                        result=result,
                    ),
                    len(cases),
                )
        return self._pass(CertificationCheck.FAILURE_REPLAY), len(cases)

    def _case_failure(
        self,
        check: CertificationCheck,
        case: CertificationCase,
        result: CrawlerExecutionResult,
        *,
        cassette_ref: str | None = None,
    ) -> CertificationCheckResult | None:
        actual_ids = [item.external_id or item.url for item in result.observations]
        actual_failure_keys = [item.item_key for item in result.item_failures]
        expected = {
            "status": case.expected_status.value,
            "external_ids": case.expected_external_ids,
            "item_failure_keys": case.expected_item_failure_keys,
            "retry_keys": case.expected_retry_keys,
            "checkpoint": case.expected_checkpoint,
        }
        actual = {
            "status": result.status.value,
            "external_ids": actual_ids,
            "item_failure_keys": actual_failure_keys,
            "retry_keys": result.retry_keys,
            "checkpoint": result.checkpoint_after,
        }
        mismatch = (
            result.status is not case.expected_status
            or actual_ids != case.expected_external_ids
            or actual_failure_keys != case.expected_item_failure_keys
            or result.retry_keys != case.expected_retry_keys
            or (
                case.expected_checkpoint is not None
                and result.checkpoint_after != case.expected_checkpoint
            )
        )
        assertion_error = self._observation_assertion_error(case, result)
        if mismatch or assertion_error is not None:
            return CertificationCheckResult(
                check=check,
                status=CheckStatus.FAIL,
                test_case=case.case_id,
                expected=expected,
                actual=actual,
                cassette_ref=cassette_ref or case.cassette_refs[0],
                diagnostic=assertion_error or "crawler result differs from case expectations",
            )
        return None

    @staticmethod
    def _observation_assertion_error(
        case: CertificationCase, result: CrawlerExecutionResult
    ) -> str | None:
        for assertion in case.observation_assertions:
            candidates = [
                item
                for item in result.observations
                if assertion.external_id is None or item.external_id == assertion.external_id
            ]
            if not candidates:
                return f"observation assertion target missing: {assertion.external_id or '*'}"
            item = candidates[0]
            checks = (
                (
                    assertion.title_contains is None
                    or assertion.title_contains in (item.title or ""),
                    "title_contains",
                ),
                (
                    assertion.url_prefix is None or item.url.startswith(assertion.url_prefix),
                    "url_prefix",
                ),
                (
                    assertion.published_at is None
                    or item.published_at == assertion.published_at,
                    "published_at",
                ),
                (
                    assertion.body_contains is None
                    or assertion.body_contains in item.body,
                    "body_contains",
                ),
                (
                    assertion.body_min_length is None
                    or len(item.body) >= assertion.body_min_length,
                    "body_min_length",
                ),
                (
                    not any(
                        pattern.lower() in item.body.lower()
                        for pattern in assertion.body_forbidden_patterns
                    ),
                    "body_forbidden_patterns",
                ),
            )
            for passed, label in checks:
                if not passed:
                    return f"observation assertion failed: {label}"
        return None

    async def _execute_case(
        self,
        version: CrawlerVersion,
        case: CertificationCase,
        cassette_ref: str,
        checkpoint: dict[str, object],
    ) -> CrawlerExecutionResult:
        spec = version.spec
        resolved_cassette_ref = (
            self._live_probe_cassette_ref(version, self.service.assets.digest(version.working_path))
            if cassette_ref == "$live_probe" and version.working_path is not None
            else cassette_ref
        )
        return await self.service.execute(
            CrawlerExecutionRequest(
                crawler_id=spec.crawler_id,
                version=spec.version,
                ticker="CERT",
                source_id=f"cert.{spec.crawler_id}",
                binding_id=f"cert:{case.case_id}",
                source_parameters=case.parameters,
                poll_run_id=new_id("cert_poll"),
                network_mode=NetworkMode.REPLAY,
                cassette_ref=resolved_cassette_ref,
                commit_checkpoint=False,
                checkpoint_override=checkpoint,
            )
        )

    def _live_probe_cassette_ref(self, version: CrawlerVersion, digest: str) -> str:
        if (
            version.live_probe_execution_id is None
            or version.live_probe_digest != digest
        ):
            raise ValueError(
                "live-derived replay requires a successful live probe of the current digest"
            )
        execution = self.service.get_execution(version.live_probe_execution_id)
        if (
            execution.status is not CrawlerExecutionStatus.SUCCEEDED
            or not execution.observations
            or execution.crawler_content_digest != digest
            or execution.cassette_ref is None
        ):
            raise ValueError("stored live probe is not a successful replay source")
        return execution.cassette_ref

    @staticmethod
    def _pass(check: CertificationCheck) -> CertificationCheckResult:
        return CertificationCheckResult(check=check, status=CheckStatus.PASS)

    @staticmethod
    def _fail(
        check: CertificationCheck,
        diagnostic: str,
        case: CertificationCase | None = None,
        result: CrawlerExecutionResult | None = None,
    ) -> CertificationCheckResult:
        return CertificationCheckResult(
            check=check,
            status=CheckStatus.FAIL,
            test_case=case.case_id if case else None,
            cassette_ref=(
                case.cassette_refs[-1] if case else result.cassette_ref if result else None
            ),
            artifact_ref=(result.artifact_refs[0] if result and result.artifact_refs else None),
            actual={"status": result.status.value} if result else {},
            diagnostic=diagnostic,
        )


__all__ = ["CrawlerCertificationService"]
