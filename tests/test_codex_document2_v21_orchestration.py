"""Offline contract and recovery acceptance for staged D2 v2.1."""

import asyncio
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexD2AgentRole, CodexD2Node
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    Document2PilotCaseRequest,
    Document2PilotUpstreamCase,
    _bootstrap_contract,
    _pilot_candidate_sets_context,
    _select_bootstrap_shell,
)
from doxagent.pilot.document2_coordinator import (
    Document2PilotCoordinatorRequest,
    _build_stages,
    _materialize_finalization_override,
)
from doxagent.workflows.codex_document2 import schema as s
from doxagent.workflows.codex_document2.orchestrator import (
    CodexDocument2Orchestrator,
    _candidate_sets_context,
)
from doxagent.workflows.codex_document2.recovery import fallback
from doxagent.workflows.codex_document2.validation import validate_output
from tests.fixtures.codex_document2 import (
    AS_OF,
    _Document2Worker,
    _global_fixture,
    _NarrativeProvider,
)


def seed(name="demand"):
    return dict(
        name=name,
        scope="demand system",
        boundary="commercial",
        ref=["D1-O1"],
        units=[dict(name="orders", scope="customer orders", horizon="FY27", ref=["D1-O1"])],
    )


def scan():
    return dict(
        shell="demand",
        units=[
            dict(
                name="orders",
                candidates=[
                    dict(
                        name="new buyer",
                        change_hypothesis="broader demand",
                        relevance="orders",
                        live_basis="disclosure",
                        ref=["https://example.com/scan-only"],
                    ),
                    dict(
                        name="routine",
                        change_hypothesis="unchanged",
                        relevance="orders",
                        live_basis="none",
                        ref=[],
                    ),
                ],
            )
        ],
    )


def selection():
    return dict(
        shell="demand",
        selections=[
            dict(
                unit="orders",
                candidate="new buyer",
                decision="DEEPEN",
                merge_into=None,
                reason="material",
                research_focus="durability",
                ref=[],
            ),
            dict(
                unit="orders",
                candidate="routine",
                decision="PARK",
                merge_into=None,
                reason="ordinary progression",
                research_focus="",
                ref=[],
            ),
        ],
    )


class Worker(_Document2Worker):
    def __init__(self, workspace, *, selection_invalid_once=False, fail_node=None, empty=False):
        super().__init__(workspace)
        self.selection_invalid_once = selection_invalid_once
        self.fail_node = fail_node
        self.empty = empty
        self.contexts = []
        self.scan_commits = 0

    async def _output(self, request):
        body = await self.workspace.read_text(
            request.run_id, f"attempts/{request.attempt_id}/input/context.json"
        )
        ctx = json.loads(body.content)
        self.contexts.append((request.node, ctx))
        if request.node == self.fail_node:
            return {}
        if "source_role" in ctx:
            return dict(
                candidates=[
                    dict(
                        name="orders", scope="orders scope", why_material="earnings", ref=["D1-O1"]
                    )
                ],
                warnings=[],
            )
        if request.node == CodexD2Node.O0_SYNTHESIS:
            candidates = [
                c for group in ctx["candidate_sets"].values() for c in group["candidates"]
            ]
            return dict(
                provisional_shells=[
                    dict(
                        shell_temp_id="temporary",
                        name="demand",
                        scope="demand system",
                        boundary="commercial",
                        ref=["D1-O1"],
                        candidate_units=candidates,
                    )
                ],
                unassigned_candidates=[],
                warnings=[],
            )
        if "reviewer_role" in ctx:
            return dict(
                reviewer_role=ctx["reviewer_role"],
                overall_assessment="valid",
                targeted_feedback=[],
                warnings=[],
            )
        if request.node == CodexD2Node.O0_FINALIZATION:
            return dict(shells=[] if self.empty else [seed()], finalization_note=[], warnings=[])
        if request.node == CodexD2Node.O1_OPEN_DISCOVERY:
            from doxagent.workflows.codex_document2.discovery_checkpoint import (
                SCAN_PATH,
                DiscoveryCheckpointService,
            )

            service = DiscoveryCheckpointService(
                self.workspace.store.root / request.run_id, request.run_id, request.attempt_id
            )
            frozen = service.load()
            if frozen is None:
                value = scan()
                value["shell"] = ctx["canonical_shell"]["name"]
                service.commit(value)
                self.scan_commits += 1
                frozen = service.load()
            # This assertion executes inside Worker.run, before its final response.
            assert (self.workspace.store.root / request.run_id / SCAN_PATH).is_file()
            chosen = selection()
            chosen["shell"] = frozen.shell
            if self.selection_invalid_once:
                self.selection_invalid_once = False
                chosen["selections"] = []
            return dict(scan_sha256=frozen.scan_sha256, selection=chosen)
        shell = ctx["canonical_shell"]
        unit = shell["units"][0]
        unit["state"] = dict(
            parameters=[
                dict(name="revenue", definition="revenue", value_type="NUMBER", ref=["D1-O1"])
            ],
            values=[
                dict(
                    name="sellside",
                    parameter="revenue",
                    source_role="SELL_SIDE",
                    value=dict(number=100, unit="USD"),
                    previous_value=None,
                    time_scope="FY27",
                    as_of="2026-08-20",
                    validity_state="CURRENT",
                    ref=["O9"],
                )
            ],
        )
        unit["expectation_baseline"] = [
            dict(
                name="ordinary",
                baseline="consensus",
                ordinary_progress="deliveries",
                open_frontier="new customers",
                time_scope="FY27",
                ref=["D1-O1"],
            )
        ]
        unit["realization_factors"] = [
            dict(
                name="buyer durability",
                mechanism="repeat orders",
                current_status="live",
                materiality_context="earnings",
                scope_boundary="customer",
                ref=["https://example.com/factor"],
            )
        ]
        unit["potential_gaps"] = [
            dict(
                name="buyer mix",
                why_live="new buyer",
                revision_logic="wider market",
                possibility_space=[dict(name="durable", implication="upside")],
                ref=["D1-O1"],
            )
        ]
        additions = []
        if request.node in {CodexD2Node.O1_STATE, CodexD2Node.O1_REALIZATION}:
            additions = [
                dict(
                    unit="orders",
                    name="new channel",
                    discovered_during=ctx["turn"],
                    change_hypothesis="more demand",
                    reason="new evidence",
                    ref=[],
                )
            ]
        resolutions = []
        if request.node == CodexD2Node.O1_STATE:
            resolutions = [
                dict(
                    unit="orders",
                    candidate="new buyer",
                    resolution="under review",
                    destination=None,
                    reason="research continues",
                )
            ]
        if request.node == CodexD2Node.O1_FINALIZATION:
            resolutions = [
                dict(
                    unit="orders",
                    candidate=c,
                    resolution="absorbed by mechanism",
                    destination="buyer durability",
                    reason="same driver",
                )
                for c in ("new buyer", "new channel")
            ]
        return dict(
            canonical_shell=shell, late_additions=additions, open_discovery_resolution=resolutions
        )


