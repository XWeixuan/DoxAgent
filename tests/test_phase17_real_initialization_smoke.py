from __future__ import annotations

import os

import pytest

from doxagent.workflows import BlackboardInitializationWorkflow, GlobalResearchInputs, WorkflowNode

pytestmark = pytest.mark.real_api


def _real_initialization_enabled() -> None:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        pytest.skip("Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API and model quota.")


def test_real_initialization_build_global_research_smoke() -> None:
    _real_initialization_enabled()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner")

    result = workflow.run(
        "ASTS",
        research_inputs=GlobalResearchInputs(
            sector_or_theme="satellite direct-to-device communications",
            industry_angle="commercialization milestones and network deployment",
            universe=["ASTS"],
            benchmarks=["SPY"],
            peers=["RKLB"],
        ),
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )

    assert result.error is None
    assert WorkflowNode.BUILD_GLOBAL_RESEARCH in result.checkpoint.completed_nodes


def test_real_initialization_expectation_units_smoke() -> None:
    _real_initialization_enabled()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner")

    result = workflow.run(
        "ASTS",
        research_inputs=GlobalResearchInputs(
            sector_or_theme="satellite direct-to-device communications",
            industry_angle="commercialization milestones and network deployment",
            universe=["ASTS"],
            benchmarks=["SPY"],
            peers=["RKLB"],
        ),
        stop_after=WorkflowNode.GENERATE_EXPECTATION_UNITS,
    )

    assert result.error is None
    assert WorkflowNode.GENERATE_EXPECTATION_UNITS in result.checkpoint.completed_nodes
    assert len(result.checkpoint.pending_patches) in {1, 2, 3}
