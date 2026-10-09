"""Local SDK execution of frozen D2 Pilot cases, with separate audit turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tomllib
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort, ReasoningSummary

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.data_runtime.pilot_case import (
    canonical_node_attempt_id,
    validate_pilot_case_root,
)
from doxagent.pilot.document2_case_builder import _bootstrap_contract, _pilot_candidate_sets_context
from doxagent.pilot.templates import render_document2_task

ISSUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_summary", "issues", "no_issues_reason"],
    "properties": {
        "task_summary": {"type": "string"},
        "no_issues_reason": {"type": ["string", "null"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "category",
                    "severity",
                    "location",
                    "observation",
                    "impact",
                    "workaround",
                    "suggestion",
                    "resolved",
                ],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "bug",
                            "task_ambiguity",
                            "writing_difficulty",
                            "execution_difficulty",
                            "evidence_limitation",
                            "quality_risk",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    **{
                        key: {"type": "string"}
                        for key in (
                            "location",
                            "observation",
                            "impact",
                            "workaround",
                            "suggestion",
                        )
                    },
                    "resolved": {"type": "boolean"},
                },
            },
        },
    },
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".p-{uuid4().hex[:12]}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def execution_lock(path: Path):
    """Nonblocking process lock; an abandoned file does not block recovery."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Pilot is already being driven: {path.parent}") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _flatten(config: dict, prefix: str = "") -> dict:
    result = {}
    for key, value in config.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten(value, name))
        else:
            result[name] = value
    return result


