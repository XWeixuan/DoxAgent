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
            checks.append(await self._determinism(value, cases))
            checks.append(await self._failure_replay(value))
        else:
            for check in (
                CertificationCheck.REPLAY,
                CertificationCheck.TEMPORAL_REPLAY,
                CertificationCheck.SYNTHETIC_INCREMENT,
                CertificationCheck.DETERMINISM,
                CertificationCheck.FAILURE_REPLAY,
            ):
                checks.append(self._fail(check, "contract validation failed"))
        overall = (
            CheckStatus.PASS
            if all(item.status is CheckStatus.PASS for item in checks)
            else CheckStatus.FAIL
        )
        result = CertificationResult(
            crawler_id=crawler_id,
            crawler_version=version,
            content_digest=digest,
            overall=overall,
            checks=checks,
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
            actual = [item.external_id or item.url for item in result.observations]
            if result.status is not CrawlerExecutionStatus.SUCCEEDED:
                return self._fail(check, result.error_message or "execution failed", case, result)
            if actual != case.expected_external_ids:
                return CertificationCheckResult(
                    check=check,
                    status=CheckStatus.FAIL,
                    test_case=case.case_id,
                    expected={"external_ids": case.expected_external_ids},
                    actual={"external_ids": actual},
                    cassette_ref=case.cassette_refs[0],
                    diagnostic="crawler observations differ from deterministic expectation",
                )
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
            actual = [item.external_id or item.url for item in second.observations]
            if (
                second.status is not CrawlerExecutionStatus.SUCCEEDED
                or actual != case.expected_external_ids
            ):
                return CertificationCheckResult(
                    check=CertificationCheck.TEMPORAL_REPLAY,
                    status=CheckStatus.FAIL,
                    test_case=case.case_id,
                    expected={"external_ids": case.expected_external_ids},
                    actual={"external_ids": actual, "status": second.status.value},
                    cassette_ref=case.cassette_refs[1],
                    diagnostic=second.error_message or "T1 increment was not discovered",
                )
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

    async def _failure_replay(self, version: CrawlerVersion) -> CertificationCheckResult:
        crawler_id = version.spec.crawler_id
        cases = self.repository.list_regressions(crawler_id)
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
                return self._fail(
                    CertificationCheck.FAILURE_REPLAY,
                    result.error_message or "historical failure still reproduces",
                    result=result,
                )
        return self._pass(CertificationCheck.FAILURE_REPLAY)

    async def _execute_case(
        self,
        version: CrawlerVersion,
        case: CertificationCase,
        cassette_ref: str,
        checkpoint: dict[str, object],
    ) -> CrawlerExecutionResult:
        spec = version.spec
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
                cassette_ref=cassette_ref,
                commit_checkpoint=False,
                checkpoint_override=checkpoint,
            )
        )

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
