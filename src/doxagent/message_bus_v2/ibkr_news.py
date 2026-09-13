"""Long-lived IBKR news session with streaming tick 292 and historical fallback."""

from __future__ import annotations

import asyncio
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any, Protocol, cast

from doxagent.message_bus_v2.schema import JsonObject, PollContext, PollResult, RawMessageInput
from doxagent.settings import DoxAgentSettings


class IbkrNewsGatewayProtocol(Protocol):
    def poll(
        self, ticker: str, since: datetime, until: datetime
    ) -> tuple[list[JsonObject], JsonObject]: ...
    def close(self) -> None: ...


class IbkrNewsGateway:
    """One TWS socket connection shared by every ticker binding in the worker."""

    def __init__(self, settings: DoxAgentSettings) -> None:
        self.settings = settings
        self._app: Any | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._id_lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._subscriptions: dict[str, int] = {}
        self._conids: dict[str, int] = {}
        self._stream: dict[str, list[JsonObject]] = defaultdict(list)
        self._next_request_id = 29_200

    def _request_id(self) -> int:
        with self._id_lock:
            self._next_request_id += 1
            return self._next_request_id

    def _ensure(self) -> Any:
        if not self.settings.ibkr_tws_enabled:
            raise RuntimeError("IBKR_TWS_ENABLED must be true for IBKR news")
        if self._app is not None and self._app.isConnected():
            return self._app
        if self._app is not None:
            self._app.disconnect()
            self._app = None
            self._subscriptions.clear()
            self._conids.clear()
        try:
            from ibapi.client import EClient  # type: ignore[import-untyped]
            from ibapi.wrapper import EWrapper  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("ibapi is required for IBKR news") from exc
        owner = self

        class App(EWrapper, EClient):  # type: ignore[misc]
            def __init__(self) -> None:
                EWrapper.__init__(self)
                EClient.__init__(self, self)
                self.connected = threading.Event()
                self.providers_done = threading.Event()
                self.contract_events: dict[int, threading.Event] = {}
                self.contract_results: dict[int, int] = {}
                self.historical_events: dict[int, threading.Event] = {}
                self.historical_rows: dict[int, list[JsonObject]] = defaultdict(list)
                self.article_events: dict[int, threading.Event] = {}
                self.article_results: dict[int, tuple[int, str]] = {}
                self.providers: list[tuple[str, str]] = []
                self.errors: list[str] = []

            def nextValidId(self, orderId: int) -> None:  # noqa: N802
                self.connected.set()

            def newsProviders(self, providers: list[Any]) -> None:  # noqa: N802
                self.providers = [(str(item.code), str(item.name)) for item in providers]
                self.providers_done.set()

            def contractDetails(self, reqId: int, details: Any) -> None:  # noqa: N802
                self.contract_results[reqId] = int(details.contract.conId)

            def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
                self.contract_events.setdefault(reqId, threading.Event()).set()

            def tickNews(
                self,
                tickerId: int,
                timeStamp: int,
                providerCode: str,
                articleId: str,
                headline: str,
                extraData: str,
            ) -> None:  # noqa: N802,E501
                ticker = next(
                    (key for key, value in owner._subscriptions.items() if value == tickerId), ""
                )
                if ticker:
                    with owner._stream_lock:
                        owner._stream[ticker].append(
                            {
                                "time": timeStamp,
                                "providerCode": providerCode,
                                "articleId": articleId,
                                "headline": headline,
                                "extraData": extraData,
                                "transport": "tick_292",
                            }
                        )

            def historicalNews(
                self,
                requestId: int,
                timeValue: str,
                providerCode: str,
                articleId: str,
                headline: str,
            ) -> None:  # noqa: N802,E501
                self.historical_rows[requestId].append(
                    {
                        "time": timeValue,
                        "providerCode": providerCode,
                        "articleId": articleId,
                        "headline": headline,
                        "transport": "historical_fallback",
                    }
                )

            def historicalNewsEnd(self, requestId: int, hasMore: bool) -> None:  # noqa: N802
                self.historical_rows[requestId].append({"_hasMore": bool(hasMore)})
                self.historical_events.setdefault(requestId, threading.Event()).set()

            def newsArticle(self, requestId: int, articleType: int, articleText: str) -> None:  # noqa: N802
                self.article_results[requestId] = (articleType, articleText)
                self.article_events.setdefault(requestId, threading.Event()).set()

            def error(self, reqId: int, *args: Any) -> None:
                error_code, error_string = _parse_error_args(args)
                if error_code is None:
                    return
                if error_code not in {2104, 2106, 2107, 2108, 2158}:
                    self.errors.append(f"{reqId}:{error_code}:{error_string}")
                for events in (self.contract_events, self.historical_events, self.article_events):
                    if reqId in events and error_code not in {2104, 2106, 2107, 2108, 2158}:
                        events[reqId].set()

        app = App()
        app.connect(
            self.settings.ibkr_tws_host,
            self.settings.ibkr_tws_port,
            clientId=self.settings.ibkr_news_client_id,
        )
        thread = threading.Thread(target=app.run, name="ibkr-news", daemon=True)
        thread.start()
        if not app.connected.wait(self.settings.ibkr_tws_timeout_seconds):
            app.disconnect()
            raise RuntimeError("IBKR news connection timed out")
        app.reqNewsProviders()
        if not app.providers_done.wait(self.settings.ibkr_tws_timeout_seconds):
            app.disconnect()
            raise RuntimeError("IBKR news provider discovery timed out")
        self._app, self._thread = app, thread
        return app

    def _conid(self, app: Any, ticker: str) -> int:
        if ticker in self._conids:
            return self._conids[ticker]
        from ibapi.contract import Contract  # type: ignore[import-untyped]

        request_id = self._request_id()
        event = app.contract_events.setdefault(request_id, threading.Event())
        contract = Contract()
        contract.symbol, contract.secType, contract.exchange, contract.currency = (
            ticker,
            "STK",
            "SMART",
            "USD",
        )
        app.reqContractDetails(request_id, contract)
        if not event.wait(self.settings.ibkr_tws_timeout_seconds):
            raise RuntimeError(f"IBKR contract lookup timed out for {ticker}")
        conid = app.contract_results.get(request_id)
        if conid is None:
            raise RuntimeError(f"IBKR contract not found for {ticker}")
        self._conids[ticker] = conid
        return int(conid)

    def _subscribe(self, app: Any, ticker: str) -> bool:
        if ticker in self._subscriptions:
            return False
        from ibapi.contract import Contract

        conid = self._conid(app, ticker)
        request_id = self._request_id()
        contract = Contract()
        contract.conId, contract.symbol, contract.secType = conid, ticker, "STK"
        contract.exchange, contract.currency = "SMART", "USD"
        codes = "+".join(code for code, _ in app.providers)
        generic = "mdoff,292" + (f":{codes}" if codes else "")
        self._subscriptions[ticker] = request_id
        app.reqMktData(request_id, contract, generic, False, False, [])
        return True

    def _article(self, app: Any, provider: str, article_id: str) -> tuple[int | None, str | None]:
        request_id = self._request_id()
        event = app.article_events.setdefault(request_id, threading.Event())
        app.reqNewsArticle(request_id, provider, article_id, [])
        if not event.wait(min(self.settings.ibkr_tws_timeout_seconds, 5)):
            app.article_events.pop(request_id, None)
            return None, None
        result = cast(
            tuple[int | None, str | None], app.article_results.get(request_id, (None, None))
        )
        app.article_events.pop(request_id, None)
        app.article_results.pop(request_id, None)
        return result

    def poll(
        self, ticker: str, since: datetime, until: datetime
    ) -> tuple[list[JsonObject], JsonObject]:
        with self._lock:
            app = self._ensure()
            subscription_created = self._subscribe(app, ticker)
            transport = "tick_292"
            has_more = False
            if subscription_created:
                request_id = self._request_id()
                event = app.historical_events.setdefault(request_id, threading.Event())
                providers = "+".join(code for code, _ in app.providers)
                if not providers:
                    raise RuntimeError("IBKR returned no subscribed news providers")
                app.reqHistoricalNews(
                    request_id,
                    self._conid(app, ticker),
                    providers,
                    until.astimezone(UTC).strftime("%Y%m%d %H:%M:%S UTC"),
                    since.astimezone(UTC).strftime("%Y%m%d %H:%M:%S UTC"),
                    300,
                    [],
                )
                if not event.wait(self.settings.ibkr_tws_timeout_seconds):
                    raise RuntimeError("IBKR historical news request timed out")
                raw = app.historical_rows.pop(request_id, [])
                app.historical_events.pop(request_id, None)
                has_more = any(bool(item.get("_hasMore")) for item in raw)
                with self._stream_lock:
                    self._stream[ticker].extend(item for item in raw if "articleId" in item)
                transport = "historical_fallback"
            unique: dict[tuple[str, str], JsonObject] = {}
            with self._stream_lock:
                queued = list(self._stream[ticker])
            for row in queued:
                if "articleId" in row:
                    unique[(str(row.get("providerCode")), str(row.get("articleId")))] = row
            eligible = [
                row
                for row in unique.values()
                if (stamp := _published(row.get("time"))) is not None
                and since <= stamp <= until + timedelta(minutes=5)
            ]
            eligible.sort(key=lambda row: _published(row.get("time")) or since, reverse=True)
            limit = self.settings.ibkr_news_max_articles_per_poll
            rows = eligible[:limit]
            emitted_keys = {
                (str(row.get("providerCode")), str(row.get("articleId"))) for row in rows
            }
            with self._stream_lock:
                self._stream[ticker] = [
                    row
                    for row in self._stream[ticker]
                    if (
                        str(row.get("providerCode")),
                        str(row.get("articleId")),
                    )
                    not in emitted_keys
                    and (stamp := _published(row.get("time"))) is not None
                    and stamp >= since
                ]
            with ThreadPoolExecutor(
                max_workers=self.settings.ibkr_news_article_concurrency,
                thread_name_prefix="ibkr-news-article",
            ) as executor:
                articles = list(
                    executor.map(
                        lambda row: self._article(
                            app, str(row["providerCode"]), str(row["articleId"])
                        ),
                        rows,
                    )
                )
            for row, (article_type, body) in zip(rows, articles, strict=True):
                row["articleType"] = article_type
                if body and article_type == 0:
                    row["articleText"] = body
            with self._stream_lock:
                pending_count = len(self._stream[ticker])
            return rows, {
                "transport": transport,
                "provider_codes": [code for code, _ in app.providers],
                "provider_names": {code: name for code, name in app.providers},
                "has_more": has_more,
                "pending_count": pending_count,
                "errors": app.errors[-10:],
            }

    def close(self) -> None:
        with self._lock:
            if self._app is not None:
                for request_id in self._subscriptions.values():
                    self._app.cancelMktData(request_id)
                self._app.disconnect()
            self._app = None
            self._subscriptions.clear()


