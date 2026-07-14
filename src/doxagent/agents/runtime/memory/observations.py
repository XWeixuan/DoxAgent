"""High-fidelity storage, indexing, and reading for ReAct tool observations."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from doxagent.agents.runtime.memory.aliases import ObservationAliasRegistry
from doxagent.tools import ToolResult

JsonDict = dict[str, Any]
ObservationPolicyName = Literal["inline", "indexed", "recomputable"]
ObservationAdapterName = Literal[
    "auto",
    "json",
    "search_results",
    "text",
    "table",
    "time_series",
    "doxatlas",
]

_MAX_NATURAL_BLOCK_CHARS = 1_200
_MAX_SINGLE_RESULT_FRESH_TOKENS = 128_000
_UNPROFILED_RAW_CHAR_INDEX_THRESHOLD = 50_000
_PROFILED_MIN_VISIBLE_CONTENT_CHARS = 10_000
_CATALOG_GROUP_MAX_CONTENT_CHARS = 40_000
_TABLE_ROWS_PER_BLOCK = 50
_PARAGRAPHS_PER_BLOCK = 8
_DATE_KEYS = (
    "datetime",
    "date",
    "timestamp",
    "time",
    "period",
    "TimePeriod",
)
_TIME_SERIES_KEYS = ("ohlcv", "rows", "observations", "series", "data")
_HIERARCHY_KEYS = (
    "narratives",
    "events",
    "propositions",
    "results",
    "media_results",
    "social_results",
    "sources",
)
_PROFILED_COMPRESSION_TOOLS = frozenset(
    {
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
)


@dataclass(frozen=True)
class RawToolResultRecord:
    tool_call_id: str
    step: int
    input_payload: JsonDict
    result: ToolResult


class RawToolResultStore:
    """Append-only source of truth for complete ToolResult objects."""

    def __init__(self) -> None:
        self._records: dict[str, RawToolResultRecord] = {}

    def add(
        self,
        *,
        tool_call_id: str,
        step: int,
        input_payload: JsonDict,
        result: ToolResult,
    ) -> None:
        if tool_call_id in self._records:
            raise ValueError(f"duplicate tool_call_id: {tool_call_id}")
        self._records[tool_call_id] = RawToolResultRecord(
            tool_call_id=tool_call_id,
            step=step,
            input_payload=deepcopy(input_payload),
            result=result.model_copy(deep=True),
        )

    def get(self, tool_call_id: str) -> RawToolResultRecord | None:
        record = self._records.get(tool_call_id)
        if record is None:
            return None
        return RawToolResultRecord(
            tool_call_id=record.tool_call_id,
            step=record.step,
            input_payload=deepcopy(record.input_payload),
            result=record.result.model_copy(deep=True),
        )

    def audit(self) -> dict[str, JsonDict]:
        return {
            tool_call_id: {
                "tool_call_id": record.tool_call_id,
                "step": record.step,
                "input": _json_safe(record.input_payload),
                "tool_result": _json_safe(record.result.model_dump(mode="python")),
            }
            for tool_call_id, record in self._records.items()
        }

    def records(self) -> tuple[RawToolResultRecord, ...]:
        return tuple(
            RawToolResultRecord(
                tool_call_id=record.tool_call_id,
                step=record.step,
                input_payload=deepcopy(record.input_payload),
                result=record.result.model_copy(deep=True),
            )
            for record in self._records.values()
        )

    def __len__(self) -> int:
        return len(self._records)


@dataclass(frozen=True)
class ObservationBlock:
    block_id: str
    tool_call_id: str
    parent_block_id: str | None
    locator: str
    content: Any
    context_envelope: JsonDict
    content_hash: str
    block_type: str
    metadata: JsonDict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"obs_{self.tool_call_id}::{self.locator}"

    def agent_view(self, alias: str) -> JsonDict:
        content = deepcopy(self.content)
        if (
            self.block_type in {"table", "time_series"}
            and isinstance(content, dict)
            and isinstance(content.get("rows"), list)
        ):
            content = {
                key: value
                for key, value in content.items()
                if key != "columns" and value not in (None, "", [], {})
            }
        semantic = self.context_envelope.get("doxatlas_semantic")
        view: JsonDict = {"alias": alias}
        if semantic not in (None, ""):
            view["kind"] = semantic
        else:
            view["path"] = _agent_path(self.locator)
            if self.block_type not in {"json", "hierarchy"}:
                view["type"] = self.block_type
        view["content"] = content
        return view

    def audit_view(self) -> JsonDict:
        return {
            "block_id": self.block_id,
            "ref": self.ref,
            "tool_call_id": self.tool_call_id,
            "parent_block_id": self.parent_block_id,
            "locator": self.locator,
            "content_hash": self.content_hash,
            "block_type": self.block_type,
            "context_envelope": deepcopy(self.context_envelope),
            "metadata": deepcopy(self.metadata),
        }


class ObservationBlockStore:
    """Append-only block index with stable agent-visible refs."""

    def __init__(self) -> None:
        self._by_id: dict[str, ObservationBlock] = {}
        self._by_ref: dict[str, str] = {}
        self._by_call: dict[str, list[str]] = {}

    def add_many(self, blocks: list[ObservationBlock]) -> None:
        for block in blocks:
            if block.block_id in self._by_id or block.ref in self._by_ref:
                raise ValueError(f"duplicate observation block: {block.ref}")
            self._by_id[block.block_id] = block
            self._by_ref[block.ref] = block.block_id
            self._by_call.setdefault(block.tool_call_id, []).append(block.block_id)

    def get_by_ref(self, ref: str) -> ObservationBlock | None:
        block_id = self._by_ref.get(ref)
        return self._by_id.get(block_id) if block_id is not None else None

    def get(self, block_id: str) -> ObservationBlock | None:
        return self._by_id.get(block_id)

    def blocks_for_call(self, tool_call_id: str) -> list[ObservationBlock]:
        return [self._by_id[item] for item in self._by_call.get(tool_call_id, [])]

    def parent(self, block: ObservationBlock) -> ObservationBlock | None:
        if block.parent_block_id is None:
            return None
        return self._by_id.get(block.parent_block_id)

    def children(self, block: ObservationBlock) -> list[ObservationBlock]:
        return [
            item for item in self._by_id.values() if item.parent_block_id == block.block_id
        ]

    def audit(self) -> list[JsonDict]:
        return [block.audit_view() for block in self._by_id.values()]

    def records(self) -> tuple[ObservationBlock, ...]:
        return tuple(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)


@dataclass(frozen=True)
class ObservationCatalogGroup:
    path: str
    anchor_ref: str
    member_refs: tuple[str, ...]
    content_chars: int


@dataclass(frozen=True)
class ObservationCallIndex:
    tool_call_id: str
    tool_name: str
    policy: ObservationPolicyName
    block_refs: tuple[str, ...]
    selected_refs: tuple[str, ...]
    original_chars: int
    original_token_estimate: int
    delivery_mode: Literal[
        "full",
        "hybrid_profiled",
        "indexed_threshold",
        "paged_oversized",
    ] = "full"
    catalog_groups: tuple[ObservationCatalogGroup, ...] = ()
    indexed_refs: tuple[str, ...] = ()

    def outline(
        self,
        store: ObservationBlockStore,
        aliases: ObservationAliasRegistry,
    ) -> JsonDict:
        all_blocks = [
            block
            for ref in self.block_refs
            if (block := store.get_by_ref(ref)) is not None
        ]
        base: JsonDict = {
            "tool_name": self.tool_name,
            "delivery_mode": self.delivery_mode,
            "original_chars": self.original_chars,
            "block_count": len(all_blocks),
        }
        if self.delivery_mode == "full":
            return base
        groups: list[JsonDict] = []
        for group in self.catalog_groups:
            anchor = store.get_by_ref(group.anchor_ref)
            if anchor is None:
                continue
            groups.append(
                {
                    "path": group.path,
                    "alias": aliases.alias_for(anchor.block_id),
                    "block_count": len(group.member_refs),
                    "chars": group.content_chars,
                }
            )
        indexes: list[JsonDict] = []
        for ref in self.indexed_refs:
            block = store.get_by_ref(ref)
            if block is None:
                continue
            indexes.append(
                {
                    "alias": aliases.alias_for(block.block_id),
                    "path": block.locator,
                }
            )
        if groups:
            base["group_catalog"] = groups
        if indexes:
            base["block_index"] = indexes
        return base


class ObservationPolicyRegistry:
    """Resolve only the descriptor policy; block size never changes this policy."""

    def resolve(
        self,
        declared_policy: str | None,
        output: JsonDict,
    ) -> ObservationPolicyName:
        if declared_policy in {"inline", "indexed", "recomputable"}:
            return cast(ObservationPolicyName, declared_policy)
        return "inline"


class ObservationParser:
    """Deterministically parse ToolResult output into natural semantic blocks."""

    def parse(
        self,
        *,
        tool_call_id: str,
        result: ToolResult,
        policy: ObservationPolicyName,
        adapter: str = "auto",
    ) -> list[ObservationBlock]:
        output = result.output
        source_markers: tuple[str, ...] = ()
        envelope = _base_envelope(result.tool_name, output, source_markers)
        if adapter == "doxatlas":
            doxatlas = _doxatlas_blocks(
                tool_call_id,
                output,
                envelope,
                source_markers,
            )
            if doxatlas:
                return doxatlas
        blocks = _structured_blocks(
            tool_call_id,
            output,
            envelope,
            source_markers,
            adapter=adapter,
        )
        if blocks:
            return blocks
        return [
            _make_block(
                tool_call_id=tool_call_id,
                locator="/",
                content=output,
                envelope=envelope,
                block_type="json",
                source_markers=source_markers,
            )
        ]


@dataclass(frozen=True)
class _CompressionPlan:
    selected_refs: tuple[str, ...]
    catalog_groups: tuple[ObservationCatalogGroup, ...] = ()
    indexed_refs: tuple[str, ...] = ()
    delivery_mode: Literal[
        "full",
        "hybrid_profiled",
        "indexed_threshold",
        "paged_oversized",
    ] = "full"


class ObservationService:
    """Coordinates raw results, block parsing, selection, and exact reads."""

    def __init__(
        self,
        *,
        raw_store: RawToolResultStore | None = None,
        block_store: ObservationBlockStore | None = None,
        parser: ObservationParser | None = None,
        policy_registry: ObservationPolicyRegistry | None = None,
        aliases: ObservationAliasRegistry | None = None,
    ) -> None:
        self.raw_store = raw_store or RawToolResultStore()
        self.block_store = block_store or ObservationBlockStore()
        self.parser = parser or ObservationParser()
        self.policy_registry = policy_registry or ObservationPolicyRegistry()
        self.aliases = aliases or ObservationAliasRegistry()
        self._call_indexes: dict[str, ObservationCallIndex] = {}
        self._catalog_members_by_anchor_id: dict[str, tuple[str, ...]] = {}

    def ingest(
        self,
        *,
        tool_call_id: str,
        step: int,
        input_payload: JsonDict,
        result: ToolResult,
        declared_policy: str | None,
        adapter: str = "auto",
    ) -> ObservationCallIndex:
        self.raw_store.add(
            tool_call_id=tool_call_id,
            step=step,
            input_payload=input_payload,
            result=result,
        )
        policy = self.policy_registry.resolve(declared_policy, result.output)
        blocks = self.parser.parse(
            tool_call_id=tool_call_id,
            result=result,
            policy=policy,
            adapter=adapter,
        )
        self.block_store.add_many(blocks)
        self.aliases.register_many(
            tuple(
                block.block_id
                for block in blocks
                if block.block_type != "outline"
            )
            + tuple(
                block.block_id
                for block in blocks
                if block.block_type == "outline"
            )
        )
        refs = tuple(block.ref for block in blocks)
        original_chars = len(_canonical_json(result.output))
        original_token_estimate = _estimated_payload_tokens(result.output)
        plan = _plan_fresh_delivery(result.tool_name, blocks, original_chars)
        selected = plan.selected_refs
        delivery_mode = plan.delivery_mode
        if delivery_mode != "full":
            policy = "indexed"
        index = ObservationCallIndex(
            tool_call_id=tool_call_id,
            tool_name=result.tool_name,
            policy=policy,
            block_refs=refs,
            selected_refs=selected,
            original_chars=original_chars,
            original_token_estimate=original_token_estimate,
            delivery_mode=delivery_mode,
            catalog_groups=plan.catalog_groups,
            indexed_refs=plan.indexed_refs,
        )
        if delivery_mode == "full":
            visible_payload = {
                "outline": index.outline(self.block_store, self.aliases),
                "loaded_blocks": [
                    block.agent_view(self.aliases.alias_for(block.block_id) or "")
                    for ref in selected
                    if (block := self.block_store.get_by_ref(ref)) is not None
                ],
            }
            if _estimated_payload_tokens(visible_payload) > _MAX_SINGLE_RESULT_FRESH_TOKENS:
                fallback = _prefix_index_plan(
                    blocks,
                    content_char_limit=_UNPROFILED_RAW_CHAR_INDEX_THRESHOLD,
                    delivery_mode="paged_oversized",
                )
                index = replace(
                    index,
                    policy="indexed",
                    selected_refs=fallback.selected_refs,
                    delivery_mode=fallback.delivery_mode,
                    catalog_groups=fallback.catalog_groups,
                    indexed_refs=fallback.indexed_refs,
                )
        self._register_catalog_groups(index.catalog_groups)
        self._call_indexes[tool_call_id] = index
        return index

    def _register_catalog_groups(
        self,
        groups: tuple[ObservationCatalogGroup, ...],
    ) -> None:
        for group in groups:
            anchor = self.block_store.get_by_ref(group.anchor_ref)
            if anchor is None:
                continue
            self._catalog_members_by_anchor_id[anchor.block_id] = group.member_refs

    def call_index(self, tool_call_id: str) -> ObservationCallIndex | None:
        return self._call_indexes.get(tool_call_id)

    def fresh_view(self, tool_call_id: str, *, micro: bool = False) -> JsonDict | None:
        index = self._call_indexes.get(tool_call_id)
        if index is None:
            return None
        selected = list(index.selected_refs)
        loaded = [
            block.agent_view(self.aliases.alias_for(block.block_id) or "")
            for ref in selected
            if (block := self.block_store.get_by_ref(ref)) is not None
        ]
        view: JsonDict = {
            "outline": index.outline(self.block_store, self.aliases),
            "loaded_blocks": loaded,
        }
        if index.delivery_mode != "full":
            view["read_instruction"] = (
                "Use read_observation(alias) to load one complete catalog group "
                "or exact indexed block."
            )
        return view

    def reconstruct_output(self, tool_call_id: str) -> JsonDict | None:
        """Rebuild ToolResult.output from the stored non-outline Observation Blocks."""

        if tool_call_id not in self._call_indexes:
            return None
        return _reconstruct_output(self.block_store.blocks_for_call(tool_call_id))

    def read(
        self,
        alias: str,
        *,
        include_parent: bool = False,
        include_children: bool = False,
    ) -> list[ObservationBlock]:
        block_id = self.aliases.resolve(alias)
        block = self.block_store.get(block_id) if block_id is not None else None
        if block is None:
            return []
        catalog_refs = self._catalog_members_by_anchor_id.get(block.block_id)
        if catalog_refs is not None:
            return [
                member
                for ref in catalog_refs
                if (member := self.block_store.get_by_ref(ref)) is not None
            ]
        blocks = [block]
        if include_parent:
            parent = self.block_store.parent(block)
            if parent is not None:
                blocks.insert(0, parent)
        if include_children:
            blocks.extend(self.block_store.children(block))
        return blocks

    def is_catalog_group_alias(self, alias: str) -> bool:
        block_id = self.aliases.resolve(alias)
        return block_id in self._catalog_members_by_anchor_id if block_id else False

    def audit(self) -> JsonDict:
        return {
            "raw_tool_results": self.raw_store.audit(),
            "block_index": self.block_store.audit(),
            "call_indexes": {
                call_id: {
                    "tool_call_id": index.tool_call_id,
                    "tool_name": index.tool_name,
                    "policy": index.policy,
                    "block_refs": list(index.block_refs),
                    "selected_refs": list(index.selected_refs),
                    "original_chars": index.original_chars,
                    "original_token_estimate": index.original_token_estimate,
                    "delivery_mode": index.delivery_mode,
                    "catalog_groups": [
                        {
                            "path": group.path,
                            "anchor_ref": group.anchor_ref,
                            "member_refs": list(group.member_refs),
                            "content_chars": group.content_chars,
                        }
                        for group in index.catalog_groups
                    ],
                    "indexed_refs": list(index.indexed_refs),
                }
                for call_id, index in self._call_indexes.items()
            },
        }


def _plan_fresh_delivery(
    tool_name: str,
    blocks: list[ObservationBlock],
    original_chars: int,
) -> _CompressionPlan:
    content_blocks = [block for block in blocks if block.block_type != "outline"]
    full_refs = tuple(block.ref for block in content_blocks)
    if not content_blocks:
        return _CompressionPlan(selected_refs=full_refs)
    if tool_name in _PROFILED_COMPRESSION_TOOLS:
        # Never shrink an already-small or empty provider payload. Exact source
        # content takes precedence over an artificial minimum display size.
        if original_chars < _PROFILED_MIN_VISIBLE_CONTENT_CHARS:
            return _CompressionPlan(selected_refs=full_refs)
        selected = _profiled_high_value_refs(tool_name, content_blocks)
        selected = _ensure_minimum_selected_content(content_blocks, selected)
        low_blocks = [block for block in content_blocks if block.ref not in selected]
        if not low_blocks:
            return _CompressionPlan(selected_refs=full_refs)
        groups, indexed_refs = _profiled_catalog(tool_name, low_blocks)
        if not groups and not indexed_refs:
            # Invalid or stale strategy: preserve navigability with a complete,
            # type-free block index instead of silently dropping content.
            indexed_refs = tuple(block.ref for block in low_blocks)
        return _CompressionPlan(
            selected_refs=tuple(block.ref for block in content_blocks if block.ref in selected),
            catalog_groups=groups,
            indexed_refs=indexed_refs,
            delivery_mode="hybrid_profiled",
        )
    if original_chars > _UNPROFILED_RAW_CHAR_INDEX_THRESHOLD:
        return _prefix_index_plan(
            blocks,
            content_char_limit=_UNPROFILED_RAW_CHAR_INDEX_THRESHOLD,
            delivery_mode="indexed_threshold",
        )
    return _CompressionPlan(selected_refs=full_refs)


def _prefix_index_plan(
    blocks: list[ObservationBlock],
    *,
    content_char_limit: int,
    delivery_mode: Literal["indexed_threshold", "paged_oversized"],
) -> _CompressionPlan:
    content_blocks = [block for block in blocks if block.block_type != "outline"]
    selected: list[str] = []
    indexed: list[str] = []
    consumed = 0
    overflowed = False
    for block in content_blocks:
        size = _block_content_chars(block)
        if not overflowed and (not selected or consumed + size <= content_char_limit):
            selected.append(block.ref)
            consumed += size
            continue
        overflowed = True
        indexed.append(block.ref)
    if not indexed:
        return _CompressionPlan(selected_refs=tuple(selected))
    return _CompressionPlan(
        selected_refs=tuple(selected),
        indexed_refs=tuple(indexed),
        delivery_mode=delivery_mode,
    )


def _profiled_high_value_refs(
    tool_name: str,
    blocks: list[ObservationBlock],
) -> set[str]:
    if tool_name == "alpha.financial_statements":
        return _alpha_financial_high_value_refs(blocks)
    if tool_name == "alpha.earnings_events":
        return _alpha_earnings_high_value_refs(blocks)
    if tool_name == "fred.series_observations":
        return _fred_high_value_refs(blocks)
    if tool_name == "sec.company_facts_and_filings":
        return {
            block.ref
            for block in blocks
            if not _is_repeated_metadata(block)
            and block.locator.startswith(
                (
                    "/cik",
                    "/company",
                    "/recent_filings",
                    "/key_facts",
                    "/fact_directory",
                    "/facts_status",
                )
            )
        }
    if tool_name == "sec.filing_sections":
        return _sec_filing_high_value_refs(blocks)
    if tool_name == "bea.nipa_data":
        return _bea_high_value_refs(blocks)
    if tool_name == "polymarket.market_probability":
        return {
            block.ref
            for block in blocks
            if not _is_repeated_metadata(block)
            and (
                not block.locator.startswith("/data/items/")
                or (_path_record_index(block.locator, "/data/items/") or 0) < 2
            )
        }
    if tool_name == "doxa_get_analysis":
        return _doxatlas_analysis_high_value_refs(blocks)
    if tool_name in {"doxatlas.query", "doxa_get_narrative_report"}:
        return {
            block.ref
            for block in blocks
            if not _is_repeated_metadata(block)
            and not _is_doxatlas_source_or_detail(block)
            and (
                "/data/narrative_flow_summary/" not in block.locator
                or (
                    _path_record_index(
                        block.locator,
                        "/data/narrative_flow_summary/",
                    )
                    or 0
                )
                >= 6
            )
        }
    return set()


def _alpha_financial_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    selected: set[str] = set()
    for block in blocks:
        locator = block.locator
        if _is_repeated_metadata(block):
            continue
        if not locator.startswith("/statements/"):
            selected.add(block.ref)
            continue
        if "/quarterlyReports/" in locator:
            index = _path_record_index(locator, "/quarterlyReports/")
            if index is not None and index < 6:
                selected.add(block.ref)
            continue
        if "/annualReports/" in locator:
            index = _path_record_index(locator, "/annualReports/")
            if index is not None and index < 3:
                selected.add(block.ref)
            continue
        selected.add(block.ref)
    return selected


def _alpha_earnings_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    selected: set[str] = set()
    for block in blocks:
        if _is_repeated_metadata(block):
            continue
        locator = block.locator
        if "/estimates/" not in locator:
            selected.add(block.ref)
            continue
        index = _path_record_index(locator, "/estimates/")
        if index is not None and index < 16:
            selected.add(block.ref)
    return selected


def _fred_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    maximum_row_by_series: dict[str, int] = {}
    for block in blocks:
        series_id = _path_segment_after(block.locator, "/series/")
        row_range = _row_index_range(block.locator, "/observations/")
        if series_id and row_range:
            maximum_row_by_series[series_id] = max(
                maximum_row_by_series.get(series_id, -1), row_range[1]
            )
    selected: set[str] = set()
    for block in blocks:
        if _is_repeated_metadata(block):
            continue
        series_id = _path_segment_after(block.locator, "/series/")
        row_range = _row_index_range(block.locator, "/observations/")
        if not series_id or not row_range:
            selected.add(block.ref)
            continue
        if row_range[1] >= maximum_row_by_series.get(series_id, row_range[1]) - 119:
            selected.add(block.ref)
    return selected


def _sec_filing_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    selected: set[str] = set()
    per_section_chars: dict[str, int] = {}
    for block in blocks:
        if _is_repeated_metadata(block):
            continue
        if not block.locator.startswith("/sections/"):
            selected.add(block.ref)
            continue
        section = _path_segment_after(block.locator, "/sections/") or "unknown"
        consumed = per_section_chars.get(section, 0)
        if consumed < 8_000:
            selected.add(block.ref)
            per_section_chars[section] = consumed + _block_content_chars(block)
    return selected


def _bea_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    selected = {
        block.ref
        for block in blocks
        if block.block_type not in {"table", "time_series"}
        and not _is_repeated_metadata(block)
    }
    rows = [
        block for block in blocks if block.block_type in {"table", "time_series"}
    ]
    consumed = 0
    for block in sorted(rows, key=_block_latest_period, reverse=True):
        if consumed >= 16_000:
            break
        selected.add(block.ref)
        consumed += _block_content_chars(block)
    return selected


def _doxatlas_analysis_high_value_refs(blocks: list[ObservationBlock]) -> set[str]:
    selected: set[str] = set()
    for block in blocks:
        if _is_repeated_metadata(block) or _is_doxatlas_source_or_detail(
            block,
            include_semantic_channels=False,
        ):
            continue
        locator = block.locator
        media_index = _path_record_index(locator, "/media/topic_atlas/topics/")
        social_index = _path_record_index(locator, "/social/topic_atlas/topics/")
        if media_index is not None and media_index >= 2:
            continue
        if social_index is not None and social_index >= 3:
            continue
        selected.add(block.ref)
    return selected


def _ensure_minimum_selected_content(
    blocks: list[ObservationBlock],
    selected: set[str],
) -> set[str]:
    consumed = sum(
        _block_content_chars(block) for block in blocks if block.ref in selected
    )
    if consumed >= _PROFILED_MIN_VISIBLE_CONTENT_CHARS:
        return selected
    expanded = set(selected)
    order_by_ref = {block.ref: index for index, block in enumerate(blocks)}
    candidates = sorted(
        (block for block in blocks if block.ref not in expanded),
        key=lambda block: (
            2
            if _is_repeated_metadata(block)
            else 1
            if _is_doxatlas_source_or_detail(
                block,
                include_semantic_channels=False,
            )
            else 0,
            order_by_ref[block.ref],
        ),
    )
    for block in candidates:
        expanded.add(block.ref)
        consumed += _block_content_chars(block)
        if consumed >= _PROFILED_MIN_VISIBLE_CONTENT_CHARS:
            break
    return expanded


def _profiled_catalog(
    tool_name: str,
    low_blocks: list[ObservationBlock],
) -> tuple[tuple[ObservationCatalogGroup, ...], tuple[str, ...]]:
    grouped: dict[str, list[ObservationBlock]] = {}
    indexed: list[str] = []
    for block in low_blocks:
        path = _profiled_catalog_path(tool_name, block)
        if path is None:
            indexed.append(block.ref)
            continue
        grouped.setdefault(path, []).append(block)
    groups: list[ObservationCatalogGroup] = []
    for path, members in grouped.items():
        pages: list[list[ObservationBlock]] = []
        current: list[ObservationBlock] = []
        current_chars = 0
        for block in members:
            size = _block_content_chars(block)
            if current and current_chars + size > _CATALOG_GROUP_MAX_CONTENT_CHARS:
                pages.append(current)
                current = []
                current_chars = 0
            current.append(block)
            current_chars += size
        if current:
            pages.append(current)
        for page_index, page in enumerate(pages, start=1):
            page_path = path
            if len(pages) > 1:
                page_path = f"{path}/part_{page_index:02d}"
            groups.append(
                ObservationCatalogGroup(
                    path=page_path,
                    anchor_ref=page[0].ref,
                    member_refs=tuple(block.ref for block in page),
                    content_chars=sum(_block_content_chars(block) for block in page),
                )
            )
    return tuple(groups), tuple(indexed)


def _profiled_catalog_path(tool_name: str, block: ObservationBlock) -> str | None:
    locator = block.locator
    if _is_repeated_metadata(block):
        return "/metadata/provider_and_source_coordinates"
    if tool_name == "alpha.financial_statements":
        for source, target in (
            ("INCOME_STATEMENT", "income_statement"),
            ("BALANCE_SHEET", "balance_sheet"),
            ("CASH_FLOW", "cash_flow"),
        ):
            if f"/statements/{source}/" not in locator:
                continue
            history = "quarterly_history" if "/quarterlyReports/" in locator else "annual_history"
            return f"/financials/{target}/{history}"
        return "/financials/other"
    if tool_name == "alpha.earnings_events":
        if "/estimates/" in locator:
            return "/earnings/estimates/later_periods"
        return "/earnings/other"
    if tool_name == "fred.series_observations":
        series_id = _path_segment_after(locator, "/series/")
        if series_id:
            return f"/macro/fred/{_safe_catalog_segment(series_id)}/earlier_history"
        return "/macro/fred/other"
    if tool_name == "sec.company_facts_and_filings":
        if locator.startswith("/fact_pages/"):
            return "/sec/company_facts/fact_pages"
        if locator.startswith("/recent_filings/"):
            return "/sec/filings/older_filings"
        return "/sec/company_facts/other"
    if tool_name == "sec.filing_sections":
        section = _path_segment_after(locator, "/sections/")
        if section:
            return f"/sec/filing_sections/section_{_safe_catalog_segment(section)}/remainder"
        return "/sec/filing_sections/other"
    if tool_name == "bea.nipa_data":
        return "/macro/bea/nipa/earlier_periods"
    if tool_name == "polymarket.market_probability":
        if locator.startswith("/data/items/"):
            return "/markets/polymarket/lower_ranked_results"
        return "/markets/polymarket/metadata"
    if tool_name == "doxa_get_analysis":
        channel = (
            "media"
            if "/data/media/" in locator
            else "social"
            if "/data/social/" in locator
            else "other"
        )
        if _is_doxatlas_source_or_detail(
            block,
            include_semantic_channels=False,
        ):
            return f"/doxatlas/analysis/{channel}/source_details"
        return f"/doxatlas/analysis/{channel}/lower_ranked_topics"
    if tool_name in {"doxatlas.query", "doxa_get_narrative_report"}:
        if "/data/narrative_flow_summary/" in locator:
            return "/doxatlas/narratives/earlier_flow"
        semantic = str(block.context_envelope.get("doxatlas_semantic") or "other")
        label = {"D": "source_details", "M": "media_details", "S": "social_details"}.get(
            semantic, "other_details"
        )
        return f"/doxatlas/narratives/{label}"
    return None


def _is_repeated_metadata(block: ObservationBlock) -> bool:
    locator = block.locator.lower()
    return locator == "/provider" or locator.startswith("/source_coordinates") or any(
        marker in locator
        for marker in ("/provider_metadata", "/request_metadata", "/response_metadata")
    )


def _is_doxatlas_source_or_detail(
    block: ObservationBlock,
    *,
    include_semantic_channels: bool = True,
) -> bool:
    locator = block.locator.lower()
    semantic = str(block.context_envelope.get("doxatlas_semantic") or "")
    return (include_semantic_channels and semantic in {"D", "M", "S"}) or any(
        marker in locator
        for marker in (
            "/source_capsules",
            "/sources",
            "/documents",
            "/details",
            "/content",
            "/content_preview",
        )
    )


def _path_record_index(locator: str, marker: str) -> int | None:
    tail = locator.split(marker, 1)[1] if marker in locator else ""
    match = re.match(r"(?:rows/|\$items/)?(\d+)", tail)
    return int(match.group(1)) if match else None


def _row_index_range(locator: str, marker: str) -> tuple[int, int] | None:
    tail = locator.split(marker, 1)[1] if marker in locator else ""
    match = re.match(r"(?:rows/|\$items/)(\d+)-(\d+)", tail)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _path_segment_after(locator: str, marker: str) -> str | None:
    if marker not in locator:
        return None
    value = locator.split(marker, 1)[1].split("/", 1)[0]
    return _unescape_pointer(value) if value else None


def _safe_catalog_segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", value.lower()).strip("_")
    return normalized or "unknown"


def _block_content_chars(block: ObservationBlock) -> int:
    return len(_canonical_json(block.content))


def _block_latest_period(block: ObservationBlock) -> str:
    content = block.content
    if not isinstance(content, dict) or not isinstance(content.get("rows"), list):
        return ""
    periods = [
        str(row.get(key) or "")
        for row in content["rows"]
        if isinstance(row, dict)
        for key in _DATE_KEYS
        if row.get(key) not in (None, "")
    ]
    return max(periods, default="")


def _structured_blocks(
    tool_call_id: str,
    output: JsonDict,
    envelope: JsonDict,
    source_markers: tuple[str, ...],
    *,
    adapter: str,
) -> list[ObservationBlock]:
    blocks: list[ObservationBlock] = []
    hierarchy = adapter == "doxatlas" or any(key in output for key in _HIERARCHY_KEYS)
    force_structure = adapter in {"search_results", "table", "time_series"}
    prefer_item_refs = adapter == "search_results"
    for key, value in output.items():
        locator = f"/{_escape_pointer(key)}"
        if key == "fact_pages" and isinstance(value, dict):
            page_parent = _make_block(
                tool_call_id=tool_call_id,
                locator=locator,
                content={
                    "field_count": len(value),
                    "keys": _bounded_outline_keys(value),
                    "omitted_key_count": max(
                        0, len(value) - len(_bounded_outline_keys(value))
                    ),
                    "original_chars": len(_canonical_json(value)),
                },
                envelope={**envelope, "path": locator},
                block_type="outline",
                source_markers=source_markers,
            )
            blocks.append(page_parent)
            for page_key, page in value.items():
                page_locator = f"{locator}/{_escape_pointer(str(page_key))}"
                blocks.extend(
                    _value_blocks(
                        tool_call_id,
                        page_locator,
                        page,
                        {**envelope, "path": page_locator},
                        source_markers,
                        parent_block_id=page_parent.block_id,
                        hierarchy=hierarchy,
                        force_structure=False,
                        prefer_item_refs=False,
                    )
                )
            continue
        blocks.extend(
            _value_blocks(
                tool_call_id,
                locator,
                value,
                {**envelope, "path": locator},
                source_markers,
                hierarchy=hierarchy,
                force_structure=force_structure,
                prefer_item_refs=prefer_item_refs,
            )
        )
    return blocks


def _doxatlas_blocks(
    tool_call_id: str,
    output: JsonDict,
    envelope: JsonDict,
    source_markers: tuple[str, ...],
) -> list[ObservationBlock]:
    blocks: list[ObservationBlock] = []
    data = output.get("data")
    if isinstance(data, (dict, list)):
        blocks.extend(
            _value_blocks(
                tool_call_id,
                "/data",
                data,
                {**envelope, "path": "/data", "doxatlas_unwrapped": True},
                source_markers,
                hierarchy=True,
                force_structure=True,
                prefer_item_refs=True,
                force_item_structure=True,
            )
        )
        wrapper = {key: value for key, value in output.items() if key != "data"}
        if wrapper:
            blocks.extend(
                _structured_blocks(
                    tool_call_id,
                    wrapper,
                    envelope,
                    source_markers,
                    adapter="json",
                )
            )
    else:
        blocks.extend(
            _structured_blocks(
                tool_call_id,
                output,
                envelope,
                source_markers,
                adapter="json",
            )
        )
    blocks = [_with_doxatlas_semantics(block) for block in blocks]
    return blocks


def _with_doxatlas_semantics(block: ObservationBlock) -> ObservationBlock:
    locator = block.locator.lower()
    semantic: tuple[str, str] | None = None
    for keys, code, name in (
        (("narratives", "narrative"), "N", "narrative"),
        (("events", "event"), "E", "event"),
        (("propositions", "ignored_propositions", "proposition"), "P", "proposition"),
        (("media_results", "media"), "M", "media"),
        (("social_results", "social"), "S", "social"),
        (("sources", "source", "documents"), "D", "source"),
    ):
        if any(f"/{key}" in locator for key in keys):
            semantic = (code, name)
    if semantic is None:
        return block
    code, name = semantic
    return replace(
        block,
        block_type=(block.block_type if block.block_type == "outline" else f"doxatlas_{name}"),
        context_envelope={**block.context_envelope, "doxatlas_semantic": code},
    )


def _value_blocks(
    tool_call_id: str,
    locator: str,
    value: Any,
    envelope: JsonDict,
    source_markers: tuple[str, ...],
    *,
    parent_block_id: str | None = None,
    hierarchy: bool = False,
    force_structure: bool = False,
    prefer_item_refs: bool = False,
    force_item_structure: bool = False,
) -> list[ObservationBlock]:
    rendered_chars = len(_canonical_json(value))
    if isinstance(value, str) and rendered_chars > _MAX_NATURAL_BLOCK_CHARS:
        return _text_blocks(
            tool_call_id,
            locator,
            value,
            envelope,
            source_markers,
            parent_block_id=parent_block_id,
        )
    if isinstance(value, dict) and (
        rendered_chars > _MAX_NATURAL_BLOCK_CHARS
        or (force_structure and any(key in value for key in _HIERARCHY_KEYS))
    ):
        parent = _make_block(
            tool_call_id=tool_call_id,
            locator=locator,
            content={
                "field_count": len(value),
                "keys": _bounded_outline_keys(value),
                "omitted_key_count": max(0, len(value) - len(_bounded_outline_keys(value))),
                "original_chars": rendered_chars,
            },
            envelope={**envelope, "path": locator},
            block_type="outline",
            source_markers=source_markers,
            parent_block_id=parent_block_id,
        )
        blocks = [parent]
        pending: JsonDict = {}
        pending_start = 0

        def flush_pending(end: int) -> None:
            nonlocal pending, pending_start
            if not pending:
                return
            compact_locator = (
                f"{locator.rstrip('/')}/$fields/{pending_start}-{end}"
            )
            blocks.append(
                _make_block(
                    tool_call_id=tool_call_id,
                    locator=compact_locator,
                    content=pending,
                    envelope={**envelope, "path": locator},
                    block_type="hierarchy" if hierarchy else "json",
                    source_markers=source_markers,
                    parent_block_id=parent.block_id,
                )
            )
            pending = {}

        for position, (key, item) in enumerate(value.items()):
            field = {str(key): item}
            field_chars = len(_canonical_json(field))
            force_semantic_child = (
                force_item_structure
                and key in _HIERARCHY_KEYS
                and isinstance(item, (dict, list))
            )
            if field_chars > _MAX_NATURAL_BLOCK_CHARS or force_semantic_child:
                flush_pending(position - 1)
                child_locator = f"{locator.rstrip('/')}/{_escape_pointer(str(key))}"
                blocks.extend(
                    _value_blocks(
                        tool_call_id,
                        child_locator,
                        item,
                        {**envelope, "path": child_locator},
                        source_markers,
                        parent_block_id=parent.block_id,
                        hierarchy=hierarchy,
                        force_structure=force_item_structure,
                        prefer_item_refs=prefer_item_refs,
                        force_item_structure=force_item_structure,
                    )
                )
                pending_start = position + 1
                continue
            candidate = {**pending, **field}
            if pending and len(_canonical_json(candidate)) > _MAX_NATURAL_BLOCK_CHARS:
                flush_pending(position - 1)
                pending_start = position
                pending = field
            else:
                if not pending:
                    pending_start = position
                pending = candidate
        flush_pending(len(value) - 1)
        return blocks
    if isinstance(value, list):
        if not value:
            return [
                _make_block(
                    tool_call_id=tool_call_id,
                    locator=locator,
                    content=[],
                    envelope=envelope,
                    block_type="json",
                    source_markers=source_markers,
                    parent_block_id=parent_block_id,
                )
            ]
        if prefer_item_refs:
            parent = _make_block(
                tool_call_id=tool_call_id,
                locator=locator,
                content={"item_count": len(value), "original_chars": rendered_chars},
                envelope={**envelope, "path": locator},
                block_type="outline",
                source_markers=source_markers,
                parent_block_id=parent_block_id,
            )
            blocks = [parent]
            for index, item in enumerate(value):
                child_locator = f"{locator}/{index}"
                blocks.extend(
                    _value_blocks(
                        tool_call_id,
                        child_locator,
                        item,
                        {**envelope, "path": child_locator},
                        source_markers,
                        parent_block_id=parent.block_id,
                        hierarchy=hierarchy,
                        force_structure=force_item_structure,
                        prefer_item_refs=False,
                        force_item_structure=force_item_structure,
                    )
                )
            return blocks
        rows = [item for item in value if isinstance(item, dict)]
        if not force_item_structure and rows and len(rows) == len(value) and all(
            _single_table_row_chars(row) <= _MAX_NATURAL_BLOCK_CHARS for row in rows
        ):
            return _table_blocks(
                tool_call_id,
                locator,
                value,
                envelope,
                source_markers,
                parent_block_id=parent_block_id,
            )
        if not rows and all(
            len(_canonical_json(item)) <= _MAX_NATURAL_BLOCK_CHARS for item in value
        ):
            return _scalar_list_blocks(
                tool_call_id,
                locator,
                value,
                envelope,
                source_markers,
                parent_block_id=parent_block_id,
            )
        parent = _make_block(
            tool_call_id=tool_call_id,
            locator=locator,
            content={"item_count": len(value), "original_chars": rendered_chars},
            envelope={**envelope, "path": locator},
            block_type="outline",
            source_markers=source_markers,
            parent_block_id=parent_block_id,
        )
        blocks = [parent]
        for index, item in enumerate(value):
            child_locator = f"{locator}/{index}"
            blocks.extend(
                _value_blocks(
                    tool_call_id,
                    child_locator,
                    item,
                    {**envelope, "path": child_locator},
                    source_markers,
                    parent_block_id=parent.block_id,
                    hierarchy=hierarchy,
                    force_structure=force_item_structure,
                    prefer_item_refs=prefer_item_refs,
                    force_item_structure=force_item_structure,
                )
            )
        return blocks
    block_type = "hierarchy" if hierarchy and isinstance(value, dict) else "json"
    return [
        _make_block(
            tool_call_id=tool_call_id,
            locator=locator,
            content=value,
            envelope=envelope,
            block_type=block_type,
            source_markers=source_markers,
            parent_block_id=parent_block_id,
        )
    ]


def _scalar_list_blocks(
    tool_call_id: str,
    locator: str,
    values: list[Any],
    envelope: JsonDict,
    source_markers: tuple[str, ...],
    *,
    parent_block_id: str | None,
) -> list[ObservationBlock]:
    blocks: list[ObservationBlock] = []
    current: list[Any] = []
    start = 0
    for index, item in enumerate(values):
        candidate = [*current, item]
        if current and len(_canonical_json(candidate)) > _MAX_NATURAL_BLOCK_CHARS:
            end = index - 1
            blocks.append(
                _make_block(
                    tool_call_id=tool_call_id,
                    locator=f"{locator.rstrip('/')}/$items/{start}-{end}",
                    content=current,
                    envelope={**envelope, "path": locator},
                    block_type="json",
                    source_markers=source_markers,
                    parent_block_id=parent_block_id,
                )
            )
            current = [item]
            start = index
        else:
            if not current:
                start = index
            current = candidate
    if current:
        blocks.append(
            _make_block(
                tool_call_id=tool_call_id,
                locator=f"{locator.rstrip('/')}/$items/{start}-{len(values) - 1}",
                content=current,
                envelope={**envelope, "path": locator},
                block_type="json",
                source_markers=source_markers,
                parent_block_id=parent_block_id,
            )
        )
    return blocks


def _text_blocks(
    tool_call_id: str,
    locator: str,
    text: str,
    envelope: JsonDict,
    source_markers: tuple[str, ...],
    *,
    parent_block_id: str | None,
) -> list[ObservationBlock]:
    normalized = _exact_text_segments(text)
    blocks: list[ObservationBlock] = []
    start = 0
    while start < len(normalized):
        group: list[str] = []
        end = start
        while end < len(normalized) and len(group) < _PARAGRAPHS_PER_BLOCK:
            candidate = [*group, normalized[end]]
            if group and len(_canonical_json("".join(candidate))) > _MAX_NATURAL_BLOCK_CHARS:
                break
            group = candidate
            end += 1
        end = start + len(group) - 1
        child_locator = f"{locator}/paragraphs/{start}-{end}"
        blocks.append(
            _make_block(
                tool_call_id=tool_call_id,
                locator=child_locator,
                content="".join(group),
                envelope={**envelope, "paragraphs": f"{start}-{end}"},
                block_type="text",
                source_markers=source_markers,
                parent_block_id=parent_block_id,
            )
        )
        start += len(group)
    return blocks


def _exact_text_segments(text: str) -> list[str]:
    """Split on paragraph boundaries while keeping every segment byte-for-byte exact."""

    paragraph_spans: list[str] = []
    cursor = 0
    for match in re.finditer(r"\n[ \t]*\n", text):
        paragraph_spans.append(text[cursor : match.end()])
        cursor = match.end()
    if cursor < len(text):
        paragraph_spans.append(text[cursor:])
    if not paragraph_spans:
        paragraph_spans = [text]
    segment_chars = _MAX_NATURAL_BLOCK_CHARS - 256
    return [
        paragraph[index : index + segment_chars]
        for paragraph in paragraph_spans
        for index in range(0, len(paragraph), segment_chars)
    ]


def _time_series_blocks(
    tool_call_id: str,
    output: JsonDict,
    envelope: JsonDict,
    source_markers: tuple[str, ...],
) -> list[ObservationBlock]:
    for key in _TIME_SERIES_KEYS:
        value = output.get(key)
        if not isinstance(value, list) or not _looks_like_table(value):
            continue
        rows = [deepcopy(item) for item in value if isinstance(item, dict)]
        if any(_single_table_row_chars(row) > _MAX_NATURAL_BLOCK_CHARS for row in rows):
            continue
        blocks: list[ObservationBlock] = []
        for start, group in _bounded_row_groups(
            rows,
            overhead={
                "series_key": key,
                "unit": output.get("unit"),
                "currency": output.get("currency"),
            },
        ):
            start_label = _row_label(group[0], start)
            end_label = _row_label(group[-1], start + len(group) - 1)
            locator = f"rows/{start_label}..{end_label}"
            columns = sorted({str(column) for row in group for column in row})
            blocks.append(
                _make_block(
                    tool_call_id=tool_call_id,
                    locator=locator,
                    content={
                        "series_key": key,
                        "columns": columns,
                        "rows": group,
                        "unit": output.get("unit"),
                        "currency": output.get("currency"),
                    },
                    envelope={
                        **envelope,
                        "range": f"{start_label}..{end_label}",
                        "columns": columns,
                        "unit": output.get("unit"),
                        "currency": output.get("currency"),
                    },
                    block_type="time_series",
                    source_markers=source_markers,
                )
            )
        return blocks
    return []


def _table_blocks(
    tool_call_id: str,
    locator: str,
    value: list[Any],
    envelope: JsonDict,
    source_markers: tuple[str, ...],
    *,
    parent_block_id: str | None = None,
) -> list[ObservationBlock]:
    rows = [deepcopy(item) for item in value if isinstance(item, dict)]
    blocks: list[ObservationBlock] = []
    for start, group in _bounded_row_groups(rows):
        end = start + len(group) - 1
        columns = sorted({str(column) for row in group for column in row})
        child_locator = f"{locator}/rows/{start}-{end}"
        start_label = _row_label(group[0], start)
        end_label = _row_label(group[-1], end)
        is_time_series = any(any(key in row for key in _DATE_KEYS) for row in group)
        blocks.append(
            _make_block(
                tool_call_id=tool_call_id,
                locator=child_locator,
                content={"columns": columns, "rows": group},
                envelope={
                    **envelope,
                    "path": child_locator,
                    "columns": columns,
                    **({"range": f"{start_label}..{end_label}"} if is_time_series else {}),
                },
                block_type="time_series" if is_time_series else "table",
                source_markers=source_markers,
                parent_block_id=parent_block_id,
            )
        )
    return blocks


def _bounded_row_groups(
    rows: list[JsonDict],
    *,
    overhead: JsonDict | None = None,
) -> list[tuple[int, list[JsonDict]]]:
    groups: list[tuple[int, list[JsonDict]]] = []
    current: list[JsonDict] = []
    start = 0
    for index, row in enumerate(rows):
        candidate = [*current, row]
        columns = sorted({str(column) for item in candidate for column in item})
        candidate_chars = len(
            _canonical_json({**(overhead or {}), "columns": columns, "rows": candidate})
        )
        if current and (
            len(current) >= _TABLE_ROWS_PER_BLOCK
            or candidate_chars > _MAX_NATURAL_BLOCK_CHARS
        ):
            groups.append((start, current))
            current = [row]
            start = index
        else:
            if not current:
                start = index
            current = candidate
    if current:
        groups.append((start, current))
    return groups


def _single_table_row_chars(row: JsonDict) -> int:
    return len(
        _canonical_json(
            {"columns": sorted(str(column) for column in row), "rows": [row]}
        )
    )


def _reconstruct_output(blocks: list[ObservationBlock]) -> JsonDict:
    root: Any = {}
    regular: list[ObservationBlock] = []
    dict_groups: list[tuple[str, int, JsonDict]] = []
    list_groups: list[tuple[str, int, list[Any]]] = []
    row_groups: list[tuple[str, int, list[Any]]] = []
    text_groups: list[tuple[str, int, str]] = []
    for block in blocks:
        if block.block_type == "outline":
            continue
        dict_match = re.match(r"^(.*)/\$fields/(\d+)-(\d+)$", block.locator)
        if dict_match and isinstance(block.content, dict):
            dict_groups.append(
                (
                    dict_match.group(1) or "/",
                    int(dict_match.group(2)),
                    deepcopy(block.content),
                )
            )
            continue
        list_match = re.match(r"^(.*)/\$items/(\d+)-(\d+)$", block.locator)
        if list_match and isinstance(block.content, list):
            list_groups.append(
                (
                    list_match.group(1) or "/",
                    int(list_match.group(2)),
                    deepcopy(block.content),
                )
            )
            continue
        row_match = re.match(r"^(.*)/rows/(\d+)-(\d+)$", block.locator)
        if (
            row_match
            and isinstance(block.content, dict)
            and isinstance(block.content.get("rows"), list)
        ):
            row_groups.append(
                (
                    row_match.group(1) or "/",
                    int(row_match.group(2)),
                    deepcopy(block.content["rows"]),
                )
            )
            continue
        paragraph_match = re.match(r"^(.*)/paragraphs/(\d+)-(\d+)$", block.locator)
        if paragraph_match and isinstance(block.content, str):
            text_groups.append(
                (paragraph_match.group(1) or "/", int(paragraph_match.group(2)), block.content)
            )
            continue
        regular.append(block)
    for block in sorted(regular, key=lambda item: (_locator_depth(item.locator), item.locator)):
        root = _set_json_pointer(root, block.locator, deepcopy(block.content))
    grouped_dicts: dict[str, list[tuple[int, JsonDict]]] = {}
    for path, start, values in dict_groups:
        grouped_dicts.setdefault(path, []).append((start, values))
    for path, groups in sorted(
        grouped_dicts.items(), key=lambda item: _locator_depth(item[0])
    ):
        merged: JsonDict = {}
        for _, values in sorted(groups):
            merged.update(values)
        existing = _get_json_pointer(root, path)
        if isinstance(existing, dict):
            merged = {**existing, **merged}
        root = _set_json_pointer(root, path, merged)
    grouped_lists: dict[str, list[tuple[int, list[Any]]]] = {}
    for path, start, values in list_groups:
        grouped_lists.setdefault(path, []).append((start, values))
    for path, groups in sorted(
        grouped_lists.items(), key=lambda item: _locator_depth(item[0])
    ):
        values: list[Any] = []
        for start, items in sorted(groups):
            if len(values) < start:
                values.extend([None] * (start - len(values)))
            values.extend(items)
        root = _set_json_pointer(root, path, values)
    grouped_rows: dict[str, list[tuple[int, list[Any]]]] = {}
    for path, start, rows in row_groups:
        grouped_rows.setdefault(path, []).append((start, rows))
    for path, groups in grouped_rows.items():
        rows: list[Any] = []
        for start, values in sorted(groups):
            if len(rows) < start:
                rows.extend([None] * (start - len(rows)))
            for offset, value in enumerate(values):
                index = start + offset
                if index < len(rows):
                    rows[index] = value
                else:
                    rows.append(value)
        root = _set_json_pointer(root, path, rows)
    grouped_text: dict[str, list[tuple[int, str]]] = {}
    for path, start, text in text_groups:
        grouped_text.setdefault(path, []).append((start, text))
    for path, groups in grouped_text.items():
        root = _set_json_pointer(root, path, "".join(text for _, text in sorted(groups)))
    return cast(JsonDict, root)


def _get_json_pointer(root: Any, pointer: str) -> Any:
    if pointer in {"", "/"}:
        return root
    current = root
    for part in (_unescape_pointer(item) for item in pointer.strip("/").split("/")):
        if isinstance(current, list) and part.isdigit():
            position = int(part)
            if position >= len(current):
                return None
            current = current[position]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _set_json_pointer(root: Any, pointer: str, value: Any) -> Any:
    if pointer in {"", "/"}:
        return value
    parts = [_unescape_pointer(part) for part in pointer.strip("/").split("/")]
    if not isinstance(root, (dict, list)):
        root = [] if parts[0].isdigit() else {}
    current = root
    for index, part in enumerate(parts[:-1]):
        next_is_index = parts[index + 1].isdigit()
        if isinstance(current, list):
            position = int(part)
            while len(current) <= position:
                current.append(None)
            if not isinstance(current[position], (dict, list)):
                current[position] = [] if next_is_index else {}
            current = current[position]
        else:
            if not isinstance(current.get(part), (dict, list)):
                current[part] = [] if next_is_index else {}
            current = current[part]
    final = parts[-1]
    if isinstance(current, list):
        position = int(final)
        while len(current) <= position:
            current.append(None)
        current[position] = value
    else:
        current[final] = value
    return root


def _unescape_pointer(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _agent_path(locator: str) -> str:
    compact = re.sub(r"/\$fields/\d+-\d+$", "", locator)
    compact = re.sub(r"/\$items/\d+-\d+$", "", compact)
    return compact or "/"


def _selected_block_refs(
    blocks: list[ObservationBlock],
    *,
    limit: int,
    tool_name: str = "",
) -> tuple[str, ...]:
    if tool_name == "sec.company_facts_and_filings":
        preferred: list[ObservationBlock] = []
        for locator_prefix in (
            "/fact_directory",
            "/key_facts",
            "/fact_pages/page_0001",
            "/recent_filings",
        ):
            candidates = [
                block for block in blocks if block.locator.startswith(locator_prefix)
            ]
            candidates.sort(
                key=lambda block: (
                    1 if block.block_type == "outline" else 0,
                    _locator_depth(block.locator),
                    block.locator,
                )
            )
            if candidates:
                preferred.append(candidates[0])
        deduplicated = list(dict.fromkeys(block.ref for block in preferred))
        if len(deduplicated) >= limit:
            return tuple(deduplicated[:limit])
    root_outlines = [
        block
        for block in blocks
        if block.block_type == "outline" and block.parent_block_id is None
    ]
    substantive = [
        block
        for block in blocks
        if block.block_type in {"table", "time_series", "text"}
    ]
    remaining = [
        block
        for block in blocks
        if block not in root_outlines and block not in substantive
    ]
    ordered = [*root_outlines, *substantive, *remaining]
    return tuple(block.ref for block in ordered[:limit])


def _bounded_outline_keys(value: JsonDict) -> list[str]:
    keys: list[str] = []
    for key in value:
        candidate = [*keys, str(key)]
        if len(candidate) > 32 or len(_canonical_json(candidate)) > 700:
            break
        keys = candidate
    return keys


def _locator_depth(locator: str) -> int:
    return len([part for part in locator.split("/") if part])


def _locator_prefixes(blocks: list[ObservationBlock]) -> list[str]:
    prefixes: list[str] = []
    for block in blocks:
        parts = [part for part in block.locator.split("/") if part]
        prefix = f"/{parts[0]}" if parts else "/"
        if prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes[:16]


def _make_block(
    *,
    tool_call_id: str,
    locator: str,
    content: Any,
    envelope: JsonDict,
    block_type: str,
    source_markers: tuple[str, ...],
    parent_block_id: str | None = None,
) -> ObservationBlock:
    canonical = _canonical_json(content)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    block_seed = f"{tool_call_id}|{locator}|{content_hash}"
    block_id = f"oblk_{hashlib.sha256(block_seed.encode('utf-8')).hexdigest()[:20]}"
    return ObservationBlock(
        block_id=block_id,
        tool_call_id=tool_call_id,
        parent_block_id=parent_block_id,
        locator=locator,
        content=deepcopy(content),
        context_envelope=deepcopy(envelope),
        content_hash=content_hash,
        block_type=block_type,
    )


def _base_envelope(
    tool_name: str,
    output: JsonDict,
    source_markers: tuple[str, ...],
) -> JsonDict:
    envelope: JsonDict = {"tool": tool_name}
    for key in (
        "provider",
        "symbol",
        "ticker",
        "period",
        "interval",
        "run_id",
        "task_id",
        "narrative_code",
        "event_code",
        "document_id",
        "title",
        "unit",
        "currency",
        "source_coordinates",
    ):
        value = output.get(key)
        if value not in (None, "", [], {}):
            envelope[key] = deepcopy(value)
    return envelope


def _looks_like_table(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _row_label(row: JsonDict, fallback: int) -> str:
    for key in _DATE_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            return _locator_token(str(value))
    return str(fallback)


def _locator_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", value.strip()) or "item"


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _estimated_payload_tokens(value: Any) -> int:
    rendered = _canonical_json(value)
    ascii_chars = sum(1 for char in rendered if ord(char) < 128)
    non_ascii_chars = len(rendered) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


__all__ = [
    "ObservationAdapterName",
    "ObservationBlock",
    "ObservationBlockStore",
    "ObservationCallIndex",
    "ObservationParser",
    "ObservationPolicyName",
    "ObservationPolicyRegistry",
    "ObservationService",
    "RawToolResultRecord",
    "RawToolResultStore",
]
