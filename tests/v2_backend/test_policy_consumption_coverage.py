import json

import pytest

from doxagent.api_v2.dto import available, missing
from doxagent.v2_read.lifecycle import activate, consumption_projection
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.repository import ReadStore


def seeded(tmp_path, first="2026-09-08T14:00:00Z"):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = dict(
        policy_id="P1",
        policy_revision_id="rev",
        policy_activation_revision="ar",
        policy_set_version=1,
        title="Policy",
        decision="LONG",
        shell_ids=["S1"],
        unresolved_shell_ids=[],
        lifecycle="ACTIVE",
        consumed=missing(),
        consumed_at=missing(),
        effective=missing(),
        matched_filters=["ACTIVE"],
    )
    store.ingest(
        "test",
        "seed",
        [
            {"kind": "policy_catalog", "ticker": "MU", "id": "P1:ar", "data": summary},
            {
                "kind": "policy_admission",
                "ticker": "MU",
                "id": "P1:ar",
                "data": {"first_activated_at": first},
            },
        ],
    )
    return store, summary


def capture(**changes):
    return dict(
        complete=True,
        started_at="2026-09-08T13:00:00Z",
        end_at="2026-09-08T15:00:00Z",
        tables_json=json.dumps(["runtime_v2_policy_activations"]),
        **changes,
    )


@pytest.mark.parametrize(
    "first,changes,expected",
    [
        ("2026-09-08T14:00:00Z", {}, False),
        ("2026-09-08T12:00:00Z", {}, None),
        (None, {}, None),
        ("2026-09-08T16:00:00Z", {}, None),
        ("2026-09-08T14:00:00Z", {"complete": False}, None),
        ("2026-09-08T14:00:00Z", {"tables_json": "[]"}, None),
    ],
)
def test_negative_evidence_requires_full_coverage(tmp_path, first, changes, expected):
    store, summary = seeded(tmp_path, first)
    records = consumption_projection(store, {**capture(), **changes})
    result = records[0]["data"] if records else summary
    assert result["consumed"]["value"] is expected
    assert result["effective"]["value"] is (True if expected is False else None)


def test_coverage_loss_and_positive_consumption(tmp_path):
    store, _ = seeded(tmp_path)
    store.ingest("test", "covered", consumption_projection(store, capture()))
    records = consumption_projection(store, {**capture(), "complete": False})
    assert records[0]["data"]["consumed"] == missing()
    store.ingest(
        "test",
        "consumed",
        [
            {
                "kind": "policy_consumption",
                "ticker": "MU",
                "id": "P1:ar",
                "data": {"activated_at": "2026-09-08T14:30:00Z"},
            }
        ],
    )
    result = consumption_projection(store, {**capture(), "complete": False})[0]["data"]
    assert result["consumed"] == available(True)
    assert result["effective"] == available(False)


def test_first_admission_is_not_reset_by_restore(tmp_path):
    store, summary = seeded(tmp_path, None)
    active = {
        "runtime_activation_id": "new",
        "policy_set": {
            "artifact_id": "d3",
            "policy_set_version": 1,
            "published_at": "2026-09-03T00:00:00Z",
        },
    }
    store.ingest(
        "test",
        "detail",
        [
            {
                "kind": "policy_detail",
                "ticker": "MU",
                "id": "rev",
                "parent": "d3",
                "data": {"summary": summary, "policy": {}},
            }
        ],
    )
    records, _ = activate(store, "MU", active, "2026-09-08T14:00:00Z")
    assert not any(r["kind"] == "policy_admission" for r in records)
    assert store.get("policy_admission", "MU", "P1:ar")["first_activated_at"] is None


def test_worker_reconciles_when_same_head_catches_up(tmp_path):
    store, _ = seeded(tmp_path)

    class Source:
        source = "runtime"

        def head(self):
            return 2

        def read(self, position, limit):
            return [
                {"seq": n, "recorded_at": "2026-09-08T14:30:00Z"}
                for n in range(position + 1, min(2, position + limit) + 1)
            ]

        def capture(self):
            return capture()

    class Mapper:
        def __call__(self, event):
            return [], []

        def coverage(self, source, proof):
            return consumption_projection(store, proof)

    worker = ProjectionWorker(store, [Source()], Mapper())
    worker.tick(limit=1)
    assert store.get("policy_catalog", "MU", "P1:ar")["consumed"] == missing()
    worker.tick(limit=1)
    assert store.get("policy_catalog", "MU", "P1:ar")["consumed"] == available(False)
