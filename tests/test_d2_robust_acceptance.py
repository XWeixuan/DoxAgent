"""Badcase-driven acceptance, bounded optional inputs and real frozen CLI transport."""

import asyncio
import copy
import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.workflows.codex_document2 import schema as s
from doxagent.workflows.codex_document2.acceptance import accept, accept_discovery, metadata_path
from doxagent.workflows.codex_document2.inputs import OptionalInput, _safe_optional_load
from tests.test_codex_document2_v21_orchestration import scan, seed, selection, setup
from tests.test_d2_discovery_checkpoint import local_service


def research_context():
    shell = s.ExpectationShellV21.model_validate(seed()).model_dump(mode="json")
    addition = dict(
        unit="orders",
        name="new",
        discovered_during="STATE",
        change_hypothesis="growth",
        reason="live",
        ref=[],
    )
    resolution = dict(
        unit="orders",
        candidate="new",
        resolution="already evaluated",
        destination="orders",
        reason="prior",
    )
    return dict(
        canonical_shell=shell,
        turn="REALIZATION",
        open_discovery_scan=scan(),
        open_discovery_selection=selection(),
        open_discovery_late_additions=[addition],
        open_discovery_resolution=[resolution],
    )


def test_historical_addition_finalization_and_idempotent_normalization():
    context = research_context()
    raw = dict(
        canonical_shell=context["canonical_shell"],
        late_additions=context["open_discovery_late_additions"],
        open_discovery_resolution=[],
    )
    accepted = accept(s.ShellResearchTurnResultV21, context, file_text=json.dumps(raw))
    assert accepted.output.canonical_shell.model_dump(mode="json") == context["canonical_shell"]
    assert accepted.output.late_additions[0].discovered_during == "STATE"
    assert accepted.output.open_discovery_resolution[0].candidate == "new"
    context["turn"] = "FINALIZATION"
    final = accept(
        s.ShellResearchTurnResultV21, context, file_text=accepted.output.model_dump_json()
    )
    assert any(d["code"] == "resolution_unresolved" for d in final.diagnostics)
    again = accept(s.ShellResearchTurnResultV21, context, file_text=final.output.model_dump_json())
    assert final.output == again.output and final.diagnostics == again.diagnostics


def test_bad_value_isolated_without_losing_unit_and_conflict_original_preserved():
    context = research_context()
    raw = dict(canonical_shell=copy.deepcopy(context["canonical_shell"]))
    unit = raw["canonical_shell"]["units"][0]
    unit["state"] = dict(
        parameters=[dict(name="sales", definition="sales", value_type="NUMBER")],
        values=[
            dict(
                name="wrong",
                parameter="unknown",
                source_role="ACTUAL",
                value=dict(number=5, unit="USD"),
                time_scope="FY27",
                as_of="2026-08-20",
            )
        ],
    )
    raw["late_additions"] = [
        *context["open_discovery_late_additions"],
        {**context["open_discovery_late_additions"][0], "reason": "updated"},
    ]
    accepted = accept(s.ShellResearchTurnResultV21, context, file_text=json.dumps(raw))
    assert accepted.output.canonical_shell.units[0].state.values == []
    assert accepted.output.late_additions[0].reason == "updated"
    assert {d["code"] for d in accepted.diagnostics} >= {"value_isolated", "conflicting_duplicate"}
    assert (
        next(d for d in accepted.diagnostics if d["code"] == "value_isolated")["original"]["value"][
            "number"
        ]
        == 5
    )


def test_malformed_item_salvaged_and_no_output_uses_upstream():
    raw = {
        "candidates": [
            {"name": "bad"},
            {"name": "valid", "scope": "scope", "why_material": "material"},
        ]
    }
    accepted = accept(s.CandidateDiscoveryResultV21, {}, file_text=json.dumps(raw))
    assert [c.name for c in accepted.output.candidates] == ["valid"]
    assert any(d["code"] == "invalid_item_isolated" for d in accepted.diagnostics)
    context = research_context()
    fallback = accept(
        s.ShellResearchTurnResultV21, context, sdk_reply='{"completion_path":"outside"}'
    )
    assert fallback.source == "upstream" and fallback.output.canonical_shell.name == "demand"


def test_selection_cycle_becomes_pending_and_hash_reply_repaired(tmp_path):
    service = local_service(tmp_path)
    frozen = service.commit(scan())
    chosen = selection()
    for item, other in zip(chosen["selections"], reversed(chosen["selections"]), strict=True):
        item["decision"], item["merge_into"] = "MERGE", other["candidate"]
    accepted = accept_discovery(
        service.load(),
        service.context,
        file_text=json.dumps(dict(scan_sha256="0" * 64, selection=chosen)),
    )
    assert accepted.output.selection.selections == []
    assert accepted.output.checkpoint.scan_sha256 == frozen["scan_sha256"]
    assert {d["code"] for d in accepted.diagnostics} >= {
        "selection_pending",
        "completion_binding_repaired",
    }


def test_local_commit_cli_freezes_real_scan(tmp_path):
    service = local_service(tmp_path)
    source = service.root / "scan.json"
    source.write_text(json.dumps(scan()), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "doxagent.workflows.codex_document2.discovery_checkpoint",
            "commit",
            "--scan-file",
            str(source),
            "--run-id",
            "run-1",
            "--attempt-id",
            "attempt-1",
        ],
        cwd=service.root,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert json.loads(run.stdout)["scan_sha256"] == service.load().scan_sha256


