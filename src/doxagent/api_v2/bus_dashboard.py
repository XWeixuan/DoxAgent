"""One minute dashboard: scalar head reads and indexed, bounded recent windows."""
import json
import math
from datetime import UTC, datetime
from decimal import Decimal

from doxagent.message_bus_v2.collection_plane.observability import binding_states,store_from_environment
from doxagent.message_bus_v2.collection_plane.source_rounds import recent_rounds
from doxagent.v2_read.repository import instant
from .bus import source_status
from .bus_metrics import metrics,source_counts_today
from .dto import available,missing


def current_body(store,ticker,view,source_ids):
    """Current article quality, not a materialized historical payload JOIN."""
    window=view['wire']['period']['current']
    where="b.kind='native:article_body_results' AND b.ticker=''"
    args=[]
    if view['wire']['period']['selected']!='ALL':
        where+=" AND json_extract(b.payload,'$.first_observed_at')>=? AND json_extract(b.payload,'$.first_observed_at')<?"
        args.extend([window['start_at'].replace('Z','+00:00'),window['end_at'].replace('Z','+00:00')])
    source=""
    if source_ids is not None:source=' AND o.source_id IN (SELECT value FROM json_each(?))'
    # Start with the existing timestamp index; match current observations by
    # parent identity. A current-day refresh never walks old ticker observations.
    observed="o.kind='article_observation' AND o.ticker=?"+source
    matches=("EXISTS(SELECT 1 FROM object_current o INDEXED BY current_parent WHERE "+observed+" AND o.parent=b.id) "
        "OR EXISTS(SELECT 1 FROM object_current a INDEXED BY current_parent "
        "JOIN object_current o INDEXED BY current_parent ON o.parent=a.id WHERE a.kind='article_alias' "
        "AND a.ticker='' AND a.parent=b.id AND "+observed+")")
    scope=[ticker,*([json.dumps(source_ids)] if source_ids is not None else [])]
    args.extend(scope+scope)
    with store.connect() as db:
        rows=db.execute("SELECT b.id,json_extract(b.payload,'$.outcome'),json_extract(b.payload,'$.status') "
            "FROM object_current b INDEXED BY object_current_body_cohort WHERE "+where+" AND ("+matches+") "
            "AND coalesce(json_extract(b.payload,'$.eligible'),1)=1 "
            "AND coalesce(json_extract(b.payload,'$.legacy_result'),0)=0",args).fetchall()
    unique={r[0]:(r[1],r[2]) for r in rows}
    full=sum(r[0] in {'FULL','SHORT_FULL'} for r in unique.values())
    pending=sum(r[0] not in {'FULL','SHORT_FULL'} and r[1]=='PENDING' for r in unique.values())
    return {'total':len(unique),'full':full,'pending':pending,'failed':len(unique)-full-pending,
            'ratio':full/len(unique) if unique else None,'network_attempts':None,'network_succeeded':None,
            'cache_reuses':None,'basis':'FIRST_OBSERVED_ARTICLE','legacy_excluded':True}


def recent_business(store,ticker,rounds):
    windows=[{'source':bid.split(':',1)[1],'start':r['from_at'],'end':r['until_at']}
             for bid,r in rounds.items() if r['from_at']]
    result={s['source']:{'latencies':[],'articles':{}} for s in windows}
    if not windows:return result
    from doxagent.semantic_clock import semantic_day
    from datetime import timedelta
    for w in windows:
        first=semantic_day(datetime.fromisoformat(w['start']))
        last=semantic_day(datetime.fromisoformat(w['end']))
        w['days']=[(first+timedelta(days=n)).isoformat() for n in range((last-first).days+1)]
    with store.connect() as db:
        # Reuse current_source's complete (kind,ticker,source,day,sort) prefix.
        # Messages/body are exact identity lookups; no new index or historical scan.
        rows=db.execute("SELECT json_extract(w.value,'$.source'),d.parent,b.id,"
            "json_extract(b.payload,'$.outcome'),json_extract(b.payload,'$.status'),"
            "json_extract(m.payload,'$.pipeline.discovered_at'),json_extract(m.payload,'$.stream_published_at') "
            "FROM json_each(?) w CROSS JOIN json_each(w.value,'$.days') day "
            "CROSS JOIN object_current d INDEXED BY current_source "
            "ON d.kind='native:live_deliveries' AND d.ticker=? AND d.source_id=json_extract(w.value,'$.source') "
            "AND d.day=day.value AND d.sort_key>=json_extract(w.value,'$.start') "
            "AND d.sort_key<json_extract(w.value,'$.end') "
            "LEFT JOIN object_current b ON b.kind='native:article_body_results' AND b.ticker='' AND b.id=d.parent "
            "LEFT JOIN object_current m ON m.kind='message' AND m.ticker=d.ticker "
            "AND m.id=json_extract(d.payload,'$.standard_id') "
            "WHERE json_extract(d.payload,'$.first_poll_due_at') IS NOT NULL "
            "AND coalesce(json_extract(b.payload,'$.eligible'),1)=1",(json.dumps(windows),ticker)).fetchall()
    for source,key,body_key,outcome,status,found,published in rows:
        if found and published:
            duration=(datetime.fromisoformat(published)-datetime.fromisoformat(found)).total_seconds()
            if duration>=0:result[source]['latencies'].append(duration)
        if body_key:result[source]['articles'][key]=(outcome,status)
    return result


