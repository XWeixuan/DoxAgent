import json,sqlite3,re,collections
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row;groups=collections.defaultdict(list)
for r in c.execute("select article_key,url,first_seen_at,outcome from live_articles indexed by live_article_observed where first_seen_at>=? and source_id='globenewswire_massive' and delivery_mode='production'",('2026-11-12T20:00:46',)):
 m=re.search(r'/news-release/\d+/\d+/\d+/(\d+)/(\d+)/([^/]+)/',r['url'])
 if m:groups[(m[1],m[3])].append(dict(r))
print(json.dumps({'duplicate_release_ids':[{'release':k,'articles':v} for k,v in groups.items() if len(v)>1],'late_ids':{k[0]:v for k,v in groups.items() if k[0] in ['3402217','3402216','3402218','3402215']}}))
