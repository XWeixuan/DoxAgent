"""Deterministic, offset-preserving source preprocessing for CDECR."""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cdecr.contracts import EvidenceSpan, SourceMessage
from cdecr.single_document_contracts import (
    DocumentBlock,
    DuplicateRelation,
    DuplicateRelationType,
    EvidenceLocator,
    EvidenceText,
    PreprocessedDocument,
    PreprocessingResult,
    SegmentKind,
    SourceSegment,
)

PIPELINE_VERSION = "single-document-v7"
DREAMER_SHORT_LIMIT = 24_000
DREAMER_BLOCK_LIMIT = 24_000
COMMON_CONTEXT_LIMIT = 4_000
GROUNDER_FULL_LIMIT = 250_000

_SCRIPT_RE = re.compile(
    r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>", re.I | re.S
)
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)
_TRACKING_QUERY_PREFIXES = ("utm_", "mc_", "ref_")
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "ref", "source", "campaign"}
_BOILERPLATE_PATTERNS = (
    re.compile(r"^\s*(?:advertisement|sponsored content|skip to content)\s*$", re.I),
    re.compile(r"^\s*(?:sign up|subscribe|log in|cookie policy|privacy policy)\b", re.I),
    re.compile(r"^\s*(?:home|markets|news|business|technology)(?:\s*[|>›]\s*\w+){2,}\s*$", re.I),
    re.compile(r"^\s*(?:share this article|read more|related articles?)\s*$", re.I),
)
_WRAPPING_QUOTES = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
}
_EQUIVALENT_CHARACTERS = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
}

EvidenceResolution = Literal[
    "EXACT",
    "STRIP_WRAPPING_QUOTES",
    "NORMALIZED_EQUIVALENT",
    "SEGMENT_CORRECTED",
    "ANCHOR_DISAMBIGUATED",
]


@dataclass(frozen=True)
class EvidenceReconciliation:
    locator: EvidenceLocator
    resolution: EvidenceResolution
    segment_id_before: str
    source_span_hash: str


def exact_document_fingerprint(source: SourceMessage) -> str:
    payload = f"{source.title}\n{source.text}".encode()
    return hashlib.sha256(payload).hexdigest()


