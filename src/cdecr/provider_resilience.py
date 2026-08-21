"""Shared provider failure classification, retry policy, and key-health state."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from time import time


class ModelFailureClass(StrEnum):
    OUTPUT_LOCAL_INVALID = "OUTPUT_LOCAL_INVALID"
    OUTPUT_WHOLE_INVALID = "OUTPUT_WHOLE_INVALID"
    PROVIDER_THROTTLED = "PROVIDER_THROTTLED"
    KEY_AUTH = "KEY_AUTH"
    KEY_ARREARAGE = "KEY_ARREARAGE"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    REQUEST_CONTRACT_INVALID = "REQUEST_CONTRACT_INVALID"
    UNKNOWN_PROVIDER_FAILURE = "UNKNOWN_PROVIDER_FAILURE"


class ProviderCircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN_1 = "OPEN_1"
    HALF_OPEN = "HALF_OPEN"
    OPEN_2 = "OPEN_2"
    RECOVERING = "RECOVERING"
    BALANCE_BLOCKED = "BALANCE_BLOCKED"


class ProviderBalanceBlockedError(RuntimeError):
    code = "provider_arrearage"
    status_code = 402

    def __init__(self) -> None:
        super().__init__("provider balance/auth is blocked for this process")


def _exception_chain(exc: Exception) -> tuple[Exception, ...]:
    chain: list[Exception] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while isinstance(current, Exception) and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return tuple(chain)


def classify_provider_error(exc: Exception) -> ModelFailureClass:
    chain = _exception_chain(exc)
    codes = [str(getattr(item, "code", "")).casefold() for item in chain]
    statuses = [getattr(item, "status_code", None) for item in chain]
    provider_codes = [
        str(body.get("code", "")).casefold()
        for item in chain
        if isinstance((body := getattr(item, "body", None)), Mapping)
    ]
    texts = [f"{type(item).__name__}:{item}".casefold() for item in chain]
    combined = ":".join([*codes, *provider_codes, *texts])
    if any(
        token in combined
        for token in (
            "invalid_json",
            "schema_validation",
            "invalid_tool",
            "structured_output_invalid",
            "post_validation",
            "coverage",
            "invalid_task",
        )
    ):
        return ModelFailureClass.OUTPUT_WHOLE_INVALID
    if "arrearage" in combined or "insufficient_balance" in combined:
        return ModelFailureClass.KEY_ARREARAGE
    if any(status in {401, 403} for status in statuses) or any(
        token in combined for token in ("unauthorized", "forbidden", "invalid_api_key")
    ):
        return ModelFailureClass.KEY_AUTH
    if any(status == 429 for status in statuses) or any(
        token in combined for token in ("429", "rate_limit", "quota", "overload")
    ):
        return ModelFailureClass.PROVIDER_THROTTLED
    if any(
        status in {408, 425} or (isinstance(status, int) and 500 <= status <= 599)
        for status in statuses
    ) or any(
        token in combined
        for token in ("timeout", "connection", "reset", "5xx", "service_unavailable")
    ):
        return ModelFailureClass.PROVIDER_TRANSIENT
    if any(status == 400 for status in statuses) or any(
        token in combined for token in ("invalid_request", "invalid_parameter", "context_exceeded")
    ):
        return ModelFailureClass.REQUEST_CONTRACT_INVALID
    # Plain local ValueError/Pydantic-style validation failures carry no HTTP
    # status or provider error code.  They must remain item-local instead of
    # triggering provider retry, key rotation, or batch fan-out.
    if (
        isinstance(exc, ValueError)
        and not any(codes)
        and not any(status is not None for status in statuses)
        and not any(provider_codes)
    ):
        return ModelFailureClass.OUTPUT_LOCAL_INVALID
    if any(codes) or any(status is not None for status in statuses) or any(provider_codes):
        return ModelFailureClass.UNKNOWN_PROVIDER_FAILURE
    return ModelFailureClass.OUTPUT_LOCAL_INVALID


def is_provider_failure(exc: Exception) -> bool:
    """Return whether an exception represents a request/provider failure.

    Output parsing and business validation failures are deliberately excluded so
    callers can retain legal items and repair only the defective local payload.
    """

    return classify_provider_error(exc) not in {
        ModelFailureClass.OUTPUT_LOCAL_INVALID,
        ModelFailureClass.OUTPUT_WHOLE_INVALID,
    }


def is_retryable_provider_failure(exc: Exception) -> bool:
    return classify_provider_error(exc) in {
        ModelFailureClass.PROVIDER_THROTTLED,
        ModelFailureClass.PROVIDER_TRANSIENT,
    }


def is_deferred_retry_provider_failure(exc: Exception) -> bool:
    """Return whether N9 may make one node-end retry wave for this failure.

    Unknown provider failures are deliberately excluded from request-local
    retries and Circuit pressure, but one bounded N9 retry is safe after the
    first wave has completely drained.
    """

    return classify_provider_error(exc) in {
        ModelFailureClass.PROVIDER_THROTTLED,
        ModelFailureClass.PROVIDER_TRANSIENT,
        ModelFailureClass.UNKNOWN_PROVIDER_FAILURE,
    }


def is_provider_pressure(exc: Exception) -> bool:
    """Only real rate/overload signals may reduce shared request capacity."""

    return classify_provider_error(exc) is ModelFailureClass.PROVIDER_THROTTLED


def provider_retry_delay(attempt: int) -> float:
    """Bounded logical-request retry schedule; attempt is one-based."""

    return (1.0, 3.0, 8.0)[min(max(1, attempt), 3) - 1]


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class ProviderKeyHealthRegistry:
    """Persistent key quarantine; stores only fingerprints, never credentials."""

    def __init__(
        self,
        *,
        quarantine_seconds: int = 14_400,
        state_path: Path | None = Path(".tmp/cdecr/provider_key_health.json"),
        enabled: bool = True,
    ) -> None:
        self.quarantine_seconds = max(60, quarantine_seconds)
        self.state_path = state_path
        self.enabled = enabled
        self._lock = threading.Lock()
        self._failures: dict[str, list[tuple[float, ModelFailureClass]]] = {}
        self._frozen_until: dict[str, float] = {}
        if self.enabled:
            self._load()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        now = time()
        frozen = payload.get("frozen_until") if isinstance(payload, dict) else None
        if isinstance(frozen, dict):
            self._frozen_until = {
                str(key): float(value)
                for key, value in frozen.items()
                if isinstance(value, (int, float)) and float(value) > now
            }
        failures = payload.get("arrearage_failures") if isinstance(payload, dict) else None
        if isinstance(failures, dict):
            for fingerprint, timestamps in failures.items():
                if not isinstance(timestamps, list):
                    continue
                recent = [
                    (float(value), ModelFailureClass.KEY_ARREARAGE)
                    for value in timestamps
                    if isinstance(value, (int, float)) and now - float(value) <= 600
                ]
                if recent:
                    self._failures[str(fingerprint)] = recent

    def _persist_locked(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "version": 1,
            "frozen_until": dict(sorted(self._frozen_until.items())),
            "arrearage_failures": {
                fingerprint: [
                    timestamp
                    for timestamp, kind in history
                    if kind is ModelFailureClass.KEY_ARREARAGE
                ]
                for fingerprint, history in sorted(self._failures.items())
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self.state_path)
        except OSError:
            # Key-health persistence is operational telemetry. A local disk issue
            # must not turn a provider request into a workflow failure.
            return

    def healthy(self, key: str) -> bool:
        if not self.enabled:
            return True
        now = time()
        with self._lock:
            return self._frozen_until.get(key_fingerprint(key), 0.0) <= now

    def ordered(self, keys: Sequence[str]) -> tuple[str, ...]:
        return tuple(key for key in keys if self.healthy(key))

    def record_failure(self, key: str, failure: ModelFailureClass) -> None:
        if not self.enabled:
            return
        fingerprint = key_fingerprint(key)
        now = time()
        with self._lock:
            history = [item for item in self._failures.get(fingerprint, []) if now - item[0] <= 600]
            history.append((now, failure))
            self._failures[fingerprint] = history
            arrearage_count = sum(item[1] is ModelFailureClass.KEY_ARREARAGE for item in history)
            if failure is ModelFailureClass.KEY_AUTH or arrearage_count >= 3:
                self._frozen_until[fingerprint] = now + self.quarantine_seconds
            self._persist_locked()

    def record_success(self, key: str) -> None:
        if not self.enabled:
            return
        fingerprint = key_fingerprint(key)
        with self._lock:
            history = self._failures.get(fingerprint, [])
            self._failures[fingerprint] = [
                item for item in history if item[1] is ModelFailureClass.KEY_ARREARAGE
            ]
            self._persist_locked()

    def clear(self, key: str | None = None) -> None:
        """Operationally clear one fingerprint or all quarantine state."""

        with self._lock:
            if key is None:
                self._failures.clear()
                self._frozen_until.clear()
            else:
                fingerprint = key_fingerprint(key)
                self._failures.pop(fingerprint, None)
                self._frozen_until.pop(fingerprint, None)
            self._persist_locked()

    def snapshot(self) -> dict[str, dict[str, object]]:
        now = time()
        with self._lock:
            return {
                fingerprint: {
                    "frozen": until > now,
                    "frozen_for_seconds": max(0.0, until - now),
                }
                for fingerprint, until in self._frozen_until.items()
            }


DEFAULT_KEY_HEALTH = ProviderKeyHealthRegistry()
