from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import sqlite3

from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.storage_status import capacity


def test_capacity_alert_reuses_existing_channel_and_resolves_with_space(tmp_path):
    p=tmp_path/'bus.db'
    with sqlite3.connect(p) as db:db.execute('CREATE TABLE operational_alerts(alert_id PRIMARY KEY,alert_key UNIQUE,severity,resolved_at,last_seen_at,data_json)')
    source=SourceOutbox(p,'bus');now=datetime.now(UTC)
    assert capacity(source,now=now,free=9*1024**3)['alert']
    assert not capacity(source,now=now,free=40*1024**3)['alert']
    with closing(source.connection(write=True)) as db:
        row=db.execute('SELECT data_json FROM operational_alerts').fetchone()
        assert json.loads(row[0])['resolved_at']
        assert db.execute('SELECT count(*) FROM v2_storage_capacity_samples').fetchone()[0]==1
        db.execute('INSERT INTO v2_storage_capacity_samples VALUES(?,?,?)',((now-timedelta(days=7)).date().isoformat(),(now-timedelta(days=7)).isoformat(),100*1024**3));db.commit()
    assert capacity(source,now=now,free=40*1024**3)['days_left']<7
