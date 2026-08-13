from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.data_runtime.contracts import (
    DataAvailability,
    DataExecutionContext,
    DataToolContract,
    build_data_tool_contracts,
)
from doxagent.data_runtime.execution import DataExecutionCore, _availability
from doxagent.data_runtime.guidance import DataToolGuide
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.source_capture_server import ObservationSourceRepository
from doxagent.models import ResultStatus
from doxagent.observations.kernel import ObservationKernel
from doxagent.observations.models import PersistedObservation
from doxagent.observations.pack import _publish_directory
from doxagent.observations.profiles import apply_output_profile
from doxagent.observations.promotion import CitationPromotionService
from doxagent.observations.store import AttemptObservationStore
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.registry import ToolDescriptor, ToolRegistry
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class _RecordingClient:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.requests: list[ToolRequest] = []

    def call(self, request: ToolRequest) -> ToolResult:
        self.requests.append(request)
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output=self.output,
            output_summary="result token=must-not-leak",
        )


def _hash(content: object) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _store(tmp_path: Path, *, run_id: str = "run-1", attempt_id: str = "c1-1"):
    run_root = tmp_path / run_id
    run_root.mkdir(exist_ok=True)
    store = AttemptObservationStore(
        control_root=tmp_path / ".control" / run_id / attempt_id,
        mirror_root=run_root / "attempts" / attempt_id / "audit" / "observations",
        run_id=run_id,
        attempt_id=attempt_id,
    )
    return run_root, store


def _observation(index: int, *, run_id: str = "run-1", attempt_id: str = "c1-1"):
    content = {"index": index, "value": f"value-{index}"}
    return PersistedObservation(
        run_id=run_id,
        attempt_id=attempt_id,
        block_id=f"block-{index}",
        tool_call_id=f"call-{index}",
        tool_name="test.lookup",
        title=f"Row {index}",
        locator=f"/rows/{index}",
        block_type="json",
        content=content,
        content_hash=_hash(content),
        source_locator=f"test://rows/{index}",
        provider="Test provider",
        method_version="test.lookup/1.0",
    )


def test_contracts_cover_registry_and_exclude_known_unavailable_tools() -> None:
    registry = default_real_tool_registry(DoxAgentSettings(IBKR_TWS_ENABLED=False))
    contracts = build_data_tool_contracts(registry)
    assert len(contracts.all()) == len(registry.names()) - 2
    assert len({item.mcp_name for item in contracts.all()}) == len(registry.names()) - 2
    assert contracts.get("tavily.search") is None
    assert contracts.get("anysearch.search") is None
    assert not contracts.require("benzinga.analyst_events").exposed
    assert not contracts.require("fmp.valuation_snapshot").exposed
    assert not contracts.require("ibkr.market_snapshot").exposed
    assert contracts.get("finnhub.fund_ownership") is None
    policy = DataToolPolicyRegistry()
    for node, role in (
        (CodexD1Node.C1, CodexAgentRole.C1),
        (CodexD1Node.C2, CodexAgentRole.C2),
        (CodexD1Node.C3, CodexAgentRole.C3),
        (CodexD1Node.C4_PRE_SCAN, CodexAgentRole.C4),
        (CodexD1Node.O4_A, CodexAgentRole.O4),
    ):
        assert policy.allowed_tools(node, role).issubset(registry.names())


def test_c1_guide_is_category_strict_sec_first_and_exposes_sell_side_gap() -> None:
    registry = default_real_tool_registry(DoxAgentSettings(IBKR_TWS_ENABLED=False))
    contracts = build_data_tool_contracts(registry)
    allowed = DataToolPolicyRegistry().allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    guide = DataToolGuide(contracts)

    financials = guide.recommend(
        task="current NVDA financial actuals",
        effective_tool_ids=allowed,
        business_category="company_financials",
    )
    consensus = guide.recommend(
        task="current NVDA revenue and EPS consensus",
        effective_tool_ids=allowed,
        business_category="sell_side_consensus",
    )

    assert financials["candidates"][0]["canonical_tool_id"] == "sec.company_financials"
    assert [item["canonical_tool_id"] for item in consensus["candidates"]] == [
        "yfinance.sell_side_consensus",
        "alpha.earnings_events",
    ]
    assert {
        item["canonical_tool_id"] for item in consensus["unavailable_gaps"]
    } >= {"twelvedata.sell_side_estimates", "fmp.sell_side_estimates"}