class Events:
    interface_version = "event-library-read-v2"
    read_only = True

    def __init__(self):
        self.calls = 0

    async def load(self, **kwargs):
        self.calls += 1
        return s_optional()


def s_optional():
    from doxagent.workflows.codex_document2.inputs import OptionalInput

    return OptionalInput(
        status=s.InputAvailability.AVAILABLE, as_of=AS_OF, payload={"events": ["published"]}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["document2.v2", "document2.v2.1"])
async def test_synthesis_pilot_seeds_complete_reports_without_candidate_branches(
    tmp_path, monkeypatch, version
):
    from doxagent.workflows.codex_document2.inputs import OptionalInput

    repo, ws, source = await _global_fixture(tmp_path)
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._repository, builder._client = repo, ws
    builder._source_cache_root = tmp_path / "pilot-sources"
    builder._event_library_provider = Events()
    builder._asset_root = Path(__file__).parents[1] / "prompts/codex_v2/document2"
    narrative_body = "# Narrative\n" + "完整正文\n" * 1000

    async def narrative(_):
        return OptionalInput(
            status=s.InputAvailability.AVAILABLE, payload={"report": narrative_body}
        )

    monkeypatch.setattr(builder, "_bootstrap_narrative", narrative)
    reports = {role: f"# {role}\n" + f"{role}全文\n" * 1000 for role in ("c1", "c3", "c5")}
    request = Document2PilotCaseRequest(
        source_workspace_run="pilot-run",
        source_global_run_id=source,
        case_id="synthesis",
        node=CodexD2Node.O0_SYNTHESIS,
        document_schema_version=version,
    )
    context = await builder._bootstrap_context(request, repo.get_bundle(source), reports)
    builder._seed_bootstrap_attempt(tmp_path / "staging", request.node, "attempt", context)
    seeded = json.loads(
        (tmp_path / "staging/attempts/attempt/input/context.json").read_text(encoding="utf-8")
    )
    assert seeded["global_research"]["reports"] == reports
    assert seeded["narrative_research"]["payload"]["report"] == narrative_body
    assert seeded["candidate_sets"] == {}


