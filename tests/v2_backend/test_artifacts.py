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