def test_ibkr_contracts_are_exposed_only_when_local_tws_is_enabled() -> None:
    registry = default_real_tool_registry(DoxAgentSettings(IBKR_TWS_ENABLED=True))
    contracts = build_data_tool_contracts(registry)

    assert contracts.require("ibkr.contract_search").exposed
    assert contracts.require("ibkr.market_snapshot").exposed
    assert contracts.require("ibkr.market_history").exposed
    assert contracts.require("ibkr.trade_tape").exposed
    assert contracts.require("ibkr.market_history").source_name == "Interactive Brokers TWS"
    assert contracts.require("market.daily_ohlcv").exposed
    assert contracts.require("market.quote_snapshot").exposed
    assert contracts.require("market.trade_tape").exposed


def test_ibkr_history_profile_keeps_business_rows_and_folds_request_metadata() -> None:
    contract = build_data_tool_contracts(
        default_real_tool_registry(DoxAgentSettings(IBKR_TWS_ENABLED=True))
    ).require("ibkr.market_history")

    profiled = apply_output_profile(
        contract,
        {
            "provider": "ibkr",
            "transport": "official_tws_socket",
            "symbol": "MU",
            "con_id": 9939,
            "period": "1m",
            "bar": "1d",
            "outside_rth": False,
            "as_of": "20260807",
            "bars": [{"date": "20260807", "close": 877.57}],
            "source_coordinates": {"source_id": "ibkr:history:9939"},
        },
    )

    assert profiled.content == {"bars": [{"date": "20260807", "close": 877.57}]}
    assert profiled.envelope["con_id"] == 9939
    assert profiled.envelope["period"] == "1m"


def test_ibkr_snapshot_data_gap_is_degraded_not_provider_unavailable() -> None:
    contract = build_data_tool_contracts(
        default_real_tool_registry(DoxAgentSettings(IBKR_TWS_ENABLED=True))
    ).require("ibkr.market_snapshot")
    result = ToolResult(
        tool_name="ibkr.market_snapshot",
        status=ResultStatus.FAILED,
        error=ToolError(
            code="market_data_unavailable",
            message="TWS returned no usable ticks.",
        ),
    )

    assert _availability(contract, result) is DataAvailability.DEGRADED


def test_attempt_capability_is_signed_scoped_and_tamper_evident() -> None:
    codec = DataCapabilityCodec("x" * 40)
    token = codec.issue(
        run_id="run-1",
        node_id=CodexD1Node.C1,
        node_attempt_id="c1-1",
        agent_role=CodexAgentRole.C1,
        ticker="mu",
        cutoff_at=datetime(2026, 8, 9, tzinfo=UTC),
        enabled_tool_ids=["sec.company_financials"],
    )
    claims = DataCapabilityCodec.verify(token, public_key=codec.public_key)
    assert claims.ticker == "MU"
    assert claims.node_attempt_id == "c1-1"
    with pytest.raises(CapabilityDenied):
        DataCapabilityCodec.verify(
            token[:-1] + ("A" if token[-1] != "A" else "B"),
            public_key=codec.public_key,
        )
    mismatched = codec.issue(
        run_id="run-1",
        node_id=CodexD1Node.C1,
        node_attempt_id="c1-1",
        agent_role=CodexAgentRole.C2,
        ticker="MU",
        cutoff_at=datetime(2026, 8, 9, tzinfo=UTC),
        enabled_tool_ids=[],
    )
    with pytest.raises(CapabilityDenied):
        DataCapabilityCodec.verify(mismatched, public_key=codec.public_key)


def test_c1_effective_tools_hide_hk_only_snapshot_for_us_ticker() -> None:
    codec = DataCapabilityCodec("m" * 40)
    policy = DataToolPolicyRegistry()
    allowed = policy.allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    token = codec.issue(
        run_id="run-market-filter",
        node_id=CodexD1Node.C1,
        node_attempt_id="c1-1",
        agent_role=CodexAgentRole.C1,
        ticker="NVDA",
        cutoff_at=datetime(2026, 8, 12, tzinfo=UTC),
        enabled_tool_ids=allowed,
    )
    claims = DataCapabilityCodec.verify(token, public_key=codec.public_key)

    assert "yfinance.hk_basic_snapshot" not in policy.effective_tools(claims)


