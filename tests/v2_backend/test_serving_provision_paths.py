import json
from types import SimpleNamespace
from doxagent.production_v2 import paths
from doxagent.v2_read.repository import ReadStore,migrate_case_evidence_indexes


def test_provisioning_resolves_read_alias_and_does_not_index_a_view(tmp_path,monkeypatch):
    target=tmp_path/'generation'/'read.sqlite3';store=ReadStore(target);store.migrate()
    alias=tmp_path/'active.json';alias.write_text(json.dumps(dict(format='doxagent.v2.read-alias.1',path=str(target))))
    monkeypatch.setenv('DOXAGENT_V2_READ_SQLITE_PATH',str(alias))
    settings=SimpleNamespace(**{k:str(tmp_path/(k+'.sqlite3')) for k in ('codex_runtime_sqlite_path','ticker_initialization_control_path','message_bus_v2_sqlite_path','persistent_runtime_v2_sqlite_path','runtime_scheduler_sqlite_path','model_usage_sqlite_path','codex_monitoring_o4_sqlite_path')})
    assert paths(settings)['read']==target.resolve()
    with store.connect(write=True) as db:
        migrate_case_evidence_indexes(db)
        assert db.execute("SELECT type FROM sqlite_master WHERE name='objects'").fetchone()[0]=='view'
