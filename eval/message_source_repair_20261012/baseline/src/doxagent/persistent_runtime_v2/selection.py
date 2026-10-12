"""One immutable closed-cycle selection per ticker with nonblocking old-bundle fallback."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.schema import (
    CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
    CodexPersistentRuntimeAgentRole,
    CodexPersistentRuntimeNode,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.semantic_clock import semantic_day

from .journal import RuntimeJournal, digest, encode
from .schema import RuntimeVersionPin, TradeRecord, strict_json_schema
from .trade_output import TradeOutputService
from .worker_receipts import ReceiptWorker


class SelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_id: str
    candidate_id: str | None
    reason: str


class WeekendSelection:
    def __init__(
        self, settings: Any, runtime: Any, journal: RuntimeJournal, *, judge: Any = None
    ) -> None:
        self.settings, self.runtime, self.journal, self.judge = settings, runtime, journal, judge

    async def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.journal.get("selection_snapshot", task["id"])
        if snapshot is None:
            raise ValueError("selection snapshot not frozen")
        candidates = [
            self.journal.get("candidates", identity) for identity in snapshot["candidate_ids"]
        ]
        candidates = [
            item for item in candidates if item and item.get("origin_trade_eligible", True)
        ]
        released = self.journal.get("trade_intents", f"trade:{task['id']}")
        if released and released["status"] in {"READY", "OUTPUT_RECORDED", "UNKNOWN"}:
            prior = task["receipt"].get("selection_result")
            if prior and prior.get("candidate_id"):
                candidate = self.journal.get("candidates", prior["candidate_id"])
                candidate["status"] = "RELEASED"
                self.journal.set("candidates", prior["candidate_id"], candidate)
                return dict(prior)
        day = date.fromisoformat(task["inputs"]["day"])
        expired = semantic_day(self.journal.clock()) != day
        result = task["receipt"].get("selection_result")
        if not result and (expired or not candidates):
            result = SelectionResult(
                selection_id=task["id"], candidate_id=None, reason="EXPIRED" if expired else "EMPTY"
            ).model_dump()
        if result is None:
            payload = task["receipt"].get("judgment_inputs")
            if payload is None:
                loader = self.runtime.input_snapshot_loader
                active = loader(task["ticker"])
                if active is None or active.index is None or active.projection is None:
                    raise ValueError("no usable bundle for final selection")
                case = self.runtime.repository.get_case(candidates[0]["case_id"])
                current_case = case.model_copy(
                    update={
                        "version_pin": RuntimeVersionPin(
                            activation_revision_id=active.activation_revision_id,
                            document1_run_id=active.document1_run_id,
                            document2_run_id=active.document2_run_id,
                            event_library_root=active.event_library_root,
                            event_library_version=active.index.version,
                            policy_set_version=active.projection.policy_set_version,
                            runtime_projection_version=active.projection.policy_set_version,
                            provisional_snapshot_version=0,
                        )
                    }
                )
                if self.judge:
                    context = {"projection": active.projection.model_dump(mode="json")}
                else:
                    prepared = await self.runtime._w3_agent._context.load(current_case)
                    context = {
                        "document1": prepared.document1,
                        "document2": prepared.document2.model_dump(mode="json"),
                        "policy_set": prepared.policy_set.model_dump(mode="json"),
                        "reference_view": prepared.reference_view,
                    }
                maintenance = self.journal.get_task(f"maintain:{task['inputs']['sweep_id']}")
                payload = {
                    "selection_id": task["id"],
                    "candidates": candidates,
                    "context": context,
                    "active_revision": active.activation_revision_id,
                    "projection": active.projection.model_dump(mode="json"),
                    "fallback": not maintenance or maintenance["status"] != "SUCCEEDED",
                    "realtime_outputs": [
                        item
                        for item in self.journal.values("trade_intents")
                        if item["ticker"] == task["ticker"]
                        and item["release_semantic_day"] == day.isoformat()
                        and item["status"] not in {"EXPIRED_SEMANTIC_DAY", "DUPLICATE_POLICY"}
                    ],
                }
                self.journal.checkpoint(task, judgment_inputs=payload)
            if self.judge:
                result = (await self.judge(payload)).model_dump()
            else:
                result = (await self._judge(task, payload)).model_dump()
        parsed = SelectionResult.model_validate(result)
        if parsed.selection_id != task["id"] or (
            parsed.candidate_id is not None
            and parsed.candidate_id not in {item["candidate_id"] for item in candidates}
        ):
            raise ValueError("selection returned an out-of-snapshot identity")
        self.journal.checkpoint(task, selection_result=result)
        # Freeze the business decision and close all non-selected candidates together.
        with self.journal.transaction() as db:
            self.journal.fence(db, task)
            for candidate in candidates:
                candidate["selection_id"] = task["id"]
                candidate["status"] = (
                    "EXPIRED"
                    if expired
                    else "SELECTED"
                    if candidate["candidate_id"] == parsed.candidate_id
                    else "NOT_SELECTED"
                )
                db.execute(
                    "UPDATE runtime_values SET payload=? WHERE namespace='candidates' AND key=?",
                    (encode(candidate), candidate["candidate_id"]),
                )
        if not expired and parsed.candidate_id:
            candidate = next(
                item for item in candidates if item["candidate_id"] == parsed.candidate_id
            )
            case = self.runtime.repository.get_case(candidate["case_id"])
            trade = TradeRecord.model_validate(candidate["trade"])
            active = self.runtime.input_snapshot_loader(task["ticker"])
            valid = active and active.projection
            if trade.executed_policy_id and valid:
                valid = any(
                    policy.policy_id == trade.executed_policy_id
                    and policy.activation_revision == trade.activation_revision
                    for policy in active.projection.policies
                )
            if valid:
                from .fencing import write_scope

                with write_scope("task", task, self.journal.clock):
                    outcome = TradeOutputService(self.journal).record(
                        case, trade, release_day=day, selection_id=task["id"]
                    )
            else:
                outcome = "RELEASE_REJECTED"
            candidate["status"] = "RELEASED" if outcome in {"READY", "OUTPUT_RECORDED"} else outcome
            self.journal.set("candidates", parsed.candidate_id, candidate)
        return dict(result)

    async def _judge(self, task: dict[str, Any], payload: dict[str, Any]) -> SelectionResult:
        run_id = "weekend-selection-" + digest(task["id"])[:24]
        prompt = task["receipt"].get("skill")
        if prompt is None:
            execution = self.journal.get("execution", "active")
            assets = self.runtime.execution_bundles.get(execution)["assets"] if execution else {}
            prompt = assets.get("w3/skills/weekend_sweep.md")
            if prompt is None:
                prompt = (
                    Path(self.settings.persistent_runtime_v2_w3_prompt_root)
                    / "skills/weekend_sweep.md"
                ).read_text(encoding="utf-8")
            self.journal.checkpoint(task, skill=prompt)
        worker = HttpCodexWorkerClient(
            self.settings.codex_worker_base_url,
            self.settings.codex_worker_bearer_token,
            capability_secret=self.settings.codex_capability_secret,
        )
        try:
            await worker.write_text(run_id, "AGENTS.md", prompt)
            await worker.write_text(run_id, "context/selection.json", encode(payload))
            job = await ReceiptWorker(
                worker, self.journal, task["id"], control_epoch=task["inputs"].get("control_epoch")
            ).run(
                WorkerRunRequest(
                    workflow_version=CODEX_PERSISTENT_RUNTIME_W3_WORKFLOW_VERSION,
                    research_lane=ResearchLane.PERSISTENT_RUNTIME,
                    run_id=run_id,
                    ticker=task["ticker"],
                    node=CodexPersistentRuntimeNode.W3,
                    agent_role=CodexPersistentRuntimeAgentRole.W3,
                    attempt_id="selection",
                    cutoff_at=datetime.fromisoformat(task["due_at"]),
                    prompt=f"{prompt}\nRead context/selection.json. selection_id={task['id']}",
                    output_schema=strict_json_schema(SelectionResult.model_json_schema()),
                    model=self.settings.persistent_runtime_v2_w3_model,
                    model_provider=self.settings.codex_model_provider,
                    effort=self.settings.persistent_runtime_v2_w3_reasoning_effort,
                    read_only=True,
                    data_mcp_enabled=False,
                    allow_subagents=False,
                    max_subagents=0,
                    timeout_seconds=self.settings.persistent_runtime_v2_w3_timeout_seconds,
                )
            )
            if job.status != "succeeded" or not job.final_response:
                raise RuntimeError(job.error_code or "weekend selection failed")
            return SelectionResult.model_validate_json(job.final_response)
        finally:
            await worker.aclose()
