from datetime import UTC, datetime, timedelta

from doxagent.api_v2.article_samples import reduce_articles, select_article_facts_many
from doxagent.api_v2.source_catalog import aggregate_metrics
from doxagent.v2_read.repository import ReadStore

NOW = datetime(2026, 11, 20, 8, tzinfo=UTC)


def records(key, found, published, *, ticker="MU", kind="realtime", pending=False):
    return [dict(kind="native:article_body_results", ticker="", id=key, source_id="news", data={
        "first_observed_at":found.isoformat(), "initial_result_at":None if pending else (found+timedelta(seconds=2)).isoformat(),
        "initial_outcome":None if pending else "FULL", "outcome":None if pending else "FULL", "status":"PENDING" if pending else "READY"}),
        dict(kind="native:live_deliveries", ticker=ticker, id=key+":"+ticker, source_id="news", parent=key, data={
            "first_discovered_at":found.isoformat(), "consumable_at":published.isoformat() if published else None,
            "first_publish_kind":kind, "initial_decision":"PUBLISHED" if published else None,
            "state":"WAIT_BODY" if pending else "PUBLISHED"})]


def test_all_recent_samples_no_twenty_row_limit_and_no_minimum(tmp_path):
    store = ReadStore(tmp_path/"read.db");store.migrate()
    rows=[]
    for i in range(30):
        found=NOW-timedelta(minutes=i+1)
        rows += records(str(i),found,found+timedelta(seconds=3))
    store.ingest("test","all",rows)
    facts=select_article_facts_many(store,[("MU","news")],NOW)["MU","news"]
    assert len(facts)==30
    metrics=reduce_articles(facts)
    assert len(metrics["latencies"])==30 and metrics["body_full"]==30
    assert len(metrics["first_body_seconds"])==30
    from doxagent.api_v2.article_samples import GLOBAL_ARTICLE_FACTS_SQL
    import json
    with store.connect() as db:
        args=((NOW-timedelta(hours=24)).timestamp()/86400+2440587.5,
              NOW.timestamp()/86400+2440587.5,(NOW-timedelta(hours=24)).timestamp(),json.dumps([['MU','news']]))
        plan=[tuple(r)[-1] for r in db.execute('EXPLAIN QUERY PLAN '+GLOBAL_ARTICLE_FACTS_SQL,args)]
    assert not any('current_identity (kind=?)' in step or 'source_delivery_parent (kind=?)' in step for step in plan), [s for s in plan if 'current_identity' in s or 'source_delivery_parent' in s]


def test_old_publication_is_separate_history_without_reading_old_unfinished_work(tmp_path):
    store = ReadStore(tmp_path/"read.db");store.migrate()
    found=NOW-timedelta(days=3)
    store.ingest("test","old",records("old",found,found+timedelta(seconds=8)))
    store.ingest("test","pending",records("pending",NOW-timedelta(hours=30),None,pending=True))
    facts=select_article_facts_many(store,[("MU","news")],NOW)["MU","news"]
    result=reduce_articles(facts)
    assert result["latencies"]==[] and result["body_total"]==0
    assert result["last_historical"]["e2e_seconds"]==8
    assert result["last_historical_body"]["full"] is True
    assert not any(r['article_key']=='pending' for r in facts)
    assert 'unfinished_count' not in result


def test_completion_window_recovery_class_and_global_article_unit_are_independent(tmp_path):
    store=ReadStore(tmp_path/"read.db");store.migrate()
    found=NOW-timedelta(hours=25);end=NOW-timedelta(minutes=2)
    store.ingest("test","recover",records("recovered",found,end,kind="capability_resume"))
    recent=NOW-timedelta(minutes=5)
    store.ingest("test","first",records("normal",recent,recent+timedelta(seconds=3)))
    store.ingest("test","fanout",records("normal",recent,recent+timedelta(seconds=6),ticker="INTC"))
    grouped=select_article_facts_many(store,[("MU","news"),("INTC","news")],NOW)
    result=aggregate_metrics([r for rs in grouped.values() for r in rs],[],[],0)
    assert result["mean_e2e_seconds"]==3 and result["e2e_samples"]==1
    assert result["publication_classes"]["capability_resume"]["samples"]==1
    assert result["publication_classes"]["realtime"]["samples"]==1
    assert result["body_total"]==1
    compact=select_article_facts_many(store,[("MU","news"),("INTC","news")],NOW,global_only=True)
    compact_metrics=aggregate_metrics([r for rs in compact.values() for r in rs],[],[],0)
    assert compact_metrics==result
    assert sum(map(len,compact.values()))<sum(map(len,grouped.values()))


def test_recent_article_full_ratio_uses_available_body_not_its_old_failed_attempt(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    found=NOW-timedelta(minutes=5)
    rows=records('recovered-body',found,found+timedelta(seconds=3))
    rows[0]['data'].update(initial_outcome='UNAVAILABLE',outcome='FULL')
    store.ingest('test','body-now-full',rows)
    metrics=reduce_articles(select_article_facts_many(store,[('MU','news')],NOW)['MU','news'])
    assert metrics['body_total']==metrics['body_full']==1
    assert metrics['first_body_seconds']==[2]
