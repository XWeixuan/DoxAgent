from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from cdecr.contracts import Language, SourceType
from cdecr.data import (
    DoxAtlasRawMediaReader,
    SourceReadError,
    detect_language,
    document_fingerprint,
    map_raw_media_row,
)
from cdecr.ports import RejectedSource, SourceQuery, SourceRecord


def query(*, limit: int = 10, min_text_chars: int = 5) -> SourceQuery:
    return SourceQuery(
        market="US",
        ticker="MU",
        start_at=datetime(2026, 6, 25, tzinfo=UTC),
        end_at=datetime(2026, 6, 26, tzinfo=UTC),
        limit=limit,
        min_text_chars=min_text_chars,
    )


def row(row_id: str, *, content: str = "A sufficiently long news body.") -> dict[str, str]:
    return {
        "id": row_id,
        "market": "us",
        "ticker": "MU",
        "published_at": "2026-06-25T12:00:00Z",
        "source_name": "Example Wire",
        "title": "Micron update",
        "content": content,
        "url": f"https://example.test/{row_id}",
    }


def test_mapping_and_custom_sha256_ignore_remote_content_hash() -> None:
    raw = {**row("7"), "content_hash": "untrusted"}
    mapped = map_raw_media_row(raw, query=query())
    assert isinstance(mapped, SourceRecord)
    assert mapped.message.message_id == "doxatlas:raw_media:7"
    assert mapped.message.source_type is SourceType.NEWS
    assert mapped.message.text == raw["content"]
    assert mapped.message.ticker_hints == ["MU"]
    assert mapped.document_fingerprint == document_fingerprint(raw["title"], raw["content"])
    assert mapped.document_fingerprint != "untrusted"


@pytest.mark.parametrize(
    "field", ["title", "content", "published_at", "source_name", "url", "ticker"]
)
def test_missing_required_fields_are_rejected(field: str) -> None:
    raw = row("8")
    raw[field] = ""
    mapped = map_raw_media_row(raw, query=query())
    assert isinstance(mapped, RejectedSource)
    assert f"missing_{field}" in mapped.reason_codes


def test_short_body_and_language_detection() -> None:
    mapped = map_raw_media_row(row("9", content="tiny"), query=query(min_text_chars=20))
    assert isinstance(mapped, RejectedSource)
    assert mapped.reason_codes == ["content_too_short"]
    assert detect_language("美光科技", "公司上调全年收入指引。") is Language.ZH
    assert detect_language("Micron", "The company raised guidance.") is Language.EN
    assert detect_language("123", "!!!") is Language.UND


def test_fingerprint_is_stable_under_nfkc_and_whitespace_normalization() -> None:
    assert document_fingerprint("Ａ  B", "one\n two") == document_fingerprint("A B", "one two")


def test_reader_pages_and_sends_only_bounded_get_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = parse_qs(request.url.query.decode())
        offset = int(params["offset"][0])
        payload = [row("1"), row("2")] if offset == 0 else [row("3")]
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reader = DoxAtlasRawMediaReader(
        supabase_url="https://project.supabase.co",
        publishable_key="publishable-key",
        page_size=2,
        client=client,
    )
    batch = reader.read(query(limit=3))
    assert batch.raw_count == 3
    assert len(batch.accepted) == 3
    assert len(requests) == 2
    for request in requests:
        params = parse_qs(request.url.query.decode())
        assert request.method == "GET"
        assert params["market"] == ["eq.us"]
        assert params["ticker"] == ["eq.MU"]
        assert params["limit"] == ["2"]
        assert "published_at.gte.2026-06-25T00:00:00Z" in params["and"][0]
        assert params["select"] == ["id,market,ticker,published_at,source_name,title,content,url"]
        assert request.headers["apikey"] == "publishable-key"


def test_reader_stops_at_accepted_limit() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row("1"), row("2")])

    reader = DoxAtlasRawMediaReader(
        supabase_url="https://project.supabase.co",
        publishable_key="key",
        page_size=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    batch = reader.read(query(limit=1))
    assert len(batch.accepted) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500, json={"body": "must not leak"}), "HTTP 500"),
        (httpx.Response(200, text="not-json"), "invalid JSON"),
        (httpx.Response(200, json={"not": "a list"}), "unexpected JSON shape"),
    ],
)
def test_reader_returns_safe_http_and_json_errors(response: httpx.Response, message: str) -> None:
    reader = DoxAtlasRawMediaReader(
        supabase_url="https://project.supabase.co",
        publishable_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: response)),
    )
    with pytest.raises(SourceReadError, match=message) as caught:
        reader.read(query())
    assert "secret-key" not in str(caught.value)
    assert "must not leak" not in str(caught.value)
