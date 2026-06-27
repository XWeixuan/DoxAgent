import os
import time
from typing import Any, cast

import pytest

from doxagent.agents import AgentRunner, default_agent_registry, default_real_agent_runner
from doxagent.gateway import (
    BailianChatCompletionsModelClient,
    BailianResponsesModelClient,
    ProviderName,
)
from doxagent.models import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    EvidenceRef,
    EvidenceSourceType,
    PatchOperation,
    ResearchSection,
    ResultStatus,
    TaskType,
    ToolCallSummary,
    ValidationStatus,
    new_id,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools import default_real_tool_registry
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InitializationMockResultFactory,
    WorkflowCheckpoint,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowRunStatus,
)
from doxagent.workflows.initialization import _ParallelAgentJob


class StructuredInitializationRunner(AgentRunner):
    def __init__(self, *, include_blockers: bool = True) -> None:
        self.factory = InitializationMockResultFactory(include_blockers=include_blockers)
        self.calls: list[tuple[AgentName, str | None]] = []
        self.tasks: list[AgentTask] = []

    def run(self, task: AgentTask) -> AgentResult:
        self.calls.append((task.agent_name, task.run_metadata.workflow_node))
        self.tasks.append(task)
        node = task.run_metadata.workflow_node
        if node in {
            WorkflowNode.BUILD_GLOBAL_RESEARCH.value,
            WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value,
        }:
            return self._research_section(task)
        return self._structured(task, self.factory(task))

    def _research_section(self, task: AgentTask) -> AgentResult:
        evidence = EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"test:{task.agent_name.value}",
            title=f"{task.agent_name.value} research evidence",
            summary="Structured test research evidence.",
            confidence=0.8,
            citation_scope="test.global_research",
        )
        section = ResearchSection(
            text=f"{task.ticker} {task.agent_name.value} research text.",
            summary=f"{task.ticker} {task.agent_name.value} research summary.",
            evidence_refs=[evidence],
            author_agent=task.agent_name,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload={"structured": section.model_dump(mode="json")},
            evidence_refs=[evidence],
        )

    def _structured(self, task: AgentTask, direct: AgentResult) -> AgentResult:
        if task.required_output_schema == "ExpectationShellConstructionResult":
            structured = dict(direct.payload)
        elif task.required_output_schema == "ExpectationDetailCandidateResult":
            structured = dict(direct.payload)
        elif task.required_output_schema == "Document2ResolutionPlan":
            evidence = EvidenceRef(
                evidence_id=new_id("evidence"),
                source_type=EvidenceSourceType.AGENT_OUTPUT,
                source_id=f"test:{task.run_metadata.workflow_node}:O1",
                title="O1 resolution evidence",
                summary="Structured test resolution evidence.",
                confidence=0.8,
                citation_scope="test.resolution_plan",
            )
            objections = task.input_context.get("unresolved_objections")
            objection_items = [
                item
                for item in objections
                if isinstance(item, dict) and isinstance(item.get("objection_id"), str)
            ] if isinstance(objections, list) else []
            expectation_id = "exp_mock_core"
            if objection_items:
                target = objection_items[0].get("target")
                if isinstance(target, dict) and isinstance(target.get("expectation_id"), str):
                    expectation_id = target["expectation_id"]
            structured = {
                "expectation_id": expectation_id,
                "decision": "resolved",
                "decisions": [
                    {
                        "objection_id": item["objection_id"],
                        "finding_id": None,
                        "decision": "resolved",
                        "resolution_note": (
                            "Mock O1 resolved this objection with supporting evidence."
                        ),
                        "changed_paths": ["expectation_unit.document"],
                        "evidence_refs": [evidence.model_dump(mode="json")],
                    }
                    for item in objection_items
                ],
                "target_finding_ids": [],
                "revised_candidate": None,
                "evidence_requests": [],
                "unresolved_finding_ids": [],
                "unresolved_reason": None,
                "rationale": "Structured resolution-plan test output.",
            }
        elif task.required_output_schema in {
            "ExpectationConstructionResult",
            "ExpectationDetailResult",
        }:
            structured = {
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured expectation construction test output.",
            }
        elif task.required_output_schema == "DoxAtlasAuditResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "unknowns": [],
                "rationale": "Structured audit test output.",
            }
        elif task.required_output_schema == "ExpectationFieldReviewResult":
            structured = {
                "findings": [],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [],
                "delegations": [],
                "unknowns": [],
                "rationale": "Structured expectation-field review test output.",
            }
        elif task.required_output_schema == "DelegatedRetrievalResult":
            structured = direct.payload
        else:
            structured = {
                "payload": direct.payload,
                "proposed_patches": [
                    patch.model_dump(mode="json") for patch in direct.proposed_patches
                ],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in direct.evidence_refs
                ],
                "objections": [
                    objection.model_dump(mode="json") for objection in direct.objections
                ],
                "delegations": [
                    delegation.model_dump(mode="json") for delegation in direct.delegations
                ],
                "tool_calls": [
                    tool_call.model_dump(mode="json") for tool_call in direct.tool_calls
                ],
            }
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=direct.status,
            payload={
                "runtime": "maf",
                "structured": structured,
                "skill_versions": {"doxagent-source-discipline": "1.0.0"},
                "model_audit": {"provider": "mock", "model": "fake"},
            },
        )