async def setup(tmp_path, **worker_options):
    repository, workspace, source = await _global_fixture(tmp_path)
    assets = tmp_path / "assets"
    shutil.copytree(Path(__file__).parents[1] / "prompts/codex_v2/document2", assets)
    for name in ("open-discovery",):
        (assets / "skills" / f"{name}.md").write_text("Offline fixture asset", encoding="utf-8")
    worker = Worker(workspace, **worker_options)
    events = Events()
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(s.InputAvailability.ABSENT),
        event_library_provider=events,
        asset_root=assets,
    )
    request = s.Document2RunRequest(
        run_id="v21-run",
        source_global_run_id=source,
        as_of=AS_OF,
        document_schema_version="document2.v2.1",
    )
    return repository, workspace, worker, events, orchestrator, request


def test_v21_contracts_and_source_identity():
    result = s.CandidateDiscoveryResultV21(
        candidates=[dict(name="orders", scope="demand", why_material="earnings", ref=[])]
    )
    inputs = _candidate_sets_context({"c1": result, "c3": result})
    assert inputs["c1"]["candidates"][0]["candidate_ref"] == "C1:orders"
    assert inputs["c3"]["candidates"][0]["candidate_ref"] == "C3:orders"
    pilot = _pilot_candidate_sets_context(
        {CodexD2Node.O0_CANDIDATE_C1: result.model_dump()},
        {CodexD2Node.O0_CANDIDATE_C1: "c1"},
        "document2.v2.1",
    )
    assert pilot["c1"] == inputs["c1"]
    synthesized = fallback(s.ShellSynthesisResultV21, {"candidate_sets": {"c1": inputs["c1"]}})
    validate_output(synthesized, {"candidate_sets": {"c1": inputs["c1"]}})
    final = fallback(s.ShellFinalizationResultV21, {"provisional_shells": synthesized.model_dump()})
    assert final.shells[0].boundary == ""
    assert final.shells[0].units[0].horizon == "UNRESOLVED"
    assert final.shells[0].name == "orders"
    forbidden = (
        "proposition",
        "shell_id",
        "expectation_id",
        "core_question",
        "boundary_rule",
        "recognition_criteria",
    )
    schema = json.dumps(s.strict_json_schema(s.ExpectationShellV21.model_json_schema()))
    assert not any(f'"{field}"' in schema for field in forbidden)
    assert (
        s.Document2RunRequest(run_id="old", source_global_run_id="source").document_schema_version
        == "document2.v2"
    )
    assert (
        s.Document2Checkpoint(
            run_id="old", source_global_run_id="source", o0_workspace_run_id="o0"
        ).document_schema_version
        == "document2.v2"
    )


@pytest.mark.parametrize("fault", ["missing", "cycle", "self", "cross-unit", "nonmerge-target"])
def test_selection_structural_errors(fault):
    value = selection()
    if fault == "missing":
        value["selections"].pop()
    elif fault == "cycle":
        for item, target in zip(value["selections"], ("routine", "new buyer"), strict=True):
            item.update(decision="MERGE", merge_into=target)
    elif fault == "self":
        value["selections"][0].update(decision="MERGE", merge_into="new buyer")
    elif fault == "cross-unit":
        value["selections"][0]["unit"] = "other"
    else:
        value["selections"][0]["merge_into"] = "routine"
    with pytest.raises(ValueError, match="selection"):
        validate_output(
            s.OpenDiscoverySelectionV21.model_validate(value), {"open_discovery_scan": scan()}
        )


def research_context():
    return dict(
        turn="FINALIZATION",
        canonical_shell=seed(),
        open_discovery_scan=scan(),
        open_discovery_selection=selection(),
        open_discovery_late_additions=[],
        o0_finalization={"shells": [seed(), seed("adjacent")]},
    )


@pytest.mark.parametrize("destination", [None, "orders", "buyer durability", "buyer mix"])
def test_final_resolution_allows_real_destinations(destination):
    shell = s.ExpectationShellV21.model_validate(seed())
    shell.units[0].realization_factors = [
        s.RealizationFactorV21(
            name="buyer durability",
            mechanism="m",
            current_status="s",
            materiality_context="c",
            scope_boundary="b",
        )
    ]
    shell.units[0].potential_gaps = [
        s.PotentialGapV21(name="buyer mix", why_live="l", revision_logic="r")
    ]
    output = s.ShellResearchTurnResultV21(
        canonical_shell=shell,
        open_discovery_resolution=[
            dict(
                unit="orders",
                candidate="new buyer",
                resolution="impact insufficient" if destination is None else "absorbed",
                destination=destination,
                reason="assessment",
            )
        ],
    )
    validate_output(output, research_context())


