"""Compare fixed observation windows, including unsuccessful and missing cycles."""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from doxagent.message_bus_v2.cadence import cadence, distribution

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
phases = [
    ('before', '2026-10-25T18:11:12.546526+00:00', '2026-10-25T18:19:00+00:00'),
    ('writer_batch', '2026-10-25T18:22:00+00:00', '2026-10-25T18:31:00+00:00'),
    ('body_fixed', '2026-10-25T18:34:00+00:00', '2026-10-25T18:41:00+00:00'),
    ('shared_intake_fixed', '2026-10-25T18:45:00+00:00', '2026-10-25T18:59:00+00:00'),
    ('ticker_intake_fixed', '2026-10-25T19:02:00+00:00', '2026-10-25T19:17:00+00:00'),
    ('writer_fairness_fixed', '2026-10-25T19:21:00+00:00', '2026-10-25T19:35:00+00:00'),
    ('scalar_receipts_fixed', '2026-10-25T19:39:00+00:00', '2026-10-25T19:54:00+00:00'),
    ('batched_ledger_fixed', '2026-10-25T19:58:00+00:00', '2026-10-25T20:16:00+00:00'),
    ('fused_handoff_fixed', '2026-10-25T20:21:00+00:00', '2026-10-25T20:28:00+00:00'),
    ('capture_reuse_fixed', '2026-10-25T20:35:00+00:00', min(data['at'], '2026-10-25T21:11:12+00:00')),
]
summary = {'at':data['at'], 'backlog':data['backlog'], 'new_articles':data['new_articles'], 'phases':{}}
for label, since, until in phases:
    since, until = max(since, data['since']), min(until, data['at'])
    if since >= until:
        continue
    runs = [r for r in data['runs'] if since <= r['dispatch_at'] < until]
    captures = [r for r in data['captures'] if since <= r['observed_at'] < until]
    fanout = [{**r, **target} for r in captures for target in (json.loads(r.get('targets_json') or '[]') or [{}])]
    measured = cadence([*data['schedules'], *runs], fanout, datetime.fromisoformat(since), datetime.fromisoformat(until))
    groups = defaultdict(list)
    for row in runs:
        if row['unit'] == 'TAIL:SWEEP':
            continue  # Historical sweeps are not ordinary real-time poll latency.
        groups[row['source_id']].append(row)
    sources = {}
    for source, rows in groups.items():
        walls, pre, physical = [], [], {}
        request_starts = defaultdict(list)
        for row in rows:
            if row.get('finished_at'):
                walls.append((datetime.fromisoformat(row['finished_at'])-datetime.fromisoformat(row['dispatch_at'])).total_seconds())
            requests = row.get('requests', [])
            stamps = [datetime.fromisoformat(r['request_started_at']) for r in requests if r.get('request_started_at')]
            if stamps:
                first = min(stamps)
                pre.append((first-datetime.fromisoformat(row['dispatch_at'])).total_seconds())
                if not row['unit'].startswith('TAIL:'):
                    request_starts[(row['binding_id'],row['unit'])].append(first)
            for r in requests:
                physical[(r.get('request_key'),r.get('request_started_at'))] = r
        costs = [(datetime.fromisoformat(r['response_received_at'])-datetime.fromisoformat(r['request_started_at'])).total_seconds()
                 for r in physical.values() if r.get('response_received_at') and r.get('request_started_at')]
        gaps = [(b-a).total_seconds() for stamps in request_starts.values()
                for a,b in zip(sorted(set(stamps)), sorted(set(stamps))[1:])]
        sources[source] = {'runs':len(rows), 'statuses':dict(Counter(r['status'] for r in rows)),
            'reasons':dict(Counter(r['reason'] for r in rows if r.get('reason'))),
            'wall_seconds':distribution(walls), 'dispatch_to_request_seconds':distribution(pre),
            'physical_request_seconds':distribution(costs), 'physical_requests':len(physical),
            'actual_request_gap_seconds':distribution(gaps)}
    executions = [json.loads(r['payload']) for r in data['body_executions'] if since <= r['created_at'] < until]
    pending_captures = [r for r in captures if r['status'] not in ('COMPLETED','CANCELLED')]
    first = [(datetime.fromisoformat(r['first_intake_at'])-datetime.fromisoformat(r['observed_at'])).total_seconds()
             for r in captures if r.get('first_intake_at')]
    complete = [(datetime.fromisoformat(r['completed_at'])-datetime.fromisoformat(r['observed_at'])).total_seconds()
                for r in captures if r.get('completed_at')]
    summary['phases'][label] = {'since':since,'until':until,'sources':sources,'cadence':measured,
        'captures':len(captures),'unsettled_captures':len(pending_captures),
        'capture_to_first_intake_seconds':distribution(first),'capture_to_complete_seconds':distribution(complete),
        'body':{'executions':len(executions),'full':sum(x.get('succeeded') and x.get('outcome') in ('FULL','SHORT_FULL') for x in executions),
                'failures':dict(Counter(x.get('reason') for x in executions if not x.get('succeeded'))),
                'work_kinds':dict(Counter(x.get('work_kind') for x in executions)),
                'duration_seconds':distribution([(datetime.fromisoformat(x['completed_at'])-datetime.fromisoformat(x['started_at'])).total_seconds() for x in executions])}}
output = Path(sys.argv[2])
output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:{'captures':v['captures'],'unsettled':v['unsettled_captures'],'body':v['body']} for k,v in summary['phases'].items()},ensure_ascii=False))
