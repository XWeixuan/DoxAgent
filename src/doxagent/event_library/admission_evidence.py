"""Small first-knowledge evidence, independent of recoverable history branches."""
from contextlib import closing
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sqlite3


def content_key(payload):
    value = dict(payload)
    for field in ('proposition', 'subject_time'):
        if isinstance(value.get(field), str):
            value[field] = ' '.join(value[field].split())
    semantic = [value.get(k) for k in ('proposition', 'assertion_state', 'subject_time',
                                      'fact_occurred_at', 'fact_occurrence_time_precision')]
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS fact_admission_evidence('
               'ticker TEXT,fact_no INTEGER,content_key TEXT,first_version INTEGER,first_at TEXT,'
               'PRIMARY KEY(ticker,fact_no,content_key))')
    db.execute('CREATE TABLE IF NOT EXISTS event_publication_clocks('
               'ticker TEXT,version INTEGER,published_at TEXT,PRIMARY KEY(ticker,version))')
    db.execute('CREATE TABLE IF NOT EXISTS event_storage_progress(key TEXT PRIMARY KEY,value TEXT NOT NULL)')


def record(db, ticker, number, payload, version, at):
    db.execute('INSERT INTO fact_admission_evidence VALUES(?,?,?,?,?) '
               'ON CONFLICT(ticker,fact_no,content_key) DO UPDATE SET first_version=excluded.first_version,'
               'first_at=excluded.first_at WHERE excluded.first_version<first_version',
               (ticker,number,content_key(payload),version,at))


def ready(db, ticker):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='event_storage_progress'").fetchone()
                and db.execute('SELECT 1 FROM event_storage_progress WHERE key=?', ('admissions-ready:'+ticker,)).fetchone())


def inherit(db, ticker, version):
    """Carry completed first-knowledge evidence across an attached branch copy."""
    if not db.execute("SELECT 1 FROM origin.sqlite_master WHERE name='event_storage_progress'").fetchone():
        return False
    key = 'admissions-ready:' + ticker
    if not db.execute('SELECT 1 FROM origin.event_storage_progress WHERE key=?', (key,)).fetchone():
        return False
    db.execute('INSERT INTO fact_admission_evidence SELECT * FROM origin.fact_admission_evidence '
               'WHERE ticker=? AND first_version<=?', (ticker, version))
    db.execute('INSERT INTO event_publication_clocks SELECT * FROM origin.event_publication_clocks '
               'WHERE ticker=? AND version<=?', (ticker, version))
    db.execute('INSERT OR REPLACE INTO event_storage_progress VALUES(?,?)', (key, '1'))
    return True


def facts(db, ticker, version, numbers=None):
    clause = '' if numbers is None else ' AND r.fact_no IN (SELECT value FROM json_each(?))'
    args = (ticker,version) if numbers is None else (ticker,version,json.dumps(numbers))
    rows = db.execute('SELECT r.fact_no,r.payload_json FROM canonical_fact_revisions r '
        'WHERE r.ticker=? AND r.library_version<=?'+clause+
        ' AND NOT EXISTS(SELECT 1 FROM canonical_fact_revisions n WHERE n.ticker=r.ticker '
        'AND n.fact_no=r.fact_no AND n.library_version>r.library_version AND n.library_version<=?)', (*args,version))
    result = {}
    for row in rows:
        evidence = db.execute('SELECT first_version,first_at FROM fact_admission_evidence '
            'WHERE ticker=? AND fact_no=? AND content_key=?',
            (ticker,row['fact_no'],content_key(json.loads(row['payload_json'])))).fetchone()
        if evidence is None:
            raise ValueError('FACT_ADMISSION_EVIDENCE_MISSING')
        result['F'+str(row['fact_no'])] = (evidence[0], datetime.fromisoformat(evidence[1].replace('Z','+00:00')) if evidence[1] else None)
    return result


