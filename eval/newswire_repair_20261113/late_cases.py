import sqlite3,json
from datetime import datetime,UTC
from doxagent.v2_read.native_content import NativeContent
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row;codec=NativeContent('/data/bus/bus.sqlite3')
out={}
for sid in ['businesswire_massive','globenewswire_massive']:
 vals=[]
 for r in c.execute("select * from live_articles indexed by live_article_observed where first_seen_at>=? and first_seen_at<? and source_id=? and delivery_mode='production'",('2026-11-12T20:05:46','2026-11-12T22:51:30',sid)):
  v=codec.decode(codec.decode(json.loads(r['input_ref'])));lag=(datetime.fromisoformat(r['first_seen_at'])-datetime.fromisoformat(v['published_at'].replace('Z','+00:00'))).total_seconds()
  if lag>180:vals.append({'url':r['url'],'first_seen_at':r['first_seen_at'],'published_at':v['published_at'],'lag':lag,'metadata':{k:v.get('metadata',{}).get(k) for k in ['language','publication_time_precision','discovery_method','pipeline_trace']},'raw_payload':{k:v.get('raw_payload',{}).get(k) for k in ['releaseDate','listing_time','listing_url','feed_url']}})
 out[sid]=sorted(vals,key=lambda x:x['lag'],reverse=True)[:4]
print(json.dumps(out))
