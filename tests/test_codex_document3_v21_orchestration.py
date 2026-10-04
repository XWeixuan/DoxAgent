from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import CodexD3AgentRole, CodexD3Node
from doxagent.codex_worker.schema import WorkerJob
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.orchestrator_v21 import Document3OrchestratorV21, batches
from doxagent.workflows.codex_document3.recovery import resume_v21
from doxagent.workflows.codex_document3.repository import (
    InMemoryDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
)
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from doxagent.workflows.codex_document3.runner_v21 import AssetDependencyError
from doxagent.workflows.codex_document3.runtime_projection import project_policy_set_v3
from doxagent.workflows.codex_document3.schema_v21 import Lead, Replacement
from doxagent.workflows.codex_document3.state_v21 import StateV21, canonical, digest
from doxagent.workflows.codex_document3.validation_v21 import (
    accept_policy,
    normalize_agenda,
    records,
    replace_structurally,
)
from tests.test_codex_document3_workflow import _AsyncWorkspace, _policy_set

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def draft():
    return {
        "title": "fixture policy",
        "ref": [],
        "transmission": "fixture transmission",
        "match_scope": "fixture news",
        "activation_conditions": [
            {
                "decision": "LONG",
                "trigger_layer": "OUTER",
                "criterion": "fixture event",
                "calibration": {
                    "reference_state": "fixture state",
                    "trigger_boundary": "fixture boundary",
                },
            }
        ],
    }


class Worker:
    def __init__(self, workspace):
        self.workspace = workspace
        self.requests = []
        self.zero = False
        self.no_agenda = False
        self.supplement = False
        self.fail_build = False
        self.patch = None
        self.interrupt_supplement = False
        self.agenda = None
        self.gaps = []
        self.policy = None

    async def run(self, request):
        self.requests.append(request)
        path = re.search(r"Read task file (\S+),", request.prompt)[1]
        task = json.loads((await self.workspace.read_text(request.run_id, path)).content)
        outputs = task["output_paths"]

        async def write(path, value):
            await self.workspace.write_text(request.run_id, path, canonical(value))

        if request.node == CodexD3Node.O3_DISCOVERY:
            await self.workspace.write_text(
                request.run_id, outputs[0], '\n{"name":"signal","lead":"fixture lead"}\ninvalid\n'
            )
        elif request.node == CodexD3Node.O3_PLANNING and not self.no_agenda:
            await write(
                outputs[0],
                {
                    "topics": self.agenda["topics"]
                    if self.agenda
                    else [{"name": "T1", "owner": "OPEN", "brief": "fixture"}],
                    "waves": self.agenda["waves"] if self.agenda else [["T1"]],
                },
            )
        elif request.node == CodexD3Node.O3_BUILD:
            if task["round"] == "supplement" and self.interrupt_supplement:
                self.interrupt_supplement = False
                raise asyncio.CancelledError()
            if self.fail_build:
                return WorkerJob(
                    job_id=request.attempt_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    status="failed",
                    error_message="fixture failure",
                )
            policies = []
            if not self.zero:
                policy_path = outputs[0] + "policies/one.json"
                await write(policy_path, self.policy or draft())
                policies.append(policy_path)
            await self.workspace.write_text(
                request.run_id,
                outputs[0] + "results.jsonl",
                "\n".join(
                    canonical({"topic": t["name"], "policies": policies, "notes": "", "ref": []})
                    for t in task["topics"]
                ),
            )
        elif request.node == CodexD3Node.O3_INTEGRATION:
            if task["stage"] == "review":
                await write(
                    outputs[0], {"relations": [], "research_requests": [], "coverage_notes": ""}
                )
                await write(
                    outputs[1],
                    {
                        "topics": [{"name": "T2", "owner": "OPEN", "brief": "fixture"}]
                        if self.supplement
                        else [],
                        "waves": [],
                    },
                )
            elif task["stage"] == "post_supplement_review":
                await write(
                    outputs[0], {"relations": [], "research_requests": [], "coverage_notes": ""}
                )
            elif task["stage"] == "consolidation":
                basis_map = next(m for m in task["read_mapping"] if m["ref"].endswith("basis.json"))
                basis = json.loads(
                    (
                        await self.workspace.read_text(request.run_id, basis_map["local_path"])
                    ).content
                )
                topics = task["main_agenda"]["topics"] + task["supplement_agenda"]["topics"]
                await write(
                    outputs[0],
                    {
                        "replacements": [],
                        "coverage": [
                            {
                                "topic": t["name"],
                                "policies": list(basis) if t["name"] == "T1" else [],
                                "note": "",
                            }
                            for t in topics
                        ],
                        "remaining_gaps": self.gaps,
                        "summary": "fixture complete",
                    },
                )
        elif request.node == CodexD3Node.O3_MAINTAIN and self.patch is not None:
            await write(outputs[0], self.patch)
        return WorkerJob(
            job_id=request.attempt_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id=f"thread-{request.run_id}",
            final_response='{"completed":true}',
        )