class PilotSdkRunner:
    def __init__(
        self,
        *,
        client: Any = None,
        model: str | None = None,
        effort: str | None = None,
        timeout_seconds: float = 7200,
    ) -> None:
        self.client = client or AsyncCodex(CodexConfig(client_name="doxagent-pilot"))
        self._owns_client = client is None
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.close()

    async def run_case(
        self,
        case_root: str | Path,
        *,
        thread_id: str | None = None,
        retry: bool = False,
    ) -> dict:
        root = Path(case_root).resolve()
        with execution_lock(root / ".pilot-sdk.lock"):
            return await self._run_case(root, thread_id=thread_id, retry=retry)

    async def _run_case(self, root: Path, *, thread_id: str | None, retry: bool) -> dict:
        manifest = read_json(root / "case_manifest.json")
        attempt = canonical_node_attempt_id(manifest)
        node = CodexD2Node(manifest["node"])
        validate_pilot_case_root(
            run_root=root,
            pilot_case_id=manifest["case_id"],
            run_id=manifest["run_id"],
            attempt_id=attempt,
            node=node,
        )
        local = root / "attempts" / attempt
        audit = local / "audit"
        audit.mkdir(exist_ok=True)
        receipt_path = audit / "pilot_sdk_receipt.json"
        frozen = tree_hash(local / "input") + tree_hash(root / "context" / "pilot_upstream")
        receipt = (
            read_json(receipt_path)
            if receipt_path.exists()
            else {
                "schema_version": "pilot-sdk-v1",
                "case_id": manifest["case_id"],
                "node": node.value,
                "input_digest": frozen,
                "phase": "research",
                "status": "new",
                "generation": 1,
                "thread_id": thread_id,
            }
        )
        if receipt["input_digest"] != frozen:
            raise ValueError("Pilot frozen inputs changed; prepare a new case")
        if receipt["status"] == "completed":
            if receipt["output_digest"] != tree_hash(local / "output"):
                raise ValueError("Pilot completed output changed")
            self._validate_completion(root, manifest)
            return receipt
        if receipt["status"] == "failed" and not retry:
            raise RuntimeError("Pilot previously failed; inspect its report then use --retry")
        retry_error = receipt.get("error") if receipt["status"] == "failed" and retry else None
        config = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
        model = self.model or config.get("model", "gpt-6.1-sol")
        effort = self.effort or config.get("model_reasoning_effort", "high")
        sdk_config = _flatten(config)
        sdk_config["mcp_servers.data.required"] = False
        if node is CodexD2Node.O1_OPEN_DISCOVERY:
            sdk_config["mcp_servers.d2_discovery.required"] = False
            sdk_config["mcp_servers.d2_discovery.enabled"] = True
            sdk_config[
                "mcp_servers.d2_discovery.tools.commit_open_discovery_scan.approval_mode"
            ] = "approve"
        elif node.value.startswith("d2_o1_") and receipt.get("thread_id"):
            # A resumed Discovery thread needs a complete server definition even
            # while disabling the tool; an enabled-only table is invalid Codex config.
            sdk_config.update(
                {
                    "mcp_servers.d2_discovery.command": sys.executable,
                    "mcp_servers.d2_discovery.args": [
                        "-m",
                        "doxagent.workflows.codex_document2.discovery_checkpoint",
                    ],
                    "mcp_servers.d2_discovery.cwd": str(root),
                    "mcp_servers.d2_discovery.enabled": False,
                    "mcp_servers.d2_discovery.required": False,
                }
            )
        # A resumed thread must use this case's capability, cwd and tool scope.
        sandbox = Sandbox.workspace_write
        sdk_config["sandbox_workspace_write.writable_roots"] = [str(root)]
        sdk_config["sandbox_workspace_write.network_access"] = True
        arguments = dict(
            cwd=str(root),
            model=model,
            sandbox=sandbox,
            approval_mode=ApprovalMode.deny_all,
            config=sdk_config,
        )
        secrets = [
            str(value)
            for key, value in sdk_config.items()
            if any(
                word in key.lower()
                for word in ("secret", "token", "capability", "password", "api_key")
            )
        ]

        def redact(value: str) -> str:
            for secret in secrets:
                if len(secret) > 8:
                    value = value.replace(secret, "[REDACTED]")
            return value

        try:
            if receipt.get("thread_id"):
                thread = await self.client.thread_resume(receipt["thread_id"], **arguments)
            else:
                thread = await self.client.thread_start(**arguments)
                receipt["thread_id"] = thread.id
                write_json(receipt_path, receipt)
            if receipt["status"] == "running":
                # Do not duplicate a still-running SDK turn after a driver crash.
                if not receipt.get("turn_id"):
                    raise RuntimeError("Uncertain SDK turn admission; inspect thread before retry")
                snapshot = await thread.read(include_turns=True)
                turn = next((t for t in snapshot.thread.turns if t.id == receipt["turn_id"]), None)
                if turn is None or _value(turn.status) not in {
                    "completed",
                    "failed",
                    "interrupted",
                }:
                    raise RuntimeError(
                        "Previous SDK turn is unsettled; inspect it before continuing"
                    )
                final = self._recover_trace(turn, audit, receipt, redact)
                if _value(turn.status) == "completed":
                    self._accept_phase(root, manifest, receipt, final)
                    write_json(receipt_path, receipt)
                else:
                    if receipt["phase"] == "research":
                        receipt["delivery_error"] = f"Recovered SDK turn {_value(turn.status)}"
                        self._accept_phase(root, manifest, receipt, final)
                    else:
                        receipt["status"] = "ready"
                    write_json(receipt_path, receipt)
            if receipt["status"] == "failed":
                # Preserve previous evidence rather than silently overwriting it.
                generation = receipt["generation"]
                write_json(audit / f"pilot_sdk_receipt.g{generation}.json", receipt)
                if receipt["phase"] == "research" and (local / "output").exists():
                    old = audit / f"research_output.g{generation}"
                    if old.exists():
                        raise ValueError("Pilot retry archive already exists")
                    (local / "output").rename(old)
                    (local / "output").mkdir()
                receipt.update(status="ready", generation=generation + 1)
            for phase in ("research", "review"):
                if receipt["phase"] != phase or receipt["status"] == "completed":
                    continue
                if phase == "research":
                    from doxagent.workflows.codex_document2.acceptance import DeliveryReceipt
                    from doxagent.workflows.codex_document2.schema import strict_json_schema

                    schema = (
                        strict_json_schema(DeliveryReceipt.model_json_schema())
                        if manifest["document_schema_version"] == "document2.v2.1"
                        else read_json(local / "input" / "output_schema.json")
                    )
                    # Re-render the execution wrapper for historical App-exported cases;
                    # frozen attempt-local research instructions stay authoritative.
                    prompt = render_document2_task(
                        case_root=root,
                        node=node.value,
                        run_id=manifest["run_id"],
                        attempt_id=attempt,
                        has_pilot_upstream=(root / "context/pilot_upstream/manifest.json").exists(),
                        document_schema_version=manifest["document_schema_version"],
                    )
                    if retry_error:
                        prompt += (
                            "\n\n上一代 Pilot 研究产物未通过接纳校验："
                            f"{retry_error}\n"
                            "本轮请根据该错误修正结果，重新提交完整正式产物；"
                            "不要改写冻结 input、上游产物或此前审计记录。\n"
                        )
                    (audit / "pilot_sdk_task.md").write_text(prompt, encoding="utf-8")
                else:
                    schema = ISSUE_SCHEMA
                    prompt = _review_prompt(attempt, node.value)
                if phase == "research" and (
                    not (local / "input/context_index/index.json").exists()
                    or not manifest.get("bootstrap_from_global_research")
                ):
                    from doxagent.codex_runtime.context_index import attach_index

                    derived = {}
                    navigation = attach_index(
                        derived,
                        root=f"attempts/{attempt}/audit/context_index",
                        context=_validation_context(root, manifest),
                    )
                    for path, text in derived.items():
                        target = root / path
                        try:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_text(text, encoding="utf-8", newline="")
                        except OSError as exc:
                            navigation = {"warning": f"context_index_unavailable: {exc}"}
                            break
                    prompt += (
                        "\nOptional UTF-8 navigation (full input remains available): "
                        + json.dumps(navigation, ensure_ascii=False)
                    )
                receipt.update(status="running", model=model, effort=effort, turn_id=None)
                receipt[f"{phase}_started_at"] = datetime.now(UTC).isoformat()
                write_json(receipt_path, receipt)
                handle = None
                delivered = False
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        handle = await thread.turn(
                            prompt,
                            cwd=str(root),
                            model=model,
                            effort=ReasoningEffort(effort),
                            output_schema=schema,
                            sandbox=Sandbox.read_only if phase == "review" else sandbox,
                            approval_mode=ApprovalMode.deny_all,
                            summary=ReasoningSummary("auto"),
                        )
                        receipt["turn_id"] = handle.id
                        write_json(receipt_path, receipt)
                        final = await self._stream(handle, audit, receipt, redact)
                    delivered = True
                    if final is not None:
                        (audit / f"{phase}_sdk_reply.txt").write_text(final, encoding="utf-8")
                    self._accept_phase(root, manifest, receipt, final)
                    write_json(receipt_path, receipt)
                except asyncio.CancelledError:
                    if handle is not None:
                        await handle.interrupt()
                    raise
                except Exception as exc:
                    if isinstance(exc, TimeoutError) and handle is not None:
                        try:
                            await asyncio.wait_for(handle.interrupt(), timeout=10)
                        except Exception:
                            pass
                    if phase == "research":
                        if delivered or (
                            isinstance(exc, TimeoutError)
                            and not (local / "output/completion.json").exists()
                        ):
                            raise
                        receipt["delivery_error"] = redact(str(exc))[:2000]
                        self._accept_phase(root, manifest, receipt, None)
                    else:
                        if receipt["output_digest"] != tree_hash(local / "output"):
                            raise ValueError("Pilot review modified formal outputs") from exc
                        count = receipt.get("review_attempts", 0) + 1
                        receipt["review_attempts"] = count
                        if count == 1:
                            try:
                                async with asyncio.timeout(self.timeout_seconds):
                                    handle = await thread.turn(
                                        _review_prompt(attempt, node.value),
                                        cwd=str(root),
                                        model=model,
                                        effort=ReasoningEffort(effort),
                                        output_schema=ISSUE_SCHEMA,
                                        sandbox=Sandbox.read_only,
                                        approval_mode=ApprovalMode.deny_all,
                                        summary=ReasoningSummary("auto"),
                                    )
                                    final = await self._stream(handle, audit, receipt, redact)
                                self._accept_phase(root, manifest, receipt, final)
                            except asyncio.CancelledError:
                                raise
                            except Exception as retry_exc:
                                self._review_unavailable(root, receipt, retry_exc)
                        else:
                            self._review_unavailable(root, receipt, exc)
                    write_json(receipt_path, receipt)
                if frozen != tree_hash(local / "input") + tree_hash(
                    root / "context/pilot_upstream"
                ):
                    raise ValueError("Pilot modified frozen inputs")
            return receipt
        except BaseException as exc:
            # Retain uncertain/running admission for SDK reconciliation on the next call.
            if receipt["status"] != "running":
                receipt["status"] = "failed"
            receipt["error"] = redact(f"{type(exc).__name__}: {exc}")[:2000]
            write_json(receipt_path, receipt)
            raise
        finally:
            render_case_report(root)

    async def _stream(self, handle: Any, audit: Path, receipt: dict, redact: Any) -> str | None:
        from openai_codex.generated.v2_all import (
            ItemCompletedNotification,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
        )

        final = None
        completed = None
        trace = audit / "pilot_process.jsonl"
        async for notification in handle.stream():
            payload = notification.payload
            if isinstance(payload, ItemCompletedNotification) and payload.turn_id == handle.id:
                value = self._record_item(payload.item, trace, receipt, redact)
                if value is not None:
                    final = value
            elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
                if payload.turn_id == handle.id:
                    usage = project_turn_telemetry(
                        items=(),
                        usage=payload.token_usage,
                        duration_ms=None,
                    )
                    _append(
                        trace,
                        {
                            "phase": receipt["phase"],
                            "kind": "usage",
                            "usage": usage.usage.model_dump(),
                        },
                    )
            elif isinstance(payload, TurnCompletedNotification) and payload.turn.id == handle.id:
                completed = payload.turn
        if completed is None:
            raise RuntimeError("SDK stream ended without a completed turn receipt")
        receipt["status"] = "ready" if _value(completed.status) == "completed" else "failed"
        receipt[f"{receipt['phase']}_turn_id"] = handle.id
        receipt[f"{receipt['phase']}_duration_ms"] = completed.duration_ms
        receipt[f"{receipt['phase']}_finished_at"] = datetime.now(UTC).isoformat()
        _append(
            trace,
            {
                "phase": receipt["phase"],
                "kind": "turn",
                "status": _value(completed.status),
                "turn_id": handle.id,
                "duration_ms": completed.duration_ms,
            },
        )
        if receipt["status"] == "failed":
            raise RuntimeError(f"SDK turn {_value(completed.status)}: {completed.error}")
        return final

    def _record_item(self, wrapped: Any, trace: Path, receipt: dict, redact: Any) -> str | None:
        item = getattr(wrapped, "root", wrapped)
        common = {
            "phase": receipt["phase"],
            "generation": receipt["generation"],
            "turn_id": receipt.get("turn_id"),
            "item_id": getattr(item, "id", None),
        }
        kind = getattr(item, "type", "")
        if kind == "agentMessage":
            if _value(getattr(item, "phase", None)) in {"final_answer", "None"}:
                return item.text
            _append(trace, {**common, "kind": "commentary", "text": redact(item.text)})
        elif kind == "reasoning":
            # Public SDK summary only. Never read or persist ReasoningThreadItem.content.
            for summary in getattr(item, "summary", None) or ():
                _append(trace, {**common, "kind": "reasoning_summary", "text": redact(summary)})
        else:
            projected = project_turn_telemetry(items=[wrapped], usage=None, duration_ms=None)
            for event in projected.events:
                value = event.model_dump(mode="json")
                _append(
                    trace,
                    {
                        **common,
                        "kind": "tool",
                        "event": json.loads(redact(json.dumps(value, ensure_ascii=False))),
                    },
                )
        return None

    def _recover_trace(self, turn: Any, audit: Path, receipt: dict, redact: Any) -> str | None:
        final = None
        for item in turn.items:
            value = self._record_item(item, audit / "pilot_process.jsonl", receipt, redact)
            if value is not None:
                final = value
        receipt["status"] = "ready"
        receipt[f"{receipt['phase']}_turn_id"] = turn.id
        return final

    def _accept_phase(self, root: Path, manifest: dict, receipt: dict, final: str | None) -> None:
        local = root / "attempts" / canonical_node_attempt_id(manifest)
        if receipt["phase"] == "research":
            self._validate_completion(root, manifest, sdk_reply=final)
            receipt.update(
                phase="review", status="ready", output_digest=tree_hash(local / "output")
            )
        else:
            if receipt["output_digest"] != tree_hash(local / "output"):
                raise ValueError("Pilot review modified formal outputs")
            if final is None:
                raise ValueError("Pilot review has no structured response")
            review = json.loads(final)
            self._validate_review(review)
            write_json(local / "audit" / "pilot_review.json", review)
            issues = local / "audit" / "pilot_issues.md"
            marker = f"\n\n## SDK 节点复盘 ({receipt['review_turn_id']})\n\n"
            previous = issues.read_text(encoding="utf-8") if issues.exists() else ""
            if marker not in previous:
                with issues.open("a", encoding="utf-8") as stream:
                    stream.write(marker + _review_markdown(review))
            receipt.update(status="completed", completed_at=datetime.now(UTC).isoformat())
            receipt.pop("error", None)

    @staticmethod
    def _validate_review(review: dict) -> None:
        jsonschema.validate(review, ISSUE_SCHEMA)
        if not review["issues"] and not (review["no_issues_reason"] or "").strip():
            raise ValueError("Empty Pilot issues require an explicit no-issues explanation")

    @staticmethod
    def _review_unavailable(root, receipt, exc):
        manifest = read_json(root / "case_manifest.json")
        local = root / "attempts" / canonical_node_attempt_id(manifest)
        if receipt["output_digest"] != tree_hash(local / "output"):
            raise ValueError("Pilot review modified formal outputs") from exc
        review = {
            "task_summary": "Research accepted; independent review unavailable",
            "issues": [],
            "no_issues_reason": "Review failed; absence of issues is not a clean review.",
        }
        write_json(local / "audit/pilot_review.json", review)
        write_json(
            local / "audit/review_unavailable.json",
            {"code": "review_unavailable", "reason": str(exc)[:2000]},
        )
        with (local / "audit/pilot_issues.md").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n\n## Review unavailable\n"
                + str(exc)[:2000]
                + "\nResearch preserved; downstream may continue.\n"
            )
        receipt.update(
            status="completed", completed_at=datetime.now(UTC).isoformat(), review_unavailable=True
        )
        receipt.pop("error", None)

    @staticmethod
    def _validate_completion(root: Path, manifest: dict, sdk_reply=None) -> None:
        from doxagent.workflows.codex_document2.acceptance import accept

        local = root / "attempts" / canonical_node_attempt_id(manifest)
        snapshot = local / "output/accepted.json"
        completion = local / "output/completion.json"
        if snapshot.exists():
            _, _, model = _bootstrap_contract(
                CodexD2Node(manifest["node"]), manifest["document_schema_version"]
            )
            if manifest["node"] == CodexD2Node.O1_OPEN_DISCOVERY.value:
                from doxagent.workflows.codex_document2.schema import OpenDiscoveryResultV21

                OpenDiscoveryResultV21.model_validate(read_json(snapshot))
            else:
                model.model_validate(read_json(snapshot))
            return
        context = _validation_context(root, manifest)
        node = CodexD2Node(manifest["node"])
        file_text = completion.read_text(encoding="utf-8") if completion.exists() else None
        if node is CodexD2Node.O1_OPEN_DISCOVERY:
            from doxagent.workflows.codex_document2.discovery_checkpoint import (
                accept_pilot_discovery,
            )

            accepted = accept_pilot_discovery(
                root, canonical_node_attempt_id(manifest), context, file_text, sdk_reply
            )
        else:
            _, _, model = _bootstrap_contract(node, manifest["document_schema_version"])
            accepted = accept(model, context, file_text=file_text, sdk_reply=sdk_reply)
        if not completion.exists():
            authored = accepted.output.model_dump(mode="json")
            if node is CodexD2Node.O1_OPEN_DISCOVERY:
                authored = {
                    "scan_sha256": accepted.output.checkpoint.scan_sha256,
                    "selection": authored["selection"],
                }
            write_json(completion, authored)
        write_json(snapshot, accepted.output.model_dump(mode="json"))
        write_json(local / "audit/acceptance.json", accepted.metadata())
        write_json(local / "output/orchestration_diagnostics.json", accepted.diagnostics)
        if node is CodexD2Node.O1_OPEN_DISCOVERY:
            write_json(
                local / "output/open_discovery_result.json", accepted.output.model_dump(mode="json")
            )


