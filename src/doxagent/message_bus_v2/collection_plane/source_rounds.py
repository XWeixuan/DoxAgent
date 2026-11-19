"""Rolling-24h source rounds. OR within alternative paths; AND across complements.

No writes or historical fallback; existing plans and covering-index receipts.
"""
from contextlib import nullcontext
import json
import math
from datetime import UTC, datetime


def stamp(value):
    return datetime.fromisoformat(value).timestamp() if value else None


def instant(value):
    return datetime.fromtimestamp(value, UTC).isoformat().replace('+00:00', 'Z')


def evaluate_rounds(plans, receipts, *, interval, until, limit=20, since=None):
    """Receipts are actual due opportunities, not just successful result rows."""
    if not plans:
        return {'expected': 0, 'succeeded': 0, 'ratio': None, 'refresh_seconds': None,
                'from_at': None, 'until_at': None, 'samples': 0}
    interval = max(1, interval)
    # Fully closed logical source slots, aligned in UTC. Unit phases remain real.
    end = math.floor(until / interval) * interval
    begin = max(end - (limit + 1) * interval,
                math.ceil(max(p['epoch'] for p in plans) / interval) * interval)
    proven = {}
    for receipt in receipts:
        if receipt['validated'] and receipt.get('received'):
            key = receipt['unit'], round(receipt['due'], 3)
            proven[key] = min(proven.get(key, receipt['received']), receipt['received'])
    if since is not None:
        begin = max(begin, math.ceil(since / interval)*interval)
    rounds = []
    for start in range(int(begin), int(end), interval):
        groups = {}
        for plan in plans:
            period, epoch = plan['interval'], plan['epoch']
            first = max(0, math.ceil((start - epoch) / period))
            last = math.ceil((start + interval - epoch) / period)
            due = [epoch + n * period for n in range(first, last)]
            if not due:
                continue
            times = [proven.get((plan['unit'], round(at, 3))) for at in due]
            completed = max(times) if all(at is not None for at in times) else None
            groups.setdefault(plan.get('group') or plan['unit'], []).append(completed)
        if not groups:
            continue
        winners = [min(t for t in times if t is not None)
                   if any(t is not None for t in times) else None for times in groups.values()]
        rounds.append((start, max(winners) if all(t is not None for t in winners) else None))
    selected = rounds[-limit:]
    fresh = sorted(at for _, at in rounds if at is not None)
    success = sum(at is not None for _, at in selected)
    return _summary(len(selected),success,fresh,selected[0][0] if selected else None,end)


def _summary(expected, succeeded, fresh, start, end):
    gaps = [b-a for a,b in zip(fresh, fresh[1:]) if b >= a]
    return {'expected': expected, 'succeeded': succeeded,
            'ratio': succeeded / expected if expected else None,
            'refresh_seconds': sum(gaps) / len(gaps) if gaps else None,
            'from_at': instant(start) if start is not None else None,
            'until_at': instant(end) if expected else None, 'samples': len(gaps)}


def _compact_rounds(db,binding_id,plans,period,now,until):
    """Index-only scalar proofs: OR alternatives, then AND required groups.

    Mean inter-completion gap telescopes to (last-first)/(count-1), so the
    complete 24h ledger need not be transferred or timestamp-parsed in Python.
    Materializing scalars first prevents SQLite sorting whole JSON documents.
    """
    end=math.floor(until/period)*period
    begin=max(math.ceil((now.timestamp()-86400)/period)*period,
              math.ceil(max(p['epoch'] for p in plans)/period)*period)
    expected=max(0,int((end-begin)/period))
    if not expected:
        return _summary(0,0,[],None,end)
    groups=[dict(unit=p['unit'],group=p.get('group') or p['unit']) for p in plans]
    bounds=(instant(now.timestamp()-86400).replace('Z','+00:00'),instant(now.timestamp()).replace('Z','+00:00'),
            instant(begin).replace('Z','+00:00'),instant(end).replace('Z','+00:00'))
    if len(plans)==1:
        # The owner generates one canonical due per unit/period. No slot/date
        # conversion or second group reduction is needed on this common path.
        direct=db.execute("SELECT count(*),count(DISTINCT due_at),"
            "min(json_extract(data_json,'$.listing_received_at')),max(json_extract(data_json,'$.listing_received_at')) "
            "FROM cycles INDEXED BY cycles_unit_time WHERE binding_id=? AND unit=? "
            "AND dispatch_at>=? AND dispatch_at<=? AND due_at>=? AND due_at<? "
            "AND status='CAPTURED' AND status!='SCHEDULE' "
            "AND json_extract(data_json,'$.listing_received_at') IS NOT NULL",
            (binding_id,plans[0]['unit'],*bounds)).fetchone()
        if direct[0]==direct[1] and direct[0]<=expected:
            return _compact_result((direct[0],direct[2],direct[3]),expected,begin,end)
        row=db.execute("WITH scalar AS MATERIALIZED (SELECT due_at,"
            "json_extract(data_json,'$.listing_received_at') received FROM cycles INDEXED BY cycles_unit_time "
            "WHERE binding_id=? AND unit=? AND dispatch_at>=? AND dispatch_at<=? AND due_at>=? AND due_at<? "
            "AND status='CAPTURED' AND status!='SCHEDULE'),proof AS ("
            "SELECT due_at,min(received) at FROM scalar WHERE received IS NOT NULL GROUP BY due_at) "
            "SELECT count(*),min(at),max(at) FROM proof",(binding_id,plans[0]['unit'],*bounds)).fetchone()
        if row[0]<=expected:
            return _compact_result(row,expected,begin,end)
    row=db.execute("WITH scalar AS MATERIALIZED (SELECT "
        "CAST(strftime('%s',c.due_at) AS INTEGER)/? slot,json_extract(p.value,'$.group') group_id,"
        "json_extract(c.data_json,'$.listing_received_at') received "
        "FROM json_each(?) p CROSS JOIN cycles c INDEXED BY cycles_unit_time "
        "ON c.binding_id=? AND c.unit=json_extract(p.value,'$.unit') "
        "WHERE c.dispatch_at>=? AND c.dispatch_at<=? AND c.due_at>=? AND c.due_at<? "
        "AND c.status='CAPTURED' AND c.status!='SCHEDULE'),"
        "group_proof AS (SELECT slot,group_id,min(received) at FROM scalar "
        "WHERE received IS NOT NULL GROUP BY slot,group_id),"
        "round_proof AS (SELECT slot,max(at) at FROM group_proof GROUP BY slot HAVING count(*)=?) "
        "SELECT count(*),min(at),max(at) FROM round_proof",
        (period,json.dumps(groups),binding_id,*bounds,len({p['group'] for p in groups}))).fetchone()
    return _compact_result(row,expected,begin,end)


