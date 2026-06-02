from doxagent.debug_viewer.query import (
    DebugRunBundle,
    build_agent_metrics_view,
    build_brief_state_view,
)
from doxagent.debug_viewer.server import LANGSMITH_RENDERER_HTML, _write_json


def test_debug_viewer_builds_brief_state_for_documents_one_and_two() -> None:
    bundle = _sample_bundle()

    view = build_brief_state_view(bundle, storage_status={"storage_mode": "postgres"})

    assert view["run"]["ticker"] == "ASTS"
    assert view["global_research"]["status"] == "present"
    sections = {item["field"]: item for item in view["global_research"]["sections"]}
    assert sections["fundamental_report"]["summary"] == "Fundamental summary"
    assert sections["market_narrative_report"]["text"] == "Narrative text"
    assert len(view["expectation_units"]) == 1
    expectation = view["expectation_units"][0]
    assert expectation["expectation_name"] == "ASTS commercialization"
    assert expectation["commit_trace"][0]["rationale"] == "Promote expectation."
    assert expectation["blockers"]["is_blocked"] is True


def test_debug_viewer_agent_metrics_derive_react_counts() -> None:
    bundle = _sample_bundle()

    view = build_agent_metrics_view(bundle, storage_status={"storage_mode": "postgres"})

    metrics = {item["agent"]: item for item in view["agents"]}
    assert metrics["O1"]["agent_loops"] == 2
    assert metrics["O1"]["tool_counts"] == {"doxa_get_narrative_report": 1}
    assert metrics["O1"]["react_delegations"] == 1
    assert metrics["O1"]["blackboard_delegations_requested"] == 1
    assert metrics["A1"]["blackboard_objections"] == 1
    assert view["totals"]["agent_loops"] == 2
    assert view["totals"]["delegations"] == 1
    assert view["totals"]["objections"] == 1


def test_debug_viewer_sanitizes_secret_like_payloads() -> None:
    bundle = _sample_bundle()

    view = build_brief_state_view(bundle, storage_status={"database_url": "postgresql://secret"})
    rendered = str(view)

    assert "postgresql://secret" not in rendered
    assert "sk-test-secret" not in rendered
    assert "[redacted]" in rendered


def test_debug_viewer_handles_missing_react_audit() -> None:
    bundle = _sample_bundle()
    bundle.working_memory.append(
        {
            "entry_id": "wm-C1",
            "author_agent": "C1",
            "content_type": "agent_result",
            "payload": {"status": "succeeded", "payload": {}, "tool_calls": []},
            "created_at": "2026-06-03T00:00:00Z",
        }
    )

    view = build_agent_metrics_view(bundle)

    metrics = {item["agent"]: item for item in view["agents"]}
    assert metrics["C1"]["audit_status"] == "missing"
    assert metrics["C1"]["agent_loops"] == 0


def test_langsmith_renderer_is_raw_first_and_has_enhanced_sections() -> None:
    assert "Raw-first renderer" in LANGSMITH_RENDERER_HTML
    assert "metadata.inputs" in LANGSMITH_RENDERER_HTML
    assert "outputs / data" in LANGSMITH_RENDERER_HTML
    assert "metadata" in LANGSMITH_RENDERER_HTML
    assert "complete LangSmith postMessage payload" in LANGSMITH_RENDERER_HTML
    assert "complete raw JSON" in LANGSMITH_RENDERER_HTML
    assert "DoxAgent Enhanced Summary" in LANGSMITH_RENDERER_HTML
    assert "Provider Text / Reasoning / Message Content" in LANGSMITH_RENDERER_HTML
    assert 'addEventListener("message"' in LANGSMITH_RENDERER_HTML
    assert "__doxagentRenderLangSmithOutput" in LANGSMITH_RENDERER_HTML
    assert "extractJsonCandidates" in LANGSMITH_RENDERER_HTML
    assert "findAgentResult" in LANGSMITH_RENDERER_HTML
    assert "renderTree" in LANGSMITH_RENDERER_HTML


def test_debug_viewer_ignores_client_disconnect_during_response_write() -> None:
    handler = _DisconnectingHandler()

    _write_json(handler, {"large": "payload"})

    assert handler.status == 200


class _DisconnectingWriter:
    def write(self, _body: bytes) -> None:
        raise ConnectionAbortedError(10053, "client aborted")