def _validation_context(root: Path, manifest: dict) -> dict:
    """Respect frozen Pilot upstream precedence without rewriting source inputs."""
    local = root / "attempts" / canonical_node_attempt_id(manifest)
    context = read_json(local / "input/context.json")
    upstream_root = root / "context/pilot_upstream"
    index = upstream_root / "manifest.json"
    if not index.exists():
        return context
    upstream = {}
    for entry in read_json(index)["entries"]:
        node = CodexD2Node(entry["node"])
        filename = (
            "open_discovery_result.json"
            if node is CodexD2Node.O1_OPEN_DISCOVERY
            else "completion.json"
        )
        accepted = upstream_root / node.value / "output/accepted.json"
        upstream[node] = read_json(
            accepted if accepted.exists() else upstream_root / node.value / "output" / filename
        )
        diagnostics_path = upstream_root / node.value / "output/orchestration_diagnostics.json"
        if diagnostics_path.exists():
            context.setdefault("orchestration_diagnostics", []).extend(read_json(diagnostics_path))
    if manifest["node"] == CodexD2Node.O0_SYNTHESIS.value:
        labels = {
            CodexD2Node.O0_CANDIDATE_C1: "c1",
            CodexD2Node.O0_CANDIDATE_C3: "c3",
            CodexD2Node.O0_CANDIDATE_C5: "c5",
            CodexD2Node.O0_CANDIDATE_NARRATIVE: "narrative",
        }
        context["candidate_sets"] = _pilot_candidate_sets_context(
            upstream,
            labels,
            manifest["document_schema_version"],
        )
    if manifest["node"].startswith("d2_o1_"):
        if CodexD2Node.O0_FINALIZATION in upstream:
            context["o0_finalization"] = upstream[CodexD2Node.O0_FINALIZATION]
        discovery = upstream.get(CodexD2Node.O1_OPEN_DISCOVERY)
        if discovery:
            context["open_discovery_scan"] = discovery["checkpoint"]["scan"]
            context["open_discovery_selection"] = discovery["selection"]
        from doxagent.workflows.codex_document2.acceptance import merge_discovery_records

        cumulative = {"late_additions": [], "open_discovery_resolution": []}
        for node in (CodexD2Node.O1_STATE, CodexD2Node.O1_REALIZATION, CodexD2Node.O1_GAPS):
            if node in upstream:
                previous = upstream[node]
                context["canonical_shell"] = previous.get("canonical_shell", previous)
                cumulative = merge_discovery_records(cumulative, previous)
        if any(
            node in upstream
            for node in (
                CodexD2Node.O1_STATE,
                CodexD2Node.O1_REALIZATION,
                CodexD2Node.O1_GAPS,
            )
        ):
            context["open_discovery_late_additions"] = cumulative["late_additions"]
            context["open_discovery_resolution"] = cumulative["open_discovery_resolution"]
    return context


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _append(path: Path, value: dict) -> None:
    value = {"observed_at": datetime.now(UTC).isoformat(), **value}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _review_prompt(attempt: str, node: str) -> str:
    return f"""这是节点 {node} 完成后的独立 Pilot 复盘任务。
保留同 thread 的研究记忆，先读取 attempts/{attempt}/output/ 和
attempts/{attempt}/audit/pilot_issues.md（不存在则忽略），结合实际经历提交结构化复盘。
只反馈实际遇到的问题或困难：bug、任务理解歧义、不太会写/组织的产物、难执行的步骤、
证据或工具能力不足、质量风险。指出具体字段/要求/步骤、表现、影响、采取的处理办法、
是否解决，以及对 prompt/skill/schema/工具/编排的改进建议。不需要每类都写，不编造，
不要把合理研究不确定性一概当成 bug；已绕过或解决的真实困难仍应记录。
severity 是影响等级，不是自动阻塞条件。没有问题时 issues=[] 并明确 no_issues_reason。
task_summary 简要说明做了什么和结果。不要输出隐藏思维链，不要重做研究。
本轮不得修改任何正式产物、input、context 或上游文件；返回匹配指定 schema 的 JSON，
驱动器会将它追加到 pilot_issues.md。"""


