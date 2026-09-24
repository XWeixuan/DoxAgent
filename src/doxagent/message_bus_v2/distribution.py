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
from .distribution_repository import DistributionRepository
from .jev import JevClient
from .monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from .relevance import regex_relevant
from .schema import (
    RawMessageInput,
    SourceDefinition,
    TickerMonitoringStatus,
    TickerSourceBinding,
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

    async def run_once(self) -> int:
        rows = self.repository.claim_deliveries(limit=16)
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

    async def _article(self, rows: list[dict[str, Any]]) -> None:
        message = RawMessageInput.model_validate_json(rows[0]["enriched_json"])
        source = SourceDefinition.model_validate_json(rows[0]["source_json"])
        eligible_rows: list[dict[str, Any]] = []
        for row in rows:
            context = AdmissionContext.model_validate(json.loads(row["admission_json"]))
            reason = evaluate_admission(
                message.published_at, context, utc_now(), message.publication_time_basis
            )
            if reason:
                self.repository.finish_delivery(
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
            found = self.terms.get(row["ticker"], int(row["terms_revision"]))
            if found is None:
                self.repository.finish_delivery(
                    row["delivery_id"],
                    row["claim_token"],
                    "CONFIG_INCOMPLETE",
                    {"reason": "TERMS_MISSING"},
                )
                continue
            definitions[row["ticker"]] = found[1]
            relevant[row["ticker"]] = regex_relevant(found[1], language, message)

        # Start the experimental comparison in parallel; deterministic positives need not wait.
        jev_task = (
            asyncio.create_task(self._jev(message, definitions))
            if definitions and source.distribution_policy and source.distribution_policy.jev_enabled
            else None
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
        scores: dict[str, float] = {}
        attempts: dict[str, int] = {}
        errors: dict[str, str] = {}
        if jev_task:
            scores, attempts, errors = await jev_task
            for row in eligible_rows:
                if relevant.get(row["ticker"]):
                    self.repository.update_jev_decision(
                        row,
                        scores.get(row["ticker"]),
                        attempts.get(row["ticker"], 0),
                        errors.get(row["ticker"]),
                    )
        for row in eligible_rows:
            if row["ticker"] in definitions and not relevant[row["ticker"]]:
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
        self, message: RawMessageInput, definitions: dict[str, TickerMonitoringTerms]
    ) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
        if self.jev is None:
            return {}, {}, {ticker: "JEV_DISABLED" for ticker in definitions}
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
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    logger.warning("Jev decision HTTP failure: %s", status)
                    if status in {400, 401, 403, 404, 422}:
                        errors.update({ticker: f"JEV_HTTP_{status}" for ticker in target})
                    elif status == 429:
                        value = exc.response.headers.get("Retry-After")
                        if value and value.isdecimal():
                            retry_delay = max(5.0, float(value))
                    continue
                except Exception as exc:
                    logger.warning("Jev decision attempt failed: %s", type(exc).__name__)
                    continue
                for ticker, score in result.items():
                    if ticker not in target:
                        continue
                    answered[ticker][index] = score
                    if score >= 0.5:
                        positive.add(ticker)
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
        relevant = regex_hit or (jev_result is not None and jev_result >= 0.5)
        decision_id = self.repository.record_decision(
            row,
            regex_result=regex_hit,
            jev_result=jev_result,
            jev_attempts=jev_attempts,
            error_code=error_code,
            final_result="RELEVANT" if relevant else "NOT_RELEVANT",
        )
        if not relevant:
            self.repository.finish_delivery(
                row["delivery_id"],
                row["claim_token"],
                "NOT_RELEVANT",
                {"decision_id": decision_id, "reason": error_code},
            )
            return
        snapshot = TickerSourceBinding.model_validate_json(row["binding_json"])
        current = self.bus.repository.get_binding(snapshot.binding_id)
        ticker_state = self.bus.repository.get_ticker_state(snapshot.ticker)
        if (
            current is None
            or not current.enabled
            or ticker_state is None
            or ticker_state.status != TickerMonitoringStatus.RUNNING
        ):
            self.repository.finish_delivery(
                row["delivery_id"], row["claim_token"], "CANCELLED", {"decision_id": decision_id}
            )
            return
        context = AdmissionContext.model_validate(json.loads(row["admission_json"]))
        reason = evaluate_admission(
            message.published_at, context, utc_now(), message.publication_time_basis
        )
        if reason:
            self.repository.finish_delivery(
                row["delivery_id"],
                row["claim_token"],
                "ADMISSION_REJECTED",
                {"reason": reason, "decision_id": decision_id},
            )
            return
        metadata = {
            **message.metadata,
            "distribution": {
                "decision_id": decision_id,
                "terms_revision": row["terms_revision"],
                "regex_hit": regex_hit,
                "jev_result": jev_result,
            },
        }
        enriched = message.model_copy(update={"admission_context": context, "metadata": metadata})
        original = RawMessageInput.model_validate_json(row["original_json"])
        try:
            result = await self.bus.accept_message(
                source=source,
                binding=current,
                message=enriched,
                bootstrap=False,
                trusted_enrichment=True,
                enrichment_input=original,
            )
        except Exception as exc:
            logger.warning(
                "distribution delivery failed delivery_id=%s code=%s",
                row["delivery_id"],
                type(exc).__name__,
            )
            if int(row["attempts"]) >= 2:
                self.repository.finish_delivery(
                    row["delivery_id"], row["claim_token"], "FAILED", {"reason": type(exc).__name__}
                )
            else:
                self.repository.finish_delivery(
                    row["delivery_id"],
                    row["claim_token"],
                    "PENDING",
                    {"reason": type(exc).__name__},
                )
            return
        state = "DEDUPLICATED" if result.decision.value == "duplicate" else "PUBLISHED"
        self.repository.finish_delivery(
            row["delivery_id"],
            row["claim_token"],
            state,
            {"decision_id": decision_id, "ingest": result.model_dump(mode="json")},
        )


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
