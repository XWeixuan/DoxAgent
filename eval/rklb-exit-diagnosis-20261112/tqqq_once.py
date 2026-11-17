"""One authorized LIVE BUY 1 TQQQ at $1; cancel, never replace or hedge."""
import os, sqlite3, json, re, time, threading
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal as D
from doxagent.trade_execution.ibkr_session import IbkrSession
from doxagent.trade_execution.schema import historical_profile
from doxagent.trade_execution.sessions import parse_hours

CLIENT = 195623
REF = 'DA-TEST-TQQQ1-20261112-ONCE'
ROOT = Path('/data/runtime/acceptance/tqqq-buy1-low-limit-20261112')
ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
assert not (ROOT / 'buy.claim.json').exists(), 'ONE_SHOT_ALREADY_CLAIMED_NO_RESUBMIT'

def stamp():
    return datetime.now(timezone.utc).isoformat()

def save(name, value):
    target = ROOT / name
    tmp = target.with_suffix('.tmp')
    with tmp.open('w') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(target)

db = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True)
db.execute('pragma query_only=ON')
revision = json.loads(db.execute("select payload from runtime_values where namespace='execution_account_profiles' and key='LIVE'").fetchone()[0])
profile = historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?', (revision,)).fetchone()[0]))
baseline = json.loads(db.execute("select payload from te_executions where id='trade:case_f8319410bef746ca998e29279eaaa216'").fetchone()[0])
old = historical_profile(json.loads(db.execute('select payload from te_profiles where revision=?', (baseline['profile_revision'],)).fetchone()[0]))
assert profile.environment == 'LIVE' and profile.account_type == 'MARGIN'
assert profile.expected_account_id == old.expected_account_id
db.close()
profile = profile.model_copy(update={'client_id': CLIENT})
lock = threading.RLock()
events, raw_orders = [], []
submits = 0
cancels = 0
attempt = None
claimed = False
accepted = None
b = None

def sink(kind, value):
    record = dict(at=stamp(), kind=kind, value=dict(value))
    with lock:
        events.append(record)
        with (ROOT / 'events.jsonl').open('a') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(record, default=str) + '\n')
            f.flush()

def guard():
    assert claimed and attempt is not None, 'NO_WRITE_BEFORE_EXCLUSIVE_CLAIM'

def own_events(kind):
    with lock:
        return [x for x in events if x['kind'] == kind and
                (x['value'].get('order_ref') == REF or
                 (x['value'].get('order_id') == attempt['order_id'] and x['value'].get('client_id') == CLIENT))]

def latest_status():
    rows = own_events('order')
    return rows[-1]['value'].get('status') if rows else None

def cancel_own():
    global cancels
    if submits and not cancels and latest_status() not in {'Filled', 'Cancelled', 'ApiCancelled', 'Rejected', 'Inactive'}:
        # Persist before the call: never cancel somebody else's order or replace it.
        cancels += 1
        save('cancel-request.json', dict(at=stamp(), order_id=attempt['order_id'], client_id=CLIENT, order_ref=REF))
        b.cancel(attempt)

