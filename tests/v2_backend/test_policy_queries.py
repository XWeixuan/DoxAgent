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


def test_current_formal_policies_remain_readable_with_unknown_consumption(tmp_path):
    import json
    from doxagent.api_v2.policies import selection
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    records=[]
    for identity,lifecycle,effective,consumed in [
        ('known','ACTIVE',True,False), ('unknown','ACTIVE',None,None),
        ('consumed','ACTIVE',None,True), ('ineffective','ACTIVE',False,None),
        ('retired','RETIRED',None,None), ('other-shell','ACTIVE',None,None),
    ]:
        records.append({'kind':'policy_catalog','ticker':'MU','id':identity,'data':{
            'lifecycle':lifecycle,'effective':available(effective) if effective is not None else missing(),
            'consumed':available(consumed) if consumed is not None else missing(),
            'shell_ids':['S2' if identity=='other-shell' else 'S1'], 'decision':'LONG',
        }})
    seq=store.ingest('test','seed',records)
    sql,args=selection('MU',seq,'S1',['2026-10-01'])
    with store.connect() as db:
        found=db.execute('SELECT id,payload FROM ('+sql+') WHERE (ACTIVE OR ACTIVE_UNKNOWN) ORDER BY id',args).fetchall()
        assert [r[0] for r in found]==['known','unknown']
        assert json.loads(found[1][1])['effective']['value'] is None
        assert db.execute('SELECT sum(ACTIVE) FROM ('+sql+')',args).fetchone()[0]==1

    control = ControlRepository(RuntimeJournal(tmp_path / 'runtime.db'))
    control.migrate()
    seq = store.ingest('test','shell',[{'kind':'shell','ticker':'MU','id':'d2:S1','data':{'shell_id':'S1'}}])
    view = store.save_token('developer','fixture',{
        'seq':seq,'as_of':'2026-10-01T00:00:00Z','wire':{
            'ticker':'MU','page':'POLICIES','activation':{'data':{'document2':{'run_id':'d2'}}},
            'period':{'selected':'ALL','previous':None},
        }},view=True)
    with TestClient(create_app(store=store,control=control,auth=OfflineAuth())) as client:
        response = client.get(PREFIX+'/tickers/MU/policies/metrics',params={'view_id':view,'shell_id':'S1'},headers={'Authorization':'Bearer offline'})
        assert response.status_code == 200,response.text
        metrics = response.json()['data']['data']
        assert metrics['active']['current']['value'] == '2'
        assert metrics['long_ratio']['current']['value'] == '1'
        assert metrics['short_ratio']['current']['value'] == '0'
        assert metrics['active']['provisional'] is True
        assert metrics['active']['current_coverage']['state'] == 'PARTIAL'
        assert metrics['added']['current']['value'] == '0'


