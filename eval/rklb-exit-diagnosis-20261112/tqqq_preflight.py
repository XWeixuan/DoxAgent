"""Read-only preflight for the separately authorized single TQQQ order."""
import os, sqlite3, json, re
from datetime import datetime, timezone
from decimal import Decimal
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('pragma query_only=ON')
revision = json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0])
profile = historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?', (revision,)).fetchone()[0]))
assert profile.environment == 'LIVE' and profile.account_type == 'MARGIN'
profile = profile.model_copy(update={'client_id': 195622})
events = []
def deny(*args, **kwargs):
    raise AssertionError('PREFLIGHT_READ_ONLY')
b = IbkrSession(profile, event_sink=lambda k, v: events.append((k, v)), write_guard=deny)
try:
    b.connect()
    b.app.placeOrder = deny
    b.app.cancelOrder = deny
    snap = b.sync()
    assert snap['complete'] and snap['history_complete']
    c = b.contract('TQQQ', 'SMART')
    assert c['con_id'] == 72539702 and c['symbol'] == 'TQQQ' and c['currency'] == 'USD'
    # The account-specific funds query and all-orders query remain read-only.
    b.open_event.clear()
    b.app.reqAllOpenOrders()
    assert b.open_event.wait(5), 'OPEN_ORDERS_INCOMPLETE'
    active = [o for o in b.orders.values() if o.get('contract', {}).get('con_id') == c['con_id'] and o.get('status') not in {'Filled', 'Cancelled', 'ApiCancelled', 'Rejected', 'Inactive'}]
    q = b.quote(c, 'BUY')
    assert q['market_data_type'] == 1 and Decimal(str(q['ask'])) > 10
    funds = snap['account_values']
    available = funds['AvailableFunds']
    assert available['currency'] == 'USD' and Decimal(available['value']) >= 10
    out = dict(at=datetime.now(timezone.utc).isoformat(), environment=profile.environment,
               account_type=profile.account_type, account=profile.expected_account_id,
               profile_revision=revision, client_id=profile.client_id, contract=c,
               available_funds=available, account_ready=funds.get('AccountReady'),
               initial_quantity=snap['positions'].get(c['con_id'], snap['positions'].get(str(c['con_id']), '0')),
               active_tqqq_orders=active, quote=q, intended_order=dict(side='BUY',quantity=1,order_type='LMT',limit_price='1.00',tif='DAY',outside_rth=True), orders_submitted=0)
finally:
    b.close()
    db.close()
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b', r'\1\2***\3', json.dumps(out, default=str)))
