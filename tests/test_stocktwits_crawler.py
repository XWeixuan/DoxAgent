from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import (
    RequestRateLimiter,
    StocktwitsClientError,
    StocktwitsHTTPClient,
)
from doxagent.stocktwits.crawler import StocktwitsPollingCrawler
from doxagent.stocktwits.repository import (
    InMemoryStocktwitsRepository,
    SQLiteStocktwitsRepository,
    StocktwitsRepository,
    repository_from_settings,
)
from doxagent.stocktwits.schema import (
    CoverageStatus,
    CrawlRunStatus,
    StocktwitsCrawlerConfig,
    StocktwitsPage,
    StocktwitsTickerState,
    TickerMode,
)


class FakeStocktwitsClient:
    def __init__(self) -> None:
        self.pages: dict[tuple[str, str | None], StocktwitsPage | Exception] = {}
        self.calls: list[tuple[str, str | None, int]] = []

    def add_page(
        self,
        symbol: str,
        *,
        max_message_id: str | None,
        ids: list[int],
        cursor_more: bool | None = False,
        next_max_id: str | None = None,
    ) -> None:
        self.pages[(symbol.upper(), max_message_id)] = StocktwitsPage(
            messages=[_message(message_id, symbol) for message_id in ids],
            cursor_more=cursor_more,
            next_max_id=next_max_id,
        )

    def add_error(
        self,
        symbol: str,
        *,
        max_message_id: str | None,
        exc: Exception,
    ) -> None:
        self.pages[(symbol.upper(), max_message_id)] = exc

    def fetch_symbol_page(
        self,
        *,
        symbol: str,
        max_message_id: str | None = None,
        page_size: int = 30,
    ) -> StocktwitsPage:
        normalized = symbol.upper()
        self.calls.append((normalized, max_message_id, page_size))
        page = self.pages.get((normalized, max_message_id))
        if page is None:
            return StocktwitsPage(messages=[], cursor_more=False)
        if isinstance(page, Exception):
            raise page
        return page


class RecordingHTTPClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object] | None, dict[str, str] | None]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.requests.append((url, params, headers))
        request = httpx.Request("GET", url, params=params, headers=headers)
        return httpx.Response(
            200,
            json={"messages": [], "cursor": {"more": False}},
            request=request,
        )


def _crawler(
    *,
    client: FakeStocktwitsClient,
    repo: StocktwitsRepository | None = None,
    symbols: list[str] | None = None,
    hot_message_threshold: int = 80,
    hot_cooldown_successes: int = 3,
) -> StocktwitsPollingCrawler:
    return StocktwitsPollingCrawler(
        repository=repo or InMemoryStocktwitsRepository(),
        client=client,
        config=StocktwitsCrawlerConfig(
            symbols=symbols
            or ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD", "PLTR", "MU"],
            target_cadence_seconds=300,
            hot_cadence_seconds=60,
            page_size=3,
            max_pages_per_crawl=2,
            hot_message_threshold=hot_message_threshold,
            hot_cooldown_successes=hot_cooldown_successes,
        ),
        now=lambda: _NOW,
    )


def _message(message_id: int, symbol: str) -> dict[str, object]:
    created_at = datetime(2026, 6, 26, 12, 0, tzinfo=UTC) + timedelta(seconds=message_id)
    return {
        "id": message_id,
        "body": f"{symbol} message {message_id}",
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "user": {"id": "u1", "username": "tester", "name": "Tester"},
        "entities": {"sentiment": {"basic": "Bullish"}},
        "symbols": [{"symbol": symbol}],
    }


_NOW = datetime(2026, 6, 26, 12, 30, tzinfo=UTC)


def test_http_client_uses_browser_like_headers_for_public_stream_api() -> None:
    transport = RecordingHTTPClient()
    client = StocktwitsHTTPClient(
        client=transport,
        rate_limiter=RequestRateLimiter(min_interval_seconds=0),
    )

    client.fetch_symbol_page(symbol="MU", page_size=5)

    assert len(transport.requests) == 1
    url, params, headers = transport.requests[0]
    assert url.endswith("/streams/symbol/MU.json")
    assert params == {"limit": 5}
    assert headers is not None
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert headers["Accept-Language"] == "en-US,en;q=0.9"
    assert headers["Origin"] == "https://stocktwits.com"