class _DisconnectingHandler:
    status: int | None = None

    def __init__(self) -> None:
        self.wfile = _DisconnectingWriter()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, _name: str, _value: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _sample_bundle() -> DebugRunBundle:
    evidence = {
        "evidence_id": "ev-1",
        "source_type": "agent_output",
        "source_id": "react:task-1",
        "title": "Evidence",
        "summary": "Evidence summary",
        "retrieval_metadata": {"api_key": "sk-test-secret"},
        "confidence": 0.8,
        "citation_scope": "expectation_unit",
    }
    expectation = {
        "document_id": "doc-exp",
        "document_type": "expectation_unit",
        "ticker": "ASTS",
        "created_at": "2026-06-03T00:00:00Z",
        "expectation_id": "exp-1",
        "expectation_name": "ASTS commercialization",
        "direction": "bullish",
        "why_it_matters": "Commercial execution drives the thesis.",
        "market_view": {
            "text": "Market view text",
            "summary": "Market view summary",
            "evidence_refs": [evidence],
            "author_agent": "O1",
            "reviewer_agents": [],
        },
        "realized_facts": [],
        "realized_facts_summary": "No realized facts yet.",
        "key_variables": [],
        "event_monitoring_direction": {
            "known_event_notice": "Monitor launches.",
            "positive_events": ["Launch success"],
            "negative_events": ["Launch delay"],
        },
    }
    global_research = {
        "document_id": "doc-global",
        "document_type": "global_research",
        "ticker": "ASTS",
        "created_at": "2026-06-03T00:00:00Z",
        "fundamental_report": _section("Fundamental summary", "Fundamental text", evidence),
        "macro_report": _section("Macro summary", "Macro text", evidence),
        "industry_report": _section("Industry summary", "Industry text", evidence),
        "market_narrative_report": _section("Narrative summary", "Narrative text", evidence),
        "market_trace_report": _section("Trace summary", "Trace text", evidence),
    }
    return DebugRunBundle(
        run={
            "run_id": "run-1",
            "ticker": "ASTS",
            "created_by": "SYSTEM",
            "workflow_state": "completed",
            "created_at": "2026-06-03T00:00:00Z",
            "updated_at": "2026-06-03T00:01:00Z",
        },
        belief_state={
            "snapshot_id": "belief-1",
            "documents": {
                "global_research": {"doc-global": {"document": global_research}},
                "expectation_unit": {"exp-1": {"document": expectation}},
            },
            "commit_ids": ["commit-1"],
        },
        working_memory=[
            {
                "entry_id": "wm-1",
                "author_agent": "O1",
                "content_type": "agent_result",
                "payload": {
                    "status": "succeeded",
                    "payload": {
                        "runtime": "react",
                        "react_audit": {
                            "tool_counts": {"doxa_get_narrative_report": 1},
                            "warnings": ["Similar query detected."],
                            "entries": [
                                {"kind": "action", "step": 1},
                                {"kind": "tool_result", "step": 1},
                                {"kind": "action", "step": 2},
                                {"kind": "delegation_result", "step": 2},
                            ],
                        },
                    },
                    "patch_ids": ["patch-1"],
                    "delegation_ids": ["delegation-1"],
                    "objection_ids": [],
                    "tool_calls": [],
                },
                "created_at": "2026-06-03T00:00:10Z",
            }
        ],
        commit_log=[
            {
                "commit_id": "commit-1",
                "patch": {
                    "patch_id": "patch-1",
                    "target": {
                        "document_type": "expectation_unit",
                        "expectation_id": "exp-1",
                        "field_path": "document",
                    },
                    "rationale": "Promote expectation.",
                    "evidence_refs": [evidence],
                },
                "trigger_reason": "Expectation promotion.",
                "created_at": "2026-06-03T00:00:20Z",
            }
        ],
        objections=[
            {
                "objection_id": "obj-1",
                "source_agent": "A1",
                "status": "open",
                "severity": "blocking",
                "target": {
                    "document_type": "expectation_unit",
                    "expectation_id": "exp-1",
                    "field_path": "document",
                },
                "reason": "Needs source support.",
            }
        ],
        delegations=[
            {
                "delegation_id": "delegation-1",
                "requester_agent": "O1",
                "target_agent": "A2",
                "status": "assigned",
                "blocking_scope": {
                    "document_type": "expectation_unit",
                    "expectation_id": "exp-1",
                    "field_path": "document",
                },
                "question": "Verify claim.",
            }
        ],
        checkpoints=[
            {
                "checkpoint_id": "checkpoint-1",
                "status": "completed",
                "next_node": None,
                "completed_nodes": ["build_global_research"],
                "checkpoint": {"metadata": {}},
                "is_latest": True,
                "created_at": "2026-06-03T00:01:00Z",
            }
        ],
        evidence_refs=[evidence],
    )


def _section(summary: str, text: str, evidence: dict[str, object]) -> dict[str, object]:
    return {
        "summary": summary,
        "text": text,
        "evidence_refs": [evidence],
        "author_agent": "C1",
        "reviewer_agents": [],
    }
