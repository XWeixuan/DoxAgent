from types import SimpleNamespace

from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.runtime import worker_records


def test_codex_missing_usage_is_distinct_from_reported_zero_and_replay_is_not_a_call(tmp_path):
    missing = project_turn_telemetry(items=[], usage=None, duration_ms=None)
    assert missing.observed_usage["input_tokens"] is None
    actual = project_turn_telemetry(
        items=[],
        usage=SimpleNamespace(
            last=SimpleNamespace(
                input_tokens=20,
                cached_input_tokens=0,
                output_tokens=3,
                total_tokens=23,
            )
        ),
        duration_ms=100,
    )
    assert actual.observed_usage["cached_input_tokens"] == 0
    value = {
        "node": "persistent_runtime_w3",
        "ticker": "MU",
        "case_id": "case-a",
        "model": "codex-model",
        "ordinal": 1,
        "job": {
            "job_id": "job-a",
            "turn_id": "turn-a",
            "status": "succeeded",
            "started_at": "2026-09-08T12:00:00Z",
            "finished_at": "2026-09-08T12:00:01Z",
            "created_at": "2026-09-08T11:59:00Z",
            "telemetry": actual.model_dump(),
        },
    }
    records, contributions = worker_records(value)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("runtime", "one", records, contributions=contributions)
    seq = store.ingest("runtime", "replay", records, contributions=contributions)
    assert Metrics(store).value("requests", ["MU"], seq, days=None) == 1
    assert Metrics(store).value("input_tokens", ["MU"], seq, days=None) == 20
    assert records[0]["data"]["timing"]["wall_seconds"]["value"] == 1
    assert records[1]["data"]["cost_usd"]["state"] == "NOT_APPLICABLE"
