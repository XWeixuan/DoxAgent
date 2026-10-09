"""Pinned independent D1 product provenance and real D2/D3 context delivery."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
from doxagent.codex_runtime.research_products import (
    PRODUCT_ROLES,
    load_d1_products,
    project_d1_products,
)
from doxagent.codex_runtime.schema import ArtifactRef, CitationEntry, CitationManifest, CodexD2Node
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    Document2PilotCaseRequest,
)
from doxagent.pilot.document3_driver import import_global_files, seed_database
from doxagent.workflows.codex_document2.inputs import Document2InputLoader
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import Document2RunRequest
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.inputs_v21 import InputPreparerV21
from doxagent.workflows.codex_document3.repository import InMemoryDocument3PolicyRepository
from tests.fixtures.codex_document2 import (
    AS_OF,
    _Document2Worker,
    _global_fixture,
    _NarrativeProvider,
)
from tests.test_codex_document2_v21_orchestration import setup


async def add_products(repo, ws, run_id):
    bundle = repo.get_bundle(run_id)
    future = {
        "时间": "2027",
        "未来事项": "future sentinel",
        "与目标公司的关系": "growth",
        "来源": "https://example.com/future",
        "来源发布日期": "2026-10-01",
    }
    relation = {
        "关系主体": "formal sentinel",
        "关系对象": "NVDA",
        "关系类型": "supplier",
        "关系说明": "independent formal scan",
        "关联业务或产品": "chips",
    }
    content = {
        "future_nodes": [future],
        "entity_relations": [relation],
        "entity_network_report": "# Network sentinel\nNetwork only【cite:O7】 https://example.com/O7\n",
    }
    reports = dict(bundle.reports)
    for key, role in PRODUCT_ROLES.items():
        for kind, text, suffix in (
            ("report", content[key] if key == "entity_network_report" else "", "report.md"),
            (
                "structured_completion",
                json.dumps({key: content[key]}, ensure_ascii=False),
                "completion.json",
            ),
        ):
            path = f"attempts/{role}/output/{suffix}"
            meta = await ws.write_text(run_id, path, text)
            ref = ArtifactRef(
                workflow_version=bundle.workflow_version,
                research_lane="global_research",
                artifact_id=f"{role}-{kind}",
                run_id=run_id,
                node=role,
                attempt_id=f"{role}-accepted",
                kind=kind,
                relative_path=path,
                sha256=meta.sha256,
                size_bytes=meta.size_bytes,
                published=kind == "report",
                content_type="text/markdown" if kind == "report" else "application/json",
            )
            repo.save_artifact(ref)
            if kind == "report":
                reports[role] = ref
                repo.save_citation_manifest(
                    CitationManifest(
                        run_id=run_id,
                        artifact_id=ref.artifact_id,
                        entries=[
                            CitationEntry(
                                alias="O7",
                                source_id=f"{role}-source",
                                url=f"https://example.com/{role}",
                                resolved=True,
                            )
                        ],
                    )
                )
            else:
                # Another attempt's valid completion must never become accepted provenance.
                old = ref.model_copy(
                    update={
                        "artifact_id": f"{role}-old",
                        "attempt_id": f"{role}-old",
                        "relative_path": f"old/{role}.json",
                    }
                )
                repo.save_artifact(old)
    updated = type(bundle).model_validate(
        {
            **bundle.model_dump(),
            **content,
            "reports": reports,
            "c4_product_status": {key: "available" for key in PRODUCT_ROLES},
        }
    )
    repo.save_bundle(updated)
    return updated, content


@pytest.mark.asyncio
async def test_accepted_lineage_states_and_d3_stable_views(tmp_path):
    repo, ws, source = await _global_fixture(tmp_path)
    bundle, content = await add_products(repo, ws, source)

    async def read(ref):
        return (await ws.read_text(source, ref.relative_path)).content

    products = await load_d1_products(bundle, repo, read)
    for key, role in PRODUCT_ROLES.items():
        p = products[key]
        assert p["content"] == content[key]
        assert p["state"] == "available" and p["provenance_available"]
        assert len(p["source_files"]) == 2
        assert p["producer_attempt_id"] == f"{role}-accepted"
        assert p["citation_manifest"]["artifact_id"] == f"{role}-report"
        assert all(not path.startswith("old/") for path in p["source_files"])
    legacy = Document3InputPreparer(
        runtime_repository=repo, policy_repository=InMemoryDocument3PolicyRepository()
    )
    prepared = await InputPreparerV21(legacy, ws).prepare(
        ticker="NVDA", as_of=AS_OF, source_global_run_id=source
    )
    root = "context/document3/v21/shared/d1"
    files = prepared["files"]
    assert (
        json.loads(files[f"{root}/c4f_future_nodes.json"])["future_nodes"]
        == content["future_nodes"]
    )
    assert (
        json.loads(files[f"{root}/c4e_formal_scan.json"])["entity_relations"]
        == content["entity_relations"]
    )
    assert (
        json.loads(files[f"{root}/c4e_network_build.json"])["report_markdown"]
        == content["entity_network_report"]
    )
    assert files[f"{root}/c4e_network_build.md"] == content["entity_network_report"]
    manifest = json.loads(files[f"{root}/products_manifest.json"])
    assert (
        manifest["entity_network_report"]["source_sha256"]
        == hashlib.sha256(content["entity_network_report"].encode()).hexdigest()
    )
    assert manifest["entity_network_report"]["citation_manifest_path"] in files
    assert prepared["topology"]["shell_owners"] == []
    assert prepared["topology"]["open_owners"] == ["OPEN_RESEARCH", "OPEN_EVENT"]
    assert all(
        m["availability"] == "available"
        for m in prepared["topology"]["owner_profiles"]["OPEN_RESEARCH"]["primary_materials"]
    )

    async def absent(ref):
        raise FileNotFoundError(ref.relative_path)

    missing = await load_d1_products(bundle, repo, absent)
    assert missing["future_nodes"]["content"] == content["future_nodes"]
    assert not missing["future_nodes"]["provenance_available"]
    assert missing["future_nodes"]["source_warnings"]


def test_empty_failed_and_legacy_states_remain_distinct():
    bundle = SimpleNamespace(
        run_id="old",
        reports={},
        future_nodes=[],
        entity_relations=[],
        entity_network_report="",
        c4_product_status={"future_nodes": "empty", "entity_relations": "failed"},
    )
    products = project_d1_products(bundle)
    assert [products[k]["state"] for k in PRODUCT_ROLES] == ["empty", "failed", "unavailable"]
    bundle.c4_product_status = {}
    assert project_d1_products(bundle)["future_nodes"]["state"] == "unrecorded"
    bundle.entity_relations = ["stale"]
    bundle.c4_product_status = {"entity_relations": "failed"}
    assert project_d1_products(bundle)["entity_relations"]["content"] == []


@pytest.mark.asyncio
async def test_d2_actual_all_node_contexts_indices_and_pilot_projection(tmp_path, monkeypatch):
    # Leave room for Windows atomic-write temp suffixes under attempt-local indices.
    tmp_path = tmp_path.parent / "product-context"
    tmp_path.mkdir()
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    bundle, content = await add_products(repo, ws, request.source_global_run_id)
    # Exercise Narrative candidate too; all O0/O1 stages must receive products.
    orch._inputs._narrative = _NarrativeProvider()
    await orch.run(request)
    expected_nodes = {
        n
        for n in CodexD2Node
        if n.value
        in {
            "d2_o0_candidate_c1",
            "d2_o0_candidate_c3",
            "d2_o0_candidate_c5",
            "d2_o0_candidate_narrative",
            "d2_o0_synthesis",
            "d2_o0_review_c1",
            "d2_o0_review_c3",
            "d2_o0_review_c5",
            "d2_o0_finalization",
            "d2_o1_open_discovery",
            "d2_o1_state",
            "d2_o1_realization",
            "d2_o1_gaps",
            "d2_o1_finalization",
        }
    }
    assert expected_nodes <= {node for node, ctx in worker.contexts}
    for _node, ctx in worker.contexts:
        assert ctx["future_nodes"] == content["future_nodes"]
        assert ctx["entity_relations"] == content["entity_relations"]
        assert "【cite:D1-O7】" in ctx["entity_network_report"]
        assert "https://example.com/O7" in ctx["entity_network_report"]
        for key, role in PRODUCT_ROLES.items():
            assert ctx["research_asset_sources"][key]["role"] == role
            assert ctx["d1_product_status"][key] == "available"
    # Check actual persisted navigation, including short Markdown in reports catalog.
    for request_item in worker.requests:
        path = f"attempts/{request_item.attempt_id}/input/task.json"
        task = json.loads((await ws.read_text(request_item.run_id, path)).content)
        index = json.loads(
            (await ws.read_text(request_item.run_id, task["context_reading"]["index_path"])).content
        )
        assert index["groups"]["reports"]["total_pages"] > 0
        root = task["context_reading"]["index_path"].rsplit("/", 1)[0]
        pointer = hashlib.sha256(b"/entity_network_report").hexdigest()[:20]
        entry = json.loads(
            (await ws.read_text(request_item.run_id, f"{root}/pointers/{pointer}.json")).content
        )
        assert entry["role"] == "c4e_network_build"
        text = "".join(
            [
                (await ws.read_text(request_item.run_id, f"{root}/{page}")).content
                for page in entry["pages"]
            ]
        )
        assert text == prepared_network(content["entity_network_report"])

    loader = Document2InputLoader(
        repository=repo, workspace=ws, narrative_provider=_NarrativeProvider()
    )
    prepared = await loader.load(
        source_global_run_id=bundle.run_id, requested_ticker="NVDA", requested_as_of=AS_OF
    )
    assert all(
        ref.artifact_id in prepared.manifest.global_research.artifact_ids
        for role, ref in bundle.reports.items()
    )
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._repository, builder._client = repo, ws
    builder._source_cache_root = tmp_path / "pilot-cache"
    builder._published_storage = None
    builder._event_library_provider = events
    pilot = await builder._bootstrap_context(
        Document2PilotCaseRequest(
            source_workspace_run="test",
            case_id="test",
            node=CodexD2Node.O0_CANDIDATE_C1,
            source_global_run_id=bundle.run_id,
        ),
        bundle,
        prepared.global_research.reports,
    )
    for key in (*PRODUCT_ROLES, "d1_product_status", "d1_product_sources"):
        assert pilot[key] == getattr(prepared.global_research, key)
    network_source = pilot["d1_product_sources"]["entity_network_report"]
    assert (
        network_source["citation_manifest"]["entries"][0]["source_id"] == "c4e_network_build-source"
    )


def prepared_network(text):
    return text.replace("【cite:O7】", "【cite:D1-O7】")


@pytest.mark.asyncio
async def test_legacy_d2_nodes_receive_same_new_products(tmp_path, monkeypatch):
    repo, ws, source = await _global_fixture(tmp_path)
    _, content = await add_products(repo, ws, source)
    worker = _Document2Worker(ws)
    original = worker._output
    contexts = []

    async def capture(request):
        contexts.append(
            (
                request.node,
                json.loads(
                    (
                        await ws.read_text(
                            request.run_id, f"attempts/{request.attempt_id}/input/context.json"
                        )
                    ).content
                ),
            )
        )
        return await original(request)

    monkeypatch.setattr(worker, "_output", capture)
    orch = CodexDocument2Orchestrator(
        worker=worker,
        workspace=ws,
        repository=repo,
        narrative_provider=_NarrativeProvider(),
        max_attempts=1,
    )
    await orch.run(Document2RunRequest(run_id="legacy-products", source_global_run_id=source))
    assert CodexD2Node.O1_FINALIZATION in {n for n, _ in contexts}
    assert CodexD2Node.O0_CANDIDATE_NARRATIVE in {n for n, _ in contexts}
    for _node, context in contexts:
        assert context["future_nodes"] == content["future_nodes"]
        assert context["entity_relations"] == content["entity_relations"]
        assert context["entity_network_report"].startswith("# Network sentinel")
        assert (
            context["research_asset_sources"]["entity_network_report"]["role"]
            == "c4e_network_build"
        )


@pytest.mark.asyncio
async def test_d3_pilot_snapshot_import_retains_all_product_sources_and_citations(tmp_path):
    repo, ws, source = await _global_fixture(tmp_path)
    bundle, _ = await add_products(repo, ws, source)
    upstream = tmp_path / "upstream.db"
    sql = SQLiteCodexRuntimeRepository(str(upstream))
    sql.save_bundle(bundle)
    for ref in repo.list_artifacts(source, limit=500):
        # Unaccepted old source is deliberately absent; it must not replace accepted data.
        sql.save_artifact(ref)
    for role in PRODUCT_ROLES.values():
        ref = bundle.reports[role]
        sql.save_citation_manifest(repo.get_citation_manifest(source, ref.artifact_id))
    pilot_db = tmp_path / "pilot.db"
    seed_database(upstream, pilot_db)
    destination = LocalWorkspaceStore(tmp_path / "pilot-workspaces")
    await import_global_files(
        SimpleNamespace(codex_runtime_sqlite_path=str(pilot_db)),
        SimpleNamespace(local=destination),
        {"source_global_run_id": source},
        ws.store.root,
        None,
    )
    pilot_repo = SQLiteCodexRuntimeRepository(str(pilot_db))
    assert pilot_repo.get_bundle(source).c4_product_status == bundle.c4_product_status
    for role in PRODUCT_ROLES.values():
        report = bundle.reports[role]
        assert destination.read_text(source, report.relative_path).sha256 == report.sha256
        assert (
            pilot_repo.get_citation_manifest(source, report.artifact_id).artifact_id
            == report.artifact_id
        )
        completion = next(
            ref
            for ref in pilot_repo.list_artifacts(source, limit=500)
            if ref.attempt_id == report.attempt_id and ref.kind.value == "structured_completion"
        )
        assert destination.read_text(source, completion.relative_path).sha256 == completion.sha256