@pytest.fixture
def rig(tmp_path):
    workspace = _AsyncWorkspace(tmp_path / "workspace")
    worker = Worker(workspace)
    runtime = InMemoryCodexRuntimeRepository()
    policy = SQLiteDocument3PolicyRepository(tmp_path / "runtime.db")
    policy.publish(_policy_set(), expected_base_version=None)
    legacy = Document3AgentRunner(
        worker=worker,
        workspace=workspace,
        model="fixture",
        model_provider=None,
        runtime_repository=runtime,
    )
    assets = {}
    for name in ["role", "common", "discovery", "planning", "build", "integration", "maintain"]:
        path = tmp_path / f"{name}.md"
        path.write_text("fixture external asset", encoding="utf8")
        assets[name] = str(path)
    state = StateV21(tmp_path / "runtime.db")
    preparer = Document3InputPreparer(runtime_repository=runtime, policy_repository=policy)
    orchestrator = Document3OrchestratorV21(
        input_preparer=preparer,
        agent_runner=legacy,
        state=state,
        node_assets=assets,
        policy_repository=policy,
    )
    return SimpleNamespace(
        orchestrator=orchestrator,
        worker=worker,
        workspace=workspace,
        state=state,
        runtime=runtime,
        policy=policy,
        assets=assets,
    )


@pytest.mark.asyncio
async def test_open_global_four_files_staged_replay_and_contract(rig):
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-test")
    assert result.status == "COMPLETE"
    assert result.handoff.policy_set_version == 2
    assert rig.policy.get_current_version("MU") == 1
    assert [v.policy_set_version for v in rig.policy.list_version_metadata("MU")] == [1]
    policy_set = rig.state.get_staged_v3("MU", 2)
    assert len(policy_set.policies) == 1
    assert policy_set.policies[0].activation_conditions[0].condition_id == "C1"
    contract = json.loads(
        __import__("pathlib")
        .Path("dev_plan/workflow_v2.1/document3_v2.1_contracts.schema.json")
        .read_text(encoding="utf8")
    )
    Draft202012Validator(contract).validate(policy_set.model_dump(mode="json"))
    calls = len(rig.worker.requests)
    again = await resume_v21(rig.orchestrator, result.run_id)
    assert again == result and len(rig.worker.requests) == calls
    for name, path in result.handoff.files.items():
        text = (await rig.workspace.read_text(result.run_id, path)).content
        assert digest(text) == result.handoff.hashes[name]
    assert all(r.run_id != result.run_id for r in rig.worker.requests)
    planning = next(r for r in rig.worker.requests if r.node == CodexD3Node.O3_PLANNING)
    integration = next(r for r in rig.worker.requests if r.node == CodexD3Node.O3_INTEGRATION)
    assert planning.thread_id == integration.thread_id == "thread-d3v21-test-global"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "zero,no_agenda,failed,status",
    [
        (True, False, False, "COMPLETE"),
        (False, True, False, "PARTIAL"),
        (False, False, True, "PARTIAL"),
    ],
)
async def test_local_failure_and_zero_policy_dispositions(rig, zero, no_agenda, failed, status):
    rig.worker.zero, rig.worker.no_agenda, rig.worker.fail_build = zero, no_agenda, failed
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-local")
    assert result.status == status
    assert rig.policy.get_current_version("MU") == 1
    if failed:
        task = rig.state.task(result.run_id, "research:OPEN:0")
        assert task["attempt_count"] == 2 and task["status"] == "FAILED"
        assert len([r for r in rig.worker.requests if r.node == CodexD3Node.O3_BUILD]) == 2


