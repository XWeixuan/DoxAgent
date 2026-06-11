import json
import threading
from typing import Any

from doxagent.agents import ModelGatewayAgentRunner
from doxagent.agents.config import default_agent_registry
from doxagent.agents.runtime.react import ReActHarnessConfig
from doxagent.gateway import (
    MockModelClient,
    ModelAuditSummary,
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ProviderName,
)
from doxagent.models import AgentName, AgentPermissions, ResultStatus, TaskType
from doxagent.prompts import PromptAssembler, PromptInjector
from doxagent.prompts.assembler import CHINESE_OUTPUT_RULES
from doxagent.tools import ToolClient, ToolDescriptor, ToolRegistry, ToolRequest, ToolResult
from doxagent.tools.mock import default_tool_registry
from tests.fixtures.phase1_contracts import agent_task


class RecordingModelClient:
    def __init__(self, structured_sequence: list[dict[str, Any]]) -> None:
        self.structured_sequence = list(structured_sequence)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        structured = self.structured_sequence.pop(0)
        return ModelResponse(
            structured=structured,
            audit=ModelAuditSummary(
                provider=ProviderName.MOCK,
                model=request.model,
                latency_seconds=0,
                metadata=request.metadata,
            ),
        )


def runner_with_sequence(
    structured_sequence: list[dict[str, Any]],
    *,
    tool_registry: ToolRegistry | None = None,
    react_config: ReActHarnessConfig | None = None,
) -> ModelGatewayAgentRunner:
    return ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured_sequence=structured_sequence)),
        tool_registry=tool_registry or default_tool_registry(),
        react_config=react_config,
        tool_mode="mock",
    )


def test_react_is_default_and_accepts_direct_structured_payload() -> None:
    task = agent_task()
    runner = runner_with_sequence([{"summary": "ok"}])

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["runtime"] == "react"
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["react_audit"]["entries"][0]["completion_reason"]


def test_react_unwraps_nested_react_protocol_action() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "react_protocol": {
                    "is_complete": True,
                    "completion_reason": "done",
                    "final_payload": {"summary": "ok"},
                    "tool_calls": [],
                    "delegations": [],
                }
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert result.payload["react_audit"]["entries"][0]["completion_reason"] == "done"


def test_react_unwraps_fenced_nested_react_protocol_text() -> None:
    task = agent_task()
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(
                text=(
                    "```json\n"
                    '{"react_protocol":{"is_complete":true,"completion_reason":"done",'
                    '"final_payload":{"summary":"ok"},"tool_calls":[],"delegations":[]}}\n'
                    "```"
                )
            )
        ),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}


