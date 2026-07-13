"""Default-deny workflow-memory policy registry."""

from __future__ import annotations

from doxagent.models import (
    AgentName,
    AgentTask,
    DocumentType,
    TaskType,
)
from doxagent.workflow_memory.errors import UnknownWorkflowMemoryPolicy
from doxagent.workflow_memory.schema import WorkflowMemoryPolicy

INITIALIZATION_WORKFLOW_NODES = {
    "StartTickerInitialization",
    "BuildGlobalResearch",
    "ReviewGlobalResearch",
    "GenerateExpectationConstruction",
    "ReviewExpectationConstruction",
    "ResolveExpectationConstruction",
    "GenerateExpectationDetails",
    "GenerateExpectationUnits",
    "ReviewExpectationFields",
    "ResolveObjectionsAndDelegations",
    "PromoteExpectationToBeliefState",
    "GenerateGlobalNarrativeReport",
    "GenerateKnownEvents",
    "GenerateMonitoringConfig",
    "ReviewMonitoringConfig",
    "ResolveMonitoringConfig",
    "GenerateMonitoringPolicy",
    "ReviewMonitoringPolicy",
    "ResolveMonitoringPolicy",
    "FinalizeInitialization",
}


class WorkflowMemoryPolicyRegistry:
    def __init__(
        self,
        policies: list[WorkflowMemoryPolicy] | None = None,
        *,
        strict_workflow_nodes: set[str] | None = None,
    ) -> None:
        self._policies = list(policies or [])
        self._strict_workflow_nodes = set(
            strict_workflow_nodes
            if strict_workflow_nodes is not None
            else INITIALIZATION_WORKFLOW_NODES
        )

    def register(self, policy: WorkflowMemoryPolicy) -> None:
        if any(item.policy_id == policy.policy_id for item in self._policies):
            raise ValueError(f"duplicate workflow memory policy id: {policy.policy_id}")
        self._policies.append(policy)

    def resolve(self, task: AgentTask) -> WorkflowMemoryPolicy:
        node = task.run_metadata.workflow_node or ""
        matches = [policy for policy in self._policies if _matches(policy, task)]
        if matches:
            return max(matches, key=_specificity)
        return WorkflowMemoryPolicy(
            policy_id=f"default-deny:{node or 'unscoped'}:{task.task_type.value}",
            workflow_node=node,
            task_type=task.task_type,
        )

    def validate_node_coverage(self, workflow_nodes: set[str] | None = None) -> None:
        """Fail CI/startup checks when a declared workflow node has no policy."""

        required = workflow_nodes or self._strict_workflow_nodes
        covered = {policy.workflow_node for policy in self._policies}
        missing = sorted(required - covered)
        if missing:
            raise UnknownWorkflowMemoryPolicy(
                "Workflow nodes missing WorkflowMemoryPolicy coverage: "
                + ", ".join(missing)
            )


