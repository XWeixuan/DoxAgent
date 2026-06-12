"""Structured document contract models for the Blackboard work products."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.blackboard import EvidenceRef
from doxagent.models.common import AgentName, DocumentType, ExpectationDirection, PolicyActionType
from doxagent.models.ids import NonEmptyStr


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
    event_id: NonEmptyStr
    event_time: datetime
    description: NonEmptyStr
    source: EvidenceRef
    expectation_id: NonEmptyStr | None = None
    discussed_by_market: bool
    has_price_reaction: bool
    is_known_old_news: bool


class KnownEventsDocument(DocumentBase):
    document_type: DocumentType = DocumentType.KNOWN_EVENTS
    events: list[KnownEvent]


class MonitoringItem(ContractModel):
    item_id: NonEmptyStr
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


class MonitoringPolicyRule(ContractModel):
    rule_id: NonEmptyStr
    action_type: PolicyActionType
    trigger_condition: NonEmptyStr
    expectation_id: NonEmptyStr | None = None
    action: NonEmptyStr
    strategy_note: NonEmptyStr
    evidence_fields: list[NonEmptyStr] = Field(default_factory=list)
    escalation_path: NonEmptyStr | None = None


class MonitoringPolicyDocument(DocumentBase):
    document_type: DocumentType = DocumentType.MONITORING_POLICY
    direct_trade_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    push_to_agent_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    cache_rules: list[MonitoringPolicyRule] = Field(default_factory=list)
    no_action_rationale: NonEmptyStr | None = None
