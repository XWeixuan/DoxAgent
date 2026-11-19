"""Bounded source-level rounds. One winning path per alternative group; AND across complements.

No writes or historical reconstruction: at most 21 logical slots per binding,
using the existing unit heads and indexed acquisition receipts.
"""
from contextlib import nullcontext
import json
import math
from datetime import UTC, datetime


def stamp(value):
    return datetime.fromisoformat(value).timestamp() if value else None


def instant(value):
    return datetime.fromtimestamp(value, UTC).isoformat().replace('+00:00', 'Z')


def evaluate_rounds(plans, receipts, *, interval, until, limit=20):
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
    gaps = [b-a for a,b in zip(fresh, fresh[1:]) if b >= a]
    success = sum(at is not None for _, at in selected)
    return {'expected': len(selected), 'succeeded': success,
            'ratio': success / len(selected) if selected else None,
            'refresh_seconds': sum(gaps) / len(gaps) if gaps else None,
            'from_at': instant(selected[0][0]) if selected else None,
            'until_at': instant(end) if selected else None, 'samples': len(gaps)}


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
            for unit in units:
                head = json.loads(unit['head_json'])
                plan = head.get('coverage_plan', {})
                epoch = stamp(plan.get('epoch') or unit['epoch'])
                period = plan.get('interval') or unit['period']
                if epoch is None or not period or (plan and plan['binding_version'] != binding['version']):
                    continue
                plans.append({'unit': unit['unit'], 'group': plan.get('group'),
                              'epoch': epoch, 'interval': period})
                start = math.floor(until / source_period) * source_period - 21 * source_period
                # Retained receipts are 48h. Do not invent failures outside retention.
                if start < now.timestamp() - 48 * 3600:
                    plans = []
                    break
                cap = 22 * (math.ceil(source_period / period) + 1)
                found = db.execute("SELECT due_at,json_extract(data_json,'$.list_validated'),"
                    "json_extract(data_json,'$.listing_received_at') FROM cycles INDEXED BY cycles_unit_time "
                    "WHERE binding_id=? AND unit=? AND dispatch_at>=? AND dispatch_at<=? "
                    "AND status!='SCHEDULE' ORDER BY dispatch_at DESC LIMIT ?",
                    (bid,unit['unit'],instant(start).replace('Z','+00:00'),
                     instant(now.timestamp()).replace('Z','+00:00'),cap)).fetchall()
                receipts.extend({'unit':unit['unit'],'due':stamp(r[0]),'validated':bool(r[1]),
                                 'received':stamp(r[2])} for r in found)
            result[bid] = evaluate_rounds(plans,receipts,interval=source_period,until=until)
        return result


def recent_rounds(store, ticker, bindings, now):
    return recent_rounds_many(store, bindings, now)
