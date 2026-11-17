import json,collections
from decimal import Decimal
from pathlib import Path
root=Path('eval/reuters_applied_repair_20261116');x=json.loads((root/'ledger-after-new-task.json').read_text(encoding='utf-8-sig'));out=[]
for j in x['jobs']:
 e=j.get('application_evidence') or {};fp=e.get('solution_fingerprint');requests=e.get('requests',[])
 nav=next((r for r in requests if r.get('resource_type')=='Document'),{})
 api=next((r for r in requests if '/pf/api/' in r.get('url','')), {})
 cookie=lambda r:next((c.get('fingerprint') for c in r.get('request_cookies',[]) if c.get('name')=='datadome'),None)
 written=next((c.get('fingerprint') for c in e.get('cookies_after_write',[]) if c.get('name')=='datadome'),None)
 setfps=[c.get('fingerprint') for c in api.get('response_extra',{}).get('set_cookies',[]) if c.get('name')=='datadome']
 out.append({'task_id':j['task_id'],'identity':j['identity_id'],'applied_at':j['applied_at'],'read_state':e.get('read_state'),'provider_equals_written':bool(fp and fp==written),'provider_equals_document_sent':bool(fp and fp==cookie(nav)),'provider_equals_first_api_sent':bool(fp and fp==cookie(api)),'api_cookie_blocked':any(c.get('blocked_reasons') for c in api.get('associated_datadome',[])), 'document_status':nav.get('response',{}).get('status'),'first_api_status':api.get('response',{}).get('status'),'first_api_layer':api.get('wire_body',api.get('response',{})).get('layer'),'api_set_cookie_replaces_solution':bool(setfps and any(v!=fp for v in setfps)),'verification_state':j['verification_state'],'solver_verification':j['solver_verification'],'business_recovery':j['business_recovery'],'cost':j['cost']})
summary={'window_start':'2026-11-12T23:24:40Z','observed_at':x['at'],'n':len(out),'provider_sent_first_api':sum(r['provider_equals_first_api_sent'] for r in out),'api_200':sum(r['first_api_status']==200 for r in out),'api_401':sum(r['first_api_status']==401 for r in out),'verified_in_window':sum(r['solver_verification']=='VERIFIED_IN_WINDOW' for r in out),'known_cost':str(sum(Decimal(r['cost']) for r in out if r['cost'])),'cases':out}
(root/'chain-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='cases'}))
