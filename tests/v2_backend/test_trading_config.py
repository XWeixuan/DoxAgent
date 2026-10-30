from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.v2_read.repository import ReadStore
from tests.test_execution_configuration import configured
from tests.v2_backend.test_api import OfflineAuth


def test_configuration_and_capital_http_receipts(tmp_path):
    clock, journal, repo, control, service, revision = configured(tmp_path)
    service.apply(repo)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    headers = {"Authorization": "Bearer offline"}
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        path = PREFIX + "/tickers/MU/trading-config"
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        first = response.json()["data"]["data"]
        assert first["active"]["ticker_revision"] == first["revision"]
        assert "execution_pin" not in first["active"]
        config = {
            **first["configuration"],
            "trailing_profit_enabled": True,
            "banned_semantic_dates": ["2026-11-04", "2026-11-05"],
        }
        response = client.put(
            path,
            headers=headers,
            json={"expected_revision": first["revision"], "configuration": config},
        )
        assert response.status_code == 200, response.text
        desired = response.json()["data"]
        assert desired["revision"] > desired["active"]["ticker_revision"]
        assert (
            client.put(
                path,
                headers=headers,
                json={"expected_revision": first["revision"], "configuration": config},
            ).status_code
            == 409
        )
        service.apply(repo)
        actual = client.get(path, headers=headers).json()["data"]["data"]
        assert actual["active"]["configuration"]["banned_semantic_dates"] == [
            "2026-11-04",
            "2026-11-05",
        ]
        assert actual["active"]["ticker_revision"] == desired["revision"]
        response = client.put(
            PREFIX + "/trading-capital/PAPER",
            headers={**headers, "Idempotency-Key": "allocation-http-1"},
            json={"amount": "4500", "reason": "test adjustment"},
        )
        assert response.status_code == 200, response.text
        saved = response.json()["data"]["accounts"][0]
        assert saved["desired"]["amount"] == "4500" and saved["applied"] is None
        assert "account" not in saved["desired"]
        service.apply(repo)
        response = client.get(PREFIX + "/trading-capital", headers=headers)
        assert response.status_code == 200, response.text
        applied = response.json()["data"]["data"]["accounts"][0]
        assert applied["book"]["allocated"] == "4500"
        assert applied["book"]["net_value"] == "4500"
        assert applied["applied"]["identity"] == "allocation-http-1"
        assert (
            client.put(
                PREFIX + "/trading-capital/LIVE",
                headers={**headers, "Idempotency-Key": "no-live"},
                json={"amount": "10000", "reason": "not configured"},
            ).status_code
            == 422
        )
        assert client.get(path).status_code == 401