class HangingParallelRunner(StructuredInitializationRunner):
    def __init__(self, *, hung_agent: AgentName, sleep_seconds: float = 5.0) -> None:
        super().__init__()
        self.hung_agent = hung_agent
        self.sleep_seconds = sleep_seconds

    def run(self, task: AgentTask) -> AgentResult:
        if task.agent_name is self.hung_agent:
            time.sleep(self.sleep_seconds)
        return super().run(task)


def test_direct_known_events_patch_hydrates_generated_event_source_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run-known-events",
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = AgentResult(
        task_id="task-known-events",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "document_id": "doc_known_events",
                "ticker": "MU",
                "events": [
                    {
                        "event_id": "event_1",
                        "event_time": "2026-06-12T00:00:00Z",
                        "description": "美光披露新的 HBM 进展。",
                        "discussed_by_market": True,
                        "has_price_reaction": True,
                        "is_known_old_news": False,
                    }
                ],
            }
        },
    )

    patched = workflow._ensure_document_patch_result(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        result,
    )

    patch = patched.proposed_patches[0]
    event_source = patch.after["events"][0]["source"]
    assert patch.rationale == (
        "GenerateKnownEvents 已将代理直接产出的稳定文档转换为 Blackboard 补丁。"
    )
    assert patched.evidence_refs
    assert patched.evidence_refs[0].evidence_id == event_source["evidence_id"]
    assert patch.evidence_refs[0].evidence_id == event_source["evidence_id"]


def test_known_events_patch_replaces_run_timestamp_and_infers_market_flags() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run-known-events-time",
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = AgentResult(
        task_id="task-known-events-time",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "document_id": "doc_known_events",
                "ticker": "MU",
                "created_at": "2026-06-14T09:40:13Z",
                "events": [
                    {
                        "event_id": "event_1",
                        "event_time": "2026-06-14T09:40:13Z",
                        "description": (
                            "2026 Q1 DRAM 合约价环比上涨 90%，MU 股价已定价该上行周期。"
                        ),
                        "discussed_by_market": True,
                    }
                ],
            }
        },
    )

    patched = workflow._ensure_document_patch_result(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        result,
    )

    event = patched.proposed_patches[0].after["events"][0]
    assert event["event_time"].startswith("2026-01-01")
    assert event["has_price_reaction"] is True
    assert event["is_known_old_news"] is True


def test_known_events_patch_uses_event_text_and_event_date_fields() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run-known-events-event-text",
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = AgentResult(
        task_id="task-known-events-event-text",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "document_id": "doc_known_events",
                "ticker": "MU",
                "created_at": "2026-06-15T00:00:00Z",
                "events": [
                    {
                        "event_id": "evt_mu_product",
                        "event_date": "2026-06-02",
                        "event_text": "Micron announced a new AI memory product family.",
                        "source_evidence_refs": ["doxa_narrative_report_mu"],
                        "expectation_id": "expectation_mu_001",
                        "has_price_reaction": False,
                        "is_known_old_news": True,
                    }
                ],
            }
        },
    )

    patched = workflow._ensure_document_patch_result(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        result,
    )

    event = patched.proposed_patches[0].after["events"][0]
    assert event["event_time"].startswith("2026-06-02")
    assert event["description"] == (
        "2026-06-02: Micron announced a new AI memory product family."
    )
    assert "source_evidence_refs" not in event["description"]
    assert event["has_price_reaction"] is False
    assert event["is_known_old_news"] is True


def test_known_events_patch_replaces_generic_iso_time_with_precise_text_hint() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    checkpoint = WorkflowCheckpoint(
        run_id="run-known-events-generic-iso-time",
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = AgentResult(
        task_id="task-known-events-generic-iso-time",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "document_id": "doc_known_events",
                "ticker": "MU",
                "created_at": "2026-06-16T00:00:00Z",
                "events": [
                    {
                        "event_id": "evt_q2_fy2026",
                        "event_time": "2026-01-01T00:00:00",
                        "description": "Q2 FY2026 earnings showed HBM demand acceleration.",
                    },
                    {
                        "event_id": "evt_may_2026",
                        "event_time": "2026-01-01T00:00:00",
                        "description": "2026年5月下旬 MU market cap crossed a major threshold.",
                    },
                ],
            }
        },
    )

    patched = workflow._ensure_document_patch_result(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        result,
    )

    events = patched.proposed_patches[0].after["events"]
    assert events[0]["event_time"].startswith("2026-04-01")
    assert events[1]["event_time"].startswith("2026-05-01")


