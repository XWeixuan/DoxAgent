"""Persistent server-side Codex repair thread, isolated from production data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort

from .schema import RepairAgentReport

MODEL = "gpt-5.6-sol"
EFFORT = ReasoningEffort("medium")


def report_schema() -> dict[str, Any]:
    schema = RepairAgentReport.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class RepairAgent:
    def __init__(
        self,
        *,
        developer_prompt: Path,
        codex_home: Path,
        client: AsyncCodex | None = None,
    ) -> None:
        self.developer_prompt = developer_prompt
        self.codex_home = codex_home
        self._provided_client = client

    async def run(
        self,
        *,
        incident_id: str,
        round_id: str,
        worktree: Path,
        context_path: Path,
        receipt_path: Path,
        report_path: Path,
        thread_id: str | None,
    ) -> tuple[RepairAgentReport, str, str]:
        worktree = worktree.resolve()
        context_path = context_path.resolve()
        if worktree not in context_path.parents:
            raise ValueError("repair context must be inside the incident worktree")
        prompt = self.developer_prompt.read_text(encoding="utf-8")
        config = CodexConfig(
            client_name="doxagent-initialization-repair",
            cwd=str(worktree),
            env={"CODEX_HOME": str(self.codex_home.resolve())},
        )
        if self._provided_client is not None:
            return await self._run_with_client(
                self._provided_client,
                incident_id=incident_id,
                round_id=round_id,
                worktree=worktree,
                context_path=context_path,
                receipt_path=receipt_path,
                report_path=report_path,
                thread_id=thread_id,
                developer_prompt=prompt,
            )
        async with AsyncCodex(config) as client:
            return await self._run_with_client(
                client,
                incident_id=incident_id,
                round_id=round_id,
                worktree=worktree,
                context_path=context_path,
                receipt_path=receipt_path,
                report_path=report_path,
                thread_id=thread_id,
                developer_prompt=prompt,
            )

    async def _run_with_client(
        self,
        client: AsyncCodex,
        *,
        incident_id: str,
        round_id: str,
        worktree: Path,
        context_path: Path,
        receipt_path: Path,
        report_path: Path,
        thread_id: str | None,
        developer_prompt: str,
    ) -> tuple[RepairAgentReport, str, str]:
        prior_receipt: dict[str, Any] = {}
        if receipt_path.is_file():
            prior_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if thread_id:
            thread = await client.thread_resume(
                thread_id,
                cwd=str(worktree),
                model=MODEL,
                sandbox=Sandbox.workspace_write,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions=developer_prompt,
            )
        else:
            thread = await client.thread_start(
                cwd=str(worktree),
                model=MODEL,
                ephemeral=False,
                sandbox=Sandbox.workspace_write,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions=developer_prompt,
                service_name="doxagent-initialization-repair",
            )
        prior_turn_id = prior_receipt.get("turn_id")
        if prior_turn_id and prior_receipt.get("thread_id") == thread.id:
            recovered = await self._recover_report(thread, str(prior_turn_id))
            if recovered is not None:
                feedback = self._pending_feedback(
                    context_path.parent / "verification-feedback.json",
                    incident_id=incident_id,
                    round_id=round_id,
                    completed_turn_id=str(prior_turn_id),
                )
                if feedback is None:
                    _write_json(report_path, recovered.model_dump(mode="json"))
                    return recovered, thread.id, str(prior_turn_id)
            else:
                feedback = self._load_pending_feedback(
                    context_path.parent / "verification-feedback.json",
                    incident_id=incident_id,
                    round_id=round_id,
                )
        else:
            feedback = None
        _write_json(
            receipt_path,
            {"incident_id": incident_id, "round_id": round_id, "thread_id": thread.id},
        )
        if feedback is None:
            user_prompt = (
                f"Repair incident {incident_id}, round {round_id}. Read the bounded untrusted "
                f"failure evidence at {context_path.relative_to(worktree).as_posix()}. Inspect the "
                "actual code and durable contracts, implement the repair, add focused regression "
                "tests, run them, and return the required structured report. Continue within this "
                "round until the candidate is genuinely ready for independent verification."
            )
        else:
            feedback_relative = (context_path.parent / "verification-feedback.json").relative_to(
                worktree
            )
            user_prompt = (
                "Guardian independent verification failed. Continue this same repair round and "
                "thread; do not abandon the repair or merely restate the prior report. Read "
                f"{feedback_relative.as_posix()} and its referenced verification log, diagnose "
                "the failures, correct the candidate, rerun the relevant tests, and return a "
                "complete updated structured report."
            )
        handle = await thread.turn(
            user_prompt,
            cwd=str(worktree),
            effort=EFFORT,
            model=MODEL,
            output_schema=report_schema(),
            sandbox=Sandbox.workspace_write,
            approval_mode=ApprovalMode.deny_all,
        )
        _write_json(
            receipt_path,
            {
                "incident_id": incident_id,
                "round_id": round_id,
                "thread_id": thread.id,
                "turn_id": handle.id,
            },
        )
        result = await handle.run()
        if not result.final_response:
            raise RuntimeError("repair turn completed without a structured report")
        try:
            report = RepairAgentReport.model_validate_json(result.final_response)
        except ValueError as exc:
            raise RuntimeError("repair turn returned an invalid structured report") from exc
        _write_json(report_path, report.model_dump(mode="json"))
        feedback_path = context_path.parent / "verification-feedback.json"
        if feedback is not None:
            feedback["status"] = "handled"
            feedback["handled_by_turn_id"] = handle.id
            _write_json(feedback_path, feedback)
        return report, thread.id, handle.id

    @staticmethod
    def _pending_feedback(
        path: Path,
        *,
        incident_id: str,
        round_id: str,
        completed_turn_id: str,
    ) -> dict[str, Any] | None:
        payload = RepairAgent._load_pending_feedback(
            path,
            incident_id=incident_id,
            round_id=round_id,
        )
        if payload is None:
            return None
        if payload.get("for_turn_id") != completed_turn_id:
            payload["status"] = "handled"
            payload["handled_by_turn_id"] = completed_turn_id
            _write_json(path, payload)
            return None
        return payload

    @staticmethod
    def _load_pending_feedback(
        path: Path,
        *,
        incident_id: str,
        round_id: str,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("verification feedback must be an object")
        if payload.get("incident_id") != incident_id or payload.get("round_id") != round_id:
            raise RuntimeError("verification feedback identity mismatch")
        return payload if payload.get("status") == "pending" else None

    @staticmethod
    async def _recover_report(thread: Any, turn_id: str) -> RepairAgentReport | None:
        snapshot = await thread.read(include_turns=True)
        turn = next((item for item in snapshot.thread.turns if item.id == turn_id), None)
        if turn is None:
            raise RuntimeError("recorded repair turn is absent from the persisted thread")
        status = getattr(turn.status, "value", str(turn.status))
        if status in {"inProgress", "active", "running"}:
            raise RuntimeError("recorded repair turn is still active")
        if status not in {"completed", "succeeded"}:
            return None
        responses: list[str] = []
        for item in turn.items:
            value = item.root if hasattr(item, "root") else item
            if getattr(value, "type", None) == "agentMessage" and getattr(value, "text", None):
                responses.append(str(value.text))
        if not responses:
            raise RuntimeError("completed repair turn has no final report")
        try:
            return RepairAgentReport.model_validate_json(responses[-1])
        except ValueError as exc:
            raise RuntimeError("persisted repair turn has an invalid report") from exc