@pytest.mark.asyncio
async def test_supplement_crash_persists_budget_and_dispatch_flag(rig):
    rig.worker.supplement = rig.worker.interrupt_supplement = True
    with pytest.raises(asyncio.CancelledError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-supp")
    run = rig.state.run("d3v21-supp")
    assert run["supplement_dispatched"] is True
    result = await resume_v21(rig.orchestrator, "d3v21-supp")
    assert result.status == "COMPLETE"
    assert rig.state.task(result.run_id, "supplement:OPEN:0")["attempt_count"] == 1
    supp = [
        r
        for r in rig.worker.requests
        if r.node == CodexD3Node.O3_BUILD and ":supplement:" in r.idempotency_key
    ]
    assert len(supp) == 2 and supp[0] == supp[1]
    assert len(rig.state.get_staged_v3("MU", result.handoff.policy_set_version).policies) == 1


@pytest.mark.asyncio
async def test_publish_failure_replays_without_worker_or_new_timestamp(rig, monkeypatch):
    original = rig.workspace.publish

    async def fail(*args):
        raise OSError("fixture publication interrupted")

    monkeypatch.setattr(rig.workspace, "publish", fail)
    with pytest.raises(OSError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-publish")
    commit = rig.state.run("d3v21-publish")["commit"]
    calls = len(rig.worker.requests)
    monkeypatch.setattr(rig.workspace, "publish", original)
    result = await resume_v21(rig.orchestrator, "d3v21-publish")
    assert len(rig.worker.requests) == calls
    assert rig.state.run(result.run_id)["commit"] == commit


@pytest.mark.asyncio
async def test_dependency_and_frozen_identity_integrity(rig):
    rig.orchestrator.runner.assets = {}
    with pytest.raises(AssetDependencyError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW)
    assert not rig.worker.requests
    rig.orchestrator.runner.assets = rig.assets
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-integrity")
    with pytest.raises(ValueError, match="identity"):
        await rig.orchestrator.initialize(
            ticker="MU", as_of=NOW.replace(day=5), run_id=result.run_id
        )
    task = rig.state.task(result.run_id, "discovery:OPEN")
    snapshot = next(iter(task["snapshots"].values()))
    await rig.workspace.write_text(result.run_id, snapshot, "corrupt")
    with pytest.raises(ValueError, match="integrity"):
        await resume_v21(rig.orchestrator, result.run_id)


def test_agenda_global_normalization_conflicts_and_original_line_numbers():
    a, issues = normalize_agenda(
        {
            "topics": [
                {"name": "T1", "owner": "same", "brief": "x"},
                {"name": "T2", "owner": "GLOBAL", "brief": "y"},
                {"name": "bad", "owner": "OPEN", "brief": "x"},
                {"name": "bad", "owner": "OPEN", "brief": "other"},
            ],
            "waves": [["T1", "T2", "T1", "unknown"]],
        },
        {"S1": "same", "S2": "same", "OPEN": "OPEN", "GLOBAL": "GLOBAL"},
    )
    assert a.waves == [["T1"], ["T2"]] and a.topics[0].owner == "OPEN"
    assert len(issues) == 2
    valid, bad = records('\n{"name":"x","lead":"y"}\nbad\n', Lead)
    assert valid[0][0] == 2 and bad[0]["line"] == 3


def test_durable_ids_salvage_revision_and_no_business_guessing(tmp_path):
    state = StateV21(tmp_path / "state.db")
    first, errors = accept_policy(draft(), state=state, run_id="r", path="output/a.json")
    assert not errors
    repeat, _ = accept_policy(draft(), state=state, run_id="r", path="output/a.json")
    assert repeat == first
    raw = first.model_dump(mode="json")
    raw["title"] = "new title"
    raw["activation_conditions"].append(
        {**draft()["activation_conditions"][0], "decision": "SHORT"}
    )
    changed, _ = accept_policy(raw, state=state, run_id="r", path="output/a.json")
    assert changed.policy_id == first.policy_id
    assert [c.condition_id for c in changed.activation_conditions] == ["C1", "C2"]
    raw = changed.model_dump(mode="json")
    raw["activation_conditions"][0]["calibration"]["trigger_boundary"] = "new boundary"
    revised, _ = accept_policy(raw, state=state, run_id="r", path="output/a.json")
    base_set = SimpleNamespace(
        policies=[changed],
        ticker="MU",
        policy_set_version=1,
        as_of=NOW,
        published_at=NOW,
        publication_state="COMPLETE",
    )
    before = project_policy_set_v3(base_set)
    base_set.policies = [revised]
    after = project_policy_set_v3(base_set)
    old, new = (
        before["policies"][0]["activation_conditions"],
        after["policies"][0]["activation_conditions"],
    )
    assert old[0]["condition_revision"] != new[0]["condition_revision"]
    assert old[1]["condition_revision"] == new[1]["condition_revision"]
    raw["activation_conditions"].append({"decision": "LONG"})
    salvaged, errors = accept_policy(raw, state=state, run_id="r", path="output/a.json")
    assert errors and len(salvaged.activation_conditions) == 2


def test_replacements_overlap_all_fail_and_unused_candidates(tmp_path):
    state = StateV21(tmp_path / "s.db")
    policies = [
        accept_policy(draft(), state=state, run_id="r", path=f"output/{i}.json")[0]
        for i in range(4)
    ]
    rs = [
        Replacement(before=[policies[0].policy_id], after=["c"], reason="x"),
        Replacement(before=[policies[0].policy_id], after=["d"], reason="y"),
    ]
    final, receipt = replace_structurally(policies[:2], {"c": policies[2], "d": policies[3]}, rs)
    assert final == policies[:2] and len(receipt["failed"]) == 2
    final, receipt = replace_structurally(
        policies[:2], {"c": policies[2], "d": policies[3]}, rs[:1]
    )
    assert final == [policies[1], policies[2]] and not receipt["failed"]
    final, _ = replace_structurally(
        policies[:2],
        {"c": policies[2]},
        [Replacement(before=[policies[0].policy_id], after=["missing"], reason="x")],
    )
    assert final == policies[:2]


def test_all_data_ceiling_planning_empty_and_oversize_not_truncated():
    registry = DataToolPolicyRegistry()
    assert not registry.allowed_tools(CodexD3Node.O3_PLANNING, CodexD3AgentRole.O3)
    full = registry.allowed_tools(CodexD3Node.O3_BUILD, CodexD3AgentRole.O3)
    assert full and not any(t.startswith(("message_bus.", "monitoring.")) for t in full)
    assert full == registry.allowed_tools(CodexD3Node.O3_INTEGRATION, CodexD3AgentRole.O3)
    huge = {"content": "x" * 140000}
    grouped = list(batches([{"small": "x"}, huge, {"small": "y"}]))
    assert grouped == [[{"small": "x"}], [huge], [{"small": "y"}]]


@pytest.mark.asyncio
async def test_d2_local_salvage_shared_originals_and_optional_global_reports(rig, tmp_path):
    from doxagent.codex_runtime.schema import PublishedDocument

    original = {
        "schema_version": "document2.v2.1",
        "ticker": "MU",
        "source_global_run_id": "g1",
        "as_of": NOW.isoformat(),
        "shells": [
            {
                "name": "Shell A",
                "scope": "full scope",
                "boundary": "full boundary",
                "units": [
                    {
                        "name": "U1",
                        "scope": "full unit",
                        "horizon": "long",
                        "state": {"parameters": [], "values": []},
                        "expectation_baseline": [],
                        "realization_factors": [],
                        "potential_gaps": [],
                    },
                    {"name": "broken"},
                ],
            },
            {"name": "broken shell"},
        ],
    }
    text = canonical(original)
    published = PublishedDocument(
        run_id="d2",
        artifact_id="a",
        artifact_kind="bundle",
        sha256=digest(text),
        size_bytes=len(text.encode()),
        content_type="application/json",
        content_text=text,
        published_at=NOW,
    )
    await rig.workspace.write_text("g1", "artifacts/c1.md", "complete C1 original")
    bundle = SimpleNamespace(
        status="published",
        ticker="MU",
        handoff=SimpleNamespace(
            document2_artifact_id="a", published_at=NOW, publication_state="COMPLETE"
        ),
    )
    global_bundle = SimpleNamespace(
        status="published",
        ticker="MU",
        reports={
            "c1": SimpleNamespace(
                relative_path="artifacts/c1.md", sha256=digest("complete C1 original")
            )
        },
        published_at=NOW,
        future_nodes=[{"name": "future original"}],
        entity_relations=[{"name": "relation original"}],
    )
    repository = SimpleNamespace(
        get_bundle=lambda key: bundle if key == "d2" else global_bundle,
        get_published_document=lambda *_: published,
        list_artifacts=lambda *_, **__: [],
    )
    rig.orchestrator.preparer.legacy._runtime_repository = repository
    extra = tmp_path / "source.txt"
    extra.write_bytes(b"original\r\nmaterial")
    prepared = await rig.orchestrator.preparer.prepare(
        ticker="MU",
        as_of=NOW,
        document2_run_id="d2",
        additional_materials=[{"path": str(extra), "sha256": digest("original\r\nmaterial")}],
    )
    assert prepared["files"]["context/document3/v21/shared/document2.json"] == text
    assert len(prepared["topology"]["shells"]) == 1
    shell = json.loads(prepared["files"][prepared["topology"]["shells"][0]["path"]])
    assert len(shell["units"]) == 1 and shell["units"][0]["potential_gaps"] == []
    assert "complete C1 original" in prepared["files"].values()
    assert "original\r\nmaterial" in prepared["files"].values()
    assert len(prepared["warnings"]) == 2
    original["schema_version"] = "document2.v2"
    text = canonical(original)
    published = published.model_copy(
        update={"content_text": text, "size_bytes": len(text.encode()), "sha256": digest(text)}
    )
    historical = await rig.orchestrator.preparer.prepare(
        ticker="MU", as_of=NOW, document2_run_id="d2"
    )
    assert not historical["topology"]["shells"]
    assert historical["files"]["context/document3/v21/shared/document2.json"] == text


@pytest.mark.asyncio
async def test_cutoff_nullable_event_and_all_additional_files_frozen_on_resume(rig, tmp_path):
    newer = NOW.replace(day=5)
    snapshot = SimpleNamespace(
        published_at=newer,
        as_of=None,
        contract_version="event-library-reference-view-v1",
        ticker="MU",
        version=9,
        sha256="a" * 64,
        reference_view="complete view",
    )
    rig.orchestrator.preparer.legacy._event_library_reader = SimpleNamespace(
        reference_view=lambda *_, **__: snapshot
    )
    prepared = await rig.orchestrator.preparer.prepare(ticker="MU", as_of=NOW)
    assert prepared["event_library_ref"] is None
    pinned = await rig.orchestrator.preparer.prepare(
        ticker="MU", as_of=NOW, event_library_version=9
    )
    assert pinned["event_library_ref"]["version"] == 9 and pinned["as_of"] == NOW.isoformat()
    extra = tmp_path / "source.txt"
    extra.write_text("old original", encoding="utf8")
    result = await rig.orchestrator.initialize(
        ticker="MU", as_of=NOW, additional_materials=[str(extra)], run_id="d3v21-frozen"
    )
    extra.write_text("new mutable content", encoding="utf8")

    def unavailable(*_, **__):
        raise AssertionError("provider queried during recovery")

    rig.orchestrator.preparer.legacy._event_library_reader.reference_view = unavailable
    assert await resume_v21(rig.orchestrator, result.run_id) == result
    assert (
        rig.state.run(result.run_id)["prepared"]["files"][
            "context/document3/v21/shared/additional/0000.txt"
        ]
        == "old original"
    )
    with pytest.raises(ValueError, match="timezone"):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW.replace(tzinfo=None))


@pytest.mark.asyncio
async def test_owner_concurrency_no_global_wave_barrier_and_session_reuse(rig, monkeypatch):
    async def prepare(**_):
        return {
            "files": {},
            "manifest": [],
            "topology": {"shells": [], "owners": {"OPEN": "OPEN", "GLOBAL": "GLOBAL"}},
            "document2_ref": None,
            "event_library_ref": None,
            "warnings": [],
            "as_of": NOW.isoformat(),
        }

    monkeypatch.setattr(rig.orchestrator.preparer, "prepare", prepare)
    rig.worker.agenda = {
        "topics": [
            {"name": "T1", "owner": "OPEN", "brief": "first"},
            {"name": "T2", "owner": "OPEN", "brief": "second"},
            {"name": "G1", "owner": "GLOBAL", "brief": "slow"},
        ],
        "waves": [["T1"], ["G1"], ["T2"]],
    }
    original = rig.worker.run
    second_wave = asyncio.Event()
    global_started = asyncio.Event()
    active, maximum, events = set(), 0, []

    async def tracked(request):
        nonlocal maximum
        if request.node == CodexD3Node.O3_BUILD:
            assert request.run_id not in active
            active.add(request.run_id)
            maximum = max(maximum, len(active))
            key = request.idempotency_key
            if ":GLOBAL:" in key:
                global_started.set()
                await asyncio.wait_for(second_wave.wait(), timeout=5)
            if ":OPEN:0:" in key:
                await asyncio.wait_for(global_started.wait(), timeout=5)
            if ":OPEN:2:" in key:
                second_wave.set()
        result = await original(request)
        events.append(request.node)
        active.discard(request.run_id)
        return result

    monkeypatch.setattr(rig.worker, "run", tracked)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-parallel")
    assert result.status == "COMPLETE" and 2 <= maximum <= 3
    assert events.index(CodexD3Node.O3_PLANNING) == 2
    open_build = [
        r
        for r in rig.worker.requests
        if r.node == CodexD3Node.O3_BUILD and r.run_id.endswith("-open")
    ]
    assert len(open_build) == 2 and open_build[0].thread_id == open_build[1].thread_id


def test_condition_counter_across_runs_removal_no_reuse_and_local_bad_ids(tmp_path):
    state = StateV21(tmp_path / "s.db")
    raw = draft()
    raw["activation_conditions"] *= 2
    first, _ = accept_policy(raw, state=state, run_id="r1", path="output/a.json")
    raw = first.model_dump(mode="json")
    raw["activation_conditions"] = raw["activation_conditions"][:1]
    removed, _ = accept_policy(
        raw, state=state, run_id="r2", path="output/a.json", inherited=first.model_dump(mode="json")
    )
    raw = removed.model_dump(mode="json")
    raw["activation_conditions"].append(draft()["activation_conditions"][0])
    raw["activation_conditions"].append(
        {**draft()["activation_conditions"][0], "condition_id": ["invalid"]}
    )
    third, errors = accept_policy(
        raw,
        state=state,
        run_id="r3",
        path="output/a.json",
        inherited=removed.model_dump(mode="json"),
    )
    assert [c.condition_id for c in third.activation_conditions] == ["C1", "C3"]
    assert errors


def test_rollback_collision_and_merge_split_with_no_transitive_economic_logic(tmp_path):
    state = StateV21(tmp_path / "s.db")
    p = [
        accept_policy(draft(), state=state, run_id="r", path=f"output/{i}.json")[0]
        for i in range(4)
    ]
    retained_collision = p[2].model_copy(update={"policy_id": p[1].policy_id})
    rs = [
        Replacement(before=[p[0].policy_id], after=["after"], reason="fixture"),
        Replacement(before=[p[1].policy_id], after=["missing"], reason="fixture"),
    ]
    final, receipt = replace_structurally(p[:2], {"after": retained_collision}, rs)
    assert final == p[:2] and len(receipt["failed"]) == 2
    final, receipt = replace_structurally(
        p[:2],
        {"a": p[2], "b": p[3]},
        [Replacement(before=[p[0].policy_id, p[1].policy_id], after=["a", "b"], reason="split")],
    )
    assert final == p[2:] and not receipt["failed"]


def test_staged_repository_inmemory_sqlite_contract_and_shared_reservations(rig):
    from concurrent.futures import ThreadPoolExecutor

    from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3

    staged = PolicySetV3(
        ticker="MU",
        policy_set_version=2,
        as_of=NOW,
        published_at=NOW,
        publication_state="COMPLETE",
        policies=[],
    )
    for repo in [InMemoryDocument3PolicyRepository(), rig.policy]:
        repo.save_staged_v3(
            "fixture", staged, project_policy_set_v3(staged), {"files": {}, "hashes": {}}
        )
        repo.save_staged_v3(
            "fixture", staged, project_policy_set_v3(staged), {"files": {}, "hashes": {}}
        )
        assert repo.get_staged_v3("MU", 2) == staged
        assert repo.get_staged_v3_by_run("fixture") == staged
        assert repo.get_version("MU", 2) is None
        with pytest.raises(ValueError, match="immutable"):
            repo.save_staged_v3(
                "fixture", staged.model_copy(update={"publication_state": "PARTIAL"}), {}, {}
            )
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(
            pool.map(lambda i: StateV21(rig.state.path).reserve(f"run-{i}", "MU", 9), range(12))
        )
    assert len(set(versions)) == 12 and min(versions) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["four_files", "sqlite"])