def test_store_allocates_unique_attempt_local_aliases_concurrently(tmp_path: Path) -> None:
    _run_root, store = _store(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        saved = list(pool.map(store.save_observation, [_observation(i) for i in range(24)]))
    assert {item.alias for item in saved} == {f"O{i}" for i in range(1, 25)}
    assert [item.alias for item in store.list_all()] == [f"O{i}" for i in range(1, 25)]
    assert store.save_observation(_observation(0)).alias == saved[0].alias


def test_source_capture_and_data_tools_share_one_attempt_alias_sequence(tmp_path: Path) -> None:
    run_root, store = _store(tmp_path)
    first = store.save_observation(_observation(1))
    repository = ObservationSourceRepository(
        run_root=run_root,
        control_root=store.control_root,
        run_id="run-1",
        attempt_id="c1-1",
    )
    from doxagent.codex_runtime.schema import SourceRecord

    captured = SourceRecord(
        source_id="capture-1",
        run_id="run-1",
        attempt_id="c1-1",
        alias="O1",
        url="https://example.test/source",
        source="Example",
        title="Captured source",
        captured_text="Useful captured paragraph.",
    )
    repository.save_source(captured)
    assert first.alias == "O1"
    assert captured.alias == "O2"
    assert store.read_alias("O2").content == "Useful captured paragraph."


def test_worker_reads_private_canonical_observations_and_deletes_retention_scope(
    tmp_path: Path,
) -> None:
    workspaces = LocalWorkspaceStore(tmp_path / "workspaces")
    run_root = workspaces.ensure_attempt("run-1", "c1-1").parent.parent
    control_root = workspaces.root / ".control" / "run-1" / "c1-1"
    store = AttemptObservationStore(
        control_root=control_root,
        mirror_root=run_root / "attempts" / "c1-1" / "audit" / "observations",
        run_id="run-1",
        attempt_id="c1-1",
    )
    store.save_observation(_observation(1))
    canonical = workspaces.read_attempt_observations("run-1", "c1-1")
    assert [item.alias for item in canonical] == ["O1"]
    workspaces.delete_attempt("run-1", "c1-1")
    assert not (run_root / "attempts" / "c1-1").exists()
    assert not control_root.exists()


def test_execution_injects_scope_and_returns_clean_inline_observations(tmp_path: Path) -> None:
    client = _RecordingClient(
        {
            "rows": [{"date": "2026-08-08", "value": 42}],
            "source_url": "https://example.test/data?api_key=secret-value",
            "note": "Authorization=hidden-value and Bearer abc.def.ghi",
        }
    )
    tools = ToolRegistry()
    tools.register(
        "test.lookup",
        client,
        descriptor=ToolDescriptor(
            name="test.lookup",
            description="Test lookup.",
            input_fields=["symbol", "limit"],
            source_name="Test provider",
            business_categories=["company_financials"],
            observation_adapter="json",
        ),
    )
    contracts = build_data_tool_contracts(tools)
    run_root, store = _store(tmp_path)
    kernel = ObservationKernel(store=store, run_root=run_root)
    execution = DataExecutionCore(
        tools=tools,
        contracts=contracts,
        context=DataExecutionContext(
            run_id="run-1",
            node_id=CodexD1Node.C1,
            node_attempt_id="c1-1",
            agent_role=CodexAgentRole.C1,
            ticker="MU",
            cutoff_at=datetime(2026, 8, 9, tzinfo=UTC),
            enabled_tool_ids=frozenset({"test.lookup"}),
        ),
        observations=kernel,
    )
    result = execution.execute("test.lookup", {"limit": 3})
    assert result.execution_status is ResultStatus.SUCCEEDED
    assert result.delivery.mode == "inline"
    assert result.delivery.observations[0].alias == "O1"
    assert client.requests[0].input == {"limit": 3, "symbol": "MU"}
    serialized = result.model_dump_json()
    assert "secret-value" not in serialized
    assert "hidden-value" not in serialized
    assert "abc.def.ghi" not in serialized
    assert "must-not-leak" not in serialized
    assert all(item.title != "/source_coordinates" for item in result.delivery.observations)


def test_observation_utf8_roundtrip_and_mojibake_marker(tmp_path: Path) -> None:
    run_root, store = _store(tmp_path)
    kernel = ObservationKernel(store=store, run_root=run_root)
    contract = DataToolContract(
        canonical_tool_id="test.utf8",
        mcp_name="test_utf8",
        source_name="Test provider",
        business_categories=["company_financials"],
        description="UTF-8 test.",
        business_purpose="UTF-8 roundtrip.",
        use_when=["testing"],
        avoid_when=["never"],
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_profile="json",
        observation_adapter="json",
    )
    kernel.ingest_tool_result(
        tool_call_id="call-clean",
        contract=contract,
        input_payload={},
        result=ToolResult(
            tool_name="test.utf8",
            status=ResultStatus.SUCCEEDED,
            output={"text": "管理层称：需求仍然强劲—但供应受限。"},
        ),
        availability=DataAvailability.AVAILABLE,
    )
    kernel.ingest_tool_result(
        tool_call_id="call-bad",
        contract=contract,
        input_payload={},
        result=ToolResult(
            tool_name="test.utf8",
            status=ResultStatus.SUCCEEDED,
            output={"text": "broken \ufffd text"},
        ),
        availability=DataAvailability.AVAILABLE,
    )

    clean = store.read_alias("O1")
    bad = store.read_alias("O2")
    assert clean is not None and clean.content == "管理层称：需求仍然强劲—但供应受限。"
    assert clean.metadata["text_encoding"] == "utf-8"
    assert clean.metadata["mojibake_suspected"] is False
    assert bad is not None and bad.metadata["mojibake_suspected"] is True


def test_large_results_materialize_bounded_observation_pack(tmp_path: Path) -> None:
    run_root, store = _store(tmp_path)
    kernel = ObservationKernel(
        store=store,
        run_root=run_root,
        inline_char_limit=200,
        selected_char_limit=300,
    )
    contract = DataToolContract(
        canonical_tool_id="test.rows",
        mcp_name="test_rows",
        source_name="Test provider",
        business_categories=["macro"],
        description="Test rows.",
        business_purpose="Test large delivery.",
        use_when=["testing"],
        avoid_when=["never"],
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_profile="table",
        observation_adapter="table",
    )
    result = ToolResult(
        tool_name="test.rows",
        status=ResultStatus.SUCCEEDED,
        output={
            "rows": [
                {"date": f"2026-07-{index + 1:02d}", "value": "x" * 120} for index in range(25)
            ]
        },
    )
    delivery = kernel.ingest_tool_result(
        tool_call_id="call-large",
        contract=contract,
        input_payload={},
        result=result,
        availability=DataAvailability.AVAILABLE,
    )
    assert delivery.mode == "pack"
    assert delivery.pack is not None
    assert delivery.inline_chars <= 12_000
    assert (run_root / delivery.pack.manifest_path).is_file()
    assert (run_root / delivery.pack.catalog_path).is_file()
    assert (run_root / delivery.pack.selected_path).is_file()
    assert kernel.read_observation("O1") is not None
    assert kernel.read_observation("O999") is None


def test_pack_publish_retries_bounded_windows_access_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    staging.mkdir()
    real_rename = os.rename
    attempts = 0

    def flaky_rename(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError(13, "access denied")
            error.winerror = 5  # type: ignore[attr-defined]
            raise error
        real_rename(source, destination)

    monkeypatch.setattr("doxagent.observations.pack.os.name", "nt")
    monkeypatch.setattr("doxagent.observations.pack.os.rename", flaky_rename)
    monkeypatch.setattr("doxagent.observations.pack.time.sleep", lambda _seconds: None)

    _publish_directory(staging, target)

    assert attempts == 3
    assert target.is_dir()


def test_promotion_is_cited_only_and_keeps_attempt_local_o_numbers(tmp_path: Path) -> None:
    repository = InMemoryCodexRuntimeRepository()
    promotion = CitationPromotionService(repository)
    first = _observation(1, attempt_id="c1-1")
    second = _observation(2, attempt_id="c1-1")
    first.alias = "O1"
    second.alias = "O2"
    manifest_one = promotion.promote(
        run_id="run-1",
        attempt_id="c1-1",
        artifact_id="artifact-c1",
        anchor="c1",
        markdown="Claim 【cite:O1】 and unresolved 【cite:O9】.",
        observations=[first, second],
    )
    assert [item.alias for item in manifest_one.entries] == ["O1", "O9"]
    assert len(repository.list_sources("run-1")) == 1
    other = _observation(3, attempt_id="c2-1")
    other.alias = "O1"
    manifest_two = promotion.promote(
        run_id="run-1",
        attempt_id="c2-1",
        artifact_id="artifact-c2",
        anchor="c2",
        markdown="Other claim 【cite:O1】.",
        observations=[other],
    )
    merged = promotion.merge(
        run_id="run-1",
        artifact_id="document1",
        manifests=[manifest_one, manifest_two],
    )
    resolved_o1 = [entry for entry in merged.entries if entry.alias == "O1" and entry.resolved]
    assert {entry.attempt_id for entry in resolved_o1} == {"c1-1", "c2-1"}
    assert {entry.anchor for entry in resolved_o1} == {"c1", "c2"}


@pytest.mark.asyncio
async def test_stdio_server_lists_only_authorized_tools_and_calls_guide(tmp_path: Path) -> None:
    run_root = tmp_path / "run-stdio"
    run_root.mkdir()
    control_root = tmp_path / ".control" / "run-stdio" / "o4-1"
    store = AttemptObservationStore(
        control_root=control_root,
        mirror_root=run_root / "attempts" / "o4-1" / "audit" / "observations",
        run_id="run-stdio",
        attempt_id="o4-1",
    )
    observation = _observation(1, run_id="run-stdio", attempt_id="o4-1")
    observation.content = {"index": 1, "value": "project-me"}
    store.save_observation(observation)
    codec = DataCapabilityCodec("s" * 40)
    policy = DataToolPolicyRegistry()
    allowed = policy.allowed_tools(CodexD1Node.O4_A, CodexAgentRole.O4)
    token = codec.issue(
        run_id="run-stdio",
        node_id=CodexD1Node.O4_A,
        node_attempt_id="o4-1",
        agent_role=CodexAgentRole.O4,
        ticker="MU",
        cutoff_at=datetime(2026, 8, 9, tzinfo=UTC),
        enabled_tool_ids=allowed,
    )
    environment = {
        **os.environ,
        "DOXAGENT_DATA_MCP_CAPABILITY": token,
        "DOXAGENT_DATA_MCP_PUBLIC_KEY": codec.public_key,
        "DOXAGENT_OBSERVATION_CONTROL_ROOT": str(control_root),
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "doxagent.mcp.data_server"],
        env=environment,
        cwd=run_root,
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {item.name for item in listed.tools}
            assert "data_tool_guide" in names
            assert "data_validate_citations" in names
            assert "data_read_observation" in names
            assert "market_daily_ohlcv" in names
            assert "market_quote_snapshot" in names
            assert "market_trade_tape" in names
            assert "tavily_search" not in names
            assert "anysearch_search" not in names
            assert not any(name.startswith(("benzinga_", "fmp_", "ibkr_")) for name in names)
            result = await session.call_tool(
                "data_tool_guide",
                arguments={"task": "check daily price history", "business_category": "market_data"},
            )
            assert result.is_error is False
            assert result.structured_content["candidates"]
            projected = await session.call_tool(
                "data_read_observation",
                arguments={"alias": "O1", "keys": ["value"]},
            )
            assert projected.structured_content["content"] == {"value": "project-me"}
            citation = await session.call_tool(
                "data_validate_citations",
                arguments={"text": "可验证事实【cite:O1】；缺失事实【cite:O9】。"},
            )
            assert citation.structured_content["resolved_aliases"] == ["O1"]
            assert citation.structured_content["unresolved_aliases"] == ["O9"]
            assert citation.structured_content["valid"] is False
