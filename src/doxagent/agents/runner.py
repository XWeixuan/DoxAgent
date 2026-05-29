"""Agent runner boundary for DoxAgent-owned contracts."""

from collections.abc import Callable
from typing import Protocol

from doxagent.agents.config import AgentRegistry, default_agent_registry
from doxagent.models import AgentError, AgentResult, AgentTask, ResultStatus


class AgentRunner(Protocol):
    def run(self, task: AgentTask) -> AgentResult:
        """Run one task and return the standard DoxAgent result contract."""


class MockAgentRunner:
    def __init__(
        self,
        registry: AgentRegistry | None = None,
        result_factory: Callable[[AgentTask], AgentResult] | None = None,
    ) -> None:
        self.registry = registry or default_agent_registry()
        self.result_factory = result_factory
        self.calls = 0

    def run(self, task: AgentTask) -> AgentResult:
        self.calls += 1
        definition = self.registry.get(task.agent_name)
        if self.result_factory is not None:
            return self.result_factory(task)
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={
                "agent_name": definition.agent_name.value,
                "task_type": task.task_type.value,
                "output_schema": definition.runtime.output_schema,
                "context_keys": sorted(task.input_context.keys()),
            },
        )


class MafAgentAdapter:
    """Placeholder adapter boundary for a future real MAF-backed runner."""

    def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.FAILED,
            error=AgentError(
                code="maf_adapter_not_configured",
                message="Real Microsoft Agent Framework execution is not configured in Phase 4.",
                retryable=False,
            ),
        )
