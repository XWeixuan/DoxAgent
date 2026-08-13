"""Shared deterministic cleaning, chunking, alias, and delivery kernel."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from doxagent.data_runtime.contracts import (
    DataAvailability,
    DataDelivery,
    DataObservationView,
    DataToolContract,
)
from doxagent.models import ResultStatus
from doxagent.observations.models import ObservationCallRecord, PersistedObservation
from doxagent.observations.pack import ObservationPackWriter
from doxagent.observations.profiles import apply_output_profile
from doxagent.observations.segmenter import content_chars, segment_cleaned_output
from doxagent.observations.store import AttemptObservationStore
from doxagent.tools.schema import ToolResult

_SECRET_KEYS = re.compile(
    r"(?:api[_-]?key|apikey|authorization|bearer|client[_-]?secret|password|token)$",
    re.IGNORECASE,
)
_SECRET_QUERY_KEYS = {"api_key", "apikey", "key", "token", "access_token"}
_SECRET_INLINE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|authorization|client[_-]?secret|password|token)"
    r"(\s*[:=]\s*)([^&,;\s\"'<>]+)"
)
_BEARER_INLINE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


class ObservationKernel:
    def __init__(
        self,
        *,
        store: AttemptObservationStore,
        run_root: str | Path,
        inline_char_limit: int = 16_000,
        inline_block_limit: int = 12,
        selected_char_limit: int = 12_000,
    ) -> None:
        self.store = store
        self._pack_writer = ObservationPackWriter(run_root)
        self._inline_char_limit = inline_char_limit
        self._inline_block_limit = inline_block_limit
        self._selected_char_limit = selected_char_limit

    def ingest_tool_result(
        self,
        *,
        tool_call_id: str,
        contract: DataToolContract,
        input_payload: dict[str, Any],
        result: ToolResult,
        availability: DataAvailability,
        latency_ms: int = 0,
    ) -> DataDelivery:
        self.store.save_raw_result(
            tool_call_id,
            {
                "input": deepcopy(input_payload),
                "tool_result": json.loads(result.model_dump_json()),
            },
        )
        profiled = apply_output_profile(contract, result.output)
        persisted: list[PersistedObservation] = []
        for block in segment_cleaned_output(profiled.content):
            content = _redact(deepcopy(block.content))
            content_hash = _content_hash(content)
            block_id = _stable_block_id(tool_call_id, block.locator, content_hash)
            source_coordinates = _redact(deepcopy(profiled.source_coordinates))
            observation = self.store.save_observation(
                PersistedObservation(
                    run_id=self.store.run_id,
                    attempt_id=self.store.attempt_id,
                    block_id=block_id,
                    tool_call_id=tool_call_id,
                    tool_name=contract.canonical_tool_id,
                    title=block.title,
                    locator=block.locator,
                    block_type=block.block_type,
                    content=content,
                    content_hash=content_hash,
                    source_locator=_source_locator(block.locator, source_coordinates),
                    source_coordinates=source_coordinates,
                    provider=contract.source_name,
                    method_version=f"{contract.canonical_tool_id}/{contract.contract_version}",
                    metadata={
                        "profile_envelope": _redact(profiled.envelope),
                        "removed_paths": list(profiled.removed_paths),
                        "text_encoding": "utf-8",
                        "mojibake_suspected": _contains_mojibake(content),
                    },
                )
            )
            persisted.append(observation)
        total_chars = sum(content_chars(item.content) for item in persisted)
        is_inline = (
            total_chars <= self._inline_char_limit
            and len(persisted) <= self._inline_block_limit
        )
        selected = persisted if is_inline else self._select_large(persisted)
        pack = None
        mode: Literal["inline", "pack"] = "inline"
        if not is_inline:
            mode = "pack"
            pack = self._pack_writer.write(
                attempt_id=self.store.attempt_id,
                tool_call_id=tool_call_id,
                tool_name=contract.canonical_tool_id,
                observations=persisted,
                selected_aliases=[item.alias for item in selected],
            )
        delivery = DataDelivery(
            mode=mode,
            observations=[_view(item) for item in selected],
            pack=pack,
            total_blocks=len(persisted),
            inline_chars=sum(content_chars(item.content) for item in selected),
        )
        raw_payload = json.dumps(result.output, ensure_ascii=False, default=str)
        inline_bytes = len(delivery.model_dump_json().encode())
        pack_bytes = 0
        if pack is not None:
            pack_root = self._pack_writer.run_root / pack.root
            pack_bytes = sum(path.stat().st_size for path in pack_root.rglob("*") if path.is_file())
        self.store.save_call(
            ObservationCallRecord(
                run_id=self.store.run_id,
                attempt_id=self.store.attempt_id,
                tool_call_id=tool_call_id,
                tool_name=contract.canonical_tool_id,
                provider=contract.source_name,
                input_payload=_redact(deepcopy(input_payload)),
                execution_status=result.status.value,
                availability=availability.value,
                block_aliases=[item.alias for item in persisted],
                selected_aliases=[item.alias for item in selected],
                delivery_mode=mode,
                original_chars=len(raw_payload),
                raw_payload_bytes=len(raw_payload.encode()),
                inline_bytes=inline_bytes,
                pack_bytes=pack_bytes,
                latency_ms=max(0, latency_ms),
            )
        )
        return delivery

    def ingest_external_source(
        self,
        *,
        source_id: str,
        url: str,
        source: str | None,
        note: str | None,
        title: str | None,
        cleaned_content: str,
    ) -> list[PersistedObservation]:
        result = ToolResult(
            tool_name="source_capture",
            status=ResultStatus.SUCCEEDED,
            output={
                "url": _redact_url(url),
                "title": title,
                "source": source,
                "note": note,
                "text": cleaned_content,
                "source_coordinates": {"url": _redact_url(url), "source_id": source_id},
            },
        )
        contract = DataToolContract(
            canonical_tool_id="source_capture",
            mcp_name="capture_source",
            source_name=source or "Public web source",
            business_categories=["source_capture"],
            description="Captured public web source.",
            business_purpose="Make a bounded public source citable.",
            use_when=["a public source must be captured for citation"],
            avoid_when=["a governed Data MCP semantic tool is available"],
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_profile="text",
            observation_adapter="text",
        )
        self.ingest_tool_result(
            tool_call_id=source_id,
            contract=contract,
            input_payload={"url": _redact_url(url)},
            result=result,
            availability=DataAvailability.AVAILABLE,
        )
        return self.store.list_for_call(source_id)

    def read_observation(self, alias: str) -> PersistedObservation | None:
        return self.store.read_alias(alias)

    def _select_large(self, observations: list[PersistedObservation]) -> list[PersistedObservation]:
        preferred = sorted(
            observations,
            key=lambda item: (-_selection_score(item), int(item.alias[1:])),
        )
        selected: list[PersistedObservation] = []
        consumed = 0
        for item in preferred:
            size = content_chars(item.content)
            if consumed + size > self._selected_char_limit:
                continue
            selected.append(item)
            consumed += size
            if consumed >= self._selected_char_limit:
                break
        return selected


def _view(item: PersistedObservation) -> DataObservationView:
    return DataObservationView(
        alias=item.alias,
        block_id=item.block_id,
        title=item.title,
        content=deepcopy(item.content),
        block_type=item.block_type,
        source_locator=item.source_locator,
        content_hash=item.content_hash,
    )


def _selection_score(item: PersistedObservation) -> int:
    locator = item.locator.lower()
    score = {
        "text": 30,
        "time_series": 28,
        "table": 26,
        "json": 10,
    }.get(item.block_type, 0)
    if any(
        token in locator
        for token in (
            "/sections",
            "/results",
            "/records",
            "/updates",
            "/key_facts",
            "/series",
            "/rows",
            "/content",
            "/text",
        )
    ):
        score += 12
    if any(
        token in locator
        for token in (
            "/metadata",
            "/requested",
            "/concepts",
            "/accession",
            "/primary_document",
            "/applied_filters",
        )
    ):
        score -= 20
    if content_chars(item.content) < 120:
        score -= 6
    return score


def _stable_block_id(tool_call_id: str, locator: str, content_hash: str) -> str:
    seed = f"{tool_call_id}|{locator}|{content_hash}".encode()
    return f"oblk_{hashlib.sha256(seed).hexdigest()[:20]}"


def _content_hash(content: Any) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_locator(locator: str, coordinates: Any) -> str:
    if isinstance(coordinates, dict):
        for key in (
            "source_locator",
            "url",
            "endpoint",
            "accession",
            "document_number",
            "source_id",
        ):
            value = coordinates.get(key)
            if value not in (None, ""):
                return str(value)[:2_000]
    return locator


def _contains_mojibake(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_mojibake(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_mojibake(item) for item in value)
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in ("\ufffd", "鈥?", "鈥檚", "Ã¢", "â€™"))


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key and _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        cleaned = _redact_url(value) if value.startswith(("http://", "https://")) else value
        cleaned = _BEARER_INLINE.sub("Bearer [REDACTED]", cleaned)
        return _SECRET_INLINE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
            cleaned,
        )
    return value


def _redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        query = [
            (key, "[REDACTED]" if key.lower() in _SECRET_QUERY_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    except ValueError:
        return "[INVALID_URL]"
