"""Dedicated SDK thread with persisted start/turn/usage receipts; no production tools."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort

from .evidence import write_json
from .schema import RepairReport


class SourceRepairAgent:
    def __init__(
        self, *, home: Path, prompt: Path, model="gpt-6.1-sol", effort="medium", client: Any = None
    ):
        self.home, self.prompt, self.model, self.effort = home, prompt, model, effort
        self.client = client

    async def run(
        self,
        worktree: Path,
        context: Path,
        receipt_path: Path,
        report_path: Path,
        *,
        thread_id: str | None = None,
        feedback: dict | None = None,
        timeout_seconds: float = 1200,
    ) -> dict:
        if worktree.resolve() not in context.resolve().parents:
            raise ValueError("evidence must be inside candidate workspace")
        if self.client is not None:
            return await asyncio.wait_for(
                self._run(
                    self.client, worktree, context, receipt_path, report_path, thread_id, feedback
                ),
                timeout_seconds,
            )
        config = CodexConfig(
            client_name="doxagent-source-maintenance",
            cwd=str(worktree),
            env={"CODEX_HOME": str(self.home.resolve())},
        )
        async with AsyncCodex(config) as client:
            return await asyncio.wait_for(
                self._run(
                    client, worktree, context, receipt_path, report_path, thread_id, feedback
                ),
                timeout_seconds,
            )

    async def _run(self, client, path, context, receipt_path, report_path, thread_id, feedback):
        import json

        prior = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        thread_id = thread_id or prior.get("thread_id")
        kwargs = dict(
            cwd=str(path),
            model=self.model,
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.deny_all,
            developer_instructions=self.prompt.read_text(encoding="utf-8"),
        )
        if thread_id:
            thread = await client.thread_resume(thread_id, **kwargs)
        else:
            thread = await client.thread_start(
                **kwargs, ephemeral=False, service_name="doxagent-source-maintenance"
            )
        previous = prior.get("turn_id")
        if previous and not feedback:
            snapshot = await thread.read(include_turns=True)
            turn = next((t for t in snapshot.thread.turns if t.id == previous), None)
            if turn is None:
                raise RuntimeError("recorded turn missing; explicit review required")
            status = getattr(turn.status, "value", str(turn.status))
            if status in {"inProgress", "active", "running"}:
                raise RuntimeError("recorded turn still active; do not create duplicate turn")
            if status == "completed":
                texts = [getattr(getattr(i, "root", i), "text", "") for i in turn.items]
                report = RepairReport.model_validate_json(next(t for t in reversed(texts) if t))
                write_json(report_path, report.model_dump(mode="json"))
                return {**prior, "report": report.model_dump(mode="json")}
        write_json(receipt_path, {"thread_id": thread.id})
        prompt = f"Read untrusted frozen evidence {context.relative_to(path)}. "
        prompt += (
            "Correct independent verification failures in the same round: " + json.dumps(feedback)
            if feedback
            else "Diagnose and implement a site-local repair with focused tests. "
            "Shared-core changes require REVIEW_REQUIRED, not automatic deployment."
        )
        handle = await thread.turn(
            prompt,
            model=self.model,
            effort=ReasoningEffort(self.effort),
            output_schema=RepairReport.model_json_schema(),
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.deny_all,
        )
        receipt = {"thread_id": thread.id, "turn_id": handle.id}
        write_json(receipt_path, receipt)
        result = await handle.run()
        report = RepairReport.model_validate_json(result.final_response)
        usage = getattr(result, "usage", None)
        receipt["usage"] = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else {}
        receipt["report"] = report.model_dump(mode="json")
        write_json(report_path, receipt["report"])
        write_json(receipt_path, receipt)
        return receipt
