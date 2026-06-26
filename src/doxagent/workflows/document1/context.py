# ruff: noqa: F403,F405
"""Behavior-preserving mixin extracted from initialization.py."""

from doxagent.workflows.document1.context_pack import (
    Document1ContextPack,
    build_document1_context_pack,
)
from doxagent.workflows.initialization.shared import *


class Document1ContextMixin:
    def _global_research_agent_context(
        self,
        inputs: GlobalResearchInputs,
        *,
        section_key: str,
        instruction: str,
        required_tool_names: list[str] | None = None,
        prior_sections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "global_research_inputs": inputs.model_dump(mode="json"),
            "document1_research_focus": {
                "primary_focus": (
                    "Prioritize recent company, macro, industry, and price developments, "
                    "roughly the last 30 days when evidence is available."
                ),
                "background_use": (
                    "Use longer history for baseline, cycle, valuation, or structural "
                    "explanation; do not turn the section into a generic one-year or "
                    "half-year overview."
                ),
                "claim_discipline": (
                    "Do not present older known facts as fresh catalysts unless a recent "
                    "filing, price reaction, guidance update, policy change, or industry "
                    "move has renewed their relevance."
                ),
            },
            "required_section_key": section_key,
            "section_instruction": instruction,
        }
        if required_tool_names:
            context["required_tool_names"] = required_tool_names
            context["tool_requirements"] = [
                {
                    "tool_name": tool_name,
                    "required": True,
                    "purpose": f"Required for {section_key}.",
                }
                for tool_name in required_tool_names
            ]
        if prior_sections is not None:
            context["prior_sections"] = prior_sections
        return context

    def _global_research_context_from_belief_state(
        self,
        run: Any,
        *,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
        permissions: AgentPermissions,
    ) -> dict[str, Any] | None:
        if not self._can_read_global_research(permissions):
            return None
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        if not bucket:
            return None
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            return None
        document = latest.get("document")
        if not isinstance(document, dict):
            return None
        pack = self._document1_context_pack_from_payload(document)
        if pack is None:
            return None
        return {
            "document_id": document.get("document_id"),
            "ticker": document.get("ticker") or run.ticker,
            "document1_context_pack": pack.model_dump(mode="json", exclude_none=True),
            "sections": self._document1_context_sections_from_pack(
                pack,
                node=node,
                agent_name=agent_name,
                task_type=task_type,
            ),
            "compaction": pack.compaction,
        }

    def _document1_context_pack_from_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> Document1ContextPack | None:
        run = self.blackboard.get_run(checkpoint.run_id)
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        if not bucket:
            return None
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            return None
        document = latest.get("document")
        if not isinstance(document, dict):
            return None
        return self._document1_context_pack_from_payload(document)

    def _document1_context_pack_from_payload(
        self,
        document: dict[str, Any],
    ) -> Document1ContextPack | None:
        try:
            global_research = GlobalResearchDocument.model_validate(document)
        except Exception:
            return None
        return build_document1_context_pack(global_research)

    def _document1_context_sections_from_pack(
        self,
        pack: Document1ContextPack,
        *,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
    ) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        claims = (
            pack.recent_company_facts
            + pack.recent_industry_macro_market_drivers
            + pack.stale_background_facts
        )
        for claim in claims:
            section_key = claim.source_section
            raw_section = {"author_agent": self._document1_section_author(section_key)}
            if not self._include_global_research_section(
                section_key,
                raw_section,
                node=node,
                agent_name=agent_name,
                task_type=task_type,
            ):
                continue
            payload = sections.setdefault(
                section_key,
                {
                    "summary": claim.text,
                    "author_agent": raw_section["author_agent"],
                    "evidence_count": len(claim.evidence_ids),
                    "claim_ids": [],
                    "freshness": claim.freshness,
                },
            )
            payload["claim_ids"].append(claim.claim_id)
            payload["evidence_count"] = max(
                int(payload.get("evidence_count") or 0),
                len(claim.evidence_ids),
            )
            if payload.get("freshness") != "recent_30d":
                payload["freshness"] = claim.freshness
        if pack.market_trace is not None and "market_trace_report" in sections:
            sections["market_trace_report"]["market_trace"] = pack.market_trace.model_dump(
                mode="json"
            )
        return sections

    def _document1_section_author(self, section_key: str) -> str | None:
        authors = {
            "fundamental_report": AgentName.C1_FUNDAMENTAL_RESEARCH.value,
            "macro_report": AgentName.C2_MACRO_RESEARCH.value,
            "industry_report": AgentName.C3_INDUSTRY_RESEARCH.value,
            "market_trace_report": AgentName.O4_MARKET_TRACE.value,
            "market_narrative_report": AgentName.O1_EXPECTATION_OWNER.value,
        }
        return authors.get(section_key)

    def _can_read_global_research(self, permissions: AgentPermissions) -> bool:
        scopes = set(permissions.readable_context_scopes)
        return bool(
            DocumentType.GLOBAL_RESEARCH.value in scopes
            or "belief_state" in scopes
            or "all" in scopes
        )

    def _include_global_research_section(
        self,
        section_key: str,
        raw_section: dict[str, Any],
        *,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
    ) -> bool:
        if (
            node
            in {
                WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
                WorkflowNode.GENERATE_EXPECTATION_DETAILS,
                WorkflowNode.GENERATE_EXPECTATION_UNITS,
            }
            and agent_name is AgentName.O1_EXPECTATION_OWNER
            and section_key == "market_narrative_report"
        ):
            return False
        author = raw_section.get("author_agent")
        if isinstance(author, str) and author == agent_name.value:
            return False
        return task_type is not TaskType.GENERATE_GLOBAL_RESEARCH

    def _market_evidence_snapshot_from_payload_refs(
        self,
        value: Any,
        *,
        ticker: str,
    ) -> dict[str, Any] | None:
        snapshots: list[dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                ref = EvidenceRef.model_validate(item)
            except Exception:
                continue
            snapshot = ref.retrieval_metadata.get("market_evidence_snapshot")
            if not is_structured_market_evidence_snapshot(snapshot):
                continue
            if isinstance(snapshot, dict) and isinstance(snapshot.get("daily_ohlcv"), list):
                snapshots.extend(
                    child
                    for child in snapshot["daily_ohlcv"]
                    if isinstance(child, dict)
                )
            elif isinstance(snapshot, dict):
                snapshots.append(snapshot)
        if not snapshots:
            return None
        return collect_market_evidence_snapshot(snapshots, target_symbol=ticker)