async def test_publication_each_remaining_boundary_recovers_same_commit(rig, monkeypatch, boundary):
    if boundary == "four_files":
        original = rig.workspace.write_text

        async def failing(run_id, path, content):
            if path.startswith("artifacts/document3/releases/") and path.endswith("document3.md"):
                raise OSError("fixture four file failure")
            return await original(run_id, path, content)

        monkeypatch.setattr(rig.workspace, "write_text", failing)
    else:
        original = rig.state.save_staged_v3

        def failing(*_):
            raise OSError("fixture SQLite commit failure")

        monkeypatch.setattr(rig.state, "save_staged_v3", failing)
    with pytest.raises(OSError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-boundary")
    commit = rig.state.run("d3v21-boundary")["commit"]
    calls = len(rig.worker.requests)
    if boundary == "four_files":
        monkeypatch.setattr(rig.workspace, "write_text", original)
    else:
        monkeypatch.setattr(rig.state, "save_staged_v3", original)
    result = await resume_v21(rig.orchestrator, "d3v21-boundary")
    assert result.status == "COMPLETE" and len(rig.worker.requests) == calls
    assert rig.state.run(result.run_id)["commit"] == commit
    assert result.handoff.release_manifest_path.startswith("published/")


@pytest.mark.asyncio
async def test_frozen_input_write_interruption_recovers_without_provider_queries(rig, monkeypatch):
    original = rig.workspace.write_text

    async def fail(run_id, path, content):
        if path.endswith("input_manifest.json"):
            raise OSError("fixture freeze failure")
        return await original(run_id, path, content)

    monkeypatch.setattr(rig.workspace, "write_text", fail)
    with pytest.raises(OSError):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-prepare")
    assert rig.state.run("d3v21-prepare") and not rig.worker.requests
    monkeypatch.setattr(rig.workspace, "write_text", original)

    async def forbidden(**_):
        raise AssertionError("providers called on frozen-input recovery")

    monkeypatch.setattr(rig.orchestrator.preparer, "prepare", forbidden)
    assert (await resume_v21(rig.orchestrator, "d3v21-prepare")).status == "COMPLETE"


@pytest.mark.asyncio
async def test_late_lead_after_agenda_only_records_gap_never_second_supplement(rig, monkeypatch):
    original = rig.worker.run

    async def late(request):
        response = await original(request)
        if request.node == CodexD3Node.O3_BUILD:
            path = re.search(r"Read task file (\S+),", request.prompt)[1]
            task = json.loads((await rig.workspace.read_text(request.run_id, path)).content)
            await rig.workspace.write_text(
                request.run_id,
                task["output_paths"][-1],
                canonical({"name": "late", "lead": "fixture later finding"}) + "\n",
            )
        return response

    monkeypatch.setattr(rig.worker, "run", late)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-late")
    assert result.status == "PARTIAL"
    assert any(
        g["name"] == "late" for g in rig.state.run(result.run_id)["coverage"]["remaining_gaps"]
    )
    assert len([r for r in rig.worker.requests if r.node == CodexD3Node.O3_PLANNING]) == 1
    calls = len(rig.worker.requests)
    assert await resume_v21(rig.orchestrator, result.run_id) == result
    assert len(rig.worker.requests) == calls


@pytest.mark.asyncio
async def test_crlf_material_and_pilot_explicit_state_binding(rig, tmp_path):
    from doxagent.workflows.codex_document3.pilot import Document3PilotEvaluator
    from doxagent.workflows.codex_document3.pinned_runner import PinnedDocument3Runner

    source = tmp_path / "crlf.txt"
    source.write_bytes(b"unchanged\r\noriginal\r\n")
    result = await rig.orchestrator.initialize(
        ticker="MU", as_of=NOW, additional_materials=[str(source)], run_id="d3v21-crlf"
    )
    file = await rig.workspace.read_text(
        result.run_id, "context/document3/v21/shared/additional/0000.txt"
    )
    assert file.content == "unchanged\r\noriginal\r\n" and file.sha256 == digest(file.content)
    report = await Document3PilotEvaluator(rig.workspace).evaluate(
        result.run_id, orchestration_version="v2.1"
    )
    assert (
        report.selected_topic_count == report.result_topic_count == report.coverage_topic_count == 1
    )
    assert report.phase == "STAGED" and report.hashes == result.handoff.hashes
    with pytest.raises(ValueError, match="does not match"):
        PinnedDocument3Runner(rig.orchestrator)


def test_public_owner_names_alias_routes_and_local_structured_salvage():
    from doxagent.workflows.codex_document3.schema_v21 import Consolidation
    from doxagent.workflows.codex_document3.validation_v21 import structured

    agenda, _ = normalize_agenda(
        {
            "topics": [
                {"name": "one", "owner": "Shell A", "brief": "fixture"},
                {"name": "two", "owner": "same::shell-0002", "brief": "fixture"},
            ],
            "waves": [],
        },
        {"S1": "Shell A", "S2": "same", "S3": "same", "OPEN": "OPEN", "GLOBAL": "GLOBAL"},
        {"same::shell-0002": "S2"},
    )
    assert [t.owner for t in agenda.topics] == ["Shell A", "same::shell-0002"]
    assert agenda.waves == [["one"], ["two"]]
    conso, issues = structured(
        canonical(
            {
                "replacements": [{"before": [], "after": [], "reason": "valid"}, {"before": []}],
                "coverage": [],
                "remaining_gaps": [],
                "summary": "fixture",
                "annotation": "ignored",
            }
        ),
        Consolidation,
    )
    assert len(conso.replacements) == 1
    assert any(i.get("error") for i in issues) and any(i.get("warning") for i in issues)
    assert "annotation" not in conso.model_dump()


@pytest.mark.asyncio
async def test_partial_worker_files_are_accepted_without_business_retry(rig, monkeypatch):
    original = rig.worker.run

    async def partial(request):
        result = await original(request)
        if request.node == CodexD3Node.O3_BUILD:
            return result.model_copy(
                update={
                    "status": "failed",
                    "final_response": "bad receipt",
                    "error_message": "fixture partial receipt",
                }
            )
        return result

    monkeypatch.setattr(rig.worker, "run", partial)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-partial-files")
    assert result.status == "COMPLETE"
    assert rig.state.task(result.run_id, "research:OPEN:0")["status"] == "PARTIAL"
    assert rig.state.task(result.run_id, "research:OPEN:0")["attempt_count"] == 1


@pytest.mark.asyncio
async def test_read_only_input_tampering_is_local_terminal_failure(rig, monkeypatch):
    original = rig.worker.run

    async def tamper(request):
        result = await original(request)
        if request.node == CodexD3Node.O3_BUILD:
            path = rig.workspace.local.root / request.run_id / "AGENTS.md"
            path.write_text("tampered asset", encoding="utf8")
        return result

    monkeypatch.setattr(rig.worker, "run", tamper)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-tamper")
    assert result.status == "PARTIAL"
    task = rig.state.task(result.run_id, "research:OPEN:0")
    assert task["status"] == "FAILED" and task["attempt_count"] == 1 and not task["files"]
    assert rig.policy.get_current_version("MU") == 1


@pytest.mark.asyncio
async def test_planning_absent_one_supplement_and_no_second_cycle(rig):
    rig.worker.no_agenda = rig.worker.supplement = True
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="d3v21-no-plan-supp")
    assert result.status == "PARTIAL"
    assert rig.state.run(result.run_id)["supplement_dispatched"] is True
    assert len([r for r in rig.worker.requests if r.node == CodexD3Node.O3_BUILD]) == 1


