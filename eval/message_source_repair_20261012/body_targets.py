"""Select existing article URLs for read-only final-publisher checks."""
import json
import sqlite3

db = sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro', uri=True, timeout=2)
for source in ('barrons_ticker_news', 'digitimes_tw_rss', 'investorshub_ticker_news'):
    seen = set()
    rows = []
    for (data,) in db.execute('select data_json from raw_messages where source_id=? order by collected_at desc limit 100', (source,)):
        value = json.loads(data)
        url = value.get('url', '')
        if not url or url in seen or (source == 'barrons_ticker_news' and 'barrons.com/' not in url):
            continue
        seen.add(url)
        rows.append({k: value.get(k) for k in ('title', 'url', 'published_at')})
        if len(rows) == 2:
            break
    print(json.dumps({'source': source, 'articles': rows}, ensure_ascii=False))
print(json.dumps({'ir_sources': [json.loads(r[0]).get('source_id') for r in db.execute('select data_json from source_definitions') if 'bloom' in r[0].lower() or '_ir' in r[0].lower()]}))
