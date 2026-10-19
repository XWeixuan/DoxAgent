from contextlib import closing
from datetime import UTC, datetime

from doxagent.v2_read import transport_archive
from doxagent.v2_read.source_lifecycle import expire
from tests.v2_backend.test_source_lifecycle import fixture_source


def test_young_rows_do_not_load_packets_and_out_of_order_old_rows_still_expire(
    tmp_path, monkeypatch
):
    source = fixture_source(tmp_path)
    source.acknowledge("live", 6)
    with closing(source.connection(write=True)) as db, db:
        db.execute(
            "UPDATE v2_source_outbox SET recorded_at=? WHERE seq IN (1,3,5)",
            (datetime.now(UTC).isoformat(),),
        )
    calls = []
    original = source.event

    def event(seq, **kwargs):
        calls.append(kwargs["connection"])
        assert kwargs["decode"] is False
        return original(seq, **kwargs)

    monkeypatch.setattr(source, "event", event)
    report = expire(source)
    assert report["scanned"] == 6 and report["expired"] == 3
    assert len(calls) == 3 and all(db is calls[0] for db in calls)


def test_shared_pack_is_decompressed_once_per_batch_and_pinned_packet_survives(
    tmp_path, monkeypatch
):
    from doxagent.v2_read.cli import backup

    source = fixture_source(tmp_path)
    source.acknowledge("live", 6)
    checkpoint = tmp_path / "backup" / "bus.db"
    backup(source.path, checkpoint)
    assert transport_archive.archive(source, checkpoint) == 6
    original = transport_archive.load_pack
    loads = []

    def load(*args):
        loads.append(args[1])
        return original(*args)

    monkeypatch.setattr(transport_archive, "load_pack", load)
    source.acknowledge_exceptions("live", 6, [2])
    report = expire(source)
    assert report["expired"] == 6 and len(loads) == 1
    assert source.event(2)["seq"] == 2 and source.event(1) is None