@pytest.mark.parametrize(
    "fault", ["missing", "destination", "parameter", "typed-value", "late-turn"]
)
def test_research_integrity_is_not_semantic_gating(fault):
    shell = s.ExpectationShellV21.model_validate(seed())
    output = dict(
        canonical_shell=shell.model_dump(),
        late_additions=[],
        open_discovery_resolution=[
            dict(
                unit="orders",
                candidate="new buyer",
                resolution="absorbed",
                destination="orders",
                reason="same",
            )
        ],
    )
    if fault == "missing":
        output["open_discovery_resolution"] = []
    elif fault == "destination":
        output["open_discovery_resolution"][0]["destination"] = "absent"
    elif fault == "late-turn":
        output["late_additions"] = [
            dict(
                unit="orders",
                name="late",
                discovered_during="STATE",
                change_hypothesis="h",
                reason="r",
            )
        ]
    else:
        output["canonical_shell"]["units"][0]["state"] = dict(
            parameters=[dict(name="p", definition="d", value_type="NUMBER")],
            values=[
                dict(
                    name="v",
                    parameter="missing" if fault == "parameter" else "p",
                    source_role="ACTUAL",
                    value=dict(stage="early"),
                    time_scope="now",
                    as_of="today",
                )
            ],
        )
    with pytest.raises(ValueError):
        validate_output(s.ShellResearchTurnResultV21.model_validate(output), research_context())


@pytest.mark.asyncio
async def test_five_stages_freeze_retry_publish_and_version_binding(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=True)
    bundle = await orch.run(request)
    assert bundle.publication_state == "PARTIAL" and not bundle.current
    counts = Counter(r.node for r in worker.requests)
    assert counts[CodexD2Node.O1_OPEN_DISCOVERY] == 1
    assert worker.scan_commits == 1
    assert counts[CodexD2Node.O1_DISCOVERY_SCAN] == 0
    assert counts[CodexD2Node.O1_DISCOVERY_SELECTION] == 0
    assert counts[CodexD2Node.O1_FINALIZATION] == 1
    cp = bundle.checkpoint
    state = next(iter(cp.shell_runs.values()))
    assert len(state.stage_outputs) == 5
    additions = json.loads(
        (await ws.read_text(request.run_id, state.late_additions_ref.relative_path)).content
    )
    assert len(additions) == 1 and additions[0]["discovered_during"] == "STATE"
    for node, ctx in worker.contexts:
        assert ctx["event_library"]["payload"] == {"events": ["published"]}
        if node.value.startswith("d2_o1"):
            assert ctx["ticker"] == "NVDA" and ctx["o0_finalization"]["shells"]
    doc = s.Document2DocumentV21.model_validate_json(
        (await ws.read_text(request.run_id, bundle.artifacts["document2"].relative_path)).content
    )
    assert doc.shell_outcomes[0].shell == "demand"
    assert doc.shells[0].units[0].expectation_baseline
    unit = doc.shells[0].units[0]
    for obj in (
        doc.shells[0],
        unit,
        *unit.state.parameters,
        *unit.state.values,
        *unit.expectation_baseline,
        *unit.realization_factors,
        *unit.potential_gaps,
    ):
        assert all(ref.startswith("【cite:O") for ref in obj.ref)
    assert "proposition" not in doc.model_dump_json()
    manifest = json.loads(
        (
            await ws.read_text(request.run_id, bundle.artifacts["citation_manifest"].relative_path)
        ).content
    )
    assert not any(e.get("url") == "https://example.com/scan-only" for e in manifest["entries"])
    assert any(e["status"] == "UNRESOLVED" for e in manifest["entries"])
    assert all(
        not ref.published for key, ref in bundle.artifacts.items() if key.startswith("discovery:")
    )
    with pytest.raises(RuntimeError, match="version mismatch"):
        await orch.run(
            request.model_copy(
                update={"document_schema_version": "document2.v2", "force_new": True}
            )
        )
    assert repo.get_bundle(request.run_id).status == "published"
    assert events.calls == 1


@pytest.mark.asyncio
async def test_success_output_replays_after_apply_failure_and_canonical_loss(tmp_path, monkeypatch):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = ws.write_text
    failed = False

    async def write(run, path, content, **kwargs):
        nonlocal failed
        if path == "artifacts/shell.json" and not failed:
            failed = True
            raise OSError("simulated effect interruption")
        return await original(run, path, content, **kwargs)

    monkeypatch.setattr(ws, "write_text", write)
    with pytest.raises(OSError):
        await orch.run(request)
    bundle = await orch.run(request)
    assert bundle.publication_state == "COMPLETE"
    assert Counter(r.node for r in worker.requests)[CodexD2Node.O1_OPEN_DISCOVERY] == 1
    before = len(worker.requests)
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    await original(state.workspace_run_id, "artifacts/shell.json", "broken canonical")
    # Exercise resume rather than the already-published fast path.
    bundle.status = "failed"
    repo.save_bundle(bundle)
    restored = await orch.run(request)
    assert len(worker.requests) == before and events.calls == 1
    shell = s.ExpectationShellV21.model_validate_json(
        (await ws.read_text(state.workspace_run_id, "artifacts/shell.json")).content
    )
    assert shell.units[0].realization_factors
    ref = next(iter(restored.checkpoint.shell_runs.values())).stage_outputs["FINALIZATION"]
    await original(request.run_id, ref.relative_path, "broken success")
    restored.status = "failed"
    repo.save_bundle(restored)
    recovered = await orch.run(request)
    assert recovered.publication_state == "PARTIAL"
    assert len(worker.requests) == before
    assert any("upstream_fallback" in w for w in recovered.checkpoint.warnings)
    assert len(worker.requests) == before


