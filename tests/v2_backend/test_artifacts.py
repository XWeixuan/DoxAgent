from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.v2_read.artifacts import ArtifactIndexer, PublishedArtifacts
from doxagent.v2_read.repository import ReadStore
from tests.test_codex_document3_workflow import _seed_published_d2


def test_published_partial_d2_is_indexed_by_shell_and_unit(tmp_path):
    path = tmp_path / "research.db"
    repository = SQLiteCodexRuntimeRepository(path)
    _seed_published_d2(repository, partial=True)
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    indexer = ArtifactIndexer(store, PublishedArtifacts(path))
    records = indexer.index("d2-mu", "MU", lineage="initialization:fixture")
    assert any(row["kind"] == "expectations_index" for row in records)
    store.ingest("publication", "d2-mu", records)
    with store.connect() as db:
        seq = store.highwater(db)
    shells = store.page("shell", "MU", seq, parent="d2-mu")
    assert shells
    assert shells[0]["data"]["shell_id"]
    assert store.get("expectations_index", "MU", "d2-mu")["publication_state"] == "PARTIAL"


def test_document2_download_returns_exact_selected_publication(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from tests.v2_backend.test_api import OfflineAuth
    path = tmp_path / 'research.db'
    _seed_published_d2(SQLiteCodexRuntimeRepository(path), partial=True)
    store = ReadStore(tmp_path / 'read.db')
    store.migrate()
    source = PublishedArtifacts(path)
    records = ArtifactIndexer(store, source).index('d2-mu','MU',lineage='fixture')
    store.ingest('publication','d2-mu',records)
    reference = store.get('document_ref','MU','d2-mu')
    _, original = source.body('d2-mu',reference['artifact_id'])
    monkeypatch.setenv('DOXAGENT_CODEX_RUNTIME_SQLITE_PATH',str(path))
    control = ControlRepository(RuntimeJournal(tmp_path / 'runtime.db'))
    control.migrate()
    with TestClient(create_app(store=store,control=control,auth=OfflineAuth())) as client:
        response = client.get(PREFIX+'/tickers/MU/expectations/runs/d2-mu/download',headers={'Authorization':'Bearer offline'})
        assert response.status_code == 200,response.text
        assert response.content == original.encode('utf-8')
        assert response.headers['content-type'].startswith('application/json')
        assert 'document2.json' in response.headers['content-disposition']
        assert response.headers['etag'] == '"'+reference['content_sha256']+'"'
        assert client.get(PREFIX+'/tickers/BE/expectations/runs/d2-mu/download',headers={'Authorization':'Bearer offline'}).status_code == 404
