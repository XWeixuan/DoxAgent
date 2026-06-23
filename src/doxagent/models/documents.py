"""Structured document contract models for the Blackboard work products."""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from doxagent.models.blackboard import EvidenceRef
from doxagent.models.common import AgentName, DocumentType, ExpectationDirection, PolicyActionType
from doxagent.models.ids import NonEmptyStr

JsonObject = dict[str, Any]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ResearchSection(ContractModel):
    text: NonEmptyStr
    summary: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    author_agent: AgentName
    reviewer_agents: list[AgentName] = Field(default_factory=list)


class DocumentBase(ContractModel):
    document_id: NonEmptyStr
    document_type: DocumentType
    ticker: NonEmptyStr
    created_at: datetime
    updated_at: datetime | None = None


class GlobalResearchDocument(DocumentBase):
    document_type: DocumentType = DocumentType.GLOBAL_RESEARCH
    fundamental_report: ResearchSection
    macro_report: ResearchSection
    industry_report: ResearchSection
    market_trace_report: ResearchSection
    market_narrative_report: ResearchSection | None = None


class PriceReaction(ContractModel):
    price_change: NonEmptyStr
    price_pattern: NonEmptyStr
    interpretation: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RealizedFact(ContractModel):
    event_id: NonEmptyStr
    description: NonEmptyStr
    price_reaction: PriceReaction
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class VariableStatus(ContractModel):
    variable_id: NonEmptyStr
    name: NonEmptyStr
    current_status: NonEmptyStr
    certainty: NonEmptyStr
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class EventMonitoringDirection(ContractModel):
    known_event_notice: NonEmptyStr
    positive_events: list[NonEmptyStr] = Field(default_factory=list)
    negative_events: list[NonEmptyStr] = Field(default_factory=list)


class ExpectationUnitDocument(DocumentBase):
    document_type: DocumentType = DocumentType.EXPECTATION_UNIT
    expectation_id: NonEmptyStr
    expectation_name: NonEmptyStr
    direction: ExpectationDirection
    why_it_matters: NonEmptyStr
    market_view: ResearchSection
    realized_facts: list[RealizedFact]
    realized_facts_summary: NonEmptyStr
    key_variables: list[VariableStatus]
    event_monitoring_direction: EventMonitoringDirection


class KnownEvent(ContractModel):
    @model_validator(mode="before")
    @classmethod
    def _hydrate_runtime_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        core_fact = _first_text(
            data,
            "core_fact",
            "description",
            "event_text",
            "text",
            "summary",
            "title",
            "event",
        )
        if core_fact:
            data.setdefault("core_fact", core_fact)
            data.setdefault("description", core_fact)
        keys = _string_list(
            data.get("duplicate_detection_keys")
            or data.get("duplicate_keys")
            or data.get("dedupe_keys")
            or data.get("keywords")
        )
        if core_fact:
            keys.extend(_derived_duplicate_keys(core_fact))
        event_id = str(data.get("event_id") or data.get("id") or "").strip()
        if event_id:
            keys.append(event_id)
        data["duplicate_detection_keys"] = _dedupe_strings(keys)
        return data

    event_id: NonEmptyStr
    event_time: datetime | None = None
    event_window: NonEmptyStr | None = None
    core_fact: NonEmptyStr
    description: NonEmptyStr
    duplicate_detection_keys: list[NonEmptyStr]
    source: EvidenceRef
    expectation_id: NonEmptyStr | None = None
    discussed_by_market: bool
    has_price_reaction: bool
    is_known_old_news: bool


class KnownEventsDocument(DocumentBase):
    document_type: DocumentType = DocumentType.KNOWN_EVENTS
    events: list[KnownEvent]


