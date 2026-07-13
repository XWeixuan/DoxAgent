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
_TABLE_ROWS_PER_BLOCK = 50
_PARAGRAPHS_PER_BLOCK = 8
_OUTLINE_BLOCK_LIMIT = 24
_DATE_KEYS = ("datetime", "date", "timestamp", "time", "period")
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
        return {
            "alias": alias,
            "block_type": self.block_type,
            "context_envelope": deepcopy(self.context_envelope),
            "content": deepcopy(self.content),
        }

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
class ObservationCallIndex:
    tool_call_id: str
    tool_name: str
    policy: ObservationPolicyName
    block_refs: tuple[str, ...]
    selected_refs: tuple[str, ...]
    original_chars: int
    original_token_estimate: int
    delivery_mode: Literal["full", "indexed_sec", "paged_oversized"] = "full"

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
        selected = set(self.selected_refs)
        ordered = sorted(
            all_blocks,
            key=lambda block: (
                0 if block.ref in selected else 1,
                0 if block.parent_block_id is None else 1,
                _locator_depth(block.locator),
                block.locator,
            ),
        )
        listed = ordered[:_OUTLINE_BLOCK_LIMIT]
        items: list[JsonDict] = []
        for block in listed:
            items.append(
                {
                    "alias": aliases.alias_for(block.block_id),
                    "block_type": block.block_type,
                    "context_envelope": deepcopy(block.context_envelope),
                }
            )
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "policy": self.policy,
            "original_chars": self.original_chars,
            "original_token_estimate": self.original_token_estimate,
            "delivery_mode": self.delivery_mode,
            "block_count": len(all_blocks),
            "listed_block_count": len(items),
            "omitted_block_count": max(0, len(all_blocks) - len(items)),
            "locator_prefixes": _locator_prefixes(all_blocks),
            "blocks": items,
        }


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
        if result.tool_name == "sec.company_facts_and_filings":
            policy = "indexed"
            delivery_mode = "indexed_sec"
            selected = _selected_block_refs(blocks, limit=3, tool_name=result.tool_name)
        elif original_token_estimate > _MAX_SINGLE_RESULT_FRESH_TOKENS:
            policy = "indexed"
            delivery_mode = "paged_oversized"
            selected = _selected_block_refs(blocks, limit=3, tool_name=result.tool_name)
        else:
            delivery_mode = "full"
            selected = tuple(
                block.ref for block in blocks if block.block_type != "outline"
            )
        index = ObservationCallIndex(
            tool_call_id=tool_call_id,
            tool_name=result.tool_name,
            policy=policy,
            block_refs=refs,
            selected_refs=selected,
            original_chars=original_chars,
            original_token_estimate=original_token_estimate,
            delivery_mode=delivery_mode,
        )
        self._call_indexes[tool_call_id] = index
        return index

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
        return {
            "outline": index.outline(self.block_store, self.aliases),
            "loaded_blocks": loaded,
            "read_instruction": (
                "All chunks are loaded for this result. Use read_observation "
                "to reload an exact block."
                if index.delivery_mode == "full"
                else (
                    "This result exceeds the 128k input window and is explicitly paged. "
                    "Use read_observation with listed O# aliases to load remaining exact blocks."
                    if index.delivery_mode == "paged_oversized"
                    else (
                        "SEC company facts uses its dedicated index/page strategy; "
                        "use read_observation for exact pages."
                    )
                )
            ),
        }

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
        blocks = [block]
        if include_parent:
            parent = self.block_store.parent(block)
            if parent is not None:
                blocks.insert(0, parent)
        if include_children:
            blocks.extend(self.block_store.children(block))
        return blocks

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
                }
                for call_id, index in self._call_indexes.items()
            },
        }


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
        force_structure or rendered_chars > _MAX_NATURAL_BLOCK_CHARS
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
        for key, item in value.items():
            child_locator = f"{locator}/{_escape_pointer(str(key))}"
            blocks.extend(
                _value_blocks(
                    tool_call_id,
                    child_locator,
                    item,
                    {**envelope, "path": child_locator},
                    source_markers,
                    parent_block_id=parent.block_id,
                    hierarchy=hierarchy,
                    force_structure=force_structure,
                    prefer_item_refs=prefer_item_refs,
                    force_item_structure=force_item_structure,
                )
            )
        return blocks
    if isinstance(value, list):
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
                    force_structure=force_structure,
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
    row_groups: list[tuple[str, int, list[Any]]] = []
    text_groups: list[tuple[str, int, str]] = []
    for block in blocks:
        if block.block_type == "outline":
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
