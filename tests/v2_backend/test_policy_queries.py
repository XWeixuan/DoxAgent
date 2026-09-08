from datetime import UTC, datetime

from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.dto import available, missing
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.policy_hits import project
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_api import OfflineAuth


def test_policy_cross_day_hits_pending_withdrawal_and_shell_queries(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    policy = {
        "policy_id": "P1",
        "policy_revision_id": "rev",
        "policy_activation_revision": "ar",
        "policy_set_version": 1,
        "title": "Policy",
        "decision": "LONG",
        "shell_ids": ["S1"],
        "unresolved_shell_ids": [],
        "lifecycle": "ACTIVE",
        "consumed": {"state": "AVAILABLE", "value": False, "reason": None},
        "consumed_at": missing(),
        "effective": {"state": "AVAILABLE", "value": True, "reason": None},
        "matched_filters": ["ACTIVE"],
    }
    store.ingest(
        "fixture",
        "policy",
        [
            {"kind": "policy_catalog", "ticker": "MU", "id": "P1:ar", "data": policy},
            {"kind": "shell", "ticker": "MU", "id": "d2:S1", "data": {"shell_id": "S1"}},
        ],
    )
    for ordinal, day in enumerate(("2026-09-08", "2026-09-09")):
        case = {
            "case_id": f"case-{ordinal}",
            "source": {"snapshot": {"ticker": "MU"}},
            "frozen_inputs": {
                "projection": {"policies": [{"policy_id": "P1", "activation_revision": "ar"}]}
            },
            "w2_final": {"policy_ids": ["P1"]},
        }
        event = {"operation": "UPDATE", "recorded_at": day + "T14:00:00Z"}
        records, counts = project(store, case, {"final_policy_hit": available(True)}, event)
        seq = store.ingest("runtime", str(ordinal), records, contributions=counts)
    assert Metrics(store).value("policy_hits", ["MU"], seq, days=None, distinct=True) == 1
    assert (
        Metrics(store).value(
            "policy_ar_hits", ["MU"], seq, days=["2026-09-08", "2026-09-09"], distinct=True
        )
        == 1
    )
    view = store.save_token(
        "developer",
        "fixture",
        {
            "seq": seq,
            "as_of": "2026-09-09T15:00:00Z",
            "wire": {
                "ticker": "MU",
                "page": "POLICIES",
                "activation": {"data": {"document2": {"run_id": "d2"}}},
                "period": {"selected": "ALL", "previous": None},
            },
        },
        view=True,
        now=datetime.now(UTC),
    )
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        headers = {"Authorization": "Bearer offline"}
        params = {"view_id": view, "shell_id": "S1"}
        response = client.get(
            PREFIX + "/tickers/MU/policies", headers=headers, params={**params, "filter": "HIT"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["data"]["items"][0]["matched_filters"] == ["ACTIVE", "HIT"]
        response = client.get(
            PREFIX + "/tickers/MU/policies/metrics", headers=headers, params=params
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["data"]["hit"]["current"]["value"] == "1"
        assert response.json()["data"]["data"]["long_ratio"]["current"]["value"] == "1"
        all_shells = client.get(PREFIX + "/tickers/MU/policies", headers=headers, params={**params, "shell_id": "ALL", "filter": "HIT"})
        assert all_shells.status_code == 200, all_shells.text
        assert all_shells.json()["data"]["data"]["items"] == client.get(PREFIX + "/tickers/MU/policies", headers=headers, params={**params, "filter": "HIT"}).json()["data"]["data"]["items"]
        assert (
            client.get(
                PREFIX + "/tickers/MU/policies/changes", headers=headers, params=params
            ).status_code
            == 200
        )
    records, counts = project(store, case, {"final_policy_hit": missing()}, event)
    latest = store.ingest("runtime", "withdraw", records, contributions=counts)
    assert (
        Metrics(store).value("policy_hits", ["MU"], latest, days=["2026-09-09"], distinct=True) == 0
    )
    assert Metrics(store).value("policy_hits", ["MU"], latest, days=None, distinct=True) == 1