def test_cli_plan_manifest_names_and_material_descriptors(tmp_path):
    from doxagent.workflows.codex_document3.cli import _materials, _parser

    manifest = tmp_path / "materials.json"
    descriptor = {"path": "context/source.json", "run_id": "source-run"}
    manifest.write_text(json.dumps([descriptor]), encoding="utf8")
    args = _parser().parse_args(
        [
            "maintain",
            "--ticker",
            "MU",
            "--orchestration-version",
            "v2.1",
            "--base-policy-set-version",
            "2",
            "--node-assets-manifest",
            "assets.json",
            "--materials-manifest",
            str(manifest),
            "--additional-material",
            "local.md",
        ]
    )
    assert args.base_policy_version == 2 and str(args.node_assets) == "assets.json"
    assert _materials(args) == ["local.md", descriptor]
    manifest.write_text("{}", encoding="utf8")
    with pytest.raises(ValueError, match="JSON list"):
        _materials(args)


@pytest.mark.asyncio
async def test_sdk_global_resume_planning_disables_then_build_reenables_data(tmp_path, monkeypatch):
    from doxagent.codex_runtime.schema import CODEX_DOCUMENT3_WORKFLOW_VERSION, ResearchLane
    from doxagent.codex_worker.schema import WorkerRunRequest
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from tests.test_codex_runtime_v2 import _AsyncSdkClient

    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *_, **__: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    root = tmp_path / "owner"
    root.mkdir()
    for ordinal, node in enumerate(
        [CodexD3Node.O3_DISCOVERY, CodexD3Node.O3_PLANNING, CodexD3Node.O3_BUILD]
    ):
        await runtime.start(
            WorkerRunRequest(
                workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
                research_lane=ResearchLane.DOCUMENT3,
                run_id="d3v21-owner",
                ticker="MU",
                node=node,
                agent_role=CodexD3AgentRole.O3,
                attempt_id=f"a{ordinal}",
                prompt="fixture transport",
                output_schema={"type": "object"},
                thread_id="existing-owner-thread",
                model="fixture",
                data_mcp_enabled=node != CodexD3Node.O3_PLANNING,
            ),
            root,
        )
        assert sdk.thread_resume_kwargs["config"]["mcp_servers.data.enabled"] is (
            node != CodexD3Node.O3_PLANNING
        )
