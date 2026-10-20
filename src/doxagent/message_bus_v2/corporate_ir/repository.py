"""Endpoint state participates in the Bus owner's intake transaction."""

from ..schema import PollStatus
from .schema import IrEndpointState, endpoints


def get_state(repo, binding_id, endpoint_id):
    with repo._connect() as db:
        row = db.execute(
            "SELECT data_json FROM ir_endpoint_states WHERE id=?", (binding_id + ":" + endpoint_id,)
        ).fetchone()
    return (
        IrEndpointState.model_validate_json(row[0])
        if row
        else IrEndpointState(binding_id=binding_id, endpoint_id=endpoint_id)
    )


def save_state(repo, state):
    with repo.transaction() as db:
        db.execute(
            "INSERT INTO ir_endpoint_states VALUES(?,?,?,?) ON CONFLICT(id) "
            "DO UPDATE SET data_json=excluded.data_json",
            (
                state.binding_id + ":" + state.endpoint_id,
                state.binding_id,
                state.endpoint_id,
                state.model_dump_json(),
            ),
        )


def aggregate(repo, source, binding, now, execution=None):
    specs = [s for s in endpoints(source, binding) if s.enabled]
    states = [get_state(repo, binding.binding_id, s.endpoint_id) for s in specs]
    old = repo.get_poll_state(binding)
    healthy = [
        s
        for s in states
        if s.status == "succeeded"
        and not s.checkpoint.get("coverage", "").startswith("PARTIAL")
        and s.last_success_at
        and (now - s.last_success_at).total_seconds() < 90
    ]
    status = (
        PollStatus.SUCCEEDED
        if states and len(healthy) == len(states)
        else PollStatus.PARTIAL
        if healthy or any(s.status == "succeeded" for s in states)
        else PollStatus.FAILED
        if any(s.last_attempt_at for s in states)
        else PollStatus.NEVER_POLLED
    )
    failures = [s for s in states if s.last_error_code]
    gaps = [s for s in states if s.checkpoint.get("coverage", "").startswith("PARTIAL")]
    successes = [s.last_success_at for s in states if s.last_success_at]
    dues = [s.next_due_at for s in states if s.next_due_at]
    dispatches = [s.next_dispatch_at for s in states if s.next_dispatch_at]
    repo.save_poll_state(
        old.model_copy(
            update={
                "status": status,
                "bootstrap_complete": bool(states) and all(s.bootstrap_complete for s in states),
                "checkpoint": {
                    "ir_endpoints": [
                        {
                            **s.model_dump(
                                mode="json",
                                exclude={
                                    "checkpoint",
                                    "etag",
                                    "last_modified",
                                    "representation_hash",
                                    "config_revision",
                                },
                            ),
                            "display_name": spec.display_name,
                            "entry_url": spec.entry_url,
                            "last_error_message": s.last_error_message
                            or s.checkpoint.get("coverage")
                            if s in gaps
                            else s.last_error_message,
                        }
                        for s, spec in zip(states, specs, strict=True)
                    ]
                },
                "last_attempt_at": max(
                    (s.last_attempt_at for s in states if s.last_attempt_at), default=None
                ),
                "last_success_at": min(successes)
                if len(successes) == len(states) and states
                else None,
                "last_failure_at": max(
                    (s.last_attempt_at for s in failures if s.last_attempt_at), default=None
                ),
                "last_error_code": "IR_ENDPOINT_FAILURE"
                if failures
                else "IR_COVERAGE_GAP"
                if gaps
                else None,
                "last_error_message": "; ".join(
                    s.endpoint_id + ": " + str(s.last_error_message or s.checkpoint.get("coverage"))
                    for s in [*failures, *gaps]
                )[:1000]
                or None,
                "target_due_at": min(dues) if dues else None,
                "next_dispatch_at": min(dispatches) if dispatches else None,
                "last_latency_ms": max((s.latency_ms for s in states), default=0),
                "consecutive_failures": max((s.consecutive_failures for s in states), default=0),
                "collected_count": old.collected_count
                + (execution.collected_count if execution else 0),
                "filtered_count": old.filtered_count
                + (execution.filtered_count if execution else 0),
                "updated_at": now,
            }
        )
    )