class MonitoringItem(ContractModel):
    @model_validator(mode="before")
    @classmethod
    def _hydrate_tool_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        name = str(
            data.get("name")
            or data.get("trigger_condition")
            or data.get("reasoning")
            or "monitoring item"
        ).strip()
        tool_input = dict(data.get("tool_input") or {})
        tool_input.pop("poll_interval_seconds", None)
        tool_input.setdefault("source_id", data.get("source_id") or "stocktwits_messages")
        tool_input.setdefault("enabled", data.get("enabled", True))
        tool_input.setdefault("mode", data.get("mode") or "merge")
        keywords = _dedupe_strings(
            [
                *(_string_list(tool_input.get("keywords"))),
                *(_string_list(data.get("base_keywords"))),
                *(_string_list(data.get("extra_keywords"))),
            ]
        )
        if keywords:
            tool_input["keywords"] = keywords
        usernames = _string_list(tool_input.get("usernames") or data.get("usernames"))
        if usernames:
            tool_input["usernames"] = usernames
        search_terms = _dedupe_strings(
            [
                *(_string_list(tool_input.get("search_terms"))),
                *(_string_list(data.get("search_terms"))),
                *(_string_list(data.get("extra_objects"))),
                *(_string_list(data.get("related_entities"))),
            ]
        )
        if search_terms:
            tool_input["search_terms"] = search_terms
        rss_urls = _string_list(tool_input.get("rss_urls") or data.get("rss_urls"))
        if rss_urls:
            tool_input["rss_urls"] = rss_urls
        source_filters = _string_list(
            tool_input.get("source_filters") or data.get("source_filters")
        )
        if source_filters:
            tool_input["source_filters"] = source_filters
        extra = dict(tool_input.get("extra") or {})
        if data.get("expectation_id"):
            extra.setdefault("expectation_id", data["expectation_id"])
        if data.get("priority"):
            extra.setdefault("priority", data["priority"])
        if data.get("trigger_condition"):
            extra.setdefault("trigger_condition", data["trigger_condition"])
        if extra:
            tool_input["extra"] = extra
        reasoning = str(
            data.get("reasoning")
            or data.get("trigger_condition")
            or data.get("description")
            or name
        ).strip()
        tool_input.setdefault("reason", reasoning)
        data["tool_input"] = tool_input
        data.setdefault("reasoning", reasoning)
        data.setdefault("base_keywords", _string_list(data.get("base_keywords")))
        data.setdefault("extra_objects", _string_list(data.get("extra_objects")))
        data.setdefault("extra_keywords", _string_list(data.get("extra_keywords")))
        data.setdefault("related_entities", _string_list(data.get("related_entities")))
        data.setdefault("priority", str(data.get("priority") or "medium"))
        data.setdefault("trigger_condition", name)
        return data

    item_id: NonEmptyStr
    tool_input: JsonObject
    reasoning: NonEmptyStr
    base_keywords: list[NonEmptyStr] = Field(default_factory=list)
    extra_objects: list[NonEmptyStr] = Field(default_factory=list)
    extra_keywords: list[NonEmptyStr] = Field(default_factory=list)
    related_entities: list[NonEmptyStr] = Field(default_factory=list)
    expectation_id: NonEmptyStr | None = None
    priority: NonEmptyStr
    trigger_condition: NonEmptyStr


class MonitoringConfigDocument(DocumentBase):
    document_type: DocumentType = DocumentType.MONITORING_CONFIG
    monitoring_items: list[MonitoringItem]
    applied_config_version: NonEmptyStr | None = None