@pytest.mark.asyncio
async def test_success_envelope_replays_without_checkpoint_pointer(tmp_path, monkeypatch):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = ws.write_text

    async def write(run, path, content, **kwargs):
        if path == "artifacts/shell.json" and '"revenue"' in content:
            raise OSError("research canonical apply interrupted")
        return await original(run, path, content, **kwargs)

    monkeypatch.setattr(ws, "write_text", write)
    with pytest.raises(OSError):
        await orch.run(request)
    bundle = repo.get_bundle(request.run_id)
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    assert state.stage == s.ShellResearchStage.OPEN_DISCOVERY
    assert "STATE" in state.stage_outputs
    # Simulate a crash before the success pointer itself reached the checkpoint.
    state.stage_outputs.pop("STATE")
    repo.save_bundle(bundle)
    monkeypatch.setattr(ws, "write_text", original)
    result = await orch.run(request)
    assert result.publication_state == "COMPLETE"
    assert Counter(r.node for r in worker.requests)[CodexD2Node.O1_STATE] == 1


@pytest.mark.asyncio
async def test_missing_discovery_assets_is_system_failure(tmp_path):
    from doxagent.workflows.codex_document2.errors import Document2ExecutionError

    repo, ws, worker, events, orch, request = await setup(tmp_path)
    (tmp_path / "assets/skills/open-discovery.md").unlink()
    with pytest.raises(Document2ExecutionError) as raised:
        await orch.run(request)
    assert raised.value.kind.value == "SYSTEM"
    assert raised.value.code == "D2_DISCOVERY_ASSET_MISSING"
    assert repo.get_bundle(request.run_id).status == "failed"
    assert Counter(r.node for r in worker.requests)[CodexD2Node.O1_OPEN_DISCOVERY] == 0


@pytest.mark.asyncio
async def test_parallel_shell_failure_keeps_healthy_five_stages(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path)

    class TwoShellWorker(Worker):
        active = 0
        peak = 0

        async def _output(self, req):
            if req.node == CodexD2Node.O1_OPEN_DISCOVERY:
                self.active += 1
                self.peak = max(self.peak, self.active)
                await asyncio.sleep(0.02)
            result = await super()._output(req)
            if req.node == CodexD2Node.O0_FINALIZATION:
                result["shells"].append(seed("supply"))
            if req.node == CodexD2Node.O1_OPEN_DISCOVERY:
                self.active -= 1
                if result["selection"]["shell"] == "demand":
                    return {}
            return result

    worker = TwoShellWorker(ws)
    orch._runner._worker.worker = worker
    bundle = await orch.run(request)
    assert worker.peak == 2
    assert bundle.publication_state == "PARTIAL"
    assert [x.status for x in bundle.shell_outcomes] == ["completed", "completed"]
    assert bundle.shell_outcomes[1].shell_id == "supply"
    assert Counter(r.node for r in worker.requests)[CodexD2Node.O1_FINALIZATION] == 2


