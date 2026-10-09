from __future__ import annotations

from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexD3Node
from doxagent.workflows.codex_document3.recovery import resume_v21
from doxagent.workflows.codex_document3.state_v21 import digest
from tests.test_codex_document3_v21_orchestration import NOW, draft
from tests.test_codex_document3_v21_orchestration import rig as v21_rig


@pytest.fixture(name="rig")
def _rig(tmp_path):
    return v21_rig.__wrapped__(tmp_path)


@pytest.mark.asyncio
async def test_maintain_single_node_feed_without_event_and_bad_condition_base_preserved(rig):
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-base")
    version = initial.handoff.policy_set_version
    base = rig.state.get_staged_v3("MU", version)
    raw = base.policies[0].model_dump(mode="json")
    raw["activation_conditions"][0]["calibration"] = {}
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [raw],
        "retire_policy_ids": [],
        "summary": "fixture",
    }
    start = len(rig.worker.requests)
    result = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        maintenance_feed={"hits": ["fixture"]},
        run_id="d3v21-maintain",
    )
    assert result.status == "PARTIAL"
    requests = rig.worker.requests[start:]
    assert len(requests) == 1 and requests[0].node == CodexD3Node.O3_MAINTAIN
    updated = rig.state.get_staged_v3("MU", result.handoff.policy_set_version)
    assert updated.policies == base.policies
    assert rig.policy.get_current_version("MU") == 1
    assert await resume_v21(rig.orchestrator, result.run_id) == result
    assert len(rig.worker.requests[start:]) == 1


@pytest.mark.asyncio
async def test_noop_requires_no_assets_or_worker_and_patch_missing_degraded(rig):
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-base")
    version = initial.handoff.policy_set_version
    start = len(rig.worker.requests)
    rig.orchestrator.runner.assets = {}
    noop = await rig.orchestrator.maintain(
        ticker="MU", base_policy_version=version, as_of=NOW, run_id="d3v21-noop"
    )
    assert noop.status == "NOOP" and len(rig.worker.requests) == start
    assert await resume_v21(rig.orchestrator, noop.run_id) == noop
    rig.orchestrator.runner.assets = rig.assets
    degraded = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        explicit_maintenance=True,
        run_id="d3v21-degraded",
    )
    assert degraded.status == "DEGRADED" and degraded.handoff is None
    assert await resume_v21(rig.orchestrator, degraded.run_id) == degraded
    assert len(rig.worker.requests) == start + 1


@pytest.mark.asyncio
async def test_upsert_retire_conflict_preserves_base_and_asof_alone_not_version(rig):
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-base")
    version = initial.handoff.policy_set_version
    base = rig.state.get_staged_v3("MU", version)
    raw = base.policies[0].model_dump(mode="json")
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [raw],
        "retire_policy_ids": [raw["policy_id"]],
    }
    result = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        explicit_maintenance=True,
        run_id="d3v21-conflict",
    )
    assert result.status == "PARTIAL"
    assert (
        rig.state.get_staged_v3("MU", result.handoff.policy_set_version).policies == base.policies
    )
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [raw],
        "retire_policy_ids": [],
    }
    noop = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW.replace(day=5),
        explicit_maintenance=True,
        run_id="d3v21-same",
    )
    assert noop.status == "NOOP"


@pytest.mark.asyncio
async def test_maintenance_gap_presence_not_default_empty_and_metadata_header_new_version(rig):
    rig.worker.gaps = [{"name": "open", "reason": "fixture unresolved"}]
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-gap-base")
    assert initial.status == "PARTIAL"
    version = initial.handoff.policy_set_version
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [],
        "retire_policy_ids": [],
    }
    inherited = await rig.orchestrator.maintain(
        ticker="MU", base_policy_version=version, as_of=NOW, run_id="d3v21-gap-inherit"
    )
    assert inherited.status == "PARTIAL"
    run = rig.state.run(inherited.run_id)
    assert "remaining_gaps" not in run["patch_fields"] and run["coverage"]["remaining_gaps"]
    rig.worker.patch["remaining_gaps"] = []
    closed = await rig.orchestrator.maintain(
        ticker="MU", base_policy_version=version, as_of=NOW.replace(day=5), run_id="d3v21-gap-close"
    )
    assert closed.status == "COMPLETE"
    import json

    coverage = json.loads(
        (
            await rig.workspace.read_text(closed.run_id, closed.handoff.files["coverage_map.json"])
        ).content
    )
    assert coverage["policy_set_version"] == closed.handoff.policy_set_version
    assert coverage["as_of"] == NOW.replace(day=5).isoformat() and coverage["remaining_gaps"] == []


