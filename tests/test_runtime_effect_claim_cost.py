"""Effect claiming scales with unfinished obligations, not terminal history."""
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import RuntimeEffect, RuntimeSideEffect, utc_now


def seeded(tmp_path,monkeypatch):
    now=utc_now();monkeypatch.setattr('doxagent.persistent_runtime_v2.repository.utc_now',lambda:now)
    repo=SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db')
    with repo._connect() as db:
        db.execute("INSERT INTO runtime_v2_cases VALUES('case','source','MU','2026-10-29','COMPLETED','OK','{}',?,?)",(now.isoformat(),now.isoformat()))
        db.executemany("INSERT INTO runtime_v2_effects VALUES(?, 'case','archive_message',?,'COMPLETED',0,?,?,?)",
            [(f'history-{i}',f'history-{i}',now.isoformat(),json.dumps({'old_wide_value':'x'*2048}),now.isoformat()) for i in range(2000)])
    return repo,now


def add(repo,now,name,*,age,status='PENDING',lease=None,updated_age=0):
    e=RuntimeEffect(effect_id=name,case_id='case',effect_type=RuntimeSideEffect.ARCHIVE_MESSAGE,
        idempotency_key=name,status=status,available_at=now-timedelta(seconds=age),
        lease_token=4 if status=='RUNNING' else 0,lease_until=lease,updated_at=now-timedelta(seconds=updated_age))
    repo.enqueue_effect(e)


def test_claim_ignores_terminal_history_and_preserves_order_limit_and_leases(tmp_path,monkeypatch):
    repo,now=seeded(tmp_path,monkeypatch)
    add(repo,now,'pending',age=20)
    add(repo,now,'expired',age=30,status='RUNNING',lease=now-timedelta(seconds=1))
    add(repo,now,'legacy-expired',age=10,status='RUNNING',updated_age=121)
    add(repo,now,'leased',age=100,status='RUNNING',lease=now+timedelta(seconds=90))
    add(repo,now,'legacy-active',age=100,status='RUNNING',updated_age=119)
    add(repo,now,'future',age=-1)
    connect=repo._connect;steps=[]
    def bounded():
        db=connect();budget=[0]
        def progress():budget[0]+=100;return budget[0]>15000
        db.set_progress_handler(progress,100);steps.append(budget);return db
    monkeypatch.setattr(repo,'_connect',bounded)
    first=repo.claim_effects(limit=2)
    assert [e.effect_id for e in first]==['expired','pending']
    assert [e.lease_token for e in first]==[5,1]
    assert all(e.lease_until==now+timedelta(seconds=120) for e in first)
    assert [e.effect_id for e in repo.claim_effects(limit=2)]==['legacy-expired']
    assert repo.claim_effects(limit=2)==[]
    assert all(v[0]<15000 for v in steps)


def test_independent_claimers_never_take_the_same_effect(tmp_path,monkeypatch):
    repo,now=seeded(tmp_path,monkeypatch)
    for name in ['one','two']:add(repo,now,name,age=1)
    other=SQLitePersistentRuntimeV2Repository(repo.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda r:r.claim_effects(limit=1),[repo,other]))
    assert {v[0].effect_id for v in results}=={'one','two'}
    assert all(v[0].lease_token==1 for v in results)
