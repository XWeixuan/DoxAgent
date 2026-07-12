"""Workflow-memory compilation failures."""


class WorkflowMemoryError(RuntimeError):
    pass


class UnknownWorkflowMemoryPolicy(WorkflowMemoryError):
    pass


class WorkflowMemoryOverBudget(WorkflowMemoryError):
    def __init__(
        self,
        *,
        policy_id: str,
        estimated_tokens: int,
        max_input_tokens: int,
        document_chars: dict[str, int],
    ) -> None:
        self.policy_id = policy_id
        self.estimated_tokens = estimated_tokens
        self.max_input_tokens = max_input_tokens
        self.document_chars = document_chars
        super().__init__(
            "workflow_memory_over_budget: "
            f"policy={policy_id} estimated_tokens={estimated_tokens} "
            f"max_input_tokens={max_input_tokens} document_chars={document_chars}"
        )


__all__ = [
    "UnknownWorkflowMemoryPolicy",
    "WorkflowMemoryError",
    "WorkflowMemoryOverBudget",
]