@pytest.mark.asyncio
async def test_metadata_only_event_change_and_empty_formal_feed_noop(rig):
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-meta-base")
    version = initial.handoff.policy_set_version
    feed = {
        "contract_version": "persistent-runtime.o3-maintenance-feed.v1",
        "ticker": "MU",
        "trading_date": "2026-10-04",
        "reference_view_delta": {
            "reference_view_delta": "",
            "removed_event_ids": [],
            "from_library_version": 9,
            "to_library_version": 9,
        },
        "trade_records": [],
        "badcase_records": [],
        "w3_coverage_gaps": [],
        "trade_candidates": [],
    }
    start = len(rig.worker.requests)
    empty = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        maintenance_feed=feed,
        run_id="d3v21-empty-feed",
    )
    assert empty.status == "NOOP" and len(rig.worker.requests) == start
    snapshot = SimpleNamespace(
        published_at=NOW,
        as_of=None,
        contract_version="event-library-reference-view-v1",
        ticker="MU",
        version=9,
        sha256=digest("full Event view"),
        reference_view="full Event view",
    )
    rig.orchestrator.preparer.legacy._event_library_reader = SimpleNamespace(
        reference_view=lambda *_, **__: snapshot
    )
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [],
        "retire_policy_ids": [],
    }
    changed = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        event_library_version=9,
        run_id="d3v21-meta-change",
    )
    assert changed.status == "COMPLETE" and changed.handoff.policy_set_version > version
    base, new = (
        rig.state.get_staged_v3("MU", version),
        rig.state.get_staged_v3("MU", changed.handoff.policy_set_version),
    )
    assert new.policies == base.policies and new.event_library_ref.version == 9


@pytest.mark.asyncio
async def test_multidirection_full_upsert_local_invalid_object_and_explicit_retirement(rig):
    policy = draft()
    policy["activation_conditions"] = [
        draft()["activation_conditions"][0],
        {**draft()["activation_conditions"][0], "trigger_layer": "MIDDLE"},
        {**draft()["activation_conditions"][0], "decision": "SHORT", "trigger_layer": "INNER"},
    ]
    rig.worker.policy = policy
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-three-base")
    version = initial.handoff.policy_set_version
    base = rig.state.get_staged_v3("MU", version)
    raw = base.policies[0].model_dump(mode="json")
    raw["activation_conditions"][0]["calibration"] = {}
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": ["malformed", raw],
        "retire_policy_ids": [],
    }
    result = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        maintenance_feed={"hits": ["C1"]},
        run_id="d3v21-three-maintain",
    )
    assert result.status == "PARTIAL"
    maintained = rig.state.get_staged_v3("MU", result.handoff.policy_set_version)
    assert maintained.policies[0].activation_conditions == base.policies[0].activation_conditions
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [],
        "retire_policy_ids": [base.policies[0].policy_id],
    }
    retired = await rig.orchestrator.maintain(
        ticker="MU",
        base_policy_version=version,
        as_of=NOW,
        explicit_maintenance=True,
        run_id="d3v21-retire",
    )
    assert retired.status == "COMPLETE"
    assert not rig.state.get_staged_v3("MU", retired.handoff.policy_set_version).policies
    assert rig.state.run(retired.run_id)["commit"]["publish_empty"] is True
    assert rig.policy.get_current_version("MU") == 1


@pytest.mark.asyncio
async def test_malformed_gap_record_does_not_implicitly_close_base_gap(rig):
    rig.worker.gaps = [{"name": "open", "reason": "still open"}]
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-bad-gap-base")
    version = initial.handoff.policy_set_version
    rig.worker.patch = {
        "base_policy_set_version": version,
        "upsert_policies": [],
        "retire_policy_ids": [],
        "remaining_gaps": [{"name": "incomplete"}],
    }
    result = await rig.orchestrator.maintain(
        ticker="MU", base_policy_version=version, as_of=NOW, run_id="d3v21-bad-gap"
    )
    assert result.status == "PARTIAL"
    assert rig.state.run(result.run_id)["coverage"]["remaining_gaps"] == rig.worker.gaps
