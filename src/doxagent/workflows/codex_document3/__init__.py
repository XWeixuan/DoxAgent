"""DoxAgent V2 D3/O3 monitoring execution policy workflow."""

from .assembler import apply_patch, assemble_initial_policy_set, build_coverage_map
from .identity import allocate_stable_policy_ids
from .inputs import Document3InputPreparer, PreparedDocument3Inputs
from .orchestrator import Document3Orchestrator
from .repository import (
    Document3PolicyRepository,
    HybridDocument3PolicyRepository,
    InMemoryDocument3PolicyRepository,
    PostgresDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
    StalePolicySetBaseError,
)
from .runtime_projection import Document3RuntimeProjectionConsumer, project_policy_set

__all__ = [
    "Document3AgentRunner",
    "Document3InputPreparer",
    "Document3Orchestrator",
    "Document3PolicyRepository",
    "Document3RuntimeProjectionConsumer",
    "HybridDocument3PolicyRepository",
    "InMemoryDocument3PolicyRepository",
    "PostgresDocument3PolicyRepository",
    "PreparedDocument3Inputs",
    "SQLiteDocument3PolicyRepository",
    "StalePolicySetBaseError",
    "allocate_stable_policy_ids",
    "apply_patch",
    "assemble_initial_policy_set",
    "build_coverage_map",
    "project_policy_set",
]


from .runner import Document3AgentRunner
