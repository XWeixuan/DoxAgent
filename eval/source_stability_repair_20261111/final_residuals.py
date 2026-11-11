"""Bounded final-window diagnostic from existing rows, no website access."""
import collections
import json
import re
import sqlite3

c = sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3?mode=ro', uri=True, timeout=2)
c.row_factory = sqlite3.Row
c.execute('pragma query_only=on')
since = '2026-11-11T18:33:45.641277+00:00'
until = '2026-11-11T19:04:09.953015+00:00'
out = []
seen = set()
for r in c.execute("""select source_id,dispatch_at,data_json from cycles indexed by cycles_time
 where dispatch_at>=? and dispatch_at<? and source_id in ('defensescoop_drones','corporate_ir_press_releases','yahoo_finance_news') and status='FAILED'""", (since, until)):
    data = json.loads(r['data_json'])
    run = data.get('physical_run_id') or data.get('run_id')
    if run in seen:
        continue
    seen.add(run)
    message = re.sub(r'https?://[^\s;]+', '<url>', data.get('terminal_error_message') or '')[:320]
    raw = c.execute('select requests_json from physical_cycles where run_id=?', (run,)).fetchone()
    requests = json.loads(raw[0]) if raw else []
    statuses = collections.Counter(str(e.get('status_code', e.get('http_status'))) for e in requests)
    out.append({'source_id': r['source_id'], 'at': r['dispatch_at'], 'message': message, 'http_status_counts': statuses})
c.close()
print(json.dumps(out))