def _review_markdown(review: dict) -> str:
    result = [review["task_summary"], ""]
    if not review["issues"]:
        result.append(f"本节点未报告问题：{review['no_issues_reason']}")
    for index, issue in enumerate(review["issues"], 1):
        result += [
            f"### {index}. {issue['category']} / {issue['severity']}",
            "",
            f"位置：{issue['location']}",
            f"表现：{issue['observation']}",
            f"影响：{issue['impact']}",
            f"处理：{issue['workaround']}",
            f"建议：{issue['suggestion']}",
            f"已解决：{issue['resolved']}",
            "",
        ]
    return "\n".join(result) + "\n"


def render_case_report(root: Path) -> Path:
    manifest = read_json(root / "case_manifest.json")
    local = root / "attempts" / canonical_node_attempt_id(manifest)
    audit = local / "audit"
    receipt = read_json(audit / "pilot_sdk_receipt.json")
    trace = audit / "pilot_process.jsonl"
    events = []
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # A crash may leave a partially written final event.
    counts = Counter(e["event"]["item_type"] for e in events if e["kind"] == "tool")
    text = [
        f"# Pilot 节点报告：{manifest['node']}",
        "",
        f"状态：{receipt['status']}；阶段：{receipt['phase']}",
        f"Thread：{receipt.get('thread_id')}；模型：{receipt.get('model')}",
        f"可观察工具事件：{dict(counts)}",
        "",
        "以下是 SDK 可见的 commentary 和公开 reasoning summary，并非完整思维链。",
        "恢复读取可能重复事件，计数是观察记录数。",
        "",
    ]
    research_ms = receipt.get("research_duration_ms")
    review_ms = receipt.get("review_duration_ms")
    if isinstance(research_ms, int) and isinstance(review_ms, int):
        total_ms = research_ms + review_ms
        if total_ms > 0:
            text += [
                "SDK Turn 合计耗时："
                f"{total_ms / 1000:.1f} 秒；研究 {research_ms / 1000:.1f} 秒 "
                f"({research_ms / total_ms:.1%})，复盘 {review_ms / 1000:.1f} 秒 "
                f"({review_ms / total_ms:.1%})。",
                "该比例不含 case 准备与两 Turn 间的程序处理。研究内部阶段需按下方时间线判断。",
                "",
            ]
    visible = [e for e in events if e["kind"] in {"commentary", "reasoning_summary"}]
    if not any(e["kind"] == "reasoning_summary" for e in visible):
        text += ["SDK 本次未提供公开 reasoning summary。", ""]
    for event in visible:
        text += [f"- [{event['phase']}/{event['kind']}] {event['text']}", ""]
    if receipt.get("error"):
        text += [f"执行错误：{receipt['error']}", ""]
    for event in events:
        if event["kind"] == "turn":
            text += [
                f"Turn {event['turn_id']}：{event['phase']} / {event['status']} / "
                f"{event.get('duration_ms')} ms",
                "",
            ]
        elif event["kind"] == "usage":
            text += [f"[{event['phase']}] Token usage：{event['usage']}", ""]
        elif event["kind"] == "tool" and event["event"]["status"].lower() not in {
            "completed",
            "succeeded",
            "success",
            "done",
        }:
            text += [f"工具异常：{event['event']}", ""]
    issues = audit / "pilot_issues.md"
    text += [
        "## Pilot issues",
        "",
        issues.read_text(encoding="utf-8")
        if issues.exists()
        else "尚未提交节点复盘；不能视为没有问题。",
    ]
    report = audit / "pilot_report.md"
    report.write_text("\n".join(text), encoding="utf-8")
    return report


