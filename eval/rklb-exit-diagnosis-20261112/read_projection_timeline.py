import os,sqlite3,json,re
from datetime import datetime,timezone
from doxagent.v2_read.repository import ReadStore
p=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path;d=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('pragma query_only=ON')
root='trade:case_f8319410bef746ca998e29279eaaa216';case='case_f8319410bef746ca998e29279eaaa216'
seqs=[r[0] for r in d.execute("select valid_from from objects where ticker='RKLB' and kind in ('case','execution') and id in (?,?) order by valid_from",(root,case))]
commits=[dict(r) for r in d.execute('select * from commits where seq in ('+','.join('?' for _ in seqs)+') order by seq',seqs)]
cp=d.execute("select position from checkpoints where source='runtime'").fetchone()[0]
s=sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True);s.row_factory=sqlite3.Row;s.execute('pragma query_only=ON')
originids=[int(r['source_event']) for r in commits if r['source']=='runtime' and str(r['source_event']).isdigit()]
low=min(originids+[cp]);entity=json.dumps([root],separators=(',',':'))
events=[dict(r) for r in s.execute('select seq,table_name,entity_id,operation,recorded_at from v2_source_outbox where seq>=? and table_name=? and entity_id=? order by seq limit 100',(low,'te_executions',entity))]
near=[dict(r) for r in s.execute('select seq,table_name,recorded_at from v2_source_outbox where seq>=? order by seq limit 1',(cp,))]
head=s.execute('select max(seq) from v2_source_outbox').fetchone()[0]
out=dict(at=datetime.now(timezone.utc).isoformat(),commits=commits,checkpoint=cp,head=head,checkpoint_event=near,execution_events=events)
print(json.dumps(out))
