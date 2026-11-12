from types import SimpleNamespace

from doxagent.site_strategy.service import merge_attempt_timings


def test_retry_keeps_physical_timeline_and_statuses_instead_of_summing_epochs():
    attempts = [SimpleNamespace(stage_timings={
        "worker_roundtrip_ms": 1000, "request_started_epoch_ms": 1794510000000,
        "request_0_started_epoch_ms": 1794510000000,
        "request_0_headers_epoch_ms": 1794510000500,
        "request_0_status_code": 401, "request_0_auxiliary": 1,
    }), SimpleNamespace(stage_timings={
        "worker_roundtrip_ms": 2000, "request_started_epoch_ms": 1794510010000,
        "request_0_started_epoch_ms": 1794510010000,
        "request_0_headers_epoch_ms": 1794510010500,
        "request_0_status_code": 200, "capture_acquired_epoch_ms": 1794510011000,
        "routing_members": 2,
    })]
    merged = merge_attempt_timings(attempts)
    assert merged["worker_roundtrip_ms"] == 3000
    assert merged["request_started_epoch_ms"] == 1794510010000
    assert merged["request_0_started_epoch_ms"] == 1794510000000
    assert merged["request_1_started_epoch_ms"] == 1794510010000
    assert merged["request_0_status_code"] == 401
    assert merged["request_1_status_code"] == 200
    assert merged["request_0_attempt_index"] == 0
    assert merged["request_1_attempt_index"] == 1
    assert merged["request_0_auxiliary"] == 1
    assert "request_1_auxiliary" not in merged
    assert merged["capture_acquired_epoch_ms"] == 1794510011000
    assert merged["routing_members"] == 2
    assert merge_attempt_timings([]) == {}
