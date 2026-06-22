from doxagent.debug_viewer.query import (
    DebugRunBundle,
    build_agent_metrics_view,
    build_brief_state_view,
)
from doxagent.debug_viewer.server import INDEX_HTML, LANGSMITH_RENDERER_HTML, _write_json
from doxagent.debug_viewer.validators import (
    validate_commit_log_state_mutation_consistency,
    validate_evidence_reference_integrity,
    validate_langsmith_trajectory_tool_boundary,
)
from eval.export_brief_state import _eval_index


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
    assert view["hard_validators"]["summary"]["validator_count"] == 3


def test_debug_viewer_renders_expectation_detail_sections_as_readable_cards() -> None:
    assert "function renderRealizedFacts" in INDEX_HTML
    assert "function renderKeyVariables" in INDEX_HTML
    assert "function renderEventMonitoringDirection" in INDEX_HTML
    assert "function parseLabeledFactText" in INDEX_HTML
    assert "${renderRealizedFacts(exp.realized_facts)}" in INDEX_HTML
    assert "${renderKeyVariables(exp.key_variables)}" in INDEX_HTML
    assert "${renderEventMonitoringDirection(exp.event_monitoring_direction)}" in INDEX_HTML
    assert "Why It Matters" in INDEX_HTML
    assert "Pricing Status" in INDEX_HTML
    assert "Raw fact JSON" in INDEX_HTML
    assert "Raw variable JSON" in INDEX_HTML
    assert "Raw Event Monitoring JSON" in INDEX_HTML


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


def test_eval_index_unwraps_stable_document_records() -> None:
    evidence = {
        "evidence_id": "ev-index",
        "source_type": "agent_output",
        "source_id": "test:index",
        "title": "Index evidence",
        "summary": "Evidence used by eval index test.",
        "confidence": 0.8,
        "citation_scope": "test",
    }
    index = _eval_index(
        brief_state={
            "global_research": {"status": "present", "sections": []},
            "expectation_units": [],
            "latest_checkpoint": {"status": "completed", "next_node": None},
        },
        hard_validators={"status": "passed", "summary": {}, "validators": []},
        stable_documents={
            "known_events": {
                "doc_known": {
                    "document": {
                        "events": [{"event_id": "event_1", "source": evidence}],
                    }
                }
            },
            "monitoring_config": {
                "doc_config": {
                    "document": {
                        "monitoring_items": [{"item_id": "monitor_1"}],
                    }
                }
            },
            "monitoring_policy": {
                "doc_policy": {
                    "document": {
                        "direct_trade_rules": [{"rule_id": "direct_1"}],
                        "push_to_agent_rules": [{"rule_id": "push_1"}],
                        "cache_rules": [{"rule_id": "cache_1"}],
                    }
                }
            },
        },
        checkpoints=[],
        working_memory=[],
        commit_log=[],
        objections=[],
        delegations=[],
        evidence_refs=[evidence],
    )

    assert index["known_events"]["event_count"] == 1
    assert index["monitoring"]["monitoring_item_count"] == 1
    assert index["monitoring"]["direct_trade_rule_count"] == 1
    assert index["monitoring"]["push_to_agent_rule_count"] == 1
    assert index["monitoring"]["cache_rule_count"] == 1


def test_evidence_reference_integrity_validator_flags_missing_and_broken_refs() -> None:
    bundle = _sample_bundle()
    bundle.evidence_refs = []

    result = validate_evidence_reference_integrity(bundle)

    assert result["status"] == "failed"
    codes = {item["code"] for item in result["findings"]}
    assert "evidence_ref_not_hydrated" in codes
    assert "missing_evidence_refs" in codes


