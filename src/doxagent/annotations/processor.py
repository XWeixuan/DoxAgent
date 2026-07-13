"""Generic, recursive and strictly non-blocking text annotation processing."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from doxagent.annotations.citations import (
    ObservationAliasResolver,
    normalize_citation_mentions,
    strip_resolved_citations,
)
from doxagent.annotations.models import (
    AnnotationBatch,
    AnnotationMetrics,
    CitationAnnotation,
    ProcessedText,
    TimeAnnotation,
)
from doxagent.annotations.store import AnnotationStore

_TIME_TAG_RE = re.compile(
    r"【(?P<kind>occurred_at|published_at):(?P<value>[^】]+)】"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
_HALF_RE = re.compile(r"^\d{4}-H[12]$")

_NON_ANNOTATABLE_TEXT_KEYS = frozenset(
    {
        "agent_name",
        "alias",
        "author_agent",
        "decision",
        "document_type",
        "execution_mode",
        "field_path",
        "model",
        "operation",
        "output_schema",
        "provider",
        "required_output_schema",
        "reviewer_agent",
        "runtime",
        "status",
        "target_agent",
        "task_type",
        "ticker",
        "tool_name",
        "triggered_by",
        "validation_status",
        "workflow_node",
    }
)


class TextAnnotationProcessor:
    """Parse annotations after result validation without affecting business flow."""

    def __init__(self, store: AnnotationStore | None = None, *, enabled: bool = True) -> None:
        self.store = store
        self.enabled = enabled

    def process(
        self,
        *,
        run_id: str,
        task_id: str,
        result_id: str,
        payload: dict[str, Any],
        aliases: ObservationAliasResolver,
    ) -> AnnotationBatch:
        untouched = deepcopy(payload)
        if not self.enabled:
            return AnnotationBatch(
                run_id=run_id,
                task_id=task_id,
                result_id=result_id,
                plain_payload=untouched,
            )
        processed: list[ProcessedText] = []
        citations: list[CitationAnnotation] = []
        times: list[TimeAnnotation] = []
        warnings: list[str] = []
        metrics = AnnotationMetrics()
        try:
            plain_payload = self._walk(
                untouched,
                path="",
                run_id=run_id,
                task_id=task_id,
                result_id=result_id,
                aliases=aliases,
                processed=processed,
                citations=citations,
                times=times,
                warnings=warnings,
                metrics=metrics,
            )
        except Exception as exc:  # annotation failures never escape into workflow
            warnings.append(f"annotation_processor_failed:{type(exc).__name__}:{exc}")
            plain_payload = untouched
        self._persist_best_effort(citations, times, warnings)
        return AnnotationBatch(
            run_id=run_id,
            task_id=task_id,
            result_id=result_id,
            plain_payload=plain_payload,
            processed_texts=processed,
            citations=citations,
            times=times,
            warnings=warnings,
            metrics=metrics,
        )

    def _walk(
        self,
        value: Any,
        *,
        path: str,
        suppress_annotations: bool = False,
        **context: Any,
    ) -> Any:
        if isinstance(value, str):
            item = self._process_text(
                value,
                payload_path=path or "/",
                suppress_annotations=suppress_annotations,
                **context,
            )
            context["processed"].append(item)
            return item.plain_text
        if isinstance(value, list):
            return [
                self._walk(
                    item,
                    path=f"{path}/{index}",
                    suppress_annotations=suppress_annotations,
                    **context,
                )
                for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            has_structured_mirror = path == "" and isinstance(value.get("structured"), dict)
            return {
                key: self._walk(
                    item,
                    path=f"{path}/{_escape(str(key))}",
                    suppress_annotations=(
                        suppress_annotations
                        or (has_structured_mirror and key == "text")
                        or _is_non_annotatable_text_key(str(key))
                    ),
                    **context,
                )
                for key, item in value.items()
            }
        return value

    def _process_text(
        self,
        raw_text: str,
        *,
        payload_path: str,
        run_id: str,
        task_id: str,
        result_id: str,
        aliases: ObservationAliasResolver,
        citations: list[CitationAnnotation],
        times: list[TimeAnnotation],
        warnings: list[str],
        metrics: AnnotationMetrics,
        suppress_annotations: bool = False,
        **_: Any,
    ) -> ProcessedText:
        if suppress_annotations:
            return ProcessedText(
                payload_path=payload_path,
                raw_tagged_text=raw_text,
                plain_text=raw_text,
            )
        plain_text = strip_resolved_citations(raw_text, aliases=aliases)
        plain_text = _TIME_TAG_RE.sub("", plain_text)
        text_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
        created_at = datetime.now(UTC)
        local_citations: list[CitationAnnotation] = []
        local_times: list[TimeAnnotation] = []
        local_warnings: list[str] = []
        if len(raw_text.strip()) >= 20:
            metrics.annotatable_text_count += 1
        temporal_candidate = bool(
            re.search(r"\b(?:19|20)\d{2}(?:[-年]|\s*Q[1-4]|\s*H[12])", raw_text)
        )
        if temporal_candidate:
            metrics.temporal_candidate_count += 1
        explicit_candidates = normalize_citation_mentions(raw_text)
        for candidate in explicit_candidates:
            if aliases.resolve(candidate.alias) is None:
                metrics.citation_tag_count += 1
                metrics.invalid_alias_count += 1
                local_warnings.append(
                    f"invalid_citation_alias:{payload_path}:{candidate.alias}"
                )
        for mention in normalize_citation_mentions(raw_text, aliases=aliases):
            metrics.citation_tag_count += 1
            block_id = aliases.resolve(mention.alias)
            if block_id is None:
                continue
            record = CitationAnnotation(
                annotation_id=f"citation_{uuid4().hex}",
                run_id=run_id,
                task_id=task_id,
                result_id=result_id,
                payload_path=payload_path,
                text_hash=text_hash,
                span_start=mention.start,
                span_end=mention.end,
                observation_block_id=block_id,
                created_at=created_at,
            )
            local_citations.append(record)
            metrics.resolved_citation_count += 1
        for match in _TIME_TAG_RE.finditer(raw_text):
            kind = match.group("kind")
            value = match.group("value").strip()
            metrics.time_tag_count += 1
            if not _valid_time(value, published=(kind == "published_at")):
                metrics.invalid_time_count += 1
                local_warnings.append(f"invalid_{kind}:{payload_path}:{value}")
                continue
            record = TimeAnnotation(
                annotation_id=f"time_{uuid4().hex}",
                run_id=run_id,
                task_id=task_id,
                result_id=result_id,
                payload_path=payload_path,
                text_hash=text_hash,
                span_start=match.start(),
                span_end=match.end(),
                occurred_at=value if kind == "occurred_at" else None,
                published_at=value if kind == "published_at" else None,
                created_at=created_at,
            )
            local_times.append(record)
            metrics.valid_time_count += 1
        if local_citations and len(raw_text.strip()) >= 20:
            metrics.cited_text_count += 1
        if local_times and temporal_candidate:
            metrics.time_annotated_text_count += 1
        citations.extend(local_citations)
        times.extend(local_times)
        warnings.extend(local_warnings)
        return ProcessedText(
            payload_path=payload_path,
            raw_tagged_text=raw_text,
            plain_text=plain_text,
            citation_annotations=local_citations,
            time_annotations=local_times,
            warnings=local_warnings,
        )

    def _persist_best_effort(
        self,
        citations: list[CitationAnnotation],
        times: list[TimeAnnotation],
        warnings: list[str],
    ) -> None:
        if self.store is None:
            return
        try:
            self.store.save_citations(citations)
            self.store.save_times(times)
        except Exception as exc:
            warnings.append(f"annotation_persistence_failed:{type(exc).__name__}:{exc}")


def render_time_tags(plain_text: str, annotations: list[TimeAnnotation]) -> str:
    """Re-render valid time tags for downstream model context.

    Citations are intentionally not rendered because task-local aliases have no
    meaning outside the producing AgentTask.
    """

    suffixes: list[str] = []
    for item in annotations:
        if item.occurred_at:
            suffixes.append(f"【occurred_at:{item.occurred_at}】")
        if item.published_at:
            suffixes.append(f"【published_at:{item.published_at}】")
    if not suffixes:
        return plain_text
    return f"{plain_text}{''.join(dict.fromkeys(suffixes))}"


def _is_non_annotatable_text_key(key: str) -> bool:
    normalized = key.strip().lower()
    return (
        normalized in _NON_ANNOTATABLE_TEXT_KEYS
        or normalized.endswith("_id")
        or normalized.endswith("_ids")
        or normalized.endswith("_ref")
        or normalized.endswith("_refs")
    )


def _valid_time(value: str, *, published: bool) -> bool:
    if _DATE_RE.fullmatch(value):
        return _valid_iso(value)
    if not published and (
        _MONTH_RE.fullmatch(value)
        or _QUARTER_RE.fullmatch(value)
        or _HALF_RE.fullmatch(value)
    ):
        return True
    if not published and "/" in value:
        left, separator, right = value.partition("/")
        return bool(
            separator
            and _valid_time(left, published=False)
            and _valid_time(right, published=False)
        )
    return _valid_iso(value, require_time=True)


def _valid_iso(value: str, *, require_time: bool = False) -> bool:
    if require_time and "T" not in value:
        return False
    if require_time and not (value.endswith("Z") or re.search(r"[+-]\d{2}:\d{2}$", value)):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = ["TextAnnotationProcessor", "render_time_tags"]
