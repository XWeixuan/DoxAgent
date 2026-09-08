from decimal import Decimal

from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.repository import ReadStore


def test_late_correction_moves_bucket_and_stale_repair_does_not_regress(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    item = {
        "metric": "cost",
        "ticker": "MU",
        "entity": "call",
        "day": "2026-09-01",
        "value": "0.1234567890123456789",
    }
    before = store.ingest("native", "1", [], position=1, contributions=[item])
    after = store.ingest(
        "native", "3", [], position=3, contributions=[{**item, "day": "2026-09-02", "value": "0.2"}]
    )
    repaired = store.ingest(
        "native", "2:repair", [], position=2, contributions=[{**item, "value": "99999"}]
    )
    metrics = Metrics(store)
    assert metrics.value("cost", ["MU"], before, days=None) == Decimal(item["value"])
    assert metrics.value("cost", ["MU"], after, days=["2026-09-01"]) == 0
    assert metrics.value("cost", ["MU"], repaired, days=None) == Decimal("0.2")
    assert metrics.value("cost", ["MU"], repaired, days=["2026-09-02"]) == Decimal("0.2")
    seq = store.ingest("native", "4", [], position=4, contributions=[{**item, "value": "0"}])
    assert metrics.value("cost", ["MU"], seq, days=None, distinct=True) == 0


def test_unsupported_read_schema_is_rejected_before_creating_tables(tmp_path):
    import sqlite3

    import pytest

    path = tmp_path / "read.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_meta(version INTEGER)")
        db.execute("INSERT INTO schema_meta VALUES(999)")
    with pytest.raises(ValueError, match="unsupported"):
        ReadStore(path).migrate()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("schema_meta",)
        ]