def test_known_event_expectation_id_can_override_weak_model_linkage() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    run = workflow.blackboard.start_run("MU", AgentName.SYSTEM)

    def expectation_doc(
        expectation_id: str,
        name: str,
        direction: str,
        fact: str,
        variable: str,
    ) -> dict[str, object]:
        return {
            "ticker": "MU",
            "created_at": "2026-06-16T00:00:00Z",
            "document_id": f"doc_{expectation_id}",
            "document_type": DocumentType.EXPECTATION_UNIT.value,
            "expectation_id": expectation_id,
            "expectation_name": name,
            "direction": direction,
            "why_it_matters": name,
            "market_view": {
                "text": name,
                "summary": name,
                "evidence_refs": [],
                "author_agent": AgentName.O1_EXPECTATION_OWNER.value,
                "reviewer_agents": [],
            },
            "realized_facts": [
                {
                    "event_id": f"event_{expectation_id}",
                    "description": fact,
                    "evidence_refs": [],
                    "price_reaction": {
                        "price_change": "n/a",
                        "price_pattern": "n/a",
                        "interpretation": "n/a",
                        "evidence_refs": [],
                    },
                }
            ],
            "realized_facts_summary": fact,
            "key_variables": [
                {
                    "variable_id": f"var_{expectation_id}",
                    "name": variable,
                    "current_status": variable,
                    "certainty": "unknown",
                    "evidence_refs": [],
                }
            ],
            "event_monitoring_direction": {
                "known_event_notice": "notice",
                "positive_events": ["positive"],
                "negative_events": ["negative"],
            },
        }

    def mutate(state: Any) -> Any:
        bucket = state.belief_state.documents.setdefault(DocumentType.EXPECTATION_UNIT, {})
        bucket["expectation_mu_hbm_super_cycle"] = {
            "document": expectation_doc(
                "expectation_mu_hbm_super_cycle",
                "HBM structural shortage thesis",
                "bullish",
                "HBM orders remain strong.",
                "HBM order growth",
            )
        }
        bucket["expectation_mu_cycle_reversal_risk"] = {
            "document": expectation_doc(
                "expectation_mu_cycle_reversal_risk",
                "cycle reversal and oversupply risk",
                "risk",
                "DRAM and NAND oversupply can reverse the cycle.",
                "Samsung HBM yield and DRAM NAND oversupply risk",
            )
        }
        return state

    workflow.blackboard.repository.mutate(run.run_id, mutate)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )

    expectation_id = workflow._known_event_expectation_id(
        checkpoint,
        {"expectation_id": "expectation_mu_hbm_super_cycle"},
        (
            "Samsung HBM3E/HBM4 yield breakthrough creates cycle reversal risk and "
            "DRAM/NAND oversupply."
        ),
    )

    assert expectation_id == "expectation_mu_cycle_reversal_risk"


def test_global_research_tool_fragment_section_recovers_chinese_text_and_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    evidence = EvidenceRef(
        evidence_id="evidence_macro",
        source_type=EvidenceSourceType.MARKET_DATA,
        source_id="twelvedata:daily_ohlcv:QQQ",
        title="QQQ market data",
        summary="Daily OHLCV data was retrieved.",
        confidence=0.7,
        citation_scope="daily_ohlcv",
        retrieval_metadata={"tool_name": "twelvedata.daily_ohlcv"},
    )
    checkpoint = WorkflowCheckpoint(
        run_id="run-global-research-fragment",
        ticker="MU",
        next_node=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )
    section = ResearchSection(
        text="symbol: QQQ\noutputsize: 260",
        summary="symbol: QQQ\noutputsize: 260",
        evidence_refs=[],
        author_agent=AgentName.C2_MACRO_RESEARCH,
    )
    result = AgentResult(
        task_id="task_macro",
        agent_name=AgentName.C2_MACRO_RESEARCH,
        status=ResultStatus.SUCCEEDED,
        payload={"structured": section.model_dump(mode="json")},
        evidence_refs=[evidence],
    )

    recovered = workflow._ensure_global_research_section_content(
        checkpoint,
        "macro_report",
        section,
        result,
    )

    assert "宏观" in recovered.text
    assert "工具参数摘要" in recovered.text
    assert "中文审计摘要" in recovered.summary
    assert recovered.evidence_refs == [evidence]


