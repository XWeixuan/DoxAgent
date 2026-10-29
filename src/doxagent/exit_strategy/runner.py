"""One lightweight five-second task hosted by the sole order writer."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import StopState, observe
from .market import PriceWindow, quote_quality
from .repository import ExitRepository

log = logging.getLogger(__name__)


def commission(quantity, price):
    return min(max(abs(quantity) * 0.005, 1.0), abs(quantity) * price * 0.01) if quantity else 0.0


def net_pnl(value, prices, policy, now):
    zone = ZoneInfo("America/New_York")
    first = datetime.fromisoformat(value["first_fill_at"])
    nights = max(0, (now.astimezone(zone).date() - first.astimezone(zone).date()).days)
    pnl = 0.0
    for leg, price in zip(value["legs"], prices, strict=True):
        pnl += (
            leg["cash"] + leg["quantity"] * price - leg["fees"] - commission(leg["quantity"], price)
        )
        rate = float(policy.borrow_rate if leg["sign"] < 0 else policy.financing_rate)
        pnl -= leg["entry_notional"] * rate * nights / 360
    return pnl


class ObservationLog:
    """Only sampled observations; no per-tick DB/event amplification."""

    def __init__(self, root):
        self.root = Path(root)
        self.last_prune = 0.0

    def write(self, records, now):
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / (now.strftime("observations-%Y%m%d-%H") + ".jsonl.gz")
        with gzip.open(target, "at", encoding="utf8") as stream:
            for r in records:
                stream.write(json.dumps(r, allow_nan=False, separators=(",", ":")) + "\n")
        if time.monotonic() - self.last_prune > 3600:
            files = sorted(self.root.glob("observations-????????-??.jsonl.gz"))
            size = sum(p.stat().st_size for p in files)
            for p in files:
                stat = p.stat()
                if p != target and (
                    size > 512 * 1024 * 1024 or now.timestamp() - stat.st_mtime > 7 * 86400
                ):
                    p.unlink()
                    size -= stat.st_size
            self.last_prune = time.monotonic()


class ExitRunner:
    def __init__(self, executor):
        self.executor = executor
        self.repository = ExitRepository(executor.journal)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exit-state")
        self.network = asyncio.Semaphore(2)
        self.network_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="exit-quote")
        self.states = {}
        self.windows = {}
        self.policies = {}
        self.contexts = {}
        self.preparing = {}
        self.releasing = set()
        self.retry_after = {}
        self.seeded = set()
        self.seed_bars = {}
        self.seed_cache = {}
        self.seed_budget = deque()
        self.pending_requests = {}
        self.evidence_windows = {}
        self.last_publish = {}
        self.last_heartbeat = 0.0
        self.audit = ObservationLog(executor.journal.path.parent / "exit_strategy" / "observations")
        self.last_tick_seconds = 0.0

    async def io(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    async def net_io(self, fn, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self.network_pool, partial(fn, *args, **kwargs)
        )

    async def release(self, broker, contract, key):
        try:
            await self.net_io(broker.release_quote, contract, key)
        except Exception:
            log.exception("exit_quote_release_deferred owner=%s", key)

    async def prepare(self, value, leg, venue, key, refresh=False):
        try:
            async with self.network:
                profile = await self.net_io(
                    self.executor.repository.profile, value["profile_revision"]
                )
                broker = self.executor.broker(
                    profile.quote_profile_revision or value["profile_revision"]
                )
                self.executor.recovery.check(broker.profile.expected_account_id)
                contract = await self.net_io(broker.contract, leg["ticker"], venue)
                if contract["con_id"] != leg["con_id"]:
                    raise ValueError("QUOTE_CONTRACT_MISMATCH")
                old = self.contexts.get(key)
                if old and (old[0] is not broker or old[1]["exchange"] != venue):
                    await self.net_io(old[0].release_quote, old[1], key)
                await self.net_io(broker.subscribe_quote, contract, key, refresh=refresh)
                self.contexts[key] = (broker, contract)
                if value["position_id"] not in self.seeded:
                    first = datetime.fromisoformat(value["first_fill_at"])
                    end = first.replace(second=0, microsecond=0).astimezone(UTC)
                    try:
                        side = "BID" if leg["quantity"] > 0 else "ASK"
                        size = "1 min" if len(value["legs"]) == 1 else "5 secs"
                        cache_key = (
                            id(broker),
                            contract["con_id"],
                            venue,
                            side,
                            end.timestamp(),
                            size,
                        )
                        bars = self.seed_cache.get(cache_key)
                        if bars is None:
                            cutoff = time.monotonic() - 600
                            while self.seed_budget and self.seed_budget[0] < cutoff:
                                self.seed_budget.popleft()
                            if len(self.seed_budget) >= 48:
                                return
                            self.seed_budget.append(time.monotonic())
                            bars = await self.net_io(
                                broker.atr_seed,
                                contract,
                                side,
                                end.strftime("%Y%m%d %H:%M:%S UTC"),
                                size,
                            )
                            self.seed_cache[cache_key] = bars
                            while len(self.seed_cache) > 40:
                                self.seed_cache.pop(next(iter(self.seed_cache)))
                        if len(value["legs"]) == 1:
                            self.windows[value["position_id"]].seed(bars, end.timestamp())
                            self.seeded.add(value["position_id"])
                        else:
                            seeds = self.seed_bars.setdefault(value["position_id"], {})
                            seeds[leg["lot_id"]] = {
                                int(b["date"]): float(b["close"])
                                for b in bars
                                if int(b["date"]) + 5 <= end.timestamp()
                            }
                            if len(seeds) == len(value["legs"]):
                                window = self.windows[value["position_id"]]
                                # Merge historical samples before live samples; no MFE is updated.
                                history = [
                                    [
                                        t + 5 - 1e-6,
                                        [seeds[leg["lot_id"]][t] for leg in value["legs"]],
                                    ]
                                    for t in sorted(
                                        set.intersection(*(set(x) for x in seeds.values()))
                                    )
                                ]
                                window.frames = (history + window.frames)[-720:]
                                window.invalidate()
                                self.seeded.add(value["position_id"])
                    except Exception as exc:
                        log.warning(
                            "exit_atr_seed_deferred position=%s error=%s", value["position_id"], exc
                        )
        except Exception as exc:
            log.warning(
                "exit_quote_prepare_deferred position=%s error=%s", value["position_id"], exc
            )

    def ensure(self, value, leg, venue, mono):
        key = value["position_id"] + ":" + leg["lot_id"]
        ctx = self.contexts.get(key)
        quote = ctx[0].quote_snapshot(ctx[1]) if ctx else None
        different = ctx and ctx[1]["exchange"] != venue
        stale = quote and any(
            mono - quote.get(s + "_received_mono", float("-inf")) > 15 for s in ("bid", "ask")
        )
        task = self.preparing.get(key)
        lost_stream = ctx and not quote and getattr(ctx[0], "healthy", False)
        if (not task or task.done()) and (lost_stream or mono >= self.retry_after.get(key, 0)):
            if (
                not ctx
                or not quote
                or different
                or stale
                or quote.get("error")
                or value["position_id"] not in self.seeded
            ):
                self.retry_after[key] = mono + 30
                self.preparing[key] = asyncio.create_task(
                    self.prepare(
                        value, leg, venue, key, refresh=bool(stale or quote and quote.get("error"))
                    )
                )
        return ctx, quote if not different else None

    async def tick(self, *, mono=None):
        began = time.monotonic()
        injected_clock = mono is not None
        mono = began if mono is None else mono
        now = self.executor.journal.clock()
        positions = await self.io(self.repository.positions)
        compute_began = time.perf_counter()
        updates, records = [], []
        active_ids = {v["position_id"] for v in positions}
        for value in positions:
            original = dict(value)
            try:
                ident = value["position_id"]
                if value.get("book_version") != self.states.get(ident, (None,))[0]:
                    self.states[ident] = (value.get("book_version"), StopState.restore(value))
                state = self.states[ident][1]
                window = self.windows.setdefault(ident, PriceWindow(value.get("price_window")))
                policy = self.policies.get(value["policy_revision"])
                if policy is None:
                    policy = await self.io(self.repository.policy, value["policy_revision"])
                    self.policies[value["policy_revision"]] = policy
                if not any(leg["quantity"] for leg in value["legs"]):
                    if not await self.io(self.repository.unsettled, value):
                        value.update(phase="CLOSED", publish=True, updated_at=now.isoformat())
                        value.pop("price_window", None)
                        updates.append(value)
                    continue
                reason = value.get("pending_reason")
                pending = self.pending_requests.get(ident)
                if pending and pending[0] == value.get("book_version"):
                    reason = "TRAILING_PROFIT"
                    value["evidence"] = pending[1]
                elif pending:
                    self.pending_requests.pop(ident, None)
                if any(
                    datetime.fromisoformat(leg["scheduled_exit"]) <= now for leg in value["legs"]
                ):
                    reason = "SESSION_CUTOFF"
                if value["phase"] == "EXIT_REQUESTED":
                    if mono - self.last_publish.get(ident, float("-inf")) >= 30:
                        value.update(publish=True, updated_at=now.isoformat())
                        updates.append(value)
                    continue
                if reason:
                    value.update(state.durable())
                    value.update(request_reason=reason, publish=True)
                    updates.append(value)
                    continue
                if value.get("basis_invalid") or value.get("paused"):
                    state.reset_window()
                    value.update(
                        data_health="STATE_GAP" if value.get("basis_invalid") else "PAUSED",
                        publish=True,
                        updated_at=now.isoformat(),
                    )
                    updates.append(value)
                    continue
                prices, quotes, generations = [], [], []
                health = "OK"
                venue = self.executor.sessions.venue(now)
                snapshots = [self.ensure(value, leg, venue, mono) for leg in value["legs"]]
                # A quote may arrive while the state DB is being read. Date this
                # observation AFTER the atomic cache copies, not at loop start.
                observed_mono = mono if injected_clock else time.monotonic()
                observed_now = now if injected_clock else self.executor.journal.clock()
                observed_utc = observed_now.timestamp()
                for leg, (ctx, quote) in zip(value["legs"], snapshots, strict=True):
                    quality = quote_quality(quote, observed_mono, policy)
                    if ctx and self.executor.sessions.classify(observed_now, ctx[1]) == "CLOSED":
                        quality = "CLOSED_SESSION"
                    if quality != "OK":
                        health = quality
                    quotes.append(quote)
                    generations.append(quote.get("generation") if quote else None)
                    prices.append(
                        float(quote["bid" if leg["quantity"] > 0 else "ask"])
                        if quality == "OK"
                        else 0.0
                    )
                valid = health == "OK"
                if valid:
                    window.add(observed_utc, prices)
                atr = window.atr([leg["quantity"] for leg in value["legs"]], observed_utc, policy)
                pnl = net_pnl(value, prices, policy, observed_now) if valid else None
                triggered = observe(
                    state,
                    policy,
                    utc=observed_utc,
                    mono=observed_mono,
                    pnl=pnl,
                    notional=value["notional"],
                    atr=atr,
                    valid=valid,
                    generation=tuple(tuple(g) if isinstance(g, list) else g for g in generations),
                )
                previous_phase, previous_health = value["phase"], value.get("data_health")
                value.update(state.durable())
                value.update(
                    phase="ARMED" if state.stop_net is not None else "TRACKING",
                    data_health=health if not valid else "OK" if atr is not None else "WARMING_ATR",
                    pnl=pnl,
                    atr=atr,
                    price_window=window.durable(),
                    updated_at=observed_now.isoformat(),
                    confirmation=[breach if ok else None for _, ok, breach in state.confirmation],
                    valid_observations=value.get("valid_observations", 0) + int(valid),
                    invalid_observations=value.get("invalid_observations", 0) + int(not valid),
                )
                value["publish"] = (
                    previous_phase != value["phase"]
                    or previous_health != value["data_health"]
                    or mono - self.last_publish.get(ident, float("-inf")) >= 30
                )
                record = dict(
                    position_id=ident,
                    utc=observed_utc,
                    pnl=pnl,
                    valid=valid,
                    health=health,
                    mfe=state.mfe,
                    atr=atr,
                    stop_net=state.stop_net,
                    quotes=quotes,
                    confirmation=list(state.confirmation),
                )
                records.append(record)
                recent_evidence = self.evidence_windows.setdefault(ident, deque(maxlen=6))
                recent_evidence.append(record)
                if triggered:
                    proof = dict(
                        observations=list(recent_evidence),
                        policy_revision=value["policy_revision"],
                        notional=value["notional"],
                        mfe=state.mfe,
                        atr=atr,
                        stop_net=state.stop_net,
                    )
                    value.update(request_reason="TRAILING_PROFIT", evidence=proof, publish=True)
                    self.pending_requests[ident] = (value.get("book_version"), proof)
                updates.append(value)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ident = original["position_id"]
                if ident in self.states:
                    self.states[ident][1].reset_window()
                publish = (
                    original.get("data_health") != "STATE_GAP"
                    or mono - self.last_publish.get(ident, float("-inf")) >= 30
                )
                if publish:
                    log.warning("exit_position_deferred position=%s error=%s", ident, exc)
                original.update(
                    data_health="STATE_GAP",
                    last_error=str(exc),
                    publish=publish,
                    updated_at=now.isoformat(),
                )
                updates.append(original)
        self.last_compute_seconds = time.perf_counter() - compute_began
        persist_began = time.perf_counter()
        if updates:
            try:
                saved = await self.io(self.repository.save_batch, updates)
            except Exception:
                # Keep unsaved high-water state; retry with a fresh CAS version next tick.
                log.error("EXIT_REQUEST_PERSIST_FAILED pending=%s", list(self.pending_requests))
                raise
            for value in updates:
                ident = value["position_id"]
                if ident not in saved:
                    self.states.pop(ident, None)
                    self.windows.pop(ident, None)
                    self.pending_requests.pop(ident, None)
                elif value.get("publish"):
                    self.last_publish[ident] = mono
                    if value.get("exit_requested_at"):
                        self.pending_requests.pop(ident, None)
        self.last_persist_seconds = time.perf_counter() - persist_began
        wanted = {
            v["position_id"] + ":" + leg["lot_id"]
            for v in positions
            if v["phase"] != "CLOSED"
            for leg in v["legs"]
        }
        for key in list(self.contexts):
            if key not in wanted and (key not in self.preparing or self.preparing[key].done()):
                broker, contract = self.contexts.pop(key)
                task = asyncio.create_task(self.release(broker, contract, key))
                self.releasing.add(task)
                task.add_done_callback(self.releasing.discard)
                self.preparing.pop(key, None)
                self.retry_after.pop(key, None)
        for key in list(self.preparing):
            if key not in wanted and self.preparing[key].done():
                self.preparing.pop(key, None)
                self.retry_after.pop(key, None)
        for ident in set(self.states) - active_ids:
            self.states.pop(ident, None)
            self.windows.pop(ident, None)
            self.seeded.discard(ident)
            self.seed_bars.pop(ident, None)
            self.last_publish.pop(ident, None)
            self.evidence_windows.pop(ident, None)
            self.pending_requests.pop(ident, None)
        if records:
            try:
                await self.io(self.audit.write, records, now)
            except OSError:
                log.exception("exit_observation_log_failed")
        self.last_tick_seconds = time.monotonic() - began
        if mono - self.last_heartbeat >= 30:
            await self.io(
                self.repository.heartbeat,
                dict(
                    heartbeat_at=now.isoformat(),
                    positions=len(active_ids),
                    tick_seconds=self.last_tick_seconds,
                    subscriptions=len(self.contexts),
                ),
            )
            self.last_heartbeat = mono

    async def run(self):
        deadline = time.monotonic()
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("exit_strategy_tick_deferred")
            deadline += 5
            if deadline < time.monotonic():
                deadline = time.monotonic() + 5  # No catch-up observations.
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def close(self):
        tasks = list(self.preparing.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for key, (broker, contract) in self.contexts.items():
            await self.net_io(broker.release_quote, contract, key)
        if self.releasing:
            await asyncio.gather(*list(self.releasing), return_exceptions=True)
        await asyncio.to_thread(self.network_pool.shutdown, wait=True)
        self.pool.shutdown(wait=True)
