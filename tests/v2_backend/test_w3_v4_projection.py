import json

from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_global_states_are_projected_to_the_case_ticker(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    mapper = DomainProjectors(store)
    for namespace in ("w3_global_input", "w3_operations_input", "w3_operations_applied"):
        value = {"ticker": "MU", "watch": {}, "pins": {}}
        records, _ = mapper(
            {
                "table_name": "runtime_values",
                "entity_id": "case1",
                "operation": "INSERT",
                "seq": 1,
                "row": {"namespace": namespace, "key": "case1", "payload": json.dumps(value)},
            }
        )
        assert records[0]["ticker"] == "MU"
        assert records[0]["id"] == namespace + ":case1"


def test_template_usage_uses_trigger_provenance_without_becoming_a_case_attempt(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "fixture",
        "proof",
        [
            {
                "kind": "business_provenance",
                "ticker": "MU",
                "id": "trigger",
                "data": {"basis": "TEST_FIXTURE"},
            }
        ],
    )
    value = dict(
        ticker="MU",
        node="persistent_runtime_w3",
        task_phase="w3_template",
        source_case_id="trigger",
        case_id=None,
        model="synthetic",
        provider="synthetic",
        run_id="mother",
        job=dict(job_id="job1", started_at=None),
    )
    records, _ = DomainProjectors(store)(
        {
            "table_name": "runtime_values",
            "entity_id": "job1",
            "operation": "INSERT",
            "seq": 2,
            "row": {"namespace": "worker_invocations", "key": "job1", "payload": json.dumps(value)},
        }
    )
    assert not any(r["kind"] == "attempt" for r in records)
