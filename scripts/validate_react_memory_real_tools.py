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

MAX_BLOCK_CHARS = 1_200
MAX_AGENT_VISIBLE_RATIO = 1.25
MAX_SMALL_RESULT_OVERHEAD_CHARS = 1_024
VALIDATION_TAG = "react-memory-real-tool-chunking-2026-07-11"
PROFILED_COMPRESSION_TOOLS = {
    "alpha.financial_statements",
    "alpha.earnings_events",
    "fred.series_observations",
    "sec.company_facts_and_filings",
    "sec.filing_sections",
    "bea.nipa_data",
    "polymarket.market_probability",
    "doxa_get_analysis",
    "doxatlas.query",
    "doxa_get_narrative_report",
}

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
                "sections": ["Item 1A", "Item 7"],
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
                "series_ids": ["DGS10", "CPIAUCSL", "UNRATE"],
                "start_date": "2015-01-01",
                "limit": 1_000,
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
    "doxatlas": [
        (
            "doxa_get_narrative_report",
            "INTC",
            {"ticker": "INTC", "view": "agent_provenance"},
        ),
        (
            "doxatlas.query",
            "INTC",
            {"ticker": "INTC", "view": "agent_provenance"},
        ),
        ("doxa_query_analysis", "INTC", {"ticker": "INTC", "limit": 5}),
        ("doxa_get_analysis", "INTC", {"ticker": "INTC", "capsule_limit": 5}),
        ("doxa_query_propositions", "INTC", {"limit": 10}),
        ("doxa_get_ignored_propositions", "INTC", {}),
        ("doxa_get_social_result", "INTC", {"limit": 10}),
        (
            "doxa_get_social_result_detail",
            "INTC",
            {"content_mode": "preview", "preview_chars": 1_200},
        ),
        ("doxa_get_media_result", "INTC", {"limit": 10}),
        (
            "doxa_get_media_result_detail",
            "INTC",
            {"content_mode": "preview", "preview_chars": 1_200},
        ),
        (
            "doxa_get_event_source",
            "INTC",
            {"limit": 10, "content_mode": "preview", "preview_chars": 1_200},
        ),
        (
            "doxatlas.source_lookup",
            "INTC",
            {"limit": 10, "content_mode": "preview", "preview_chars": 1_200},
        ),
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


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def _first_named_scalar(value: Any, names: tuple[str, ...]) -> Any | None:
    for item in _walk_dicts(value):
        for name in names:
            candidate = item.get(name)
            if candidate not in (None, "", [], {}):
                return candidate
    return None


def _first_doxatlas_event_scope(value: Any) -> dict[str, Any]:
    """Find one internally consistent event scope without exposing provider content."""

    scope_fields = {
        "run_id",
        "narrative_code",
        "event_code",
        "narrative_id",
        "narrative_event_id",
    }

    def visit(item: Any, inherited: dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(item, dict):
            context = {
                **inherited,
                **{
                    key: candidate
                    for key, candidate in item.items()
                    if key in scope_fields and candidate not in (None, "", [], {})
                },
            }
            if context.get("narrative_event_id"):
                return {"narrative_event_id": context["narrative_event_id"]}
            if context.get("narrative_id") and context.get("event_code"):
                return {
                    "narrative_id": context["narrative_id"],
                    "event_code": context["event_code"],
                }
            if all(context.get(key) for key in ("run_id", "narrative_code", "event_code")):
                return {
                    "run_id": context["run_id"],
                    "narrative_code": context["narrative_code"],
                    "event_code": context["event_code"],
                }
            for child in item.values():
                found = visit(child, context)
                if found:
                    return found
        elif isinstance(item, list):
            for child in item:
                found = visit(child, inherited)
                if found:
                    return found
        return None

    return visit(value, {}) or {}


def _is_source_derived(
    content: Any,
    block_type: str,
    source_digests: set[str],
    source_strings: list[str],
    source_values: list[Any],
) -> bool:
    if block_type == "outline":
        return True
    # DoxAtlas semantic labeling changes a table block's public type to M/S/D,
    # but its exact source rows remain the same contiguous provider records.
    if isinstance(content, dict) and isinstance(content.get("rows"), list):
        rows = content.get("rows")
        return all(_digest(row) in source_digests for row in rows)
    # Semantic adapters may relabel an exact text fragment as N/E/P/M/S/D.
    if isinstance(content, str):
        return any(content in candidate for candidate in source_strings)
    if isinstance(content, dict):
        return any(
            isinstance(candidate, dict)
            and all(key in candidate and candidate[key] == value for key, value in content.items())
            for candidate in source_values
        )
    if isinstance(content, list):
        return any(
            isinstance(candidate, list)
            and any(
                candidate[index : index + len(content)] == content
                for index in range(max(1, len(candidate) - len(content) + 1))
            )
            for candidate in source_values
        )
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


def _contains_provider_error_envelope(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("Error Message", "Information", "Note"):
            if value.get(key) not in (None, "", [], {}):
                return True
        if str(value.get("status") or "").lower() == "error":
            return True
        if value.get("Error") not in (None, "", [], {}):
            return True
        return any(_contains_provider_error_envelope(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_provider_error_envelope(item) for item in value)
    return False


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
    def exact_read(block: Any) -> bool:
        alias = service.aliases.alias_for(block.block_id)
        if alias is None:
            return False
        loaded = service.read(alias)
        if service.is_catalog_group_alias(alias):
            return any(
                item.block_id == block.block_id
                and _digest(item.content) == _digest(block.content)
                for item in loaded
            )
        return (
            len(loaded) == 1
            and _digest(loaded[0].content) == _digest(block.content)
        )

    reads_ok = all(exact_read(block) for block in blocks)
    source_values = list(_walk(result.output))
    source_digests = {_digest(value) for value in source_values}
    source_strings = [value for value in source_values if isinstance(value, str)]
    source_derived_checks = [
        (
            block,
            _is_source_derived(
                block.content,
                block.block_type,
                source_digests,
                source_strings,
                source_values,
            ),
        )
        for block in blocks
    ]
    source_derived = all(passed for _, passed in source_derived_checks)
    source_derived_failures = [
        {"path": block.locator, "type": block.block_type}
        for block, passed in source_derived_checks
        if not passed
    ][:12]
    raw_record = service.raw_store.get(tool_call_id)
    raw_exact = raw_record is not None and raw_record.result == result
    reconstructed = service.reconstruct_output(tool_call_id)
    reconstruction_exact = reconstructed == result.output
    original_chars = len(_canonical(result.output))
    fresh_view = service.fresh_view(tool_call_id)
    agent_visible_chars = len(_canonical(fresh_view)) if fresh_view is not None else 0
    inflation_ratio = agent_visible_chars / max(1, original_chars)
    allowed_agent_visible_chars = max(
        original_chars + MAX_SMALL_RESULT_OVERHEAD_CHARS,
        int(original_chars * MAX_AGENT_VISIBLE_RATIO),
    )
    agent_visible_size_ok = agent_visible_chars <= allowed_agent_visible_chars
    content_refs = {
        block.ref for block in blocks if block.block_type != "outline"
    }
    catalog_refs = {
        ref for group in index.catalog_groups for ref in group.member_refs
    }
    delivery_coverage_ok = content_refs == (
        set(index.selected_refs) | catalog_refs | set(index.indexed_refs)
    )
    delivery_disjoint_ok = not (
        set(index.selected_refs) & catalog_refs
        or set(index.selected_refs) & set(index.indexed_refs)
        or catalog_refs & set(index.indexed_refs)
    )
    catalog_reads_ok = all(
        (anchor := service.block_store.get_by_ref(group.anchor_ref)) is not None
        and (alias := service.aliases.alias_for(anchor.block_id)) is not None
        and tuple(block.ref for block in service.read(alias)) == group.member_refs
        for group in index.catalog_groups
    )
    outline = fresh_view.get("outline", {}) if isinstance(fresh_view, dict) else {}
    catalog_outline = outline.get("group_catalog", [])
    index_outline = outline.get("block_index", [])
    catalog_outline_ok = len(catalog_outline) == len(index.catalog_groups)
    complete_index_ok = (
        len(index_outline) == len(index.indexed_refs)
        and all("type" not in item for item in index_outline if isinstance(item, dict))
        and "omitted_block_count" not in outline
    )
    profiled_minimum_ok = not (
        result.tool_name in PROFILED_COMPRESSION_TOOLS
        and result.status.value != "failed"
        and original_chars >= 10_000
        and agent_visible_chars < 10_000
    )
    unprofiled_threshold_ok = not (
        result.tool_name not in PROFILED_COMPRESSION_TOOLS
        and original_chars > 50_000
        and index.delivery_mode not in {"indexed_threshold", "paged_oversized"}
    )
    oversized = sum(size > MAX_BLOCK_CHARS for size in block_chars)
    chunking_assessed = bool(result.output) and result.status.value != "failed"
    chunking_ok = (not chunking_assessed) or (
        bool(blocks)
        and oversized == 0
        and hashes_ok
        and reads_ok
        and source_derived
        and raw_exact
        and reconstruction_exact
        and agent_visible_size_ok
        and delivery_coverage_ok
        and delivery_disjoint_ok
        and catalog_reads_ok
        and catalog_outline_ok
        and complete_index_ok
        and profiled_minimum_ok
        and unprofiled_threshold_ok
    )
    status_semantics_ok = not (
        result.status.value == "succeeded"
        and _contains_provider_error_envelope(result.output)
    )
    validation_ok = chunking_ok and status_semantics_ok
    return {
        "status": result.status.value,
        "error": result.error.code if result.error else None,
        "policy": index.policy,
        "adapter": descriptor.observation_adapter,
        "original_chars": original_chars,
        "agent_visible_chars": agent_visible_chars,
        "agent_visible_inflation_ratio": round(inflation_ratio, 4),
        "agent_visible_allowed_chars": allowed_agent_visible_chars,
        "agent_visible_size_ok": agent_visible_size_ok,
        "delivery_mode": index.delivery_mode,
        "selected_block_count": len(index.selected_refs),
        "catalog_group_count": len(index.catalog_groups),
        "catalog_block_count": len(catalog_refs),
        "indexed_block_count": len(index.indexed_refs),
        "catalog_paths": [group.path for group in index.catalog_groups],
        "shape": _shape_profile(result.output),
        "block_count": len(blocks),
        "block_types": sorted({block.block_type for block in blocks}),
        "largest_block_chars": max(block_chars, default=0),
        "oversized_block_count": oversized,
        "raw_exact": raw_exact,
        "reconstruction_exact": reconstruction_exact,
        "block_hashes_ok": hashes_ok,
        "read_observation_exact": reads_ok,
        "catalog_read_exact": catalog_reads_ok,
        "delivery_coverage_ok": delivery_coverage_ok,
        "delivery_disjoint_ok": delivery_disjoint_ok,
        "complete_index_ok": complete_index_ok,
        "profiled_minimum_ok": profiled_minimum_ok,
        "unprofiled_threshold_ok": unprofiled_threshold_ok,
        "source_derived": source_derived,
        "source_derived_failures": source_derived_failures,
        "chunking_assessed": chunking_assessed,
        "chunking_ok": chunking_ok,
        "status_semantics_ok": status_semantics_ok,
        "structure_ok": chunking_ok,
        "validation_ok": validation_ok,
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
            recent = company.output.get("recent_filings", [])
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
            search_payload = search.output.get("search")
            candidates = search_payload.get("results") if isinstance(search_payload, dict) else None
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
    narrative = prior_results.get("doxa_get_narrative_report")
    if narrative is None or not narrative.output:
        narrative = prior_results.get("doxatlas.query")
    narrative_output = narrative.output if narrative else {}
    event_scope = _first_doxatlas_event_scope(narrative_output)
    if tool_name == "doxa_get_analysis":
        query = prior_results.get("doxa_query_analysis")
        query_output = query.output if query else {}
        task_code = _first_named_scalar(query_output, ("task_code",))
        task_id = _first_named_scalar(query_output, ("task_id",))
        if task_code:
            resolved["task_code"] = task_code
        elif task_id:
            resolved["task_id"] = task_id
    if tool_name in {
        "doxa_query_propositions",
        "doxa_get_social_result",
        "doxa_get_social_result_detail",
        "doxa_get_media_result",
        "doxa_get_media_result_detail",
        "doxa_get_event_source",
        "doxatlas.source_lookup",
    }:
        resolved.update(event_scope)
    if tool_name == "doxa_get_ignored_propositions":
        run_id = _first_named_scalar(narrative_output, ("run_id",))
        if run_id:
            resolved["run_id"] = run_id
        else:
            resolved.update(event_scope)
    if tool_name == "doxa_get_social_result_detail":
        social = prior_results.get("doxa_get_social_result")
        social_code = _first_named_scalar(
            social.output if social else {},
            ("social_code", "social_result_code"),
        )
        if social_code:
            resolved["social_codes"] = [social_code]
        else:
            # Keep the real endpoint call contract-valid even when the selected
            # event contains no social rows; the provider may return no-data.
            resolved["social_codes"] = ["S01"]
    if tool_name == "doxa_get_media_result_detail":
        media = prior_results.get("doxa_get_media_result")
        media_code = _first_named_scalar(
            media.output if media else {},
            ("media_code", "media_result_code"),
        )
        if media_code:
            resolved["media_codes"] = [media_code]
    return resolved


def run(group: str, tool: str | None = None) -> int:
    settings = DoxAgentSettings()
    registry = default_real_tool_registry(settings)
    langsmith = _langsmith_client(settings)
    groups = CASES if group == "all" else {group: CASES[group]}
    prior_results: dict[str, ToolResult] = {}
    failed_validations = 0
    for group_name, cases in groups.items():
        for position, (tool_name, ticker, base_input) in enumerate(cases, start=1):
            if tool is not None and tool_name != tool:
                continue
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
            if not summary.get("validation_ok", summary.get("structure_ok")):
                failed_validations += 1
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if failed_validations else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", *CASES], default="all")
    parser.add_argument(
        "--tool",
        choices=sorted({case[0] for cases in CASES.values() for case in cases}),
        help="Only call one independent tool from the selected group.",
    )
    args = parser.parse_args()
    return run(args.group, args.tool)


if __name__ == "__main__":
    raise SystemExit(main())
