"""Shared article classification and idempotent ticker-local publication."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any

import httpx

from .admission import AdmissionContext, evaluate_admission
from .distribution_repository import (
    DistributionRepository,
    _complete_body_required,
    _complete_body_verified,
)
from .jev import JevClient
from .monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from .relevance import regex_relevant
from .schema import (
    RawMessageInput,
    SourceDefinition,
    utc_now,
)
from .service import MessageBusV2Service

logger = logging.getLogger(__name__)


class DistributionWorker:
    def __init__(
        self,
        repository: DistributionRepository,
        bus: MessageBusV2Service,
        terms: MonitoringTermsService,
        *,
        jev: JevClient | None = None,
        concurrency: int = 2,
    ) -> None:
        self.repository = repository
        self.bus = bus
        self.terms = terms
        self.jev = jev
        self.concurrency = max(1, min(concurrency, 8))
        self._jev_retry_at = 0.0
        self._jev_half_open = False
        self._retirement_at = 0.0
        self._active: set[asyncio.Task] = set()
        self._active_decisions: set[asyncio.Task] = set()
        self._pump_task = None

    def start(self):
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self):
        while True:
            try:
                await self.run_available()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('distribution pump deferred; durable claims remain recoverable')
            await asyncio.sleep(.25)

    async def _read(self, operation, *args):
        return await self.bus.storage.read(self.bus.repository.read_only, operation, *args)

    async def run_available(self) -> int:
        """Fill each released article slot without waiting for a slow batch peer."""
        for task in tuple(self._active_decisions):
            if task.done():
                self._active_decisions.remove(task)
                if not task.cancelled() and task.exception():
                    logger.error(
                        "distribution continuation failed: %s", task.exception(),
                        exc_info=task.exception(),
                    )
        for task in tuple(self._active):
            if task.done():
                self._active.remove(task)
                if not task.cancelled() and task.exception():
                    logger.error("distribution article failed: %s", type(task.exception()).__name__)
        available = self.concurrency - len(self._active)
        ready, decisions_ready = await self._read(self.repository.ready_work)
        rows = await self.bus.storage.call(self.repository.claim_deliveries, article_limit=available, _lane_priority=0) if available > 0 and ready else []
        groups = defaultdict(list)
        for row in rows:
            groups[(row["article_id"], int(row["terms_revision"]))].append(row)
        self._active.update(
            asyncio.create_task(self._article(group, offload_decisions=True))
            for group in groups.values()
        )
        available_decisions = 2 - len(self._active_decisions)
        if available_decisions > 0 and decisions_ready:
            decisions = await self.bus.storage.call(self.repository.claim_decisions, available_decisions)
            self._active_decisions.update(asyncio.create_task(self._continue_decisions(*row)) for row in decisions)
        if time.monotonic() >= self._retirement_at:
            self._retirement_at = time.monotonic() + 60
            await self.bus.storage.call(self.repository.expire_runs)
        return len(rows)

    async def close(self):
        if self._pump_task:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        for task in self._active | self._active_decisions:
            task.cancel()
        await asyncio.gather(*(self._active | self._active_decisions), return_exceptions=True)
        self._active.clear()

    async def run_once(self) -> int:
        if time.monotonic() >= self._retirement_at:
            self._retirement_at = time.monotonic() + 60
            retired = await self.bus.storage.call(self.repository.expire_runs)
            if retired:
                logger.info("retired expired realtime distribution runs count=%s", retired)
        rows = await self.bus.storage.call(self.repository.claim_deliveries, limit=16)
        groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[(row["article_id"], int(row["terms_revision"]))].append(row)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded(group: list[dict[str, Any]]) -> None:
            async with semaphore:
                await self._article(group)

        outcomes = await asyncio.gather(
            *(guarded(group) for group in groups.values()), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.error("distribution article worker failed: %s", type(outcome).__name__)
        return len(rows)

    async def _article(self, rows: list[dict[str, Any]], *, offload_decisions=False) -> None:
        message = RawMessageInput.model_validate_json(rows[0]["enriched_json"])
        source = SourceDefinition.model_validate_json(rows[0]["source_json"])
        current_source = await self._read(
            self.bus.repository.get_source, source.source_id
        )
        if _complete_body_required(current_source or source) and not _complete_body_verified(
            message
        ):
            for row in rows:
                await self.bus.storage.call(
                    self.repository.finish_delivery,
                    row["delivery_id"],
                    row["claim_token"],
                    "NO_PUBLIC_BODY",
                    {"reason": "complete_public_body_required"},
                )
            return
        eligible_rows: list[dict[str, Any]] = []
        for row in rows:
            context = AdmissionContext.model_validate(json.loads(row["admission_json"]))
            reason = evaluate_admission(
                message.published_at, context, utc_now(), message.publication_time_basis
            )
            if reason:
                await self.bus.storage.call(
                    self.repository.finish_delivery,
                    row["delivery_id"],
                    row["claim_token"],
                    "ADMISSION_REJECTED",
                    {"reason": reason},
                )
            else:
                eligible_rows.append(row)
        if not eligible_rows:
            return
        language = source.content_language or "en"
        definitions: dict[str, TickerMonitoringTerms] = {}
        relevant: dict[str, bool] = {}
        for row in eligible_rows:
            found = await self._read(
                self.terms.get, row["ticker"], int(row["terms_revision"])
            )
            if found is None:
                await self.bus.storage.call(
                    self.repository.finish_delivery,
                    row["delivery_id"],
                    row["claim_token"],
                    "CONFIG_INCOMPLETE",
                    {"reason": "TERMS_MISSING"},
                )
                continue
            definitions[row["ticker"]] = found[1]
            relevant[row["ticker"]] = regex_relevant(found[1], language, message)

        # Only undecided tickers need the business classifier. L1 comparisons
        # are optional offline evaluation, not recurring production work.
        jev_enabled = bool(
            definitions and source.distribution_policy and source.distribution_policy.jev_enabled
        )
        for row in eligible_rows:
            if relevant.get(row["ticker"]):
                await self._deliver(
                    row,
                    source,
                    message,
                    regex_hit=True,
                    jev_result=None,
                    jev_attempts=0,
                    error_code=None,
                )
        eligible_rows = [row for row in eligible_rows
                         if row["ticker"] in definitions and not relevant.get(row["ticker"])]
        definitions = {ticker: value for ticker, value in definitions.items()
                       if not relevant.get(ticker)}
        if not eligible_rows:
            return
        if offload_decisions and jev_enabled:
            from .schema import sha256_text, canonical_json

            reduced = [
                {k: v for k, v in row.items() if k not in {"enriched_json", "source_json"}}
                for row in eligible_rows
            ]
            payload = {
                "rows": reduced,
                "message": message.model_dump(mode="json"),
                "source": source.model_dump(mode="json"),
                "relevant": relevant,
                "definitions": {k: v.model_dump(mode="json") for k, v in definitions.items()},
            }
            encoded = await self.bus.storage.prepare(self.bus.repository.content.encode, payload)
            await self.bus.storage.call(
                self.repository.defer_decisions,
                {
                    "id": sha256_text(canonical_json([r["delivery_id"] for r in reduced])),
                    "encoded": encoded,
                    "rows": reduced,
                    "relevant": relevant,
                },
            )
            return
        await self._finish_article_decisions(
            message, source, eligible_rows, definitions, relevant, jev_enabled
        )

    async def _continue_decisions(self, identity, token, payload):
        payload = json.loads(
            await self.bus.storage.prepare(self.bus.repository.content.text, payload)
        )
        rows = await self.bus.storage.call(
            self.repository.resume_decisions, identity, token, payload["rows"], _lane_priority=0
        )
        async def renew():
            while True:
                await asyncio.sleep(25)
                await self.bus.storage.call(
                    self.repository.renew_decisions, identity, token, rows, _lane_priority=0
                )
        heartbeat = asyncio.create_task(renew()) if rows else None
        processing = None
        try:
            if rows:
                definitions = {row["ticker"]: TickerMonitoringTerms.model_validate(
                    payload["definitions"][row["ticker"]]) for row in rows}
                processing = asyncio.create_task(self._finish_article_decisions(
                    RawMessageInput.model_validate(payload["message"]),
                    SourceDefinition.model_validate(payload["source"]), rows,
                    definitions, {row["ticker"]: False for row in rows}, True,
                ))
                await asyncio.wait({processing, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat.done():
                    heartbeat.result()
                await processing
        finally:
            if heartbeat:
                heartbeat.cancel()
                if processing:
                    processing.cancel()
                await asyncio.gather(heartbeat, *([processing] if processing else []), return_exceptions=True)
        await self.bus.storage.call(self.repository.finish_decisions, identity, token)

    async def _finish_article_decisions(
        self, message, source, eligible_rows, definitions, relevant, jev_enabled
    ):
        scores: dict[str, float] = {}
        attempts: dict[str, int] = {}
        errors: dict[str, str] = {}
        cached = await self._read(self.repository.cached_decisions, eligible_rows)
        for ticker, value in cached.items():
            scores[ticker] = 1.0 if value["regex_result"] else value["jev_result"]
            attempts[ticker] = value["jev_attempts"]
        definitions = {k:v for k,v in definitions.items() if k not in cached}
        delivered = set()
        async def positive(ticker, score, attempt):
            for row in eligible_rows:
                if row['ticker'] == ticker:
                    await self._deliver(row, source, message, regex_hit=False,
                        jev_result=score, jev_attempts=attempt, error_code=None)
                    delivered.add(ticker)
        if jev_enabled:
            new_scores, new_attempts, errors = await self._jev(message, definitions, on_positive=positive) if definitions else ({},{},{})
            scores.update(new_scores)
            attempts.update(new_attempts)
            for row in eligible_rows:
                if relevant.get(row["ticker"]):
                    await self.bus.storage.call(
                        self.repository.update_jev_decision,
                        row,
                        scores.get(row["ticker"]),
                        attempts.get(row["ticker"], 0),
                        errors.get(row["ticker"]),
                    )
        for row in eligible_rows:
            if row['ticker'] in delivered:
                continue
            if (row["ticker"] in definitions or row["ticker"] in cached) and not relevant[row["ticker"]]:
                await self._deliver(
                    row,
                    source,
                    message,
                    regex_hit=False,
                    jev_result=scores.get(row["ticker"]),
                    jev_attempts=attempts.get(row["ticker"], 0),
                    error_code=errors.get(row["ticker"]),
                )

    async def _jev(
        self, message: RawMessageInput, definitions: dict[str, TickerMonitoringTerms], *, on_positive=None
    ) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
        if self.jev is None:
            return {}, {}, {ticker: "JEV_DISABLED" for ticker in definitions}
        if time.monotonic() < self._jev_retry_at or self._jev_half_open:
            return {}, {}, {ticker: "JEV_PROVIDER_UNAVAILABLE" for ticker in definitions}
        half_open = bool(self._jev_retry_at)
        if half_open:
            self._jev_half_open = True
        try:
            return await self._jev_attempts(message, definitions, on_positive=on_positive)
        finally:
            if half_open:
                self._jev_half_open = False

    async def _jev_attempts(
        self, message: RawMessageInput, definitions: dict[str, TickerMonitoringTerms], *, on_positive=None
    ) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
        states = _article_states(message)
        if states is None:
            return {}, {}, {ticker: "JEV_INPUT_LIMIT" for ticker in definitions}
        deadline = time.monotonic() + 60
        answered: dict[str, dict[int, float]] = {ticker: {} for ticker in definitions}
        attempts = {ticker: 0 for ticker in definitions}
        errors: dict[str, str] = {}
        positive: set[str] = set()
        retry_delay = 5.0
        for round_number in (1, 2):
            for index, state in enumerate(states):
                target = {
                    ticker: definition
                    for ticker, definition in definitions.items()
                    if ticker not in positive
                    and index not in answered[ticker]
                    and ticker not in errors
                }
                if not target:
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    errors.update({ticker: "JEV_TIME_BUDGET" for ticker in target})
                    break
                for ticker in target:
                    attempts[ticker] = round_number
                try:
                    async with asyncio.timeout(min(15.0, remaining)):
                        result = await self.jev.classify(state, target)
                    self._jev_retry_at = 0.0
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    logger.warning("Bailian decision HTTP failure: %s", status)
                    if status == 402:
                        self._jev_retry_at = time.monotonic() + 300
                        errors.update(
                            {
                                ticker: "JEV_PROVIDER_UNAVAILABLE"
                                for ticker in definitions
                                if ticker not in positive
                            }
                        )
                        break
                    if status in {400, 401, 403, 404, 422}:
                        errors.update({ticker: f"JEV_HTTP_{status}" for ticker in target})
                    elif status == 429:
                        value = exc.response.headers.get("Retry-After")
                        if value and value.isdecimal():
                            retry_delay = max(5.0, float(value))
                    continue
                except Exception as exc:
                    logger.warning("Bailian decision attempt failed: %s", type(exc).__name__)
                    continue
                for ticker, score in result.items():
                    if ticker not in target:
                        continue
                    answered[ticker][index] = score
                    if score >= 0.5:
                        positive.add(ticker)
                        if on_positive:
                            await on_positive(ticker, score, round_number)
            incomplete = {
                ticker
                for ticker in definitions
                if ticker not in positive
                and ticker not in errors
                and len(answered[ticker]) < len(states)
            }
            if not incomplete:
                break
            if round_number == 1:
                if retry_delay >= deadline - time.monotonic():
                    errors.update({ticker: "JEV_TIME_BUDGET" for ticker in incomplete})
                    break
                await asyncio.sleep(retry_delay)
        scores = {
            ticker: max(values.values())
            for ticker, values in answered.items()
            if values and (ticker in positive or len(values) == len(states))
        }
        for ticker in definitions:
            if ticker not in scores and ticker not in errors:
                errors[ticker] = "JEV_RETRY_EXHAUSTED"
        return scores, attempts, errors

    async def _deliver(
        self,
        row: dict[str, Any],
        source: SourceDefinition,
        message: RawMessageInput,
        *,
        regex_hit: bool,
        jev_result: float | None,
        jev_attempts: int,
        error_code: str | None,
    ) -> None:
        try:
            completed = await self.bus.storage.call(
                self.repository.settle_delivery, self.bus, row, source, message,
                regex_result=regex_hit, jev_result=jev_result, jev_attempts=jev_attempts,
                error_code=error_code, _lane_priority=0 if regex_hit or (jev_result or 0)>=.5 else 1,
            )
        except Exception:
            logger.exception("distribution delivery failed delivery_id=%s", row["delivery_id"])
            await self.bus.storage.call(self.repository.finish_delivery,
                row["delivery_id"],row["claim_token"],
                "FAILED" if int(row["attempts"])>=2 else "PENDING",
                {"reason":"DELIVERY_COMMIT_FAILED"})
            return
        if completed:
            result, enriched = completed
            await self.bus.record_consumable(result, enriched)


def _article_states(message: RawMessageInput) -> list[RawMessageInput] | None:
    """Keep every body character in bounded paragraph states, or explicitly decline Jev."""
    body = message.body or ""
    if len(body) + len(message.title or "") + len(message.summary or "") > 64000:
        return None
    if not body:
        return [message]
    chunks: list[str] = []
    current = ""
    for paragraph in body.splitlines(keepends=True):
        while paragraph:
            room = 16000 - len(current)
            if room == 0:
                chunks.append(current)
                current = ""
                room = 16000
            current += paragraph[:room]
            paragraph = paragraph[room:]
    if current:
        chunks.append(current)
    if len(chunks) > 4:
        return None
    return [message.model_copy(update={"body": chunk}) for chunk in chunks]
