from doxagent.v2_read.ordering import message_anchor, occurrence_anchor


def test_precision_anchors_preserve_chronology_and_unknown_last():
    assert occurrence_anchor("2026-Q2", "QUARTER") == occurrence_anchor("2026-04-01", "DAY")
    assert occurrence_anchor("2026-Q2", "QUARTER") < occurrence_anchor("2026-08-26", "DAY")
    assert occurrence_anchor("2026-08", "MONTH") < occurrence_anchor("2026-09-22", "DAY")
    assert occurrence_anchor("2026-01-02/2026-09-30", "INTERVAL") == occurrence_anchor(
        "2026-01-02", "DAY"
    )
    assert occurrence_anchor(None, "UNKNOWN") == ""
    assert occurrence_anchor("2026-09-08T08:00:00-04:00", "TIMESTAMP") == occurrence_anchor(
        "2026-09-08T12:00:00Z", "TIMESTAMP"
    )
    base = {"stream_published_at": "2026-09-08T12:00:00Z", "member_index": 0}
    assert message_anchor({**base, "stream_offset": 10}) > message_anchor(
        {**base, "stream_offset": 9}
    )
