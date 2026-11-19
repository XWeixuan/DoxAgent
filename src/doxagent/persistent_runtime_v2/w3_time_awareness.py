"""One real execution origin per Case; never use the journal's business clock."""

import json
import sqlite3
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock

from doxagent.codex_worker.schema import W3TimeAwarenessContext
from doxagent.control_io import diagnostic_warning

from .journal import RuntimeJournal

_CACHE_SIZE = 2048


def time_context(
    journal: RuntimeJournal, case_id: str, *, start: bool = False, saved=None
) -> W3TimeAwarenessContext | None:
    """Reuse frozen/memory origins; never acquire a writer just for Elapsed.

    The v6 registration owns durability. Compatibility/recovery may use a
    process-local lower bound rather than delaying Trade or resetting each branch.
    """
    started_at = datetime.now(UTC).isoformat() if start else None
    state = getattr(journal, "_w3_time_origins", None)
    if state is None:
        state = journal.__dict__.setdefault("_w3_time_origins", (OrderedDict(), Lock()))
    cache, lock = state

    def remember(value, *, authoritative=False):
        context = W3TimeAwarenessContext.model_validate(value)
        if context.case_id != case_id:
            raise ValueError("time origin belongs to another Case")
        with lock:
            if authoritative or case_id not in cache:
                cache[case_id] = context
            cache.move_to_end(case_id)
            result = cache[case_id]
            if len(cache) > _CACHE_SIZE:
                cache.popitem(last=False)
        return result

    if saved is not None:
        try:
            return remember(saved, authoritative=True)
        except (ValueError, TypeError):
            diagnostic_warning(__name__, "W3 elapsed invalid frozen origin case_id=%s", case_id)
    with lock:
        if case_id in cache:
            cache.move_to_end(case_id)
            return cache[case_id]
    try:
        # Only old/unseeded callers need this single non-waiting read. Normal v6
        # passes its already-loaded registration value and never reaches SQLite.
        with journal.transaction(write=False, operation="w3.time_origin.read", timeout=0) as db:
            row = db.execute(
                "SELECT payload FROM runtime_values WHERE namespace='w3_time_awareness' AND key=?",
                (case_id,),
            ).fetchone()
        if row is not None:
            return remember(json.loads(row[0]), authoritative=True)
    except (sqlite3.Error, ValueError, TypeError, OSError) as exc:
        diagnostic_warning(
            __name__, "W3 elapsed origin read degraded case_id=%s error=%s",
            case_id, type(exc).__name__,
        )
    if not start:
        return None
    context = remember(dict(case_id=case_id, started_at_utc=started_at))
    diagnostic_warning(
        __name__, "W3 elapsed process-local fallback case_id=%s started_at_utc=%s; "
        "prior elapsed may be unavailable", case_id, context.started_at_utc.isoformat(),
    )
    return context
