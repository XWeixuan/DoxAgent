"""Bounded read-only post-configuration check; no BODY or model requests."""
import json, os, sqlite3
from datetime import UTC, datetime

path = os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
c = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=2)
c.row_factory = sqlite3.Row
c.execute('pragma query_only=on')
out = {'at': datetime.now(UTC).isoformat()}
out['google_since_change'] = [dict(r) for r in c.execute(
    "select d.ticker,d.state,d.initial_decision,d.regex_result,d.jev_result,"
    "d.last_error,count(*) n from live_articles a indexed by live_article_observed "
    "join live_deliveries d using(article_key) where a.first_seen_at>=? "
    "and d.source_id='google_news_search_rss' group by 1,2,3,4,5,6",
    ('2026-11-17T13:31:09',))]
out['warm_query_plan'] = [dict(r) for r in c.execute(
    'EXPLAIN QUERY PLAN select stream_item_id,stream_offset,published_at '
    'from stream_items where ticker=? order by stream_offset desc limit 64', ('IONQ',))]
out['member_query_plan'] = [dict(r) for r in c.execute(
    'EXPLAIN QUERY PLAN select standard_message_id from stream_members '
    'where stream_item_id=? order by member_index limit 64', ('nonexistent',))]
out['publication_modes'] = [dict(r) for r in c.execute(
    "select json_extract(data_json,'$.streaming.publication_mode') mode,count(*) n "
    "from ticker_source_bindings where tombstoned_at is null and enabled=1 group by 1")]
print(json.dumps(out, ensure_ascii=False))
