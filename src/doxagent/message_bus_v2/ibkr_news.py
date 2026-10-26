"""Long-lived IBKR news session with streaming tick 292 and historical fallback."""

from __future__ import annotations

import asyncio
import re
import threading
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any, Protocol, cast

from doxagent.message_bus_v2.schema import JsonObject, PollContext, PollResult, RawMessageInput, utc_now
from doxagent.settings import DoxAgentSettings
from .collection_override import CollectionStopped, REQUEST_GATE


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
        self.on_news = None
        self._articles = OrderedDict()
        self._delivered = defaultdict(dict)

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
                        owner._delivered[ticker].pop(providerCode + ':' + articleId, None)
                        owner._articles.pop((providerCode, articleId), None)
                        owner._stream[ticker].append(
                            {
                                "time": timeStamp,
                                "providerCode": providerCode,
                                "articleId": articleId,
                                "headline": headline,
                                "extraData": extraData,
                                "transport": "tick_292",
                                "callback_at": datetime.now(UTC).isoformat(),
                            }
                        )

                    if owner.on_news is not None:
                        owner.on_news(ticker)

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
        with self._stream_lock:
            cached = self._articles.get((provider, article_id))
        if cached and datetime.now(UTC) - cached[0] < timedelta(seconds=60):
            return cached[1]
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
        if result[0] == 0 and result[1]:
            with self._stream_lock:
                self._articles[(provider, article_id)] = (datetime.now(UTC), result)
                while len(self._articles) > 512:
                    self._articles.popitem(last=False)
        return result

    def poll(
        self, ticker: str, since: datetime, until: datetime
    ) -> tuple[list[JsonObject], JsonObject]:
        with self._lock:
            app = self._ensure()
            self._subscribe(app, ticker)
        transport = "tick_292"
        has_more = False
        error_start = len(app.errors)
        # Periodic SDK reconciliation also recovers callback loss after restart.
        request_id = self._request_id()
        event = app.historical_events.setdefault(request_id, threading.Event())
        providers = "+".join(code for code, _ in app.providers)
        if not providers:
            raise RuntimeError("IBKR returned no subscribed news providers")
        from .poll_trace import request_started

        trace = request_started("SDK", f"ibkr://news/{ticker}", "ibkr_sdk")
        if trace is not None:
            trace["sdk_request_id"] = request_id
        app.reqHistoricalNews(
            request_id,
            self._conid(app, ticker),
            providers,
            since.astimezone(UTC).strftime("%Y%m%d %H:%M:%S UTC"),
            until.astimezone(UTC).strftime("%Y%m%d %H:%M:%S UTC"),
            300,
            [],
        )
        if not event.wait(self.settings.ibkr_tws_timeout_seconds):
            raise RuntimeError("IBKR historical news request timed out")
        if trace is not None:
            trace["response_received_at"] = datetime.now(UTC).isoformat()
            trace["status"] = "CALLBACK_COMPLETE"
        raw = app.historical_rows.pop(request_id, [])
        app.historical_events.pop(request_id, None)
        if not any('_hasMore' in item for item in raw):
            if trace is not None:
                trace['status'] = 'SDK_ERROR'
            raise RuntimeError('IBKR historical news did not reach historicalNewsEnd')
        has_more = any(bool(item.get("_hasMore")) for item in raw)
        with self._stream_lock:
            self._stream[ticker].extend(item for item in raw if "articleId" in item)
        transport = "historical_fallback"
        unique: dict[tuple[str, str], JsonObject] = {}
        with self._stream_lock:
            queued = list(self._stream[ticker])
        for row in queued:
            if "articleId" in row:
                key = str(row.get('providerCode')) + ':' + str(row.get('articleId'))
                delivered = self._delivered[ticker].get(key)
                if not delivered or (until - datetime.fromisoformat(delivered)).total_seconds() >= 1800:
                    unique[(str(row.get("providerCode")), str(row.get("articleId")))] = row
        eligible = [
            row
            for row in unique.values()
            if (stamp := _published(row.get("time"))) is None
            or since <= stamp <= until + timedelta(minutes=5)
        ]
        eligible.sort(key=lambda row: _published(row.get("time")) or since, reverse=True)
        limit = self.settings.ibkr_news_max_articles_per_poll
        rows = eligible[:limit]
        emitted_keys = {(str(row.get("providerCode")), str(row.get("articleId"))) for row in rows}
        with self._stream_lock:
            self._stream[ticker] = [
                row
                for row in self._stream[ticker]
                if (
                    str(row.get("providerCode")),
                    str(row.get("articleId")),
                )
                not in emitted_keys
                and (str(row.get('providerCode')) + ':' + str(row.get('articleId'))) not in self._delivered[ticker]
                and ((stamp := _published(row.get("time"))) is None or stamp >= since)
            ]
        with self._stream_lock:
            pending_count = len(self._stream[ticker])
        return rows, {
            "transport": transport,
            "provider_codes": [code for code, _ in app.providers],
            "provider_names": {code: name for code, name in app.providers},
            "has_more": has_more,
            "pending_count": pending_count,
            "errors": app.errors[error_start:][-10:],
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
    for pattern in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
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
        self._contexts = {}
        self._callbacks = {}
        self._active_bindings = None
        self._article_slots = asyncio.Semaphore(getattr(settings, 'ibkr_news_article_concurrency', 4))
        if isinstance(self.gateway, IbkrNewsGateway):
            self.gateway.on_news = self._callback

    async def poll(self, context: PollContext) -> PollResult:
        self._contexts[context.ticker] = (asyncio.get_running_loop(), context, REQUEST_GATE.get())
        since = context.requested_at - timedelta(hours=24)
        prior = context.checkpoint.get("last_sdk_until")
        if isinstance(self.gateway, IbkrNewsGateway):
            self.gateway._delivered[context.ticker].update(context.checkpoint.get('recent_sdk_articles', {}))
        if prior:
            since = max(since, datetime.fromisoformat(prior) - timedelta(minutes=5))
        async with context.request_permit():
            work = asyncio.create_task(
                asyncio.to_thread(self.gateway.poll, context.ticker, since, context.requested_at)
            )
            try:
                rows, metadata = await asyncio.shield(work)
            except asyncio.CancelledError:
                # A cancelled to_thread waiter does not stop its socket owner.
                # Drain before AdapterRegistry.close disconnects the gateway;
                # otherwise a queued poll may reconnect after close and retain
                # a non-daemon IBAPI reader during interpreter shutdown.
                await asyncio.gather(work, return_exceptions=True)
                raise
        if isinstance(self.gateway, IbkrNewsGateway):
            async def complete(row):
                async with self._article_slots:
                    kind, body = await asyncio.to_thread(self.gateway._article, self.gateway._app,
                        str(row['providerCode']), str(row['articleId']))
                return {**row, 'articleType': kind, **({'articleText': body} if body and kind == 0 else {})}
            rows = await asyncio.gather(*(complete(row) for row in rows))
        result = self._result(context, rows, metadata)
        if isinstance(self.gateway, IbkrNewsGateway) and context.on_page:
            recent = {**self.gateway._delivered[context.ticker],
                      **{str(row['providerCode']) + ':' + str(row['articleId']): utc_now().isoformat() for row in rows}}
            checkpoint = {**result.next_checkpoint, 'recent_sdk_articles': dict(list(recent.items())[-1000:])}
            from .poll_trace import REQUESTS
            receipts = [r for r in REQUESTS.get() or [] if r.get('transport') == 'ibkr_sdk' and r.get('response_received_at')]
            received = datetime.fromisoformat(receipts[-1]['response_received_at']) if receipts else context.requested_at
            await context.on_page(result.messages, observed_at=received, checkpoint=checkpoint)
            self.gateway._delivered[context.ticker] = checkpoint['recent_sdk_articles']
            result = result.model_copy(update={'next_checkpoint': checkpoint})
        return result

    def _result(self, context, rows, metadata):
        since = context.requested_at - timedelta(hours=24)
        providers_value = metadata.get("provider_names")
        providers = (
            cast(dict[str, object], providers_value) if isinstance(providers_value, dict) else {}
        )
        messages: list[RawMessageInput] = []
        for row in rows:
            published = _published(row.get("time"))
            if published is not None and (
                published < since or published > context.requested_at + timedelta(minutes=5)
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
                    published_at=published or context.requested_at,
                    publication_time_basis="EXACT" if published else "UNKNOWN_FIRST_SEEN",
                    raw_payload=row,
                    metadata={
                        "provider": "ibkr",
                        "identity_evidence": {
                            "id_kind": "provider_article",
                            "url_kind": "article" if link_match else "generic",
                        },
                        "provider_code": provider_code,
                        "article_id": article_id,
                        "canonical_url_available": bool(link_match),
                        "transport": row.get("transport"),
                        "acquisition_received_at": row.get("callback_at"),
                    },
                )
            )
        return PollResult(
            messages=messages,
            next_checkpoint=(
                {"last_sdk_until": context.requested_at.isoformat()}
                if not metadata.get("has_more") and not metadata.get("errors")
                else context.checkpoint
            ),
            window_coverage="PARTIAL"
            if metadata.get("has_more") or metadata.get("pending_count") or metadata.get("errors")
            else "COMPLETE",
            acquisition_metadata=metadata,
        )

    def _callback(self, ticker):
        active = self._contexts.get(ticker)
        if active is None or active[1].on_page is None:
            return
        loop, context, gate = active

        def wake():
            if self._active_bindings is not None and context.binding.binding_id not in self._active_bindings:
                return
            try:
                if gate is not None:
                    gate.check()
            except CollectionStopped:
                return
            if ticker not in self._callbacks or self._callbacks[ticker].done():
                self._callbacks[ticker] = asyncio.create_task(self._deliver_ticks(context, gate))

        if not loop.is_closed():
            loop.call_soon_threadsafe(wake)

    async def _deliver_ticks(self, context, gate=None):
        from .schema import utc_now

        # Socket callbacks retain the pending rows until durable capture succeeds.
        with self.gateway._stream_lock:
            rows = [dict(row) for row in self.gateway._stream[context.ticker]]
        if not rows:
            return
        app = self.gateway._app

        async def complete(row):
            async with self._article_slots:
                if gate is not None:
                    gate.check()
                kind, body = await asyncio.to_thread(
                    self.gateway._article, app, str(row["providerCode"]), str(row["articleId"])
                )
            return {
                **row,
                "articleType": kind,
                **({"articleText": body} if body and kind == 0 else {}),
            }

        try:
            rows = await asyncio.gather(*(complete(row) for row in rows))
            received = min(
                datetime.fromisoformat(r["callback_at"]) for r in rows if r.get("callback_at")
            )
            current = context.model_copy(update={"requested_at": utc_now()})
            result = self._result(current, rows, {"provider_names": dict(app.providers)})
            await context.on_page(result.messages, observed_at=received, bootstrap=False)
            for row in rows:
                self.gateway._delivered[context.ticker][str(row['providerCode']) + ':' + str(row['articleId'])] = utc_now().isoformat()
            self.gateway._delivered[context.ticker] = dict(list(self.gateway._delivered[context.ticker].items())[-1000:])
            keys = {(r["providerCode"], r["articleId"]) for r in rows}
            with self.gateway._stream_lock:
                self.gateway._stream[context.ticker] = [
                    r
                    for r in self.gateway._stream[context.ticker]
                    if (r.get("providerCode"), r.get("articleId")) not in keys
                ]
        except CollectionStopped:
            return
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "IBKR callback capture failed ticker=%s; retained for reconciliation",
                context.ticker,
            )

    async def close(self) -> None:
        if isinstance(self.gateway, IbkrNewsGateway):
            self.gateway.on_news = None
        self._contexts.clear()
        await asyncio.gather(*self._callbacks.values(), return_exceptions=True)
        self.gateway.close()


__all__ = [
    "IbkrNewsAdapter",
    "IbkrNewsGateway",
    "IbkrNewsGatewayProtocol",
    "_parse_error_args",
]
