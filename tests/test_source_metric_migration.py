import sqlite3
from datetime import UTC, datetime, timedelta

from doxagent.message_bus_v2.metric_migration import migrate
from doxagent.v2_read.repository import ReadStore


def test_release_backfill_is_idempotent_and_keeps_old_members_of_recent_articles(tmp_path):
    bus=tmp_path/'bus.db'; read=tmp_path/'read.db'
    store=ReadStore(read); store.migrate()
    now=datetime.now(UTC); old=(now-timedelta(days=3)).isoformat(); recent=now.isoformat()
    with sqlite3.connect(bus) as db:
        db.execute('CREATE TABLE live_deliveries(article_key,ticker,source_id,standard_id,first_discovered_at,'
            'first_poll_due_at,consumable_at,state,initial_decision,first_publish_kind)')
        db.executemany('INSERT INTO live_deliveries VALUES(?,?,?,?,?,?,?,?,?,?)',[
            ('shared','MU','news','m1',old,old,old,'PUBLISHED','PUBLISHED',None),
            ('shared','INTC','news','m2',recent,recent,recent,'PUBLISHED','PUBLISHED','realtime'),
            ('pending','MU','news',None,old,old,None,'WAIT_BODY',None,None)])
    result=migrate(bus,read)
    assert result=={'read_metrics':'installed','scalar_delivery_rows':2}
    assert migrate(bus,read)=={'read_metrics':'already_installed'}
    with store.connect() as db:
        rows=db.execute("SELECT ticker,parent,day "
            "FROM object_current WHERE kind='native:live_deliveries'").fetchall()
        assert len(rows)==2 and all(r['day'] for r in rows)
        assert {r['ticker'] for r in rows}=={'MU','INTC'}