try:
    b = IbkrSession(profile, event_sink=sink, write_guard=guard)
    b.connect()
    original_open = b.app.openOrder
    def observe_open(order_id, contract, order, state):
        if order.account == profile.expected_account_id:
            with lock:
                raw_orders.append(dict(at=stamp(), order_id=order_id, client_id=order.clientId,
                    order_ref=order.orderRef, con_id=contract.conId, symbol=contract.symbol,
                    account=order.account, side=order.action, quantity=str(order.totalQuantity),
                    order_type=order.orderType, limit_price=str(order.lmtPrice), status=state.status,
                    perm_id=order.permId, what_if=order.whatIf, tif=order.tif, outside_rth=order.outsideRth))
        original_open(order_id, contract, order, state)
    b.app.openOrder = observe_open
    snap = b.sync()
    assert snap['complete'] and snap['history_complete']
    contract = b.contract('TQQQ', 'SMART')
    assert contract['con_id'] == 72539702 and contract['symbol'] == 'TQQQ'
    assert contract['currency'] == 'USD' and contract['primary_exchange'] == 'NASDAQ'
    conid = contract['con_id']
    initial = D(snap['positions'].get(conid, snap['positions'].get(str(conid), '0')))
    assert initial == 0, 'TEST_MUST_OPEN_LONG_NOT_COVER_SHORT'
    available = snap['account_values']['AvailableFunds']
    assert available['currency'] == 'USD' and D(available['value']) >= 10
    assert str(snap['account_values'].get('AccountReady')).lower() == 'true'
    # Fresh all-open snapshot; generic IbkrSession.orders omits contract, so use raw callback.
    with lock:
        raw_orders.clear()
    b.open_event.clear()
    b.app.reqAllOpenOrders()
    assert b.open_event.wait(5), 'ALL_OPEN_ORDERS_INCOMPLETE'
    with lock:
        active = [r for r in raw_orders if r['con_id'] == conid and r['status'] not in {'Filled','Cancelled','ApiCancelled','Rejected','Inactive'}]
    assert not active, 'EXISTING_TQQQ_ORDER_ABORT_TEST'
    quote = b.quote(contract, 'BUY')
    now = datetime.now(timezone.utc)
    assert any(a <= now < z for a, z in parse_hours(contract['trading_hours'], contract['time_zone'])), 'SMART_SESSION_CLOSED'
    assert quote['market_data_type'] == 1 and D(str(quote['ask'])) > 10
    assert D(str(quote.get('bid', 0))) > 10 and D(str(quote['ask'])) >= D(str(quote['bid']))
    assert 0 <= time.monotonic() - quote['ask_received_mono'] < 5
    attempt = dict(account=profile.expected_account_id, client_id=CLIENT, order_id=b.next_id,
                   order_ref=REF, contract=contract, side='BUY', quantity=1,
                   order_type='LMT', limit_price='1.00', session='EXTENDED')
    save('before.json', dict(at=stamp(), account=profile.expected_account_id, profile_revision=revision,
                            contract=contract, quote=quote, initial_quantity=str(initial), available_funds=available))
    fd = os.open(ROOT / 'buy.claim.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(dict(at=stamp(), state='SUBMIT_ONCE_NO_RETRY', attempt=attempt), f)
        f.flush()
        os.fsync(f.fileno())
    claimed = True
    original_place, original_cancel = b.app.placeOrder, b.app.cancelOrder
    def place_once(order_id, c, o):
        global submits
        assert submits == 0 and order_id == attempt['order_id']
        assert c.conId == conid and c.symbol == 'TQQQ' and c.secType == 'STK' and c.currency == 'USD'
        assert o.account == profile.expected_account_id and o.orderRef == REF
        assert o.action == 'BUY' and D(o.totalQuantity) == 1 and o.orderType == 'LMT'
        assert D(str(o.lmtPrice)) == D('1.00') and not o.whatIf and o.transmit and o.parentId == 0
        submits += 1
        save('submit-call.json', dict(at=stamp(), orders_submitted=submits, attempt=attempt))
        return original_place(order_id, c, o)
    def cancel_only(order_id, *args):
        assert submits == 1 and cancels == 1 and order_id == attempt['order_id']
        return original_cancel(order_id, *args)
    b.app.placeOrder = place_once
    b.app.cancelOrder = cancel_only
    b.submit(attempt)  # Sole order submission. Never invoke normal intents/hedge/WhatIf.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = latest_status()
        if status == 'Submitted':
            accepted = dict(at=stamp(), status=status, order_id=attempt['order_id'])
            save('accepted.json', accepted)
            break
        if status in {'Filled','Cancelled','ApiCancelled','Rejected','Inactive'}:
            break
        time.sleep(0.05)
    cancel_own()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if latest_status() in {'Filled','Cancelled','ApiCancelled','Rejected','Inactive'}:
            break
        time.sleep(0.1)
    final = b.sync()
    with lock:
        fills = [x['value'] for x in events if x['kind'] == 'fill' and x['value'].get('order_ref') == REF]
        ours = [r for r in raw_orders if r['order_ref'] == REF]
        order_events = own_events('order')
    b.open_event.clear()
    fresh_open = []
    original_observer = b.app.openOrder
    def final_open(order_id, c, o, state):
        if o.account == profile.expected_account_id and o.orderRef == REF:
            fresh_open.append(dict(order_id=order_id, status=state.status, perm_id=o.permId))
        original_observer(order_id, c, o, state)
    b.app.openOrder = final_open
    b.app.reqAllOpenOrders()
    assert b.open_event.wait(5), 'FINAL_ALL_OPEN_INCOMPLETE'
    final_qty = D(final['positions'].get(conid, final['positions'].get(str(conid), '0')))
    result = dict(at=stamp(), environment='LIVE', account=profile.expected_account_id, client_id=CLIENT,
                  order_ref=REF, order_id=attempt['order_id'], orders_submitted=submits,
                  cancel_requests=cancels, accepted=accepted, last_status=latest_status(),
                  initial_quantity=str(initial), final_quantity=str(final_qty), fills=fills,
                  own_orders=ours, own_order_events=order_events, open_test_orders=fresh_open,
                  complete=final['complete'], history_complete=final['history_complete'],
                  errors=b.errors)
    result['success'] = bool(accepted and submits == 1 and result['last_status'] == 'Cancelled'
                             and not fills and final_qty == initial and not fresh_open
                             and final['complete'] and final['history_complete'])
    save('result.json', result)
    print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b', r'\1\2***\3', json.dumps(result)), flush=True)
except BaseException as exc:
    save('error.json', dict(at=stamp(), error=str(exc), orders_submitted=submits,
                           cancel_requests=cancels, attempt=attempt))
    raise
finally:
    if b:
        try:
            if attempt and submits:
                cancel_own()
        finally:
            b.close()
