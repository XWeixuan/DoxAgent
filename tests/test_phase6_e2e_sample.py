import json
from pathlib import Path

import pytest

from doxagent.examples.phase6_exporter import DOCUMENT_ORDER
from doxagent.examples.phase6_mock_run import main, run_sample

FIXTURE_PATH = Path("examples/phase6_mock_ticker/fixture.json")
GENERATED_PATH = Path("examples/phase6_mock_ticker/generated_run.json")


def test_phase6_sample_export_contains_full_audit_surface() -> None:
    exported = run_sample(FIXTURE_PATH)

    assert exported["sample"]["mock"] is True
    assert exported["workflow"]["status"] == "completed"
    assert exported["workflow"]["ticker"] == "NVDA"
    assert list(exported["documents"]) == DOCUMENT_ORDER
    assert len(exported["commit_log"]) == 5
    assert len(exported["working_memory"]) >= 5
    assert exported["evidence_refs"]
    assert exported["objections"][0]["status"] == "resolved"
    assert exported["objections"][0]["resolution_note"]
    assert exported["delegations"][0]["status"] == "completed"
    assert exported["delegations"][0]["result_summary"]
    assert exported["residual_risks"] == ["mock_fixture_only_no_real_external_services"]


def test_phase6_sample_documents_include_monitoring_outputs_without_trading_execution() -> None:
    exported = run_sample(FIXTURE_PATH)

    monitoring_config = exported["documents"]["monitoring_config"]
    monitoring_policy = exported["documents"]["monitoring_policy"]

    assert monitoring_config
    assert monitoring_policy
    policy_json = json.dumps(monitoring_policy)
    assert "broker_api" not in policy_json
    assert "order_id" not in policy_json
    assert "executed_trade" not in policy_json
    assert "No broker action is triggered" in policy_json


def test_phase6_commit_log_links_patches_authors_reasons_and_evidence() -> None:
    exported = run_sample(FIXTURE_PATH)

    for commit in exported["commit_log"]:
        assert commit["patch_id"]
        assert commit["author_agent"]
        assert commit["trigger_reason"]
        assert commit["evidence_ids"]
        assert commit["after"]

    expectation_commit = [
        commit
        for commit in exported["commit_log"]
        if commit["document_type"] == "expectation_unit"
    ][0]
    assert expectation_commit["resolved_objection_ids"]
    assert expectation_commit["residual_disputes"] == []


def test_phase6_module_entrypoint_writes_output_json(tmp_path: Path) -> None:
    output_path = tmp_path / "generated_run.json"

    exit_code = main(["--fixture", str(FIXTURE_PATH), "--output", str(output_path)])

    assert exit_code == 0
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert exported["workflow"]["status"] == "completed"
    assert list(exported["documents"]) == DOCUMENT_ORDER


def test_phase6_committed_generated_artifact_is_reviewable_json() -> None:
    exported = json.loads(GENERATED_PATH.read_text(encoding="utf-8"))

    assert exported["sample"]["mock"] is True
    assert exported["workflow"]["status"] == "completed"
    assert list(exported["documents"]) == DOCUMENT_ORDER
    assert exported["commit_log"]


def test_phase6_module_entrypoint_prints_compact_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH)])

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sample"] == "phase6_mock_ticker"
    assert summary["status"] == "completed"
    assert summary["document_types"] == DOCUMENT_ORDER