@pytest.mark.asyncio
async def test_pilot_bootstrap_envelope_and_event_freezing(tmp_path, monkeypatch):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    bundle = await orch.run(request)
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._repository = repo
    builder._source_cache_root = tmp_path / "pilot-sources"
    builder._client = ws
    builder._event_library_provider = Events()
    builder._asset_root = tmp_path / "assets"

    async def narrative(_):
        return s_optional()

    monkeypatch.setattr(builder, "_bootstrap_narrative", narrative)
    upstream = []
    needed = {
        CodexD2Node.O0_FINALIZATION,
        CodexD2Node.O1_OPEN_DISCOVERY,
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
    }
    for ref in repo.list_artifacts(request.run_id):
        if ref.node not in needed or "turns/" not in ref.relative_path:
            continue
        root = tmp_path / "upstream" / ref.node.value
        output = root / "attempts" / ref.attempt_id / "output"
        output.mkdir(parents=True)
        payload = json.loads((await ws.read_text(request.run_id, ref.relative_path)).content)
        if ref.node == CodexD2Node.O1_OPEN_DISCOVERY:
            cp = s.OpenDiscoveryCheckpointV21.model_validate(payload["checkpoint"])
            child = ws.store.root / cp.workspace_run_id
            shutil.copytree(child / "context", root / "context")
            shutil.copytree(child / "attempts" / ref.attempt_id / "input", output.parent / "input")
            payload = dict(scan_sha256=cp.scan_sha256, selection=payload["selection"])
            manifest = dict(
                schema_version="codex-research-pilot-case-v2",
                node=ref.node.value,
                node_attempt_id=ref.attempt_id,
                case_id=root.name,
                run_id=cp.workspace_run_id,
                discovery_contract_version="single-v1",
            )
        else:
            manifest = dict(node=ref.node.value, node_attempt_id=ref.attempt_id)
        (output / "completion.json").write_text(json.dumps(payload), encoding="utf-8")
        (root / "case_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        upstream.append(Document2PilotUpstreamCase(node=ref.node, case_root=root))
    pilot_request = Document2PilotCaseRequest(
        source_workspace_run="pilot-run",
        source_global_run_id=request.source_global_run_id,
        case_id="pilot-case",
        node=CodexD2Node.O1_FINALIZATION,
        document_schema_version="document2.v2.1",
        upstream_cases=tuple(upstream),
        shell_key="demand",
    )
    global_bundle = repo.get_bundle(request.source_global_run_id)
    ctx = await builder._bootstrap_context(
        pilot_request, global_bundle, {"c1": "a", "c3": "b", "c5": "c"}
    )
    assert "canonical_shell" not in ctx["canonical_shell"]
    assert ctx["canonical_shell"]["units"][0]["potential_gaps"]
    assert len(ctx["open_discovery_late_additions"]) == 1
    assert ctx["open_discovery_scan"]["shell"] == "demand"
    assert ctx["open_discovery_selection"]["selections"][0]["decision"] == "DEEPEN"
    assert ctx["open_discovery_resolution"][0]["resolution"] == "under review"
    assert ctx["event_library"]["payload"] == {"events": ["published"]}
    assert ctx["research_cutoff_at"] == AS_OF.isoformat()
    await builder._bootstrap_context(pilot_request, global_bundle, {})
    assert builder._event_library_provider.calls == 1
    builder._seed_bootstrap_attempt(
        tmp_path / "pilot-staging", pilot_request.node, "pilot-attempt", ctx
    )
    shape = json.loads(
        (tmp_path / "pilot-staging/attempts/pilot-attempt/input/output_schema.json").read_text()
    )
    assert "canonical_shell" in shape["properties"]
    assert not bundle.current


@pytest.mark.asyncio
async def test_source_attempt_pilot_preserves_frozen_inputs_and_never_loads_provider(tmp_path):
    from doxagent.settings import DoxAgentSettings

    repo, ws, worker, events, orch, request = await setup(tmp_path)
    bundle = await orch.run(request)
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    ref = state.stage_outputs["STATE"]
    source = tmp_path / "workspaces" / state.workspace_run_id
    staging = tmp_path / "source-pilot"
    shutil.copytree(source, staging)
    input_root = staging / "attempts" / ref.attempt_id / "input"
    original = {
        str(p.relative_to(input_root)): p.read_bytes() for p in input_root.rglob("*") if p.is_file()
    }
    builder = object.__new__(Document2PilotCaseBuilder)
    builder.settings = DoxAgentSettings(codex_capability_secret="offline-test-secret-" * 3)
    builder.python = Path("python.exe").resolve()
    builder.runtime_env_file = tmp_path / "runtime.env"
    builder._event_library_provider = Events()
    builder._materialize(
        staging,
        staging,
        Document2PilotCaseRequest(
            source_workspace_run=state.workspace_run_id,
            case_id="source-pilot",
            node=CodexD2Node.O1_STATE,
            document_schema_version="document2.v2.1",
        ),
        attempt_id=ref.attempt_id,
    )
    assert original == {
        str(p.relative_to(input_root)): p.read_bytes() for p in input_root.rglob("*") if p.is_file()
    }
    manifest = json.loads((staging / "case_manifest.json").read_text())
    assert manifest["document_schema_version"] == "document2.v2.1"
    assert manifest["cutoff_at"] == AS_OF.isoformat()
    assert builder._event_library_provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,output_model",
    [("STATE", s.ShellResearchTurnResultV21), ("OPEN_DISCOVERY", s.OpenDiscoveryResultV21)],
)
async def test_v21_envelope_and_legacy_receipts_decode_without_codec_changes(
    tmp_path, stage, output_model
):
    from doxagent.codex_runtime.schema import CitationManifest
    from doxagent.ticker_initialization.invocation import decode, encode
    from doxagent.ticker_initialization.substeps import _D2Codec
    from doxagent.workflows.codex_document2.runner import Document2TurnResult

    repo, ws, worker, events, orch, request = await setup(tmp_path)
    bundle = await orch.run(request)
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    ref = state.stage_outputs[stage]
    attempt = next(a for a in repo.list_attempts(request.run_id) if a.attempt_id == ref.attempt_id)
    output = output_model.model_validate_json(
        (await ws.read_text(request.run_id, ref.relative_path)).content
    )
    receipt = Document2TurnResult(
        output=output,
        artifact=ref,
        attempt=attempt,
        job=None,
        thread_id=None,
        citation_manifest=CitationManifest(run_id=request.run_id, artifact_id="local"),
        workspace_run_id=state.workspace_run_id,
    )
    codec = _D2Codec(output_model)
    synthesis = next(r for r in worker.requests if r.node == CodexD2Node.O0_SYNTHESIS)
    synthesis_context = json.loads(
        (
            await ws.read_text(
                synthesis.run_id, f"attempts/{synthesis.attempt_id}/input/context.json"
            )
        ).content
    )
    for role, node in (
        ("c1", CodexD2Node.O0_CANDIDATE_C1),
        ("c3", CodexD2Node.O0_CANDIDATE_C3),
        ("c5", CodexD2Node.O0_CANDIDATE_C5),
    ):
        candidate = next(r for r in worker.requests if r.node == node)
        context = json.loads(
            (
                await ws.read_text(
                    candidate.run_id, f"attempts/{candidate.attempt_id}/input/context.json"
                )
            ).content
        )
        assert synthesis_context["global_research"]["reports"][role] == context["primary_source"]
    assert synthesis_context["narrative_research"]["status"] == "ABSENT"
    assert codec.validate_python(codec.dump_python(receipt)).output == output
    assert decode(encode(s.ShellResearchTurnResultV21)) is s.ShellResearchTurnResultV21
    assert decode(encode(s.ExpectationShell)) is s.ExpectationShell
    assert decode(encode(output_model)) is output_model
    assert [r.node for r in worker.requests if r.agent_role == CodexD2AgentRole.O1] == [
        CodexD2Node.O1_OPEN_DISCOVERY,
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]
    assert CodexD2Node("d2_o1_discovery_scan") == CodexD2Node.O1_DISCOVERY_SCAN


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["closure", "destination"])
async def test_final_contract_failure_retries_only_finalization(tmp_path, monkeypatch, fault):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = worker._output

    async def output(req):
        value = await original(req)
        if req.node == CodexD2Node.O1_FINALIZATION:
            if fault == "closure":
                value["open_discovery_resolution"] = []
            else:
                value["open_discovery_resolution"][0]["destination"] = "missing object"
        return value

    monkeypatch.setattr(worker, "_output", output)
    bundle = await orch.run(request)
    counts = Counter(r.node for r in worker.requests)
    assert counts[CodexD2Node.O1_FINALIZATION] == 1
    assert counts[CodexD2Node.O1_OPEN_DISCOVERY] == 1
    assert bundle.shell_outcomes[0].status == "completed"
    assert bundle.publication_state == "PARTIAL"
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    resolution = json.loads(
        (await ws.read_text(request.run_id, state.discovery_resolution_ref.relative_path)).content
    )
    assert resolution  # Finalization no longer erases prior closures.