def test_known_events_patch_localizes_external_source_evidence() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    source = EvidenceRef(
        evidence_id="evidence_doxatlas",
        source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        source_id="doxatlas:get-narrative-report:MU",
        title="DoxAtlas narrative report",
        summary="DoxAtlas narrative report was retrieved.",
        retrieval_metadata={"tool_name": "doxa_get_narrative_report"},
        confidence=0.8,
        citation_scope="doxatlas_narrative_report",
    )
    checkpoint = WorkflowCheckpoint(
        run_id="run-known-events-source",
        ticker="MU",
        next_node=WorkflowNode.GENERATE_KNOWN_EVENTS,
    )
    result = AgentResult(
        task_id="task-known-events-source",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "document_id": "doc_known_events",
                "ticker": "MU",
                "events": [
                    {
                        "event_id": "event_1",
                        "event_time": "2026-06-12T00:00:00Z",
                        "description": "美光披露新的 HBM 进展。",
                        "source": source.model_dump(mode="json"),
                        "discussed_by_market": True,
                        "has_price_reaction": True,
                        "is_known_old_news": False,
                    }
                ],
            }
        },
    )

    patched = workflow._ensure_document_patch_result(
        checkpoint,
        WorkflowNode.GENERATE_KNOWN_EVENTS,
        result,
    )

    event_source = patched.proposed_patches[0].after["events"][0]["source"]
    assert event_source["title"] == "DoxAtlas 叙事报告"
    assert event_source["summary"] == "已检索 DoxAtlas 叙事报告。"
    assert patched.evidence_refs[0].summary == "已检索 DoxAtlas 叙事报告。"


def test_patch_submission_hydrates_and_localizes_nested_evidence_refs() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    nested = EvidenceRef(
        evidence_id="evidence_nested",
        source_type=EvidenceSourceType.DOXATLAS_SOURCE,
        source_id="doxatlas:get-narrative-report:MU",
        title="DoxAtlas narrative report",
        summary="DoxAtlas narrative report was retrieved.",
        retrieval_metadata={"tool_name": "doxa_get_narrative_report"},
        confidence=0.8,
        citation_scope="doxatlas_narrative_report",
    )
    patch = BlackboardPatch(
        patch_id="patch_nested",
        target=BlackboardTarget(
            document_type=DocumentType.KNOWN_EVENTS,
            ticker="MU",
            document_id="doc_known_events",
            field_path="document",
        ),
        operation=PatchOperation.CREATE,
        after={"events": [{"event_id": "event_1", "source": nested.model_dump(mode="json")}]},
        rationale="测试嵌套 evidence ref 归一化。",
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        validation_status=ValidationStatus.VALID,
    )

    hydrated = workflow._patch_with_nested_evidence_refs(patch)

    assert [ref.evidence_id for ref in hydrated.evidence_refs] == ["evidence_nested"]
    assert hydrated.evidence_refs[0].summary == "已检索 DoxAtlas 叙事报告。"
    assert hydrated.after["events"][0]["source"]["summary"] == "已检索 DoxAtlas 叙事报告。"


def test_agent_runner_default_uses_bailian_and_real_tools() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="agent_runner",
        settings=DoxAgentSettings(dashscope_api_key="test-key"),
    )

    assert workflow.runner.default_provider is ProviderName.BAILIAN
    assert workflow.runner.default_model == "qwen3.7-plus"
    assert workflow.runner.tool_mode == "real"
    assert "doxa_get_narrative_report" in workflow.runner.tool_registry.names()


