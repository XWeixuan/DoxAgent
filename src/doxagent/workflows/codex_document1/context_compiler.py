"""Create immutable, role-scoped context artifacts for every Codex turn."""

from __future__ import annotations

import json
from uuid import uuid4

from doxagent.codex_runtime.client import WorkspaceClient
from doxagent.codex_runtime.repository import CodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_D1_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CodexD1Node,
    CodexWorkflowVersion,
    ResearchLane,
)


class CodexD1ContextCompiler:
    def __init__(
        self,
        workspace: WorkspaceClient,
        repository: CodexRuntimeRepository,
        *,
        workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION,
        research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
    ) -> None:
        self._workspace = workspace
        self._repository = repository
        self._workflow_version = workflow_version
        self._research_lane = research_lane

    async def write_context(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        attempt_id: str,
        payload: dict[str, object],
    ) -> ArtifactRef:
        relative_path = f"attempts/{attempt_id}/input/context.json"
        body = json.dumps(
            {
                "schema_version": "codex-d1-node-context-v1",
                "run_id": run_id,
                "node": node.value,
                "payload": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        metadata = await self._workspace.write_text(run_id, relative_path, body)
        artifact = ArtifactRef(
            workflow_version=self._workflow_version,
            research_lane=self._research_lane,
            artifact_id=uuid4().hex,
            run_id=run_id,
            node=node,
            attempt_id=attempt_id,
            kind=ArtifactKind.CONTEXT,
            relative_path=relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type="application/json",
        )
        self._repository.save_artifact(artifact)
        return artifact

    async def write_output(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        attempt_id: str,
        report_markdown: str,
        completion_json: str,
        persist_metadata: bool = True,
    ) -> tuple[ArtifactRef, ArtifactRef]:
        base = f"artifacts/{node.value}/{attempt_id}"
        report_path = f"{base}/report.md"
        completion_path = f"{base}/completion.json"
        report_meta = await self._workspace.write_text(run_id, report_path, report_markdown)
        completion_meta = await self._workspace.write_text(run_id, completion_path, completion_json)
        report_ref = ArtifactRef(
            workflow_version=self._workflow_version,
            research_lane=self._research_lane,
            artifact_id=uuid4().hex,
            run_id=run_id,
            node=node,
            attempt_id=attempt_id,
            kind=ArtifactKind.REPORT,
            relative_path=report_path,
            sha256=report_meta.sha256,
            size_bytes=report_meta.size_bytes,
            content_type="text/markdown",
        )
        completion_ref = ArtifactRef(
            workflow_version=self._workflow_version,
            research_lane=self._research_lane,
            artifact_id=uuid4().hex,
            run_id=run_id,
            node=node,
            attempt_id=attempt_id,
            kind=ArtifactKind.STRUCTURED_COMPLETION,
            relative_path=completion_path,
            sha256=completion_meta.sha256,
            size_bytes=completion_meta.size_bytes,
            content_type="application/json",
        )
        if persist_metadata:
            self._repository.save_artifact(report_ref)
            self._repository.save_artifact(completion_ref)
        return report_ref, completion_ref
