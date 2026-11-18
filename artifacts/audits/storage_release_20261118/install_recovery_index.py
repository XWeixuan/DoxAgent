"""One-time small partial index; no history conversion or business row change."""
import json,pathlib,sqlite3,time
path=pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3')
started=time.monotonic()
with sqlite3.connect(path,timeout=10) as db:
    db.execute('CREATE INDEX IF NOT EXISTS cycles_unfinished ON cycles(status,run_id) WHERE finished_at IS NULL')
    plan=db.execute("EXPLAIN QUERY PLAN SELECT run_id FROM cycles WHERE status='CAPTURED' AND finished_at IS NULL AND json_extract(data_json,'$.attempt_finished')=0").fetchall()
    assert any('cycles_unfinished' in row[3] for row in plan),plan
    tick=time.monotonic()
    pending=db.execute("SELECT count(*) FROM cycles WHERE status='CAPTURED' AND finished_at IS NULL AND json_extract(data_json,'$.attempt_finished')=0").fetchone()[0]
    print(json.dumps({'index':'cycles_unfinished','seconds':time.monotonic()-started,'query_seconds':time.monotonic()-tick,'interrupted_cycles':pending,'plan':plan,'row_changes':db.total_changes}))
