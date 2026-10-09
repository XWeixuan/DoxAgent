from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doxagent.source_maintenance.controller import Controller
from doxagent.source_maintenance.evidence import write_json
from doxagent.source_maintenance.policy import SourcePolicy
from doxagent.source_maintenance.repository import MaintenanceRepository
from doxagent.source_maintenance.schema import (
    ActionKind,
    ActionReceipt,
    DeploymentManifest,
    HealthSample,
    Incident,
    ProposedAction,
    RepairReport,
    RepairRound,
    Stage,
)
from doxagent.source_maintenance.settings import MaintenanceSettings
from doxagent.source_maintenance.workspace import Workspaces, git


@pytest.mark.parametrize("baseline_drift", [False, True])
def test_candidate_feedback_apply_observe_and_manifest_resume(tmp_path, baseline_drift):
    now = datetime(2026, 10, 5, 8, tzinfo=UTC)
    root = tmp_path / "maintenance"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    git(["init"], source)
    (source / "source.py").write_text("def parser():\n    return 0\n")
    (source / "test_source.py").write_text("def test_normal(): pass\ndef test_failure(): pass\n")
    commit = Workspaces.commit(source, "baseline")
    manifest_path = root / "manifest.json"
    write_json(
        manifest_path,
        DeploymentManifest(
            source_commit=commit, images={"v2-message-bus": "sha256:baseline"}, file_hashes={}
        ).model_dump(mode="json"),
    )
    repository = MaintenanceRepository(root / "maintenance.sqlite3", clock=lambda: now)
    item = repository.observe(
        Incident(
            resource_key="SOURCE:test:ACQUISITION",
            failure_class="ACQUISITION",
            source_ids=["test"],
            affected_bindings=["MU:test", "BE:test"],
            first_observed_at=now,
            last_observed_at=now,
            stage=Stage.CODING,
        )
    )
    policy = SourcePolicy(
        symbols={"source.py": ["parser"]},
        tests=["test_source.py"],
        normal_fixture_test="test_source.py::test_normal",
        failure_fixture_test="test_source.py::test_failure",
        services={"v2-message-bus": ["source.py"]},
    )

    class FakeDocker:
        def __init__(self):
            self.round_ids = []
            self.validations = 0

        def agent(self, round_):
            self.round_ids.append(round_.round_id)
            Path(round_.worktree, "source.py").write_text("def parser():\n    return 1\n")
            report = RepairReport(
                root_cause="parser",
                confidence=0.9,
                evidence_refs=["context"],
                failure_scope="SOURCE",
                proposed_actions=[
                    ProposedAction(kind=ActionKind.APPLY_SOURCE_PATCH, target="v2-message-bus")
                ],
                changed_files=["source.py"],
                tests=["test_source.py"],
                affected_sites=["test"],
                expected_recovery="real parsing",
                remaining_items=[],
                human_action=None,
            )
            return {
                "report": report.model_dump(mode="json"),
                "thread_id": "same-thread",
                "turn_id": "turn",
            }

        def verify(self, path, tests):
            self.validations += 1
            assert any("frozen-tests" in p for p in tests)
            return {"passed": self.validations > 1, "log": "independent feedback"}

    class FakeBackend:
        def __init__(self):
            self.applied = 0

        def execute(self, item, action, **kwargs):
            self.applied += 1
            receipt = repository.prepare_action(
                ActionReceipt(
                    action_id=kwargs["key"],
                    incident_id=item.incident_id,
                    resource_key="SERVICE:bus",
                    kind=action.kind,
                    target=action.target,
                )
            )
            receipt.status = "SUCCEEDED"
            repository.save_action(receipt)
            return receipt

        def canary(self, item):
            return {"passed": True, "publisher_body": True}

    docker = FakeDocker()
    backend = FakeBackend()
    controller = Controller(
        MaintenanceSettings(
            enabled=True,
            mode="apply",
            root=root,
            source_repository=str(source),
            manifest_path=manifest_path,
        ),
        repository,
        docker=docker,
        backend=backend,
        policies={"test": policy},
    )
    for _ in range(4):
        controller.tick()
    if baseline_drift:
        write_json(
            root / "deployment-head.json",
            DeploymentManifest(
                source_commit="b" * 40,
                images={"v2-message-bus": "sha256:new-human"},
                file_hashes={},
            ).model_dump(mode="json"),
        )
        controller.tick()
        assert backend.applied == 0
        assert repository.get(item.incident_id).stage == Stage.REVIEW_REQUIRED
        assert "rebase" in repository.get(item.incident_id).human_action
        return
    controller.tick()
    current = repository.get(item.incident_id)
    assert current.stage == Stage.OBSERVE
    assert backend.applied == 1
    assert len(set(docker.round_ids)) == 1
    assert repository.rounds(item.incident_id)[0].thread_id == "same-thread"
    for ticker in ["MU", "BE"]:
        for number in range(3):
            repository.record_sample(
                HealthSample(
                    source_id="test",
                    binding_id=ticker + ":test",
                    operation_id=ticker + str(number),
                    started_at=now,
                    completed_at=now + timedelta(seconds=number),
                    status="SUCCEEDED",
                    valid_content=False,
                )
            )
    controller.tick()
    assert repository.get(item.incident_id).stage == Stage.RECOVERED