def test_default_real_runner_applies_langsmith_env_and_wraps_bailian(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    sdk_clients: list[dict[str, str]] = []

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            sdk_clients.append({"api_key": api_key, "base_url": base_url})

    def fake_wrap_provider_client(
        provider: ProviderName,
        client: object,
        **kwargs: object,
    ) -> object:
        calls.append({"provider": provider, **kwargs})
        return client

    for key in (
        "LANGSMITH_TRACING",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("doxagent.agents.runner.AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr("doxagent.agents.runner.wrap_provider_client", fake_wrap_provider_client)

    runner = default_real_agent_runner(
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            dashscope_fallback_api_key=None,
            langsmith_tracing=True,
            langsmith_endpoint="https://api.smith.langchain.com",
            langsmith_api_key="ls-test",
            langsmith_project="DoxAgent",
        )
    )

    assert runner.default_provider is ProviderName.BAILIAN
    assert len(sdk_clients) == 1
    assert sdk_clients[0]["api_key"] == "test-key"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"
    assert os.environ["LANGSMITH_API_KEY"] == "ls-test"
    assert os.environ["LANGSMITH_PROJECT"] == "DoxAgent"
    assert calls[0]["provider"] is ProviderName.BAILIAN
    assert calls[0]["tracing_enabled"] is True
    assert calls[0]["tracing_extra"] == {
        "metadata": {
            "runtime": "doxagent",
            "provider": "bailian",
            "model": "qwen3.7-plus",
        }
    }


def test_default_real_runner_configures_dashscope_fallback_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_clients: list[dict[str, str]] = []

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            sdk_clients.append({"api_key": api_key, "base_url": base_url})

    monkeypatch.setattr("doxagent.agents.runner.AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        "doxagent.agents.runner.wrap_provider_client",
        lambda _provider, client, **_kwargs: client,
    )

    runner = default_real_agent_runner(
        settings=DoxAgentSettings(
            dashscope_api_key="primary-key",
            dashscope_fallback_api_key="fallback-key",
            dashscope_base_url="https://dashscope.example.test/v1",
            dashscope_enable_thinking=True,
            dashscope_thinking_budget=2000,
        )
    )

    assert [client["api_key"] for client in sdk_clients] == ["primary-key", "fallback-key"]
    assert {client["base_url"] for client in sdk_clients} == {"https://dashscope.example.test/v1"}
    assert len(runner.model_gateway.fallbacks) == 1
    assert runner.default_provider is ProviderName.BAILIAN
    assert runner.default_model == "qwen3.7-plus"
    primary = cast(BailianResponsesModelClient, runner.model_gateway.primary)
    fallback = cast(BailianResponsesModelClient, runner.model_gateway.fallbacks[0])
    assert primary.enable_thinking is True
    assert primary.thinking_budget == 2000
    assert fallback.enable_thinking is True
    assert fallback.thinking_budget == 2000


def test_default_real_runner_uses_dashscope_chat_endpoint_for_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_clients: list[dict[str, str]] = []

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            sdk_clients.append({"api_key": api_key, "base_url": base_url})

    monkeypatch.setattr("doxagent.agents.runner.AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        "doxagent.agents.runner.wrap_provider_client",
        lambda _provider, client, **_kwargs: client,
    )

    runner = default_real_agent_runner(
        settings=DoxAgentSettings(
            dashscope_api_key="primary-key",
            dashscope_fallback_api_key=None,
            dashscope_base_url="https://responses.example.test/v1",
            dashscope_chat_base_url="https://chat.example.test/v1",
            dashscope_model="deepseek-v4-flash",
            dashscope_enable_thinking=True,
            dashscope_thinking_budget=2000,
        )
    )

    assert sdk_clients == [
        {"api_key": "primary-key", "base_url": "https://chat.example.test/v1"}
    ]
    assert runner.default_model == "deepseek-v4-flash"
    primary = cast(BailianChatCompletionsModelClient, runner.model_gateway.primary)
    assert primary.enable_thinking is True
    assert primary.thinking_budget == 2000


def test_default_agent_allowed_tools_exist_in_real_registry() -> None:
    registry = default_agent_registry()
    real_tools = set(default_real_tool_registry(DoxAgentSettings(dashscope_api_key="x")).names())

    missing = {
        f"{agent.value}:{tool_name}"
        for agent in registry.names()
        for tool_name in registry.get(agent).runtime.allowed_tools
        if tool_name not in real_tools
    }

    assert missing == set()


def test_expectation_detail_candidate_acceptance_rejects_identity_changes() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    factory = InitializationMockResultFactory(include_blockers=False)
    shell = factory._expectation_shell("NVDA")
    task = AgentTask.model_validate(
        {
            "task_id": "task_count",
            "ticker": "NVDA",
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": "generate_expectation_detail",
            "input_context": {"expectation_shell": shell.model_dump(mode="json")},
            "required_output_schema": "ExpectationDetailCandidateResult",
            "permissions": default_agent_registry()
            .get(AgentName.O1_EXPECTATION_OWNER)
            .runtime.to_permissions(),
            "run_metadata": {
                "run_id": "run_count",
                "ticker": "NVDA",
                "workflow_node": "GenerateExpectationDetails",
                "created_at": "2026-06-01T00:00:00Z",
            },
        }
    )
    result = factory(task)
    candidate = workflow._expectation_unit_candidate_from_detail_result(
        "NVDA",
        shell,
        result,
    )
    assert candidate.document.expectation_id == shell.expectation_id

    bad_document = candidate.document.model_copy(
        update={"expectation_id": "exp_changed"},
        deep=True,
    )
    bad_payload = result.payload | {"candidate": bad_document.model_dump(mode="json")}
    bad_result = result.model_copy(
        update={"payload": bad_payload},
        deep=True,
    )
    with pytest.raises(Exception, match="changed the construction expectation_id"):
        workflow._expectation_unit_candidate_from_detail_result("NVDA", shell, bad_result)


def test_expectation_detail_candidate_acceptance_defers_quality_findings() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    factory = InitializationMockResultFactory(include_blockers=False)
    shell = factory._expectation_shell("NVDA")
    task = AgentTask.model_validate(
        {
            "task_id": "task_detail_quality",
            "ticker": "NVDA",
            "agent_name": AgentName.O1_EXPECTATION_OWNER,
            "task_type": "generate_expectation_detail",
            "input_context": {"expectation_shell": shell.model_dump(mode="json")},
            "required_output_schema": "ExpectationDetailCandidateResult",
            "permissions": default_agent_registry()
            .get(AgentName.O1_EXPECTATION_OWNER)
            .runtime.to_permissions(),
            "run_metadata": {
                "run_id": "run_detail_quality",
                "ticker": "NVDA",
                "workflow_node": "GenerateExpectationDetails",
                "created_at": "2026-06-01T00:00:00Z",
            },
        }
    )
    result = factory(task)
    candidate = workflow._expectation_unit_candidate_from_detail_result(
        "NVDA",
        shell,
        result,
    )
    quality_gap_document = candidate.document.model_copy(
        update={"realized_facts": [], "key_variables": []},
        deep=True,
    )
    quality_gap_payload = result.payload | {
        "candidate": quality_gap_document.model_dump(mode="json")
    }
    quality_gap_result = result.model_copy(
        update={"payload": quality_gap_payload},
        deep=True,
    )

    accepted = workflow._expectation_unit_candidate_from_detail_result(
        "NVDA",
        shell,
        quality_gap_result,
    )

    assert accepted.document.realized_facts == []
    assert accepted.document.key_variables == []


def test_resolver_o1_has_no_tools_in_effective_permissions() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    permissions = (
        default_agent_registry()
        .get(AgentName.O1_EXPECTATION_OWNER)
        .runtime.to_permissions()
    )

    effective = workflow._effective_permissions(
        permissions,
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        TaskType.REVIEW_EXPECTATION_FIELD,
        AgentName.O1_EXPECTATION_OWNER,
    )

    assert effective.allowed_tools == []


def test_expectation_shell_construction_allows_one_to_three() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    factory = InitializationMockResultFactory(include_blockers=False)
    shell = factory._expectation_shell("NVDA")
    result = AgentResult(
        task_id="task_shell_count",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "shells": [shell.model_dump(mode="json")],
                "evidence_refs": [
                    evidence.model_dump(mode="json") for evidence in shell.evidence_refs
                ],
                "delegations": [],
                "unknowns": [],
                "rationale": "Only one shell.",
            }
        },
        evidence_refs=shell.evidence_refs,
    )

    construction = workflow._validate_expectation_shells("NVDA", result)

    assert len(construction.shells) == 1


