"""Bounded read-only receipt for the current COIN activation window."""
import argparse
import json
import sqlite3
import re
from collections import Counter,defaultdict
from datetime import UTC,datetime
from pathlib import Path

from doxagent.message_bus_v2.collection_plane.store import path_for
from doxagent.settings import DoxAgentSettings


def seconds(a,b):
    return (datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds() if a and b else None


def stats(values):
    v=sorted(x for x in values if x is not None)
    return {'n':len(v),'mean':round(sum(v)/len(v),3),'p95':round(v[min(len(v)-1,int(len(v)*.95))],3),'max':round(v[-1],3)} if v else {'n':0}


def redact(value):
    if isinstance(value,str):return re.sub(r'([?&](?:token|api_key|apikey|key)=)[^&\s\x27\x22<>]*',r'\1[REDACTED]',value,flags=re.I)
    if isinstance(value,list):return [redact(x) for x in value]
    if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--since',required=True);a=p.parse_args()
    settings=DoxAgentSettings();now=datetime.now(UTC).isoformat();out={'at':now,'since':a.since,'elapsed_seconds':seconds(a.since,now),'sources':{}}
    db=sqlite3.connect(path_for(settings.message_bus_v2_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1);db.execute('PRAGMA query_only=ON')
    results=defaultdict(lambda:{'status':Counter(),'reasons':Counter(),'errors':[],'dispatch_delay':[],'discovery_cycle':[],'units':set()})
    for sid,unit,status,reason,due,dispatch,data in db.execute("SELECT source_id,unit,status,reason,due_at,dispatch_at,data_json FROM cycles WHERE ticker=? AND dispatch_at>=? AND status!='SCHEDULE'",('COIN',a.since)):
        r=results[sid];d=json.loads(data);r['status'][status]+=1;r['units'].add(unit)
        if reason:r['reasons'][reason]+=1
        error=d.get('terminal_error_message')
        if error and len(r['errors'])<3 and error not in r['errors']:r['errors'].append(error)
        r['dispatch_delay'].append(seconds(due,dispatch))
        if d.get('list_validated'):r['discovery_cycle'].append(seconds(dispatch,d.get('listing_received_at')))
    for sid,r in results.items():out['sources'][sid]={**r,'status':dict(r['status']),'reasons':dict(r['reasons']),'units':sorted(r['units']),'dispatch_delay':stats(r['dispatch_delay']),'discovery_cycle':stats(r['discovery_cycle'])}
    out['x_stream']=json.loads((db.execute('SELECT value FROM meta WHERE key=?',('x_official_stream',)).fetchone() or ['{}'])[0])
    out['capture_queue']=[list(x) for x in db.execute('SELECT status,count(*) FROM captures WHERE observed_at>=? GROUP BY status',(a.since,))]
    db.close()
    with sqlite3.connect(Path(settings.message_bus_v2_sqlite_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.execute('PRAGMA query_only=ON')
        out['deliveries']=[dict(source=r[0],state=r[1],body=r[2],error=r[3],count=r[4]) for r in db.execute('SELECT d.source_id,d.state,a.outcome,d.last_error,count(*) FROM live_deliveries d JOIN live_articles a ON a.article_key=d.article_key WHERE d.ticker=? GROUP BY d.source_id,d.state,a.outcome,d.last_error',('COIN',))]
        out['published_after_activation']=[list(r) for r in db.execute('SELECT source_id,count(*),min(consumable_at),max(consumable_at) FROM live_deliveries WHERE ticker=? AND consumable_at>=? GROUP BY source_id',('COIN',a.since))]
        out['recent_articles']=[dict(source=r[0],title=r[1],url=r[2],first_seen=r[3],state=r[4],body=r[5],error=r[6],published=r[7]) for r in db.execute('SELECT d.source_id,a.title,a.url,a.first_seen_at,d.state,a.outcome,d.last_error,d.consumable_at FROM live_articles a JOIN live_deliveries d ON d.article_key=a.article_key AND d.ticker=? WHERE a.first_seen_at>=? ORDER BY a.first_seen_at DESC LIMIT 30',('COIN',a.since))]
    # Stream diagnostics contain no API credential; keep only operational fields.
    out['x_stream']={k:v for k,v in out['x_stream'].items() if k in {'state','accounts','at','connected_at','last_post_at','received','error','http_status'}}
    print(json.dumps(redact(out)))


if __name__=='__main__':main()
