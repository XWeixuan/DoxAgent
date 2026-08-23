from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.codex_runtime.repository import (
    InMemoryCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    CitationEntry,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    GlobalResearchBundle,
    MarketSituationBundle,
    ResearchLane,
    ThreadRecord,
)
from doxagent.dashboard_api import create_app
from doxagent.data_runtime.policy import DataCapabilityCodec
from doxagent.horizontal_collection.registry import collection_target_registry_for_lane
from doxagent.observations.promotion import CitationPromotionService
from doxagent.pilot.case_builder import (
    C4_PRE_SCAN_UPSTREAM_FILE,
    GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES,
    PilotCaseRequest,
    _pilot_identity,
)


class _FakeResearchLaneService:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def start(self, request: Any) -> dict[str, object]:
        self.requests.append(request)
        return {
            "run_id": request.run_id,
            "research_lane": request.research_lane.value,
            "status": "queued",
        }

    async def start_document2(self, request: Any) -> dict[str, object]:
        self.requests.append(request)
        return {
            "run_id": "d2-deterministic",
            "research_lane": "document2",
            "workflow_version": "codex_document2_v1",
            "source_global_run_id": request.source_global_run_id,
            "status": "queued",
        }

    def list_runs(self, **_: object) -> list[dict[str, object]]:
        return []

    def get(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "status": "published"}

    async def cancel(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "status": "cancelled"}

    async def retry(self, run_id: str, request: object | None = None) -> dict[str, object] | None:
        if request is None:
            return None
        return await self.start(request)

    def events(self, run_id: str, after: int, limit: int) -> list[dict[str, object]]:
        return [{"run_id": run_id, "sequence": after + 1}]

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object]:
        return {
            "artifact": {"run_id": run_id, "artifact_id": artifact_id},
            "content": "lane artifact",
            "etag": "b" * 64,
        }


def test_aggregate_citations_are_unique_across_node_attempts() -> None:
    service = CitationPromotionService(InMemoryCodexRuntimeRepository())
    manifests = [
        CitationManifest(
            run_id="global-run",
            artifact_id="c1-report",
            entries=[
                CitationEntry(
                    alias="O1",
                    source_id="source-c1",
                    attempt_id="c1-attempt",
                    resolved=True,
                )
            ],
        ),
        CitationManifest(
            run_id="global-run",
            artifact_id="c3-report",
            entries=[
                CitationEntry(
                    alias="O1",
                    source_id="source-c3",
                    attempt_id="c3-attempt",
                    resolved=True,
                )
            ],
        ),
    ]

    plan = service.plan_aggregate(manifests)

    assert plan.rewrite(attempt_id="c1-attempt", markdown="A【cite:O1】") == (
        "A【cite:O1】"
    )
    assert plan.rewrite(attempt_id="c3-attempt", markdown="B【cite:O1】") == (
        "B【cite:O2】"
    )
    assert [entry.alias for entry in plan.entries] == ["O1", "O2"]
    assert [entry.attempt_id for entry in plan.entries] == [
        "c1-attempt",
        "c3-attempt",
    ]


def test_aggregate_citations_reject_unresolved_entries() -> None:
    service = CitationPromotionService(InMemoryCodexRuntimeRepository())
    manifest = CitationManifest(
        run_id="global-run",
        artifact_id="c3-report",
        entries=[
            CitationEntry(
                alias="O660",
                attempt_id="c3-attempt",
                resolved=False,
                warning="unresolved",
            )
        ],
    )

    with pytest.raises(ValueError, match="c3-attempt/O660"):
        service.plan_aggregate([manifest])


def test_lane_models_reject_mismatched_workflow_identity() -> None:
    with pytest.raises(ValidationError, match="research_lane"):
        ThreadRecord(
            workflow_version="codex_global_research_v1",
            research_lane=ResearchLane.MARKET_SITUATION_RESEARCH,
            ticker="NVDA",
            run_id="run-bad",
            agent_role=CodexAgentRole.C5,
            thread_id="thread-bad",
            model="test",
        )


def test_sqlite_repository_round_trips_and_filters_both_new_bundle_types(
    tmp_path: Path,
) -> None:
    repository = SQLiteCodexRuntimeRepository(tmp_path / "runtime.sqlite3")
    global_bundle = GlobalResearchBundle(
        run_id="global-run",
        ticker="NVDA",
        status="draft",
    )
    market_bundle = MarketSituationBundle(
        run_id="market-run",
        ticker="NVDA",
        status="draft",
    )
    repository.save_bundle(global_bundle)
    repository.save_bundle(market_bundle)

    assert repository.get_bundle("global-run") == global_bundle
    assert repository.get_bundle("market-run") == market_bundle
    assert [
        item.run_id
        for item in repository.list_run_summaries(
            "NVDA", research_lane=ResearchLane.GLOBAL_RESEARCH
        )
    ] == ["global-run"]
    assert [
        item.run_id
        for item in repository.list_run_summaries(
            "NVDA", research_lane=ResearchLane.MARKET_SITUATION_RESEARCH
        )
    ] == ["market-run"]


