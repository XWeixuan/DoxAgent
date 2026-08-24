"""Failure semantics for Document2 worker turns.

The workflow deliberately separates request/runtime faults from degradable
research-turn failures.  Only the latter may become a partial shell outcome.
"""

from __future__ import annotations

from enum import StrEnum

from doxagent.codex_runtime.schema import CodexD2Node
from doxagent.codex_worker.schema import WorkerJob


class Document2FailureKind(StrEnum):
    SYSTEM = "SYSTEM"
    TRANSIENT = "TRANSIENT"
    FORMAT = "FORMAT"
    SHELL = "SHELL"


class Document2ExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        kind: Document2FailureKind,
        node: CodexD2Node,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.node = node
        self.retryable = retryable

    @property
    def allows_partial(self) -> bool:
        return self.kind is not Document2FailureKind.SYSTEM


_SYSTEM_MARKERS = (
    "invalid_json_schema",
    "invalid_request",
    "output_schema",
    "response_format",
    "authentication",
    "unauthorized",
    "permission_denied",
    "permission denied",
    "api key",
    "model_not_found",
    "model not found",
    "unsupported model",
)
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "rate_limit",
    "rate limit",
    "too many requests",
    "service unavailable",
    "temporarily unavailable",
    "connection",
    "network",
    "worker_restarted",
)


def worker_execution_error(job: WorkerJob, node: CodexD2Node) -> Document2ExecutionError:
    code = job.error_code or "D2_WORKER_TURN_FAILED"
    message = job.error_message or "Codex worker returned no structured output"
    normalized = f"{code} {message}".lower()
    if any(marker in normalized for marker in _SYSTEM_MARKERS):
        kind = Document2FailureKind.SYSTEM
        retryable = False
    elif any(marker in normalized for marker in _TRANSIENT_MARKERS):
        kind = Document2FailureKind.TRANSIENT
        retryable = True
    else:
        kind = Document2FailureKind.SHELL
        retryable = True
    return Document2ExecutionError(
        message,
        code=code,
        kind=kind,
        node=node,
        retryable=retryable,
    )


def raised_worker_error(exc: Exception, node: CodexD2Node) -> Document2ExecutionError:
    code = str(getattr(exc, "code", type(exc).__name__))
    message = str(exc) or type(exc).__name__
    normalized = f"{code} {message}".lower()
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        marker in normalized for marker in _TRANSIENT_MARKERS
    ):
        kind = Document2FailureKind.TRANSIENT
        retryable = True
    else:
        kind = Document2FailureKind.SYSTEM
        retryable = False
    return Document2ExecutionError(
        message,
        code=code,
        kind=kind,
        node=node,
        retryable=retryable,
    )


def format_execution_error(exc: Exception, node: CodexD2Node) -> Document2ExecutionError:
    return Document2ExecutionError(
        str(exc) or type(exc).__name__,
        code="D2_OUTPUT_FORMAT_INVALID",
        kind=Document2FailureKind.FORMAT,
        node=node,
        retryable=True,
    )
