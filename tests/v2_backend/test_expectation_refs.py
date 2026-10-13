from copy import deepcopy

from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.dto import available, missing
from doxagent.api_v2.expectation_refs import enrich_policy, related_expectations
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_api import OfflineAuth


def seed(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    doc = dict(
        run_id="source-d2",
        artifact_id="artifact-d2",
        content_sha256="a" * 64,
        schema_version="document2.v2.1",
        published_at="2026-10-14T00:00:00Z",
    )
    summary = dict(
        schema_version="document3.policy-summary.v3",
        policy_id="P1",
        policy_revision_id="rev",
        policy_set_version=7,
        title="Supply",
        directions=["LONG", "SHORT"],
        executable_directions=["LONG"],
        consumed_directions=["SHORT"],
        shell_ids=[],
        unresolved_shell_ids=[],
        lifecycle="ACTIVE",
        consumed=available(False),
        consumed_at=missing(),
        effective=available(True),
        matched_filters=["ACTIVE"],
    )
    refs = [
        "context/document3/v21/d2/shell-0001.json#/units/0",
        "context/document3/v21/shared/document2.json#/shells/0/units/0",
        "context/document3/v21/shared/document2.json#/shells/0/units/0/potential_gaps/0/possibility_space/0",
        "context/document3/v21/d2/shell-0001.json",
        "context/document3/v21/d2/shell-0001.json#/units/999",
        "context/document3/v21/d2/../private.json#/units/0",
        "https://example.com/research",
        "【cite:O3】",
    ]
    detail = dict(
        summary=summary,
        source_document2=doc,
        activation_semantics="OR",
        policy_usage=[],
        policy=dict(
            policy_id="P1",
            title="Supply",
            ref=refs,
            transmission="Supply to earnings",
            match_scope="Supply",
            activation_conditions=[
                dict(
                    condition_id="C1",
                    decision="LONG",
                    trigger_layer="MIDDLE",
                    criterion="New order",
                    calibration=dict(reference_state="No order", trigger_boundary="Signed"),
                )
            ],
        ),
    )
    unit = dict(
        name="Customer demand",
        ordinal=0,
        state=dict(parameters=[dict(name="Revenue")], values=[dict(parameter="Revenue")]),
        expectation_baseline=[],
        realization_factors=[],
        potential_gaps=[dict(name="New customers", possibility_space=[dict(name="Adoption")])],
    )
    policy_ref = {
        **doc,
        "run_id": "source-d3",
        "artifact_id": "artifact-d3",
        "schema_version": "document3.v3",
        "policy_set_version": 7,
    }
    records = [
        dict(kind="document_ref", ticker="MU", id=doc["run_id"], data=doc),
        dict(
            kind="shell",
            ticker="MU",
            id="source-d2:shell-0001",
            parent="source-d2",
            data=dict(
                shell_id="shell-0001",
                ordinal=1,
                view_id="source-d2#/shells/0",
                status="COMPLETED",
                name="Demand",
            ),
        ),
        dict(
            kind="shell",
            ticker="MU",
            id="source-d2:shell-0000",
            parent="source-d2",
            data=dict(
                shell_id="shell-0000",
                ordinal=0,
                view_id="source-d2#/shells/0",
                status="FAILED",
                name="Failed seed",
            ),
        ),
        dict(
            kind="unit",
            ticker="MU",
            id="source-d2:shell-0001:unit-0000",
            parent="source-d2:shell-0001",
            data=unit,
        ),
        dict(kind="policy_detail", ticker="MU", id="rev", parent="artifact-d3", data=detail),
        dict(kind="policy_set", ticker="MU", id="artifact-d3", data=dict(reference=policy_ref)),
        dict(kind="activation_revision", ticker="MU", id="pin", data=dict(policy_set=policy_ref)),
    ]
    seq = store.ingest("test", "refs", records)
    return store, detail, seq


def test_fixed_document_refs_batch_names_and_real_pointer_validation(tmp_path, monkeypatch):
    store, detail, seq = seed(tmp_path)
    calls = []
    batch = store.batch_get
    monkeypatch.setattr(
        store, "batch_get", lambda requests, seq: (calls.append(requests), batch(requests, seq))[1]
    )
    result = related_expectations(store, "MU", detail, seq)
    assert len(calls) == 1
    assert len(result) == 3
    assert result[0]["unit_name"] == "Customer demand"
    assert result[0]["run_id"] == "source-d2"
    assert len(result[0]["refs"]) == 2
    assert result[1]["item"] == "gaps-0"
    assert result[1]["item_name"] == "New customers"
    assert result[2]["unit_id"] is None
    before = deepcopy(detail)
    assert enrich_policy(store, "MU", detail, seq)["policy"]["ref"] == detail["policy"]["ref"]
    assert detail == before
    broken = deepcopy(detail)
    broken["policy"]["ref"] = [
        "context/document3/v21/d2/shell-0001.json#/units/0/potential_gaps/0/not_a_field"
    ]
    assert related_expectations(store, "MU", broken, seq) == []
    broken["source_document2"]["content_sha256"] = "wrong"
    assert related_expectations(store, "MU", broken, seq) == []
    broken["source_document2"] = None
    assert related_expectations(store, "MU", broken, seq) == []


def test_historical_watermark_and_legacy_refs(tmp_path):
    store, detail, seq = seed(tmp_path)
    store.ingest(
        "test",
        "rename",
        [
            dict(
                kind="unit",
                ticker="MU",
                id="source-d2:shell-0001:unit-0000",
                parent="source-d2:shell-0001",
                data=dict(name="New name"),
            )
        ],
    )
    assert related_expectations(store, "MU", detail, seq)[0]["unit_name"] == "Customer demand"
    legacy = {
        **detail,
        "policy": dict(source_refs=[dict(shell_id="shell-0001", expectation_id="E1", gap_id="G1")]),
    }
    store.ingest(
        "test",
        "legacy-unit",
        [
            dict(
                kind="unit",
                ticker="MU",
                id="source-d2:shell-0001:E1",
                parent="source-d2:shell-0001",
                data=dict(
                    proposition="Legacy unit",
                    potential_gaps=[dict(gap_id="G1", possible_occurrence="Legacy gap")],
                ),
            )
        ],
    )
    result = related_expectations(store, "MU", legacy)
    assert result[0]["unit_name"] == "Legacy unit"
    assert result[0]["item_name"] == "Legacy gap"
    assert result[0]["item"] == "G1"


def test_policy_detail_and_pin_api_bind_exact_version_without_usage_write(tmp_path):
    store, detail, _ = seed(tmp_path)
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        headers = {"Authorization": "Bearer offline"}
        url = PREFIX + "/tickers/MU/policy-sets/7/policies/P1"
        with store.connect() as db:
            before = store.highwater(db)
        for path, args in [
            (url, {"runtime_activation_id": "pin"}),
            (PREFIX + "/tickers/MU/policies/P1/revisions/rev", {"policy_set_version": "7"}),
        ]:
            response = client.get(path, params=args, headers=headers)
            assert response.status_code == 200, response.text
            result = response.json()["data"]["data"]
            assert result["related_expectations"][0]["unit_name"] == "Customer demand"
            assert result["policy"] == detail["policy"]
        assert (
            client.get(
                url.replace("/7/", "/8/"), params={"runtime_activation_id": "pin"}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.get(url, params={"runtime_activation_id": "other"}, headers=headers).status_code
            == 404
        )
        assert (
            client.get(
                url.replace("/P1", "/P2"), params={"runtime_activation_id": "pin"}, headers=headers
            ).status_code
            == 404
        )
        with store.connect() as db:
            assert store.highwater(db) == before
