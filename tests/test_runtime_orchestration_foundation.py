from datetime import UTC, date, datetime, timedelta

import pytest

from doxagent.persistent_runtime_v2.calendar import MarketCalendar
from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
from doxagent.persistent_runtime_v2.journal import LeaseLost, RuntimeJournal
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.semantic_clock import boundary, semantic_day


def test_clock_dst_and_market_holidays(tmp_path):
    assert boundary(date(2026, 3, 8)) == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert semantic_day(datetime(2026, 3, 8, 6, 59, tzinfo=UTC)) == date(2026, 3, 7)
    assert semantic_day(boundary(date(2026, 11, 1))) == date(2026, 11, 1)
    calendar = MarketCalendar(RuntimeJournal(tmp_path / "state.db"))
    assert not calendar.is_session(date(2026, 9, 7))
    assert calendar.next_session(date(2026, 9, 4)) == date(2026, 9, 8)
    assert calendar.closed_cycle(date(2026, 9, 7)) == "2026-09-05"


def test_lease_recovery_fences_old_worker_and_preserves_budget(tmp_path):
    now = [datetime(2026, 9, 6, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "state.db", clock=lambda: now[0])
    journal.put_task("first", "MU", "CASE", {"source": "one"})
    first = journal.claim("first", seconds=10)
    journal.checkpoint(first, round1={"answer": "saved"})
    now[0] += timedelta(seconds=11)
    recovered = RuntimeJournal(journal.path, clock=lambda: now[0])
    second = recovered.claim("first")
    assert second["receipt"]["round1"] == {"answer": "saved"}
    assert second["failures"] == 0
    with pytest.raises(LeaseLost):
        journal.finish(first)
    recovered.fail(second, ValueError("bad"))
    now[0] += timedelta(seconds=6)
    recovered.fail(recovered.claim("first"), ValueError("bad again"))
    assert recovered.get_task("first")["status"] == "FAILED"
    assert recovered.claim("first") is None
    recovered.put_task("next", "MU", "CASE", {})
    recovered.finish(recovered.claim("next"))
    assert recovered.get_task("next")["status"] == "SUCCEEDED"
    recovered.resume("first", "fixed source")
    assert recovered.get_task("first")["generation"] == 2
    assert recovered.get_task("first")["receipt"]["round1"]["answer"] == "saved"


def test_hot_bundle_keeps_old_contents_after_new_publication(tmp_path):
    journal = RuntimeJournal(tmp_path / "state.db")
    bundles = ExecutionBundles(journal)
    prompts = RuntimeV2PromptSet.load()
    original = bundles.publish(prompts)
    newer = bundles.publish(RuntimeV2PromptSet(**{**vars(prompts), "core": "new core"}))
    assert bundles.get(original)["prompts"]["core"] == prompts.core
    assert bundles.get(newer)["prompts"]["core"] == "new core"
    assert journal.get("execution", "active") == newer
