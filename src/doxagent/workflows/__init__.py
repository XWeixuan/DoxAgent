"""Workflow exports are lazy; reading contracts does not import agent runners."""
from importlib import import_module

_EXPORTS = {'InMemoryWorkflowCheckpointRepository': 'doxagent.workflows.checkpoint_repository', 'PostgresWorkflowCheckpointRepository': 'doxagent.workflows.checkpoint_repository', 'WorkflowCheckpointRecord': 'doxagent.workflows.checkpoint_repository', 'WorkflowCheckpointRepository': 'doxagent.workflows.checkpoint_repository', 'WorkflowContractError': 'doxagent.workflows.errors', 'WorkflowDependencyError': 'doxagent.workflows.errors', 'WorkflowError': 'doxagent.workflows.errors', 'GlobalResearchAssembler': 'doxagent.workflows.global_research', 'GlobalResearchInputs': 'doxagent.workflows.global_research', 'GlobalResearchModuleRunner': 'doxagent.workflows.global_research', 'INITIALIZATION_NODES': 'doxagent.workflows.initialization', 'BlackboardInitializationWorkflow': 'doxagent.workflows.initialization', 'InitializationMockResultFactory': 'doxagent.workflows.initialization', 'WorkflowAgentResultNormalizer': 'doxagent.workflows.normalizer', 'WorkflowCheckpoint': 'doxagent.workflows.schema', 'WorkflowExecutionResult': 'doxagent.workflows.schema', 'WorkflowNode': 'doxagent.workflows.schema', 'WorkflowNodeStatus': 'doxagent.workflows.schema', 'WorkflowRunStatus': 'doxagent.workflows.schema', 'WorkflowRunSummary': 'doxagent.workflows.schema', 'WorkflowStorage': 'doxagent.workflows.storage', 'default_workflow_storage': 'doxagent.workflows.storage'}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value
