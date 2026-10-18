"""Real owner API canaries. No credentials, CAPTCHA interaction or publishing."""
import argparse
import asyncio
import json
import socket
import time
from pathlib import Path
from uuid import uuid4

import httpx
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

ROOT = Path('/site-data/registry/site-maintenance-sessions-20261019')


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['login', 'verify'])
    args = parser.parse_args()
    token = Path('/run/doxagent-site-access/admin-token').read_text().strip()
    results = []
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8011',
            headers={'Authorization': 'Bearer ' + token}, timeout=60, trust_env=False) as admin:
        async def inventory():
            response = await admin.get('/v1/maintenance/inventory')
            response.raise_for_status()
            return response.json()

        async def operate(action, **kwargs):
            start = time.monotonic()
            response = await admin.post('/v1/maintenance/operations', json={
                'action': action, 'idempotency_key': 'maintenance-session-canary:' + uuid4().hex,
                **kwargs,
            })
            response.raise_for_status()
            op = response.json()
            ack = time.monotonic() - start
            while op['state'] in ('QUEUED', 'RUNNING') and time.monotonic() - start < 55:
                await asyncio.sleep(.3)
                response = await admin.get('/v1/maintenance/operations/' + op['operation_id'])
                response.raise_for_status()
                op = response.json()
            row = {'action': action, 'state': op['state'], 'error': op.get('error'),
                   'result': op.get('result'), 'ack_seconds': ack, 'seconds': time.monotonic()-start}
            results.append(row)
            print(json.dumps(row), flush=True)
            return row

        sup = ChromeSupervisorClient('/run/doxagent-chrome/supervisor.sock', owner_id='v2-site-access')

        async def target_present(identity, target):
            status = await sup.status(identity)
            async with httpx.AsyncClient(trust_env=False, timeout=3) as http:
                targets = (await http.get(status['cdp_http_url'] + '/json/list')).json()
            return any(t['id'] == target for t in targets)

        inv = await inventory()
        if inv.get('active_operations'):
            raise RuntimeError('An operator is active; leave their work unchanged')
        if args.phase == 'login':
            for active in inv.get('sessions', []):
                if active['state'] == 'OPEN':
                    conflict = await operate('OPEN_LOGIN', scope_id='publisher:wsj', identity_id='wsj-main')
                    assert conflict['state'] == 'FAILED' and 'Viewer is in use by ' in conflict['error']
                    await operate('RELEASE_VIEWER', session_id=active['session_id'])
            for scope_id, identity in [('source:reuters_site_search','digitimes-de-1'),
                                       ('source:barrons_ticker_news','dowjones-main'),
                                       ('publisher:wsj','wsj-main')]:
                inv = await inventory()
                task = next((t for t in inv['challenges'] if t['identity_id'] == identity), None)
                opened = await operate('OPEN_LOGIN', scope_id=scope_id, identity_id=identity)
                assert opened['state'] == 'DONE' and opened['result']['stage'] == 'VIEWER_READY'
                session_id = opened['result']['session_id']
                repeated = await operate('OPEN_LOGIN', scope_id=scope_id, identity_id=identity)
                assert repeated['result']['session_id'] == session_id
                await asyncio.sleep(2.1)
                inv = await inventory()
                session = next(s for s in inv['sessions'] if s['session_id'] == session_id)
                assert session['kind'] == 'LOGIN'
                status = await sup.status(identity)
                assert int(Path('/run/doxagent-chrome/vnc-target').read_text()) == status['vnc_port']
                with socket.create_connection(('127.0.0.1',5900),timeout=3) as peer:
                    assert peer.recv(4) == b'RFB '
                row = {'identity':identity,'viewer_ready':True,'kind':session['kind']}
                if task and task['state'] != 'TARGET_LOST':
                    assert session['target_id'] != task['original_target_id']
                    row['original_target_preserved'] = await target_present(identity,task['original_target_id'])
                    assert row['original_target_preserved']
                results.append(row)
                await operate('CLOSE_LOGIN', session_id=session_id)  # This canary's new LOGIN only.
        else:
            task = next(t for t in inv['challenges'] if t['identity_id']=='digitimes-de-1')
            assert task['state'] != 'TARGET_LOST'
            opened = await operate('OPEN_CHALLENGE', task_id=task['task_id'])
            assert opened['state'] == 'DONE'
            checked = await operate('VERIFY_CHALLENGE',session_id=opened['result']['session_id'])
            assert checked['state'] == 'DONE', checked
            assert checked['result']['challenge'] == 'CLEARED', checked
    ROOT.mkdir(exist_ok=True)
    (ROOT / ('acceptance.'+args.phase+'.json')).write_text(json.dumps(results,indent=2))


asyncio.run(main())
