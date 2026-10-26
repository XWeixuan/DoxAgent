"""All scheduled cycles and article timelines; retain failures and boundary debt."""
import collections
import json
import sys
from pathlib import Path
from doxagent.message_bus_v2.cadence import cadence, instant, distribution

p = Path(sys.argv[1])
d = json.loads(p.read_text(encoding='utf-8-sig'))
c = cadence(d['runs']+d['schedules'],d['captures'],instant(d['since']),instant(d['at']))
sources = collections.defaultdict(list)
for unit in c['units']:
    source = unit['binding_id'].split(':',1)[-1]
    sources[source].append(unit)
out = {k:v for k,v in c.items() if k!='units'}
out['sources'] = []
for source, units in sources.items():
    sums = {key:sum(u[key] for u in units) for key in ('expected_cycles','fresh_cycles','missing_cycles','request_cycles','pending_cycles','late_recovered_cycles')}
    sums.update(source_id=source,units=len(units),coverage=sums['fresh_cycles']/sums['expected_cycles'] if sums['expected_cycles'] else None,
        statuses=dict(sum((collections.Counter(u['statuses']) for u in units),collections.Counter())))
    out['sources'].append(sums)
out['sources'].sort(key=lambda x:((x['coverage'] if x['coverage'] is not None else 2),x['source_id']))
out['tail_debt']=d['tail_debt']
out['backlog']=d['backlog']
out['reuse']=d['reuse']
out['jobs']=dict(collections.Counter(f"{r['work_kind']}:{r['status']}" for r in d['jobs']))
out['job_queue']=d['job_queue']
out['body_results']=dict(collections.Counter(r['outcome'] or r['status'] for r in d['new_body_records']))
out['consumable_receipts']=len(d['delivery_receipts'])
out['intake_commit_delay']=distribution([(instant(r['committed_at'])-instant(r['observed_at'])).total_seconds() for r in d['captures']])
out['candidate_intake_delay']=distribution([(instant(r['first_intake_at'])-instant(r['observed_at'])).total_seconds() for r in d['captures'] if r['first_intake_at']])
out['censored']=d['censored']
out['units']=c['units']
p.with_name(p.stem+'-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k not in ('units','sources','tail_debt','backlog','reuse')},ensure_ascii=False))
print(json.dumps(out['sources'][:15],ensure_ascii=False))
