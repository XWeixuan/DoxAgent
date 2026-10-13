import json
from pathlib import Path
r=Path('/home/ubuntu/v21-production-20261012')
b=json.loads((r/'before-window.json').read_text())['semantics'];a=json.loads((r/'after-semantics.json').read_text())
brefs={x['ticker']:x['artifacts'] for x in b['active_revisions']};arefs={x['ticker']:x['artifacts'] for x in a['active_revisions']}
result={}
for t in ('MU','BE','INTC','RKLB'):
 fields=('event_library','monitoring_configuration') if t!='RKLB' else tuple(brefs[t])
 result[t]={k:arefs[t][k]==brefs[t][k] for k in fields}
 assert all(result[t].values()),(t,result[t])
result['monitoring_tables']={k:a['monitoring_tables'].get(k)==v for k,v in b['monitoring_tables'].items()}
bc={x['ticker']:json.loads(x['payload']) for x in b['control_rows']};ac={x['ticker']:json.loads(x['payload']) for x in a['control_rows']}
fields=('epoch','mode','requested_mode','removed','removal_epoch','cutoff_at','mode_effective_at')
result['control']={t:{k:ac[t].get(k)==bc[t].get(k) for k in fields} for t in bc}
assert all(all(v.values()) for v in result['control'].values())
(r/'preservation-proof.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
