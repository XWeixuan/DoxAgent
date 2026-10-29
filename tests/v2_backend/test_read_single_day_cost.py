from contextlib import contextmanager

from doxagent.v2_read.repository import ReadStore


def test_sparse_day_page_does_not_walk_other_days_and_preserves_snapshot(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    old = [dict(kind="case", ticker="INTC", id=f"old-{i}", day="2026-10-28",
                sort=f"2026-10-28:{i:04}", data={"title": "old"}) for i in range(2000)]
    recent = [dict(kind="case", ticker="INTC", id=f"new-{i}", day="2026-10-29",
                   sort=f"2026-10-29:{i}", data={"title": "new"}) for i in range(4)]
    before = store.ingest("runtime", "one", old + recent)
    after = store.ingest("runtime", "two", [{**recent[-1], "data": {"title": "updated"}}])
    original = store.connect

    @contextmanager
    def bounded(**kwargs):
        with original(**kwargs) as db:
            steps = 0

            def budget():
                nonlocal steps
                steps += 100
                return steps > 2500

            db.set_progress_handler(budget, 100)
            yield db

    store.connect = bounded
    head = store.page("case", "INTC", after, days=["2026-10-29"])
    assert [r["id"] for r in head] == ["new-3", "new-2", "new-1", "new-0"]
    assert head[0]["data"]["title"] == "updated"
    snapshot = store.page("case", "INTC", before, days=["2026-10-29"], limit=2)
    assert snapshot[0]["data"]["title"] == "new"
    assert [r["id"] for r in store.page("case", "INTC", before, days=["2026-10-29"],
            after=(snapshot[-1]["sort"], snapshot[-1]["id"]), limit=2)] == ["new-1", "new-0"]