def test_horizontal_target_registries_are_lane_local() -> None:
    global_ids = {
        item.collection_target_id
        for item in collection_target_registry_for_lane(ResearchLane.GLOBAL_RESEARCH).all()
    }
    market_ids = {
        item.collection_target_id
        for item in collection_target_registry_for_lane(
            ResearchLane.MARKET_SITUATION_RESEARCH
        ).all()
    }
    assert global_ids and all(item.startswith(("c1_", "c3_", "c5_")) for item in global_ids)
    assert market_ids and all(item.startswith(("c2_", "o4_")) for item in market_ids)
    assert global_ids.isdisjoint(market_ids)


def test_new_bundle_manifests_have_exact_lane_nodes_and_canonical_resources() -> None:
    root = Path(__file__).resolve().parents[1]
    global_manifest = json.loads(
        (root / "codex_assets/global_research_v1/bundle_manifest.json").read_text(encoding="utf-8")
    )
    market_manifest = json.loads(
        (root / "codex_assets/market_situation_v1/bundle_manifest.json").read_text(encoding="utf-8")
    )
    assert list(global_manifest["nodes"]) == [
        "c4_pre_scan",
        "c1",
        "c3",
        "c5",
        "c4_enrichment",
    ]
    assert list(market_manifest["nodes"]) == ["c2", "o4"]
    assert "c4_finalization" not in global_manifest["nodes"]
    for manifest in (global_manifest, market_manifest):
        for source in manifest["resource_sources"].values():
            assert (root / source).is_file()


def test_pilot_lane_identity_and_manual_upstream_contracts_are_persistent() -> None:
    workflow, lane, root = _pilot_identity(
        PilotCaseRequest(
            source_run="source",
            node=CodexD1Node.C5,
            case_id="case",
            research_lane=ResearchLane.GLOBAL_RESEARCH,
        )
    )
    assert workflow == "codex_global_research_v1"
    assert lane is ResearchLane.GLOBAL_RESEARCH
    assert root.as_posix() == "codex_assets/global_research_v1"
    assert GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES[CodexD1Node.C1] == (C4_PRE_SCAN_UPSTREAM_FILE,)
    assert GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES[CodexD1Node.C5] == ("c1.md", "c3.md")
    assert CodexD1Node.C2 not in GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES
    assert CodexD1Node.O4 not in GLOBAL_RESEARCH_MANUAL_UPSTREAM_FILES


def test_data_capability_rejects_cross_lane_and_cross_role_claims() -> None:
    codec = DataCapabilityCodec("s" * 32)
    token = codec.issue(
        workflow_version="codex_global_research_v1",
        research_lane=ResearchLane.MARKET_SITUATION_RESEARCH,
        run_id="run-bad",
        node_id=CodexD1Node.C5,
        node_attempt_id="c5-1",
        agent_role=CodexAgentRole.C5,
        ticker="NVDA",
        cutoff_at=datetime(2026, 8, 20, tzinfo=UTC),
        enabled_tool_ids=[],
    )
    with pytest.raises(CapabilityDenied, match="workflow and research lane"):
        DataCapabilityCodec.verify(token, public_key=codec.public_key)

    role_token = codec.issue(
        workflow_version="codex_global_research_v1",
        research_lane=ResearchLane.GLOBAL_RESEARCH,
        run_id="run-role-bad",
        node_id=CodexD1Node.C5,
        node_attempt_id="c5-1",
        agent_role=CodexAgentRole.O4,
        ticker="NVDA",
        cutoff_at=datetime(2026, 8, 20, tzinfo=UTC),
        enabled_tool_ids=[],
    )
    with pytest.raises(CapabilityDenied, match="node and role"):
        DataCapabilityCodec.verify(role_token, public_key=codec.public_key)


def test_research_lane_api_has_independent_authenticated_start_routes() -> None:
    service = _FakeResearchLaneService()
    client = TestClient(
        create_app(
            auth_mode="mock-required",
            codex_research_lane_service=service,  # type: ignore[arg-type]
        )
    )
    headers = {"Authorization": "Bearer local-test"}
    global_payload = {
        "run_id": "global-api",
        "ticker": "NVDA",
        "research_brief": "test",
    }
    market_payload = {
        "run_id": "market-api",
        "ticker": "NVDA",
        "research_brief": "test",
    }

    assert (
        client.post("/api/dashboard/v1/research-runs/global", json=global_payload).status_code
        == 401
    )
    global_started = client.post(
        "/api/dashboard/v1/research-runs/global", headers=headers, json=global_payload
    )
    market_started = client.post(
        "/api/dashboard/v1/research-runs/market-situation",
        headers=headers,
        json=market_payload,
    )
    document2_started = client.post(
        "/api/dashboard/v1/research-runs/document2",
        headers=headers,
        json={"source_global_run_id": "global-api"},
    )
    assert global_started.json()["data"]["research_lane"] == "global_research"
    assert market_started.json()["data"]["research_lane"] == "market_situation_research"
    assert document2_started.json()["data"]["research_lane"] == "document2"
    assert document2_started.json()["data"]["source_global_run_id"] == "global-api"
    assert len(service.requests) == 3
    assert (
        client.post(
            "/api/dashboard/v1/research-runs/global-api/retry",
            headers=headers,
            json={**global_payload, "run_id": "wrong-run"},
        ).status_code
        == 422
    )
