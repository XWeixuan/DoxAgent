import os, sqlite3, json
from datetime import datetime, timezone
from doxagent.v2_read.repository import ReadStore
p = ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']).path
d = sqlite3.connect('file:' + str(p) + '?mode=ro', uri=True)
d.row_factory = sqlite3.Row
d.execute('pragma query_only=ON')
d.execute('begin')
root = 'trade:case_f8319410bef746ca998e29279eaaa216'
case = 'case_f8319410bef746ca998e29279eaaa216'
seqs = [r[0] for r in d.execute("select valid_from from objects where ticker='RKLB' and kind in ('case','execution') and id in (?,?)", (root, case))]
out = {'at': datetime.now(timezone.utc).isoformat()}
out['case_commits'] = [dict(r) for r in d.execute('select c.*,r.projected_at from commits c left join commit_retention r on r.seq=c.seq where c.seq in (' + ','.join('?' for _ in seqs) + ') order by c.seq', seqs)]
out['health'] = [dict(r) for r in d.execute("select * from source_health where source='runtime'")]
out['checkpoint'] = d.execute("select position from checkpoints where source='runtime'").fetchone()[0]
out['recent_runtime'] = [dict(r) for r in d.execute("select c.*,r.projected_at from commits c left join commit_retention r on r.seq=c.seq where c.seq<=? and c.source='runtime' order by c.seq desc limit 8", (d.execute("select value from read_meta where key='highwater'").fetchone()[0],))]
print(json.dumps(out))