def test_set_queries_keep_revision_identity_history_and_direction_state(tmp_path):
    from doxagent.api_v2.policy_queries import metrics, page, selection
    store = ReadStore(tmp_path / 'sets.db')
    store.migrate()
    store.migrate_policy_query_indexes()
    store.migrate_policy_query_indexes()  # Explicit migration is safe to repeat.
    def summary(lifecycle='ACTIVE', effective=True, consumed=False, **extra):
        return {'lifecycle': lifecycle, 'effective': available(effective) if effective is not None else missing(),
                'consumed': available(consumed) if consumed is not None else missing(), 'shell_ids': ['S1'], **extra}
    first = summary(executable_directions=['LONG', 'SHORT'])
    records = [{'kind': 'policy_catalog', 'ticker': 'MU', 'id': identity, 'data': data} for identity, data in [
        ('P1:r1', first), ('P1:old', summary('RETIRED', False)),
        ('P2:r2', summary(effective=None, consumed=None, decision='LONG')),
        ('P3:r3', summary(effective=False, consumed=True, decision='SHORT')),
    ]]
    for identity, key, kind, shell in [('a', 'P1:r1', 'ADD', 'S1'), ('a2', 'P1:r1', 'ADD', 'S2'),
                                       ('m', 'P1:r1', 'MODIFY', 'S1'), ('m2', 'P1:r1', 'MODIFY', 'S1'),
                                       ('r', 'P1:old', 'RETIRE', 'S1')]:
        records.append({'kind': 'policy_change', 'ticker': 'MU', 'id': identity, 'route': key,
                        'parent': shell, 'day': '2026-10-13', 'data': {'type': kind}})
    def fact(entity, key, day, metric='policy_ar_hits', value='1'):
        return {'metric': metric, 'ticker': 'MU', 'entity': entity, 'day': day, 'value': value,
                'dimensions': {'business_key': key}}
    frozen = store.ingest('test', 'seed-sets', records, contributions=[
        fact('h1', 'P1:r1', '2026-10-13'), fact('h2', 'P1:r1', '2026-10-12'),
        fact('old', 'P1:old', '2026-10-12'), fact('e1', 'P1:r1', '2026-10-13', 'policy_executed'),
    ])
    latest = store.ingest('test', 'withdraw-consume', [
        {'kind': 'policy_catalog', 'ticker': 'MU', 'id': 'P1:r1', 'data': summary(executable_directions=['SHORT'])},
    ], contributions=[fact('h1', 'P1:r1', '2026-10-13', value='0')])
    # Keep the old correlated implementation solely as an equivalence oracle
    # on this tiny fixture; never run the expensive query on production.
    from tests.v2_backend.policy_query_reference import selection as reference
    for watermark in [frozen, latest]:
        for shell in ['ALL', 'S1', 'S2']:
            for days in [None, ['2026-10-13'], ['2026-10-12'], ['2026-10-12', '2026-10-13'], []]:
                with store.connect() as db:
                    old_sql, old_args = reference('MU', watermark, shell, days)
                    new_sql, new_args = selection('MU', watermark, shell, days, db=db)
                    fields = 'id,ACTIVE,ACTIVE_UNKNOWN,ADDED,HIT,MODIFIED,RETIRED,EXECUTED'
                    old_rows = db.execute('SELECT '+fields+' FROM ('+old_sql+') ORDER BY id', old_args).fetchall()
                    new_rows = db.execute('SELECT '+fields+' FROM ('+new_sql+') ORDER BY id', new_args).fetchall()
                    assert [tuple(r) for r in new_rows] == [tuple(r) for r in old_rows]
                counts = metrics(store, 'MU', watermark, shell, days)
                assert counts['active'] == sum(bool(r['ACTIVE'] or r['ACTIVE_UNKNOWN']) for r in old_rows)
                assert counts['unknown_count'] == sum(bool(r['ACTIVE_UNKNOWN']) for r in old_rows)
                for flag in ['ADDED', 'HIT', 'MODIFIED', 'RETIRED', 'EXECUTED']:
                    assert counts[flag.lower()] == sum(bool(r[flag]) for r in old_rows)
    old = metrics(store, 'MU', frozen, 'ALL', ['2026-10-13'])
    assert {k: old[k] for k in ['active', 'long_active', 'short_active', 'added', 'modified', 'retired', 'hit', 'executed', 'unknown_count']} == {
        'active': 2, 'long_active': 2, 'short_active': 1, 'added': 1, 'modified': 1, 'retired': 1, 'hit': 1, 'executed': 1, 'unknown_count': 1}
    fresh = metrics(store, 'MU', latest, 'S1', ['2026-10-13'])
    assert fresh['long_active'] == 1 and fresh['short_active'] == 1 and fresh['hit'] == 0
    for days, hits in [(None, 2), (['2026-10-12'], 2), (['2026-10-12', '2026-10-13'], 2), ([], 0)]:
        result = metrics(store, 'MU', latest, 'S1', days)
        assert result['active'] == 2
        assert result['hit'] == hits
        for selected in ['ADDED', 'HIT', 'MODIFIED', 'RETIRED', 'EXECUTED']:
            rows, _ = page(store, 'MU', latest, 'S1', days, selected, '', 100)
            assert len(rows) == result[selected.lower()]
    first_page, unknown = page(store, 'MU', frozen, 'S1', None, 'ACTIVE', '', 1)
    assert first_page[0]['id'] == 'P1:r1' and unknown == 1
    second_page, unknown = page(store, 'MU', frozen, 'S1', None, 'ACTIVE', 'P1:r1', 1)
    assert second_page[0]['id'] == 'P2:r2' and unknown == 1
    with store.connect() as db:
        sql, args = selection('MU', frozen, 'ALL', None, db=db)
        plan = '\n'.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql, args))
    assert 'policy_positive_current' in plan and 'policy_positive_history' in plan
    assert 'CORRELATED SCALAR SUBQUERY' not in plan
    assert 'LEFT-JOIN' not in plan  # No repeated scans of grouped CTEs on deployed SQLite.
