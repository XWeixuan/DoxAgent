from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.preprocessing import (
    DREAMER_BLOCK_LIMIT,
    GROUNDER_FULL_LIMIT,
    align_unique_evidence_locator,
    exact_document_fingerprint,
    grounder_context,
    locator_to_evidence,
    minhash64,
    minhash_similarity,
    normalize_url,
    preprocess_source,
)
from cdecr.single_document_contracts import DuplicateRelationType, EvidenceLocator


def source(
    *,
    message_id: str = "MSG-1",
    title: str = "Micron raises outlook",
    text: str = "Micron raised its revenue outlook.\n\nDemand for memory chips strengthened.",
    url: str = "https://example.test/story?utm_source=x&id=3",
) -> SourceMessage:
    return SourceMessage(
        message_id=message_id,
        source_type=SourceType.NEWS,
        title=title,
        text=text,
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url=url,
        ticker_hints=["MU"],
        language=Language.EN,
    )


def test_unique_exact_evidence_text_realigns_model_offset() -> None:
    message = source(title="Alpha event and beta event")
    document = preprocess_source(message).document
    locator = EvidenceLocator(
        segment_id="title:0",
        start_char=0,
        end_char=5,
        text="beta event",
    )
    aligned = align_unique_evidence_locator(locator, document, message)
    assert (aligned.start_char, aligned.end_char) == (16, 26)
    assert locator_to_evidence(aligned, document, message).text == "beta event"


def test_repeated_evidence_text_does_not_get_heuristically_realigned() -> None:
    message = source(title="event then event")
    document = preprocess_source(message).document
    locator = EvidenceLocator(segment_id="title:0", start_char=1, end_char=6, text="event")
    with pytest.raises(ValueError, match="does not match"):
        align_unique_evidence_locator(locator, document, message)


def test_cleaning_preserves_offsets_and_maps_evidence_back() -> None:
    raw = (
        "Advertisement\n\n"
        "<script>secret navigation()</script>\n\n"
        "Micron raised guidance by 10%.\n\n"
        "Demand remained strong."
    )
    message = source(text=raw)
    result = preprocess_source(message)
    document = result.document
    assert "Advertisement" not in document.cleaned_text
    assert "secret navigation" not in document.cleaned_text
    segment = next(item for item in document.segments if "raised guidance" in item.text)
    local_start = segment.text.index("raised guidance")
    locator = EvidenceLocator(
        segment_id=segment.segment_id,
        start_char=local_start,
        end_char=local_start + len("raised guidance"),
        text="raised guidance",
    )
    span = locator_to_evidence(locator, document, message)
    assert message.text[span.start_char : span.end_char] == "raised guidance"
    assert span.start_char == raw.index("raised guidance")


def test_invalid_evidence_locator_is_rejected() -> None:
    message = source()
    document = preprocess_source(message).document
    with pytest.raises(ValueError, match="does not match"):
        locator_to_evidence(
            EvidenceLocator(segment_id="text:0", start_char=0, end_char=6, text="Broken"),
            document,
            message,
        )


def test_long_document_blocks_are_contiguous_bounded_and_overlap_once() -> None:
    paragraphs = [f"Paragraph {index} " + (chr(65 + index) * 20_000) for index in range(4)]
    document = preprocess_source(source(text="\n\n".join(paragraphs))).document
    assert document.is_long_document
    assert len(document.document_blocks) >= 2
    assert all(len(block.text) <= DREAMER_BLOCK_LIMIT for block in document.document_blocks)
    for previous, current in zip(
        document.document_blocks, document.document_blocks[1:], strict=False
    ):
        if current.overlap_segment_ids:
            assert current.overlap_segment_ids == [previous.segment_ids[-1]]


def test_huge_single_paragraph_is_split_without_losing_offset_identity() -> None:
    message = source(text="Micron " + ("memory demand " * 20_000))
    document = preprocess_source(message).document
    body_segments = [item for item in document.segments if item.field == "text"]
    assert len(body_segments) > 1
    for segment in body_segments:
        assert message.text[segment.original_start : segment.original_end] == segment.text


def test_fingerprints_minhash_and_url_normalization_are_deterministic() -> None:
    message = source()
    assert exact_document_fingerprint(message) == exact_document_fingerprint(message)
    signature = minhash64(message.text)
    assert len(signature) == 64
    assert minhash_similarity(signature, signature) == 1.0
    assert normalize_url(message.url) == "https://example.test/story?id=3"


def test_only_exact_duplicate_is_marked_reusable() -> None:
    first = preprocess_source(source(message_id="A")).document
    exact = preprocess_source(source(message_id="B"), known_documents=[first])
    assert exact.reusable_message_id == "A"
    assert exact.duplicate_relations[0].relation_type is DuplicateRelationType.EXACT

    normalized = preprocess_source(
        source(message_id="C", text="Micron  raised its revenue outlook. Demand strengthened."),
        known_documents=[first],
    )
    assert normalized.reusable_message_id is None


def test_same_normalized_url_creates_audit_relation_without_reuse() -> None:
    first = preprocess_source(source(message_id="A")).document
    second = preprocess_source(
        source(message_id="B", text="A completely different report.", url=source().url),
        known_documents=[first],
    )
    assert second.reusable_message_id is None
    assert second.duplicate_relations[0].relation_type is DuplicateRelationType.URL_REPRINT


def test_repeated_evidence_text_aligns_only_to_unique_nearest_occurrence() -> None:
    message = source(text="Micron raised guidance. Other text. Micron raised guidance.")
    document = preprocess_source(message).document
    locator = EvidenceLocator(
        segment_id="text:0",
        start_char=31,
        end_char=54,
        text="Micron raised guidance.",
    )
    aligned = align_unique_evidence_locator(locator, document, message)
    assert aligned.start_char == 36
    assert locator_to_evidence(aligned, document, message).text == locator.text


def test_short_repeated_evidence_text_remains_ambiguous() -> None:
    message = source(text="Micron rose. Micron fell.")
    document = preprocess_source(message).document
    locator = EvidenceLocator(
        segment_id="text:0",
        start_char=2,
        end_char=8,
        text="Micron",
    )
    with pytest.raises(ValueError, match="does not match segment slice"):
        align_unique_evidence_locator(locator, document, message)


def test_large_grounder_context_keeps_candidate_and_neighbor_segments() -> None:
    paragraphs = [f"P{index} " + (chr(65 + index) * 50_000) for index in range(6)]
    message = source(text="\n\n".join(paragraphs))
    document = preprocess_source(message).document
    assert len(document.cleaned_text) > GROUNDER_FULL_LIMIT
    candidate = SimpleNamespace(
        evidence_locations=[
            EvidenceLocator(
                segment_id="text:2",
                start_char=0,
                end_char=2,
                text="P2",
            )
        ]
    )
    context = grounder_context(document, [candidate])
    assert len(context) < len(document.cleaned_text)
    assert "[text:2]" in context
    assert "[text:1]" in context
    assert "[text:3]" in context
