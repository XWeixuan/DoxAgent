"""Materialize and validate immutable, attempt-local Codex D1 agent bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doxagent.codex_runtime.client import WorkspaceClient
from doxagent.codex_runtime.errors import StructuredOutputInvalid
from doxagent.codex_runtime.schema import CodexD1Node, CodexWorkflowVersion, ResearchLane
from doxagent.workflows.codex_document1.schema import NodeOutput

BUNDLE_VERSION = "codex-d1-agent-bundle-v2"
SUPPORTED_BUNDLE_VERSIONS = frozenset(
    {
        BUNDLE_VERSION,
        "codex-global-research-agent-bundle-v1",
        "codex-market-situation-agent-bundle-v1",
    }
)
ATTEMPT_TASK_SCHEMA_VERSION = "codex-d1-attempt-task-v4"
PROGRESSIVE_NODES = frozenset(
    {
        CodexD1Node.C1,
        CodexD1Node.C2,
        CodexD1Node.C3,
        CodexD1Node.O4_B,
        CodexD1Node.O4_A,
        CodexD1Node.C5,
        CodexD1Node.O4,
    }
)


@dataclass(frozen=True)
class SeededAttemptBundle:
    input_sha256: str
    task_path: str
    context_path: str
    horizontal_path: str | None
    report_draft_path: str | None
    progress_path: str | None
    observation_candidates_path: str | None
    structured_output_path: str | None
    manual_upstream_paths: tuple[str, ...]
    required_sections: tuple[str, ...]
    required_skills: tuple[str, ...]


class AttemptBundleSeeder:
    def __init__(self, workspace: WorkspaceClient, root: str | Path) -> None:
        self._workspace = workspace
        self._root = Path(root)
        self._repo_root = Path(__file__).resolve().parents[4]
        self._manifest_text = (self._root / "bundle_manifest.json").read_text(encoding="utf-8")
        self._manifest = json.loads(self._manifest_text)
        if self._manifest.get("bundle_version") not in SUPPORTED_BUNDLE_VERSIONS:
            raise ValueError(f"unsupported Codex D1 bundle: {self._manifest.get('bundle_version')}")

    def node_definition(self, node: CodexD1Node) -> dict[str, Any]:
        try:
            return dict(self._manifest["nodes"][node.value])
        except KeyError as exc:
            raise ValueError(f"node is absent from bundle manifest: {node.value}") from exc

    def input_sha256(
        self,
        *,
        node: CodexD1Node,
        context_payload: dict[str, object],
        horizontal: dict[str, object] | None,
        manual_upstream: dict[str, str] | None = None,
        workflow_version: CodexWorkflowVersion = "codex_d1_v2",
        research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
    ) -> str:
        definition = self.node_definition(node)
        resources = self._resource_contents(definition)
        canonical = {
            "bundle_version": self._manifest["bundle_version"],
            "task_contract_version": ATTEMPT_TASK_SCHEMA_VERSION,
            "bundle_manifest_sha256": _sha256(self._manifest_text),
            "node": node.value,
            "workflow_version": workflow_version,
            "research_lane": research_lane.value,
            "node_definition": definition,
            "context_payload": context_payload,
            "horizontal": horizontal,
            "manual_upstream": {
                name: _sha256(content) for name, content in sorted((manual_upstream or {}).items())
            },
            "resources": {name: _sha256(text) for name, text in sorted(resources.items())},
        }
        return _sha256(_json(canonical, compact=True))

    async def seed(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        attempt_id: str,
        context_payload: dict[str, object],
        horizontal: dict[str, object] | None,
        previous_failure: str | None = None,
        manual_upstream: dict[str, str] | None = None,
        workflow_version: CodexWorkflowVersion = "codex_d1_v2",
        research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1,
    ) -> SeededAttemptBundle:
        definition = self.node_definition(node)
        resources = self._resource_contents(definition)
        base = f"attempts/{attempt_id}"
        input_base = f"{base}/input"
        output_base = f"{base}/output"
        skills = tuple(definition.get("required_skills", ()))
        sections = tuple(definition.get("required_sections", ()))
        progressive = node in PROGRESSIVE_NODES
        horizontal_path = f"{input_base}/horizontal.json" if horizontal is not None else None
        report_path = f"{output_base}/report_draft.md" if progressive else None
        progress_path = f"{output_base}/progress.json" if progressive else None
        candidates_path = f"{output_base}/observation_candidates.json" if progressive else None
        structured_output_path = None if progressive else f"{output_base}/completion.json"
        required_skill_paths = tuple(f"{input_base}/{item}" for item in skills)
        manual_upstream_paths = tuple(
            f"{input_base}/manual_upstream/{name}" for name in sorted(manual_upstream or {})
        )
        task = {
            "schema_version": ATTEMPT_TASK_SCHEMA_VERSION,
            "bundle_version": self._manifest["bundle_version"],
            "workflow_version": workflow_version,
            "research_lane": research_lane.value,
            "node": node.value,
            "required_sections": list(sections),
            "required_skills": list(required_skill_paths),
            "context_path": f"{input_base}/context.json",
            "horizontal_path": horizontal_path,
            "draft_path": report_path,
            "progress_path": progress_path,
            "observation_candidates_path": candidates_path,
            "structured_output_path": structured_output_path,
            "output_language": "zh-CN",
            "output_schema_path": f"{input_base}/{self._manifest['schema']}",
            "previous_attempt_failure": previous_failure,
            "manual_upstream": (
                {
                    "mode": "pilot_override",
                    "files": list(manual_upstream_paths),
                    "precedence": "manual_over_source_run",
                    "citation_policy": "context_only_reverify",
                }
                if manual_upstream_paths
                else None
            ),
            "progress_contract": (
                {
                    "status": "in_progress | completed",
                    "completed_sections": "ordered subset of required_sections",
                }
                if progressive
                else None
            ),
        }
        context = {
            "schema_version": "codex-research-node-context-v1",
            "workflow_version": workflow_version,
            "research_lane": research_lane.value,
            "run_id": run_id,
            "node": node.value,
            "payload": context_payload,
        }
        input_hash = self.input_sha256(
            node=node,
            context_payload=context_payload,
            horizontal=horizontal,
            manual_upstream=manual_upstream,
            workflow_version=workflow_version,
            research_lane=research_lane,
        )
        writes = {
            f"{input_base}/AGENTS.md": resources["AGENTS.md"],
            f"{input_base}/task.md": resources[definition["agent"]],
            f"{input_base}/task.json": _json(task),
            f"{input_base}/context.json": _json(context),
            f"{input_base}/bundle_manifest.json": self._manifest_text,
            f"{input_base}/{self._manifest['schema']}": resources[self._manifest["schema"]],
        }
        for skill in skills:
            writes[f"{input_base}/{skill}"] = resources[skill]
        if horizontal_path is not None:
            writes[horizontal_path] = _json(horizontal)
        for name, content in sorted((manual_upstream or {}).items()):
            writes[f"{input_base}/manual_upstream/{name}"] = content
        for path, content in writes.items():
            await self._workspace.write_text(run_id, path, content)
        audit = {
            "schema_version": "codex-d1-attempt-bundle-audit-v1",
            "bundle_version": self._manifest["bundle_version"],
            "input_sha256": input_hash,
            "files": {path: _sha256(content) for path, content in sorted(writes.items())},
        }
        await self._workspace.write_text(run_id, f"{base}/audit/bundle.json", _json(audit))
        if progressive:
            await self._workspace.write_text(run_id, report_path or "", "")
            await self._workspace.write_text(
                run_id,
                progress_path or "",
                _json(
                    {
                        "completed_sections": [],
                        "status": "in_progress",
                    }
                ),
            )
            await self._workspace.write_text(run_id, candidates_path or "", "[]")
        elif structured_output_path is not None:
            await self._workspace.write_text(run_id, structured_output_path, "{}")
        return SeededAttemptBundle(
            input_sha256=input_hash,
            task_path=f"{input_base}/task.json",
            context_path=f"{input_base}/context.json",
            horizontal_path=horizontal_path,
            report_draft_path=report_path,
            progress_path=progress_path,
            observation_candidates_path=candidates_path,
            structured_output_path=structured_output_path,
            manual_upstream_paths=manual_upstream_paths,
            required_sections=sections,
            required_skills=required_skill_paths,
        )

    def _resource_contents(self, definition: dict[str, Any]) -> dict[str, str]:
        paths = [
            "AGENTS.md",
            self._manifest["schema"],
            definition["agent"],
            *definition.get("required_skills", ()),
        ]
        values: dict[str, str] = {}
        for relative in paths:
            source_overrides = {
                **self._manifest.get("resource_sources", {}),
                **definition.get("resource_sources", {}),
            }
            source = source_overrides.get(relative) if isinstance(source_overrides, dict) else None
            if source is not None:
                path = (self._repo_root / str(source)).resolve()
                if self._repo_root not in path.parents:
                    raise ValueError(f"canonical bundle resource escaped repo: {source}")
            else:
                path = (self._root / relative).resolve()
                if self._root.resolve() not in path.parents:
                    raise ValueError(f"bundle resource escaped root: {relative}")
            values[relative] = path.read_text(encoding="utf-8")
        return values


class AttemptOutputValidator:
    def __init__(self, workspace: WorkspaceClient) -> None:
        self._workspace = workspace

    async def validate(
        self,
        *,
        run_id: str,
        node: CodexD1Node,
        seeded: SeededAttemptBundle,
        output: NodeOutput,
    ) -> None:
        if node not in PROGRESSIVE_NODES:
            return
        assert seeded.report_draft_path
        assert seeded.progress_path
        assert seeded.observation_candidates_path
        draft = await self._workspace.read_text(run_id, seeded.report_draft_path)
        if not draft.content or not draft.content.strip():
            raise StructuredOutputInvalid("progressive report draft is missing or empty")
        progress_file = await self._workspace.read_text(run_id, seeded.progress_path)
        try:
            progress = json.loads(_strip_utf8_bom(progress_file.content or ""))
        except json.JSONDecodeError as exc:
            raise StructuredOutputInvalid("progress.json is not valid JSON") from exc
        if progress.get("status") != "completed":
            raise StructuredOutputInvalid("progress.json is not completed")
        declared_sections = progress.get("required_sections")
        if declared_sections is not None and declared_sections != list(seeded.required_sections):
            raise StructuredOutputInvalid("progress.json required_sections changed")
        if progress.get("completed_sections") != list(seeded.required_sections):
            raise StructuredOutputInvalid("not all required report sections were completed")
        if _normalize_newlines(draft.content) != _normalize_newlines(output.report_markdown):
            raise StructuredOutputInvalid("report_draft.md does not match report_markdown")
        candidate_file = await self._workspace.read_text(run_id, seeded.observation_candidates_path)
        try:
            candidates = json.loads(_strip_utf8_bom(candidate_file.content or ""))
        except json.JSONDecodeError as exc:
            raise StructuredOutputInvalid("observation_candidates.json is not valid JSON") from exc
        expected = [item.model_dump(mode="json") for item in output.observation_candidates]
        if candidates != expected:
            raise StructuredOutputInvalid(
                "observation_candidates.json does not match structured completion"
            )


def _normalize_newlines(value: str) -> str:
    return _strip_utf8_bom(value).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _strip_utf8_bom(value: str) -> str:
    return value.removeprefix("\ufeff")


def _json(value: object, *, compact: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=compact,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
        default=str,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