def default_workflow_memory_policy_registry() -> WorkflowMemoryPolicyRegistry:
    policies: list[WorkflowMemoryPolicy] = [
        _policy(
            "init.build_global_research.v1",
            "BuildGlobalResearch",
            TaskType.GENERATE_GLOBAL_RESEARCH,
            "ResearchSection",
            directives=(
                "global_research_inputs",
                "document1_research_focus",
                "required_section_key",
                "section_instruction",
                "prior_sections",
            ),
        ),
        _policy(
            "init.generate_global_narrative.v1",
            "GenerateGlobalNarrativeReport",
            TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
            "ResearchSection",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT),
            directives=("required_section_key", "section_instruction"),
        ),
        _policy(
            "init.generate_expectation_construction.v1",
            "GenerateExpectationConstruction",
            TaskType.GENERATE_EXPECTATION_UNIT,
            "ExpectationShellConstructionResult",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH,),
        ),
        _policy(
            "compat.generate_expectation_document.v1",
            "GenerateExpectationConstruction",
            TaskType.GENERATE_EXPECTATION_UNIT,
            "ExpectationUnitDocument",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH,),
        ),
        _policy(
            "init.review_expectation_construction.v1",
            "ReviewExpectationConstruction",
            TaskType.REVIEW_EXPECTATION_FIELD,
            "DoxAtlasAuditResult",
            agent=AgentName.A1_DOXATLAS_AUDIT,
            directives=("review_scope", "review_instruction", "doxatlas_scope_guardrails"),
            active=("expectation_shells",),
        ),
        _policy(
            "init.resolve_expectation_construction.o1.v1",
            "ResolveExpectationConstruction",
            TaskType.GENERATE_EXPECTATION_UNIT,
            "ExpectationShellConstructionResult",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH,),
            directives=("resolution_request",),
            active=("expectation_shells", "unresolved_objections"),
        ),
        _policy(
            "init.resolve_expectation_construction.a2.v1",
            "ResolveExpectationConstruction",
            TaskType.DELEGATED_RETRIEVAL,
            "DelegatedRetrievalResult",
            agent=AgentName.A2_FACT_CHECK,
            active=("delegation",),
        ),
        _policy(
            "init.generate_expectation_details.v1",
            "GenerateExpectationDetails",
            TaskType.GENERATE_EXPECTATION_DETAIL,
            "ExpectationDetailCandidateResult",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH,),
            directives=(
                "detail_instruction",
                "detail_completion_budget",
                "detail_recovery_retry",
            ),
            active=("expectation_shell",),
        ),
        _policy(
            "init.resolve_objections.a2.v1",
            "ResolveObjectionsAndDelegations",
            TaskType.DELEGATED_RETRIEVAL,
            "DelegatedRetrievalResult",
            agent=AgentName.A2_FACT_CHECK,
            active=("delegation",),
        ),
        _policy(
            "init.resolve_objections.field_repair.v1",
            "ResolveObjectionsAndDelegations",
            TaskType.REVIEW_EXPECTATION_FIELD,
            "Document2FieldRepairResult",
            agent=AgentName.O1_EXPECTATION_OWNER,
            directives=(
                "resolution_request",
                "resolution_mode",
                "field_repair_batch",
                "allowed_output_contract",
                "output_guidance",
            ),
            active=(
                "field_repair_task",
                "current_candidate",
                "findings",
                "unresolved_objections",
            ),
        ),
        _policy(
            "init.generate_known_events.v1",
            "GenerateKnownEvents",
            TaskType.GENERATE_KNOWN_EVENTS,
            "KnownEventsDocument",
            agent=AgentName.O1_EXPECTATION_OWNER,
            documents=(DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT),
        ),
        _policy(
            "init.generate_monitoring_config.v1",
            "GenerateMonitoringConfig",
            TaskType.GENERATE_MONITORING_CONFIG,
            "MonitoringConfigDocument",
            agent=AgentName.O2_MONITORING_CONFIG,
            documents=(
                DocumentType.GLOBAL_RESEARCH,
                DocumentType.EXPECTATION_UNIT,
                DocumentType.KNOWN_EVENTS,
            ),
        ),
        _policy(
            "init.resolve_monitoring_config.v1",
            "ResolveMonitoringConfig",
            TaskType.RESOLVE_MONITORING_CONFIG,
            "MonitoringConfigDocument",
            agent=AgentName.O2_MONITORING_CONFIG,
            active=("document3_pending_patch", "document3_review_objections"),
        ),
        _policy(
            "init.generate_monitoring_policy.v1",
            "GenerateMonitoringPolicy",
            TaskType.GENERATE_MONITORING_POLICY,
            "MonitoringPolicyDocument",
            agent=AgentName.O4_MARKET_TRACE,
            documents=(
                DocumentType.GLOBAL_RESEARCH,
                DocumentType.EXPECTATION_UNIT,
                DocumentType.KNOWN_EVENTS,
                DocumentType.MONITORING_CONFIG,
            ),
        ),
        _policy(
            "init.resolve_monitoring_policy.v1",
            "ResolveMonitoringPolicy",
            TaskType.RESOLVE_MONITORING_POLICY,
            "MonitoringPolicyDocument",
            agent=AgentName.O4_MARKET_TRACE,
            active=(
                "document3_pending_patch",
                "document3_review_objections",
                "monitoring_config_brief",
            ),
        ),
        _policy(
            "runtime.w1.v1",
            "persistent_runtime_execution",
            TaskType.RUNTIME_W1_NOVELTY,
            "W1Result",
            agent=AgentName.W1_RUNTIME_NOVELTY,
            active=("source_message", "runtime_context"),
        ),
        _policy(
            "runtime.w2.v1",
            "persistent_runtime_execution",
            TaskType.RUNTIME_W2_POLICY,
            "W2Result",
            agent=AgentName.W2_RUNTIME_POLICY,
            active=("source_message", "runtime_context"),
        ),
        _policy(
            "runtime.o3.v1",
            "persistent_runtime_execution",
            TaskType.RUNTIME_O3_JUDGMENT,
            "O3Result",
            agent=AgentName.O3_TRADING_STRATEGY,
            active=("source_message", "runtime_context"),
        ),
        _policy(
            "runtime.a2.v1",
            "persistent_runtime_execution",
            TaskType.FACT_CHECK,
            "A2Result",
            agent=AgentName.A2_FACT_CHECK,
            active=("source_message", "runtime_context"),
        ),
    ]
    for agent, schema in (
        (AgentName.A1_DOXATLAS_AUDIT, "DoxAtlasAuditResult"),
        (AgentName.C1_FUNDAMENTAL_RESEARCH, "ExpectationFieldReviewResult"),
        (AgentName.C3_INDUSTRY_RESEARCH, "ExpectationFieldReviewResult"),
        (AgentName.O4_MARKET_TRACE, "ExpectationFieldReviewResult"),
    ):
        policies.append(
            _policy(
                f"init.review_expectation_fields.{agent.value.lower()}.v1",
                "ReviewExpectationFields",
                TaskType.REVIEW_EXPECTATION_FIELD,
                schema,
                agent=agent,
                documents=(DocumentType.GLOBAL_RESEARCH,),
                directives=("review_scope", "review_instruction"),
                active=("review_candidates",),
            )
        )
    for agent in (AgentName.C1_FUNDAMENTAL_RESEARCH, AgentName.C3_INDUSTRY_RESEARCH):
        policies.append(
            _policy(
                f"init.review_monitoring_config.{agent.value.lower()}.v1",
                "ReviewMonitoringConfig",
                TaskType.REVIEW_MONITORING_CONFIG,
                "ResearchSection",
                agent=agent,
                directives=("review_scope", "review_instruction"),
                active=("document3_pending_patch",),
            )
        )
    policies.append(
        _policy(
            "init.review_monitoring_policy.o2.v1",
            "ReviewMonitoringPolicy",
            TaskType.REVIEW_MONITORING_POLICY,
            "ResearchSection",
            agent=AgentName.O2_MONITORING_CONFIG,
            directives=("review_scope", "review_instruction"),
            active=("document3_pending_patch", "monitoring_config_brief"),
        )
    )
    for node in INITIALIZATION_WORKFLOW_NODES:
        policies.append(
            _policy(
                f"init.delegated.{node}.v1",
                node,
                TaskType.DELEGATED_RETRIEVAL,
                agent=AgentName.A2_FACT_CHECK,
                directives=("delegated_question", "delegation_context"),
            )
        )
    return WorkflowMemoryPolicyRegistry(policies)


