"""Explicit current-source rebuild after technical receipt expiry.

Each source is read at a consistent SQLite snapshot; a temporary consumer pins
the bridge from that snapshot to subsequent CDC. It never writes fake backfill
events into a business source or re-executes a business effect.
"""
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime, timedelta
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

from .outbox import TABLES, quoted
from .repository import ReadStore, encode, instant
from .source_lifecycle import BUSINESS_FACT_KINDS


class SnapshotStore(ReadStore):
    """Batch only a new, unpublished target; live readers retain normal commits."""
    _import_db=None
    _serial=0
    _depth=0
    _pending=0
    defer_initialization_progress=True
    defer_graph_projection=True

    @contextmanager
    def importing(self):
        with super().connect(write=True) as db:
            db.execute('PRAGMA busy_timeout=60000')
            # Isolated bulk import only: SQLite's tiny default page cache spills
            # the same index pages repeatedly while importing a Library snapshot.
            db.execute('PRAGMA cache_size=-65536')
            self._import_db=db
            try:yield
            finally:self._import_db=None

    @contextmanager
    def connect(self, *, write=False, timeout=2):
        db=self._import_db
        if db is None:
            with super().connect(write=write,timeout=timeout) as connection:yield connection
            return
        self._serial+=1;name='snapshot_'+str(self._serial);self._depth+=1
        db.execute('SAVEPOINT '+name)
        try:
            yield db
            db.execute('RELEASE '+name)
        except BaseException:
            db.execute('ROLLBACK TO '+name);db.execute('RELEASE '+name)
            raise
        finally:self._depth-=1
        self._pending+=int(write)
        if self._depth==0 and self._pending>=500:
            db.commit();db.execute('BEGIN IMMEDIATE');self._pending=0


def settle_cases(store):
    """Settle derivatives only after all attempts/executions exist."""
    from .graph import project as project_graph
    from .runtime import hot_path_contributions,w3_contributions
    with store.connect() as db:
        cases=[(r[0],r[1],json.loads(r[2])) for r in db.execute("SELECT ticker,id,payload FROM object_current WHERE kind='case'")]
    for ticker,identity,summary in cases:
        records,counts=project_graph(store,ticker,summary,[])
        native=store.get('native:runtime_v2_cases',ticker,identity)
        if native:
            counts.extend(hot_path_contributions(store,native))
            counts.extend(w3_contributions(store,native))
        store.ingest('business-baseline','settle:'+identity,records,contributions=counts)
    return len(cases)


