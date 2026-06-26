# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.initialization.shared import *


class InitializationRecoveryMixin:
    def _save_parallel_outcome_checkpoint(self, checkpoint: WorkflowCheckpoint) -> bool:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.checkpoint_repository.save_checkpoint(checkpoint)
                return True
            except Exception as exc:  # best-effort cache; final ordered path still validates.
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        if last_error is not None:
            return False
        return True

    def _recover_stale_agent_dispatch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        *,
        cache_key: str | None = None,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        state = self._agent_idempotency(checkpoint).get(key, {})
        if state.get("status") != "running":
            return checkpoint
        if not self._is_stale_agent_dispatch(state):
            return checkpoint

        message = (
            f"stale_agent_dispatch: {node.value}/{agent_name.value} was left running "
            f"for more than {self.settings.workflow_agent_stale_after_seconds} seconds; "
            "recording audit event and retrying this agent."
        )
        self._write_agent_dispatch_recovery(
            checkpoint,
            node,
            agent_name,
            section_key,
            state,
            message,
        )
        return self._mark_agent_dispatch(
            checkpoint,
            node,
            agent_name,
            status="failed",
            section_key=section_key,
            cache_key=cache_key,
            error_message=message,
        )

    def _is_stale_agent_dispatch(self, state: dict[str, Any]) -> bool:
        updated_at = state.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            return False
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        return age_seconds >= self.settings.workflow_agent_stale_after_seconds

    def _write_agent_dispatch_recovery(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        previous_state: dict[str, Any],
        message: str,
    ) -> None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type="agent_dispatch_stale_recovery",
                payload={
                    "event_code": "stale_agent_dispatch_recovered",
                    "status": "failed",
                    "retry_reason": "stale_running_dispatch",
                    "message": message,
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "agent_name": agent_name.value,
                    "section_key": section_key,
                    "stale_after_seconds": self.settings.workflow_agent_stale_after_seconds,
                    "previous_dispatch": previous_state,
                },
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write stale dispatch recovery for "
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

    def _cached_global_research_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
    ) -> AgentResult | None:
        key = self._agent_idempotency_key(node, agent_name)
        idempotency = self._agent_idempotency(checkpoint)
        state = idempotency.get(key, {})
        if state.get("status") == "running":
            raise WorkflowContractError(
                f"duplicate_agent_running: {node.value}/{agent_name.value} is already running."
            )
        if state.get("status") != "completed":
            return None

        cached_results = self._global_research_agent_results(checkpoint)
        cached = cached_results.get(key)
        if not isinstance(cached, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult missing for {node.value}/{agent_name.value}."
            )
        raw_result = cached.get("result")
        if not isinstance(raw_result, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult malformed for {node.value}/{agent_name.value}."
            )
        try:
            return AgentResult.model_validate(raw_result)
        except Exception as exc:
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult could not be restored for "
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

    def _cached_workflow_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        cache_key: str,
    ) -> AgentResult | None:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        idempotency = self._agent_idempotency(checkpoint)
        state = idempotency.get(key, {})
        if state.get("status") == "running":
            raise WorkflowContractError(
                f"duplicate_agent_running: {node.value}/{agent_name.value}/{cache_key} "
                "is already running."
            )
        if state.get("status") != "completed":
            return None

        cached_results = self._workflow_agent_results(checkpoint)
        cached = cached_results.get(key)
        if not isinstance(cached, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult missing for "
                f"{node.value}/{agent_name.value}/{cache_key}."
            )
        raw_result = cached.get("result")
        if not isinstance(raw_result, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult malformed for "
                f"{node.value}/{agent_name.value}/{cache_key}."
            )
        try:
            return AgentResult.model_validate(raw_result)
        except Exception as exc:
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult could not be restored for "
                f"{node.value}/{agent_name.value}/{cache_key}: {exc}"
            ) from exc

    def _mark_agent_dispatch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        status: Literal["running", "failed"],
        section_key: str,
        cache_key: str | None = None,
        error_message: str | None = None,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        state = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if cache_key is not None:
            state["cache_key"] = cache_key
        if error_message is not None:
            state["error_message"] = error_message
        idempotency = self._agent_idempotency(checkpoint) | {key: state}
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {_WORKFLOW_AGENT_IDEMPOTENCY_KEY: idempotency}
            },
            deep=True,
        )

    def _store_global_research_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name)
        cached_results = self._global_research_agent_results(checkpoint)
        cached_results[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": "completed",
            "result": result.model_dump(mode="json"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        idempotency = self._agent_idempotency(checkpoint)
        idempotency[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": "completed",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {
                    _GLOBAL_RESEARCH_AGENT_RESULTS_KEY: cached_results,
                    _WORKFLOW_AGENT_IDEMPOTENCY_KEY: idempotency,
                }
            },
            deep=True,
        )

    def _store_workflow_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        result: AgentResult,
        *,
        cache_key: str,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        cached_results = self._workflow_agent_results(checkpoint)
        cached_results[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "cache_key": cache_key,
            "status": "completed",
            "result": result.model_dump(mode="json"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        idempotency = self._agent_idempotency(checkpoint)
        idempotency[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "cache_key": cache_key,
            "status": "completed",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {
                    _WORKFLOW_AGENT_RESULTS_KEY: cached_results,
                    _WORKFLOW_AGENT_IDEMPOTENCY_KEY: idempotency,
                }
            },
            deep=True,
        )

    def _agent_idempotency(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_WORKFLOW_AGENT_IDEMPOTENCY_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _global_research_agent_results(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_GLOBAL_RESEARCH_AGENT_RESULTS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _workflow_agent_results(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_WORKFLOW_AGENT_RESULTS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _agent_idempotency_key(
        self,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        cache_key: str | None = None,
    ) -> str:
        base = f"{node.value}:{agent_name.value}"
        return f"{base}:{cache_key}" if cache_key else base

    def _latest_checkpoint_or(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        try:
            return self.checkpoint_repository.get_latest(checkpoint.run_id)
        except Exception:
            return checkpoint