def test_initialize_tickers_staggers_10_symbols_across_5_minute_window() -> None:
    client = FakeStocktwitsClient()
    crawler = _crawler(client=client)

    states = crawler.initialize_tickers(now=_NOW)

    assert len(states) == 10
    assert [state.symbol for state in states] == [
        "AAPL",
        "MSFT",
        "NVDA",
        "TSLA",
        "AMZN",
        "META",
        "GOOGL",
        "AMD",
        "PLTR",
        "MU",
    ]
    assert [(state.next_due_at - _NOW).total_seconds() for state in states] == [
        0,
        30,
        60,
        90,
        120,
        150,
        180,
        210,
        240,
        270,
    ]


def test_repository_from_settings_defaults_to_local_sqlite(tmp_path) -> None:
    db_path = tmp_path / "stocktwits.sqlite3"
    settings = DoxAgentSettings(stocktwits_sqlite_path=str(db_path))

    repository = repository_from_settings(settings)

    assert isinstance(repository, SQLiteStocktwitsRepository)
    assert repository.path == db_path


def test_repository_from_settings_blocks_postgres_without_explicit_migration_opt_in() -> None:
    settings = DoxAgentSettings(
        stocktwits_storage_mode="postgres",
        database_url="postgresql://example.invalid/db",
    )

    try:
        repository_from_settings(settings)
    except RuntimeError as exc:
        assert "Postgres/Supabase persistence is disabled" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("postgres Stocktwits persistence should be blocked by default")


def test_sqlite_repository_persists_state_messages_and_runs(tmp_path) -> None:
    db_path = tmp_path / "stocktwits.sqlite3"
    repo = SQLiteStocktwitsRepository(db_path)
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[103, 102], cursor_more=False)
    crawler = _crawler(client=client, repo=repo, symbols=["MU"])

    run = crawler.crawl_symbol("MU", now=_NOW)
    reopened = SQLiteStocktwitsRepository(db_path)
    state = reopened.get_ticker_state("MU")
    runs = reopened.recent_crawl_runs(symbol="MU", limit=1)

    assert state is not None
    assert state.last_seen_message_id == "103"
    assert run.inserted_count == 2
    assert runs[0].run_id == run.run_id
    assert runs[0].coverage_status is CoverageStatus.LIKELY_COMPLETE


def test_initial_crawl_inserts_messages_then_advances_checkpoint() -> None:
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[103, 102], cursor_more=False)
    repo = InMemoryStocktwitsRepository()
    crawler = _crawler(client=client, repo=repo, symbols=["MU"])

    run = crawler.crawl_symbol("MU", now=_NOW)
    state = repo.get_ticker_state("MU")

    assert state is not None
    assert run.status is CrawlRunStatus.SUCCEEDED
    assert run.coverage_status is CoverageStatus.LIKELY_COMPLETE
    assert run.inserted_count == 2
    assert run.duplicate_count == 0
    assert state.last_seen_message_id == "103"
    assert state.latest_coverage_status is CoverageStatus.LIKELY_COMPLETE


def test_repeated_crawl_dedupes_checkpoint_message_and_inserts_newer_messages() -> None:
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[103, 102], cursor_more=False)
    repo = InMemoryStocktwitsRepository()
    crawler = _crawler(client=client, repo=repo, symbols=["MU"])
    crawler.crawl_symbol("MU", now=_NOW)
    client.pages.clear()
    client.add_page("MU", max_message_id=None, ids=[105, 104, 103], cursor_more=True)

    run = crawler.crawl_symbol("MU", now=_NOW + timedelta(minutes=5))
    state = repo.get_ticker_state("MU")

    assert state is not None
    assert run.coverage_status is CoverageStatus.COMPLETE
    assert run.checkpoint_found is True
    assert run.inserted_count == 2
    assert run.duplicate_count == 1
    assert state.last_seen_message_id == "105"


