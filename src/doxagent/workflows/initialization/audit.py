# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.initialization.shared import *


class InitializationAuditMixin:
    def _with_retry_audit(
        self,
        result: AgentResult,
        previous_failure: AgentResult,
    ) -> AgentResult:
        payload = dict(result.payload)
        payload["retry_audit"] = {
            "retried": True,
            "attempt_count": 2,
            "retry_reason": "previous_agent_result_retryable_failure",
            "previous_failure": self._agent_failure_audit(previous_failure),
        }
        return result.model_copy(update={"payload": payload}, deep=True)

    def _with_failure_audit(self, result: AgentResult) -> AgentResult:
        if result.status is not ResultStatus.FAILED:
            return result
        payload = dict(result.payload)
        payload["failure_audit"] = self._agent_failure_audit(result)
        return result.model_copy(update={"payload": payload}, deep=True)

    def _agent_failure_audit(self, result: AgentResult | None) -> dict[str, Any]:
        if result is None:
            return {
                "status": "unknown",
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "details": {},
            }
        error = result.error
        return {
            "status": result.status.value,
            "agent_name": result.agent_name.value,
            "task_id": result.task_id,
            "error_code": error.code if error is not None else None,
            "error_message": error.message if error is not None else None,
            "retryable": error.retryable if error is not None else False,
            "details": error.details if error is not None else {},
        }

    def _write_working_memory(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
        content_type: str,
    ) -> None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=result.agent_name,
                content_type=content_type,
                payload={
                    "status": result.status.value,
                    "payload": result.payload,
                    "patch_ids": [patch.patch_id for patch in result.proposed_patches],
                    "objection_ids": [item.objection_id for item in result.objections],
                    "delegation_ids": [item.delegation_id for item in result.delegations],
                    "tool_calls": [item.model_dump(mode="json") for item in result.tool_calls],
                    "tool_usage_audit": result.payload.get("tool_usage_audit", {}),
                    "market_evidence_snapshot": result.payload.get(
                        "market_evidence_snapshot",
                        {},
                    ),
                    "acceptance_audit": self._acceptance_audit(
                        checkpoint,
                        result,
                        parse_status="ok",
                        schema_status="ok",
                        write_status="ok",
                    ),
                    "skill_versions": result.payload.get("skill_versions", {}),
                    "model_audit": result.payload.get("model_audit"),
                },
                evidence_refs=result.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write working memory entry for "
                f"{checkpoint.next_node.value if checkpoint.next_node else 'unknown'} "
                f"{result.agent_name.value}: {exc}"
            ) from exc

    def _write_patch_audit_working_memory(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        content_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type=content_type,
                payload={
                    **payload,
                    "patch_ids": [patch.patch_id],
                    "patch_target": patch.target.model_dump(mode="json"),
                    "patch_rationale": patch.rationale,
                },
                evidence_refs=patch.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write patch audit for {content_type}: {exc}"
            ) from exc

    def _write_agent_acceptance_failure(
        self,
        checkpoint: WorkflowCheckpoint,
        task: AgentTask,
        result: AgentResult,
        *,
        event_code: Literal["parse_failed", "schema_failed"],
        message: str,
        expected_schema: str,
    ) -> None:
        failed = result
        if result.status is not ResultStatus.FAILED:
            failed = result.model_copy(
                update={
                    "status": ResultStatus.FAILED,
                    "error": AgentError(
                        code=event_code,
                        message=message,
                        retryable=False,
                        details={
                            "expected_schema": expected_schema,
                            "workflow_node": task.run_metadata.workflow_node,
                        },
                    ),
                },
                deep=True,
            )
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=task.agent_name,
                content_type=f"agent_result_{event_code}",
                payload={
                    "event_code": event_code,
                    "status": "failed",
                    "message": message,
                    "expected_schema": expected_schema,
                    "run_id": checkpoint.run_id,
                    "workflow_node": task.run_metadata.workflow_node,
                    "agent_name": task.agent_name.value,
                    "task_id": task.task_id,
                    "agent_result": self._agent_result_summary(failed),
                    "payload": result.payload,
                    "error": result.error.model_dump(mode="json") if result.error else None,
                    "acceptance_audit": self._acceptance_audit(
                        checkpoint,
                        failed,
                        parse_status="failed" if event_code == "parse_failed" else "ok",
                        schema_status="failed" if event_code == "schema_failed" else "ok",
                        write_status="ok",
                    ),
                },
                evidence_refs=result.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write {event_code} for "
                f"{task.run_metadata.workflow_node}/{task.agent_name.value}: {exc}"
            ) from exc

    def _looks_like_schema_failure(self, exc: Exception) -> bool:
        return "schema" in str(exc).lower()

    def _write_parallel_agent_acceptance_failure(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        result: AgentResult,
        *,
        event_code: Literal["parse_failed", "schema_failed"],
        message: str,
        expected_schema: str,
    ) -> None:
        failed = result.model_copy(
            update={
                "status": ResultStatus.FAILED,
                "error": AgentError(
                    code=event_code,
                    message=message,
                    retryable=False,
                    details={
                        "expected_schema": expected_schema,
                        "workflow_node": node.value,
                    },
                ),
            },
            deep=True,
        )
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=agent_name,
                content_type=f"agent_result_{event_code}",
                payload={
                    "event_code": event_code,
                    "status": "failed",
                    "message": message,
                    "expected_schema": expected_schema,
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "agent_name": agent_name.value,
                    "task_id": result.task_id,
                    "agent_result": self._agent_result_summary(failed),
                    "payload": result.payload,
                    "error": result.error.model_dump(mode="json") if result.error else None,
                    "acceptance_audit": self._acceptance_audit(
                        checkpoint,
                        failed,
                        parse_status="failed" if event_code == "parse_failed" else "ok",
                        schema_status="failed" if event_code == "schema_failed" else "ok",
                        write_status="ok",
                    ),
                },
                evidence_refs=result.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write {event_code} for "
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

    def _write_workflow_exception(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        exc: Exception,
    ) -> str | None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type="workflow_exception",
                payload={
                    "event_code": "workflow_exception",
                    "status": "failed",
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "error_code": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
        except Exception as audit_exc:
            return str(audit_exc)
        return None

    def _agent_failure_event_code(self, result: AgentResult) -> str:
        if result.error is None:
            return "agent_failed"
        code = result.error.code
        if code in {
            "invalid_json",
            "missing_json_text",
            "invalid_react_action",
            "invalid_structured_output",
        }:
            return "parse_failed"
        if code in {"invalid_final_payload", "schema_invalid"} or "schema" in code:
            return "schema_failed"
        return "agent_failed"

    def _agent_metadata(
        self,
        node: WorkflowNode,
        results: list[AgentResult],
    ) -> dict[str, Any]:
        return {
            "last_agent_results": {
                node.value: [self._agent_result_summary(result) for result in results],
            },
            "last_error_code": next(
                (
                    result.error.code
                    for result in results
                    if result.error is not None
                ),
                None,
            ),
        }

    def _agent_result_summary(self, result: AgentResult) -> dict[str, Any]:
        return {
            "agent_name": result.agent_name.value,
            "status": result.status.value,
            "error_code": result.error.code if result.error is not None else None,
            "patch_ids": [patch.patch_id for patch in result.proposed_patches],
            "evidence_ids": [evidence.evidence_id for evidence in result.evidence_refs],
            "tool_calls": [tool_call.model_dump(mode="json") for tool_call in result.tool_calls],
            "tool_usage_audit": result.payload.get("tool_usage_audit", {}),
            "acceptance_audit": result.payload.get("acceptance_audit", {}),
            "skill_versions": result.payload.get("skill_versions", {}),
            "runtime": result.payload.get("runtime"),
        }

    def _acceptance_audit(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
        *,
        parse_status: str,
        schema_status: str,
        write_status: str,
    ) -> dict[str, Any]:
        targets = [
            {
                "document_type": patch.target.document_type.value,
                "object_id": patch.target.document_id or patch.target.expectation_id,
                "field_path": patch.target.field_path,
            }
            for patch in result.proposed_patches
        ]
        output_schema = result.payload.get("agent_definition", {}).get("output_schema")
        if output_schema is None and result.error is not None:
            output_schema = result.error.details.get("expected_schema")
        return {
            "run_id": checkpoint.run_id,
            "agent_name": result.agent_name.value,
            "workflow_node": checkpoint.next_node.value if checkpoint.next_node else None,
            "output_schema": output_schema,
            "parse_status": parse_status,
            "schema_status": schema_status,
            "write_status": write_status,
            "blackboard_target": targets,
        }

    def _with_tool_usage_audit(self, result: AgentResult) -> AgentResult:
        payload = dict(result.payload)
        structured = payload.get("structured")
        declared_tools = _declared_tool_names(structured if isinstance(structured, dict) else {})
        audit = payload.get("react_audit")
        actual_tools = {
            tool_call.tool_name
            for tool_call in result.tool_calls
            if tool_call.status is ResultStatus.SUCCEEDED
        }
        if isinstance(audit, dict) and isinstance(audit.get("tool_counts"), dict):
            actual_tools.update(str(tool_name) for tool_name in audit["tool_counts"])
        unexecuted = sorted(declared_tools.difference(actual_tools))
        payload["tool_usage_audit"] = {
            "declared_tool_names": sorted(declared_tools),
            "actual_tool_names": sorted(actual_tools),
            "unexecuted_declared_tool_names": unexecuted,
            "status": "warning" if unexecuted else "ok",
        }
        return result.model_copy(update={"payload": payload}, deep=True)
