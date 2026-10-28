import json
from datetime import datetime, timedelta, timezone


def test_hourly_archive_preserves_counts_without_fabricating_percentiles():
    from doxagent.message_bus_v2.cadence import cadence
    from doxagent.message_bus_v2.collection_plane.observability import _include_archives
    start=datetime(2026,10,20,tzinfo=timezone.utc);end=start+timedelta(hours=1)
    schedule={'run_id':'schedule','binding_id':'b','unit':'HEAD','status':'SCHEDULE',
              'reason':'ENABLED','dispatch_at':start.isoformat(),'due_at':start.isoformat(),'interval_seconds':30}
    result=cadence([schedule],[],start,end)
    prior={**result['units'][0],'dispatched_cycles':120,'request_cycles':80,'fresh_cycles':40,
           'statuses':{'CAPTURED':40,'FAILED':80}}
    output=_include_archives(result,[{'data_json':json.dumps({'units':[prior]})}])
    assert output['expected_cycles']==120 and output['fresh_cycles']==40
    assert output['missing_cycles']==0 and output['all_cycle_coverage']==1/3
    assert output['units'][0]['statuses']=={'CAPTURED':40,'FAILED':80}
    assert output['fresh_gap_seconds']['p95'] is None


async def test_hundred_unchanged_lists_do_not_write_business_or_native(tmp_path):
    from tests.test_message_collection_plane import setup_plane
    from doxagent.message_bus_v2.collection_plane.bridge import CaptureBridge

    repo, bus, collector, adapter, store = setup_plane(tmp_path)
    await collector.refresh(seeds=True)
    source, targets, unit, options = next(iter(collector.desired.values()))
    bridge = CaptureBridge(bus, store=store)
    await collector.acquire(source, targets, unit, options)
    assert await bridge.once() == 1
    before = {str(p) for p in repo.content.files.root.rglob("*") if p.is_file()}
    with repo._connect() as db:
        original = dict(db.execute("SELECT article_key,input_revision FROM live_articles"))
        counts = {
            t: db.execute("SELECT count(*) FROM " + t).fetchone()[0]
            for t in ("live_articles", "live_deliveries", "live_origins", "collection_acceptances")
        }
    for _ in range(100):
        await collector.acquire(source, targets, unit, options)
        assert await bridge.once() == 0
    with repo._connect() as db:
        assert dict(db.execute("SELECT article_key,input_revision FROM live_articles")) == original
        assert {t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in counts} == counts
    assert {str(p) for p in repo.content.files.root.rglob("*") if p.is_file()} == before
    assert adapter.calls == 101


def test_96h_equivalent_retention_preserves_failures_and_bounds_growth(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    from doxagent.message_bus_v2.collection_plane.maintenance import maintain

    store = AcquisitionStore(tmp_path / "acquisition.sqlite3", initialize=True)
    beginning = datetime(2026, 10, 20, tzinfo=timezone.utc)
    interval = 30
    with store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"schedule:{i}",
                    f"b{i}",
                    "shared",
                    f"T{i}",
                    "HEAD",
                    beginning.isoformat(),
                    beginning.isoformat(),
                    interval,
                    1,
                    1,
                    "SCHEDULE",
                    "ENABLED",
                    None,
                    json.dumps({"epoch_at": beginning.isoformat()}),
                )
                for i in range(20)
            ],
        )
        db.execute("INSERT INTO first_seen VALUES('forever','2026-10-01T00:00:00+00:00')")

    def insert_hours(start, end):
        rows = []
        physical = []
        for hour in range(start, end):
            for second in range(0, 3600, interval):
                at = (beginning + timedelta(hours=hour, seconds=second)).isoformat()
                physical_id = f"p{hour:03d}{second:04d}"
                physical.append((physical_id, at, "[]"))
                proof = json.dumps({"physical_run_id": physical_id, "list_validated": False})
                rows.extend(
                    (
                        physical_id + ":b" + str(i),
                        "b" + str(i),
                        "shared",
                        "T" + str(i),
                        "HEAD",
                        at,
                        at,
                        interval,
                        1,
                        1,
                        "FAILED",
                        "fixture_refused",
                        at,
                        proof,
                    )
                    for i in range(20)
                )
        with store.connect(write=True) as db:
            db.executemany("INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            db.executemany("INSERT INTO physical_cycles VALUES(?,?,?)", physical)

    def expire(hour):
        monkeypatch.setattr(
            "doxagent.message_bus_v2.collection_plane.maintenance.utc_now",
            lambda: beginning + timedelta(hours=hour),
        )
        for _ in range(510):
            maintain(store)
        with store.connect() as db:
            return db.execute("SELECT count(*) FROM cycles WHERE status!='SCHEDULE'").fetchone()[0]

    insert_hours(0, 72)
    assert expire(72) == 48 * 120 * 20
    initial = store.path.stat().st_size
    insert_hours(72, 96)
    assert expire(96) == 48 * 120 * 20
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM first_seen").fetchone()[0] == 1
        hours = [json.loads(r[0]) for r in db.execute("SELECT data_json FROM unit_hours")]
        assert len(hours) == 48 * 20
        assert sum(h["expected_cycles"] for h in hours) == 48 * 120 * 20
        assert all(h["fresh_cycles"] == 0 and h["all_cycle_coverage"] == 0 for h in hours)
        assert db.execute("SELECT count(*) FROM physical_cycles").fetchone()[0] <= 48 * 120 + 512
    # SQLite reuses expired pages; bounded hour summaries add a small amount.
    assert store.path.stat().st_size < initial * 1.3
