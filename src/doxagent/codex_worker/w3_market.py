"""Asynchronous Case market preparation; tool reads never wait for IBKR."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from doxagent.persistent_runtime_v2.w3_research_store import compact, sha

NY = ZoneInfo("America/New_York")


def market_active(now):
    """US stock session envelope; actual coverage still comes from provider bars."""
    from exchange_calendars import get_calendar

    calendar = get_calendar("XNYS")
    local = now.astimezone(NY)
    day = local.date() + (timedelta(days=1) if local.hour >= 20 else timedelta())
    return calendar.is_session(day.isoformat())


def venues_for_window(end, hours):
    venues = set()
    for step in range(int(hours * 12) + 1):
        hour = (end - timedelta(minutes=step * 5)).astimezone(NY).hour
        venues.add("SMART" if 4 <= hour < 20 else "OVERNIGHT")
    return sorted(venues)


def offline_market_view(evidence, cutoff_at, ticker):
    """Keep dataset granularity; never invent five-minute bars from ten-minute data."""
    import json

    cutoff = datetime.fromisoformat(cutoff_at)
    result = dict(
        mode="OFFLINE_REPLAY",
        ticker=ticker,
        observed_at=cutoff_at,
        use="TIMELINESS_AND_COMPLETION_ONLY",
        series={},
        sources=[],
    )
    for name, text in evidence.items():
        if not name.endswith(".json") or "price" not in name.lower():
            continue
        try:
            source = json.loads(text)
        except ValueError:
            continue
        if not isinstance(source, dict) or not isinstance(source.get("bars"), list):
            continue
        daily = source.get("bar_size") == "1 day"
        rows = []
        for bar in source["bars"]:
            end = stamp(bar.get("bar_end_at") or bar["date"])
            at = stamp(bar.get("bar_start_at") or bar["date"])
            if end > cutoff or daily and not bar.get("session_finished_by_cutoff", False):
                continue
            if not daily and at < cutoff - timedelta(hours=4):
                continue
            rows.append(
                [at.isoformat(), *[bar.get(k) for k in ("open", "high", "low", "close", "volume")]]
            )
        result["series"]["daily" if daily else "fine"] = dict(
            status="READY" if rows else "UNAVAILABLE",
            bar_size=source.get("bar_size"),
            rows=rows[-5:] if daily else rows,
            source=name,
            availability=source.get("availability"),
            derivation=source.get("derivation"),
            error=source.get("error"),
            coverage="Frozen dataset bars only; missing intervals remain unknown",
        )
        result["sources"].append(name)
    result["series"].setdefault(
        "hourly",
        {
            "status": "UNAVAILABLE",
            "reason": "No hourly series in this frozen dataset; full originals remain readable",
        },
    )
    result["view_version"] = sha(compact(result))
    return result


def stamp(value, timezone=NY):
    if isinstance(value, (int, float)) or str(value).isdigit() and len(str(value)) > 8:
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).strip()
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        result = None
        for pattern in ("%Y%m%d %H:%M:%S", "%Y%m%d  %H:%M:%S", "%Y%m%d"):
            try:
                result = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if result is None:
            pieces = text.rsplit(" ", 1)
            if len(pieces) == 2:
                return stamp(pieces[0], ZoneInfo(pieces[1]))
            raise ValueError("unrecognized IBKR bar time") from None
    return (result.replace(tzinfo=timezone) if result.tzinfo is None else result).astimezone(UTC)


class MarketProvider:
    """One session, two request-ID isolated histories; no account/order methods."""

    def __init__(self, settings):
        self.settings = settings
        self.session = None
        self.contracts = {}
        self.lock = threading.RLock()
        self.active = 0
        self.failed = False
        self.next_connect_at = 0.0
        self.connect_failures = 0

    def history(self, ticker, bar, duration, venue="SMART", end=None):
        from dataclasses import replace

        from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession

        with self.lock:
            if not self.settings.ibkr_tws_enabled:
                raise RuntimeError("IBKR_TWS_DISABLED")
            app = getattr(self.session, "_app", None)
            if self.session is not None and (app is None or not app.isConnected()):
                self.failed = True
                if not self.active:
                    self.session.close()
                    self.session = None
            if self.failed and self.active:
                raise ConnectionError("IBKR_RECONNECT_PENDING")
            if self.session is None:
                if time.monotonic() < self.next_connect_at:
                    raise ConnectionError("IBKR reconnect backoff")
                config = replace(
                    IbkrTwsConfig.from_settings(self.settings),
                    client_id=self.settings.ibkr_tws_client_id + 901,
                )
                session = IbkrTwsSession(config)
                try:
                    session.connect()
                except Exception:
                    session.close()
                    self.connect_failures += 1
                    self.next_connect_at = time.monotonic() + min(
                        10, 2 ** (self.connect_failures - 1)
                    )
                    raise
                self.session, self.failed = session, False
                self.connect_failures = 0
            session = self.session
            if ticker not in self.contracts:
                self.contracts[ticker] = session.resolve_stock(ticker)[0]
            contract = self.contracts[ticker]
            self.active += 1
        try:
            return session.historical_bars(
                contract,
                duration=duration,
                bar_size=bar,
                use_rth=bar == "1 day",
                market_venue=venue,
                format_date=1 if bar == "1 day" else 2,
                end_datetime=(end or datetime.now(UTC)).strftime("%Y%m%d %H:%M:%S UTC"),
            )
        except Exception as exc:
            from doxagent.tools.providers.ibkr_tws import IbkrTwsConnectionError

            with self.lock:
                if isinstance(exc, (ConnectionError, OSError, IbkrTwsConnectionError)):
                    self.failed = True
                    self.next_connect_at = time.monotonic() + 1
            raise
        finally:
            with self.lock:
                self.active -= 1
                if self.failed and not self.active:
                    session.close()
                    self.session = None

    def close(self):
        with self.lock:
            if self.session:
                self.session.close()
                self.session = None


def compile_series(responses, *, now, hours=None, minutes=None, daily=False):
    rows, forming, sources = {}, [], []
    start = now - timedelta(hours=hours) if hours else None
    for venue, response in responses:
        sources.append(venue)
        for raw in response.get("bars", []):
            at = stamp(raw["date"])
            if daily:
                from exchange_calendars import get_calendar

                calendar = get_calendar("XNYS")
                day = at.astimezone(NY).date().isoformat()
                if not calendar.is_session(day):
                    continue
                end = calendar.session_close(day).to_pydatetime()
            else:
                end = at + timedelta(minutes=minutes)
            if start and at < start or at > now:
                continue
            row = [
                at.isoformat(),
                *[raw.get(k) for k in ("open", "high", "low", "close", "volume")],
                venue,
            ]
            # SMART covers daytime/extended; OVERNIGHT owns the night segment.
            hour = at.astimezone(NY).hour
            if not daily and venue == "SMART" and (hour >= 20 or hour < 4):
                continue
            if not daily and venue == "OVERNIGHT" and 4 <= hour < 20:
                continue
            if end > now:
                forming.append(row)
            else:
                rows[at.isoformat()] = row
    values = [rows[k] for k in sorted(rows)]
    if daily:
        values = values[-5:]
    missing = []
    if start and minutes:
        step = minutes * 60
        first = int(start.timestamp() // step) * step
        if first < start.timestamp():
            first += step
        end = int(now.timestamp() // step) * step
        absent = [
            t
            for t in range(first, end, step)
            if datetime.fromtimestamp(t, UTC).isoformat() not in rows
        ]
        for at in absent:
            if missing and missing[-1][1] == at:
                missing[-1][1] = at + step
            else:
                missing.append([at, at + step])
    complete = bool(values) and not missing and (not daily or len(values) == 5)
    return dict(
        status="READY" if complete else "PARTIAL",
        columns=["time", "o", "h", "l", "c", "volume", "venue"],
        rows=values,
        forming=forming,
        coverage=dict(
            requested_from=start.isoformat() if start else "5 completed RTH sessions",
            requested_to=now.isoformat(),
            actual_first=values[0][0] if values else None,
            actual_last=values[-1][0] if values else None,
            sources=sources,
            complete=complete,
            missing_intervals_utc=[
                [
                    datetime.fromtimestamp(a, UTC).isoformat(),
                    datetime.fromtimestamp(b, UTC).isoformat(),
                ]
                for a, b in missing
            ],
            volume_unit="PROVIDER_UNVERIFIED",
            gaps="No synthetic bars; session/provider gaps possible",
        ),
    )


class W3MarketPreparer:
    def __init__(self, store, settings, provider=None):
        self.store, self.settings = store, settings
        self.provider = provider or MarketProvider(settings)
        self.cases, self.fixed, self.tasks, self.fixed_locks = {}, {}, {}, {}
        self.gate = asyncio.Semaphore(2)
        self.closed = False
        self.loop = None
        self.all_tasks = set()
        self.requests = set()
        self.fixed_tasks = {}

    def warm_fixed(self, ticker):
        if self.closed or not getattr(self.settings, "ibkr_tws_enabled", False):
            return
        previous = self.fixed_tasks.get(ticker)
        if previous is not None and not previous.done():
            return
        task = asyncio.create_task(self.prepare_fixed(ticker))
        self.fixed_tasks[ticker] = task
        self.all_tasks.add(task)
        task.add_done_callback(self.all_tasks.discard)

    async def request(self, ticker, bar, duration, venue="SMART", end=None):
        if self.closed:
            raise RuntimeError("market preparer closed")

        async def perform():
            async with self.gate:
                return await asyncio.to_thread(
                    self.provider.history, ticker, bar, duration, venue, end
                )

        pending = asyncio.create_task(perform())
        self.requests.add(pending)

        def settled(task):
            self.requests.discard(task)
            if not task.cancelled():
                task.exception()  # Retrieve failures even when the Case was cancelled.

        pending.add_done_callback(settled)
        # Cancelling one Case cannot release the slot while its blocking request
        # is still using the shared connection.
        return await asyncio.shield(pending)

    def start(self, manifest):
        if self.closed or manifest.get("mode") == "OFFLINE_REPLAY":
            return
        self.loop = asyncio.get_running_loop()
        case = manifest["case_id"]
        if case in self.tasks and not self.tasks[case].done():
            return
        self.cases.setdefault(
            case,
            dict(
                manifest=manifest, fine=None, error=None, next_refresh_at=None, wake=asyncio.Event()
            ),
        )
        task = asyncio.create_task(self.run(case))
        self.tasks[case] = task
        self.all_tasks.add(task)
        task.add_done_callback(self.all_tasks.discard)

    async def fetch_routes(self, ticker, bar, duration, now):
        results, failures = [], []
        hours = 24 if bar == "1 hour" else float(duration.split()[0]) / 3600
        for venue in venues_for_window(now, hours):
            try:
                results.append((venue, await self.request(ticker, bar, duration, venue, now)))
            except Exception as exc:
                failures.append(dict(venue=venue, error=type(exc).__name__, message=str(exc)))
        return results, failures

    async def prepare_fixed(self, ticker):
        async with self.fixed_locks.setdefault(ticker, asyncio.Lock()):
            now = datetime.now(UTC)
            cached = self.fixed.get(ticker)
            if cached and cached["expires_at"] > now:
                return
            previous = cached or {}
            try:
                from exchange_calendars import get_calendar

                calendar = get_calendar("XNYS")
                session = calendar.date_to_session(
                    now.astimezone(NY).date().isoformat(), direction="previous"
                )
                if calendar.session_close(session).to_pydatetime() > now:
                    session = calendar.previous_session(session)
                daily_key = str(session)
                if (
                    previous.get("daily_key") == daily_key
                    and previous.get("daily", {}).get("status") == "READY"
                ):
                    daily_view = previous["daily"]
                else:
                    daily = await self.request(ticker, "1 day", "10 D", end=now)
                    daily_view = compile_series([("SMART", daily)], now=now, daily=True)
                hourly, errors = await self.fetch_routes(ticker, "1 hour", "2 D", now)
                self.fixed[ticker] = dict(
                    daily=daily_view,
                    daily_key=daily_key,
                    hourly=compile_series(hourly, now=now, hours=24, minutes=60),
                    errors=errors,
                    expires_at=now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                    observed_at=now.isoformat(),
                )
            except Exception as exc:
                self.fixed[ticker] = {
                    **previous,
                    "error": str(exc),
                    "expires_at": now + timedelta(seconds=30),
                }

    async def run(self, case):
        state = self.cases[case]
        manifest = state["manifest"]
        ticker = manifest["ticker"]
        fixed = asyncio.create_task(self.prepare_fixed(ticker))
        try:
            while not self.closed:
                now = datetime.now(UTC)
                deadline = manifest.get("deadline_at")
                if deadline and now >= datetime.fromisoformat(deadline):
                    break
                try:
                    responses, errors = await self.fetch_routes(ticker, "5 mins", "14400 S", now)
                    fine = compile_series(responses, now=now, hours=4, minutes=5)
                    if responses:
                        state.update(fine=fine, observed_at=now.isoformat(), error=errors or None)
                    else:
                        state["error"] = errors
                    hint = state.pop("supplement_requested", None)
                    if hint:
                        event = datetime.fromisoformat(hint)
                        result, errors = await self.fetch_routes(
                            ticker, "5 mins", "5400 S", event + timedelta(hours=1)
                        )
                        state["supplement"] = dict(
                            event_time=hint,
                            series=compile_series(
                                result, now=event + timedelta(hours=1), hours=1.5, minutes=5
                            ),
                            errors=errors,
                        )
                except Exception as exc:
                    state["error"] = {"layer": "history", "message": str(exc)}
                state["market_active"] = market_active(now)
                state["next_refresh_at"] = (
                    datetime.now(UTC) + timedelta(seconds=self.settings.w3_market_refresh_seconds)
                ).isoformat()
                try:
                    await asyncio.wait_for(
                        state["wake"].wait(), self.settings.w3_market_refresh_seconds
                    )
                    state["wake"].clear()
                except TimeoutError:
                    pass
                while not market_active(datetime.now(UTC)) and not self.closed:
                    if deadline and datetime.now(UTC) >= datetime.fromisoformat(deadline):
                        return
                    state["market_active"] = False
                    try:
                        await asyncio.wait_for(
                            state["wake"].wait(), self.settings.w3_market_refresh_seconds
                        )
                        state["wake"].clear()
                        break
                    except TimeoutError:
                        pass
                if ticker in self.fixed and self.fixed[ticker]["expires_at"] <= datetime.now(UTC):
                    await self.prepare_fixed(ticker)
        finally:
            await asyncio.gather(fixed, return_exceptions=True)
            if self.tasks.get(case) is asyncio.current_task():
                self.tasks.pop(case, None)
                self.cases.pop(case, None)

    def stop(self, case):
        task = self.tasks.pop(case, None)
        if task:
            task.cancel()
        self.cases.pop(case, None)

    def view(self, manifest, args):
        if manifest.get("mode") == "OFFLINE_REPLAY":
            return manifest.get("market_view") or {
                "status": "UNAVAILABLE",
                "mode": "OFFLINE_REPLAY",
                "evidence": list(manifest.get("evidence", {})),
                "reason": "Use frozen price evidence through read; no broker connection",
            }
        state = self.cases.get(manifest["case_id"], {})
        fixed = self.fixed.get(manifest["ticker"], {})
        now = datetime.now(UTC)
        event = args.get("event_time")
        if event:
            event_at = datetime.fromisoformat(event)
            if event_at.tzinfo is None or event_at > now:
                raise ValueError("event_time must be an aware, non-future timestamp")
            if event_at < now - timedelta(hours=4) and state:
                if state.get("supplement", {}).get("event_time") != event:
                    state["supplement_requested"] = event
                    self.loop.call_soon_threadsafe(state["wake"].set)
        view = dict(
            ticker=manifest["ticker"],
            timezone="America/New_York",
            use="TIMELINESS_AND_COMPLETION_ONLY",
            observed_at=state.get("observed_at"),
            series={
                k: fixed.get(
                    k,
                    {
                        "status": "UNAVAILABLE" if fixed.get("error") else "PREPARING",
                        "error": fixed.get("error"),
                    },
                )
                for k in ("daily", "hourly")
            }
            | {
                "fine": state.get("fine")
                or {"status": "UNAVAILABLE" if state.get("error") or not state else "PREPARING"}
            },
            refresh=dict(
                next_refresh_at=state.get("next_refresh_at"), last_error=state.get("error")
            ),
            supplement=state.get("supplement")
            or {"status": "PREPARING" if event else "NOT_REQUESTED"},
        )
        observed = state.get("observed_at")
        active = market_active(now)
        view["refresh"]["market_active"] = active
        view["refresh"]["stale"] = bool(
            active and observed and (now - datetime.fromisoformat(observed)).total_seconds() > 90
        )
        if not active:
            view["refresh"]["state"] = "MARKET_CLOSED"
        # Poll completion times change every 30 seconds even if no bar changes.
        # Compare business data/health, while each full read keeps its exact times.
        stable_series = {
            key: {k: v for k, v in series.items() if k != "coverage"}
            | {
                "coverage": {
                    k: v
                    for k, v in series.get("coverage", {}).items()
                    if k not in {"requested_from", "requested_to"}
                }
            }
            for key, series in view["series"].items()
        }
        identity = sha(
            compact(
                {
                    "ticker": view["ticker"],
                    "series": stable_series,
                    "supplement": view["supplement"],
                }
                | {
                    "health": {
                        k: view["refresh"].get(k) for k in ("stale", "market_active", "last_error")
                    }
                }
            )
        )
        if args.get("since_version") == identity:
            return dict(view_version=identity, unchanged=True, refresh=view["refresh"])
        return {**view, "view_version": identity}

    async def close(self):
        self.closed = True
        tasks = list(self.all_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self.requests), return_exceptions=True)
        await asyncio.to_thread(self.provider.close)
