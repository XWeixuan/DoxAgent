"""Optional per-process observer for actual provider calls, independent of orchestration."""

from datetime import UTC, datetime
from uuid import uuid4

observer = None


def notify(callback, value):
    import logging

    try:
        callback(value)
    except Exception:
        # Observation failures must not discard a paid provider response or trigger paid retries.
        logging.getLogger(__name__).error(
            "V2_USAGE_CAPTURE_FAILED invocation=%s", value["invocation_id"]
        )


def receipt(identity, provider, node, model, started, response=None, error=None):
    usage = getattr(response, "usage", None)
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}
    incoming = usage.get("input_tokens", usage.get("prompt_tokens"))
    outgoing = usage.get("output_tokens", usage.get("completion_tokens"))
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
    return {
        "invocation_id": identity,
        "scope": "API",
        "provider": provider,
        "node": node,
        "model": model,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "FAILED" if error else "SUCCEEDED",
        "error_code": type(error).__name__ if error else None,
        "usage": {
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "cached_input_tokens": details.get("cached_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }


def call(function, *, _usage_provider, _usage_node, **kwargs):
    if observer is None:
        return function(**kwargs)
    identity, started = uuid4().hex, datetime.now(UTC).isoformat()
    callback = observer
    initial = receipt(
        identity, _usage_provider, _usage_node, kwargs.get("model", "unknown"), started
    )
    notify(callback, {**initial, "status": "STARTED", "finished_at": None})
    response, error = None, None
    try:
        response = function(**kwargs)
        return response
    except BaseException as exc:
        error = exc
        raise
    finally:
        notify(
            callback,
            receipt(
                identity,
                _usage_provider,
                _usage_node,
                kwargs.get("model", "unknown"),
                started,
                response,
                error,
            ),
        )


async def acall(function, *, _usage_provider, _usage_node, **kwargs):
    if observer is None:
        return await function(**kwargs)
    identity, started = uuid4().hex, datetime.now(UTC).isoformat()
    callback = observer
    initial = receipt(
        identity, _usage_provider, _usage_node, kwargs.get("model", "unknown"), started
    )
    notify(callback, {**initial, "status": "STARTED", "finished_at": None})
    response, error = None, None
    try:
        response = await function(**kwargs)
        return response
    except BaseException as exc:
        error = exc
        raise
    finally:
        notify(
            callback,
            receipt(
                identity,
                _usage_provider,
                _usage_node,
                kwargs.get("model", "unknown"),
                started,
                response,
                error,
            ),
        )