def test_checkpoint_not_found_records_gap_and_preserves_old_checkpoint() -> None:
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[105, 104, 103], cursor_more=True)
    client.add_page("MU", max_message_id="102", ids=[102, 101], cursor_more=False)
    repo = InMemoryStocktwitsRepository()
    repo.upsert_ticker_state(
        StocktwitsTickerState(
            symbol="MU",
            next_due_at=_NOW,
            last_seen_message_id="100",
            last_seen_message_created_at=_NOW - timedelta(minutes=5),
            last_successful_crawl_at=_NOW - timedelta(minutes=5),
        )
    )
    crawler = _crawler(client=client, repo=repo, symbols=["MU"])

    run = crawler.crawl_symbol("MU", now=_NOW)
    state = repo.get_ticker_state("MU")

    assert state is not None
    assert run.coverage_status is CoverageStatus.GAP_DETECTED
    assert run.gap_reason == "checkpoint_not_found_before_stream_exhausted"
    assert run.checkpoint_found is False
    assert state.last_seen_message_id == "100"
    assert state.current_mode is TickerMode.HOT
    assert state.latest_coverage_status is CoverageStatus.GAP_DETECTED


def test_initial_incomplete_bootstraps_checkpoint_after_persisting_messages() -> None:
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[106, 105, 104], cursor_more=True)
    client.add_page("MU", max_message_id="103", ids=[103, 102, 101], cursor_more=True)
    repo = InMemoryStocktwitsRepository()
    crawler = _crawler(client=client, repo=repo, symbols=["MU"])

    run = crawler.crawl_symbol("MU", now=_NOW)
    state = repo.get_ticker_state("MU")

    assert state is not None
    assert run.coverage_status is CoverageStatus.INCOMPLETE
    assert run.gap_reason == "initial_history_exceeded_page_budget"
    assert run.inserted_count == 6
    assert state.last_seen_message_id == "106"
    assert state.current_mode is TickerMode.HOT
    assert state.last_successful_crawl_at is None


def test_one_ticker_failure_does_not_block_other_due_tickers() -> None:
    client = FakeStocktwitsClient()
    client.add_error(
        "AAPL",
        max_message_id=None,
        exc=StocktwitsClientError("timeout", code="timeout"),
    )
    client.add_page("MSFT", max_message_id=None, ids=[201], cursor_more=False)
    repo = InMemoryStocktwitsRepository()
    repo.upsert_ticker_state(StocktwitsTickerState(symbol="AAPL", next_due_at=_NOW))
    repo.upsert_ticker_state(StocktwitsTickerState(symbol="MSFT", next_due_at=_NOW))
    crawler = _crawler(client=client, repo=repo, symbols=["AAPL", "MSFT"])

    runs = crawler.poll_due_once(max_tickers=2, now=_NOW)
    states = {state.symbol: state for state in repo.list_ticker_states()}

    assert [run.symbol for run in runs] == ["AAPL", "MSFT"]
    assert runs[0].status is CrawlRunStatus.FAILED
    assert runs[0].coverage_status is CoverageStatus.FAILED
    assert runs[1].status is CrawlRunStatus.SUCCEEDED
    assert states["AAPL"].latest_coverage_status is CoverageStatus.FAILED
    assert states["MSFT"].last_seen_message_id == "201"


def test_hot_mode_triggers_on_high_volume_and_cools_after_complete_runs() -> None:
    client = FakeStocktwitsClient()
    client.add_page("MU", max_message_id=None, ids=[103, 102, 101], cursor_more=False)
    repo = InMemoryStocktwitsRepository()
    crawler = _crawler(
        client=client,
        repo=repo,
        symbols=["MU"],
        hot_message_threshold=3,
        hot_cooldown_successes=2,
    )

    hot_run = crawler.crawl_symbol("MU", now=_NOW)
    state_after_hot = repo.get_ticker_state("MU")
    assert state_after_hot is not None
    assert hot_run.coverage_status is CoverageStatus.LIKELY_COMPLETE
    assert state_after_hot.current_mode is TickerMode.HOT

    client.pages.clear()
    client.add_page("MU", max_message_id=None, ids=[104, 103], cursor_more=False)
    crawler.crawl_symbol("MU", now=_NOW + timedelta(minutes=1))
    client.pages.clear()
    client.add_page("MU", max_message_id=None, ids=[105, 104], cursor_more=False)
    crawler.crawl_symbol("MU", now=_NOW + timedelta(minutes=2))
    cooled_state = repo.get_ticker_state("MU")

    assert cooled_state is not None
    assert cooled_state.current_mode is TickerMode.NORMAL
