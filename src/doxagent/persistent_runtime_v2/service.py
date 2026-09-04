"""Parallel W1/W2 orchestration and durable side effects for Runtime V2."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Lock, Thread, current_thread
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from doxagent.event_library.contracts import CanonicalEvent
from doxagent.event_library.provider import KnownEventIndexSnapshot
from doxagent.workflows.codex_document3.runtime_projection import (
    assert_runtime_projection_compatible,
)
from doxagent.workflows.codex_document3.schema import (
    PolicyDecision,
    PolicyDetailSnapshot,
    RuntimePolicyProjection,
    RuntimePolicyRecord,
)

from .prompts import RuntimeV2PromptSet
from .providers import RuntimeKnownEventProvider, RuntimePolicyProvider
from .repository import PersistentRuntimeV2Repository
from .router import route_runtime_case, route_w3_result
from .schema import (
    ArchiveRecord,
    BadcaseRecord,
    PolicyActivationRecord,
    ProvisionalFactDetail,
    RuntimeCase,
    RuntimeCaseStatus,
    RuntimeConfidence,
    RuntimeEffect,
    RuntimeEffectStatus,
    RuntimeModelTurn,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    RuntimeTechnicalStatus,
    RuntimeVersionPin,
    SourceMessageEnvelope,
    TradeDecisionOrigin,
    TradeRecord,
    W1CaptureMode,
    W1FactExtractionResult,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W1Round1Result,
    W2PolicyResult,
    W3CaseResult,
    W3CaseStatus,
    W3CoverageGapRecord,
    W3Mode,
    W3PolicyResult,
    W3RouteCase,
    W3ThreadKind,
    new_runtime_v2_id,
    utc_now,
)
from .transport import (
    RuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    RuntimeResponsesResult,
)
from .w3 import W3Agent, W3Error

if TYPE_CHECKING:
    from .projection import RuntimeV2ProjectionOutbox

T = TypeVar("T", bound=BaseModel)
_EASTERN = ZoneInfo("America/New_York")


def _w2_projection_business_payload(
    projection: RuntimePolicyProjection,
) -> dict[str, Any]:
    """Project only W2 matching semantics; keep protocol/audit state local."""

    return {
        "activation_semantics": "OR",
        "policies": [
            policy.model_dump(mode="json", exclude={"activation_revision"})
            for policy in projection.policies
        ]
    }


def _w2_detail_business_payload(details: PolicyDetailSnapshot) -> dict[str, Any]:
    """Expose canonical Policy bodies without the versioned retrieval envelope."""

    return {
        "activation_semantics": "OR",
        "policies": [policy.model_dump(mode="json") for policy in details.policies]
    }


def _w1_provisional_business_payload(value: ProvisionalFactDetail) -> dict[str, Any]:
    """Expose the provisional identity and fact semantics, not its journal envelope."""

    return {
        "provisional_event_id": value.provisional_event_id,
        "candidate": value.candidate.model_dump(mode="json"),
    }


def _w1_canonical_event_business_payload(value: CanonicalEvent) -> dict[str, Any]:
    """Project fields needed for novelty comparison and temporal/relationship semantics."""

    return value.model_dump(
        mode="json",
        include={
            "event_id",
            "title",
            "event_type",
            "occurred_at",
            "occurrence_time_precision",
            "status",
            "canonical_summary",
            "known_event_summary",
            "related_event_ids",
            "supersedes_event_id",
            "derived_from_event_ids",
            "facts",
        },
    )


def _w1_final_business_payload(value: W1NoveltyResult | None) -> dict[str, Any] | None:
    """Carry the adjudicated novelty semantics into R3 without confidence metadata."""

    if value is None:
        return None
    return value.model_dump(
        mode="json",
        include={"result", "reference_ids", "reason"},
    )


class RuntimeInputUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeSemanticOutputError(RuntimeError):
    pass


class PersistentRuntimeV2Service:
    def __init__(
        self,
        *,
        repository: PersistentRuntimeV2Repository,
        responses: RuntimeResponsesClient,
        known_events: RuntimeKnownEventProvider,
        policies: RuntimePolicyProvider,
        prompts: RuntimeV2PromptSet | None = None,
        prompt_root: Path | None = None,
        retry_delays_seconds: tuple[float, float] = (5.0, 10.0),
        sleep: Callable[[float], None] = time.sleep,
        dispatch_effects: bool = True,
        projection_outbox: RuntimeV2ProjectionOutbox | None = None,
        w3_agent: W3Agent | None = None,
        w3_max_ticker_concurrency: int = 5,
        w3_lease_seconds: int = 1200,
    ) -> None:
        self.repository = repository
        self.responses = responses
        self.known_events = known_events
        self.policies = policies
        self.prompts = prompts or RuntimeV2PromptSet.load(prompt_root)
        self.retry_delays_seconds = retry_delays_seconds
        self._sleep = sleep
        self._dispatch_effects = dispatch_effects
        self._projection_outbox = projection_outbox
        self._w3_agent = w3_agent
        self._w3_max_ticker_concurrency = w3_max_ticker_concurrency
        self._w3_lease_seconds = w3_lease_seconds
        self._hot_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prv2-hot")
        self._effect_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="prv2-effect",
        )
        self._closed = False
        self._w3_threads: set[Thread] = set()
        self._w3_threads_lock = Lock()

    def __enter__(self) -> PersistentRuntimeV2Service:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Drain submitted work and release worker threads deterministically."""

        if self._closed:
            return
        self._closed = True
        self._hot_executor.shutdown(wait=True, cancel_futures=False)
        self._effect_executor.shutdown(wait=True, cancel_futures=False)
        while True:
            with self._w3_threads_lock:
                threads = list(self._w3_threads)
            if not threads:
                break
            for thread in threads:
                thread.join()
        if self._w3_agent is not None:
            self._w3_agent.close()

    def execute_message(self, source: SourceMessageEnvelope) -> RuntimeCase:
        if self._closed:
            raise RuntimeError("Persistent Runtime V2 service is closed")
        existing = self.repository.get_case_by_source(source.source_message_id)
        if existing is not None:
            return existing
        trading_date = source.occurrence_source_time.astimezone(_EASTERN).date()
        index = self.known_events.current_index(source.snapshot.ticker)
        projection = self.policies.current_projection(source.snapshot.ticker)
        if index is None:
            raise RuntimeInputUnavailable(
                "event_library_unavailable",
                "Published Known Event Index is unavailable",
            )
        if projection is None:
            raise RuntimeInputUnavailable(
                "policy_projection_unavailable",
                "Published Runtime Policy Projection is unavailable",
            )
        assert_runtime_projection_compatible(projection)
        projection = self._without_consumed_policies(projection)
        provisional_version = self.repository.provisional_snapshot_version(
            source.snapshot.ticker,
            trading_date,
        )
        case = RuntimeCase(
            case_id=new_runtime_v2_id("case"),
            trading_date=trading_date,
            source=source,
            version_pin=RuntimeVersionPin(
                event_library_version=index.version,
                provisional_snapshot_version=provisional_version,
                policy_set_version=projection.policy_set_version,
                runtime_projection_version=projection.policy_set_version,
            ),
            status=RuntimeCaseStatus.RUNNING,
        )
        case = self.repository.save_case(case)
        if case.status is not RuntimeCaseStatus.RUNNING:
            return case

        started = perf_counter()
        w1_future: Future[tuple[W1Round1Result, W1NoveltyResult, str]] = self._hot_executor.submit(
            self._run_w1_hot, case, index
        )
        w2_future: Future[tuple[W2PolicyResult, W2PolicyResult, str]] = self._hot_executor.submit(
            self._run_w2_hot, case, projection
        )
        try:
            w1_r1, w1_final, w1_response_id = w1_future.result()
            w2_r1, w2_final, _w2_response_id = w2_future.result()
        except Exception as exc:
            w1_future.cancel()
            w2_future.cancel()
            return self._fail_case(case, exc)

        route = route_runtime_case(w1_final, w2_final)
        if route.primary_route is RuntimePrimaryRoute.TRADE:
            # Claim before effects are enqueued so another dispatcher cannot
            # run the Delta side effect for a Policy boundary this case lost.
            try:
                claimed_policy = self._claim_first_policy(case, w2_final)
            except Exception as exc:
                return self._fail_case(case, exc)
            if claimed_policy is None:
                route = RuntimeRouteDecision(
                    primary_route=RuntimePrimaryRoute.ARCHIVE,
                    side_effects=[RuntimeSideEffect.ARCHIVE_MESSAGE],
                    reason="policy_activation_already_consumed",
                )
        adjudicated = case.model_copy(
            update={
                "status": (
                    RuntimeCaseStatus.PENDING_W3
                    if route.primary_route.value == "W3"
                    else RuntimeCaseStatus.ADJUDICATED
                ),
                "w1_round1": w1_r1,
                "w1_final": w1_final,
                "w2_round1": w2_r1,
                "w2_final": w2_final,
                "route": route,
                "hot_path_latency_ms": round((perf_counter() - started) * 1000),
                "updated_at": utc_now(),
            }
        )
        self._enqueue_route_effects(adjudicated, w1_response_id)
        if self._dispatch_effects:
            self._effect_executor.submit(self.dispatch_pending_effects, limit=20)
        return adjudicated

    def _run_w1_hot(
        self,
        case: RuntimeCase,
        index: KnownEventIndexSnapshot,
    ) -> tuple[W1Round1Result, W1NoveltyResult, str]:
        provisional = self.repository.list_provisional(case.ticker, case.trading_date)
        r1_payload = {
            "source_message": case.source.snapshot.model_dump(mode="json"),
            "published_known_event_index": index.known_event_index,
            "today_provisional_facts": [
                _w1_provisional_business_payload(item) for item in provisional
            ],
        }
        r1, r1_response_id = self._call_with_retry(
            case=case,
            lane="W1",
            round_name="R1",
            round_prompt=self.prompts.w1_r1,
            payload=r1_payload,
            output_model=W1Round1Result,
            schema_name="w1_round1_result",
        )
        selected_ids = r1.event_ids[:5]
        r1 = r1.model_copy(update={"event_ids": selected_ids})
        provisional_ids = {item.provisional_event_id for item in provisional}
        requested_provisional = [item for item in selected_ids if item in provisional_ids]
        requested_canonical = [item for item in selected_ids if item not in provisional_ids]
        details = self.known_events.details(
            case.ticker,
            case.version_pin.event_library_version,
            requested_canonical,
        )
        if details is None:
            raise RuntimeInputUnavailable(
                "event_detail_unavailable",
                "Version-pinned Event Detail is unavailable",
            )
        provisional_details = self.repository.get_provisional(
            case.ticker,
            case.trading_date,
            requested_provisional,
        )
        found_provisional = {item.provisional_event_id for item in provisional_details}
        missing = [
            *details.missing_event_ids,
            *[item for item in requested_provisional if item not in found_provisional],
        ]
        if missing:
            raise RuntimeInputUnavailable(
                "event_detail_missing",
                f"Version-pinned Event Detail is missing {len(missing)} requested IDs",
            )
        loaded_ids = {
            *[event.event_id for event in details.events],
            *[item.provisional_event_id for item in provisional_details],
        }

        def validate(value: W1NoveltyResult) -> None:
            if not set(value.reference_ids).issubset(loaded_ids):
                raise RuntimeSemanticOutputError(
                    "W1 R2 returned a reference ID that was not loaded"
                )

        r2, r2_response_id = self._call_with_retry(
            case=case,
            lane="W1",
            round_name="R2",
            round_prompt=self.prompts.w1_r2,
            payload={
                "source_message": case.source.snapshot.model_dump(mode="json"),
                "event_details": {
                    "canonical_events": [
                        _w1_canonical_event_business_payload(event)
                        for event in details.events
                    ],
                    "provisional_events": [
                        _w1_provisional_business_payload(item)
                        for item in provisional_details
                    ],
                },
            },
            output_model=W1NoveltyResult,
            schema_name="w1_novelty_result",
            previous_response_id=r1_response_id,
            validate=validate,
        )
        return r1, r2, r2_response_id

    def _run_w2_hot(
        self,
        case: RuntimeCase,
        projection: RuntimePolicyProjection,
    ) -> tuple[W2PolicyResult, W2PolicyResult, str]:
        projected_ids = {item.policy_id for item in projection.policies}

        def validate_r1(value: W2PolicyResult) -> None:
            if not set(value.policy_ids).issubset(projected_ids):
                raise RuntimeSemanticOutputError(
                    "W2 R1 returned a Policy ID outside the pinned projection"
                )

        r1, r1_response_id = self._call_with_retry(
            case=case,
            lane="W2",
            round_name="R1",
            round_prompt=self.prompts.w2_r1,
            payload={
                "source_message": case.source.snapshot.model_dump(mode="json"),
                "runtime_policy_projection": _w2_projection_business_payload(projection),
            },
            output_model=W2PolicyResult,
            schema_name="w2_policy_result",
            validate=validate_r1,
        )
        r1 = self._sanitize_condition_attribution(r1, projection)
        if not r1.policy_ids or r1.confidence is not RuntimeConfidence.LOW:
            return r1, r1, r1_response_id
        details = self.policies.details(
            case.ticker,
            case.version_pin.policy_set_version,
            r1.policy_ids,
        )
        if details.missing_policy_ids:
            raise RuntimeInputUnavailable(
                "policy_detail_missing",
                "Version-pinned Policy Detail is missing requested IDs",
            )
        requested_ids = set(details.requested_policy_ids)

        def validate_r2(value: W2PolicyResult) -> None:
            if not set(value.policy_ids).issubset(requested_ids):
                raise RuntimeSemanticOutputError(
                    "W2 R2 returned a Policy ID that was not selected in R1"
                )

        r2, r2_response_id = self._call_with_retry(
            case=case,
            lane="W2",
            round_name="R2",
            round_prompt=self.prompts.w2_r2,
            payload={
                "source_message": case.source.snapshot.model_dump(mode="json"),
                "policy_details": _w2_detail_business_payload(details),
            },
            output_model=W2PolicyResult,
            schema_name="w2_policy_result",
            previous_response_id=r1_response_id,
            validate=validate_r2,
        )
        current_projection = self._without_consumed_policies(projection)
        active_ids = {item.policy_id for item in current_projection.policies}
        r2 = r2.model_copy(
            update={"policy_ids": [item for item in r2.policy_ids if item in active_ids]}
        )
        r2 = self._sanitize_condition_attribution(r2, current_projection)
        return r1, r2, r2_response_id

    def _without_consumed_policies(
        self, projection: RuntimePolicyProjection
    ) -> RuntimePolicyProjection:
        consumed = self.repository.list_consumed_policy_revisions(projection.ticker)
        return projection.model_copy(
            update={
                "policies": [
                    item
                    for item in projection.policies
                    if (item.policy_id, item.activation_revision) not in consumed
                ]
            }
        )

    @staticmethod
    def _sanitize_condition_attribution(
        result: W2PolicyResult, projection: RuntimePolicyProjection
    ) -> W2PolicyResult:
        by_policy = {item.policy_id: set(item.condition_ids) for item in projection.policies}
        cleaned = []
        for item in result.matched_condition_ids:
            valid = by_policy.get(item.policy_id)
            if valid is None:
                continue
            cleaned.append(
                item.model_copy(
                    update={
                        "condition_ids": [
                            condition_id
                            for condition_id in item.condition_ids
                            if condition_id in valid
                        ]
                    }
                )
            )
        return result.model_copy(update={"matched_condition_ids": cleaned})

    def _call_with_retry(
        self,
        *,
        case: RuntimeCase,
        lane: str,
        round_name: str,
        round_prompt: str,
        payload: dict[str, Any],
        output_model: type[T],
        schema_name: str,
        previous_response_id: str | None = None,
        validate: Callable[[T], None] | None = None,
    ) -> tuple[T, str]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            started = perf_counter()
            try:
                result: RuntimeResponsesResult[T] = self.responses.complete(
                    RuntimeResponsesRequest(
                        instructions=self.prompts.instructions(round_prompt),
                        payload=payload,
                        output_model=output_model,
                        schema_name=schema_name,
                        previous_response_id=previous_response_id,
                        metadata={
                            "runtime": "persistent_v2",
                            "case_id": case.case_id,
                            "lane": lane,
                            "round": round_name,
                        },
                    )
                )
                if validate is not None:
                    validate(result.value)
                self._record_success_turn(
                    case=case,
                    lane=lane,
                    round_name=round_name,
                    attempt=attempt,
                    previous_response_id=previous_response_id,
                    result=result,
                )
                return result.value, result.response_id
            except RuntimeSemanticOutputError as exc:
                error = RuntimeResponsesError(
                    "semantic_output_validation_failed",
                    str(exc),
                    retryable=True,
                )
            except RuntimeResponsesError as exc:
                error = exc
            last_error = error
            self.repository.append_turn(
                RuntimeModelTurn(
                    case_id=case.case_id,
                    lane=lane,  # type: ignore[arg-type]
                    round_name=round_name,  # type: ignore[arg-type]
                    attempt_number=attempt,
                    status=(
                        RuntimeTechnicalStatus.PENDING_RETRY
                        if error.retryable and attempt < 3
                        else RuntimeTechnicalStatus.UNAVAILABLE
                        if not error.retryable
                        else RuntimeTechnicalStatus.FAILED
                    ),
                    previous_response_id=previous_response_id,
                    model=self.responses.model,
                    latency_ms=round((perf_counter() - started) * 1000),
                    error_code=error.code,
                    error_message=str(error),
                )
            )
            if not error.retryable or attempt >= 3:
                raise error
            self._sleep(self.retry_delays_seconds[attempt - 1])
        raise RuntimeError("Runtime model retry loop ended unexpectedly") from last_error

    def _record_success_turn(
        self,
        *,
        case: RuntimeCase,
        lane: str,
        round_name: str,
        attempt: int,
        previous_response_id: str | None,
        result: RuntimeResponsesResult[Any],
    ) -> None:
        self.repository.append_turn(
            RuntimeModelTurn(
                case_id=case.case_id,
                lane=lane,  # type: ignore[arg-type]
                round_name=round_name,  # type: ignore[arg-type]
                attempt_number=attempt,
                status=RuntimeTechnicalStatus.OK,
                response_id=result.response_id,
                previous_response_id=previous_response_id,
                model=self.responses.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                reasoning_tokens=result.reasoning_tokens,
                cached_input_tokens=result.cached_input_tokens,
                latency_ms=result.latency_ms,
                output=result.value.model_dump(mode="json"),
            )
        )

    def _enqueue_route_effects(self, case: RuntimeCase, w1_response_id: str) -> None:
        if case.route is None:
            raise ValueError("Cannot enqueue effects before routing")
        effects: list[RuntimeEffect] = []
        for effect_type in case.route.side_effects:
            payload: dict[str, Any] = {}
            if effect_type is RuntimeSideEffect.EMIT_DELTA:
                payload["previous_response_id"] = w1_response_id
            effects.append(
                RuntimeEffect(
                    case_id=case.case_id,
                    effect_type=effect_type,
                    idempotency_key=f"{case.case_id}:{effect_type.value}",
                    payload=payload,
                )
            )
        w3_case = None
        if case.route.primary_route.value == "W3":
            w3_case = W3RouteCase(
                case_id=case.case_id,
                ticker=case.ticker,
                source=case.source,
                w1_final=case.w1_final,  # type: ignore[arg-type]
                w2_final=case.w2_final,  # type: ignore[arg-type]
                event_library_version=case.version_pin.event_library_version,
                policy_set_version=case.version_pin.policy_set_version,
                route_reason=case.route.reason,
                mode=self._w3_mode(case),
            )
        self.repository.save_case_with_effects(case, effects, w3_case)

    @staticmethod
    def _w3_mode(case: RuntimeCase) -> W3Mode:
        if (
            case.w1_final is not None
            and case.w1_final.result is W1NoveltyVerdict.NEW
            and case.w1_final.confidence is RuntimeConfidence.NORMAL
            and case.w2_final is not None
            and not case.w2_final.policy_ids
            and case.w2_final.confidence is RuntimeConfidence.NORMAL
        ):
            return W3Mode.UNCOVERED_NEW
        return W3Mode.REVALIDATE_THEN_EVALUATE

    def process_pending_effects(self, *, limit: int = 20) -> int:
        return sum(
            self._process_claimed_effect(effect)
            for effect in self.repository.claim_effects(limit=limit)
        )

    def dispatch_pending_effects(self, *, limit: int = 20) -> int:
        """Dispatch W3 without a global pool; ticker slots provide the sole cap."""

        dispatched = 0
        for effect in self.repository.claim_effects(limit=limit):
            if effect.effect_type is RuntimeSideEffect.ROUTE_TO_W3:
                thread = Thread(
                    target=self._run_w3_effect_thread,
                    args=(effect,),
                    name=f"prv2-w3-{effect.case_id[-8:]}",
                    daemon=False,
                )
                with self._w3_threads_lock:
                    self._w3_threads.add(thread)
                thread.start()
                dispatched += 1
            else:
                dispatched += self._process_claimed_effect(effect)
        return dispatched

    def _run_w3_effect_thread(self, effect: RuntimeEffect) -> None:
        try:
            self._process_claimed_effect(effect)
        finally:
            with self._w3_threads_lock:
                self._w3_threads.discard(current_thread())

    def _process_claimed_effect(self, effect: RuntimeEffect) -> int:
        try:
            self._execute_effect(effect)
        except Exception as exc:
            next_attempt = effect.attempt_count + 1
            retryable = not isinstance(exc, RuntimeResponsesError) and next_attempt < 3
            failed = effect.model_copy(
                update={
                    "status": (
                        RuntimeEffectStatus.PENDING_RETRY
                        if retryable
                        else RuntimeEffectStatus.FAILED
                    ),
                    "attempt_count": next_attempt,
                    "available_at": (
                        utc_now() + timedelta(seconds=self.retry_delays_seconds[next_attempt - 1])
                        if retryable
                        else utc_now()
                    ),
                    "last_error": f"{type(exc).__name__}: {str(exc)[:500]}",
                    "updated_at": utc_now(),
                }
            )
            self.repository.save_effect(failed)
            if effect.effect_type is RuntimeSideEffect.ROUTE_TO_W3:
                self._record_w3_failure(effect.case_id, exc, retryable=retryable)
            self._refresh_case_completion(effect.case_id)
            return 0
        done = effect.model_copy(
            update={
                "status": RuntimeEffectStatus.COMPLETED,
                "attempt_count": effect.attempt_count + 1,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_effect(done)
        self._refresh_case_completion(effect.case_id)
        return 1

    def _execute_effect(self, effect: RuntimeEffect) -> None:
        case = self.repository.get_case(effect.case_id)
        if case is None or case.w1_final is None or case.w2_final is None or case.route is None:
            raise RuntimeInputUnavailable("case_unavailable", "Routed Runtime Case is unavailable")
        if effect.effect_type is RuntimeSideEffect.EMIT_DELTA:
            self._execute_r3(case, effect)
            return
        if effect.effect_type is RuntimeSideEffect.ARCHIVE_MESSAGE:
            self.repository.save_archive(
                ArchiveRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    source_message_id=case.source.source_message_id,
                    reason=case.route.reason,
                )
            )
            return
        if effect.effect_type is RuntimeSideEffect.CREATE_TRADE_RECORD:
            if not case.w2_final.policy_ids:
                raise RuntimeSemanticOutputError("TRADE effect requires a Policy hit")
            claimed = self._claim_first_policy(case, case.w2_final)
            if claimed is None:
                self._resolve_consumed_policy_race(case)
                return
            executed, activation, matched_condition_ids, decision = claimed
            self.repository.save_trade(
                TradeRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    executed_policy_id=executed,
                    activation_revision=activation.activation_revision,
                    matched_condition_ids=matched_condition_ids,
                    candidate_policy_ids=case.w2_final.policy_ids,
                    policy_set_version=case.version_pin.policy_set_version,
                    decision=decision,
                    w1_result=case.w1_final,
                    w2_result=case.w2_final,
                )
            )
            return
        if effect.effect_type is RuntimeSideEffect.MARK_BADCASE:
            self.repository.save_badcase(
                BadcaseRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    matched_known_event_ids=case.w1_final.reference_ids,
                    hit_policy_ids=case.w2_final.policy_ids,
                    event_library_version=case.version_pin.event_library_version,
                    policy_set_version=case.version_pin.policy_set_version,
                    w1_reason=case.w1_final.reason,
                    w2_reason=case.w2_final.reason,
                )
            )
            return
        if effect.effect_type is RuntimeSideEffect.ROUTE_TO_W3:
            self._execute_w3(case)
            return
        raise ValueError(f"Unsupported Runtime V2 effect: {effect.effect_type}")

    def _execute_w3(self, case: RuntimeCase) -> None:
        if self._w3_agent is None:
            raise RuntimeInputUnavailable(
                "w3_agent_unavailable",
                "W3 route requires a configured Codex W3 Agent",
            )
        existing = self.repository.get_w3_case(case.case_id)
        mode = self._w3_mode(case)
        w3_case = existing or W3RouteCase(
            case_id=case.case_id,
            ticker=case.ticker,
            source=case.source,
            w1_final=case.w1_final,  # type: ignore[arg-type]
            w2_final=case.w2_final,  # type: ignore[arg-type]
            event_library_version=case.version_pin.event_library_version,
            policy_set_version=case.version_pin.policy_set_version,
            route_reason=case.route.reason,  # type: ignore[union-attr]
            mode=mode,
        )
        running = w3_case.model_copy(
            update={
                "status": W3CaseStatus.RUNNING,
                "attempt_count": w3_case.attempt_count + 1,
                "error_code": None,
                "error_message": None,
                "updated_at": utc_now(),
            }
        )
        self.repository.save_w3_case(running)
        slot = self.repository.acquire_w3_slot(
            ticker=case.ticker,
            case_id=case.case_id,
            max_concurrency=self._w3_max_ticker_concurrency,
            lease_seconds=self._w3_lease_seconds,
        )
        if slot is None:
            raise W3Error(
                "w3_ticker_concurrency_busy",
                f"{case.ticker} already has five active W3 turns",
            )
        returned_thread: str | None = None
        clear_main = False
        try:
            result, returned_thread, context_pin = self._w3_agent.run(
                case=case,
                w3_case=running,
                slot=slot,
            )
            resolved = route_w3_result(result)
            resolved = self._apply_w3_result(case, running, result) or resolved
            self.repository.save_w3_case(
                running.model_copy(
                    update={
                        "status": W3CaseStatus.RESOLVED,
                        "context_version_pin": context_pin,
                        "result": result,
                        "resolved_route": resolved,
                        "thread_kind": slot.kind,
                        "thread_id": returned_thread,
                        "updated_at": utc_now(),
                    }
                )
            )
            self.repository.save_case(
                case.model_copy(
                    update={
                        "w3_result": result,
                        "resolved_route": resolved,
                        "updated_at": utc_now(),
                    }
                )
            )
        except W3Error as exc:
            clear_main = slot.kind is W3ThreadKind.MAIN and exc.invalid_thread
            raise
        finally:
            self.repository.release_w3_slot(
                slot,
                thread_id=(returned_thread if slot.kind is W3ThreadKind.MAIN else None),
                clear_main_thread=clear_main,
            )

    def _apply_w3_result(
        self,
        case: RuntimeCase,
        w3_case: W3RouteCase,
        result: W3CaseResult,
    ) -> RuntimeRouteDecision | None:
        if result.novelty.result is W1NoveltyVerdict.OLD:
            self.repository.save_archive(
                ArchiveRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    source_message_id=case.source.source_message_id,
                    reason=result.novelty.reason,
                )
            )
            if result.policy.policy_ids:
                self.repository.save_badcase(
                    BadcaseRecord(
                        case_id=case.case_id,
                        ticker=case.ticker,
                        trading_date=case.trading_date,
                        source=case.source,
                        matched_known_event_ids=result.novelty.reference_ids,
                        hit_policy_ids=result.policy.policy_ids,
                        event_library_version=case.version_pin.event_library_version,
                        policy_set_version=case.version_pin.policy_set_version,
                        w1_reason=result.novelty.reason,
                        w2_reason=result.policy.reason,
                    )
                )
            return None
        claimed = None
        if result.policy.policy_ids:
            claimed = self._claim_first_policy(case, result.policy)
            if claimed is None:
                self.repository.save_archive(
                    ArchiveRecord(
                        case_id=case.case_id,
                        ticker=case.ticker,
                        source_message_id=case.source.source_message_id,
                        reason="policy_activation_already_consumed",
                    )
                )
                return RuntimeRouteDecision(
                    primary_route=RuntimePrimaryRoute.ARCHIVE,
                    side_effects=[RuntimeSideEffect.ARCHIVE_MESSAGE],
                    reason="policy_activation_already_consumed",
                )
        maximum = self.known_events.max_event_numeric_id(
            case.ticker,
            case.version_pin.event_library_version,
        )
        if maximum is None:
            raise RuntimeInputUnavailable(
                "event_library_max_id_unavailable",
                "Published Event maximum ID is unavailable",
            )
        for index, candidate in enumerate(result.delta_candidates):
            self.repository.allocate_provisional(
                ticker=case.ticker,
                trading_date=case.trading_date,
                source_message_id=case.source.source_message_id,
                candidate_index=index,
                candidate=candidate,
                published_max_event_numeric_id=maximum,
            )
        if claimed is not None:
            executed, activation, matched_condition_ids, decision = claimed
            self.repository.save_trade(
                TradeRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    decision_origin=TradeDecisionOrigin.POLICY,
                    executed_policy_id=executed,
                    activation_revision=activation.activation_revision,
                    matched_condition_ids=matched_condition_ids,
                    candidate_policy_ids=result.policy.policy_ids,
                    policy_set_version=case.version_pin.policy_set_version,
                    decision=decision,
                    w1_result=case.w1_final,  # type: ignore[arg-type]
                    w2_result=case.w2_final,  # type: ignore[arg-type]
                    w3_result=result,
                )
            )
            return None
        self.repository.save_w3_coverage_gap(
            W3CoverageGapRecord(
                case_id=case.case_id,
                w3_case_id=w3_case.w3_case_id,
                ticker=case.ticker,
                trading_date=case.trading_date,
                source=case.source,
                policy_set_version=case.version_pin.policy_set_version,
                result=result,
            )
        )
        if result.expert_trade.trade:
            if result.expert_trade.direction is None:
                raise RuntimeSemanticOutputError("W3 direct trade is missing direction")
            self.repository.save_trade(
                TradeRecord(
                    case_id=case.case_id,
                    ticker=case.ticker,
                    trading_date=case.trading_date,
                    source=case.source,
                    decision_origin=TradeDecisionOrigin.W3,
                    w3_case_id=w3_case.w3_case_id,
                    candidate_policy_ids=[],
                    policy_set_version=case.version_pin.policy_set_version,
                    decision=result.expert_trade.direction,
                    w1_result=case.w1_final,  # type: ignore[arg-type]
                    w2_result=case.w2_final,  # type: ignore[arg-type]
                    w3_result=result,
                )
            )
        return None

    def _claim_first_policy(
        self,
        case: RuntimeCase,
        result: W2PolicyResult | W3PolicyResult,
    ) -> tuple[str, RuntimePolicyRecord, list[str], PolicyDecision] | None:
        attribution = {item.policy_id: item.condition_ids for item in result.matched_condition_ids}
        for policy_id in result.policy_ids:
            activation = self.policies.activation(
                case.ticker,
                case.version_pin.policy_set_version,
                policy_id,
            )
            decision = self.policies.decision(
                case.ticker,
                case.version_pin.policy_set_version,
                policy_id,
            )
            if activation is None or decision is None:
                raise RuntimeInputUnavailable(
                    "executed_policy_unavailable",
                    "Executed Policy is unavailable in the pinned PolicySet",
                )
            valid_condition_ids = set(activation.condition_ids)
            matched = [
                item for item in attribution.get(policy_id, []) if item in valid_condition_ids
            ]
            record = PolicyActivationRecord(
                case_id=case.case_id,
                source_message_id=case.source.source_message_id,
                ticker=case.ticker,
                policy_id=policy_id,
                activation_revision=activation.activation_revision,
                policy_set_version=case.version_pin.policy_set_version,
                matched_condition_ids=matched,
            )
            if self.repository.claim_policy_activation(record):
                return policy_id, activation, matched, decision
        return None

    def _resolve_consumed_policy_race(self, case: RuntimeCase) -> None:
        resolved = RuntimeRouteDecision(
            primary_route=RuntimePrimaryRoute.ARCHIVE,
            side_effects=[RuntimeSideEffect.ARCHIVE_MESSAGE],
            reason="policy_activation_already_consumed",
        )
        self.repository.save_archive(
            ArchiveRecord(
                case_id=case.case_id,
                ticker=case.ticker,
                source_message_id=case.source.source_message_id,
                reason=resolved.reason,
            )
        )
        self.repository.save_case(
            case.model_copy(update={"resolved_route": resolved, "updated_at": utc_now()})
        )

    def _record_w3_failure(
        self,
        case_id: str,
        exc: Exception,
        *,
        retryable: bool,
    ) -> None:
        value = self.repository.get_w3_case(case_id)
        if value is None:
            return
        self.repository.save_w3_case(
            value.model_copy(
                update={
                    "status": (W3CaseStatus.FAILED_RETRYABLE if retryable else W3CaseStatus.FAILED),
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error_message": str(exc)[:1000],
                    "updated_at": utc_now(),
                }
            )
        )

    def _execute_r3(self, case: RuntimeCase, effect: RuntimeEffect) -> None:
        if (
            case.resolved_route is not None
            and case.resolved_route.primary_route.value == "ARCHIVE"
            and "policy_activation_already_consumed" in case.resolved_route.reason
        ):
            return
        mode = (
            W1CaptureMode.NEW_CAPTURE
            if case.w1_final and case.w1_final.result is W1NoveltyVerdict.NEW
            else W1CaptureMode.AMBIGUOUS_CAPTURE
        )
        extraction, _response_id = self._call_with_retry(
            case=case,
            lane="W1",
            round_name="R3",
            round_prompt=(
                f"{self.prompts.w1_r3}\n\n"
                "## Current Capture Mode\n\n"
                f"The runtime-selected capture mode for this turn is `{mode.value}`."
            ),
            payload={
                "source_message": case.source.snapshot.model_dump(mode="json"),
                "w1_final": _w1_final_business_payload(case.w1_final),
            },
            output_model=W1FactExtractionResult,
            schema_name="w1_fact_extraction_result",
            previous_response_id=str(effect.payload.get("previous_response_id") or "") or None,
        )
        maximum = self.known_events.max_event_numeric_id(
            case.ticker,
            case.version_pin.event_library_version,
        )
        if maximum is None:
            raise RuntimeInputUnavailable(
                "event_library_max_id_unavailable",
                "Published Event maximum ID is unavailable",
            )
        for index, candidate in enumerate(extraction.candidates):
            self.repository.allocate_provisional(
                ticker=case.ticker,
                trading_date=case.trading_date,
                source_message_id=case.source.source_message_id,
                candidate_index=index,
                candidate=candidate,
                published_max_event_numeric_id=maximum,
            )
        self.repository.save_case(
            case.model_copy(update={"w1_extraction": extraction, "updated_at": utc_now()})
        )

    def _refresh_case_completion(self, case_id: str) -> None:
        case = self.repository.get_case(case_id)
        if case is None:
            return
        effects = self.repository.list_effects(case_id)
        if any(effect.status is RuntimeEffectStatus.FAILED for effect in effects):
            status = RuntimeCaseStatus.FAILED
            technical = RuntimeTechnicalStatus.FAILED
        elif any(
            effect.status
            in {
                RuntimeEffectStatus.PENDING,
                RuntimeEffectStatus.PENDING_RETRY,
                RuntimeEffectStatus.RUNNING,
            }
            for effect in effects
        ):
            return
        elif case.route and case.route.primary_route.value == "W3" and case.resolved_route is None:
            status = RuntimeCaseStatus.PENDING_W3
            technical = RuntimeTechnicalStatus.OK
        else:
            status = RuntimeCaseStatus.COMPLETED
            technical = RuntimeTechnicalStatus.OK
        self.repository.save_case(
            case.model_copy(
                update={
                    "status": status,
                    "technical_status": technical,
                    "updated_at": utc_now(),
                }
            )
        )
        projected = self.repository.get_case(case_id)
        if projected is not None:
            self._project_terminal(projected)

    def _fail_case(self, case: RuntimeCase, exc: Exception) -> RuntimeCase:
        unavailable = isinstance(exc, RuntimeInputUnavailable) or (
            isinstance(exc, RuntimeResponsesError) and not exc.retryable
        )
        failed = case.model_copy(
            update={
                "status": (
                    RuntimeCaseStatus.UNAVAILABLE if unavailable else RuntimeCaseStatus.FAILED
                ),
                "technical_status": (
                    RuntimeTechnicalStatus.UNAVAILABLE
                    if unavailable
                    else RuntimeTechnicalStatus.FAILED
                ),
                "error_code": getattr(exc, "code", type(exc).__name__),
                "error_message": str(exc)[:1000],
                "updated_at": utc_now(),
            }
        )
        saved = self.repository.save_case(failed)
        self._project_terminal(saved)
        return saved

    def _project_terminal(self, case: RuntimeCase) -> None:
        if self._projection_outbox is None:
            return
        self._projection_outbox.enqueue_case(
            case,
            model_turn_count=len(self.repository.list_turns(case.case_id)),
        )
        self._effect_executor.submit(self._projection_outbox.flush, limit=20)
