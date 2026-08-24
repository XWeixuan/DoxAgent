from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdecr.contracts import SourceMessage
from doxagent.cdecr_integration.contracts import HistoricalLoadReport
from doxagent.cdecr_integration.historical_loader import (
    HistoricalNewsLoader,
    HistoricalStagingRepository,
)
from doxagent.monitoring.schema import (
    FetchedExternalMessage,
    InterfaceType,
    SourceType,
)


class FakeHistoricalProvider:
    provider_id = "benzinga_news"

    def __init__(self, rows: list[FetchedExternalMessage]) -> None:
        self.rows = rows

    def fetch(
        self, *, ticker: str, window_start: datetime, window_end: datetime
    ) -> Sequence[FetchedExternalMessage]:
        return list(self.rows)


def _body(index: int) -> str:
    sentence = (
        f"Company update {index} describes a concrete business development with enough detail "
        "for deterministic full-text admission and event extraction. "
    )
    return sentence * 12


def _row(index: int, *, as_of: datetime, url: str | None = None) -> FetchedExternalMessage:
    published = as_of - timedelta(days=index % 14, minutes=index)
    return FetchedExternalMessage(
        source_id="benzinga_news",
        binding_id="AMD:benzinga_news",
        ticker="AMD",
        source_type=SourceType.MEDIA,
        interface_type=InterfaceType.BY_TICKER,
        raw_payload={
            "id": index,
            "title": f"AMD update {index}",
            "body": _body(index),
            "url": url or f"https://example.com/news/{index}?utm_source=test",
            "created": published.isoformat(),
            "stocks": [{"name": "AMD"}],
            "author": "Reporter",
        },
        provider_message_id=str(index),
        source_url=url or f"https://example.com/news/{index}?utm_source=test",
        source_published_at=published,
    )


@pytest.mark.asyncio
async def test_historical_loader_isolated_deduped_and_stably_stratified(tmp_path: Path) -> None:
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    rows = [_row(index, as_of=as_of) for index in range(510)]
    rows.append(_row(999, as_of=as_of, url="https://example.com/news/1?utm_medium=dup"))

    async def run(name: str) -> tuple[list[SourceMessage], HistoricalLoadReport]:
        staging = HistoricalStagingRepository(tmp_path / name / "staging.sqlite3")
        loader = HistoricalNewsLoader(
            staging=staging,
            providers=[FakeHistoricalProvider(rows)],
            max_sources=500,
            sample_seed=77,
        )
        return await loader.load(market="US", ticker="AMD", as_of=as_of)

    selected_one, report_one = await run("one")
    selected_two, report_two = await run("two")
    assert len(selected_one) == 500
    assert report_one.selected_count == 500
    assert report_one.duplicate_count == 1
    assert [item.message_id for item in selected_one] == [
        item.message_id for item in selected_two
    ]
    assert report_one.selected_message_ids == report_two.selected_message_ids
    assert all(
        "utm_" not in item.url or item.url.endswith("utm_source=test")
        for item in selected_one
    )
    with sqlite3.connect(tmp_path / "one" / "staging.sqlite3") as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "historical_candidates" in tables
    assert "event_stream" not in tables