def test_trajectory_tool_boundary_validator_flags_forbidden_tools() -> None:
    bundle = _sample_bundle()
    bundle.working_memory.append(
        {
            "entry_id": "wm-A2",
            "author_agent": "A2",
            "content_type": "delegated_retrieval_result",
            "payload": {
                "status": "succeeded",
                "payload": {
                    "runtime": "react",
                    "react_audit": {
                        "tool_counts": {"doxa_get_analysis": 1},
                        "warnings": [],
                        "entries": [{"kind": "action", "step": 1}],
                    },
                },
                "tool_calls": [
                    {
                        "tool_name": "doxa_get_analysis",
                        "status": "succeeded",
                        "output_summary": "not allowed for A2",
                    }
                ],
            },
            "created_at": "2026-06-03T00:00:11Z",
        }
    )

    result = validate_langsmith_trajectory_tool_boundary(bundle)

    assert result["status"] == "failed"
    assert any(item["code"] == "tool_not_allowed_for_agent" for item in result["findings"])


def test_trajectory_validator_allows_failed_tool_call_recovered_by_successful_retry() -> None:
    bundle = _sample_bundle()
    bundle.working_memory.append(
        {
            "entry_id": "wm-A2-retry",
            "author_agent": "A2",
            "content_type": "delegated_retrieval_result",
            "payload": {
                "status": "succeeded",
                "payload": {
                    "runtime": "react",
                    "react_audit": {
                        "tool_counts": {"anysearch.search": 1},
                        "warnings": [],
                        "entries": [{"kind": "action", "step": 1}],
                    },
                },
                "tool_calls": [
                    {
                        "tool_name": "anysearch.search",
                        "status": "failed",
                        "output_summary": "temporary provider error",
                    },
                    {
                        "tool_name": "anysearch.search",
                        "status": "succeeded",
                        "output_summary": "retry found evidence",
                    },
                ],
            },
            "created_at": "2026-06-03T00:00:11Z",
        }
    )

    result = validate_langsmith_trajectory_tool_boundary(bundle)

    assert not any(
        item["code"] == "failed_tool_call_in_successful_agent_result"
        and item["location"] == "working_memory.wm-A2-retry.payload.tool_calls[0]"
        for item in result["findings"]
    )


def test_trajectory_validator_uses_workflow_node_tool_overrides() -> None:
    bundle = _sample_bundle()
    bundle.working_memory.append(
        {
            "entry_id": "wm-A1-construction-review",
            "author_agent": "A1",
            "content_type": "a1_expectation_construction_review",
            "payload": {
                "status": "succeeded",
                "payload": {
                    "runtime": "react",
                    "react_audit": {
                        "tool_counts": {"doxa_get_narrative_report": 1},
                        "warnings": [],
                        "entries": [{"kind": "action", "step": 1}],
                    },
                    "model_audits": [
                        {"metadata": {"workflow_node": "ReviewExpectationConstruction"}}
                    ],
                },
                "tool_calls": [
                    {
                        "tool_name": "doxa_get_narrative_report",
                        "status": "succeeded",
                        "output_summary": "已检索 DoxAtlas 叙事报告。",
                    }
                ],
                "acceptance_audit": {"workflow_node": "ReviewExpectationConstruction"},
            },
            "created_at": "2026-06-03T00:00:11Z",
        }
    )

    result = validate_langsmith_trajectory_tool_boundary(bundle)

    assert not any(
        item["code"] == "tool_not_allowed_for_agent"
        and item["details"].get("agent") == "A1"
        and item["details"].get("tool_name") == "doxa_get_narrative_report"
        for item in result["findings"]
    )


