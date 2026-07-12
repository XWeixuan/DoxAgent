"""Generic, recursive and strictly non-blocking text annotation processing."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from doxagent.annotations.models import (
    AnnotationBatch,
    AnnotationMetrics,
    CitationAnnotation,
    ProcessedText,
    TimeAnnotation,
)
from doxagent.annotations.store import AnnotationStore

_TAG_RE = re.compile(
    r"【(?P<kind>cite|occurred_at|published_at):(?P<value>[^】]+)】"
)
_ALIAS_RE = re.compile(r"^O[1-9]\d*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
_HALF_RE = re.compile(r"^\d{4}-H[12]$")


class ObservationAliasResolver(Protocol):
    def resolve(self, alias: str) -> str | None: ...


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

    def _walk(self, value: Any, *, path: str, **context: Any) -> Any:
        if isinstance(value, str):
            item = self._process_text(value, payload_path=path or "/", **context)
            context["processed"].append(item)
            return item.plain_text
        if isinstance(value, list):
            return [
                self._walk(item, path=f"{path}/{index}", **context)
                for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            return {
                key: self._walk(item, path=f"{path}/{_escape(str(key))}", **context)
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
        **_: Any,
    ) -> ProcessedText:
        plain_text = _TAG_RE.sub("", raw_text)
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
        for match in _TAG_RE.finditer(raw_text):
            kind = match.group("kind")
            value = match.group("value").strip()
            if kind == "cite":
                metrics.citation_tag_count += 1
                block_id = aliases.resolve(value) if _ALIAS_RE.fullmatch(value) else None
                if block_id is None:
                    metrics.invalid_alias_count += 1
                    local_warnings.append(f"invalid_citation_alias:{payload_path}:{value}")
                    continue
                record = CitationAnnotation(
                    annotation_id=f"citation_{uuid4().hex}",
                    run_id=run_id,
                    task_id=task_id,
                    result_id=result_id,
                    payload_path=payload_path,
                    text_hash=text_hash,
                    span_start=match.start(),
                    span_end=match.end(),
                    observation_block_id=block_id,
                    created_at=created_at,
                )
                local_citations.append(record)
                metrics.resolved_citation_count += 1
                continue
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