def backfill(repository, ticker, *, limit=64):
    """Explicit migration: at most one indexed page; no writer during source IO."""
    key='admissions-backfill:'+ticker
    with repository._read() as db:
        if ready(db,ticker):return True
        row=db.execute('SELECT value FROM event_storage_progress WHERE key=?',(key,)).fetchone()
    if row:
        state=json.loads(row[0])
    else:
        paths=[];path=repository.path.resolve();ceiling=repository.published_version(ticker);seen=set()
        while True:
            if str(path) in seen:raise ValueError('Cyclic Event lineage')
            seen.add(str(path));side=Path(str(path)+'.branch.json')
            branch=json.loads(side.read_text(encoding='utf-8')) if side.is_file() else None
            base=int(branch['base_version']) if branch else -1
            paths.append([str(path),base,ceiling])
            if not branch:break
            path=Path(branch['source']).resolve();ceiling=min(ceiling,base)
        state={'paths':paths,'index':0,'phase':'clocks','cursor':0}
    # Cold no-change branches contribute no publication in this interval. Their
    # sidecars already supplied the lineage; match the ordinary history reader
    # without reopening a database which was legitimately retired. Also handle
    # checkpoints created by older versions of this migration.
    while True:
        path,base,ceiling=state['paths'][state['index']]
        if base < ceiling or state['index']+1 == len(state['paths']):
            break
        state.update(index=state['index']+1, phase='clocks', cursor=0)
    with closing(sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True)) as source:
        source.row_factory=sqlite3.Row
        if state['phase']=='clocks':
            rows=source.execute("SELECT version,coalesce(published_at,created_at) AS at FROM library_versions "
                "WHERE ticker=? AND version>? AND version<=? AND status='PUBLISHED' ORDER BY version LIMIT ?",
                (ticker,state['cursor'],ceiling,limit)).fetchall()
            records=[tuple(r) for r in rows]
        else:
            rows=source.execute('SELECT rowid,fact_no,library_version,payload_json FROM canonical_fact_revisions '
                'WHERE ticker=? AND rowid>? AND library_version>? AND library_version<=? ORDER BY rowid LIMIT ?',
                (ticker,state['cursor'],base,ceiling,limit)).fetchall()
            records=[(r['fact_no'],content_key(json.loads(r['payload_json'])),r['library_version']) for r in rows]
    complete=False
    with closing(repository._connect()) as db,db:
        db.execute('PRAGMA busy_timeout=10')
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT value FROM event_storage_progress WHERE key=?',(key,)).fetchone()
        if (current[0] if current else None)!=(row[0] if row else None):return False
        if state['phase']=='clocks':
            db.executemany('INSERT OR IGNORE INTO event_publication_clocks VALUES(?,?,?)',[(ticker,*r) for r in records])
        else:
            for number,signature,version in records:
                at=db.execute('SELECT published_at FROM event_publication_clocks WHERE ticker=? AND version=?',(ticker,version)).fetchone()
                db.execute('INSERT INTO fact_admission_evidence VALUES(?,?,?,?,?) '
                    'ON CONFLICT(ticker,fact_no,content_key) DO UPDATE SET first_version=excluded.first_version,'
                    'first_at=excluded.first_at WHERE excluded.first_version<first_version',
                    (ticker,number,signature,version,at[0] if at else None))
        if len(rows)==limit:
            state['cursor']=rows[-1][0]
        elif state['phase']=='clocks':
            state.update(phase='facts',cursor=0)
        elif state['index']+1<len(state['paths']):
            state.update(index=state['index']+1,phase='clocks',cursor=0)
        else:
            complete=True
            db.execute('INSERT OR REPLACE INTO event_storage_progress VALUES(?,?)',('admissions-ready:'+ticker,'1'))
        if complete:db.execute('DELETE FROM event_storage_progress WHERE key=?',(key,))
        else:db.execute('INSERT OR REPLACE INTO event_storage_progress VALUES(?,?)',(key,json.dumps(state)))
    return complete