def test_trajectory_validator_allows_declared_tool_recovered_elsewhere_in_run() -> None:
    bundle = _sample_bundle()
    bundle.working_memory.extend(
        [
            {
                "entry_id": "wm-C3-search",
                "author_agent": "C3",
                "content_type": "c3_industry_review",
                "payload": {
                    "status": "succeeded",
                    "payload": {"runtime": "react", "react_audit": {"entries": []}},
                    "tool_calls": [
                        {
                            "tool_name": "tavily.search",
                            "status": "succeeded",
                            "output_summary": "search evidence",
                        }
                    ],
                },
                "created_at": "2026-06-03T00:00:11Z",
            },
            {
                "entry_id": "wm-O1-inherited-evidence",
                "author_agent": "O1",
                "content_type": "objection_resolution_result",
                "payload": {
                    "status": "succeeded",
                    "payload": {"runtime": "react", "react_audit": {"entries": []}},
                    "tool_calls": [],
                    "tool_usage_audit": {
                        "declared_tool_names": ["tavily.search"],
                        "actual_tool_names": [],
                        "unexecuted_declared_tool_names": ["tavily.search"],
                    },
                },
                "created_at": "2026-06-03T00:00:12Z",
            },
        ]
    )

    result = validate_langsmith_trajectory_tool_boundary(bundle)

    assert not any(
        item["code"] == "declared_unexecuted_tool_evidence"
        and item["details"].get("tool_name") == "tavily.search"
        for item in result["findings"]
    )


def test_commit_log_state_mutation_validator_flags_orphan_stable_documents() -> None:
    bundle = _sample_bundle()

    result = validate_commit_log_state_mutation_consistency(bundle)

    assert result["status"] == "failed"
    assert any(
        item["code"] == "stable_document_without_commit_trace"
        and "global_research" in item["location"]
        for item in result["findings"]
    )


def test_hard_validators_fail_empty_or_unclosed_runs_instead_of_vacuous_pass() -> None:
    bundle = DebugRunBundle(
        run={
            "run_id": "run-empty",
            "ticker": "MU",
            "created_by": "SYSTEM",
            "workflow_state": "initialized",
            "created_at": "2026-06-12T00:00:00Z",
            "updated_at": "2026-06-12T00:01:00Z",
        },
        belief_state={"snapshot_id": "belief-empty", "documents": {}, "commit_ids": []},
        checkpoints=[
            {
                "checkpoint_id": "checkpoint-empty",
                "status": "running",
                "next_node": "BuildGlobalResearch",
                "completed_nodes": ["StartTickerInitialization"],
                "checkpoint": {
                    "metadata": {
                        "workflow_agent_idempotency": {
                            "BuildGlobalResearch:C2": {
                                "workflow_node": "BuildGlobalResearch",
                                "agent_name": "C2",
                                "section_key": "macro_report",
                                "status": "running",
                            }
                        }
                    }
                },
                "is_latest": True,
                "created_at": "2026-06-12T00:01:00Z",
            }
        ],
    )

    evidence = validate_evidence_reference_integrity(bundle)
    trajectory = validate_langsmith_trajectory_tool_boundary(bundle)
    commit = validate_commit_log_state_mutation_consistency(bundle)

    assert evidence["status"] == "failed"
    assert {item["code"] for item in evidence["findings"]} == {"no_evidence_scoped_items"}
    trajectory_codes = {item["code"] for item in trajectory["findings"]}
    assert {"workflow_trace_not_completed", "open_agent_dispatch"}.issubset(trajectory_codes)
    assert commit["status"] == "failed"
    assert {item["code"] for item in commit["findings"]} == {"no_state_mutations_to_validate"}


def test_trajectory_validator_accepts_completed_document2_smoke_stop_after() -> None:
    bundle = _sample_bundle()
    bundle.checkpoints = [
        {
            "checkpoint_id": "checkpoint-document2",
            "status": "running",
            "next_node": "GenerateGlobalNarrativeReport",
            "completed_nodes": ["PromoteExpectationToBeliefState"],
            "checkpoint": {
                "metadata": {
                    "document2_smoke_source_run_id": "run-source",
                    "document2_smoke_stop_after": "PromoteExpectationToBeliefState",
                }
            },
            "is_latest": True,
            "created_at": "2026-06-12T00:01:00Z",
        }
    ]

    trajectory = validate_langsmith_trajectory_tool_boundary(bundle)

    assert "workflow_trace_not_completed" not in {
        item["code"] for item in trajectory["findings"]
    }


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
