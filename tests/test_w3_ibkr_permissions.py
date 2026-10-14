from datetime import UTC, datetime

import pytest

from doxagent.codex_runtime.schema import (
    CodexPersistentRuntimeAgentRole,
    CodexPersistentRuntimeNode,
)
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.data_server import GUIDE_TOOL_NAME, READ_TOOL_NAME, DataMcpApplication
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from tests.test_codex_runtime_v2 import _AsyncSdkClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase", [None, "w3_template", "trade_decision", "post_decision_operations"]
)
async def test_w3_sdk_exposes_only_ibkr_data_tools_and_scopes_capability(
    tmp_path, monkeypatch, phase
):
    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *a, **k: sdk)
    settings = DoxAgentSettings(ibkr_tws_enabled=True)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True,
                                settings=settings)
    run_root = tmp_path / "w3-run"
    run_root.mkdir()
    await runtime.start(WorkerRunRequest(
        workflow_version="persistent_runtime_w3_v1", research_lane="persistent_runtime",
        run_id="w3-run", ticker="MU", node=CodexPersistentRuntimeNode.W3,
        agent_role=CodexPersistentRuntimeAgentRole.W3, attempt_id="attempt",
        cutoff_at=datetime.now(UTC), prompt="offline", output_schema={"type": "object"},
        task_phase=phase, read_only=True, data_mcp_enabled=True, allow_subagents=False,
        model="gpt-6-sol", effort="medium",
    ), run_root)
    config = sdk.thread_start_kwargs["config"]
    assert sdk.thread_start_kwargs["model"] == "gpt-6-sol"
    assert config["mcp_servers.data.enabled"] is True
    assert config["mcp_servers.data.required"] is True
    assert config["mcp_servers.data.startup_timeout_sec"] == 90
    assert config["mcp_servers.source_capture.enabled"] is False
    allowed = DataToolPolicyRegistry().allowed_tools(
        CodexPersistentRuntimeNode.W3, CodexPersistentRuntimeAgentRole.W3
    )
    contracts = build_data_tool_contracts(default_real_tool_registry(settings))
    assert allowed == frozenset({"ibkr.market_history"})
    assert config["mcp_servers.data.enabled_tools"] == [GUIDE_TOOL_NAME, READ_TOOL_NAME] + [
        contracts.get(tool).mcp_name for tool in sorted(allowed)
    ]
    claims = DataCapabilityCodec.verify(
        config["mcp_servers.data.env.DOXAGENT_DATA_MCP_CAPABILITY"],
        public_key=config["mcp_servers.data.env.DOXAGENT_DATA_MCP_PUBLIC_KEY"],
    )
    assert set(claims.enabled_tool_ids) == allowed
    assert claims.read_only
    # Server-side policy also denies non-IBKR tools, even in an overbroad claim.
    claims.enabled_tool_ids += [
        "sec.filing_content", "market.quote_snapshot", "tavily.search",
        "ibkr.contract_search", "ibkr.market_snapshot", "ibkr.option_surface",
        "ibkr.trade_tape", "ibkr.historical_ticks", "ibkr.shortability_snapshot",
        "ibkr.fed_funds_curve",
    ]
    app = DataMcpApplication(claims=claims, run_root=run_root,
                             control_root=tmp_path / ".control" / "w3-run" / "attempt",
                             settings=settings)
    assert app.effective_tool_ids == allowed
    assert {c.canonical_tool_id for c in app.exposed_contracts()} == allowed
    assert all(c.read_only for c in app.exposed_contracts())


def test_w3_history_executes_through_data_mcp_with_case_window(tmp_path, monkeypatch):
    from doxagent.models import ResultStatus
    from doxagent.observations.profiles import apply_output_profile
    from tests.test_commercial_provider_tools import _FakeIbkrSession

    calls = []

    class Session(_FakeIbkrSession):
        def __init__(self, _config):
            super().__init__()

        def historical_bars(self, contract, **kwargs):
            calls.append((contract.symbol, kwargs))
            result = super().historical_bars(contract, **kwargs)
            result["use_rth"] = kwargs["use_rth"]
            return result

    monkeypatch.setattr("doxagent.tools.providers.ibkr.IbkrTwsSession", Session)
    codec = DataCapabilityCodec("s" * 32)
    token = codec.issue(
        workflow_version="persistent_runtime_w3_v1", research_lane="persistent_runtime",
        run_id="w3-run", node_id=CodexPersistentRuntimeNode.W3,
        node_attempt_id="attempt", agent_role=CodexPersistentRuntimeAgentRole.W3,
        ticker="MU", cutoff_at=datetime(2026, 10, 13, 13, tzinfo=UTC),
        enabled_tool_ids=["ibkr.market_history"],
    )
    run_root = tmp_path / "w3-run"
    run_root.mkdir()
    app = DataMcpApplication(
        claims=codec.verify(token, public_key=codec.public_key), run_root=run_root,
        control_root=tmp_path / ".control" / "w3-run" / "attempt",
        settings=DoxAgentSettings(ibkr_tws_enabled=True),
    )
    result = app.execution.execute("ibkr.market_history", {
        "bar": "10min", "duration": "7200 S", "outside_rth": True,
    })
    assert result.execution_status is ResultStatus.SUCCEEDED, result.error
    assert calls == [("MU", {"duration": "7200 S", "bar_size": "10 mins",
                            "use_rth": False, "end_datetime": "20261013 13:00:00 UTC"})]
    result = app.execution.execute("ibkr.market_history", {
        "bar": "1d", "duration": "2 D", "outside_rth": True,
        "end_datetime": "20261013 12:00:00 UTC",
    })
    assert result.execution_status is ResultStatus.SUCCEEDED, result.error
    assert calls[-1][1] == {"duration": "2 D", "bar_size": "1 day", "use_rth": False,
                            "end_datetime": "20261013 12:00:00 UTC"}
    failed = app.execution.execute("ibkr.market_history", {"duration": "-1 D"})
    assert failed.execution_status is ResultStatus.FAILED
    assert len(calls) == 2
    denied = app.execution.execute("ibkr.market_snapshot", {})
    assert denied.error["code"] == "tool_not_allowed"
    profile = apply_output_profile(app.contracts.require("ibkr.market_history"), {
        "bars": [{"date": "20261013", "close": 100}], "duration": "2 D",
        "bar_size": "1 day", "end_datetime": "20261013 12:00:00 UTC",
        "what_to_show": "TRADES",
    })
    assert profile.content == {"bars": [{"date": "20261013", "close": 100}]}
    assert profile.envelope["end_datetime"] == "20261013 12:00:00 UTC"