def test_orphan_round_adopted_without_second_worker(tmp_path):
    now = datetime(2026, 10, 5, 8, tzinfo=UTC)
    repository = MaintenanceRepository(tmp_path / "maintenance.sqlite3", clock=lambda: now)
    item = repository.observe(
        Incident(
            resource_key="SOURCE:sample:ACQUISITION",
            failure_class="ACQUISITION",
            source_ids=["sample"],
            first_observed_at=now,
            last_observed_at=now,
            stage=Stage.CODING,
        )
    )
    round_ = RepairRound(
        incident_id=item.incident_id,
        number=1,
        started_at=now,
        deadline=now + timedelta(minutes=20),
        worktree=str(tmp_path / "candidate"),
        evidence_path="context",
    )
    repository.begin_round(round_, daily_rounds=6, daily_seconds=7200)

    class FakeDocker:
        def agent(self, r):
            assert r.round_id == round_.round_id
            return None

    controller = Controller(
        MaintenanceSettings(enabled=True, mode="diagnose", root=tmp_path),
        repository,
        docker=FakeDocker(),
        backend=object(),
    )
    controller.tick()
    assert len(repository.rounds()) == 1
    assert repository.get(item.incident_id).round_count == 1


def test_cancel_stops_only_maintenance_worker_and_releases_round_slot(tmp_path):
    now = datetime(2026, 10, 5, 8, tzinfo=UTC)
    repository = MaintenanceRepository(tmp_path / "maintenance.sqlite3", clock=lambda: now)
    item = repository.observe(
        Incident(
            resource_key="SOURCE:s:ACQUISITION",
            failure_class="ACQUISITION",
            source_ids=["s"],
            first_observed_at=now,
            last_observed_at=now,
        )
    )
    round_ = RepairRound(
        incident_id=item.incident_id,
        number=1,
        started_at=now,
        deadline=now + timedelta(minutes=20),
        worktree="candidate",
        evidence_path="context",
    )
    repository.begin_round(round_, daily_rounds=6, daily_seconds=7200)
    repository.operator(item.incident_id, retry=False, reason="human cancellation")
    stopped = []

    class FakeDocker:
        def stop_worker(self, value):
            stopped.append(value.round_id)

    Controller(
        MaintenanceSettings(enabled=True, mode="diagnose", root=tmp_path),
        repository,
        docker=FakeDocker(),
        backend=object(),
    ).tick()
    assert stopped == [round_.round_id]
    assert repository.rounds()[0].status == "CANCELLED"
    repository.begin_round(
        round_.model_copy(update={"round_id": "new-round", "incident_id": "other"}),
        daily_rounds=6,
        daily_seconds=7200,
    )
