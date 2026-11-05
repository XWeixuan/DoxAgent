"""One real execution origin per Case; never use the journal's business clock."""

import logging
import sqlite3
from datetime import UTC, datetime

from doxagent.codex_worker.schema import W3TimeAwarenessContext

from .journal import RuntimeJournal, encode

_log = logging.getLogger(__name__)


def time_context(
    journal: RuntimeJournal, case_id: str, *, start: bool = False
) -> W3TimeAwarenessContext | None:
    started_at = datetime.now(UTC).isoformat() if start else None
    try:
        saved = journal.get("w3_time_awareness", case_id)
        if saved is None and start:
            value = dict(case_id=case_id, started_at_utc=started_at)
            with journal.transaction(operation="w3.time_origin", timeout=0) as db:
                db.execute(
                    "INSERT OR IGNORE INTO runtime_values(namespace,key,payload) VALUES(?,?,?)",
                    ("w3_time_awareness", case_id, encode(value)),
                )
            saved = journal.get("w3_time_awareness", case_id)
        return W3TimeAwarenessContext.model_validate(saved) if saved is not None else None
    except sqlite3.Error:
        # Optional elapsed telemetry must not replace the business outcome.
        _log.warning("W3 time origin unavailable for %s", case_id, exc_info=True)
        return None