def _compact_result(row,expected,begin,end):
    # Acquisition proof timestamps are owner-written UTC ISO values.
    count=row[0];gaps=max(0,count-1)
    return dict(expected=expected,succeeded=count,ratio=count/expected,
        refresh_seconds=(stamp(row[2])-stamp(row[1]))/gaps if gaps else None,
        from_at=instant(begin),until_at=instant(end),samples=gaps)


def recent_rounds_many(store, bindings, now, *, units=None, db=None):
    """Batch heads, then bounded indexed seeks per real unit (never per-message)."""
    if store is None:
        return {}
    by_binding = {}
    with nullcontext(db) if db is not None else store.connect() as db:
        rows = units if units is not None else db.execute("SELECT binding_id,unit,required,head_json,"
            "json_extract(state,'$.target_due_at') epoch,"
            "json_extract(state,'$.schedule_interval_seconds') period "
            "FROM units WHERE binding_id IN (SELECT value FROM json_each(?))",
            (json.dumps([b['binding_id'] for b in bindings]),)).fetchall()
        for row in rows:
            by_binding.setdefault(row['binding_id'], []).append(dict(row))
        result = {}
        for binding in bindings:
            bid = binding['binding_id']
            units = by_binding.get(bid, [])
            active = [u for u in units if u['required'] == 1]
            if active:
                units, until = active, now.timestamp()
            else:
                # A market-close retirement preserves the complete last plan.
                retired = [(u, json.loads(u['head_json']).get('disabled_at')) for u in units]
                latest = max((at for _,at in retired if at), default=None)
                units = [u for u,at in retired if at == latest] if latest else []
                until = stamp(latest) if latest else now.timestamp()
            plans, receipts = [], []
            source_period = binding['polling']['target_interval_seconds']
            count = math.ceil(86400/max(1,source_period))
            for unit in units:
                head = json.loads(unit['head_json'])
                plan = head.get('coverage_plan', {})
                epoch = stamp(plan.get('epoch') or unit['epoch'])
                period = plan.get('interval') or unit['period']
                if epoch is None or not period or (plan and plan['binding_version'] != binding['version']):
                    continue
                plans.append({'unit': unit['unit'], 'group': plan.get('group'),
                              'epoch': epoch, 'interval': period})
            if plans and all(p['interval']==source_period for p in plans):
                result[bid]=_compact_rounds(db,bid,plans,source_period,now,until)
                continue
            for plan in plans:
                start = now.timestamp() - 86400
                found = db.execute("SELECT due_at,json_extract(data_json,'$.listing_received_at') FROM cycles INDEXED BY cycles_unit_time "
                    "WHERE binding_id=? AND unit=? AND dispatch_at>=? AND dispatch_at<=? "
                    "AND status='CAPTURED' AND status!='SCHEDULE'",
                    (bid,plan['unit'],instant(start).replace('Z','+00:00'),
                     instant(now.timestamp()).replace('Z','+00:00'))).fetchall()
                receipts.extend({'unit':plan['unit'],'due':stamp(r[0]),'validated':True,
                                 'received':stamp(r[1])} for r in found)
            result[bid] = evaluate_rounds(plans,receipts,interval=source_period,until=until,limit=count,since=now.timestamp()-86400)
        return result


def recent_rounds(store, ticker, bindings, now):
    return recent_rounds_many(store, bindings, now)
