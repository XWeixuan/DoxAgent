from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.source_maintenance.cli import main
from doxagent.source_maintenance.controller import Controller
from doxagent.source_maintenance.evidence import EvidenceStore, evidence_app, redact
from doxagent.source_maintenance.health import diagnose, recovered
from doxagent.source_maintenance.policy import SourcePolicy, validate_candidate
from doxagent.source_maintenance.repository import LeaseLost, MaintenanceRepository
from doxagent.source_maintenance.schema import (
    ActionKind,
    ActionReceipt,
    DeploymentManifest,
    HealthSample,
    Incident,
    RepairReport,
    RepairRound,
    Stage,
)
from doxagent.source_maintenance.settings import MaintenanceSettings
from doxagent.source_maintenance.workspace import Workspaces, git, safe_file

NOW = datetime(2026, 10, 5, 8, tzinfo=UTC)


def sample(i=0, **kwargs):
    values = dict(
        source_id="reuters",
        operation_id=f"op-{i}",
        binding_id="MU:reuters",
        status="FAILED",
        started_at=NOW - timedelta(minutes=20 - i),
        completed_at=NOW - timedelta(minutes=20 - i),
    )
    return HealthSample(**{**values, **kwargs})


def incident(**kwargs):
    return Incident(
        resource_key="SOURCE:reuters:ACQUISITION",
        failure_class="ACQUISITION",
        source_ids=["reuters"],
        affected_bindings=["MU:reuters"],
        first_observed_at=NOW - timedelta(minutes=20),
        last_observed_at=NOW,
        **kwargs,
    )


def report(files=None, **kwargs):
    return RepairReport(
        root_cause="fixture-backed parser failure",
        confidence=0.9,
        evidence_refs=["failure.json"],
        failure_scope="SOURCE",
        proposed_actions=[],
        changed_files=files or [],
        tests=["focused"],
        affected_sites=["reuters"],
        expected_recovery="list parsing",
        remaining_items=[],
        human_action=None,
        **kwargs,
    )


