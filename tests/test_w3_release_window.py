import importlib.util
import json
from datetime import UTC,datetime,timedelta
from pathlib import Path
import sqlite3

import pytest


@pytest.mark.parametrize('status,offset,kind,blocked',[
    ('RUNNING',30,'route_to_w3',True),
    ('RUNNING',30,'w3_post_decision',True), # SDK already succeeded, consumer still owns Effect.
    ('RUNNING',-30,'route_to_w3',False),
    ('PENDING',-30,'w3_post_decision',True),
    ('PENDING_RETRY',30,'w3_post_decision',False),
    ('COMPLETED',30,'w3_post_decision',False),
    ('RUNNING',30,'emit_delta',False),
])
def test_publication_idle_includes_result_consumption(tmp_path,status,offset,kind,blocked):
    script=Path(__file__).resolve().parents[1]/'scripts/ops/w3_latency_release_20261111.py'
    spec=importlib.util.spec_from_file_location('release_window',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'runtime.db';now=datetime(2026,11,11,tzinfo=UTC)
    stamp=(now+timedelta(seconds=offset)).isoformat()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runtime_v2_effects(status,effect_type,available_at,payload_json)')
        db.execute('INSERT INTO runtime_v2_effects VALUES(?,?,?,?)',
                   (status,kind,stamp,json.dumps(dict(lease_until=stamp))))
    if blocked:
        with pytest.raises(ValueError,match='keep serving'):module.assert_scheduler_idle(path,now=now)
    else:module.assert_scheduler_idle(path,now=now)