def _parse_error_args(args: tuple[Any, ...]) -> tuple[int | None, str]:
    """Accept pre-10.33 and current 10.49 EWrapper.error callback layouts."""

    if len(args) >= 3 and not isinstance(args[1], str):
        try:
            return int(args[1]), str(args[2])
        except (TypeError, ValueError):
            return None, ""
    if len(args) >= 2:
        try:
            return int(args[0]), str(args[1])
        except (TypeError, ValueError):
            return None, ""
    return None, ""


def _published(value: object) -> datetime | None:
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = float(str(value))
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    for pattern in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text.removesuffix(" UTC").strip(), pattern).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


class IbkrNewsAdapter:
    def __init__(
        self, settings: DoxAgentSettings, gateway: IbkrNewsGatewayProtocol | None = None
    ) -> None:
        self.gateway = gateway or IbkrNewsGateway(settings)

    async def poll(self, context: PollContext) -> PollResult:
        since = context.requested_at - timedelta(hours=24)
        async with context.request_permit():
            rows, metadata = await asyncio.to_thread(
                self.gateway.poll, context.ticker, since, context.requested_at
            )
        providers_value = metadata.get("provider_names")
        providers = (
            cast(dict[str, object], providers_value) if isinstance(providers_value, dict) else {}
        )
        messages: list[RawMessageInput] = []
        for row in rows:
            published = _published(row.get("time"))
            if (
                published is None
                or published < since
                or published > context.requested_at + timedelta(minutes=5)
            ):
                continue
            article_id = str(row.get("articleId") or "")
            provider_code = str(row.get("providerCode") or "IBKR")
            raw_body = str(row.get("articleText") or "").strip()
            link_match = re.search(
                r'https?://[^\s"<>]+', raw_body or str(row.get("extraData") or "")
            )
            body = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw_body))).strip() or None
            url = (
                link_match.group(0).rstrip(".,)")
                if link_match
                else "https://www.interactivebrokers.com/en/trading/providers.php"
            )
            publisher = str(providers.get(provider_code) or provider_code)
            messages.append(
                RawMessageInput(
                    external_id=f"{provider_code}:{article_id}",
                    source_item_key=f"{provider_code}:{article_id}",
                    title=(
                        re.sub(r"^(?:\{[^}]+\}\s*)+", "", str(row.get("headline") or "")).strip()
                        or None
                    ),
                    body=body,
                    summary=None,
                    source=publisher,
                    publisher_name=publisher,
                    url=url,
                    published_at=published,
                    raw_payload=row,
                    metadata={
                        "provider": "ibkr",
                        "provider_code": provider_code,
                        "article_id": article_id,
                        "canonical_url_available": bool(link_match),
                        "transport": row.get("transport"),
                    },
                )
            )
        return PollResult(
            messages=messages,
            window_coverage="PARTIAL"
            if metadata.get("has_more") or metadata.get("pending_count") or metadata.get("errors")
            else "COMPLETE",
            acquisition_metadata=metadata,
        )

    def close(self) -> None:
        self.gateway.close()


__all__ = [
    "IbkrNewsAdapter",
    "IbkrNewsGateway",
    "IbkrNewsGatewayProtocol",
    "_parse_error_args",
]
