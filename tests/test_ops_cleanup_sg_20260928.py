"""Safety boundaries for the explicitly approved, dated operations cleanup."""

import importlib.util
import json
from pathlib import Path

import pytest

FILE = Path(__file__).parents[1] / "scripts/ops/cleanup_sg_20260928.py"
SPEC = importlib.util.spec_from_file_location("incident_cleanup", FILE)
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


def test_allowlists_exclude_dynamic_dependencies():
    assert len(cleanup.IMAGE_PREFIXES) == len(set(cleanup.IMAGE_PREFIXES)) == 42
    assert "3cc03c11fd9d" not in cleanup.IMAGE_PREFIXES
    assert "0522bf4e87bd" not in cleanup.IMAGE_PREFIXES
    assert cleanup.TEMPLATE not in cleanup.OLD_CONTAINERS


def test_targets_keep_latest_and_original_event_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DATA", tmp_path)
    latest = tmp_path / "backups/20260920T214547524194Z"
    latest.mkdir(parents=True)
    old = tmp_path / "backups" / cleanup.OLD_BACKUPS[0]
    old.mkdir()
    recovery = tmp_path / "backups/20260911T1050Z-o2-authoritative-recovery"
    recovery.mkdir()
    original = recovery / "event-library-primary.sqlite3"
    original.touch()
    read = recovery / "read-v2.sqlite3"
    read.touch()
    assert set(cleanup.backup_targets()) == {old, read}
    assert original.exists()
    assert latest.exists()


def test_rejects_volume_root_and_external_target(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setattr(cleanup, "DATA", root)
    for path in [root, external]:
        with pytest.raises(RuntimeError, match="escaped"):
            cleanup.safe_target(path)


def guardian(agent=cleanup.AGENT_TAG, template=cleanup.TEMPLATE):
    return {
        "Name": "/" + cleanup.GUARDIAN,
        "Image": "sha256:guardian",
        "Config": {
            "Env": [
                "DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE=" + agent,
                "DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER=" + template,
            ]
        },
    }


def test_protects_agent_without_a_container_reference(monkeypatch):
    monkeypatch.setattr(cleanup, "run", lambda _: json.dumps([{"Id": "sha256:agent"}]))
    protected = cleanup.protected_images(
        [
            guardian(),
            {"Name": "/" + cleanup.TEMPLATE, "Image": "sha256:template"},
            {"Name": "/doxagent-barrons-chrome-control", "Image": "sha256:old"},
        ]
    )
    assert protected == {"sha256:guardian", "sha256:agent", "sha256:template"}


@pytest.mark.parametrize("changed", [guardian(agent="other:server"), guardian(template="other")])
def test_changed_guardian_configuration_requires_reaudit(changed):
    with pytest.raises(RuntimeError, match="configuration changed"):
        cleanup.protected_images([changed])
