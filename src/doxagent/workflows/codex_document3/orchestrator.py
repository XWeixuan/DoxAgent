"""D3 initialization/maintenance orchestration and canonical publication."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from doxagent.codex_runtime.published_storage import PublishedDocumentStorage
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CodexD3Node,
    PublishedDocument,
    ResearchLane,
    utc_now,
)

from .assembler import apply_patch, assemble_initial_policy_set, build_coverage_map
from .identity import allocate_stable_policy_ids
from .inputs import Document3InputPreparer
from .repository import Document3PolicyRepository
from .runner import Document3AgentRunner
from .runtime_projection import project_policy_set
from .schema import (
    CalibrationLogEntry,
    CoverageMap,
    Document3Bundle,
    Document3Handoff,
    O3RunResult,
    O3RunStatus,
    Policy,
    PolicyPatchSet,
    PolicySet,
    PublicationState,
    WorklistEntry,
)
from .validator import validate_initial_artifacts, validate_patch

ModelT = TypeVar("ModelT", bound=BaseModel)


class Document3RuntimeRepository(Protocol):
    def save_bundle(self, bundle: Any) -> None: ...

    def save_artifact(self, artifact: ArtifactRef) -> None: ...

    def save_published_document(self, document: PublishedDocument) -> None: ...


class Document3Orchestrator:
    def __init__(
        self,
        *,
        input_preparer: Document3InputPreparer,
        agent_runner: Document3AgentRunner,
        policy_repository: Document3PolicyRepository,
        runtime_repository: Document3RuntimeRepository,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._inputs = input_preparer
        self._agent = agent_runner
        self._policy_repository = policy_repository
        self._runtime_repository = runtime_repository
        self._published_storage = published_storage

    async def initialize(
        self,
        *,
        ticker: str,
        document2_run_id: str,
        event_library_version: int | None = None,
        run_id: str | None = None,
        cutoff_at: datetime | None = None,
    ) -> O3RunResult:
        normalized_ticker = ticker.upper()
        selected_run_id = run_id or f"d3-{normalized_ticker.lower()}-{uuid4().hex[:20]}"
        cutoff = cutoff_at or utc_now()
        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="draft",
            )
        )
        try:
            prepared = await self._inputs.prepare_initialize(
                ticker=normalized_ticker,
                document2_run_id=document2_run_id,
                event_library_version=event_library_version,
            )
            previous = prepared.previous_policy_set
            await self._agent.seed_initialize(
                run_id=selected_run_id,
                document2_json=prepared.document2.model_dump_json(indent=2),
                reference_view=prepared.reference_view,
                previous_policy_set_json=(previous.model_dump_json(indent=2) if previous else None),
                metadata={
                    "mode": "O3_INITIALIZE",
                    "ticker": normalized_ticker,
                    "run_id": selected_run_id,
                    "document2_ref": prepared.document2_ref.model_dump(mode="json"),
                    "event_library_ref": (
                        prepared.event_library_ref.model_dump(mode="json")
                        if prepared.event_library_ref
                        else None
                    ),
                    "failed_shells": [item.model_dump() for item in prepared.failed_shells],
                    "warnings": prepared.warnings,
                },
            )
            _, thread_id = await self._agent.run_initialize(
                run_id=selected_run_id,
                ticker=normalized_ticker,
                cutoff_at=cutoff,
            )
            worklist = await self._read_jsonl(
                selected_run_id, "output/work/worklist.jsonl", WorklistEntry
            )
            calibration_log = await self._read_jsonl(
                selected_run_id,
                "output/work/calibration_log.jsonl",
                CalibrationLogEntry,
            )
            policies = await self._read_policy_drafts(selected_run_id)
            provisional = build_coverage_map(
                ticker=normalized_ticker,
                worklist=worklist,
                expected_gap_refs=prepared.expected_gap_refs,
                failed_shells=prepared.failed_shells,
                warnings=prepared.warnings,
            )
            await self._agent.workspace.write_text(
                selected_run_id,
                "output/work/coverage_map.json",
                provisional.model_dump_json(indent=2),
            )
            review, _ = await self._agent.run_final_review(
                run_id=selected_run_id,
                ticker=normalized_ticker,
                cutoff_at=cutoff,
                thread_id=thread_id,
            )
            if review.status == "REVIEW_BLOCKED" and review.blocking_issue_count:
                raise ValueError("O3 Final Global Pass reported a structural blocking issue")
            await self._assert_agent_write_boundary(selected_run_id)

            worklist = await self._read_jsonl(
                selected_run_id, "output/work/worklist.jsonl", WorklistEntry
            )
            calibration_log = await self._read_jsonl(
                selected_run_id,
                "output/work/calibration_log.jsonl",
                CalibrationLogEntry,
            )
            policies = await self._read_policy_drafts(selected_run_id)
            validation = validate_initial_artifacts(
                expected_gap_refs=prepared.expected_gap_refs,
                worklist=worklist,
                calibration_log=calibration_log,
                policies=policies,
            )
            if not validation.valid:
                raise ValueError(
                    "D3 deterministic structural validation failed: "
                    + "; ".join(item.message for item in validation.blocking_findings)
                )
            if (
                prepared.document2_ref.publication_state is PublicationState.PARTIAL
                or prepared.warnings
                or review.issue_count
            ):
                validation = validation.model_copy(
                    update={"publication_state": PublicationState.PARTIAL}
                )

            policy_set, id_map = assemble_initial_policy_set(
                ticker=normalized_ticker,
                document2_ref=prepared.document2_ref,
                event_library_ref=prepared.event_library_ref,
                drafts=policies,
                previous=previous,
                validation=validation,
                published_at=utc_now(),
            )
            canonical_worklist = [
                item.model_copy(
                    update={
                        "policy_ids": [
                            id_map.get(policy_id, policy_id) for policy_id in item.policy_ids
                        ]
                    }
                )
                for item in worklist
            ]
            coverage = build_coverage_map(
                ticker=normalized_ticker,
                worklist=canonical_worklist,
                expected_gap_refs=prepared.expected_gap_refs,
                failed_shells=prepared.failed_shells,
                warnings=[
                    *prepared.warnings,
                    *(item.message for item in validation.findings),
                    *(item.message for item in review.issues),
                ],
            )
            expected_base = previous.policy_set_version if previous else None
            handoff = await self._publish_policy_set(
                run_id=selected_run_id,
                policy_set=policy_set,
                coverage=coverage,
                expected_base_version=expected_base,
            )
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="published",
                    handoff=handoff,
                    artifacts=[
                        handoff.published_artifact,
                        handoff.coverage_artifact,
                        handoff.runtime_projection_artifact,
                    ],
                    updated_at=handoff.published_at,
                    published_at=handoff.published_at,
                )
            )
            unresolved = sum(item.status.value == "UNRESOLVED" for item in canonical_worklist)
            return O3RunResult(
                status=(
                    O3RunStatus.COMPLETED
                    if policy_set.publication_state is PublicationState.COMPLETE
                    else O3RunStatus.PARTIAL
                ),
                processed_gap_count=len(prepared.expected_gap_refs),
                policy_count=len(policy_set.policies),
                unresolved_path_count=unresolved,
                warning_count=len(coverage.warnings),
                policy_set_version=policy_set.policy_set_version,
            )
        except Exception as exc:
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="failed",
                    error=str(exc)[:4000],
                )
            )
            raise

    async def maintain(
        self,
        *,
        ticker: str,
        event_library_version: int | None = None,
        run_id: str | None = None,
        cutoff_at: datetime | None = None,
    ) -> O3RunResult:
        normalized_ticker = ticker.upper()
        current, event_ref, reference_view = self._inputs.prepare_maintenance_reference(
            ticker=normalized_ticker,
            event_library_version=event_library_version,
        )
        if event_ref is None or (
            current.event_library_ref is not None
            and event_ref.version <= current.event_library_ref.version
        ):
            return O3RunResult(
                status=O3RunStatus.NOOP,
                policy_count=len(current.policies),
                policy_set_version=current.policy_set_version,
            )
        if not self._reference_view_has_content(reference_view):
            return O3RunResult(
                status=O3RunStatus.DEGRADED,
                policy_count=len(current.policies),
                warning_count=1,
                policy_set_version=current.policy_set_version,
            )

        selected_run_id = run_id or f"d3m-{normalized_ticker.lower()}-{uuid4().hex[:20]}"
        await self._agent.seed_maintenance(
            run_id=selected_run_id,
            policy_set_json=current.model_dump_json(indent=2),
            reference_view=reference_view,
            metadata={
                "mode": "O3_MAINTAIN",
                "ticker": normalized_ticker,
                "run_id": selected_run_id,
                "base_policy_set_version": current.policy_set_version,
                "event_library_ref": event_ref.model_dump(mode="json"),
            },
        )
        await self._agent.run_maintain(
            run_id=selected_run_id,
            ticker=normalized_ticker,
            cutoff_at=cutoff_at or utc_now(),
        )
        await self._assert_agent_write_boundary(selected_run_id)
        patch = await self._read_json(
            selected_run_id, "output/work/policy_patch.json", PolicyPatchSet
        )
        stable_upserts, _ = allocate_stable_policy_ids(
            ticker=normalized_ticker,
            drafts=patch.upsert_policies,
            previous=current.policies,
        )
        patch = patch.model_copy(update={"upsert_policies": stable_upserts})
        validation = validate_patch(
            patch=patch,
            current_policy_ids={item.policy_id for item in current.policies},
            current_version=current.policy_set_version,
        )
        if not validation.valid:
            raise ValueError(validation.blocking_findings[0].message)
        updated = apply_patch(current=current, patch=patch, published_at=utc_now())
        if updated is None:
            return O3RunResult(
                status=O3RunStatus.NOOP,
                policy_count=len(current.policies),
                policy_set_version=current.policy_set_version,
            )
        if validation.findings:
            updated = updated.model_copy(update={"publication_state": PublicationState.PARTIAL})
        coverage = CoverageMap(
            ticker=normalized_ticker,
            warnings=[item.message for item in validation.findings],
        )
        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="draft",
            )
        )
        try:
            handoff = await self._publish_policy_set(
                run_id=selected_run_id,
                policy_set=updated,
                coverage=coverage,
                expected_base_version=current.policy_set_version,
            )
        except Exception as exc:
            self._runtime_repository.save_bundle(
                Document3Bundle(
                    ticker=normalized_ticker,
                    run_id=selected_run_id,
                    status="failed",
                    error=str(exc)[:4000],
                )
            )
            raise
        self._runtime_repository.save_bundle(
            Document3Bundle(
                ticker=normalized_ticker,
                run_id=selected_run_id,
                status="published",
                handoff=handoff,
                artifacts=[
                    handoff.published_artifact,
                    handoff.coverage_artifact,
                    handoff.runtime_projection_artifact,
                ],
                updated_at=handoff.published_at,
                published_at=handoff.published_at,
            )
        )
        return O3RunResult(
            status=(
                O3RunStatus.PARTIAL
                if updated.publication_state is PublicationState.PARTIAL
                else O3RunStatus.COMPLETED
            ),
            policy_count=len(updated.policies),
            warning_count=len(validation.findings),
            policy_set_version=updated.policy_set_version,
        )

    async def _read_jsonl(self, run_id: str, path: str, model: type[ModelT]) -> list[ModelT]:
        response = await self._agent.workspace.read_text(run_id, path)
        return [
            model.model_validate_json(line)
            for line in (response.content or "").splitlines()
            if line.strip()
        ]

    async def _read_json(self, run_id: str, path: str, model: type[ModelT]) -> ModelT:
        response = await self._agent.workspace.read_text(run_id, path)
        if response.content is None:
            raise ValueError(f"workspace file has no text content: {path}")
        return model.model_validate_json(response.content)

    async def _read_policy_drafts(self, run_id: str) -> list[Policy]:
        inventory = await self._agent.workspace.inventory(run_id)
        paths = sorted(
            item.relative_path
            for item in inventory.files
            if item.relative_path.startswith("output/work/policies/")
            and item.relative_path.endswith(".json")
        )
        return [await self._read_json(run_id, path, Policy) for path in paths]

    async def _assert_agent_write_boundary(self, run_id: str) -> None:
        inventory = await self._agent.workspace.inventory(run_id)
        unauthorized = [
            item.relative_path
            for item in inventory.files
            if not item.relative_path.startswith("context/document3/")
            and not item.relative_path.startswith("output/work/")
        ]
        if unauthorized:
            raise ValueError(f"D3 agent wrote outside its boundary: {unauthorized[:10]}")

    @staticmethod
    def _reference_view_has_content(value: str) -> bool:
        content = re.sub(r"[#|\-\s]", "", value)
        return len(content) >= 20

    async def _publish_policy_set(
        self,
        *,
        run_id: str,
        policy_set: PolicySet,
        coverage: CoverageMap,
        expected_base_version: int | None,
    ) -> Document3Handoff:
        projection = project_policy_set(policy_set)
        payloads = {
            "output/final/document3.json": policy_set.model_dump_json(indent=2) + "\n",
            "output/final/coverage_map.json": coverage.model_dump_json(indent=2) + "\n",
            "output/final/runtime_projection.json": projection.model_dump_json(indent=2) + "\n",
            "output/final/document3.md": self._render_markdown(policy_set),
        }
        for path, content in payloads.items():
            await self._agent.workspace.write_text(run_id, path, content)
        release_payloads = {
            "artifacts/document3/document3.json": payloads["output/final/document3.json"],
            "artifacts/document3/coverage_map.json": payloads["output/final/coverage_map.json"],
            "artifacts/document3/runtime_projection.json": payloads[
                "output/final/runtime_projection.json"
            ],
            "artifacts/document3/document3.md": payloads["output/final/document3.md"],
        }
        metadata = {
            path: await self._agent.workspace.write_text(run_id, path, content)
            for path, content in release_payloads.items()
        }
        await self._agent.workspace.publish(run_id, list(release_payloads))
        artifacts: dict[str, ArtifactRef] = {}
        kinds = {
            "artifacts/document3/document3.json": ArtifactKind.BUNDLE,
            "artifacts/document3/coverage_map.json": ArtifactKind.REPORT,
            "artifacts/document3/runtime_projection.json": ArtifactKind.REPORT,
        }
        for path, kind in kinds.items():
            item = metadata[path]
            artifact = ArtifactRef(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                artifact_id=uuid4().hex,
                run_id=run_id,
                node=CodexD3Node.PUBLISH,
                attempt_id="d3-publish-01",
                kind=kind,
                relative_path=path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                content_type="application/json",
                published=True,
            )
            self._runtime_repository.save_artifact(artifact)
            await self._save_published_content(artifact, release_payloads[path])
            artifacts[path] = artifact

        self._policy_repository.publish(policy_set, expected_base_version=expected_base_version)
        return Document3Handoff(
            ticker=policy_set.ticker,
            run_id=run_id,
            policy_set_version=policy_set.policy_set_version,
            publication_state=policy_set.publication_state,
            published_artifact=artifacts["artifacts/document3/document3.json"],
            coverage_artifact=artifacts["artifacts/document3/coverage_map.json"],
            runtime_projection_artifact=artifacts["artifacts/document3/runtime_projection.json"],
            published_at=policy_set.published_at,
        )

    async def _save_published_content(self, artifact: ArtifactRef, content: str) -> None:
        encoded = content.encode("utf-8")
        artifact_kind: Literal["report", "bundle", "manifest"] = (
            "bundle" if artifact.kind is ArtifactKind.BUNDLE else "report"
        )
        if len(encoded) <= 2 * 1024 * 1024:
            document = PublishedDocument(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                artifact_kind=artifact_kind,
                sha256=artifact.sha256,
                size_bytes=len(encoded),
                content_type=artifact.content_type,
                content_text=content,
                published_at=artifact.created_at,
            )
        else:
            if self._published_storage is None:
                raise ValueError("D3 published artifact exceeds inline limit without storage")
            storage_path = (
                f"codex/document3/{artifact.run_id}/{artifact.artifact_id}/"
                f"{artifact.relative_path.rsplit('/', 1)[-1]}"
            )
            await self._published_storage.put(storage_path, encoded, artifact.content_type)
            document = PublishedDocument(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                artifact_kind=artifact_kind,
                sha256=artifact.sha256,
                size_bytes=len(encoded),
                content_type=artifact.content_type,
                storage_path=storage_path,
                published_at=artifact.created_at,
            )
        self._runtime_repository.save_published_document(document)

    @staticmethod
    def _render_markdown(policy_set: PolicySet) -> str:
        lines = [
            f"# {policy_set.ticker} Monitoring Execution Policies V{policy_set.policy_set_version}",
            "",
            f"Publication state: {policy_set.publication_state.value}",
            "",
        ]
        for policy in policy_set.policies:
            lines.extend(
                [
                    f"## {policy.title} ({policy.policy_id})",
                    "",
                    f"- Direction: {policy.decision.value}",
                    f"- Match scope: {policy.match_scope}",
                    f"- Activation: {policy.activation_summary}",
                    "",
                ]
            )
            for condition in policy.activation_conditions:
                lines.append(f"- {condition.condition_id}: {condition.criterion}")
            lines.append("")
        return "\n".join(lines)
