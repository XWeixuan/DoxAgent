"""D3 exports load on demand; read-only schema imports do not start agent tooling."""
from importlib import import_module

_EXPORTS = {
    'Document3AgentRunner': 'runner',
    'Document3InputPreparer': 'inputs',
    'PreparedDocument3Inputs': 'inputs',
    'Document3Orchestrator': 'orchestrator',
    'Document3RuntimeProjectionConsumer': 'runtime_projection',
    'project_policy_set': 'runtime_projection',
    'allocate_stable_policy_ids': 'identity',
    'apply_patch': 'assembler',
    'assemble_initial_policy_set': 'assembler',
    'build_coverage_map': 'assembler',
    **dict.fromkeys([
        'Document3PolicyRepository', 'HybridDocument3PolicyRepository',
        'InMemoryDocument3PolicyRepository', 'PostgresDocument3PolicyRepository',
        'SQLiteDocument3PolicyRepository', 'StalePolicySetBaseError',
    ], 'repository'),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module('.' + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
