"""One ordinary-login/verify cycle on the already authenticated Barron's identity.

No credentials entered, no challenge solved; failures keep the original page.
"""
import json
import runpy
import time

request=runpy.run_path('/usr/local/sbin/doxagent-site-login-admin')['_request']


def operation(payload):
    payload['idempotency_key']='barrons-maintenance-acceptance-'+str(time.time_ns())
    op=request('POST','/v1/maintenance/operations',payload,timeout=5)
    seen=None
    for _ in range(30):
        row=request('GET','/v1/maintenance/operations/'+op['operation_id'],timeout=8)
        if (row['state'],row['stage'])!=seen:
            print(json.dumps(row),flush=True)
            seen=(row['state'],row['stage'])
        if row['state'] in ('DONE','FAILED','CANCELLED','INTERRUPTED'):
            return row
        time.sleep(2)
    raise RuntimeError('operation receipt still pending')


opened=operation({'action':'OPEN_LOGIN','scope_id':'source:barrons_ticker_news','identity_id':'dowjones-main'})
if opened['state']=='DONE':
    time.sleep(3)
    operation({'action':'VERIFY_LOGIN','session_id':opened['result']['session_id']})
