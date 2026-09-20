import base64
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.auth import Principal, SupabaseAuth
from doxagent.api_v2.errors import ApiFailure
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.repository import ReadStore


class OfflineAuth:
    def configuration(self):
        return {
            "provider": "supabase",
            "supabase_url": "https://test.supabase.co",
            "supabase_publishable_key": "sb_publishable_fixture",
        }

    async def authenticate(self, token):
        if token != "offline":
            raise ApiFailure("UNAUTHORIZED", 401)
        return Principal("developer", "DEVELOPER", time.time() + 3600)


def test_api_control_is_durable_and_auth_has_no_open_fallback(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        assert client.get(PREFIX + "/auth/config").status_code == 200
        assert client.get(PREFIX + "/tickers/MU").status_code == 401
        headers = {"Authorization": "Bearer offline", "Idempotency-Key": "request-1"}
        body = {
            "ticker": "MU",
            "monitor_mode": "MESSAGE_MONITORING",
            "initialization": "FORCE_INITIALIZE",
        }
        first = client.post(PREFIX + "/tickers", json=body, headers=headers)
        assert first.status_code == 202, first.text
        replay = client.post(PREFIX + "/tickers", json=body, headers=headers)
        assert replay.json()["data"]["operation_id"] == first.json()["data"]["operation_id"]
        state = client.get(PREFIX + "/tickers/MU", headers=headers)
        assert state.json()["data"]["state"] == "AVAILABLE"
        assert state.headers["etag"] == state.json()["data"]["data"]["control_etag"]
        assert client.get(PREFIX + "/tickers/MU?unknown=1", headers=headers).status_code == 422
        assert (
            client.post(
                PREFIX + "/tickers", json={**body, "account": "private"}, headers=headers
            ).status_code
            == 422
        )
    assert control.pending()[0]["id"] == first.json()["data"]["operation_id"]


@pytest.mark.asyncio
async def test_auth_cache_cannot_outlive_expiry_and_profile_is_trusted():
    calls = []

    def transport(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": "alice"})
        return httpx.Response(200, json=[{"tier": "DEVELOPER"}])

    def token(expires):
        payload = (
            base64.urlsafe_b64encode(
                json.dumps({"exp": expires, "user_metadata": {"tier": "DEVELOPER"}}).encode()
            )
            .decode()
            .rstrip("=")
        )
        return "header." + payload + ".signature"

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        valid = token(time.time() + 60)
        assert (await auth.authenticate(valid)).user_id == "alice"
        await auth.authenticate(valid)
        assert len(calls) == 2
        with pytest.raises(ApiFailure) as error:
            await auth.authenticate(token(time.time() - 1))
        assert error.value.status == 401
        assert "select=tier" in calls[1] and "limit=1" in calls[1]


def test_api_startup_does_not_create_missing_read_database(tmp_path):
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    path = tmp_path / "absent.db"
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        create_app(store=ReadStore(path), control=control, auth=OfflineAuth())
    assert not path.exists()


@pytest.mark.parametrize("repair_count", [0, 2])
def test_overview_nonroutine_repairs_uses_projected_round_count(tmp_path, repair_count):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    now = datetime(2026, 9, 21, 16, tzinfo=UTC)
    day = "2026-09-21"
    store.ingest(
        "fixture",
        "ticker",
        [{"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}}],
    )
    contributions = [
        {
            "metric": "nonroutine_repairs",
            "ticker": "MU",
            "entity": f"round-{index}",
            "day": day,
            "value": 1,
        }
        for index in range(repair_count)
    ]
    store.ingest("initialization", "repair-rounds", [], contributions=contributions)
    store.ingest(
        "capture_coverage",
        "initialization-proof",
        [
            {
                "kind": "capture_coverage",
                "ticker": "",
                "id": "initialization",
                "data": {
                    "source": "initialization",
                    "started_at": (now - timedelta(days=60)).isoformat(),
                    "end_at": (now + timedelta(days=2)).isoformat(),
                    "closed_end_at": (now + timedelta(days=2)).isoformat(),
                    "complete": True,
                    "tables_json": json.dumps(
                        [
                            "initialization_repair_incidents",
                            "initialization_repair_rounds",
                        ]
                    ),
                },
            }
        ],
    )
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    app = create_app(store=store, control=control, auth=OfflineAuth())
    with TestClient(app) as client:
        view = app.state.views.create(
            "developer",
            "OVERVIEW",
            None,
            "CURRENT_TRADING_DAY",
            now=now,
        )
        response = client.get(
            PREFIX + "/overview/metrics",
            params={"view_id": view["view_id"]},
            headers={"Authorization": "Bearer offline"},
        )
    assert response.status_code == 200, response.text
    metric = response.json()["data"]["data"]["nonroutine_repairs"]
    assert metric["current"]["state"] == "AVAILABLE"
    assert metric["current"]["value"] == str(repair_count)
    assert metric["current_coverage"]["state"] == "COMPLETE"
    assert metric["previous"]["reason"] == "NOT_APPLICABLE"
    assert metric["change_pct"]["reason"] == "NOT_APPLICABLE"
