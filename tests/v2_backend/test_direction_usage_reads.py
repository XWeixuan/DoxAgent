from doxagent.api_v2.dto import available, missing
from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode
from doxagent.persistent_runtime_v2.policy_consumption import (
    apply_release_authorizations,
    put_value,
    usage,
)
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.v2_read.lifecycle import direction_summary
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_direction_watch_and_admission_receipts_project_through_real_outbox(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    SQLitePersistentRuntimeV2Repository(journal.path)
    source = SourceOutbox(journal.path, "runtime")
    source.migrate()
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = dict(
        schema_version="document3.policy-summary.v3",
        policy_id="P1",
        policy_revision_id="rule",
        policy_set_version=1,
        title="scope",
        directions=["LONG", "SHORT"],
        executable_directions=[],
        consumed_directions=[],
        shell_ids=[],
        unresolved_shell_ids=[],
        lifecycle="ACTIVE",
        consumed=missing(),
        consumed_at=missing(),
        effective=missing(),
        matched_filters=["ACTIVE"],
    )
    store.ingest(
        "fixture", "catalog", [dict(kind="policy_catalog", ticker="MU", id="P1:rule", data=summary)]
    )
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    with journal.transaction() as db:
        put_value(
            db,
            "policy_direction_usage",
            encode(["MU", "P1", "LONG"]),
            dict(usage(db, "MU", "P1", "LONG"), consumed=True, consumption_id="c1"),
        )
        apply_release_authorizations(db, ticker="MU", revision_id="revision", requests=[])
        put_value(
            db,
            "policy_watch_applied",
            "case",
            dict(
                ticker="MU",
                case_id="case",
                outcomes=[dict(policy_id="P1", action="SET", status="APPLIED")],
            ),
        )
    worker.tick(limit=500)
    result = store.get("policy_catalog", "MU", "P1:rule")
    assert result["consumed_directions"] == ["LONG"] and result["executable_directions"] == [
        "SHORT"
    ]
    assert result["effective"] == available(True) and result["consumed"] == available(False)
    assert (
        store.get("native:runtime_values", "MU", "policy_watch_applied:case")["outcomes"][0][
            "status"
        ]
        == "APPLIED"
    )
    assert store.get("native:runtime_values", "MU", "policy_authorization_applied:revision")
    with journal.transaction() as db:
        apply_release_authorizations(
            db,
            ticker="MU",
            revision_id="next",
            requests=[
                dict(
                    policy_id="P1",
                    bindings=[dict(direction="LONG", consumption_id="c1", usage_generation=1)],
                )
            ],
        )
    worker.tick(limit=500)
    result = store.get("policy_catalog", "MU", "P1:rule")
    assert result["consumed_directions"] == [] and result["executable_directions"] == [
        "LONG",
        "SHORT",
    ]
    assert (
        store.get("policy_direction_usage", "MU", encode(["MU", "P1", "LONG"]))["usage_generation"]
        == 2
    )


def test_absent_capture_is_unknown_not_false_negative_eligibility(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = dict(policy_id="P1", directions=["LONG", "SHORT"], lifecycle="ACTIVE")
    result = direction_summary(store, "MU", summary)
    assert result["effective"]["value"] is None and result["consumed"]["value"] is None
    assert result["executable_directions"] == []


def test_shadow_rebuild_retains_direction_authorization_and_watch_evidence(tmp_path):
    from doxagent.v2_read.maintenance import activate_alias, rebuild, verify_shadow

    journal = RuntimeJournal(tmp_path / "source.db")
    SQLitePersistentRuntimeV2Repository(journal.path)
    source = SourceOutbox(journal.path, "runtime")
    source.migrate()
    with journal.transaction() as db:
        put_value(
            db,
            "policy_direction_usage",
            encode(["MU", "P1", "LONG"]),
            dict(usage(db, "MU", "P1", "LONG"), consumed=True, consumption_id="c1"),
        )
        put_value(db, "policy_consumptions", "c1", dict(ticker="MU", policy_id="P1", consumed=True))
        apply_release_authorizations(db, ticker="MU", revision_id="active", requests=[])
        put_value(
            db, "policy_watch_applied", "case", dict(ticker="MU", case_id="case", outcomes=[])
        )
    target = tmp_path / "shadow.db"
    report = rebuild([source], target)
    store = ReadStore(target)
    assert report["heads"] == {"runtime": source.head()}
    assert store.get("policy_direction_usage", "MU", encode(["MU", "P1", "LONG"]))["consumed"]
    assert store.get("native:runtime_values", "MU", "policy_consumptions:c1")
    assert store.get("native:runtime_values", "MU", "policy_watch_applied:case")["outcomes"] == []
    assert store.get("native:runtime_values", "MU", "policy_authorization_applied:active")
    verify_shadow(store, [source])
    alias = activate_alias(tmp_path / "isolated-alias.json", target, [source])
    assert alias["path"] == str(target.resolve())


def test_native_hit_statistics_keep_all_final_hits_independent_of_selection(tmp_path):
    from doxagent.v2_read.policy_hits import project

    store = ReadStore(tmp_path / "hits.db")
    store.migrate()
    store.ingest(
        "fixture",
        "activation",
        [
            dict(
                kind="activation_revision",
                ticker="MU",
                id="a1",
                data={"policy_set": {"artifact_id": "artifact"}},
            )
        ],
    )
    store.ingest(
        "fixture",
        "policies",
        [
            dict(
                kind="policy_detail",
                ticker="MU",
                id=pid,
                parent="artifact",
                data={"summary": {"policy_id": pid, "policy_revision_id": "revision-" + pid}},
            )
            for pid in ["P1", "P2", "P3"]
        ],
    )
    case = dict(
        case_id="case",
        source={"snapshot": {"ticker": "MU"}},
        version_pin={"activation_revision_id": "a1"},
        frozen_inputs={
            "projection": {"policies": [{"policy_id": pid} for pid in ["P1", "P2", "P3"]]}
        },
        w3_result={
            "policy": {"policy_ids": ["P1", "P2"]},
            "policy_execution": {"selected_condition": {"policy_id": "P1", "condition_id": "C1"}},
        },
    )
    records, metrics = project(
        store,
        case,
        {"final_policy_hit": {"value": True}},
        {"operation": "UPDATE", "recorded_at": "2026-10-10T12:00:00+00:00"},
    )
    assert {r["data"]["policy_id"] for r in records if r["data"]["hit"]} == {"P1", "P2"}
    assert {
        m["dimensions"]["policy_id"]
        for m in metrics
        if m["metric"] == "policy_hits" and m["value"] == "1"
    } == {"P1", "P2"}
