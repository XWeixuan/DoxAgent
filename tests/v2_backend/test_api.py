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


def test_read_context_does_not_hold_projection_writer_during_calendar_work(tmp_path):
    from doxagent.api_v2.views import Views
    from doxagent.v2_read.calendar import PageCalendar

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("fixture", "ticker", [
        {"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}},
    ])

    class ConcurrentCalendar(PageCalendar):
        def clock(self, now, **kwargs):
            with store.connect(write=True, timeout=0) as db:
                db.execute("INSERT OR REPLACE INTO read_meta VALUES('concurrent-proof','ok')")
            return super().clock(now, **kwargs)

    views = Views(store, ConcurrentCalendar())
    value = views.create("developer", "RESEARCH", "MU", None)
    pinned = views.get("developer", value["view_id"], "MU")
    assert pinned["wire"]["ticker"] == "MU"


@pytest.mark.parametrize("day", ["2026-09-27", "2026-12-25"])
def test_overview_closed_day_current_period_is_validation_not_store_failure(tmp_path, day):
    from doxagent.api_v2.views import Views
    from doxagent.v2_read.calendar import PageCalendar

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    views = Views(store, PageCalendar())
    now = datetime.fromisoformat(day + "T17:00:00+00:00")
    with pytest.raises(ApiFailure) as error:
        views.create("developer", "OVERVIEW", None, "CURRENT_TRADING_DAY", now=now)
    assert (error.value.code, error.value.status) == ("VALIDATION_FAILED", 422)
    previous = views.create("developer", "OVERVIEW", None, "PREVIOUS_TRADING_DAY", now=now)
    assert previous["period"]["selected"] == "PREVIOUS_TRADING_DAY"
    assert previous["clock"]["is_trading_day"]["value"] is False


def test_semantic_current_day_stays_available_on_weekends(tmp_path):
    from doxagent.api_v2.views import Views
    from doxagent.v2_read.calendar import PageCalendar

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("fixture", "ticker", [
        {"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}},
    ])
    view = Views(store, PageCalendar()).create(
        "developer", "MESSAGE_BUS", "MU", "CURRENT_TRADING_DAY",
        now=datetime(2026, 9, 27, 17, tzinfo=UTC),
    )
    assert view["period"]["current"]["trading_days"] == ["2026-09-27"]


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


@pytest.mark.parametrize("failed_count", [0, 2])
def test_overview_failed_cases_uses_existing_failure_node_buckets(tmp_path, failed_count):
    from doxagent.v2_read.calendar import PageCalendar

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    # The view must still be live when the HTTP request reads it.
    now = datetime.now(UTC)
    while not PageCalendar().clock(now)["is_trading_day"]["value"]:
        now += timedelta(days=1)
    day = PageCalendar().clock(now)["semantic_day"]
    store.ingest(
        "fixture",
        "ticker",
        [{"kind": "ticker", "ticker": "MU", "id": "MU", "data": {"removed": False}}],
    )
    store.ingest('fixture','second-ticker',[
        {'kind':'ticker','ticker':'INTC','id':'INTC','data':{'removed':False}}])
    contributions = [
        {'metric':'graph_cases','ticker':ticker,'entity':f'case-{index}:FAILURE',
         'day':day,'value':1,'dimensions':{'node':'FAILURE'}}
        for ticker,n in [('MU',failed_count),('INTC',failed_count*2)] for index in range(n)
    ]
    # Node-level failures and successful cases must not inflate the Case total.
    contributions.extend([
        {'metric':'graph_failed','ticker':'MU','entity':'retry:W1','day':day,'value':9,'dimensions':{'node':'W1'}},
        {'metric':'graph_cases','ticker':'MU','entity':'success:SOURCE','day':day,'value':50,'dimensions':{'node':'SOURCE'}},
        {'metric':'graph_cases','ticker':'REMOVED','entity':'old:FAILURE','day':day,'value':99,'dimensions':{'node':'FAILURE'}},
    ])
    store.ingest('runtime','graph-buckets',[],contributions=contributions)
    store.ingest(
        "capture_coverage",
        "runtime-proof",
        [
            {
                "kind": "capture_coverage",
                "ticker": "",
                "id": "runtime",
                "data": {
                    "source": "runtime",
                    "started_at": (now - timedelta(days=60)).isoformat(),
                    "end_at": (now + timedelta(days=2)).isoformat(),
                    "closed_end_at": (now + timedelta(days=2)).isoformat(),
                    "complete": True,
                    "tables_json": json.dumps(
                        [
                            "runtime_v2_cases",
                            "runtime_v2_w3_cases",
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
    metric = response.json()["data"]["data"]["failed_cases"]
    assert metric["current"]["state"] == "AVAILABLE"
    assert metric["current"]["value"] == str(failed_count * 3)
    assert metric["current_coverage"]["state"] == "COMPLETE"
    assert "nonroutine_repairs" not in response.json()["data"]["data"]


def test_failed_case_period_recovery_and_frozen_view(tmp_path, monkeypatch):
    from contextvars import ContextVar
    from doxagent.v2_read import query_budget
    monkeypatch.setattr(query_budget,'frozen_proofs',ContextVar('test_proofs',default=None))
    from doxagent.api_v2.overview import failed_cases_metric
    store=ReadStore(tmp_path/'read.db');store.migrate()
    proof={'kind':'capture_coverage','ticker':'','id':'runtime','data':{
        'complete':True,'started_at':'2026-09-01T00:00:00Z','end_at':'2026-10-01T00:00:00Z',
        'tables_json':json.dumps(['runtime_v2_cases','runtime_v2_w3_cases'])}}
    values=[{'metric':'graph_cases','ticker':ticker,'entity':identity,'day':day,
             'value':1,'dimensions':{'node':'FAILURE'}}
            for ticker,identity,day in [('MU','a','2026-09-08'),('INTC','b','2026-09-08'),
                                        ('MU','prior','2026-09-07'),('MU','outside','2026-09-01')]]
    seq=store.ingest('runtime','initial',[proof],contributions=values)
    view={'seq':seq,'tickers':['MU','INTC'],'wire':{'period':{
        'current':{'trading_days':['2026-09-08']},'previous':{'trading_days':['2026-09-07']}}}}
    metric=failed_cases_metric(store,view)
    assert metric['current']['value']=='2' and metric['previous']['value']=='1'
    assert metric['change_pct']['value']=='100'
    # A recovered Case replaces its existing bucket contribution; no failed-attempt sum.
    changed=store.ingest('runtime','recovered',[],contributions=[{**values[0],'value':0}])
    assert failed_cases_metric(store,{**view,'seq':changed})['current']['value']=='1'
    assert failed_cases_metric(store,view)['current']['value']=='2'
