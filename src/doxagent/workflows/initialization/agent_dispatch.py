# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.initialization.shared import *

_SERIAL_AGENT_DISPATCH_KEY = "serial_agent_dispatch"


class InitializationAgentDispatchMixin:
    def _run_agent(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
        output_schema: str,
        *,
        extra_context: dict[str, Any] | None = None,
        audit_failures: bool = True,
        validate_output: bool = True,
        retry_on_retryable_failure: bool = True,
        workflow_watchdog: bool = True,
    ) -> AgentResult:
        definition = self.registry.get(agent_name)
        permissions = self._effective_permissions(
            definition.runtime.to_permissions(),
            node,
            task_type,
            agent_name,
        )
        input_context = self._task_input_context(
            checkpoint,
            node,
            agent_name,
            task_type,
            permissions,
        )
        if extra_context:
            input_context = input_context | extra_context

        def build_task(
            *,
            retry_attempt: int = 0,
            previous_failure: AgentResult | None = None,
        ) -> AgentTask:
            task_input_context = input_context
            if retry_attempt:
                task_input_context = input_context | {
                    "retry_context": self._agent_retry_context(
                        previous_failure,
                        retry_attempt=retry_attempt,
                    )
                }
            return AgentTask(
                task_id=new_id("task"),
                ticker=checkpoint.ticker,
                agent_name=agent_name,
                task_type=task_type,
                input_context=task_input_context,
                required_output_schema=output_schema,
                permissions=permissions,
                run_metadata=RunMetadata(
                    run_id=checkpoint.run_id,
                    ticker=checkpoint.ticker,
                    workflow_node=node.value,
                    created_at=datetime.now(UTC),
                ),
            )

        def run_task(task: AgentTask) -> AgentResult:
            result = self.runner.run(task)
            try:
                result = self.result_normalizer.normalize(result)
            except WorkflowContractError as exc:
                if audit_failures:
                    self._write_agent_acceptance_failure(
                        checkpoint,
                        task,
                        result,
                        event_code="schema_failed",
                        message=str(exc),
                        expected_schema=output_schema,
                    )
                raise
            if (
                validate_output
                and self.execution_mode == "agent_runner"
                and result.status is ResultStatus.SUCCEEDED
            ):
                try:
                    self.output_validator.validate(result.payload, output_schema)
                except WorkflowContractError as exc:
                    if audit_failures:
                        self._write_agent_acceptance_failure(
                            checkpoint,
                            task,
                            result,
                            event_code="schema_failed",
                            message=str(exc),
                            expected_schema=output_schema,
                        )
                    raise
            return result

        task = build_task()
        result = self._run_serial_agent_task(
            checkpoint,
            node,
            task,
            run_task,
            workflow_watchdog=workflow_watchdog,
        )
        if retry_on_retryable_failure and self._is_retryable_agent_result_failure(result):
            first_failure = result
            task = build_task(retry_attempt=1, previous_failure=first_failure)
            result = self._with_retry_audit(
                self._run_serial_agent_task(
                    checkpoint,
                    node,
                    task,
                    run_task,
                    workflow_watchdog=workflow_watchdog,
                ),
                first_failure,
            )
        if result.status is ResultStatus.FAILED:
            event_code = self._agent_failure_event_code(result)
            if audit_failures and event_code in {"parse_failed", "schema_failed"}:
                self._write_agent_acceptance_failure(
                    checkpoint,
                    task,
                    result,
                    event_code=cast(Literal["parse_failed", "schema_failed"], event_code),
                    message=result.error.message if result.error is not None else "Agent failed.",
                    expected_schema=output_schema,
                )
        return self._with_failure_audit(self._with_tool_usage_audit(result))

    def _run_agent_jobs_concurrently(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        jobs: list[_ParallelAgentJob],
        *,
        on_outcome: Callable[[_ParallelAgentOutcome], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> list[_ParallelAgentOutcome]:
        if not jobs:
            return []

        def run_job(job: _ParallelAgentJob) -> _ParallelAgentOutcome:
            try:
                result = self._run_parallel_agent_job_once(checkpoint, node, job)
                if self._is_retryable_agent_result_failure(result):
                    result = self._run_parallel_agent_job_once(checkpoint, node, job)
                return _ParallelAgentOutcome(job=job, result=result)
            except Exception as exc:
                return _ParallelAgentOutcome(job=job, error=exc)

        outcomes: list[_ParallelAgentOutcome] = []
        outcome_queue: Queue[_ParallelAgentOutcome] = Queue()
        pending_by_order = {job.order: job for job in jobs}
        timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.workflow_agent_stale_after_seconds
        )
        deadline = time.monotonic() + timeout_seconds

        def worker(job: _ParallelAgentJob) -> None:
            outcome_queue.put(run_job(job))

        for job in jobs:
            thread = threading.Thread(
                target=worker,
                args=(job,),
                name=f"doxagent-{node.value}-{job.agent_name.value}-{job.order}",
                daemon=True,
            )
            thread.start()

        while pending_by_order:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                outcome = outcome_queue.get(timeout=min(1.0, remaining))
            except Empty:
                continue
            if outcome.job.order not in pending_by_order:
                continue
            pending_by_order.pop(outcome.job.order, None)
            if on_outcome is not None:
                on_outcome(outcome)
            outcomes.append(outcome)

        for job in pending_by_order.values():
            outcome = _ParallelAgentOutcome(
                job=job,
                error=WorkflowContractError(
                    f"parallel_agent_timeout: {self._parallel_job_label(node, job)} "
                    f"did not return within {timeout_seconds:g} seconds."
                ),
            )
            if on_outcome is not None:
                on_outcome(outcome)
            outcomes.append(outcome)
        return sorted(outcomes, key=lambda outcome: outcome.job.order)

    def _parallel_job_label(self, node: WorkflowNode, job: _ParallelAgentJob) -> str:
        parts = [node.value, job.agent_name.value]
        if job.section_key:
            parts.append(job.section_key)
        label = "/".join(parts)
        metadata = [f"order={job.order}"]
        if job.cache_key:
            metadata.append(f"cache_key={job.cache_key}")
        return f"{label} ({', '.join(metadata)})"

    def _run_parallel_agent_job_once(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        job: _ParallelAgentJob,
    ) -> AgentResult:
        worker_checkpoint = checkpoint.model_copy(deep=True)
        return self._run_agent(
            worker_checkpoint,
            node,
            job.agent_name,
            job.task_type,
            job.output_schema,
            extra_context=deepcopy(job.extra_context),
            audit_failures=False,
            validate_output=False,
            retry_on_retryable_failure=False,
            workflow_watchdog=False,
        )

    def _run_serial_agent_task(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        task: AgentTask,
        run_task: Callable[[AgentTask], AgentResult],
        *,
        workflow_watchdog: bool,
    ) -> AgentResult:
        if not workflow_watchdog:
            return run_task(task)
        timeout_seconds = self._serial_agent_timeout_seconds(node, task)
        running = self._serial_agent_dispatch_checkpoint(
            checkpoint,
            node,
            task,
            status="running",
            timeout_seconds=timeout_seconds,
        )
        self._save_parallel_outcome_checkpoint(running)
        result = self._run_agent_task_with_workflow_timeout(
            task,
            run_task,
            timeout_seconds=timeout_seconds,
        )
        if result.status is ResultStatus.FAILED and result.error is not None:
            failed = self._serial_agent_dispatch_checkpoint(
                running,
                node,
                task,
                status="failed",
                timeout_seconds=timeout_seconds,
                error_code=result.error.code,
                error_message=result.error.message,
            )
            self._save_parallel_outcome_checkpoint(failed)
        return result

    def _run_agent_task_with_workflow_timeout(
        self,
        task: AgentTask,
        run_task: Callable[[AgentTask], AgentResult],
        *,
        timeout_seconds: float,
    ) -> AgentResult:
        outcome_queue: Queue[tuple[str, AgentResult | BaseException]] = Queue(maxsize=1)

        def worker() -> None:
            try:
                outcome_queue.put(("result", run_task(task)))
            except BaseException as exc:
                outcome_queue.put(("error", exc))

        thread = threading.Thread(
            target=worker,
            name=f"doxagent-serial-{task.run_metadata.workflow_node}-{task.agent_name.value}",
            daemon=True,
        )
        thread.start()
        try:
            kind, value = outcome_queue.get(timeout=max(0.001, timeout_seconds))
        except Empty:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.FAILED,
                error=AgentError(
                    code="workflow_agent_timeout",
                    message=(
                        f"{task.run_metadata.workflow_node}/{task.agent_name.value} "
                        f"exceeded workflow timeout {timeout_seconds:g} seconds."
                    ),
                    retryable=False,
                    details={
                        "workflow_node": task.run_metadata.workflow_node,
                        "agent_name": task.agent_name.value,
                        "task_type": task.task_type.value,
                        "required_output_schema": task.required_output_schema,
                        "timeout_seconds": timeout_seconds,
                    },
                ),
            )
        if kind == "error":
            raise cast(BaseException, value)
        return cast(AgentResult, value)

    def _serial_agent_timeout_seconds(
        self,
        node: WorkflowNode,
        task: AgentTask,
    ) -> float:
        if (
            node is WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS
            and task.agent_name is AgentName.O1_EXPECTATION_OWNER
        ):
            return min(120.0, float(self.settings.model_request_timeout_seconds))
        return float(self.settings.workflow_agent_stale_after_seconds)

    def _serial_agent_dispatch_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        task: AgentTask,
        *,
        status: Literal["running", "failed"],
        timeout_seconds: float,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> WorkflowCheckpoint:
        dispatch = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": task.agent_name.value,
            "task_id": task.task_id,
            "task_type": task.task_type.value,
            "required_output_schema": task.required_output_schema,
            "timeout_seconds": timeout_seconds,
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error_code is not None:
            dispatch["error_code"] = error_code
        if error_message is not None:
            dispatch["error_message"] = error_message
        return checkpoint.model_copy(
            update={"metadata": checkpoint.metadata | {_SERIAL_AGENT_DISPATCH_KEY: dispatch}},
            deep=True,
        )

    def _effective_permissions(
        self,
        permissions: AgentPermissions,
        node: WorkflowNode,
        task_type: TaskType,
        agent_name: AgentName,
    ) -> AgentPermissions:
        updates: dict[str, Any] = {}
        if node is WorkflowNode.BUILD_GLOBAL_RESEARCH:
            updates["can_raise_objection"] = False
            updates["writable_targets"] = [DocumentType.GLOBAL_RESEARCH.value]
            if (
                permissions.allowed_tools
                and task_type is TaskType.GENERATE_GLOBAL_RESEARCH
                and agent_name is AgentName.O4_MARKET_TRACE
            ):
                updates["allowed_tools"] = BUILD_GLOBAL_RESEARCH_MARKET_TOOLS
        node_agent_tools = NODE_AGENT_ALLOWED_TOOL_OVERRIDES.get((node, agent_name))
        if node_agent_tools is not None:
            updates["allowed_tools"] = node_agent_tools
        if node is WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT:
            updates["writable_targets"] = [DocumentType.GLOBAL_RESEARCH.value]
        if (
            agent_name is AgentName.O1_EXPECTATION_OWNER
            and node
            in {
                WorkflowNode.GENERATE_EXPECTATION_DETAILS,
                WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
                WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
            }
        ):
            updates["can_propose_patch"] = False
            updates["writable_targets"] = []
        if task_type is TaskType.GENERATE_EXPECTATION_UNIT:
            updates["writable_targets"] = []
        elif task_type is TaskType.GENERATE_EXPECTATION_DETAIL:
            updates["writable_targets"] = []
        elif task_type is TaskType.GENERATE_KNOWN_EVENTS:
            updates["writable_targets"] = [DocumentType.KNOWN_EVENTS.value]
        elif task_type is TaskType.GENERATE_MONITORING_CONFIG:
            updates["writable_targets"] = [DocumentType.MONITORING_CONFIG.value]
        elif task_type is TaskType.RESOLVE_MONITORING_CONFIG:
            updates["writable_targets"] = [DocumentType.MONITORING_CONFIG.value]
        elif task_type in {
            TaskType.GENERATE_MONITORING_POLICY,
            TaskType.RESOLVE_MONITORING_POLICY,
        }:
            updates["writable_targets"] = [DocumentType.MONITORING_POLICY.value]
        elif task_type in {
            TaskType.REVIEW_MONITORING_CONFIG,
            TaskType.REVIEW_MONITORING_POLICY,
        }:
            updates["writable_targets"] = []
        elif (
            node
            in {
                WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
                WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
            }
            and task_type is TaskType.REVIEW_EXPECTATION_FIELD
        ):
            updates["writable_targets"] = [DocumentType.EXPECTATION_UNIT.value]
            if agent_name is AgentName.O1_EXPECTATION_OWNER:
                updates["can_propose_patch"] = False
                updates["writable_targets"] = []
        return permissions.model_copy(update=updates, deep=True) if updates else permissions

    def _a1_allowed_tools_for_node(self, node: WorkflowNode) -> list[str]:
        return NODE_AGENT_ALLOWED_TOOL_OVERRIDES.get(
            (node, AgentName.A1_DOXATLAS_AUDIT),
            [],
        )

    def _a1_tool_purpose(self, tool_name: str, node: WorkflowNode) -> str:
        if tool_name == "doxa_query_analysis":
            return "List available DoxAtlas analysis tasks and task_code values for the ticker."
        if tool_name == "doxa_get_analysis":
            return (
                "Read DoxAtlas analysis/topic context by ticker and task_code without "
                "starting new runs."
            )
        if tool_name == "doxa_query_propositions":
            return "Check proposition-level support or contradiction for the reviewed field."
        if tool_name == "doxa_get_ignored_propositions":
            return "Find ignored or weak propositions that may undermine the reviewed claim."
        if tool_name == "doxa_get_event_source":
            return "Inspect source material bound to a narrative event or source id."
        if tool_name == "doxa_get_media_result":
            return "Check media event capsules for completed expectation facts."
        if tool_name == "doxa_get_media_result_detail":
            return "Inspect selected Mxx media records, URLs, source quality, and content."
        if tool_name == "doxa_get_social_result":
            return "Check high-conviction social evidence for completed expectation facts."
        if tool_name == "doxa_get_social_result_detail":
            return "Inspect selected Sxx social records, URLs, source, and content."
        return f"Optional DoxAtlas read evidence for {node.value}."

    def _is_parallel_agent_timeout_error(self, error: Exception) -> bool:
        return "parallel_agent_timeout:" in str(error)

    def _is_retryable_agent_result_failure(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.FAILED or result.error is None:
            return False
        if result.error.retryable:
            return True
        gateway_error = result.error.details.get("gateway_error")
        return (
            isinstance(gateway_error, dict)
            and gateway_error.get("code") == "model_request_timeout"
        )

    def _agent_retry_context(
        self,
        previous_failure: AgentResult | None,
        *,
        retry_attempt: int,
    ) -> dict[str, Any]:
        return {
            "retry_attempt": retry_attempt,
            "retry_reason": "previous_agent_result_retryable_failure",
            "previous_failure": self._agent_failure_audit(previous_failure),
        }

    def _runner_tool_registry(self) -> Any | None:
        tool_registry = getattr(self.runner, "tool_registry", None)
        if tool_registry is not None:
            return tool_registry
        nested_runner = getattr(self.runner, "runner", None)
        return getattr(nested_runner, "tool_registry", None)

    def _merge_prefetched_tool_result(
        self,
        result: AgentResult,
        tool_result: ToolResult,
    ) -> AgentResult:
        tool_evidence_refs = [
            self._normalize_evidence_ref_language(ref) for ref in tool_result.evidence_refs
        ]
        summary = ToolCallSummary(
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            input_summary="workflow 预取请求",
            output_summary=self._tool_output_summary_text(
                tool_result.tool_name,
                tool_result.output_summary,
            ),
            evidence_refs=tool_evidence_refs,
        )
        payload = dict(result.payload)
        structured = payload.get("structured")
        if isinstance(structured, dict):
            updated_structured = dict(structured)
            evidence_refs = updated_structured.get("evidence_refs", [])
            if not isinstance(evidence_refs, list):
                evidence_refs = []
            updated_structured["evidence_refs"] = evidence_refs + [
                item.model_dump(mode="json") for item in tool_evidence_refs
            ]
            payload["structured"] = updated_structured
        merged_result = result.model_copy(
            update={
                "payload": payload,
                "evidence_refs": result.evidence_refs + tool_evidence_refs,
                "tool_calls": result.tool_calls + [summary],
            },
            deep=True,
        )
        return self._with_tool_usage_audit(merged_result)

    def _tool_output_summary_text(self, tool_name: str, value: Any) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if tool_name == "doxa_get_narrative_report":
            return "已检索 DoxAtlas 叙事报告。"
        if tool_name.startswith("doxa_") or tool_name.startswith("doxatlas."):
            return "已检索 DoxAtlas 工具结果。"
        return "工具调用已返回结果。"

    def _has_successful_tool_call(self, result: AgentResult, tool_name: str) -> bool:
        return any(
            tool_call.tool_name == tool_name and tool_call.status is ResultStatus.SUCCEEDED
            for tool_call in result.tool_calls
        )

    def _payload_mentions_narrative_gap(self, result: AgentResult) -> bool:
        payload = result.payload.get("structured")
        if not isinstance(payload, dict):
            payload = result.payload
        unknowns = payload.get("unknowns", [])
        rationale = payload.get("rationale", "")
        text = " ".join(
            [
                *(item for item in unknowns if isinstance(item, str)),
                rationale if isinstance(rationale, str) else "",
            ]
        ).lower()
        return bool(
            ("doxatlas" in text or "narrative" in text)
            and any(
                marker in text
                for marker in (
                    "missing",
                    "failed",
                    "gap",
                    "unavailable",
                    "缺失",
                    "失败",
                )
            )
        )
