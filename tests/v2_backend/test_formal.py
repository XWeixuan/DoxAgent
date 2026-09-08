import hashlib
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
from doxagent.v2_read.identities import migrate
from doxagent.v2_read.repository import ReadStore
from doxagent.workflows.codex_document3.schema import Document3Bundle, Document3Handoff
from tests.test_codex_document3_workflow import NOW, _policy_set, _seed_published_d2
from tests.test_codex_event_library_incremental import _publish_v1
from tests.test_persistent_runtime_v2 import _source
from tests.v2_backend.test_api import OfflineAuth


def test_formal_activation_indexes_exact_documents_policy_and_library(tmp_path):
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
    with events._write() as db:
        migrate(db)
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
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        response = client.get(
            PREFIX + f"/tickers/MU/runtime/cases/{case['case_id']}",
            params={"view_id": view},
            headers={"Authorization": "Bearer offline"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["document1"]["value"]["run_id"] == "d1-mu"
        assert data["document2"]["value"]["content_sha256"] == d2.sha256
        assert data["w1"]["data"]["reasoning"]["data"]["content_id"]
        assert "New facts" not in response.text