def build_delivery(root: Path, cases: list[Path], *, status: str) -> dict:
    """Only business outputs and curated audit files; never signed config or inputs."""
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    lines = [
        "# Document2 SDK Pilot 交付",
        "",
        f"流程状态：{status}",
        "",
        "节点产物、问题与可观察过程分别封存。未完成复盘的节点不能视为无问题。",
        "",
    ]
    package = root / "pilot_delivery.zip"
    temporary = package.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for case in cases:
            manifest = read_json(case / "case_manifest.json")
            local = case / "attempts" / canonical_node_attempt_id(manifest)
            receipt_path = local / "audit" / "pilot_sdk_receipt.json"
            if receipt_path.exists():
                report = render_case_report(case)
                receipt = read_json(receipt_path)
                lines += [
                    f"## {manifest['node']} ({receipt['status']})",
                    "",
                    report.read_text(encoding="utf-8"),
                    "",
                ]
            else:
                lines += [f"## {manifest['node']}：尚未由 SDK 执行", ""]
            files = list((local / "output").rglob("*"))
            files += [
                local / "audit" / name
                for name in (
                    "pilot_sdk_receipt.json",
                    "pilot_process.jsonl",
                    "pilot_review.json",
                    "pilot_issues.md",
                    "pilot_report.md",
                )
            ]
            for path in files:
                if not path.is_file():
                    continue
                name = f"{manifest['case_id']}/{path.relative_to(local).as_posix()}"
                archive.write(path, name)
                entries.append(
                    {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                )
        report_path = root / "PILOT_REPORT.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        for name in ("source_provenance.json", "PILOT_ANALYSIS.md"):
            attachment = root / name
            if attachment.is_file():
                archive.write(attachment, attachment.name)
                entries.append(
                    {
                        "path": attachment.name,
                        "sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
                    }
                )
        write_json(root / "pilot_delivery_manifest.json", {"status": status, "files": entries})
        archive.write(report_path, report_path.name)
        archive.write(root / "pilot_delivery_manifest.json", "pilot_delivery_manifest.json")
    temporary.replace(package)
    return {
        "status": status,
        "report": str(report_path),
        "artifacts": str(package),
        "manifest": str(root / "pilot_delivery_manifest.json"),
        "cases": len(cases),
    }