def test_disabled_creates_no_database_or_worker(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOXAGENT_SOURCE_MAINTENANCE_ENABLED", "false")
    root = tmp_path / "must-not-exist"
    main(["--root", str(root), "guardian", "--once"])
    assert not root.exists()
    assert "false" in capsys.readouterr().out
    from doxagent.source_maintenance.signals import poll

    poll(None, None, None, None, NOW)
    assert not root.exists()


def test_distinct_eligible_failures_over_fifteen_minutes():
    rows = [sample(0), sample(5), sample(16)]
    assert len(diagnose(rows, NOW)) == 1
    assert not diagnose([sample(0), sample(1), sample(2)], NOW)
    assert not diagnose([sample(0), sample(16, operation_id="op-0")], NOW)
    assert not diagnose([r.model_copy(update={"eligible": False}) for r in rows], NOW)
    assert not diagnose([r.model_copy(update={"enabled": False}) for r in rows], NOW)


def test_empty_real_success_resets_failures_not_deferral():
    rows = [sample(0), sample(5), sample(16)]
    assert diagnose(rows + [sample(18, status="DEFERRED")], NOW)
    assert not diagnose(rows + [sample(18, status="SUCCEEDED", valid_content=False)], NOW)


def test_search_partial_is_failure_episode_but_single_partial_not_incident():
    rows = [sample(i, status="PARTIAL", query_successes=1, query_failures=2) for i in [0, 5, 16]]
    assert len(diagnose(rows, NOW)) == 1
    assert not diagnose(rows[-1:], NOW)


def test_shared_dead_driver_merges_sources_and_tickers():
    rows = [
        sample(
            0,
            source_id=s,
            binding_id=t + ":" + s,
            stage="RUNTIME",
            runtime_kind="external_chrome",
            driver_epoch=4,
            driver_recovery_failed=True,
            operation_id="driver-4",
        )
        for s, t in [
            ("barrons", "MU"),
            ("investing", "BE"),
            ("digitimes", "INTC"),
            ("ihub", "RKLB"),
        ]
    ]
    found = diagnose(rows, NOW)
    assert len(found) == 1
    assert len(found[0].source_ids) == 4
    assert len(found[0].affected_bindings) == 4


def test_body_unique_articles_eighty_percent_and_paywall_human():
    rows = [sample(i + 10, stage="BODY", article_key=f"article-{i}") for i in range(3)]
    assert diagnose(rows, NOW)
    assert not diagnose([r.model_copy(update={"article_key": "one"}) for r in rows], NOW)
    assert not diagnose(
        rows + [sample(17, stage="BODY", article_key="fourth", status="SUCCEEDED")], NOW
    )
    wall = sample(
        19,
        stage="BODY",
        article_key="wall",
        reason="subscription_required",
        accessible_candidate=False,
    )
    assert diagnose([wall], NOW)[0].failure_class == "HUMAN"


def test_scheduler_requires_due_and_no_inflight():
    stale = sample(0, stage="SCHEDULER", due_at=NOW - timedelta(seconds=301))
    assert diagnose([stale], NOW)
    assert not diagnose([stale.model_copy(update={"in_flight": True})], NOW)
    assert not diagnose([stale.model_copy(update={"eligible": False})], NOW)


def test_real_observation_three_per_affected_binding_body_checked():
    item = incident()
    rows = [
        sample(i, status="SUCCEEDED", completed_at=NOW + timedelta(minutes=i)) for i in range(3)
    ]
    assert recovered(rows, item, since=NOW)
    assert not recovered(rows[:2], item, since=NOW)
    item.affected_bindings.append("BE:reuters")
    assert not recovered(rows, item, since=NOW)
    shared = [
        r.model_copy(update={"binding_id": None, "affected_bindings": ["MU:reuters", "BE:reuters"]})
        for r in rows
    ]
    assert recovered(shared, item, since=NOW)
    assert not recovered(
        shared + [sample(1, stage="BODY", completed_at=NOW, article_key="article")], item, since=NOW
    )


def test_lease_fences_late_controller_and_budget_not_reset(tmp_path):
    clock = [NOW]
    repo = MaintenanceRepository(tmp_path / "maintenance.sqlite3", clock=lambda: clock[0])
    original = repo.observe(incident())
    lease = repo.claim(original.incident_id, "one", seconds=5)
    assert repo.claim(original.incident_id, "two") is None
    clock[0] += timedelta(seconds=6)
    new = repo.claim(original.incident_id, "two")
    assert new.generation > lease.generation
    with pytest.raises(LeaseLost):
        repo.save(lease)
    new.round_count = 2
    repo.save(new)
    observed = repo.observe(incident())
    assert observed.round_count == 2
    assert observed.incident_id == original.incident_id


def test_action_dedup_and_cross_incident_resource_lock(tmp_path):
    repo = MaintenanceRepository(tmp_path / "maintenance.sqlite3")
    first = ActionReceipt(
        action_id="operation",
        incident_id="one",
        resource_key="SERVICE:access",
        kind=ActionKind.APPLY_SOURCE_PATCH,
        target="v2-site-access",
    )
    assert repo.prepare_action(first) == repo.prepare_action(first)
    with pytest.raises(RuntimeError):
        repo.prepare_action(first.model_copy(update={"action_id": "other", "incident_id": "two"}))
    first.status = "SUCCEEDED"
    repo.save_action(first)
    assert repo.prepare_action(first.model_copy(update={"action_id": "other"}))


def test_sample_retention_and_dedup(tmp_path):
    repo = MaintenanceRepository(tmp_path / "m.sqlite3", clock=lambda: NOW)
    row = sample()
    repo.record_sample(row)
    repo.record_sample(row)
    assert len(repo.samples()) == 1
    repo.record_sample(sample(0, operation_id="old", completed_at=NOW - timedelta(days=8)))
    assert len(repo.samples()) == 1


def test_evidence_redaction_and_bounded_capability(tmp_path):
    from fastapi.testclient import TestClient

    repo = MaintenanceRepository(tmp_path / "m.sqlite3")
    item = repo.observe(incident())
    store = EvidenceStore(tmp_path, repo)
    path = store.freeze(
        item,
        "log",
        {
            "token": "top-secret",
            "message": "Bearer ABCDEF",
            "url": "https://example.com/?token=do-not-show",
        },
    )
    assert "top-secret" not in path.read_text()
    assert "ABCDEF" not in path.read_text()
    assert "do-not-show" not in path.read_text()
    assert hashlib.sha256(path.read_bytes()).hexdigest() in path.name
    client = TestClient(evidence_app(store, "test"))
    base = "/v1/incidents/" + item.incident_id + "/"
    assert client.get(base + "get_poll_health").status_code == 401
    assert client.get(base + "shell", headers={"Authorization": "Bearer test"}).status_code == 404
    assert "[REDACTED]" in str(redact({"cookie": "sensitive"}))


def test_candidate_exact_overlay_and_symbol_boundary(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(["init"], repo)
    source = repo / "source.py"
    source.write_text("def reuters():\n    return 0\n\ndef other():\n    return 1\n")
    (repo / "test.py").write_text("def test_normal(): pass\ndef test_failure(): pass\n")
    commit = Workspaces.commit(repo, "initial")
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "source.py").write_text(source.read_text().replace("return 0", "return 2"))
    digest = hashlib.sha256((overlay / "source.py").read_bytes()).hexdigest()
    manifest = DeploymentManifest(
        source_commit=commit,
        images={},
        file_hashes={"source.py": digest},
        overlay_root=str(overlay),
    )
    manager = Workspaces(tmp_path / "maintenance", str(repo))
    work = manager.prepare(incident().incident_id, manifest)
    assert "return 2" in (work / "source.py").read_text()
    (work / "source.py").write_text(
        (work / "source.py").read_text().replace("return 2", "return 3")
    )
    policy = SourcePolicy(
        symbols={"source.py": ["reuters"]},
        tests=["test.py"],
        normal_fixture_test="test.py::test_normal",
        failure_fixture_test="test.py::test_failure",
    )
    assert validate_candidate(work, report(["source.py"]), [policy]) == ["source.py"]
    (work / "source.py").write_text(
        (work / "source.py").read_text().replace("return 1", "return 99")
    )
    with pytest.raises(ValueError):
        validate_candidate(work, report(["source.py"]), [policy])
    with pytest.raises(ValueError):
        safe_file(work, "../../outside")


def test_one_worker_daily_budget_and_wall_reservation(tmp_path):
    repo = MaintenanceRepository(tmp_path / "m.sqlite3", clock=lambda: NOW)
    row = RepairRound(
        incident_id="one",
        number=1,
        started_at=NOW,
        deadline=NOW + timedelta(seconds=1200),
        worktree="candidate",
        evidence_path="context",
    )
    repo.begin_round(row, daily_rounds=1, daily_seconds=1200)
    with pytest.raises(RuntimeError):
        repo.begin_round(
            row.model_copy(update={"round_id": "two"}), daily_rounds=3, daily_seconds=7200
        )
    row.status = "VERIFIED"
    repo.save_round(row)
    with pytest.raises(ValueError):
        repo.begin_round(
            row.model_copy(update={"round_id": "two"}), daily_rounds=1, daily_seconds=1200
        )


def test_shadow_no_side_effect_backend_and_human_path(tmp_path):
    repo = MaintenanceRepository(tmp_path / "m.sqlite3", clock=lambda: NOW)
    for i in [0, 5, 16]:
        repo.record_sample(sample(i))
    fake = SimpleNamespace(snapshot=lambda *_: (_ for _ in ()).throw(AssertionError("no API")))
    controller = Controller(
        MaintenanceSettings(enabled=True, root=tmp_path, mode="shadow"), repo, backend=fake
    )
    controller.tick()
    assert len(repo.incidents()) == 1
    assert repo.incidents()[0].stage == Stage.OBSERVED
    repo2 = MaintenanceRepository(tmp_path / "human.sqlite3", clock=lambda: NOW)
    repo2.record_sample(sample(reason="challenge_required"))
    c2 = Controller(
        MaintenanceSettings(enabled=True, root=tmp_path, mode="diagnose"), repo2, backend=fake
    )
    c2.tick()
    c2.tick()
    assert repo2.incidents()[0].stage == Stage.HUMAN_REQUIRED


def test_old_acquisition_failure_does_not_start_new_maintenance():
    rows = [sample(i) for i in [0, 5, 16]]
    assert not diagnose(rows, NOW + timedelta(hours=1))


def test_body_failure_ratio_not_reset_by_one_good_article():
    rows = [sample(i + 10, stage="BODY", article_key=f"article-{i}") for i in range(4)]
    rows.append(sample(19, stage="BODY", article_key="good", status="SUCCEEDED"))
    assert diagnose(rows, NOW)[0].failure_class == "BODY"


def test_repeated_external_crashes_across_epochs_are_one_latest_incident():
    rows = [
        sample(
            10 + i,
            stage="RUNTIME",
            runtime_kind="external_chrome",
            driver_epoch=i,
            operation_id=f"epoch-{i}",
        )
        for i in range(3)
    ]
    found = diagnose(rows, NOW)
    assert len(found) == 1
    assert found[0].resource_key.endswith(":2")


def test_historical_driver_failure_does_not_reopen_stable_incident():
    failed = sample(
        stage="RUNTIME",
        runtime_kind="external_chrome",
        driver_epoch=1,
        driver_recovery_failed=True,
        completed_at=NOW - timedelta(days=1),
    )
    assert not diagnose([failed], NOW)


def test_evidence_repository_is_truly_readonly(tmp_path):
    path = tmp_path / "readonly.sqlite3"
    writer = MaintenanceRepository(path, clock=lambda: NOW)
    item = writer.observe(incident())
    reader = MaintenanceRepository(path, clock=lambda: NOW, readonly=True)
    assert reader.get(item.incident_id) == item
    with pytest.raises(RuntimeError, match="read-only"):
        reader.record_sample(sample())
    import sqlite3

    with reader.connection() as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM maintenance_incidents")


def test_closed_evidence_archived_after_thirty_days_not_active_thread(tmp_path):
    import zipfile

    clock = [NOW]
    repo = MaintenanceRepository(tmp_path / "m.sqlite3", clock=lambda: clock[0])
    item = repo.observe(incident(stage=Stage.STABLE))
    store = EvidenceStore(tmp_path, repo)
    path = store.freeze(item, "example", {"fixture": "original"})
    original = path.read_bytes()
    thread = tmp_path / "codex-home" / "active-session.json"
    thread.parent.mkdir()
    thread.write_text("active")
    store.archive_closed()
    assert path.exists()
    clock[0] += timedelta(days=31)
    store.archive_closed()
    assert not path.exists() and thread.exists()
    with zipfile.ZipFile(tmp_path / "archives" / (item.incident_id + ".zip")) as archive:
        assert archive.read(path.name) == original
