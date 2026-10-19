"""Bounded existing-owner maintenance operations; no credentials or CAPTCHA actions."""
import json
import runpy
import sys
import time

request = runpy.run_path('/usr/local/sbin/doxagent-site-login-admin')['_request']
action = sys.argv[1]
if action == 'open':
    payload = {'action': 'OPEN_LOGIN', 'scope_id': 'source:investing_ticker_news',
               'identity_id': sys.argv[2] if len(sys.argv) > 2 else 'public-markets-nl'}
elif action in ('verify', 'close'):
    payload = {'action': 'VERIFY_LOGIN' if action == 'verify' else 'CLOSE_LOGIN', 'session_id': sys.argv[2]}
elif action == 'activate':
    payload = {'action': 'ACTIVATE_PRIMARY', 'scope_id': 'source:investing_ticker_news'}
else:
    raise ValueError('unsupported diagnostic operation')
payload['idempotency_key'] = 'investing-diagnostic-' + str(time.time_ns())
op = request('POST', '/v1/maintenance/operations', payload, timeout=15)
last = None
for _ in range(30):
    row = request('GET', '/v1/maintenance/operations/' + op['operation_id'], timeout=8)
    if (row['state'], row['stage']) != last:
        print(json.dumps(row), flush=True)
        last = (row['state'], row['stage'])
    if row['state'] in ('DONE', 'FAILED', 'CANCELLED', 'INTERRUPTED'):
        break
    time.sleep(2)
else:
    raise TimeoutError('maintenance operation remains pending')