def mean_period(store,ticker,view,source_ids):
    days=view['wire']['period']['current']['trading_days']
    clause="metric IN ('bus_e2e_seconds','bus_e2e_samples') AND ticker=?"
    args=[ticker]
    if view['wire']['period']['selected']=='ALL':clause+=" AND day='*'"
    else:
        clause+=' AND day IN (SELECT value FROM json_each(?))';args.append(json.dumps(days))
    if source_ids is not None:
        clause+=' AND source_id IN (SELECT value FROM json_each(?))';args.append(json.dumps(source_ids))
    totals={}
    with store.connect() as db:
        for metric,value in db.execute('SELECT metric,value FROM metric_buckets_current WHERE '+clause,args):
            totals[metric]=totals.get(metric,Decimal(0))+Decimal(value)
    count=int(totals.get('bus_e2e_samples',0))
    return float(totals.get('bus_e2e_seconds',0)/count) if count else None,count


def dashboard_data(store,ticker,view,args):
    now=datetime.now(UTC)
    with store.connect() as db:
        # A minute dashboard is a live head, while the context supplies its calendar scope.
        seq=store.highwater(db)
        rows=db.execute("SELECT payload FROM object_current WHERE kind='native:ticker_source_bindings' "
                        "AND ticker=? AND json_extract(payload,'$.tombstoned_at') IS NULL",(ticker,)).fetchall()
    view={**view,'seq':seq}
    bindings=[json.loads(r[0]) for r in rows]
    with store.connect() as db:
        definitions={r[0]:json.loads(r[1]) for r in db.execute(
            "SELECT id,payload FROM object_current WHERE kind='native:source_definitions' AND ticker='' "
            "AND id IN (SELECT value FROM json_each(?))",(json.dumps([b['source_id'] for b in bindings]),))}
    ticker_state=store.get('ticker',ticker,ticker,seq) or {}
    live=binding_states([ticker])
    sources=[source_status(store,b,seq,view,live_states=live,source=definitions.get(b['source_id']),
                           ticker_state=ticker_state) for b in bindings]
    counts=dict(total=len(sources),running=0,sleeping=0,disabled=0,abnormal=0,unknown=0)
    for s in sources:
        state=s['countdown_state']
        if state=='DISABLED':counts['disabled']+=1
        elif state in {'CLOSED_SLEEP','WINDOW_CLOSED','PAUSED'}:counts['sleeping']+=1
        elif s['poll_status'] in {'partial','failed'}:counts['abnormal']+=1
        elif state=='SCHEDULED':counts['running']+=1
        else:counts['unknown']+=1
    rounds=recent_rounds(store_from_environment(),ticker,bindings,now)
    business=recent_business(store,ticker,rounds)
    today=source_counts_today(store,ticker,seq,view['wire']['clock']['semantic_day'])
    for s in sources:
        sid=s['source']['source_id'];r=rounds.get(s['source']['binding_id'],{})
        samples=business.get(sid,{'latencies':[],'articles':{}})
        times=sorted(samples['latencies']);articles=samples['articles']
        full=sum(v[0] in {'FULL','SHORT_FULL'} for v in articles.values())
        s['published_today']=today.get(sid,available(0)) if today is not None else missing()
        s['recent']={'coverage':{k:r.get(k,0 if k!='ratio' else None) for k in ['expected','succeeded','ratio']},
            'mean_e2e_seconds':sum(times)/len(times) if times else None,
            'p95_e2e_seconds':times[math.ceil(.95*len(times))-1] if times else None,'e2e_samples':len(times),
            'refresh_seconds':r.get('refresh_seconds'),'body_total':len(articles),'body_full':full,
            'body_ratio':full/len(articles) if articles else None}
    selected=[s['source']['source_id'] for s in sources if
              (not args.get('source_id') or s['source']['source_id']==args['source_id']) and
              (not args.get('source_kind') or s['source']['kind']==args['source_kind'])]
    selected_ids=selected if args.get('source_id') or args.get('source_kind') else None
    body=current_body(store,ticker,view,selected_ids)
    data=metrics(store,ticker,view,{k:args[k] for k in ['source_id','source_kind'] if args.get(k)},body=body)
    mean,count=mean_period(store,ticker,view,selected_ids)
    chosen=[s['recent']['coverage'] for s in sources if s['source']['source_id'] in selected]
    expected=sum(c['expected'] for c in chosen);success=sum(c['succeeded'] for c in chosen)
    return {'metrics':data,'sources':sources,'counts':counts,'mean_e2e_seconds':mean,'e2e_samples':count,
            'coverage':{'expected':expected,'succeeded':success,'ratio':success/expected if expected else None}}