def _policy(
    policy_id: str,
    workflow_node: str,
    task_type: TaskType,
    required_output_schema: str | None = None,
    *,
    agent: AgentName | None = None,
    documents: tuple[DocumentType, ...] = (),
    directives: tuple[str, ...] = (),
    active: tuple[str, ...] = (),
) -> WorkflowMemoryPolicy:
    return WorkflowMemoryPolicy(
        policy_id=policy_id,
        workflow_node=workflow_node,
        task_type=task_type,
        required_output_schema=required_output_schema,
        agent_name=agent,
        document_types=documents,
        directive_fields=directives,
        active_work_item_fields=active,
    )


def _matches(policy: WorkflowMemoryPolicy, task: AgentTask) -> bool:
    return (
        policy.workflow_node == (task.run_metadata.workflow_node or "")
        and policy.task_type is task.task_type
        and (policy.agent_name is None or policy.agent_name is task.agent_name)
        and (
            policy.required_output_schema is None
            or policy.required_output_schema == task.required_output_schema
        )
    )


def _specificity(policy: WorkflowMemoryPolicy) -> tuple[int, int]:
    return (
        int(policy.agent_name is not None) + int(policy.required_output_schema is not None),
        len(policy.active_work_item_fields) + len(policy.document_types),
    )


__all__ = [
    "INITIALIZATION_WORKFLOW_NODES",
    "WorkflowMemoryPolicyRegistry",
    "default_workflow_memory_policy_registry",
]
