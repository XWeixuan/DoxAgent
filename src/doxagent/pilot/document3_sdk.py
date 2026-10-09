"""Local D3 SDK Worker with per-turn issue review and assistant delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.pilot.sdk_runner import ISSUE_SCHEMA, _review_markdown, read_json, write_json
from doxagent.workflows.codex_document3.timing_v21 import HostTiming


class LocalPilotWorkspace:
    """Async workspace protocol over the same local store used by the Worker."""

    def __init__(self, store):
        self.local = store

    async def read_text(self, *args, **kwargs):
        return self.local.read_text(*args, **kwargs)

    async def write_text(self, *args, **kwargs):
        return self.local.write_text(*args, **kwargs)

    async def inventory(self, *args, **kwargs):
        return self.local.inventory(*args, **kwargs)

    async def publish(self, *args, **kwargs):
        return self.local.publish(*args, **kwargs)


def _protected(root, backup=None):
    manifest = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or (relative.parts[0] == "attempts" and "audit" in relative.parts):
            continue
        with path.open("rb") as stream:
            identity = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest[relative.as_posix()] = identity
        if backup is not None:
            target = backup / identity
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    return manifest


def _redact(text):
    for key, value in os.environ.items():
        if (
            value
            and len(value) >= 12
            and any(k in key.upper() for k in ("SECRET", "TOKEN", "API_KEY", "PASSWORD"))
        ):
            text = text.replace(value, "[REDACTED]")
    return text


def _request_identity(request):
    """Hash the request value, not incidental JSON key insertion order."""
    payload = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _legacy_request_identity(request):
    """Recognize receipts written before canonical request hashing was introduced."""
    from doxagent.workflows.codex_document3.schema import strict_json_schema
    from doxagent.workflows.codex_document3.schema_v21 import TechnicalReceipt

    ordered = request.model_copy(
        update={"output_schema": strict_json_schema(TechnicalReceipt.model_json_schema())}
    )
    if ordered.output_schema != request.output_schema:
        return None
    return hashlib.sha256(ordered.model_dump_json().encode()).hexdigest()


class Document3PilotWorker(LocalPilotWorkspace):
    def __init__(self, store, runtime):
        super().__init__(store)
        self.runtime = runtime
        self.pilot_failed = False

    async def _execute(self, request, cwd, audit, phase):
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        handle = await self.runtime.start(request, cwd)
        write_json(
            audit / f"{phase}_execution.json",
            {
                "thread_id": getattr(handle, "thread_id", None),
                "turn_id": getattr(handle, "turn_id", None),
            },
        )
        raw = getattr(handle, "_handle", None)
        if raw is None or not hasattr(raw, "stream"):
            result = await asyncio.wait_for(handle.run(), request.timeout_seconds)
            write_json(
                audit / f"{phase}_summary.json",
                {
                    "reasoning_summaries": [],
                    "telemetry": result.telemetry.model_dump(mode="json")
                    if result.telemetry
                    else {},
                    "wall_time_ms": int((time.monotonic() - started) * 1000),
                    "started_at": started_at,
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
            return result
        from openai_codex.generated.v2_all import (
            ItemCompletedNotification,
            MessagePhase,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
        )

        from doxagent.codex_worker.sdk_runtime import WorkerTurnResult

        final = fallback = completed = usage = None
        events, summaries = [], []

        async def stream():
            nonlocal final, fallback, completed, usage
            with (audit / f"{phase}_process.jsonl").open("a", encoding="utf8") as output:
                async for notification in raw.stream():
                    payload = notification.payload
                    if (
                        isinstance(payload, ItemCompletedNotification)
                        and payload.turn_id == handle.turn_id
                    ):
                        item = payload.item.root
                        kind = str(getattr(item, "type", ""))
                        if kind == "agentMessage":
                            if item.phase == MessagePhase.final_answer:
                                final = item.text
                            elif item.phase is None:
                                fallback = item.text
                            else:
                                output.write(
                                    json.dumps(
                                        {
                                            "kind": "commentary",
                                            "text": _redact(item.text)[:8000],
                                            "observed_at": datetime.now(UTC).isoformat(),
                                            "elapsed_ms": int((time.monotonic() - started) * 1000),
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                        if kind == "reasoning":
                            # Only provider-exposed summaries, never hidden/raw reasoning.
                            summary = getattr(item, "summary", [])
                            if summary:
                                text = _redact("\n".join(str(x) for x in summary))[:8000]
                                summaries.append(text)
                                output.write(
                                    json.dumps(
                                        {
                                            "kind": "reasoning_summary",
                                            "text": text,
                                            "observed_at": datetime.now(UTC).isoformat(),
                                            "elapsed_ms": int((time.monotonic() - started) * 1000),
                                        },
                                        ensure_ascii=False,
                                    )
                                    + "\n"
                                )
                        projected = project_turn_telemetry(
                            items=[payload.item], usage=None, duration_ms=None
                        )
                        for event in projected.events:
                            event.sequence = len(events)
                            event.summary = _redact(event.summary)
                            events.append(event)
                            output.write(
                                json.dumps(
                                    {
                                        **event.model_dump(mode="json"),
                                        "observed_at": datetime.now(UTC).isoformat(),
                                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                        output.flush()
                    elif (
                        isinstance(payload, ThreadTokenUsageUpdatedNotification)
                        and payload.turn_id == handle.turn_id
                    ):
                        usage = payload.token_usage
                    elif (
                        isinstance(payload, TurnCompletedNotification)
                        and payload.turn.id == handle.turn_id
                    ):
                        completed = payload.turn

        try:
            await asyncio.wait_for(stream(), request.timeout_seconds)
        except BaseException:
            await handle.interrupt()
            raise
        if completed is None:
            raise RuntimeError("SDK stream ended without completion")
        telemetry = project_turn_telemetry(items=(), usage=usage, duration_ms=completed.duration_ms)
        telemetry.events = events[-256:]
        telemetry.mcp_call_count = sum(e.item_type == "mcp_tool_call" for e in events)
        telemetry.command_call_count = sum(e.item_type == "command" for e in events)
        telemetry.file_change_count = sum(e.item_type == "file_change" for e in events)
        telemetry.subagent_call_count = sum(e.item_type == "subagent" for e in events)
        telemetry.failures = [
            f"{e.name}:{e.status}"
            for e in events
            if e.status.lower() not in {"completed", "succeeded", "done"}
        ]
        write_json(
            audit / f"{phase}_summary.json",
            {
                "reasoning_summaries": summaries,
                "telemetry": telemetry.model_dump(mode="json"),
                "wall_time_ms": int((time.monotonic() - started) * 1000),
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return WorkerTurnResult(
            handle.thread_id,
            completed.id,
            completed.status.value,
            final or fallback,
            str(completed.error) if completed.error else None,
            telemetry,
        )

    async def _recover_research(self, cwd, audit):
        from doxagent.codex_worker.sdk_runtime import WorkerTurnResult

        path = audit / "research_execution.json"
        execution = read_json(path) if path.exists() else {}
        if not execution.get("thread_id") or not execution.get("turn_id"):
            raise RuntimeError("Uncertain Pilot SDK admission; inspect thread before retry")
        thread = await self.runtime._client.thread_resume(execution["thread_id"], cwd=str(cwd))
        snapshot = await thread.read(include_turns=True)
        turn = next((t for t in snapshot.thread.turns if t.id == execution["turn_id"]), None)
        status = str(getattr(getattr(turn, "status", None), "value", ""))
        if status not in {"completed", "failed", "interrupted"}:
            raise RuntimeError("Previous Pilot SDK turn is unsettled; continue after it settles")
        final = None
        for item in turn.items:
            value = getattr(item, "root", item)
            if getattr(value, "type", None) == "agentMessage":
                phase = str(getattr(getattr(value, "phase", None), "value", ""))
                if phase in {"final_answer", ""}:
                    final = value.text
        telemetry = project_turn_telemetry(
            items=turn.items, usage=None, duration_ms=turn.duration_ms
        )
        write_json(
            audit / "research_summary.json",
            {
                "recovered": True,
                "reasoning_summaries": [],
                "telemetry": telemetry.model_dump(mode="json"),
            },
        )
        return WorkerTurnResult(
            execution["thread_id"],
            turn.id,
            status,
            final,
            str(turn.error) if turn.error else None,
            telemetry,
        )

    async def run(self, request):
        cwd = self.local.ensure_run(request.run_id)
        audit = self.local.ensure_attempt(request.run_id, request.attempt_id) / "audit"
        audit.mkdir(exist_ok=True)
        receipt = audit / "pilot_sdk_receipt.json"
        signature = _request_identity(request)
        record = (
            read_json(receipt)
            if receipt.exists()
            else {
                "request_hash": signature,
                "node": request.node.value,
                "model": request.model,
                "effort": request.effort,
                "cutoff_at": request.cutoff_at.isoformat(),
            }
        )
        if record["request_hash"] != signature:
            if record["request_hash"] != _legacy_request_identity(request):
                raise ValueError("Pilot durable request identity changed")
            record["request_hash"] = signature
            write_json(receipt, record)
        if record.get("phase") == "complete":
            self.pilot_failed |= bool(record.get("review_error"))
            return WorkerJob.model_validate(record["job"])
        if record.get("phase") == "research_running":
            result = await self._recover_research(cwd, audit)
            job = WorkerJob(
                job_id=request.attempt_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="succeeded" if result.status == "completed" else "failed",
                thread_id=result.thread_id,
                turn_id=result.turn_id,
                final_response=result.final_response,
                error_message=result.error_message,
                telemetry=result.telemetry,
            )
            record.update(phase="review", job=job.model_dump(mode="json"))
            write_json(receipt, record)
        if "job" not in record:
            record["phase"] = "research_running"
            write_json(receipt, record)
            journal = f"attempts/{request.attempt_id}/audit/pilot_issues.md"
            prompt = request.prompt + (
                f"\nThis is a local SDK Pilot. In addition to declared business outputs, "
                f"you may append concise observed difficulties to {journal} while working. "
                "Record actual bugs, unclear task requirements, writing/organization difficulties, "
                "execution difficulties, evidence limitations and quality risks, with location, "
                "impact and workaround; do not invent issues or reveal private chain of thought. "
                "Keep the business output/technical receipt contract unchanged."
            )
            try:
                result = await self._execute(
                    request.model_copy(update={"prompt": prompt}), cwd, audit, "research"
                )
                job = WorkerJob(
                    job_id=request.attempt_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    status="succeeded" if result.status == "completed" else "failed",
                    thread_id=result.thread_id,
                    turn_id=result.turn_id,
                    final_response=result.final_response,
                    error_message=result.error_message,
                    telemetry=result.telemetry,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                job = WorkerJob(
                    job_id=request.attempt_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    status="failed",
                    error_message=str(exc),
                )
            record.update(phase="review", job=job.model_dump(mode="json"))
            write_json(receipt, record)
        job = WorkerJob.model_validate(record["job"])
        audit_timing = HostTiming()
        audit_timing.step("audit_prepare")
        audit_started = time.monotonic()
        backup = cwd.parent / ".audit_recovery"
        protected = _protected(cwd, backup)
        record.setdefault("host_metrics", {})["audit_prepare_ms"] = (
            time.monotonic() - audit_started
        ) * 1000
        review = None
        audit_timing.step("audit_sdk")
        try:
            result = await self._execute(
                request.model_copy(
                    update={
                        "thread_id": job.thread_id,
                        "read_only": True,
                        "data_mcp_enabled": False,
                        "o4_operations_enabled": False,
                        "output_schema": ISSUE_SCHEMA,
                        "prompt": f"这是 {request.node.value} 任务结束后的独立 Pilot 复盘。"
                        f"读取产物和 attempts/{request.attempt_id}/audit/pilot_issues.md。"
                        "只汇报实际 bug、理解歧义、写作/组织困难、执行困难、证据限制和质量风险；"
                        "具体说明位置、影响、处理、建议、是否已解决。无问题明确说明，不编造，不复述隐藏思维链。"
                        "不继续研究/调用研究工具/改文件。只返回结构化反馈，宿主追加 issue 文件。",
                    }
                ),
                cwd,
                audit,
                "review",
            )
            if result.status != "completed":
                raise RuntimeError(result.error_message or result.status)
            review = json.loads(result.final_response)
            jsonschema.validate(review, ISSUE_SCHEMA)
            if not review["issues"] and not (review["no_issues_reason"] or "").strip():
                raise ValueError("empty issues require an explicit reason")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            review = {
                "task_summary": "Pilot review unavailable",
                "issues": [],
                "no_issues_reason": "Not evaluated; review failed.",
            }
            record["review_error"] = str(exc)
            self.pilot_failed = True
        finally:
            audit_timing.step("audit_restore")
            restore_started = time.monotonic()
            after = _protected(cwd)
            if after != protected:
                for path in after.keys() - protected.keys():
                    (cwd / path).unlink()
                for path, identity in protected.items():
                    if after.get(path) != identity:
                        source = backup / identity
                        with source.open("rb") as stream:
                            if hashlib.file_digest(stream, "sha256").hexdigest() != identity:
                                raise ValueError("audit recovery copy checksum mismatch")
                        target = cwd / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.is_symlink():
                            target.unlink()
                        shutil.copyfile(source, target)
                record["review_error"] = "audit changed protected files; restored"
                self.pilot_failed = True
            record["host_metrics"]["audit_restore_ms"] = (time.monotonic() - restore_started) * 1000
        audit_timing.step("receipt_write")
        receipt_started = time.monotonic()
        write_json(audit / "pilot_review.json", review)
        with (audit / "pilot_issues.md").open("a", encoding="utf8") as issues:
            issues.write("\n## 节点完成后的复盘\n\n" + _review_markdown(review))
            if record.get("review_error"):
                issues.write(f"\nHost audit failure: {_redact(record['review_error'])}\n")
        record["phase"] = "complete"
        write_json(receipt, record)
        record["host_metrics"]["receipt_write_ms"] = (time.monotonic() - receipt_started) * 1000
        record.setdefault("host_spans", []).extend(audit_timing.finish())
        write_json(receipt, record)
        return job


def build_document3_delivery(root: Path) -> dict:
    """Rebuild reports and an artifact-only ZIP without touching models/databases."""
    root = root.resolve()
    records = []
    report = [
        "# D3 SDK Pilot 过程与问题",
        "",
        "仅汇总可观测 SDK 事件、公开 reasoning summary 与节点复盘；没有隐藏思维链。",
        "",
    ]
    for path in sorted((root / "workspaces").glob("*/attempts/*/audit/pilot_sdk_receipt.json")):
        receipt = read_json(path)
        records.append({"path": path.relative_to(root).as_posix(), **receipt})
        report += [
            f"## {path.parent.parent.parent.parent.name} / {path.parent.parent.name}",
            "",
            f"研究状态：{receipt.get('job', {}).get('status', 'unfinished')}；"
            f"阶段：{receipt.get('phase')}",
            "",
        ]
        for filename in ("research_summary.json", "review_summary.json"):
            summary = path.parent / filename
            if summary.exists():
                value = read_json(summary)
                telemetry = value.get("telemetry", {})
                report += [
                    f"### {filename}",
                    "",
                    f"时长(ms)：{value.get('wall_time_ms', telemetry.get('sdk_duration_ms'))}；"
                    f"MCP：{telemetry.get('mcp_call_count', 0)}；"
                    f"命令：{telemetry.get('command_call_count', 0)}",
                    "",
                    "Usage：" + json.dumps(telemetry.get("usage", {}), ensure_ascii=False),
                    "",
                ]
                summaries = value.get("reasoning_summaries", [])
                report += summaries or ["SDK 未提供公开 reasoning summary。"]
                report += ["", "可观测步骤（完整记录见对应 process.jsonl）：", ""]
                report += [
                    f"- {e['name']} / {e['status']}: {e.get('summary', '')}"
                    for e in telemetry.get("events", [])[:20]
                ]
                report.append("")
        issues = path.parent / "pilot_issues.md"
        if issues.exists():
            report += [issues.read_text("utf8"), ""]
    status = (
        read_json(root / "pilot_result.json")
        if (root / "pilot_result.json").exists()
        else {"status": "incomplete"}
    )
    write_json(root / "pilot_delivery.json", {"result": status, "nodes": records})
    (root / "PILOT_REPORT.md").write_text(_redact("\n".join(report)), encoding="utf8")
    bundle = root / "pilot_artifacts.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        paths = [root / "PILOT_REPORT.md", root / "pilot_delivery.json"]
        if (root / "PILOT_ANALYSIS.md").is_file():
            paths.append(root / "PILOT_ANALYSIS.md")
        if (root / "pilot_source_provenance.json").is_file():
            paths.append(root / "pilot_source_provenance.json")
        paths.extend((root / "pilot_recovery").glob("*.json"))
        paths += list((root / "workspaces").rglob("*"))
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            # Avoid frozen source copies, credentials, SQLite files and local MCP controls.
            if relative.parts[0] == "workspaces" and not (
                "output" in relative.parts or "audit" in relative.parts
            ):
                continue
            if path.suffix in {".db", ".sqlite", ".token"}:
                continue
            archive.writestr(relative.as_posix(), _redact(path.read_text("utf8")))
    return {
        "report": str(root / "PILOT_REPORT.md"),
        "delivery": str(root / "pilot_delivery.json"),
        "artifacts": str(bundle),
        "node_count": len(records),
        "built_at": datetime.now(UTC).isoformat(),
    }