def test_react_accepts_complete_final_payload_with_skill_call_metadata() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": default_agent_registry()
            .get(AgentName.C1_FUNDAMENTAL_RESEARCH)
            .runtime.to_permissions(),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "final with loaded skill reference",
                "skill_calls": [{"skill_id": "financial-statement", "reason": "already used"}],
                "final_payload": {
                    "text": "完整研究正文。",
                    "summary": "完整研究摘要。",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            }
        ],
        react_config=ReActHarnessConfig(max_steps=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["summary"] == "完整研究摘要。"
    assert "financial-statement" in result.payload["react_audit"]["loaded_skill_ids"]


def test_react_retries_once_after_non_json_model_text() -> None:
    task = agent_task()
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(
            MockModelClient(
                text_sequence=[
                    "I need to answer in JSON.",
                    '{"is_complete": true, "completion_reason": "fixed", '
                    '"final_payload": {"summary": "ok"}}',
                ]
            )
        ),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"] == {"summary": "ok"}
    assert any(
        entry["kind"] == "model_format_error"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_requests_include_chinese_output_rules_and_step_metadata() -> None:
    client = RecordingModelClient(
        [
            {"is_complete": False, "tool_calls": [], "delegations": []},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "ok"},
            },
        ]
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(agent_task())

    assert result.status is ResultStatus.SUCCEEDED
    assert [request.metadata["react_step"] for request in client.requests] == ["1", "2"]
    first_request_text = "\n".join(message.content for message in client.requests[0].messages)
    assert CHINESE_OUTPUT_RULES[0] in first_request_text
    assert "JSON key" in first_request_text
    user_payload = json.loads(client.requests[0].messages[-1].content)
    assert "assembled_task_prompt" not in user_payload
    assert "task_spec" not in user_payload
    assert "rules" not in user_payload
    assert "runtime_output_schema" not in user_payload["task"]
    assert user_payload["task"]["input_context"] == {"document_ids": ["global-research-001"]}
    assert "available_tools" in user_payload
    assert "available_skills" in user_payload
    assert user_payload["loaded_skills"] == []
    assert user_payload["tool_call_policy"]["required_tool_names"] == []


def test_prompt_assembler_adds_chinese_output_rules_for_single_shot_paths() -> None:
    registry = default_agent_registry()
    task = agent_task()
    definition = registry.get(task.agent_name)
    injected = PromptInjector().inject(task, definition)

    assembled = PromptAssembler().assemble(
        injected,
        definition,
        injected.prompt_bundle,
        context_snapshot=None,
        tool_results=[],
    )

    assert CHINESE_OUTPUT_RULES[0] in assembled.instructions
    assert "JSON key" not in assembled.user_prompt
    user_payload = json.loads(assembled.user_prompt)
    assert "rules" not in user_payload
    assert user_payload["task_summary"]["input_context"] == {
        "document_ids": ["global-research-001"]
    }


def test_react_loads_external_skill_on_demand() -> None:
    client = RecordingModelClient(
        [
            {
                "is_complete": False,
                "skill_calls": [
                    {
                        "skill_id": "financial-statement",
                        "reason": "Need financial statement analysis standards.",
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Financial quality reviewed.",
                    "summary": "Financial quality reviewed.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(client),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    first_payload = json.loads(client.requests[0].messages[-1].content)
    second_payload = json.loads(client.requests[1].messages[-1].content)
    assert [item["skill_id"] for item in first_payload["available_skills"]] == [
        "financial-statement",
        "valuation-model",
    ]
    assert "Financial Statement Analysis" not in json.dumps(
        first_payload["loaded_skills"],
        ensure_ascii=True,
    )
    assert second_payload["loaded_skills"][0]["skill_id"] == "financial-statement"
    assert "instructions" in second_payload["loaded_skills"][0]
    assert result.payload["skill_ids"] == ["financial-statement"]
    assert result.payload["external_skill_package_ids"] == ["financial-statement"]
    assert any(
        entry["kind"] == "skill_result" and entry["status"] == "loaded"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_does_not_load_same_skill_twice() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {"is_complete": False, "skill_calls": [{"skill_id": "financial-statement"}]},
            {"is_complete": False, "skill_calls": [{"skill_id": "financial-statement"}]},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Done.",
                    "summary": "Done.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["skill_ids"] == ["financial-statement"]
    duplicate_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry["kind"] == "skill_result" and entry["status"] == "duplicate"
    ]
    assert duplicate_entries


def test_react_rejects_unexposed_skill_call() -> None:
    definition = default_agent_registry().get(AgentName.C1_FUNDAMENTAL_RESEARCH)
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.GENERATE_GLOBAL_RESEARCH,
            "required_output_schema": "ResearchSection",
            "permissions": definition.runtime.to_permissions(),
            "run_metadata": agent_task().run_metadata.model_copy(
                update={"workflow_node": "BuildGlobalResearch"}
            ),
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {"is_complete": False, "skill_calls": [{"skill_id": "macro-analysis"}]},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {
                    "text": "Done.",
                    "summary": "Done.",
                    "evidence_refs": [],
                    "author_agent": "C1",
                    "reviewer_agents": [],
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["skill_ids"] == []
    assert any(
        entry["kind"] == "skill_result" and entry["status"] == "rejected"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_retries_after_no_progress_action() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {"is_complete": False, "tool_calls": [], "delegations": []},
            {
                "is_complete": True,
                "completion_reason": "fixed",
                "final_payload": {"summary": "ok"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert any(
        entry["kind"] == "react_no_progress"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_normalizes_report_like_payload_to_research_section() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ResearchSection"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "ticker": "NVDA",
                    "section_key": "fundamental_report",
                    "sections": [{"title": "Growth", "body": "Revenue is rising."}],
                    "unknowns": ["Balance sheet detail missing."],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["author_agent"] == "O1"
    assert "Revenue is rising" in result.payload["structured"]["text"]
    assert "Balance sheet detail missing" in result.payload["structured"]["text"]


def test_react_filters_invalid_research_section_evidence_refs() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ResearchSection"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "text": "ASTS deployment update.",
                    "summary": "Deployment update.",
                    "evidence_refs": ["BlueBird 7 mission"],
                    "author_agent": "narrative_engine",
                    "reviewer_agents": ["analyst"],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["evidence_refs"] == []
    assert result.payload["structured"]["author_agent"] == "O1"
    assert result.payload["structured"]["reviewer_agents"] == []


def test_react_normalizes_expectation_construction_payload_extras() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "expectations": [{"expectation_id": "exp_1", "name": "AI cycle"}],
                    "rationale": "Built from available research.",
                    "notes": [{"extra": "drop me"}],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["proposed_patches"][0]["target"]["expectation_id"] == "exp_1"
    assert structured["evidence_refs"][0]["source_type"] == "agent_output"
    assert "notes" not in structured


def test_react_normalizes_expectation_shell_construction_without_patches() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationShellConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "expectations": [
                        {
                            "expectation_id": "exp_1",
                            "name": "Commercial milestone execution",
                            "direction": "bullish",
                            "market_view": "Market focuses on execution milestones.",
                        }
                    ],
                    "rationale": "Built from DoxAtlas narrative evidence.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["shells"][0]["expectation_id"] == "exp_1"
    assert "proposed_patches" not in structured


def test_react_normalizes_expectation_detail_to_single_patch() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationDetailResult",
            "input_context": {
                **base_task.input_context,
                "expectation_shell": {
                    "expectation_id": "exp_1",
                    "expectation_name": "Commercial milestone execution",
                    "direction": "bullish",
                    "why_it_matters": "It drives valuation.",
                    "market_view": {
                        "text": "Market focuses on execution milestones.",
                        "summary": "Execution milestones drive the view.",
                        "evidence_refs": [],
                        "author_agent": "O1",
                        "reviewer_agents": ["A1"],
                    },
                    "evidence_refs": [],
                    "unknowns": [],
                    "rationale": "Shell rationale.",
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "detailed",
                "final_payload": {
                    "realized_facts_summary": "Known facts are partially priced.",
                    "key_variables": ["Deployment cadence"],
                    "positive_events": ["Deployment milestone confirmed"],
                    "negative_events": ["Deployment delay"],
                    "rationale": "Detail completed.",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["expectation_id"] == "exp_1"
    assert patch["after"]["expectation_name"] == "Commercial milestone execution"
    assert patch["after"]["key_variables"][0]["name"] == "Deployment cadence"


def test_react_synthesizes_expectation_patch_from_global_research_context() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "required_output_schema": "ExpectationConstructionResult",
            "input_context": {
                **base_task.input_context,
                "global_research_context": {
                    "ticker": "ASTS",
                    "sections": {
                        "market_narrative_report": {
                            "summary": "Commercial deployment milestones drive market debate.",
                            "text": "ASTS direct-to-device deployment remains the key variable.",
                        }
                    },
                },
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {"rationale": "Use global research context."},
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["document_type"] == "expectation_unit"
    assert patch["after"]["expectation_name"].endswith("commercialization milestone execution")


def test_react_normalizes_expectation_patch_target_to_document_id() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "proposed_patches": [
                        {
                            "target": {
                                "document_type": "expectation_unit",
                                "ticker": "WRONG",
                                "expectation_id": "target_id",
                                "field_path": "document",
                            },
                            "operation": "create",
                            "after": {
                                "id": "after_id",
                                "name": "Milestone execution",
                                "description": "Milestone execution matters.",
                                "key_variables": ["Launch cadence"],
                                "realized_facts": ["BlueBird launch announced"],
                            },
                            "rationale": "test",
                        }
                    ],
                    "rationale": "test",
                },
            }
        ]
    )

    result = runner.run(task)

    patch = result.payload["structured"]["proposed_patches"][0]
    assert patch["target"]["ticker"] == task.ticker
    assert patch["target"]["expectation_id"] == patch["after"]["expectation_id"]
    assert patch["after"]["key_variables"][0]["name"] == "Launch cadence"
    assert patch["after"]["realized_facts"][0]["description"] == "BlueBird launch announced"


def test_react_normalizes_output_delegations_for_expectation_construction() -> None:
    task = agent_task().model_copy(
        update={"required_output_schema": "ExpectationConstructionResult"},
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "drafted",
                "final_payload": {
                    "expectations": [{"expectation_id": "exp_1", "name": "Milestone"}],
                    "delegations": [
                        {
                            "target_agent": "A2",
                            "task_type": "data_retrieval",
                            "question": "Fetch OHLCV data.",
                            "context_summary": "Need market data.",
                        }
                    ],
                    "rationale": "test",
                },
            }
        ]
    )

    result = runner.run(task)

    delegation = result.payload["structured"]["delegations"][0]
    assert delegation["requester_agent"] == "O1"
    assert delegation["target_agent"] == "A2"
    assert delegation["required_evidence"] == ["market_data"]
    assert delegation["blocking_scope"]["ticker"] == task.ticker


def test_react_normalizes_doxatlas_audit_payload_to_strict_schema() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A1_DOXATLAS_AUDIT,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "DoxAtlasAuditResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "audited",
                "final_payload": {
                    "overall_status": "revise",
                    "summary": "期望壳缺少 proposition 证据，需要 O1 修订。",
                    "findings": [
                        "market_view is not supported by source ids",
                        {
                            "field": "direction",
                            "status": "needs_more_evidence",
                            "reason": "Direction is plausible but not directly traced.",
                        },
                    ],
                    "text": "This report-like field must not survive normalization.",
                    "author_agent": "A1",
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["verdict"] == "needs_revision"
    assert structured["revision_required"] is True
    assert structured["findings"][0]["field_path"] == "document"
    assert structured["findings"][0]["status"] == "unsupported"
    assert structured["findings"][1]["field_path"] == "direction"
    assert "text" not in structured
    assert "summary" not in structured


def test_react_normalizes_expectation_field_review_report_payload_to_schema() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "ExpectationFieldReviewResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": True,
                "completion_reason": "reviewed",
                "final_payload": {
                    "ticker": "ASTS",
                    "review_timestamp": "2026-06-12T10:00:00Z",
                    "overall_assessment": (
                        "Realized facts are plausible, but several fields need direct "
                        "filing or company-source citations before promotion."
                    ),
                    "patches_reviewed": [
                        {
                            "patch_id": "patch_1",
                            "expectation_id": "expectation_1",
                            "issues": [
                                "realized_facts lack direct SEC citation",
                                {
                                    "field_path": "key_variables.current_state",
                                    "status": "needs_more_evidence",
                                    "recommendation": "Replace placeholder variable names.",
                                },
                            ],
                        }
                    ],
                },
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["rationale"].startswith("Realized facts are plausible")
    assert structured["findings"][0]["field_path"] == "expectation_1"
    assert structured["findings"][0]["status"] == "needs_more_evidence"
    assert structured["findings"][1]["field_path"] == "key_variables.current_state"
    assert "ticker" not in structured
    assert "patches_reviewed" not in structured


def test_react_accepts_direct_expectation_field_review_payload_with_delegations_key() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
            "task_type": TaskType.REVIEW_EXPECTATION_FIELD,
            "required_output_schema": "ExpectationFieldReviewResult",
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "ticker": "ASTS",
                "task_type": "ExpectationFieldReviewResult",
                "findings": [
                    {
                        "field_path": "key_variables.current_state",
                        "status": "needs_more_evidence",
                        "rationale": "Industry validation source is missing.",
                        "evidence_refs": [],
                    }
                ],
                "objections": [],
                "delegations": [],
                "unknowns": ["Need third-party launch cadence source."],
                "rationale": "C3 completed the field review.",
            }
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["structured"]["findings"][0]["field_path"] == (
        "key_variables.current_state"
    )
    assert result.payload["react_audit"]["entries"][0]["completion_reason"] == (
        "model returned direct structured payload"
    )


def test_react_normalizes_a2_search_payload_to_delegated_retrieval_result() -> None:
    task = agent_task().model_copy(
        update={
            "agent_name": AgentName.A2_FACT_CHECK,
            "task_type": TaskType.DELEGATED_RETRIEVAL,
            "required_output_schema": "DelegatedRetrievalResult",
            "permissions": AgentPermissions(
                readable_context_scopes=["delegations"],
                writable_targets=[],
                allowed_tools=["anysearch.search"],
            ),
            "input_context": {
                "delegation": {
                    "delegation_id": "delegation_test",
                    "question": "Verify whether Apple published quarterly results.",
                }
            },
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {
                        "tool_name": "anysearch.search",
                        "input": {
                            "query": "Apple investor relations quarterly results",
                            "max_results": 3,
                            "domain": "finance",
                        },
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "verified",
                "final_payload": {
                    "conclusion": "Public company sources support the delegated fact.",
                    "verdict": "confirmed",
                    "basis": "Search found relevant investor-relations evidence.",
                    "queries": ["Apple investor relations quarterly results"],
                    "uncertainties": ["Exact filing detail was not extracted."],
                    "text": "This free-text alias should be normalized, not preserved.",
                },
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    structured = result.payload["structured"]
    assert structured["answer"] == "Public company sources support the delegated fact."
    assert structured["claim_verdict"] == "supported"
    assert structured["delegation_id"] == "delegation_test"
    assert structured["can_complete_delegation"] is True
    assert structured["source_refs"]
    assert structured["query_log"] == ["Apple investor relations quarterly results"]
    assert "text" not in structured


def test_can_switch_to_single_shot_maf_path() -> None:
    task = agent_task().model_copy(
        update={"input_context": {"execution_mode": "single_shot"}},
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "ok"})),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["runtime"] == "maf"
    assert result.payload["execution_mode"] == "single_shot"


def test_caller_planned_tool_execution_remains_available() -> None:
    task = agent_task().model_copy(
        update={
            "input_context": {
                "execution_mode": "caller_planned_tools",
                "tool_requests": [
                    {"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}
                ],
            }
        },
        deep=True,
    )
    runner = ModelGatewayAgentRunner(
        model_gateway=ModelGateway(MockModelClient(structured={"summary": "ok"})),
        tool_registry=default_tool_registry(),
        tool_mode="mock",
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["runtime"] == "maf"
    assert result.payload["execution_mode"] == "caller_planned_tools"
    assert result.tool_calls[0].tool_name == "doxatlas.query"


def test_react_blocks_fourth_call_to_same_tool() -> None:
    task = agent_task()
    tool_action = {
        "is_complete": False,
        "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
    }
    runner = runner_with_sequence(
        [
            tool_action,
            tool_action,
            tool_action,
            tool_action,
            {
                "is_complete": True,
                "completion_reason": "enough",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[-1].status is ResultStatus.FAILED
    assert result.tool_calls[-1].output_summary is not None
    assert "tool_call_limit_exceeded" in result.tool_calls[-1].output_summary
    assert result.payload["react_audit"]["tool_counts"]["doxatlas.query"] == 3


def test_react_warns_on_similar_tool_query() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
            },
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "doxatlas.query", "input": {"query": "AI demand"}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["react_audit"]["warnings"]


def test_react_permission_denial_is_a_tool_result_not_exception() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "market_data.snapshot", "input": {}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[0].tool_name == "market_data.snapshot"
    assert result.tool_calls[0].status is ResultStatus.FAILED


def test_react_required_tool_gap_is_audited_without_blocking_final_payload() -> None:
    base_task = agent_task()
    task = base_task.model_copy(
        update={
            "input_context": {
                **base_task.input_context,
                "required_tool_names": ["market_data.snapshot"],
            }
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [{"tool_name": "market_data.snapshot", "input": {}}],
            },
            {
                "is_complete": True,
                "completion_reason": "done with data gap",
                "final_payload": {"summary": "done", "unknowns": ["market snapshot unavailable"]},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.tool_calls[0].status is ResultStatus.FAILED
    assert any(
        entry["kind"] == "required_tool_gap"
        for entry in result.payload["react_audit"]["entries"]
    )


def test_react_delegation_requires_permission() -> None:
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["doxatlas.query"],
                can_delegate=False,
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "delegations": [
                    {
                        "target_agent": "A2",
                        "question": "check",
                        "context_summary": "ctx",
                    }
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ]
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    delegation_entries = [
        entry
        for entry in result.payload["react_audit"]["entries"]
        if entry["kind"] == "delegation_result"
    ]
    assert delegation_entries[0]["status"] == "failed"


def test_react_full_compaction_uses_model_summary() -> None:
    task = agent_task()
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "doxatlas.query", "input": {"query": "large context"}}
                ],
            },
            {"summary": {"data_retrieved": ["tool result"]}},
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ],
        react_config=ReActHarnessConfig(compaction_token_threshold=1),
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert result.payload["react_audit"]["compacted_summaries"]


class RendezvousToolClient(ToolClient):
    def __init__(self, name: str, own_event: threading.Event, peer_event: threading.Event) -> None:
        self.name = name
        self.own_event = own_event
        self.peer_event = peer_event

    def call(self, request: ToolRequest) -> ToolResult:
        self.own_event.set()
        saw_peer = self.peer_event.wait(timeout=0.5)
        return ToolResult(
            tool_name=self.name,
            status=ResultStatus.SUCCEEDED,
            output={"saw_parallel_peer": saw_peer, "input": request.input},
            output_summary=f"{self.name} saw_peer={saw_peer}",
        )


def test_react_executes_concurrent_safe_tools_in_parallel() -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    registry = ToolRegistry()
    registry.register(
        "first.concurrent",
        RendezvousToolClient("first.concurrent", first_started, second_started),
        descriptor=ToolDescriptor(
            name="first.concurrent",
            description="first",
            concurrent_safe=True,
        ),
    )
    registry.register(
        "second.concurrent",
        RendezvousToolClient("second.concurrent", second_started, first_started),
        descriptor=ToolDescriptor(
            name="second.concurrent",
            description="second",
            concurrent_safe=True,
        ),
    )
    task = agent_task().model_copy(
        update={
            "permissions": AgentPermissions(
                readable_context_scopes=["global_research"],
                writable_targets=["expectation_unit"],
                allowed_tools=["first.concurrent", "second.concurrent"],
            )
        },
        deep=True,
    )
    runner = runner_with_sequence(
        [
            {
                "is_complete": False,
                "tool_calls": [
                    {"tool_name": "first.concurrent", "input": {"query": "a"}},
                    {"tool_name": "second.concurrent", "input": {"query": "b"}},
                ],
            },
            {
                "is_complete": True,
                "completion_reason": "done",
                "final_payload": {"summary": "done"},
            },
        ],
        tool_registry=registry,
    )

    result = runner.run(task)

    assert result.status is ResultStatus.SUCCEEDED
    assert all("saw_peer=True" in call.output_summary for call in result.tool_calls)
