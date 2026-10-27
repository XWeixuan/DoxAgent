"""Paid provider completion survives a failed SQL index/lease commit."""

import json
import os
from dataclasses import fields
from datetime import UTC, datetime

from doxagent.codex_worker.result_receipt import commit

from .journal import digest
from .transport import RuntimeResponsesError, RuntimeResponsesResult


def complete(journal, client, request, identity):
    if journal is None:
        return client.complete(request)
    from .fencing import assert_write_lease

    with journal.transaction(write=False, expand=False, operation="round.dispatch_fence") as db:
        assert_write_lease(db)
    fingerprint = digest(
        {
            f.name: request.output_model.model_json_schema()
            if f.name == "output_model"
            else getattr(request, f.name)
            for f in fields(request)
        }
    )
    path = journal.path.parent / "runtime-round-receipts" / (digest(identity) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    dispatch = dict(
        identity=identity,
        input_hash=fingerprint,
        status="DISPATCHED",
        provider_started_at=datetime.now(UTC).isoformat(),
        pid=os.getpid(),
    )
    try:
        with path.open("x", encoding="utf8") as file:
            json.dump(dispatch, file)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        try:
            previous = json.loads(path.read_text(encoding="utf8"))
        except ValueError as exc:
            raise RuntimeResponsesError(
                "provider_dispatch_unknown",
                "Original provider dispatch requires reconciliation",
                retryable=False,
            ) from exc
        if previous.get("input_hash") != fingerprint:
            raise ValueError("immutable provider attempt input conflict")
        if previous["status"] == "COMPLETED":
            data = previous["result"]
            return RuntimeResponsesResult(
                **{**data, "value": request.output_model.model_validate(data["value"])}
            )
        if previous["status"] == "FAILED":
            error = previous["error"]
            raise RuntimeResponsesError(
                error["code"],
                error["message"],
                retryable=error["retryable"],
                usage=error.get("usage"),
            )
        raise RuntimeResponsesError(
            "provider_dispatch_unknown",
            "Original provider request may still be running; do not redispatch",
            retryable=False,
        )
    try:
        result = client.complete(request)
    except RuntimeResponsesError as exc:
        commit(
            path,
            {
                **dispatch,
                "status": "FAILED",
                "error": dict(
                    code=exc.code, message=str(exc), retryable=exc.retryable, usage=exc.usage
                ),
            },
        )
        raise
    data = {f.name: getattr(result, f.name) for f in fields(result)}
    data["value"] = result.value.model_dump(mode="json")
    # Completion fact is not a business publish, so it intentionally bypasses
    # the expired owner fence. A new owner still fences the SQL/business commit.
    commit(
        path,
        {
            **dispatch,
            "status": "COMPLETED",
            "provider_completed_at": datetime.now(UTC).isoformat(),
            "result": data,
        },
    )
    return result


def completion_times(journal, identity):
    if journal is None:
        return None
    path = journal.path.parent / "runtime-round-receipts" / (digest(identity) + ".json")
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf8"))
    if value.get("status") != "COMPLETED":
        return None
    return datetime.fromisoformat(value["provider_started_at"]), datetime.fromisoformat(
        value["provider_completed_at"]
    )
