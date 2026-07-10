"""Run real information-source tools through ReAct observation chunking.

The script deliberately emits only structural metrics. Complete provider payloads stay
inside the process and are never copied into LangSmith or the terminal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langsmith import Client as LangSmithClient

from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.models import AgentName, AgentPermissions
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.schema import ToolRequest, ToolResult

MAX_BLOCK_CHARS = 16_000
VALIDATION_TAG = "react-memory-real-tool-chunking-2026-07-11"

CASES: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    "company": [
        ("sec.company_facts_and_filings", "META", {"ticker": "META", "cik": "1326801"}),
        (
            "sec.filing_sections",
            "META",
            {
                "ticker": "META",
                "cik": "1326801",
                "form": "10-K",
                "items": ["Item 1A", "Item 7"],
            },
        ),
        ("alpha.company_overview", "META", {"ticker": "META"}),
        ("alpha.financial_statements", "META", {"ticker": "META"}),
        ("alpha.shares_outstanding", "META", {"ticker": "META"}),
        ("alpha.earnings_events", "META", {"ticker": "META"}),
        ("yfinance.hk_basic_snapshot", "0700.HK", {"ticker": "0700.HK"}),
    ],
    "macro": [
        (
            "fred.series_observations",
            "META",
            {
                "series_ids": ["DGS10", "CPIAUCSL"],
                "start_date": "2025-01-01",
                "limit": 24,
            },
        ),
        (
            "bls.timeseries",
            "META",
            {
                "series_ids": ["CUUR0000SA0", "CES0000000001"],
                "start_year": "2025",
                "end_year": "2026",
            },
        ),
        (
            "bea.nipa_data",
            "META",
            {
                "table_name": "T10101",
                "line_number": "1",
                "year": "LAST5",
                "frequency": "Q",
            },
        ),
        ("fed.fomc_calendar_materials", "META", {"year": "2026"}),
        (
            "polymarket.market_probability",
            "META",
            {"query": "Federal Reserve rate cut in 2026", "limit": 5},
        ),
        (
            "twelvedata.daily_ohlcv",
            "META",
            {"ticker": "META", "outputsize": 60},
        ),
        ("yfinance.daily_ohlcv", "META", {"ticker": "META", "outputsize": 60}),
    ],
    "industry_search": [
        ("finnhub.company_peers", "META", {"ticker": "META"}),
        ("fmp.sector_performance", "META", {"exchange": "NASDAQ"}),
        (
            "tavily.search",
            "META",
            {
                "query": "Meta Platforms 2026 capital expenditure guidance",
                "topic": "news",
                "search_depth": "basic",
                "max_results": 5,
            },
        ),
        (
            "tavily.extract",
            "META",
            {
                "urls": ["https://investor.atmeta.com/"],
                "extract_depth": "basic",
            },
        ),
        (
            "anysearch.search",
            "META",
            {
                "query": "Meta Platforms 2026 capital expenditure guidance",
                "max_results": 5,
                "content_types": ["web", "news"],
            },
        ),
    ],
    "runtime": [
        (
            "finnhub.trade_stream",
            "META",
            {"ticker": "META", "duration_seconds": 1, "max_events": 10},
        ),
        ("monitoring.get_ticker_config", "META", {"ticker": "META"}),
        ("monitoring.list_status", "META", {"ticker": "META", "limit": 5}),
        ("monitoring.recent_events", "META", {"ticker": "META", "limit": 5}),
    ],
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _is_source_derived(
    content: Any,
    block_type: str,
    source_digests: set[str],
    source_strings: list[str],
) -> bool:
    if block_type == "outline":
        return True
    if block_type in {"table", "time_series"} and isinstance(content, dict):
        rows = content.get("rows")
        return isinstance(rows, list) and all(_digest(row) in source_digests for row in rows)
    if block_type == "text" and isinstance(content, str):
        return any(content in candidate for candidate in source_strings)
    return _digest(content) in source_digests


def _shape_profile(source: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []

    def visit(value: Any, path: str, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(value, dict):
            nodes.append(
                {
                    "path": path,
                    "kind": "object",
                    "field_count": len(value),
                    "chars": len(_canonical(value)),
                }
            )
            for key, item in value.items():
                visit(item, f"{path}/{key}", depth + 1)
        elif isinstance(value, list):
            nodes.append(
                {
                    "path": path,
                    "kind": "array",
                    "item_count": len(value),
                    "chars": len(_canonical(value)),
                }
            )
            for index, item in enumerate(value[:100]):
                visit(item, f"{path}/{index}", depth + 1)
        elif isinstance(value, str) and len(value) > 1_000:
            nodes.append(
                {
                    "path": path,
                    "kind": "text",
                    "chars": len(value),
                }
            )

    visit(source, "", 0)
    largest = sorted(nodes, key=lambda item: int(item["chars"]), reverse=True)[:12]
    return {
        "top_level": {key: type(value).__name__ for key, value in source.items()},
        "largest_nodes": largest,
    }


def _langsmith_client(settings: DoxAgentSettings) -> LangSmithClient | None:
    if not settings.langsmith_api_key or not settings.langsmith_project:
        return None
    return LangSmithClient(
        api_url=settings.langsmith_endpoint,
        api_key=settings.langsmith_api_key,
    )


def _trace_start(
    client: LangSmithClient | None,
    settings: DoxAgentSettings,
    tool_name: str,
    ticker: str,
    input_payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    if client is None:
        return None, "langsmith_not_configured"
    run_id = str(uuid4())
    try:
        client.create_run(
            f"react_memory_validation.{tool_name}.REAL1",
            {"ticker": ticker, "input": input_payload},
            "tool",
            id=run_id,
            project_name=settings.langsmith_project,
            tags=[VALIDATION_TAG, "real-provider-call", "payload-redacted"],
            extra={
                "metadata": {
                    "validation": "react_memory_structural_chunking",
                    "tool_name": tool_name,
                    "raw_payload_persisted": False,
                }
            },
        )
        return run_id, None
    except Exception as exc:  # validation must still exercise the provider
        return None, f"{type(exc).__name__}: {exc}"


def _trace_finish(
    client: LangSmithClient | None,
    run_id: str | None,
    summary: dict[str, Any],
) -> str | None:
    if client is None or run_id is None:
        return None
    try:
        client.update_run(
            run_id,
            end_time=datetime.now(UTC),
            outputs=summary,
            error=(summary.get("error") if summary.get("status") == "failed" else None),
        )
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _validate_result(
    tool_call_id: str,
    input_payload: dict[str, Any],
    result: ToolResult,
    descriptor: Any,
) -> dict[str, Any]:
    service = ObservationService()
    index = service.ingest(
        tool_call_id=tool_call_id,
        step=1,
        input_payload=input_payload,
        result=result,
        declared_policy=descriptor.observation_policy,
        adapter=descriptor.observation_adapter,
    )
    blocks = service.block_store.blocks_for_call(tool_call_id)
    block_chars = [len(_canonical(block.content)) for block in blocks]
    hashes_ok = all(block.content_hash == _digest(block.content) for block in blocks)
    reads_ok = all(
        (loaded := service.read(block.ref))
        and len(loaded) == 1
        and _digest(loaded[0].content) == _digest(block.content)
        for block in blocks
    )
    source_values = list(_walk(result.output))
    source_digests = {_digest(value) for value in source_values}
    source_strings = [value for value in source_values if isinstance(value, str)]
    source_derived = all(
        _is_source_derived(
            block.content,
            block.block_type,
            source_digests,
            source_strings,
        )
        for block in blocks
    )
    raw_record = service.raw_store.get(tool_call_id)
    raw_exact = raw_record is not None and raw_record.result == result
    original_chars = len(_canonical(result.output))
    oversized = sum(size > MAX_BLOCK_CHARS for size in block_chars)
    assessable = bool(result.output) and result.status.value != "failed"
    structure_ok = (
        assessable
        and bool(blocks)
        and oversized == 0
        and hashes_ok
        and reads_ok
        and source_derived
        and raw_exact
    )
    return {
        "status": result.status.value,
        "error": result.error.code if result.error else None,
        "policy": index.policy,
        "adapter": descriptor.observation_adapter,
        "original_chars": original_chars,
        "shape": _shape_profile(result.output),
        "block_count": len(blocks),
        "block_types": sorted({block.block_type for block in blocks}),
        "largest_block_chars": max(block_chars, default=0),
        "oversized_block_count": oversized,
        "raw_exact": raw_exact,
        "block_hashes_ok": hashes_ok,
        "read_observation_exact": reads_ok,
        "source_derived": source_derived,
        "structure_ok": structure_ok,
        "refs": [block.ref for block in blocks[:8]],
    }


def _dynamic_input(
    tool_name: str,
    input_payload: dict[str, Any],
    prior_results: dict[str, ToolResult],
) -> dict[str, Any]:
    resolved = dict(input_payload)
    if tool_name == "sec.filing_sections":
        company = prior_results.get("sec.company_facts_and_filings")
        if company:
            recent = company.output.get("submissions", {}).get("recent_filings", [])
            filing = next(
                (item for item in recent if isinstance(item, dict) and item.get("form") == "10-K"),
                None,
            )
            if filing:
                resolved["accession"] = filing.get("accession")
                resolved["primary_document"] = filing.get("primary_document")
    if tool_name == "tavily.extract":
        search = prior_results.get("tavily.search")
        if search:
            candidates = search.output.get("results")
            if isinstance(candidates, list):
                first_url = next(
                    (
                        item.get("url")
                        for item in candidates
                        if isinstance(item, dict) and item.get("url")
                    ),
                    None,
                )
                if first_url:
                    resolved["urls"] = [first_url]
    return resolved


def run(group: str) -> int:
    settings = DoxAgentSettings()
    registry = default_real_tool_registry(settings)
    langsmith = _langsmith_client(settings)
    groups = CASES if group == "all" else {group: CASES[group]}
    prior_results: dict[str, ToolResult] = {}
    failed_validations = 0
    for group_name, cases in groups.items():
        for position, (tool_name, ticker, base_input) in enumerate(cases, start=1):
            input_payload = _dynamic_input(tool_name, base_input, prior_results)
            descriptor = registry.describe(tool_name)
            if descriptor is None:
                raise RuntimeError(f"descriptor missing for {tool_name}")
            trace_id, trace_start_error = _trace_start(
                langsmith,
                settings,
                tool_name,
                ticker,
                input_payload,
            )
            request = ToolRequest(
                tool_name=tool_name,
                ticker=ticker,
                agent_name=AgentName.SYSTEM,
                input=input_payload,
                metadata={
                    "validation": "react_memory_structural_chunking",
                    "run_mode": "real_provider",
                },
            )
            try:
                result = registry.call(
                    request,
                    AgentPermissions(allowed_tools=[tool_name]),
                )
                summary = _validate_result(
                    f"{group_name}_{position}",
                    input_payload,
                    result,
                    descriptor,
                )
                prior_results[tool_name] = result
            except Exception as exc:
                summary = {
                    "status": "exception",
                    "error": f"{type(exc).__name__}: {exc}",
                    "structure_ok": False,
                }
            summary.update(
                {
                    "group": group_name,
                    "tool_name": tool_name,
                    "langsmith_run_id": trace_id,
                    "langsmith_start_error": trace_start_error,
                }
            )
            summary["langsmith_finish_error"] = _trace_finish(langsmith, trace_id, summary)
            if not summary.get("structure_ok"):
                failed_validations += 1
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if failed_validations else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", *CASES], default="all")
    args = parser.parse_args()
    return run(args.group)


if __name__ == "__main__":
    raise SystemExit(main())
