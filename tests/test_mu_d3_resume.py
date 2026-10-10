from scripts.ops.resume_mu_d3_v21 import (
    DERIVED,
    SOURCE,
    SUCCESSOR,
    _successor_payload,
    _successor_task,
)


def test_successor_checkpoint_keeps_frozen_plan_without_partial_publication():
    source = {
        "phase": "STAGED",
        "status": "PARTIAL",
        "prepared": {"topology": {"owners": ["GLOBAL", "S0001"]}},
        "agenda": {"topics": [], "waves": []},
        "scan": ["frozen lead"],
        "discovery_missing": ["prior warning"],
        "missing": ["Build failed", "Integration unavailable"],
        "diagnostics": [{"error": "usage_limit_exceeded"}],
        "version_floor": 20,
        "commit": {"version": 21},
        "basis": {"old": "partial"},
        "review": None,
    }
    result = _successor_payload(source)
    assert result["phase"] == "BUILD"
    assert result["scan"] == ["frozen lead"]
    assert result["agenda"] == source["agenda"]
    assert result["missing"] == ["prior warning"]
    assert result["version_floor"] == 21
    assert result["owner_workspaces"]["S0001"] == f"{SUCCESSOR}-s0001"
    assert not (DERIVED & result.keys())
    assert source["commit"] == {"version": 21}


def test_only_unfinished_build_waves_are_reopened_with_fresh_model_session():
    completed = {
        "status": "COMPLETED", "attempt_count": 1,
        "owner_slot": "S0001", "request": {"model": "gpt-6-sol"},
        "files": {"output/result.json": "{}"},
    }
    failed = {
        **completed, "status": "FAILED", "attempt_count": 2,
        "job": {"status": "failed"}, "acceptance": {"results": {}},
    }
    partial = {**failed, "status": "PARTIAL", "attempt_count": 1}

    inherited = _successor_task("research:S0001:0", completed)
    assert inherited["status"] == "COMPLETED"
    assert inherited["request"]["model"] == "gpt-6-sol"
    assert inherited["workspace_run_id"] == f"{SUCCESSOR}-s0001"
    for original in (failed, partial):
        reopened = _successor_task("research:S0001:1", original)
        assert reopened["status"] == "PENDING"
        assert reopened["attempt_count"] == 0
        assert reopened["max_attempts"] == 1
        assert reopened["fresh_session"] is True
        assert reopened["files"] == original["files"]
        assert "request" not in reopened
        assert "job" not in reopened
        assert "acceptance" not in reopened
    assert _successor_task("integration:main:0", failed) is None
    assert SOURCE != SUCCESSOR
