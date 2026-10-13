from pathlib import Path
import json,sqlite3
root=Path('/home/ubuntu/v21-production-20261012');data=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
d=sqlite3.connect('file:'+str(data/'initialization/control.sqlite3')+'?mode=ro',uri=True)
for ticker in ('MU','BE','INTC'):
 p=root/('activation-submit-'+ticker+'.json')
 if not p.exists():print(ticker,'not submitted');continue
 x=json.loads(p.read_text());rid=x['initialization_id'];r=json.loads(d.execute('select payload from initialization_runs where id=?',(rid,)).fetchone()[0]);print(ticker,rid,r.get('status'),r.get('error'))
 for n in d.execute('select node_key,payload from initialization_nodes where run_id=?',(rid,)):
  a=json.loads(n[1]);print(' ',n[0],a.get('status'),a.get('error'))
 active=d.execute('select revision_id from ticker_active_revision where ticker=?',(ticker,)).fetchone();print('active',active[0]);print('acks',d.execute('select worker,revision_id from activation_worker_ack where ticker=?',(ticker,)).fetchall())
