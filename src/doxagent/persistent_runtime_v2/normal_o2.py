"""Normal-window O2 membership and one-time provisional boundary assignment."""
from datetime import date, timedelta
import json

from doxagent.semantic_clock import semantic_day


class SupplementalRetired(Exception):
    """Administrative retirement; never a model failure or fact settlement."""


def scheduled_sweep(task):
    return task['id'].startswith('sweep:') and not task['inputs'].get('supplemental')


def supplemental(task):
    return task['inputs'].get('scope') == 'SUPPLEMENTAL' or task['id'].startswith(
        ('supplement:', 'maintain:drain:', 'maintain:supplemental:'))


def retire_owned(journal, task):
    with journal.transaction(operation='o2.retire') as db:
        journal.fence(db, task)
        db.execute("UPDATE runtime_tasks SET status='FAILED',receipt=json_set(receipt,"
                   "'$.maintenance_retired',?,'$.administrative_reason',?),owner=NULL,lease_until=NULL "
                   "WHERE id=?", (journal.clock().isoformat(), 'SUPPLEMENTAL_O2_DISABLED', task['id']))


def freeze_candidates(repository, journal, task, base):
    """Freeze a scalar cutoff under the writer, then bounded immutable membership.

    Candidate identities/created_at never change. A per-ticker maintenance lease
    prevents another normal consumer from settling rows during this capture.
    """
    header = task['receipt'].get('o2_freeze')
    if header is None:
        with journal.transaction(operation='o2.freeze', expand=False) as db:
            row = journal.fence(db, task)
            raw = json.loads(row['receipt'])
            header = raw.get('o2_freeze')
            if header is None:
                at = journal.clock().isoformat()
                high = db.execute('SELECT coalesce(max(rowid),0) FROM runtime_v2_candidates').fetchone()[0]
                # The scheduled cutoff closes the preceding semantic day, even
                # when Sweep.inputs.day names the following calendar boundary.
                from datetime import datetime
                day = semantic_day(datetime.fromisoformat(task['inputs']['cutoff'])) - timedelta(days=1)
                header = dict(at=at, highwater=high, closing_day=str(day), revision=1,
                              base=base['artifacts']['event_library'])
                raw['o2_freeze'] = header
                db.execute('UPDATE runtime_tasks SET receipt=? WHERE id=?',
                           (json.dumps(raw, separators=(',', ':')), task['id']))
                db.execute("INSERT OR IGNORE INTO runtime_values VALUES('o2_closing_freeze',?,?)",
                           (task['ticker'] + ':' + str(day), json.dumps(dict(task_id=task['id'], at=at))))
        task['receipt']['o2_freeze'] = header
    selected = []
    after = ('', '')
    while True:
        with repository._connect() as db:
            rows = db.execute(
                "SELECT c.created_at,c.candidate_identity,c.payload_json FROM runtime_v2_candidates c "
                "WHERE c.ticker=? AND c.daily_status='PENDING' AND c.rowid<=? AND c.created_at<=? "
                "AND (c.created_at,c.candidate_identity)>(?,?) "
                "AND NOT EXISTS (SELECT 1 FROM runtime_v2_admission_exclusions x "
                "WHERE x.source_message_id=c.source_message_id) "
                "AND NOT EXISTS (SELECT 1 FROM runtime_values v WHERE v.namespace='invalid_admissions' "
                "AND v.key=c.source_message_id AND v.payload!='false') "
                "ORDER BY c.created_at,c.candidate_identity LIMIT 100",
                (task['ticker'], header['highwater'], header['at'], *after)).fetchall()
        if not rows:
            break
        selected.extend(json.loads(repository.content.text(row[2])) for row in rows)
        after = (rows[-1][0], rows[-1][1])
    return selected


def provisional_day(db, payload, prepared, inserted_at):
    original = date.fromisoformat(payload['trading_date'])
    if prepared['daily_status'] != 'PENDING' or not payload.get('case_id'):
        return str(original), None
    now = semantic_day(inserted_at)
    if now == original + timedelta(days=1):
        return str(now), 'semantic-boundary:' + str(original)
    if now == original:
        row = db.execute("SELECT payload FROM runtime_values WHERE namespace='o2_closing_freeze' AND key=?",
                         (payload['ticker'] + ':' + str(original),)).fetchone()
        if row:
            return str(original + timedelta(days=1)), json.loads(row[0])['task_id']
    return str(original), None
