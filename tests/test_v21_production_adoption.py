from datetime import UTC, datetime

import pytest

from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.ticker_initialization.schema import InitializationError


@pytest.mark.parametrize("operation", ["ACTIVATE", "REPLACE_ARTIFACT", "ROLLBACK"])
def test_manual_commit_cannot_overwrite_concurrent_maintenance(tmp_path, operation):
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="commit", block="ACTIVATION")],
        operation_kind=operation,
    )
    lease = repo.claim("test")
    identity = run.initialization_id + "-activation"
    repo.stage_revision(lease, identity, {"event_library": {"version": 1}})
    repo.begin(lease, "commit", {})
    with repo._write() as db:
        db.execute("INSERT INTO ticker_active_revision VALUES('MU','concurrent-maintenance')")
    with pytest.raises(InitializationError, match="active revision changed"):
        repo.activate(lease, identity)
    with repo._write() as db:
        db.execute("DELETE FROM ticker_active_revision WHERE ticker='MU'")
    repo.activate(lease, identity)
    repo.activate(lease, identity)
    assert repo.active_revision("MU")["revision_id"] == identity


def test_targeted_backfill_does_not_duplicate_historical_cases(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_read.outbox import SourceOutbox

    journal = RuntimeJournal(tmp_path / "runtime.db")
    journal.set("worker_invocations", "old", {"case_id": "historical"})
    journal.set("policy_watch", "new", {"policy_id": "p"})
    source = SourceOutbox(journal.path, "runtime")
    source.migrate()
    assert source.backfill("runtime_values", namespaces=("policy_watch",)) == 1
    assert source.backfill("runtime_values", namespaces=("policy_watch",)) == 0
    with source.connection() as db:
        rows = db.execute("SELECT payload FROM v2_source_receipts").fetchall()
        assert len(rows) == 1
        assert '"policy_watch"' in rows[0][0]


def test_incremental_upgrade_verifies_backup_and_keeps_old_checkpoint(tmp_path):
    import gzip
    import json
    import sqlite3

    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.production_v21 import file_digest, upgrade_v21
    from doxagent.v2_read.outbox import SourceOutbox

    paths = {
        name: tmp_path / (name + ".db")
        for name in ("research", "initialization", "bus", "runtime", "read", "scheduler")
    }
    for path in paths.values():
        sqlite3.connect(path).close()
    journal = RuntimeJournal(paths["runtime"])
    journal.set("worker_invocations", "old", {"historical": True})
    journal.set("policy_direction_usage", "long", {"policy_id": "p"})
    journal.set("policy_watch", "watch", {"policy_id": "p"})
    source = SourceOutbox(paths["runtime"], "runtime")
    source.migrate()
    with source.connection(write=True) as db:
        db.execute("INSERT INTO v2_backfill_checkpoint VALUES('runtime_values',99)")
    manifest = {"databases": {}}
    for name in ("research", "initialization", "bus", "runtime", "read"):
        with sqlite3.connect(paths[name]) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        archive = tmp_path / (name + ".gz")
        archive.write_bytes(gzip.compress(paths[name].read_bytes()))
        size, sha = file_digest(paths[name])
        manifest["databases"][name] = dict(
            source=str(paths[name]), archive=str(archive), size=size, sha256=sha
        )
    file = tmp_path / "backup-manifest.json"
    file.write_text(json.dumps(manifest))
    receipt = upgrade_v21(paths, file)
    assert receipt["runtime_values_backfilled"] == 2
    with source.connection() as db:
        assert (
            db.execute(
                "SELECT position FROM v2_backfill_checkpoint WHERE table_name='runtime_values'"
            ).fetchone()[0]
            == 99
        )
        assert (
            db.execute(
                "SELECT count(*) FROM v2_source_outbox WHERE table_name='runtime_values'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.asyncio
async def test_preserved_configuration_does_not_prepare_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from doxagent.ticker_initialization import activation_adapter as module
    from doxagent.workflows.codex_document3.repository import _project
    from tests.fixtures.codex_document3 import _policy_set

    canonical = _policy_set()
    refs = dict(
        document1={},
        document2={"run_id": canonical.document2_ref.run_id},
        document3={"version": 1},
        event_library={"version": 1},
        monitoring_configuration={"initialization_id": "existing"},
    )
    context = SimpleNamespace(
        run=SimpleNamespace(
            initialization_id="adoption",
            ticker="MU",
            operation_kind="ACTIVATE",
            base_revision="base",
        ),
        node=SimpleNamespace(
            key="activation.prepare",
            dependencies=[],
            inputs={"artifacts": refs, "preserve_monitoring_configuration": True},
        ),
        repository=SimpleNamespace(
            active_revision=lambda _: {"revision_id": "base", "artifacts": refs},
            stage_revision=lambda lease, identity, selected: selected,
        ),
        lease=None,
    )
    monkeypatch.setattr(
        module,
        "SQLiteDocument3PolicyRepository",
        lambda _: SimpleNamespace(
            get_projection=lambda *args: _project(canonical), get_version=lambda *args: canonical
        ),
    )
    monkeypatch.setattr(
        module,
        "PublishedEventLibraryReader",
        lambda *args, **kwargs: SimpleNamespace(known_index=lambda *args, **kwargs: object()),
    )
    adapter = module.ActivationAdapter(
        SimpleNamespace(
            codex_runtime_sqlite_path=tmp_path / "research.db", event_library_root=tmp_path
        )
    )
    monkeypatch.setattr(adapter, "_documents_available", lambda *args: None)

    async def ready(*args, **kwargs):
        pass

    monkeypatch.setattr(adapter, "_wait_w3", ready)
    result = await adapter.execute(context)
    assert (
        result.artifacts["activation_revision"]["monitoring_configuration"]
        == refs["monitoring_configuration"]
    )
    context.node.inputs["artifacts"] = {
        **refs,
        "monitoring_configuration": {"initialization_id": "other"},
    }
    with pytest.raises(ValueError, match="must match current base"):
        await adapter.execute(context)


@pytest.mark.asyncio
async def test_adoption_rebases_version_and_preserves_policy_business_content(tmp_path):
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.research_adoption import adopt
    from doxagent.workflows.codex_document3.repository import SQLiteDocument3PolicyRepository
    from doxagent.workflows.codex_document3.state_v21 import StateV21
    from tests.fixtures.codex_document3 import _policy_set
    from tests.test_codex_document3_v21_orchestration import NOW, rig

    fixture = rig.__wrapped__(tmp_path / "source")
    prepare = fixture.orchestrator.preparer.prepare

    async def with_published_d2(**kwargs):
        prepared = await prepare(**kwargs)
        prepared["document2_ref"] = _policy_set().document2_ref.model_dump(mode="json")
        return prepared

    fixture.orchestrator.preparer.prepare = with_published_d2
    source_run = "adoption-source"
    result = await fixture.orchestrator.initialize(ticker="MU", as_of=NOW, run_id=source_run)
    source_policy = fixture.state.get_staged_v3("MU", result.policy_set_version)
    source_receipt = fixture.state.get_staged_by_run(source_run)[1]
    SQLiteCodexRuntimeRepository(fixture.state.path)
    target = tmp_path / "production" / "research.db"
    target.parent.mkdir()
    policies = SQLiteDocument3PolicyRepository(target)
    StateV21(target).reserve("existing-reservation", "MU", floor=49)
    policies.publish(
        _policy_set().model_copy(update={"policy_set_version": 50}), expected_base_version=None
    )
    spec = dict(
        ticker="MU",
        source_database=fixture.state.path,
        source_workspace=str(fixture.workspace.local.root),
        d1_run="source-d1",
        d2_run=source_policy.document2_ref.run_id,
        d3_run=source_run,
        adoption_run="adoption-target",
        source_sha256=source_receipt["hashes"]["document3.json"],
    )
    first = await adopt(spec, database=target, workspace_root=tmp_path / "formal-workspaces")
    second = await adopt(spec, database=target, workspace_root=tmp_path / "formal-workspaces")
    assert first == second
    assert first["version"] == 51
    assert policies.get_current_version("MU") == 50
    migrated = StateV21(target).get_staged_v3("MU", 51)
    assert migrated.policies == source_policy.policies
    assert migrated.document2_ref == source_policy.document2_ref
    assert migrated.event_library_ref == source_policy.event_library_ref


def test_upstream_materialization_uses_accepted_attempts_not_stale_d2_paths(tmp_path):
    import hashlib
    from types import SimpleNamespace as NS

    from doxagent.research_adoption import materialize_upstream_workspaces

    source = tmp_path / "source"
    target = tmp_path / "formal"

    def ref(run, identity, path, content, **extra):
        file = source / run / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return NS(
            artifact_id=identity,
            relative_path=path,
            node="research",
            attempt_id=extra.pop("attempt_id", "accepted"),
            kind="structured_completion",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            published=False,
            **extra,
        )

    accepted = ref("d1", "accepted", "artifacts/c4.json", b'{"current":true}')
    stale = NS(
        **{**vars(accepted), "artifact_id": "stale", "attempt_id": "obsolete", "sha256": "0" * 64}
    )
    final = ref("d2", "final", "artifacts/document2.json", b'{"final":true}')
    obsolete_d2 = NS(**{**vars(final), "artifact_id": "old", "sha256": "0" * 64})
    repo = NS(
        get_bundle=lambda run: (
            NS(reports={"C4": accepted}, handoff=None)
            if run == "d1"
            else NS(handoff=NS(document2_artifact_id="final"))
        ),
        list_artifacts=lambda run, **_: [stale, accepted] if run == "d1" else [obsolete_d2, final],
        get_published_document=lambda *args: None,
    )
    copied = materialize_upstream_workspaces(
        {"d1_run": "d1", "d2_run": "d2", "source_workspace": str(source)}, repo, target
    )
    assert len(copied) == 2
    assert (target / "d1/artifacts/c4.json").read_bytes() == b'{"current":true}'
    assert (target / "d2/artifacts/document2.json").read_bytes() == b'{"final":true}'
