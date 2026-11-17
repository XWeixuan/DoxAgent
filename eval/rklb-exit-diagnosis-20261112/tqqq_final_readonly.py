"""Independent verification only. All trading methods disabled."""
import json, sqlite3, re
from datetime import datetime, timezone
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('pragma query_only=ON')
rev = json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0])
p = historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?', (rev,)).fetchone()[0])).model_copy(update={'client_id': 195624})
db.close()
REF = 'DA-TEST-TQQQ1-20261112-ONCE'
events, opened = [], []
def deny(*a, **kw):
    raise AssertionError('READONLY_NO_ORDER')
b = IbkrSession(p, event_sink=lambda k,v: events.append((k,v)), write_guard=deny)
try:
    b.connect()
    b.app.placeOrder = deny
    b.app.cancelOrder = deny
    snap = b.sync()
    completed = [r for r in b.orders.values() if r.get('order_ref') == REF]
    original = b.app.openOrder
    def observe(order_id, c, o, s):
        if o.orderRef == REF:
            opened.append(dict(order_id=order_id,perm_id=o.permId,status=s.status))
        original(order_id,c,o,s)
    b.app.openOrder = observe
    b.open_event.clear()
    b.app.reqAllOpenOrders()
    assert b.open_event.wait(5)
    fills = [v for k,v in events if k=='fill' and v.get('order_ref') == REF]
    result = dict(at=datetime.now(timezone.utc).isoformat(), orders_submitted=0,
                  complete=snap['complete'],history_complete=snap['history_complete'],
                  completed_test_orders=completed, open_test_orders=opened, fills=fills,
                  tqqq_quantity=snap['positions'].get(72539702,snap['positions'].get('72539702','0')))
    assert result['complete'] and result['history_complete'] and not opened and not fills
    assert len(completed)==1 and completed[0]['perm_id']==372543003 and completed[0]['status']=='Cancelled'
    assert result['tqqq_quantity']=='0'
finally:
    b.close()
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(result)))