def test_agent_runner_workflow_uses_module_integration_for_global_research() -> None:
    runner = StructuredInitializationRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert result.summary.stable_document_types == [DocumentType.GLOBAL_RESEARCH]
    assert result.summary.commit_count == 1
    assert runner.calls == [
        (AgentName.C1_FUNDAMENTAL_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.C2_MACRO_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.C3_INDUSTRY_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
        (AgentName.O4_MARKET_TRACE, WorkflowNode.BUILD_GLOBAL_RESEARCH.value),
    ]
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert len(run.working_memory) == 5
    agent_entries = [
        entry
        for entry in run.working_memory
        if entry.content_type == "global_research_agent_result"
    ]
    assert {entry.author_agent for entry in agent_entries} == {
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
        AgentName.C3_INDUSTRY_RESEARCH,
        AgentName.O4_MARKET_TRACE,
    }
    assembly_entries = [
        entry for entry in run.working_memory if entry.content_type == "global_research_assembly"
    ]
    assert len(assembly_entries) == 1
    assert assembly_entries[0].payload["patch_ids"] == [run.commit_log[0].patch.patch_id]


def test_parallel_agent_jobs_call_outcome_callback() -> None:
    runner = StructuredInitializationRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        completed_nodes=[WorkflowNode.START_TICKER_INITIALIZATION],
        node_statuses={
            WorkflowNode.START_TICKER_INITIALIZATION: WorkflowNodeStatus.COMPLETED,
        },
        next_node=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )
    seen: list[AgentName] = []
    jobs = [
        _ParallelAgentJob(
            order=0,
            agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
            task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
            output_schema="ResearchSection",
            section_key="fundamental_report",
            extra_context={"section_key": "fundamental_report"},
        )
    ]

    outcomes = workflow._run_agent_jobs_concurrently(
        checkpoint,
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        jobs,
        on_outcome=lambda outcome: seen.append(outcome.job.agent_name),
    )

    assert [outcome.job.agent_name for outcome in outcomes] == [
        AgentName.C1_FUNDAMENTAL_RESEARCH
    ]
    assert seen == [AgentName.C1_FUNDAMENTAL_RESEARCH]