@pytest.mark.asyncio
async def test_formal_files_win_even_when_sdk_fails_and_transport_is_small(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = worker.run

    async def file_delivery(req):
        job = await original(req)
        if req.node.value.startswith("d2_o1_") and req.node.value != "d2_o1_open_discovery":
            await ws.write_text(
                req.run_id, f"attempts/{req.attempt_id}/output/completion.json", job.final_response
            )
            job = job.model_copy(
                update=dict(status="failed", final_response="{}", error_message="reply failed")
            )
        return job

    worker.run = file_delivery
    bundle = await orch.run(request)
    assert bundle.status == "published" and bundle.publication_state == "PARTIAL"
    assert all(outcome.status == "completed" for outcome in bundle.shell_outcomes)
    assert all(
        set(req.output_schema["properties"]) == {"completion_path", "status"}
        for req in worker.requests
    )
    for ref in repo.list_artifacts(request.run_id):
        if ref.node.value == "d2_o1_finalization" and "/turns/" in ref.relative_path:
            metadata = json.loads(
                (await ws.read_text(request.run_id, metadata_path(ref.relative_path))).content
            )
            assert metadata["source"] == "file"
            assert {d["code"] for d in metadata["diagnostics"]} >= {
                "sdk_reply_mismatch",
                "worker_delivery_error",
            }


@pytest.mark.asyncio
async def test_optional_budget_and_finite_transient_retry():
    class Hanging:
        async def load(self, **kwargs):
            await asyncio.sleep(10)

    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await _safe_optional_load(
        Hanging(), ticker="MU", as_of=None, label="test", budget_seconds=0.02
    )
    assert loop.time() - start < 0.2 and result.status == s.InputAvailability.UNAVAILABLE

    class Recovers:
        calls = 0

        async def load(self, **kwargs):
            self.calls += 1
            return (
                OptionalInput(status=s.InputAvailability.UNAVAILABLE, warning="SSL EOF")
                if self.calls == 1
                else OptionalInput(status=s.InputAvailability.AVAILABLE, payload="report")
            )

    provider = Recovers()
    result = await _safe_optional_load(provider, ticker="MU", as_of=None, label="test")
    assert provider.calls == 2 and result.status == s.InputAvailability.AVAILABLE


@pytest.mark.asyncio
async def test_optional_negative_cache_recovers_and_valid_cache_is_reused(tmp_path):
    from doxagent.pilot.document2_case_builder import Document2PilotCaseBuilder

    cutoff = datetime(2026, 10, 7, tzinfo=UTC)
    bundle = SimpleNamespace(ticker="MU", run_id="g1", published_at=cutoff)
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._source_cache_root = tmp_path
    builder.settings = SimpleNamespace(doxatlas_tool_base_url="test")
    cache = tmp_path / "g1" / "narrative.json"
    cache.parent.mkdir()
    cache.write_text(
        OptionalInput(status=s.InputAvailability.UNAVAILABLE).model_dump_json(), encoding="utf-8"
    )

    class Provider:
        calls = 0

        async def load(self, **kwargs):
            self.calls += 1
            return OptionalInput(
                status=s.InputAvailability.AVAILABLE, as_of=cutoff, payload="完整研究"
            )

    provider = Provider()
    first = await builder._cached_optional(bundle, "narrative", provider)
    second = await builder._cached_optional(bundle, "narrative", provider)
    assert first.payload == second.payload == "完整研究" and provider.calls == 1
    assert second.metadata["reused_from"] == str(cache)
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["payload"] = "corrupted"
    cache.write_text(json.dumps(raw), encoding="utf-8")
    third = await builder._cached_optional(bundle, "narrative", provider)
    assert third.payload == "完整研究" and provider.calls == 2


def test_pilot_event_library_uses_published_reader_with_pinned_version(tmp_path):
    from doxagent.pilot.document2_case_builder import Document2PilotCaseBuilder
    from doxagent.settings import DoxAgentSettings
    from doxagent.workflows.codex_document2.inputs import PublishedEventLibraryProvider

    builder = Document2PilotCaseBuilder(
        repo_root=tmp_path,
        cases_root=tmp_path,
        python=sys.executable,
        runtime_env_file=tmp_path / "runtime.env",
        settings=DoxAgentSettings(event_library_root=str(tmp_path / "events")),
        event_library_version=7,
    )
    assert isinstance(builder._event_library_provider, PublishedEventLibraryProvider)
    assert builder._event_library_provider._pinned_version == 7


def test_optional_http_budget_reaches_actual_client():
    from doxagent.tools.providers.doxatlas import DoxAtlasToolClient

    calls = []
    provider = object.__new__(DoxAtlasToolClient)
    provider.client = SimpleNamespace(
        post=lambda url, **kwargs: (
            calls.append(kwargs) or SimpleNamespace(status_code=200, json=lambda: {"ok": True})
        )
    )
    provider._post_doxatlas_json(
        url="https://test.invalid", json_body={}, headers={}, cache_ttl=None, timeout_seconds=0.75
    )
    assert calls[0]["timeout"] == 0.75


@pytest.mark.asyncio
async def test_corrupt_report_degrades_only_that_domain(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    bundle = repo.get_bundle(request.source_global_run_id)
    corrupt = bundle.reports["c1"].model_copy(update={"sha256": "0" * 64})
    repo.save_bundle(bundle.model_copy(update={"reports": {**bundle.reports, "c1": corrupt}}))
    prepared = await orch._inputs.load(
        source_global_run_id=request.source_global_run_id,
        requested_ticker=None,
        requested_as_of=request.as_of,
    )
    assert prepared.global_research.reports["c1"] == ""
    assert prepared.global_research.reports["c3"] and prepared.global_research.reports["c5"]
    assert "report_unavailable" in prepared.manifest.global_research.warning
    completed = await orch.run(request)
    assert completed.status == "published" and completed.publication_state == "PARTIAL"
    assert not any(r.node.value == "d2_o0_candidate_c1" for r in worker.requests)