def test_adjacent_destination_and_structural_correction_remain_allowed():
    context = research_context()
    context["o0_finalization"]["shells"][1]["units"][0]["name"] = "adjacent unit"
    shell = s.ExpectationShellV21.model_validate(seed())
    shell.units[0].name = "restructured orders"
    output = s.ShellResearchTurnResultV21(
        canonical_shell=shell,
        open_discovery_resolution=[
            dict(
                unit="orders",
                candidate="new buyer",
                resolution="owned by adjacent research",
                destination="adjacent unit",
                reason="boundary refinement",
            )
        ],
    )
    validate_output(output, context)
    output.open_discovery_resolution[0].destination = "orders"
    with pytest.raises(ValueError, match="unknown destination"):
        validate_output(output, context)


def test_empty_scan_and_merge_into_park_are_valid():
    empty = s.OpenDiscoveryScanV21(shell="demand", units=[dict(name="orders", candidates=[])])
    validate_output(empty, {"canonical_shell": seed()})
    validate_output(
        s.OpenDiscoverySelectionV21(shell="demand", selections=[]),
        {"open_discovery_scan": empty.model_dump()},
    )
    value = selection()
    value["selections"][0].update(decision="MERGE", merge_into="routine")
    validate_output(
        s.OpenDiscoverySelectionV21.model_validate(value), {"open_discovery_scan": scan()}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_zero_shells_and_failed_scan_are_partial(tmp_path, empty):
    repo, ws, worker, events, orch, request = await setup(
        tmp_path, empty=empty, fail_node=None if empty else CodexD2Node.O1_OPEN_DISCOVERY
    )
    bundle = await orch.run(request)
    assert bundle.publication_state == "PARTIAL" and not bundle.current
    if not empty:
        assert bundle.shell_outcomes[0].status == "completed"
        assert any("discovery_unavailable" in warning for warning in bundle.checkpoint.warnings)


def test_pilot_versioned_dependencies_contracts_and_override(tmp_path):
    request = Document2PilotCoordinatorRequest(
        coordinator_id="v21",
        source_d2_run_id="run",
        source_global_run_id="global",
        document_schema_version="document2.v2.1",
    )
    stages = _build_stages(request)
    nodes = [x["node"] for x in stages]
    assert len(nodes) == 14
    assert nodes[-5:] == [
        n.value
        for n in (
            CodexD2Node.O1_OPEN_DISCOVERY,
            CodexD2Node.O1_STATE,
            CodexD2Node.O1_REALIZATION,
            CodexD2Node.O1_GAPS,
            CodexD2Node.O1_FINALIZATION,
        )
    ]
    assert CodexD2Node.O1_STATE.value in stages[-1]["dependencies"]
    assert (
        _bootstrap_contract(CodexD2Node.O1_STATE, "document2.v2.1")[2]
        is s.ShellResearchTurnResultV21
    )
    assert (
        _bootstrap_contract(CodexD2Node.O1_OPEN_DISCOVERY, "document2.v2.1")[2]
        is s.OpenDiscoveryCompletionV21
    )
    root = tmp_path / "coordinator"
    source = tmp_path / "seeds.json"
    source.write_text(
        s.ShellFinalizationResultV21(shells=[seed()]).model_dump_json(), encoding="utf-8"
    )
    override = _materialize_finalization_override(root, source, "document2.v2.1")
    assert override["shell_ids"] == ["demand"]
    assert _select_bootstrap_shell({"shells": [seed()]}, "demand")["name"] == "demand"
    assert (
        Document2PilotCaseRequest(
            source_workspace_run="run", node=CodexD2Node.O1_STATE, case_id="case"
        ).document_schema_version
        == "document2.v2"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "published"])