class MonitoringPolicyRule(ContractModel):
    @model_validator(mode="before")
    @classmethod
    def _hydrate_policy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        policy_id = str(
            data.get("policy_id") or data.get("rule_id") or data.get("id") or ""
        ).strip()
        if policy_id:
            data.setdefault("policy_id", policy_id)
            data.setdefault("rule_id", policy_id)
        policy_type = str(data.get("policy_type") or "").strip()
        action_type = str(data.get("action_type") or "").strip()
        if not policy_type:
            policy_type = (
                "escalate"
                if action_type == PolicyActionType.PUSH_TO_AGENT.value
                else action_type
            )
        if not policy_type:
            policy_type = PolicyActionType.CACHE.value
        if not action_type:
            action_type = (
                PolicyActionType.PUSH_TO_AGENT.value
                if policy_type == "escalate"
                else policy_type
            )
        data["policy_type"] = policy_type
        data["action_type"] = action_type
        trigger_condition = str(
            data.get("trigger_condition")
            or data.get("condition")
            or data.get("description")
            or data.get("trigger")
            or "监控与 ticker 相关的信号。"
        ).strip()
        data.setdefault("trigger_condition", trigger_condition)
        if not isinstance(data.get("trigger"), dict):
            data["trigger"] = {"condition": trigger_condition}
        scope = dict(data.get("scope") or {})
        if data.get("expectation_id"):
            scope.setdefault("expectation_unit_id", data["expectation_id"])
        data["scope"] = scope
        if not isinstance(data.get("confirmation"), dict):
            data["confirmation"] = {
                "market_confirmation": str(data.get("confirmation") or "").strip()
            }
        if not isinstance(data.get("risk_guard"), dict):
            risk_guard = str(data.get("risk_guard") or "").strip()
            data["risk_guard"] = {"guardrail": risk_guard or "不生成真实 broker order。"}
        if not isinstance(data.get("action"), dict):
            data["action"] = _default_policy_action_payload(data.get("action"), policy_type)
        data.setdefault(
            "reasoning",
            str(
                data.get("reasoning")
                or data.get("strategy_note")
                or data.get("note")
                or ""
            ).strip()
            or "该 policy 服务于 Document 3 运行时动作路由。",
        )
        return data

    policy_id: NonEmptyStr
    rule_id: NonEmptyStr
    policy_type: NonEmptyStr
    action_type: PolicyActionType
    scope: JsonObject = Field(default_factory=dict)
    trigger: JsonObject = Field(default_factory=dict)
    trigger_condition: NonEmptyStr
    confirmation: JsonObject = Field(default_factory=dict)
    expectation_id: NonEmptyStr | None = None
    action: JsonObject
    risk_guard: JsonObject = Field(default_factory=dict)
    strategy_note: NonEmptyStr
    reasoning: NonEmptyStr
    evidence_fields: list[NonEmptyStr] = Field(default_factory=list)
    escalation_path: NonEmptyStr | None = None


class MonitoringPolicyDocument(DocumentBase):
    @model_validator(mode="before")
    @classmethod
    def _hydrate_policy_views(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        policies = list(data.get("policies") or [])
        if not policies:
            policies = [
                *list(data.get("direct_trade_rules") or []),
                *list(data.get("push_to_agent_rules") or []),
                *list(data.get("cache_rules") or []),
            ]
            data["policies"] = policies
        if policies:
            data.setdefault(
                "direct_trade_rules",
                [item for item in policies if _raw_policy_type(item) == "direct_trade"],
            )
            data.setdefault(
                "push_to_agent_rules",
                [item for item in policies if _raw_policy_type(item) == "escalate"],
            )
            data.setdefault(
                "cache_rules",
                [item for item in policies if _raw_policy_type(item) == "cache"],
            )
        return data

    document_type: DocumentType = DocumentType.MONITORING_POLICY
    policies: list[MonitoringPolicyRule] = Field(default_factory=list)
    direct_trade_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    push_to_agent_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    cache_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    no_action_rationale: NonEmptyStr | None = None


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _first_text(value, *keys)
            if nested:
                return nested
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(_string_list(child))
        return items
    return []


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _derived_duplicate_keys(text: str) -> list[str]:
    keys: list[str] = []
    for match in re.findall(r"\b[A-Z]{2,6}\b|\b\d+(?:\.\d+)?%?\b", text):
        keys.append(match)
    for token in (
        "approved",
        "approval",
        "guidance",
        "order",
        "contract",
        "lawsuit",
        "监管",
        "订单",
        "指引",
        "审批",
    ):
        if token.lower() in text.lower():
            keys.append(token)
    return keys


def _raw_policy_type(value: Any) -> str:
    if isinstance(value, MonitoringPolicyRule):
        return value.policy_type
    if not isinstance(value, dict):
        return ""
    policy_type = str(value.get("policy_type") or "").strip()
    if policy_type:
        return policy_type
    action_type = str(value.get("action_type") or "").strip()
    return "escalate" if action_type == PolicyActionType.PUSH_TO_AGENT.value else action_type


def _default_policy_action_payload(value: Any, policy_type: str) -> JsonObject:
    text = str(value or "").strip()
    if policy_type == "direct_trade":
        return {
            "side": "long",
            "conviction": "medium",
            "size_bucket": "normal",
            "note": text or "生成 trade intent，不生成真实订单。",
        }
    if policy_type == "escalate":
        return {
            "send_to": ["O1", "O4"],
            "question": text or "请复核该消息是否改变现有 expectation unit。",
            "priority": "medium",
        }
    return {
        "cache_label": "background_only",
        "handling": text or "进入缓存，等待批量复核。",
    }
