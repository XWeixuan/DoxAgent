import hashlib
import json
import pytest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactRef,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    PublishedDocument,
)
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.schema import RuntimeCase, RuntimeVersionPin
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.artifacts import PublishedArtifacts
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.graph import project as project_graph
from doxagent.v2_read.repository import ReadStore
from doxagent.workflows.codex_document3.schema import Document3Bundle, Document3Handoff
from tests.test_codex_document3_workflow import NOW, _policy_set, _seed_published_d2
from tests.test_codex_event_library_incremental import _publish_v1
from tests.test_persistent_runtime_v2 import _source
from tests.v2_backend.test_api import OfflineAuth


def test_graph_projection_accepts_native_case_delete(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    summary = {
        "case_id": "case-delete",
        "results": ["ARCHIVE"],
        "first_round_shape": "PARALLEL",
        "w3_status": None,
        "status": "COMPLETED",
        "semantic_day": "2026-09-11",
        "received_at": "2026-09-11T12:00:00Z",
    }

    records, metrics = project_graph(
        store,
        "MU",
        summary,
        [
            {
                "kind": "native:runtime_v2_cases",
                "ticker": "MU",
                "id": "case-delete",
                "data": None,
            }
        ],
    )

    assert records
    assert metrics


def test_formal_projection_ignores_successful_internal_d1_d2_nodes(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    run_id = "init-mu"
    store.ingest(
        "initialization",
        "run",
        [
            {
                "kind": "native:initialization_runs",
                "ticker": "MU",
                "id": run_id,
                "data": {
                    "initialization_id": run_id,
                    "ticker": "MU",
                    "control_operation_id": "operation-a",
                    "status": "RUNNING",
                    "state_seq": 1,
                    "manual_resume_required": False,
                    "error": None,
                },
            }
        ],
    )
    mapper = FormalProjectors(store)
    payload = {
        "key": "d2.o0.candidate",
        "block": "D2",
        "dependencies": [],
        "inputs": {"managed_by": "d2"},
        "status": "SUCCEEDED",
        "receipt": {},
        "result": {"artifacts": {"return": {"output": {}}}},
    }
    records, metrics = mapper(
        {
            "source": "initialization",
            "seq": 1,
            "table_name": "initialization_nodes",
            "entity_id": json.dumps([run_id, "d2.o0.candidate"]),
            "operation": "UPDATE",
            "recorded_at": "2026-09-11T00:00:00Z",
            "row": {
                "run_id": run_id,
                "node_key": "d2.o0.candidate",
                "payload": json.dumps(payload),
            },
        }
    )
    assert [record["kind"] for record in records] == [
        "native:initialization_nodes",
        "initialization",
    ]
    assert metrics == []


def test_runtime_noop_activation_inherits_base_policy_run(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "initialization",
        "base",
        [
            {
                "kind": "activation_revision",
                "ticker": "MU",
                "id": "activation-base",
                "data": {"policy_set": {"policy_set_version": 7, "run_id": "d3-base"}},
            }
        ],
    )
    mapper = FormalProjectors(store)
    assert (
        mapper._policy_run_id(
            {
                "ticker": "MU",
                "revision_id": "runtime-maintain-fixture-activation",
                "base_revision": "activation-base",
                "artifacts": {"document3": {"version": 7}},
            }
        )
        == "d3-base"
    )


@pytest.mark.parametrize("attribution_recorded", [False, True, "resolved"])
def test_formal_activation_indexes_exact_documents_policy_and_library(
    tmp_path, attribution_recorded, monkeypatch
):
    source = SQLiteCodexRuntimeRepository(tmp_path / "research.db")
    _seed_published_d2(source)

    def publish(run, identity, body, lane, workflow, node):
        sha = hashlib.sha256(body.encode()).hexdigest()
        ref = ArtifactRef(
            artifact_id=identity,
            run_id=run,
            workflow_version=workflow,
            research_lane=lane,
            node=node,
            attempt_id="fixture",
            kind="bundle",
            relative_path=f"artifacts/{identity}.json",
            sha256=sha,
            size_bytes=len(body.encode()),
            published=True,
            content_type="application/json",
        )
        source.save_artifact(ref)
        source.save_published_document(
            PublishedDocument(
                artifact_id=identity,
                run_id=run,
                artifact_kind="bundle",
                sha256=sha,
                size_bytes=len(body.encode()),
                content_type="application/json",
                content_text=body,
                published_at=NOW,
            )
        )
        return ref

    reports = {
        section: publish(
            "d1-mu", section, "# " + section, "global_research", "codex_global_research_v1", section
        )
        for section in ("c1", "c3", "c5")
    }
    main = publish("d1-mu", "main", "{}", "global_research", "codex_global_research_v1", "publish")
    source.save_bundle(
        GlobalResearchBundle(
            run_id="d1-mu",
            ticker="MU",
            status="published",
            reports=reports,
            published_at=NOW,
            handoff=GlobalResearchHandoffV1(
                run_id="d1-mu", ticker="MU", document_artifact_id=main.artifact_id, published_at=NOW
            ),
        )
    )
    policy_set = _policy_set()
    d2 = source.get_published_document("d2-mu", "d2-artifact")
    policy_set.document2_ref.sha256 = d2.sha256
    policy_ref = publish(
        "d3-mu",
        "policy",
        policy_set.model_dump_json(),
        "document3",
        "codex_document3_v1",
        "d3_publish",
    )
    source.save_bundle(
        Document3Bundle(
            run_id="d3-mu",
            ticker="MU",
            status="published",
            published_at=NOW,
            handoff=Document3Handoff(
                run_id="d3-mu",
                ticker="MU",
                policy_set_version=1,
                publication_state="COMPLETE",
                published_artifact=policy_ref,
                coverage_artifact=policy_ref,
                runtime_projection_artifact=policy_ref,
            ),
        )
    )
    root = tmp_path / "events"
    events = EventLibraryRepository(root / "US" / "MU" / "event_library.sqlite3")
    _publish_v1(events, root)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    mapper = FormalProjectors(store, PublishedArtifacts(source.path), root)
    records = mapper.activation(
        {
            "revision_id": "activation-a",
            "ticker": "MU",
            "artifacts": {
                "document1": {"run_id": "d1-mu"},
                "document2": {"run_id": "d2-mu"},
                "document3": {"run_id": "d3-mu", "version": 1},
                "event_library": {"version": 1},
                "monitoring_configuration": {"initialization_id": "initialization-a"},
            },
        }
    )
    store.ingest("initialization", "activation-a", records)
    active = store.get("activation_revision", "MU", "activation-a")
    assert active["maintenance"]["status"] == "INITIALIZED"
    assert active["document2"]["content_sha256"] == d2.sha256
    assert active["policy_set"]["content_sha256"] == policy_ref.sha256
    assert store.get("research_download", "MU", "d1-mu")
    assert len([r for r in records if r["kind"] == "policy_detail"]) == 1
    assert store.get("activation", "MU", "active") is None
    message = _source()
    store.ingest(
        "fixture",
        "source",
        [
            {
                "kind": "native:source_definitions",
                "ticker": "",
                "id": message.source_id,
                "data": {"display_name": "News", "kind": "api"},
            }
        ],
    )
    case = RuntimeCase(
        source=message,
        trading_date=NOW.date(),
        version_pin=RuntimeVersionPin(
            activation_revision_id="activation-a",
            document1_run_id="d1-mu",
            document2_run_id="d2-mu",
            event_library_version=1,
            provisional_snapshot_version=0,
            policy_set_version=1,
            runtime_projection_version=1,
        ),
    ).model_dump(mode="json")
    case["w1_final"] = {
        "result": "NEW",
        "confidence": "normal",
        "reference_ids": [],
        "reason": "New facts",
    }
    case["w2_final"] = {
        "policy_ids": [],
        "confidence": "normal",
        "matched_condition_ids": [],
        "reason": "No hit",
    }
    if attribution_recorded:
        case["w1_final"]["fact_attributions"] = [{"event_id": "E99", "fact_ids": ["F7"]}]
        case["w1_final"]["reference_ids"] = ["E99"]
        case["w2_round1"] = {
            "candidate_policy_ids": [],
            "reason": "No criterion matches the reported fact",
        }
        case["w2_final"]["reason"] = "no_policy_candidate_recalled"
    if attribution_recorded == "resolved":
        event_record = next(r for r in records if r["kind"] == "event")
        event_id = event_record["id"].rsplit(":", 1)[-1]
        fact_record = next(
            r for r in records if r["kind"] == "fact" and r["parent"] == event_record["id"]
        )
        expected_fact = fact_record["data"]["fact"]["fact_id"]
        case["w1_final"].update(
            result="OLD",
            reference_ids=[event_id],
            fact_attributions=[{"event_id": event_id, "fact_ids": [expected_fact]}],
        )
    # Detail must never open the native Case's unrelated historical inputs.
    case["frozen_inputs"] = {"unrelated_history": "irrelevant" * 100000}
    seq = store.ingest(
        "fixture",
        "case",
        [
            {
                "kind": "native:runtime_v2_cases",
                "ticker": "MU",
                "id": case["case_id"],
                "data": case,
            },
            *mapper.case(case, 1),
        ],
    )
    view = store.save_token(
        "developer",
        "runtime-test",
        {
            "seq": seq,
            "as_of": "2026-09-08T12:00:00Z",
            "wire": {"page": "RUNTIME", "ticker": "MU"},
        },
        view=True,
        now=datetime.now(UTC),
    )
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    from doxagent.v2_read.content_files import ContentFiles

    original_read = ContentFiles.read

    def bounded_read(self, *args, **kwargs):
        result = original_read(self, *args, **kwargs)
        assert b"unrelated_history" not in result
        return result

    monkeypatch.setattr(ContentFiles, "read", bounded_read)
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        response = client.get(
            PREFIX + f"/tickers/MU/runtime/cases/{case['case_id']}",
            params={"view_id": view},
            headers={"Authorization": "Bearer offline"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]["data"]
        assert data["document1"]["value"]["run_id"] == "d1-mu"
        assert data["document2"]["value"]["content_sha256"] == d2.sha256
        assert data["w1"]["data"]["reasoning"]["data"]["content_id"]
        assert "New facts" not in response.text
        if attribution_recorded == "resolved":
            reference = data["w1"]["data"]["references"][0]
            assert reference["fact_ids"] == [expected_fact]
            assert (
                reference["library_snapshot_id"] == active["event_library"]["library_snapshot_id"]
            )
            assert data["w1"]["state"] == "AVAILABLE"
            assert reference["title"]["value"] == event_record["data"]["title"]
            assert (
                reference["facts"][0]["proposition"]["value"]
                == fact_record["data"]["fact"]["proposition"]
            )
        elif attribution_recorded:
            assert data["w1"]["state"] == "PARTIAL"
            assert data["w1"]["data"]["unresolved_reference_ids"] == ["E99"]
            assert data["w1"]["data"]["fact_attributions"] == [
                {"event_id": "E99", "fact_ids": ["F7"]}
            ]
            assert data["w2"]["data"]["reasoning_stage"] == "R1"
            assert data["w2"]["data"]["reasoning"]["state"] == "AVAILABLE"
            assert data["w2"]["data"]["rounds"][1]["not_executed_reason"] == "NO_POLICY_CANDIDATE"
        else:
            assert data["w1"]["data"]["fact_attributions"] is None
        if attribution_recorded == "resolved":
            from doxagent.v2_read.runtime import timing

            policy_record = next(r for r in records if r["kind"] == "policy_detail")
            policy_id = policy_record["data"]["summary"]["policy_id"]
            case.update(w2_final=None, w2_round1=None, status="FAILED")

            def attempt_record(identity, round_name, status):
                return {
                    "kind": "attempt",
                    "ticker": "MU",
                    "id": identity,
                    "parent": case["case_id"],
                    "route": "W2",
                    "sort": NOW.isoformat(),
                    "data": {
                        "attempt_id": identity,
                        "turn_id": identity,
                        "node_id": "W2",
                        "round": round_name,
                        "ordinal": 1,
                        "attempt_number": 1,
                        "status": status,
                        "timing": timing("2026-09-08T12:00:00Z", "2026-09-08T12:01:00Z"),
                        "error": None,
                    },
                }

            # Fixture writes are not endpoint reads and may inspect the prior revision.
            monkeypatch.setattr(ContentFiles, "read", original_read)
            current_seq = store.ingest(
                "fixture",
                "failed-r2",
                [
                    {
                        "kind": "native:runtime_v2_cases",
                        "ticker": "MU",
                        "id": case["case_id"],
                        "data": case,
                    },
                    *mapper.case(case, 2),
                    attempt_record("r1-turn", "R1", "SUCCEEDED"),
                    attempt_record("r2-turn", "R2", "FAILED"),
                    {
                        "kind": "native:runtime_v2_turns",
                        "ticker": "MU",
                        "id": "r1-turn",
                        "data": {"output": {"candidate_policy_ids": [policy_id]}},
                    },
                ],
            )
            current_view = store.save_token(
                "developer",
                "runtime-current",
                {
                    "seq": current_seq,
                    "as_of": "2026-09-08T12:02:00Z",
                    "wire": {"page": "RUNTIME", "ticker": "MU"},
                },
                view=True,
                now=datetime.now(UTC),
            )
            monkeypatch.setattr(ContentFiles, "read", bounded_read)
            response = client.get(
                PREFIX + f"/tickers/MU/runtime/cases/{case['case_id']}",
                params={"view_id": current_view},
                headers={"Authorization": "Bearer offline"},
            )
            assert response.status_code == 200, response.text
            second = response.json()["data"]["data"]["w2"]["data"]
            assert (
                second["candidate_policies"][0]["title"]["value"]
                == policy_record["data"]["summary"]["title"]
            )
            assert second["policies"] == []
            assert second["policy_hit"]["value"] is None
            assert second["rounds"][1]["attempt_count"] == 1
            assert second["rounds"][1]["not_executed_reason"] is None
            assert any(
                a["round"] == "R2" and a["status"] == "FAILED" for a in second["attempts"]["items"]
            )
            response = client.get(
                PREFIX + f"/tickers/MU/runtime/cases/{case['case_id']}/attempts",
                params={"view_id": view, "node": "W2"},
                headers={"Authorization": "Bearer offline"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["data"]["items"] == []
