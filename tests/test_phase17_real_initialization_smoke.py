from __future__ import annotations

import os

import pytest

from doxagent.models import DocumentType
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import BlackboardInitializationWorkflow, GlobalResearchInputs, WorkflowNode
from doxagent.workflows.storage import default_workflow_storage

pytestmark = pytest.mark.real_api

_EVAL_TICKER = "MU"
_EVAL_RESEARCH_INPUTS = GlobalResearchInputs(
    sector_or_theme="memory and storage semiconductors",
    industry_angle=(
        "DRAM and NAND pricing cycle, HBM demand, "
        "AI server memory mix, and capex discipline"
    ),
    universe=["MU"],
    benchmarks=["SOXX", "QQQ"],
    peers=["WDC", "STX", "SNDK"],
)


def _real_initialization_enabled() -> None:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        pytest.skip("Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API and model quota.")


def _persistent_smoke_settings() -> DoxAgentSettings:
    _real_initialization_enabled()
    settings = DoxAgentSettings()
    if settings.storage_mode != "postgres":
        pytest.skip(
            "Set DOXAGENT_STORAGE_MODE=postgres so real initialization smoke runs persist to DB."
        )
    if not settings.database_url:
        pytest.skip("Set DOXAGENT_DATABASE_URL so real initialization smoke runs persist to DB.")
    return settings


def _assert_run_visible_to_storage(
    settings: DoxAgentSettings,
    run_id: str,
):
    storage = default_workflow_storage(settings)
    run = storage.blackboard.get_run(run_id)
    assert run.run_id == run_id
    assert run.ticker == _EVAL_TICKER
    return run


def test_real_initialization_build_global_research_smoke() -> None:
    settings = _persistent_smoke_settings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)

    result = workflow.run(
        _EVAL_TICKER,
        research_inputs=_EVAL_RESEARCH_INPUTS,
        stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )

    assert result.error is None
    assert WorkflowNode.BUILD_GLOBAL_RESEARCH in result.checkpoint.completed_nodes
    run = _assert_run_visible_to_storage(settings, result.checkpoint.run_id)
    assert run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH)


def test_real_initialization_expectation_units_smoke() -> None:
    settings = _persistent_smoke_settings()
    workflow = BlackboardInitializationWorkflow(execution_mode="agent_runner", settings=settings)

    result = workflow.run(
        _EVAL_TICKER,
        research_inputs=_EVAL_RESEARCH_INPUTS,
        stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    )

    assert result.error is None
    assert WorkflowNode.GENERATE_EXPECTATION_DETAILS in result.checkpoint.completed_nodes
    assert len(result.checkpoint.pending_patches) in {1, 2, 3}
    _assert_run_visible_to_storage(settings, result.checkpoint.run_id)
    assert result.checkpoint.pending_patches
