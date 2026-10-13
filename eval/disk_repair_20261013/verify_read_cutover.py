"""Business readback through the real API image, at the preserved public sequence."""
import json
import sqlite3
import urllib.request
from contextlib import closing
from pathlib import Path

from doxagent.v2_read.repository import ReadStore

root=Path('/data/read')
old=ReadStore(root/'v2.disk-repair-20261013.precompact.sqlite3')
new=ReadStore(root/'v2.sqlite3')
with closing(sqlite3.connect(old.path.as_uri()+'?mode=ro',uri=True)) as db:
    seq=db.execute('SELECT max(seq) FROM commits').fetchone()[0]
    kinds=db.execute('SELECT DISTINCT kind FROM object_current').fetchall()
    checked={}
    for (kind,) in kinds:
        if kind=='capture_coverage':
            continue  # Live capture proofs advance independently; builder hashed the frozen originals.
        rows=db.execute('SELECT ticker,id FROM object_current WHERE kind=? LIMIT 3',(kind,)).fetchall()
        for ticker,identity in rows:
            assert old.get(kind,ticker,identity,seq)==new.get(kind,ticker,identity,seq), (kind,ticker,identity)
        checked[kind]=len(rows)
    gaps=db.execute('SELECT source,event,reason FROM gaps ORDER BY source,event').fetchall()
    fact_lookup=db.execute("SELECT count(*),count(json_extract(payload,'$.fact.fact_id')) FROM objects WHERE kind='fact' AND valid_from<=?",(seq,)).fetchone()
with closing(sqlite3.connect(new.path.as_uri()+'?mode=ro',uri=True)) as db:
    assert gaps==db.execute('SELECT source,event,reason FROM gaps ORDER BY source,event').fetchall()
    head=db.execute('SELECT max(seq) FROM commits').fetchone()[0]
    assert head>=seq
    assert fact_lookup==db.execute("SELECT count(*),count(json_extract(payload,'$.fact.fact_id')) FROM objects WHERE kind='fact' AND valid_from<=?",(seq,)).fetchone(), 'fact SQL lookup changed'
health={}
for route in ('healthz','readyz'):
    with urllib.request.urlopen('http://127.0.0.1:8000/'+route,timeout=15) as response:
        health[route]=response.status
print(json.dumps(dict(verified=True,preserved_seq=seq,current_seq=head,
                     kinds_checked=checked,objects_checked=sum(checked.values()),
                     fact_lookup=fact_lookup,gaps=gaps,health=health)),flush=True)