def rebuild_current(sources, target, *, event_root=None, max_batches=10000):
    from .artifacts import PublishedArtifacts
    from .formal import FormalProjectors
    from .projectors import DomainProjectors
    from .projector import ProjectionWorker
    from .maintenance import atomic_json
    target=Path(target).resolve()
    if target.exists():raise ValueError('current snapshot target must be new')
    store=SnapshotStore(target);store.migrate()
    bridge='snapshot:'+uuid4().hex
    report=dict(format='doxagent.current-source-rebuild.1',path=str(target),sources={},rows=0,gaps=[])
    lease_until=datetime.now(UTC)+timedelta(hours=24)
    report['bridge_expires_at']=instant(lease_until)
    research=next((PublishedArtifacts(s.path) for s in sources if s.source=='research'),None)
    mapper=FormalProjectors(store,research,event_root)
    opened={};deferred=[]
    def event(source,db,table,row):
        value=dict(row);coordinate=value.pop('_snapshot_rowid')
        keys=[r[1] for r in sorted((r for r in db.execute('PRAGMA table_info('+quoted(table)+')') if r[5]),key=lambda r:r[5])]
        identity=encode([value[k] for k in keys]) if keys else str(coordinate)
        return dict(source=source.source,table_name=table,entity_id=identity,operation='BACKFILL',
                    row=source.content.source_row(value),recorded_at=None,seq=report['sources'][source.source]['head'])
    def apply(source,db,table,row,*,control_seed=False):
        if datetime.now(UTC)>=lease_until:raise TimeoutError('SOURCE_SNAPSHOT_BRIDGE_EXPIRED')
        e=event(source,db,table,row)
        records,counts=(DomainProjectors(store)(e) if control_seed else mapper(e))
        if control_seed:records=[r for r in records if r['kind'] in {'ticker','navigation'}]
        store.ingest(source.source,'snapshot:'+('seed:' if control_seed else '')+table+':'+e['entity_id'],records,contributions=counts)
    try:
        with ExitStack() as stack, store.importing():
            for source in sources:
                source.acknowledge_exceptions(bridge,source.head(),[])
                db=stack.enter_context(closing(source.connection()));db.execute('BEGIN')
                state=db.execute('SELECT epoch,head FROM v2_source_state WHERE source=?',(source.source,)).fetchone()
                capture=db.execute('SELECT tables_json FROM v2_capture_meta WHERE source=?',(source.source,)).fetchone()
                if not state or not capture:raise ValueError('snapshot capture identity missing')
                report['sources'][source.source]=dict(epoch=state['epoch'],head=state['head'])
                opened[source.source]=(source,db,json.loads(capture[0]))
            runtime=opened.get('runtime')
            if runtime:
                source,db,tables=runtime
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_serving_business_facts'").fetchone():
                    seeds=[json.loads(r[0]) for r in db.execute("SELECT payload FROM v2_serving_business_facts WHERE kind IN ('policy_hit_set','policy_admission')")]
                    for value in seeds:
                        value['sort']=value.pop('sort_key',value['id']);value['search']=value.pop('search_text','') or ''
                        store.ingest('business-baseline',uuid4().hex,[value])
                for table in ('v2_business_imports','v2_analysis_admission','v2_ticker_control'):
                    if table not in tables:continue
                    for row in db.execute('SELECT rowid AS _snapshot_rowid,* FROM '+quoted(table)):
                        apply(source,db,table,row,control_seed=table=='v2_ticker_control')
            for name in ('research','initialization','bus','runtime','events','usage'):
                if name not in opened:continue
                source,db,tables=opened[name]
                for table in TABLES[name]:
                    if table not in tables:continue
                    table_started=time.monotonic();table_rows=0
                    # Runtime process namespaces are filtered before loading large
                    # frozen inputs; the same whitelist as capture/backfill applies.
                    predicate=''
                    if table=='runtime_values':
                        predicate=' WHERE namespace IN ('+','.join("'"+n+"'" for n in RUNTIME_NAMESPACES)+')'
                    for row in db.execute('SELECT rowid AS _snapshot_rowid,* FROM '+quoted(table)+predicate+' ORDER BY rowid'):
                        report['rows']+=1
                        table_rows+=1
                        try:apply(source,db,table,row)
                        except (ValueError,KeyError,TypeError) as exc:
                            deferred.append((name,table,row['_snapshot_rowid'],str(exc)))
                    print(json.dumps(dict(snapshot_source=name,table=table,rows=table_rows,seconds=round(time.monotonic()-table_started,2))),file=sys.stderr,flush=True)
            for _ in range(3):
                remaining=[]
                for name,table,rowid,_ in deferred:
                    source,db,_tables=opened[name]
                    row=db.execute('SELECT rowid AS _snapshot_rowid,* FROM '+quoted(table)+' WHERE rowid=?',(rowid,)).fetchone()
                    try:apply(source,db,table,row)
                    except (ValueError,KeyError,TypeError) as exc:remaining.append((name,table,rowid,str(exc)))
                if len(remaining)==len(deferred):break
                deferred=remaining
            report['gaps']=[dict(source=n,table=t,rowid=r,reason=e) for n,t,r,e in deferred]
            # Import the complete node/event ledger first. Recomputing its
            # aggregate for every historical event makes a rebuild quadratic.
            from .initialization import progress
            with store.connect() as db:
                runs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM object_current WHERE kind='native:initialization_runs'")]
            for run in runs:
                store.ingest('business-baseline','progress:'+run['initialization_id'],
                             [progress(store,run,{'kind':'snapshot-complete'})])
            if runtime:
                source,db,_=runtime
                for kind,ticker,identity,raw in db.execute('SELECT kind,ticker,id,payload FROM v2_serving_business_facts'):
                    value=json.loads(raw)
                    if kind.startswith('metric:'):
                        store.ingest('business-baseline',uuid4().hex,[],contributions=[value])
                    elif kind.startswith('field:'):
                        actual_kind=kind[6:];current=store.get(actual_kind,ticker,identity)
                        if current and current.get('runtime_activation_id')==value.get('runtime_activation_id'):
                            store.ingest('business-baseline',uuid4().hex,[dict(kind=actual_kind,ticker=ticker,id=identity,data={**current,**value})])
                    elif kind not in ('policy_hit_set','policy_admission'):
                        value['sort']=value.pop('sort_key',value['id']);value['search']=value.pop('search_text','') or ''
                        store.ingest('business-baseline',uuid4().hex,[value])
            # All historical attempts/executions are now present. The target is
            # unpublished: only its final graph is observable.
            report['settled_cases']=settle_cases(store)
            with store.connect(write=True) as db:
                for name,state in report['sources'].items():
                    db.execute('INSERT OR REPLACE INTO source_epochs VALUES(?,?)',(name,state['epoch']))
                    db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)',(name,state['head']))
                for gap in report['gaps']:
                    db.execute('INSERT OR REPLACE INTO gaps(source,event,reason) VALUES(?,?,?)',
                               (gap['source'],'snapshot:'+gap['table']+':'+str(gap['rowid']),gap['reason']))
        # Read transactions ended; subsequent source changes remain bridged.
        store.defer_initialization_progress=False
        store.defer_graph_projection=False
        worker=ProjectionWorker(store,sources,mapper,consumer_id=bridge)
        for _ in range(max_batches):
            if datetime.now(UTC)>=lease_until:raise TimeoutError('SOURCE_SNAPSHOT_BRIDGE_EXPIRED')
            worker.tick(limit=500)
            if all(store.position(s.source)>=s.head() for s in sources):break
        else:raise TimeoutError('current snapshot catchup incomplete')
        with store.connect() as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('snapshot rebuild integrity failed')
            report.update(generation=db.execute('SELECT id FROM generation').fetchone()[0],seq=store.highwater(db),
                          verified=not db.execute('SELECT 1 FROM gaps LIMIT 1').fetchone())
        report['at']=instant(datetime.now(UTC));atomic_json(str(target)+'.current-snapshot.json',report)
        return report
    finally:
        for source in sources:source.retire_consumer(bridge)


RUNTIME_NAMESPACES=('trade_intents','trade_receipts','analysis_trade_decisions','candidates','worker_receipts',
 'worker_requests','round_inputs','schedule','selection_snapshot','worker_invocations','reference_deliveries',
 'policy_direction_usage','policy_consumptions','policy_authorization_head','policy_authorization_applied',
 'policy_consumption_migration','policy_watch','policy_watch_applied','policy_watch_validation','message_pins',
 'w3_operations_applied','w3_global_input','w3_operations_input')