async def test_old_run_version_cannot_be_overwritten(tmp_path, status):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    prior = s.Document2Bundle(
        run_id=request.run_id,
        ticker="NVDA",
        source_global_run_id=request.source_global_run_id,
        status=status,
        publication_state="PARTIAL" if status == "published" else None,
        checkpoint=s.Document2Checkpoint(
            run_id=request.run_id,
            source_global_run_id=request.source_global_run_id,
            o0_workspace_run_id="old-o0",
        ),
    )
    repo.save_bundle(prior)
    with pytest.raises(RuntimeError, match="version mismatch"):
        await orch.run(
            request.model_copy(update={"reuse_published_partial": True, "force_new": True})
        )
    assert repo.get_bundle(request.run_id) == prior
    assert not worker.requests


@pytest.mark.asyncio
async def test_failed_input_preparation_keeps_v21_binding_for_resume(tmp_path, monkeypatch):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = orch._inputs.load

    async def fail(**kwargs):
        raise OSError("input service unavailable")

    monkeypatch.setattr(orch._inputs, "load", fail)
    with pytest.raises(OSError):
        await orch.run(request)
    assert repo.get_bundle(request.run_id).checkpoint.document_schema_version == "document2.v2.1"
    monkeypatch.setattr(orch._inputs, "load", original)
    assert (await orch.run(request)).publication_state == "COMPLETE"


@pytest.mark.asyncio
async def test_pinned_run_identity_preserves_v2_and_distinguishes_v21_cutoff():
    import hashlib
    from datetime import timedelta

    from doxagent.workflows.codex_document2.pinned_runner import PinnedDocument2Runner

    calls = []

    class Orchestrator:
        async def run(self, request):
            calls.append(request)
            return s.Document2Bundle(
                run_id=request.run_id,
                ticker=request.ticker,
                source_global_run_id=request.source_global_run_id,
                status="published",
            )

    launcher = PinnedDocument2Runner(reader=object(), orchestrator_factory=lambda _: Orchestrator())
    args = dict(
        source_global_run_id="global",
        ticker="MU",
        as_of=AS_OF,
        event_library_version=3,
        event_library_sha256="event-hash",
        event_library_published_at=AS_OF,
    )
    old = await launcher.run_pinned(**args)
    assert old == "document2-pinned-" + hashlib.sha256(b"global|MU|3|event-hash").hexdigest()[:20]
    first = await launcher.run_pinned(**args, document_schema_version="document2.v2.1")
    args["as_of"] += timedelta(days=1)
    second = await launcher.run_pinned(**args, document_schema_version="document2.v2.1")
    assert old != first != second
    assert calls[0].document_schema_version == "document2.v2"
    assert calls[1].document_schema_version == "document2.v2.1"