def normalize_document_text(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalized_document_fingerprint(source: SourceMessage) -> str:
    normalized = f"{normalize_document_text(source.title)}\n{normalize_document_text(source.text)}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_url(value: str) -> str:
    split = urlsplit(value.strip())
    filtered = [
        (key, item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
        and not key.casefold().startswith(_TRACKING_QUERY_PREFIXES)
    ]
    path = re.sub(r"/{2,}", "/", split.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(
        (split.scheme.casefold(), split.netloc.casefold(), path, urlencode(filtered), "")
    )


def _shingles(value: str, *, size: int = 5) -> set[str]:
    words = _WORD_RE.findall(normalize_document_text(value))
    if len(words) < size:
        return {" ".join(words)} if words else {""}
    return {" ".join(words[index : index + size]) for index in range(len(words) - size + 1)}


def minhash64(value: str) -> list[int]:
    """Return a deterministic 64-permutation, unsigned 64-bit MinHash signature."""

    shingles = _shingles(value)
    signature: list[int] = []
    for seed in range(64):
        prefix = seed.to_bytes(2, "big")
        signature.append(
            min(
                int.from_bytes(
                    hashlib.blake2b(prefix + shingle.encode("utf-8"), digest_size=8).digest(),
                    "big",
                )
                for shingle in shingles
            )
        )
    return signature


def minhash_similarity(left: Sequence[int], right: Sequence[int]) -> float:
    if len(left) != 64 or len(right) != 64:
        raise ValueError("MinHash signatures must contain exactly 64 values")
    return sum(a == b for a, b in zip(left, right, strict=True)) / 64


def _excluded_intervals(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _SCRIPT_RE.finditer(text)]


def _overlaps(start: int, end: int, intervals: Sequence[tuple[int, int]]) -> bool:
    return any(
        start < excluded_end and end > excluded_start for excluded_start, excluded_end in intervals
    )


def _is_boilerplate(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _BOILERPLATE_PATTERNS)


def _paragraph_ranges(text: str) -> Iterable[tuple[int, int]]:
    """Yield non-empty paragraph ranges without changing original characters."""

    for match in re.finditer(r"[^\r\n]+(?:\r?\n(?!\s*\r?\n)[^\r\n]+)*", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            yield start, end


def _make_segments(source: SourceMessage) -> tuple[list[SourceSegment], int]:
    segments = [
        SourceSegment(
            segment_id="title:0",
            kind=SegmentKind.TITLE,
            field="title",
            text=source.title,
            original_start=0,
            original_end=len(source.title),
        )
    ]
    excluded = _excluded_intervals(source.text)
    removed = 0
    paragraph_index = 0
    for start, end in _paragraph_ranges(source.text):
        paragraph = source.text[start:end]
        if _overlaps(start, end, excluded) or _is_boilerplate(paragraph):
            removed += 1
            continue
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(end, chunk_start + 44_000)
            if chunk_end < end:
                whitespace = source.text.rfind(" ", chunk_start, chunk_end)
                if whitespace > chunk_start:
                    chunk_end = whitespace
            kind = SegmentKind.LEAD if paragraph_index == 0 else SegmentKind.PARAGRAPH
            segments.append(
                SourceSegment(
                    segment_id=f"text:{paragraph_index}",
                    kind=kind,
                    field="text",
                    text=source.text[chunk_start:chunk_end],
                    original_start=chunk_start,
                    original_end=chunk_end,
                    paragraph_index=paragraph_index,
                )
            )
            paragraph_index += 1
            chunk_start = chunk_end
            while chunk_start < end and source.text[chunk_start].isspace():
                chunk_start += 1
    return segments, removed


def _serialize_segments(segments: Sequence[SourceSegment]) -> str:
    return "\n\n".join(f"[{segment.segment_id}]\n{segment.text}" for segment in segments)


def _make_blocks(segments: Sequence[SourceSegment]) -> list[DocumentBlock]:
    title = segments[0]
    body = list(segments[1:])
    common_segments = [title]
    if body:
        common_segments.append(body[0])
    common_context = _serialize_segments(common_segments)[:COMMON_CONTEXT_LIMIT]
    all_text = _serialize_segments(segments)
    if len(all_text) <= DREAMER_SHORT_LIMIT:
        return [
            DocumentBlock(
                block_id="block:0",
                segment_ids=[segment.segment_id for segment in segments],
                text=all_text,
                common_context="",
            )
        ]

    blocks: list[DocumentBlock] = []
    cursor = 0
    prior: SourceSegment | None = None
    while cursor < len(body):
        selected: list[SourceSegment] = [prior] if prior is not None else []
        overlap_ids = [prior.segment_id] if prior is not None else []
        first_new = body[cursor]
        if selected and len(_serialize_segments([*selected, first_new])) > DREAMER_BLOCK_LIMIT:
            selected = []
            overlap_ids = []
        while cursor < len(body):
            candidate = body[cursor]
            candidate_text = _serialize_segments([*selected, candidate])
            if selected and len(candidate_text) > DREAMER_BLOCK_LIMIT:
                break
            selected.append(candidate)
            cursor += 1
            if len(candidate_text) >= DREAMER_BLOCK_LIMIT:
                break
        if not selected:
            selected = [body[cursor]]
            cursor += 1
        blocks.append(
            DocumentBlock(
                block_id=f"block:{len(blocks)}",
                segment_ids=[segment.segment_id for segment in selected],
                text=_serialize_segments(selected),
                common_context=common_context,
                overlap_segment_ids=overlap_ids,
            )
        )
        prior = selected[-1]
    return blocks


def preprocess_source(
    source: SourceMessage,
    *,
    known_documents: Sequence[PreprocessedDocument] = (),
    near_duplicate_threshold: float = 0.80,
) -> PreprocessingResult:
    segments, removed = _make_segments(source)
    signature = minhash64(f"{source.title}\n{source.text}")
    exact = exact_document_fingerprint(source)
    normalized = normalized_document_fingerprint(source)
    normalized_url = normalize_url(source.url)
    cleaned = _serialize_segments(segments)
    document = PreprocessedDocument(
        message_id=source.message_id,
        pipeline_version=PIPELINE_VERSION,
        source_fingerprint=exact,
        normalized_fingerprint=normalized,
        normalized_url=normalized_url,
        segments=segments,
        cleaned_text=cleaned,
        document_blocks=_make_blocks(segments),
        minhash64=signature,
        is_long_document=len(source.text) > DREAMER_SHORT_LIMIT,
        is_complex_document=len(segments) > 80,
        removed_span_count=removed,
    )
    relations: list[DuplicateRelation] = []
    reusable: str | None = None
    for known in known_documents:
        relation_type: DuplicateRelationType | None = None
        score = 0.0
        if exact == known.source_fingerprint:
            relation_type = DuplicateRelationType.EXACT
            score = 1.0
            reusable = reusable or known.message_id
        elif normalized == known.normalized_fingerprint:
            relation_type = DuplicateRelationType.NORMALIZED
            score = 1.0
        elif normalized_url == known.normalized_url:
            relation_type = DuplicateRelationType.URL_REPRINT
            score = 1.0
        else:
            score = minhash_similarity(signature, known.minhash64)
            if score >= near_duplicate_threshold:
                relation_type = DuplicateRelationType.NEAR
        if relation_type is not None:
            relation_id = hashlib.sha256(
                f"{source.message_id}|{known.message_id}|{relation_type.value}".encode()
            ).hexdigest()
            relations.append(
                DuplicateRelation(
                    relation_id=f"duplicate:{relation_id}",
                    source_message_id=source.message_id,
                    target_message_id=known.message_id,
                    relation_type=relation_type,
                    score=score,
                )
            )
    return PreprocessingResult(
        document=document,
        duplicate_relations=relations,
        reusable_message_id=reusable,
    )


def locator_to_evidence(
    locator: EvidenceLocator,
    document: PreprocessedDocument,
    source: SourceMessage,
) -> EvidenceSpan:
    segment = next(
        (item for item in document.segments if item.segment_id == locator.segment_id),
        None,
    )
    if segment is None:
        raise ValueError(f"unknown evidence segment {locator.segment_id}")
    if locator.end_char > len(segment.text):
        raise ValueError("evidence locator is outside its source segment")
    if segment.text[locator.start_char : locator.end_char] != locator.text:
        raise ValueError("evidence locator text does not match segment slice")
    span = EvidenceSpan(
        field=segment.field,
        start_char=segment.original_start + locator.start_char,
        end_char=segment.original_start + locator.end_char,
        text=locator.text,
    )
    span.validate_source(source)
    return span


def align_unique_evidence_locator(
    locator: EvidenceLocator,
    document: PreprocessedDocument,
    source: SourceMessage,
) -> EvidenceLocator:
    """Correct an offset only when the cited text has one exact occurrence in its segment."""

    segment = next(
        (item for item in document.segments if item.segment_id == locator.segment_id),
        None,
    )
    if segment is None:
        locator_to_evidence(locator, document, source)
        return locator  # pragma: no cover - locator_to_evidence always raises
    if (
        locator.end_char <= len(segment.text)
        and segment.text[locator.start_char : locator.end_char] == locator.text
    ):
        locator_to_evidence(locator, document, source)
        return locator
    starts: list[int] = []
    cursor = 0
    while True:
        start = segment.text.find(locator.text, cursor)
        if start < 0:
            break
        starts.append(start)
        cursor = start + 1
    if not starts:
        locator_to_evidence(locator, document, source)
        return locator  # pragma: no cover - locator_to_evidence always raises
    if len(starts) > 1:
        distances = sorted((abs(start - locator.start_char), start) for start in starts)
        if len(locator.text) < 12 or (len(distances) > 1 and distances[0][0] == distances[1][0]):
            locator_to_evidence(locator, document, source)
            return locator  # pragma: no cover - locator_to_evidence always raises
        start = distances[0][1]
    else:
        start = starts[0]
    aligned = locator.model_copy(
        update={"start_char": start, "end_char": start + len(locator.text)}
    )
    locator_to_evidence(aligned, document, source)
    return aligned


def locate_unique_evidence_text(
    evidence: EvidenceText,
    document: PreprocessedDocument,
    source: SourceMessage,
) -> EvidenceLocator:
    """Resolve an LLM quote to one exact segment-local interval."""

    segment = next(
        (item for item in document.segments if item.segment_id == evidence.segment_id),
        None,
    )
    if segment is None:
        raise ValueError(f"unknown evidence segment {evidence.segment_id}")
    starts: list[int] = []
    cursor = 0
    while True:
        start = segment.text.find(evidence.text, cursor)
        if start < 0:
            break
        starts.append(start)
        cursor = start + 1
    if not starts:
        raise ValueError("evidence text does not occur in its source segment")
    if len(starts) != 1:
        raise ValueError("evidence text is ambiguous within its source segment")
    locator = EvidenceLocator(
        segment_id=evidence.segment_id,
        start_char=starts[0],
        end_char=starts[0] + len(evidence.text),
        text=evidence.text,
    )
    locator_to_evidence(locator, document, source)
    return locator


def _occurrences(text: str, needle: str) -> list[int]:
    starts: list[int] = []
    cursor = 0
    while needle:
        start = text.find(needle, cursor)
        if start < 0:
            break
        starts.append(start)
        cursor = start + 1
    return starts


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) < 2:
        return value
    expected = _WRAPPING_QUOTES.get(value[0])
    if expected is None or value[-1] != expected:
        return value
    stripped = value[1:-1]
    return stripped if stripped else value


def _normalized_equivalent_with_spans(
    value: str,
) -> tuple[str, list[tuple[int, int]]]:
    normalized: list[str] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(value):
        entity = re.match(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);", value[cursor:])
        if entity is not None:
            raw = entity.group(0)
            decoded = html.unescape(raw)
            if decoded != raw:
                source_start = cursor
                cursor += len(raw)
                for character in decoded:
                    mapped = _EQUIVALENT_CHARACTERS.get(character, character)
                    if mapped.isspace():
                        mapped = " "
                    for item in mapped:
                        normalized.append(item)
                        spans.append((source_start, cursor))
                continue
        source_start = cursor
        character = value[cursor]
        cursor += 1
        mapped = _EQUIVALENT_CHARACTERS.get(character, character)
        if mapped.isspace():
            mapped = " "
            while cursor < len(value) and value[cursor].isspace():
                cursor += 1
        for item in mapped:
            normalized.append(item)
            spans.append((source_start, cursor))
    return "".join(normalized), spans


def _locator(
    *,
    segment_id: str,
    segment_text: str,
    start: int,
    end: int,
) -> EvidenceLocator:
    return EvidenceLocator(
        segment_id=segment_id,
        start_char=start,
        end_char=end,
        text=segment_text[start:end],
    )


def _reconciled(
    *,
    evidence: EvidenceText,
    locator: EvidenceLocator,
    resolution: EvidenceResolution,
    document: PreprocessedDocument,
    source: SourceMessage,
) -> EvidenceReconciliation:
    span = locator_to_evidence(locator, document, source)
    source_span_hash = hashlib.sha256(
        (
            f"{span.field}|{span.start_char}|{span.end_char}|{span.text}"
        ).encode()
    ).hexdigest()
    return EvidenceReconciliation(
        locator=locator,
        resolution=resolution,
        segment_id_before=evidence.segment_id,
        source_span_hash=source_span_hash,
    )


def reconcile_evidence_text(
    evidence: EvidenceText,
    document: PreprocessedDocument,
    source: SourceMessage,
    *,
    candidate_anchors: Sequence[EvidenceLocator] = (),
) -> EvidenceReconciliation:
    """Resolve model Evidence conservatively and return an exact source locator."""

    segments = {item.segment_id: item for item in document.segments}
    declared = segments.get(evidence.segment_id)
    exact_starts = (
        _occurrences(declared.text, evidence.text) if declared is not None else []
    )
    if declared is not None and len(exact_starts) == 1:
        start = exact_starts[0]
        return _reconciled(
            evidence=evidence,
            locator=_locator(
                segment_id=declared.segment_id,
                segment_text=declared.text,
                start=start,
                end=start + len(evidence.text),
            ),
            resolution="EXACT",
            document=document,
            source=source,
        )

    stripped = _strip_wrapping_quotes(evidence.text)
    stripped_starts = (
        _occurrences(declared.text, stripped) if declared is not None else []
    )
    if stripped != evidence.text and declared is not None and len(stripped_starts) == 1:
        start = stripped_starts[0]
        return _reconciled(
            evidence=evidence,
            locator=_locator(
                segment_id=declared.segment_id,
                segment_text=declared.text,
                start=start,
                end=start + len(stripped),
            ),
            resolution="STRIP_WRAPPING_QUOTES",
            document=document,
            source=source,
        )

    working_text = stripped if stripped != evidence.text else evidence.text
    if declared is not None:
        normalized_segment, segment_spans = _normalized_equivalent_with_spans(
            declared.text
        )
        normalized_evidence, _ = _normalized_equivalent_with_spans(working_text)
        normalized_starts = _occurrences(normalized_segment, normalized_evidence)
        if len(normalized_starts) == 1 and normalized_evidence:
            normalized_start = normalized_starts[0]
            normalized_end = normalized_start + len(normalized_evidence)
            original_start = segment_spans[normalized_start][0]
            original_end = segment_spans[normalized_end - 1][1]
            return _reconciled(
                evidence=evidence,
                locator=_locator(
                    segment_id=declared.segment_id,
                    segment_text=declared.text,
                    start=original_start,
                    end=original_end,
                ),
                resolution="NORMALIZED_EQUIVALENT",
                document=document,
                source=source,
            )

    cross_segment_matches: list[tuple[str, int]] = []
    for segment in document.segments:
        for start in _occurrences(segment.text, working_text):
            cross_segment_matches.append((segment.segment_id, start))
    if len(cross_segment_matches) == 1:
        segment_id, start = cross_segment_matches[0]
        segment = segments[segment_id]
        return _reconciled(
            evidence=evidence,
            locator=_locator(
                segment_id=segment_id,
                segment_text=segment.text,
                start=start,
                end=start + len(working_text),
            ),
            resolution="SEGMENT_CORRECTED",
            document=document,
            source=source,
        )

    candidate_starts = exact_starts or stripped_starts
    candidate_text = evidence.text if exact_starts else stripped
    if declared is not None and len(candidate_starts) > 1:
        anchored: list[tuple[int, int]] = []
        for start in candidate_starts:
            end = start + len(candidate_text)
            overlap = sum(
                max(0, min(end, anchor.end_char) - max(start, anchor.start_char))
                for anchor in candidate_anchors
                if anchor.segment_id == declared.segment_id
            )
            anchored.append((overlap, start))
        anchored.sort(reverse=True)
        if (
            anchored
            and anchored[0][0] > 0
            and (len(anchored) == 1 or anchored[0][0] > anchored[1][0])
        ):
            start = anchored[0][1]
            return _reconciled(
                evidence=evidence,
                locator=_locator(
                    segment_id=declared.segment_id,
                    segment_text=declared.text,
                    start=start,
                    end=start + len(candidate_text),
                ),
                resolution="ANCHOR_DISAMBIGUATED",
                document=document,
                source=source,
            )

    if declared is None:
        raise ValueError(f"unknown evidence segment {evidence.segment_id}")
    if candidate_starts:
        raise ValueError("evidence text is ambiguous within its source segment")
    raise ValueError("evidence text does not occur in its source segment")


def grounder_context(document: PreprocessedDocument, candidates: Sequence[object]) -> str:
    """Bound large Grounder inputs to candidate evidence and adjacent paragraphs."""

    if len(document.cleaned_text) <= GROUNDER_FULL_LIMIT:
        return document.cleaned_text
    segment_by_id = {segment.segment_id: segment for segment in document.segments}
    wanted: set[str] = {document.segments[0].segment_id}
    if len(document.segments) > 1:
        wanted.add(document.segments[1].segment_id)
    for candidate in candidates:
        locations = getattr(candidate, "evidence_locations", ())
        for location in locations:
            segment = segment_by_id.get(location.segment_id)
            if segment is None:
                continue
            wanted.add(segment.segment_id)
            if segment.paragraph_index is not None:
                for neighbor in document.segments:
                    if neighbor.paragraph_index in {
                        segment.paragraph_index - 1,
                        segment.paragraph_index + 1,
                    }:
                        wanted.add(neighbor.segment_id)
    selected = [segment for segment in document.segments if segment.segment_id in wanted]
    return _serialize_segments(selected)