def test_parallel_agent_jobs_timeout_hung_worker_without_blocking() -> None:
    runner = HangingParallelRunner(hung_agent=AgentName.C1_FUNDAMENTAL_RESEARCH)
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            storage_mode="memory",
            workflow_agent_stale_after_seconds=1,
        ),
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        completed_nodes=[WorkflowNode.START_TICKER_INITIALIZATION],
        node_statuses={
            WorkflowNode.START_TICKER_INITIALIZATION: WorkflowNodeStatus.COMPLETED,
        },
        next_node=WorkflowNode.BUILD_GLOBAL_RESEARCH,
    )
    jobs = [
        _ParallelAgentJob(
            order=0,
            agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
            task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
            output_schema="ResearchSection",
            section_key="fundamental_report",
            extra_context={"section_key": "fundamental_report"},
        ),
        _ParallelAgentJob(
            order=1,
            agent_name=AgentName.C2_MACRO_RESEARCH,
            task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
            output_schema="ResearchSection",
            section_key="macro_report",
            extra_context={"section_key": "macro_report"},
        ),
    ]
    seen: list[AgentName] = []

    started_at = time.monotonic()
    outcomes = workflow._run_agent_jobs_concurrently(
        checkpoint,
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        jobs,
        on_outcome=lambda outcome: seen.append(outcome.job.agent_name),
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 3
    assert [outcome.job.agent_name for outcome in outcomes] == [
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
    ]
    assert outcomes[0].error is not None
    assert "parallel_agent_timeout" in str(outcomes[0].error)
    assert "fundamental_report" in str(outcomes[0].error)
    assert "order=0" in str(outcomes[0].error)
    assert outcomes[1].result is not None
    assert set(seen) == {
        AgentName.C1_FUNDAMENTAL_RESEARCH,
        AgentName.C2_MACRO_RESEARCH,
    }


def test_agent_runner_recovers_stale_global_research_dispatch_before_retry() -> None:
    runner = StructuredInitializationRunner()
    workflow = BlackboardInitializationWorkflow(
        runner=runner,
        execution_mode="agent_runner",
        settings=DoxAgentSettings(
            dashscope_api_key="test-key",
            workflow_agent_stale_after_seconds=1,
        ),
    )
    run = workflow.blackboard.start_run("NVDA", AgentName.SYSTEM)
    stale_key = workflow._agent_idempotency_key(
        WorkflowNode.BUILD_GLOBAL_RESEARCH,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
    )
    checkpoint = WorkflowCheckpoint(
        run_id=run.run_id,
        ticker="NVDA",
        completed_nodes=[WorkflowNode.START_TICKER_INITIALIZATION],
        node_statuses={
            WorkflowNode.START_TICKER_INITIALIZATION: WorkflowNodeStatus.COMPLETED,
        },
        next_node=WorkflowNode.BUILD_GLOBAL_RESEARCH,
        metadata={
            "ticker_loaded": True,
            "workflow_agent_idempotency": {
                stale_key: {
                    "run_id": run.run_id,
                    "workflow_node": WorkflowNode.BUILD_GLOBAL_RESEARCH.value,
                    "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH.value,
                    "section_key": "fundamental_report",
                    "status": "running",
                    "updated_at": "2000-01-01T00:00:00+00:00",
                }
            },
        },
    )
    workflow.checkpoint_repository.save_checkpoint(checkpoint)

    result = workflow.resume_latest(run.run_id, stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    assert result.status is WorkflowRunStatus.RUNNING
    assert WorkflowNode.BUILD_GLOBAL_RESEARCH in result.checkpoint.completed_nodes
    assert runner.calls.count(
        (AgentName.C1_FUNDAMENTAL_RESEARCH, WorkflowNode.BUILD_GLOBAL_RESEARCH.value)
    ) == 1
    run_after = workflow.blackboard.get_run(run.run_id)
    recovery_entries = [
        entry
        for entry in run_after.working_memory
        if entry.content_type == "agent_dispatch_stale_recovery"
    ]
    assert len(recovery_entries) == 1
    assert recovery_entries[0].author_agent is AgentName.SYSTEM
    assert recovery_entries[0].payload["retry_reason"] == "stale_running_dispatch"
    latest_records = workflow.checkpoint_repository.list_checkpoints(run.run_id)
    assert any(
        record.checkpoint.metadata["workflow_agent_idempotency"][stale_key]["status"] == "failed"
        for record in latest_records
    )


def test_agent_runner_workflow_completes_with_structured_agent_result_json() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(include_blockers=False),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.COMPLETED
    assert result.summary.stable_document_types == [
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    ]
    assert result.summary.commit_count == 7
    assert result.checkpoint.metadata["execution_mode"] == "agent_runner"
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert [
        commit.patch.target.field_path
        for commit in run.commit_log
        if commit.patch.target.document_type is DocumentType.GLOBAL_RESEARCH
    ] == ["document", "document.market_narrative_report"]
    assert set(run.belief_state.documents) == {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
        DocumentType.MONITORING_POLICY,
    }


def test_global_narrative_tool_call_fragment_is_replaced_with_chinese_fallback() -> None:
    class ToolCallNarrativeRunner(StructuredInitializationRunner):
        def _research_section(self, task: AgentTask) -> AgentResult:
            if (
                task.run_metadata.workflow_node
                == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value
            ):
                evidence = EvidenceRef(
                    evidence_id="evidence_narrative",
                    source_type=EvidenceSourceType.DOXATLAS_SOURCE,
                    source_id="doxatlas:get-narrative-report:NVDA",
                    title="DoxAtlas narrative report",
                    summary="DoxAtlas narrative report was retrieved.",
                    retrieval_metadata={"tool_name": "doxa_get_narrative_report"},
                    confidence=0.8,
                    citation_scope="doxatlas_narrative_report",
                )
                tool_text = "name: doxa_get_narrative_report\narguments:\nticker: NVDA"
                section = ResearchSection(
                    text=tool_text,
                    summary=tool_text,
                    evidence_refs=[evidence],
                    author_agent=task.agent_name,
                )
                return AgentResult(
                    task_id=task.task_id,
                    agent_name=task.agent_name,
                    status=ResultStatus.SUCCEEDED,
                    payload={"structured": section.model_dump(mode="json")},
                    evidence_refs=[evidence],
                    tool_calls=[
                        ToolCallSummary(
                            tool_name="doxa_get_narrative_report",
                            status=ResultStatus.SUCCEEDED,
                            input_summary="检索叙事报告",
                            output_summary="已检索 DoxAtlas 叙事报告。",
                            evidence_refs=[evidence],
                        )
                    ],
                )
            return super()._research_section(task)

    workflow = BlackboardInitializationWorkflow(
        runner=ToolCallNarrativeRunner(include_blockers=False),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    global_doc = next(iter(run.belief_state.documents[DocumentType.GLOBAL_RESEARCH].values()))[
        "document"
    ]
    narrative = global_doc["market_narrative_report"]

    assert result.status is WorkflowRunStatus.COMPLETED
    assert "name: doxa_get_narrative_report" not in narrative["text"]
    assert "市场叙事" in narrative["text"]
    assert narrative["evidence_refs"][0]["summary"] == "已检索 DoxAtlas 叙事报告。"


def test_agent_runner_structured_schema_invalid_blocks_without_stable_state() -> None:
    class InvalidStructuredRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.SUCCEEDED,
                payload={"structured": ["bad"]},
            )

    workflow = BlackboardInitializationWorkflow(
        runner=InvalidStructuredRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "structured output" in result.error or "schema validation" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    assert set(run.belief_state.documents) == set()
    assert len(run.commit_log) == 0


def test_unexpected_agent_exception_blocks_and_writes_business_audit() -> None:
    class ExplodingRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            raise RuntimeError(f"boom in {task.run_metadata.workflow_node}")

    workflow = BlackboardInitializationWorkflow(
        runner=ExplodingRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "boom in BuildGlobalResearch" in result.error
    assert result.checkpoint.metadata["last_error_code"] == "RuntimeError"
    assert result.checkpoint.metadata["last_error_boundary"] == "unexpected_exception"
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    exception_entries = [
        entry for entry in run.working_memory if entry.content_type == "workflow_exception"
    ]
    assert len(exception_entries) == 1
    assert exception_entries[0].payload["error_code"] == "RuntimeError"
    assert exception_entries[0].payload["workflow_node"] == WorkflowNode.BUILD_GLOBAL_RESEARCH.value


def test_agent_runner_required_tool_failure_blocks_and_preserves_tool_summary() -> None:
    class RequiredToolFailureRunner(AgentRunner):
        def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                agent_name=task.agent_name,
                status=ResultStatus.FAILED,
                tool_calls=[
                    ToolCallSummary(
                        tool_name="doxatlas.query",
                        status=ResultStatus.FAILED,
                        input_summary="required workflow evidence",
                        output_summary="tool failed",
                    ),
                ],
                error=AgentError(
                    code="required_tool_failed",
                    message="Required tool failed.",
                    retryable=False,
                ),
            )

    workflow = BlackboardInitializationWorkflow(
        runner=RequiredToolFailureRunner(),
        execution_mode="agent_runner",
    )

    result = workflow.run("NVDA")

    assert result.status is WorkflowRunStatus.BLOCKED
    assert result.error is not None
    assert "Required tool failed" in result.error
    run = workflow.blackboard.get_run(result.checkpoint.run_id)
    failed_memory = next(entry for entry in run.working_memory if entry.payload["tool_calls"])
    assert failed_memory.payload["tool_calls"][0]["status"] == "failed"
    assert set(run.belief_state.documents) == set()


def test_agent_runner_resume_latest_after_manual_blocker_resolution() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(include_blockers=True),
        execution_mode="agent_runner",
        auto_resolve_blockers=False,
    )
    blocked = workflow.run("NVDA")
    run = workflow.blackboard.get_run(blocked.checkpoint.run_id)

    workflow.blackboard.resolve_objection(
        blocked.checkpoint.run_id,
        run.objections[0].objection_id,
        "Manual review resolved the objection.",
    )
    workflow.blackboard.complete_delegation(
        blocked.checkpoint.run_id,
        run.delegations[0].delegation_id,
        "Manual fact-check completed.",
    )

    resumed = workflow.resume_latest(blocked.checkpoint.run_id)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.summary.commit_count == 7
    assert resumed.summary.unresolved_objection_count == 0
    assert resumed.summary.blocking_delegation_count == 0


def test_agent_runner_partial_retry_does_not_duplicate_completed_global_commit() -> None:
    workflow = BlackboardInitializationWorkflow(
        runner=StructuredInitializationRunner(include_blockers=False),
        execution_mode="agent_runner",
    )
    partial = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)

    resumed = workflow.resume(partial.checkpoint)
    run = workflow.blackboard.get_run(partial.checkpoint.run_id)

    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert len(run.commit_log) == 7
    assert [
        commit.patch.target.field_path
        for commit in run.commit_log
        if commit.patch.target.document_type is DocumentType.GLOBAL_RESEARCH
    ] == ["document", "document.market_narrative_report"]
