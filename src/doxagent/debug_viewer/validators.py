"""Hard validators for persisted Blackboard initialization runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from doxagent.agents.config import default_agent_registry

if TYPE_CHECKING:
    from doxagent.debug_viewer.query import DebugRunBundle

JsonDict = dict[str, Any]

_CORE_GLOBAL_SECTIONS = (
    "fundamental_report",
    "macro_report",
    "industry_report",
    "market_trace_report",
    "market_narrative_report",
)
_REQUIRED_EVIDENCE_FIELDS = (
    "evidence_id",
    "source_type",
    "source_id",
    "title",
    "summary",
    "confidence",
    "citation_scope",
)
_UNSUCCESSFUL_TOOL_STATUSES = {"failed", "error", "blocked", "timeout"}
_SUCCESS_STATUSES = {"succeeded", "success", "completed", "complete", "partial"}


def build_hard_validator_view(bundle: DebugRunBundle) -> JsonDict:
    """Run all local hard validators used by the Brief State Viewer and eval export."""

    validators = [
        validate_evidence_reference_integrity(bundle),
        validate_langsmith_trajectory_tool_boundary(bundle),
        validate_commit_log_state_mutation_consistency(bundle),
    ]
    failed = [item for item in validators if item["status"] == "failed"]
    warnings = [item for item in validators if item["status"] == "warning"]
    return {
        "status": "failed" if failed else "warning" if warnings else "passed",
        "summary": {
            "validator_count": len(validators),
            "failed_count": len(failed),
            "warning_count": len(warnings),
            "finding_count": sum(len(_list(item.get("findings"))) for item in validators),
        },
        "validators": validators,
    }


def validate_evidence_reference_integrity(bundle: DebugRunBundle) -> JsonDict:
    """Check that important Blackboard claims have locatable evidence refs."""

    ctx = _Context(bundle)
    findings: list[JsonDict] = []
    checked_items = 0

    for record in ctx.documents("global_research"):
        document = _dict(record.get("document"))
        for field in _CORE_GLOBAL_SECTIONS:
            section = _dict(document.get(field))
            if not section:
                continue
            checked_items += 1
            _require_evidence(
                findings,
                section.get("evidence_refs"),
                location=f"stable_documents.global_research.{record['object_id']}.{field}",
                label=f"Global Research section {field}",
                evidence_index=ctx.evidence_index,
            )

    for record in ctx.documents("expectation_unit"):
        document = _dict(record.get("document"))
        expectation_id = str(document.get("expectation_id") or record["object_id"])
        market_view = _dict(document.get("market_view"))
        checked_items += 1
        _require_evidence(
            findings,
            market_view.get("evidence_refs"),
            location=f"stable_documents.expectation_unit.{expectation_id}.market_view",
            label=f"Expectation {expectation_id} market_view",
            evidence_index=ctx.evidence_index,
        )
        for idx, fact in enumerate(_dicts(document.get("realized_facts"))):
            checked_items += 1
            _require_evidence(
                findings,
                fact.get("evidence_refs"),
                location=f"stable_documents.expectation_unit.{expectation_id}.realized_facts[{idx}]",
                label=f"Expectation {expectation_id} realized fact {idx}",
                evidence_index=ctx.evidence_index,
            )
            price_reaction = _dict(fact.get("price_reaction"))
            if price_reaction:
                checked_items += 1
                _require_evidence(
                    findings,
                    price_reaction.get("evidence_refs"),
                    location=(
                        "stable_documents.expectation_unit."
                        f"{expectation_id}.realized_facts[{idx}].price_reaction"
                    ),
                    label=f"Expectation {expectation_id} price reaction {idx}",
                    evidence_index=ctx.evidence_index,
                )
        for idx, variable in enumerate(_dicts(document.get("key_variables"))):
            checked_items += 1
            _require_evidence(
                findings,
                variable.get("evidence_refs"),
                location=f"stable_documents.expectation_unit.{expectation_id}.key_variables[{idx}]",
                label=f"Expectation {expectation_id} key variable {idx}",
                evidence_index=ctx.evidence_index,
            )

    for record in ctx.documents("known_events"):
        document = _dict(record.get("document"))
        for idx, event in enumerate(_dicts(document.get("events"))):
            checked_items += 1
            _require_evidence(
                findings,
                [event.get("source")],
                location=f"stable_documents.known_events.{record['object_id']}.events[{idx}].source",
                label=f"Known event {idx}",
                evidence_index=ctx.evidence_index,
            )

    for objection in bundle.objections:
        checked_items += 1
        _require_evidence(
            findings,
            objection.get("evidence_refs"),
            location=f"objections.{objection.get('objection_id', 'unknown')}.evidence_refs",
            label=f"Objection {objection.get('objection_id', 'unknown')}",
            evidence_index=ctx.evidence_index,
        )

    for commit in bundle.commit_log:
        patch = _dict(commit.get("patch"))
        document_type = _str(_deep_get(commit, ("patch", "target", "document_type")))
        if document_type in {
            "global_research",
            "expectation_unit",
            "known_events",
            "monitoring_config",
            "monitoring_policy",
        }:
            checked_items += 1
            _require_evidence(
                findings,
                patch.get("evidence_refs"),
                location=f"commit_log.{commit.get('commit_id', 'unknown')}.patch.evidence_refs",
                label=f"Commit {commit.get('commit_id', 'unknown')} patch evidence",
                evidence_index=ctx.evidence_index,
            )

    if checked_items == 0:
        findings.append(
            _finding(
                "error",
                "no_evidence_scoped_items",
                (
                    "No stable documents, objections, or state-changing commits were available "
                    "for evidence reference validation."
                ),
                "stable_documents",
            )
        )

    return _result(
        "evidence_reference_integrity",
        "Evidence Reference Integrity Validator",
        findings,
        checked_items=checked_items,
        scope=(
            "Checks existence and locatability of evidence refs for stable research sections, "
            "expectation claims, known events, objections, and stable-state commits. It does "
            "not judge evidence sufficiency or reasoning depth."
        ),
    )


def validate_langsmith_trajectory_tool_boundary(bundle: DebugRunBundle) -> JsonDict:
    """Check locally persisted trajectory/tool summaries against agent boundaries."""

    allowed_tools = _allowed_tools_by_agent()
    findings: list[JsonDict] = []
    checked_items = 0
    author_entries: dict[str, int] = {}
    successful_tools_in_run: set[str] = set()
    for entry in bundle.working_memory:
        successful_tools_in_run.update(_successful_tool_names_from_entry(entry))

    for entry in bundle.working_memory:
        agent = _str(entry.get("author_agent"), default="unknown")
        author_entries[agent] = author_entries.get(agent, 0) + 1
        payload = _dict(entry.get("payload"))
        status = _str(payload.get("status")).lower()
        result_payload = _dict(payload.get("payload"))
        runtime = _str(result_payload.get("runtime")).lower()
        audit = _dict(result_payload.get("react_audit"))
        content_type = _str(entry.get("content_type"))
        entry_id = _str(entry.get("entry_id"), default="unknown")
        checked_items += 1

        if runtime == "react" or content_type in {
            "agent_result",
            "global_research_agent_result",
            "expectation_detail_result",
            "expectation_construction_resolution",
            "a1_expectation_construction_review",
            "delegated_retrieval_result",
        }:
            if not audit:
                findings.append(
                    _finding(
                        "error",
                        "missing_local_trajectory",
                        f"{agent} Working Memory entry has no persisted ReAct audit trajectory.",
                        f"working_memory.{entry_id}.payload.payload.react_audit",
                        details={"agent": agent, "content_type": content_type},
                    )
                )
            elif not any(item.get("kind") == "action" for item in _dicts(audit.get("entries"))):
                findings.append(
                    _finding(
                        "warning",
                        "no_action_loop_entries",
                        f"{agent} ReAct audit has no action loop entries.",
                        f"working_memory.{entry_id}.payload.payload.react_audit.entries",
                        details={"agent": agent, "content_type": content_type},
                    )
                )

        for tool_name in _tool_names_from_entry(entry):
            checked_items += 1
            allowed = _allowed_tools_for_entry(agent, entry, allowed_tools)
            if allowed is not None and tool_name not in allowed:
                findings.append(
                    _finding(
                        "error",
                        "tool_not_allowed_for_agent",
                        (
                            f"{agent} used or declared tool {tool_name}, outside its "
                            "configured allowlist."
                        ),
                        f"working_memory.{entry_id}",
                        details={
                            "agent": agent,
                            "tool_name": tool_name,
                            "allowed_tools": sorted(allowed),
                        },
                    )
                )

        tool_calls = _dicts(payload.get("tool_calls"))
        recovered_tools = {
            _str(tool_call.get("tool_name"))
            for tool_call in tool_calls
            if _str(tool_call.get("status")).lower() in _SUCCESS_STATUSES
        }
        for idx, tool_call in enumerate(tool_calls):
            call_status = _str(tool_call.get("status")).lower()
            tool_name = _str(tool_call.get("tool_name"))
            if (
                call_status in _UNSUCCESSFUL_TOOL_STATUSES
                and status in _SUCCESS_STATUSES
                and tool_name not in recovered_tools
            ):
                findings.append(
                    _finding(
                        "error",
                        "failed_tool_call_in_successful_agent_result",
                        (
                            f"{agent} has failed tool call {tool_call.get('tool_name')} but the "
                            "agent result is marked successful."
                        ),
                        f"working_memory.{entry_id}.payload.tool_calls[{idx}]",
                        details={"agent": agent, "tool_call": tool_call},
                    )
                )

        unexecuted = _list(
            _deep_get(payload, ("tool_usage_audit", "unexecuted_declared_tool_names"))
        )
        for idx, tool_name in enumerate(unexecuted):
            if str(tool_name) in successful_tools_in_run:
                continue
            findings.append(
                _finding(
                    "error",
                    "declared_unexecuted_tool_evidence",
                    f"{agent} declared tool evidence without an executed tool call: {tool_name}.",
                    (
                        f"working_memory.{entry_id}.payload.tool_usage_audit."
                        f"unexecuted_declared_tool_names[{idx}]"
                    ),
                    details={"agent": agent, "tool_name": tool_name},
                )
            )

    latest_completed = _latest_completed_nodes(bundle)
    required_agents = _required_agents_for_completed_nodes(latest_completed)
    for node, agents in required_agents.items():
        for agent in agents:
            checked_items += 1
            if author_entries.get(agent, 0) == 0:
                findings.append(
                    _finding(
                        "error",
                        "missing_required_agent_trajectory",
                        (
                            f"{node} completed but no local trajectory/Working Memory "
                            f"entry was found for {agent}."
                        ),
                        f"latest_checkpoint.completed_nodes.{node}",
                        details={"workflow_node": node, "agent": agent},
                    )
            )

    latest_checkpoint = _latest_checkpoint(bundle)
    latest_status = _str(latest_checkpoint.get("status")).lower()
    if latest_checkpoint and latest_status != "completed":
        findings.append(
            _finding(
                "error",
                "workflow_trace_not_completed",
                (
                    "Latest workflow checkpoint is not completed, so local trajectory review "
                    "cannot represent a closed initialization run."
                ),
                "latest_checkpoint.status",
                details={
                    "status": latest_checkpoint.get("status"),
                    "next_node": latest_checkpoint.get("next_node"),
                },
            )
        )

    checkpoint_payload = _dict(latest_checkpoint.get("checkpoint"))
    idempotency = _dict(_deep_get(checkpoint_payload, ("metadata", "workflow_agent_idempotency")))
    for key, state in idempotency.items():
        if _str(_dict(state).get("status")).lower() == "running":
            checked_items += 1
            findings.append(
                _finding(
                    "error",
                    "open_agent_dispatch",
                    f"Agent dispatch {key} is still marked running in latest checkpoint metadata.",
                    f"latest_checkpoint.checkpoint.metadata.workflow_agent_idempotency.{key}",
                    details={"dispatch": state},
                )
            )

    if checked_items == 0:
        findings.append(
            _finding(
                "error",
                "no_local_trajectory_entries",
                "No Working Memory or completed-node trajectory entries were available to validate.",
                "working_memory",
            )
        )

    return _result(
        "langsmith_trajectory_tool_boundary",
        "LangSmith Trajectory / Tool Boundary Validator",
        findings,
        checked_items=checked_items,
        scope=(
            "Checks the locally persisted ReAct audit/tool-call mirror used by the Brief State "
            "Viewer against workflow-agent boundaries. Remote LangSmith MCP review is still "
            "required for full LLM-as-judge process scoring."
        ),
        metadata={
            "remote_langsmith_review_required": True,
            "local_trajectory_source": (
                "working_memory.payload.payload.react_audit and payload.tool_calls"
            ),
        },
    )


def validate_commit_log_state_mutation_consistency(bundle: DebugRunBundle) -> JsonDict:
    """Check stable Blackboard state can be explained by commit-log mutations."""

    ctx = _Context(bundle)
    findings: list[JsonDict] = []
    checked_items = 0
    commit_targets = [_commit_target(commit) for commit in bundle.commit_log]
    commit_ids = {
        _str(commit.get("commit_id"))
        for commit in bundle.commit_log
        if commit.get("commit_id")
    }
    belief_commit_ids = {
        _str(item)
        for item in _list(bundle.belief_state.get("commit_ids"))
        if _str(item)
    }

    for commit_id in belief_commit_ids:
        checked_items += 1
        if commit_id not in commit_ids:
            findings.append(
                    _finding(
                        "error",
                        "belief_state_commit_id_missing_from_commit_log",
                        (
                            f"Belief state references commit_id {commit_id}, but it is "
                            "absent from commit_log."
                        ),
                        f"belief_state.commit_ids.{commit_id}",
                    details={"commit_id": commit_id},
                )
            )

    for record in ctx.all_documents():
        document_type = _str(record.get("document_type"))
        object_id = _str(record.get("object_id"))
        document = _dict(record.get("document"))
        checked_items += 1
        if not _has_matching_commit(commit_targets, document_type, object_id, document):
            findings.append(
                _finding(
                    "error",
                    "stable_document_without_commit_trace",
                    (
                        f"Stable {document_type} document {object_id} has no matching commit_log "
                        "entry explaining its state mutation."
                    ),
                    f"stable_documents.{document_type}.{object_id}",
                    details={
                        "document_type": document_type,
                        "object_id": object_id,
                        "document_id": document.get("document_id"),
                        "expectation_id": document.get("expectation_id"),
                    },
                )
            )

    stable_targets = {
        (record["document_type"], record["object_id"])
        for record in ctx.all_documents()
    }
    stable_expectation_ids = {
        _str(_dict(record.get("document")).get("expectation_id"))
        for record in ctx.documents("expectation_unit")
    }
    for commit in bundle.commit_log:
        checked_items += 1
        patch = _dict(commit.get("patch"))
        target = _dict(patch.get("target"))
        document_type = _str(target.get("document_type"))
        if not document_type:
            findings.append(
                _finding(
                    "error",
                    "commit_missing_target_document_type",
                    f"Commit {commit.get('commit_id')} patch target has no document_type.",
                    f"commit_log.{commit.get('commit_id', 'unknown')}.patch.target",
                    details={"commit_id": commit.get("commit_id")},
                )
            )
            continue
        target_object = _target_object_id(target)
        after = _dict(patch.get("after"))
        after_type = _str(after.get("document_type"))
        if after_type and after_type != document_type:
            findings.append(
                _finding(
                    "error",
                    "commit_after_document_type_mismatch",
                    (
                        f"Commit {commit.get('commit_id')} target document_type {document_type} "
                        f"does not match patch.after document_type {after_type}."
                    ),
                    f"commit_log.{commit.get('commit_id', 'unknown')}.patch.after.document_type",
                    details={"commit_id": commit.get("commit_id")},
                )
            )
        if document_type == "expectation_unit" and target_object:
            after_expectation = _str(after.get("expectation_id"))
            if after_expectation and after_expectation != target_object:
                findings.append(
                    _finding(
                        "error",
                        "commit_expectation_id_mismatch",
                        (
                            f"Commit {commit.get('commit_id')} target expectation_id "
                            f"{target_object} "
                            f"does not match patch.after expectation_id {after_expectation}."
                        ),
                        f"commit_log.{commit.get('commit_id', 'unknown')}.patch",
                        details={"commit_id": commit.get("commit_id")},
                    )
                )
        if (
            document_type == "expectation_unit"
            and target_object
            and target_object not in stable_expectation_ids
        ):
            findings.append(
                _finding(
                    "warning",
                    "commit_targets_missing_expectation",
                    (
                        f"Commit {commit.get('commit_id')} targets expectation {target_object}, "
                        "which is not present in final stable state."
                    ),
                    f"commit_log.{commit.get('commit_id', 'unknown')}.patch.target.expectation_id",
                    details={"commit_id": commit.get("commit_id"), "expectation_id": target_object},
                )
            )
        elif document_type != "expectation_unit":
            key = (document_type, target_object or _str(after.get("document_id")) or "singleton")
            if document_type in {
                "global_research",
                "known_events",
                "monitoring_config",
                "monitoring_policy",
            }:
                target_types = {item[0] for item in stable_targets}
                if document_type not in target_types:
                    findings.append(
                        _finding(
                            "warning",
                            "commit_targets_missing_document_type",
                            (
                                f"Commit {commit.get('commit_id')} targets {document_type}, "
                                "which is not present in final stable state."
                            ),
                            (
                                f"commit_log.{commit.get('commit_id', 'unknown')}."
                                "patch.target.document_type"
                            ),
                            details={"commit_id": commit.get("commit_id"), "target": key},
                        )
                    )

    commit_patch_ids = {
        _str(_deep_get(commit, ("patch", "patch_id")))
        for commit in bundle.commit_log
        if _deep_get(commit, ("patch", "patch_id"))
    }
    working_memory_patch_ids = _working_memory_patch_ids(bundle.working_memory)
    for patch_id in sorted(commit_patch_ids):
        checked_items += 1
        if patch_id not in working_memory_patch_ids:
            findings.append(
                _finding(
                    "warning",
                    "commit_patch_not_seen_in_working_memory",
                    f"Commit patch {patch_id} is not referenced by any Working Memory patch_ids.",
                    f"commit_log.patch_id.{patch_id}",
                    details={"patch_id": patch_id},
                )
            )

    if checked_items == 0:
        findings.append(
            _finding(
                "error",
                "no_state_mutations_to_validate",
                (
                    "No stable documents, belief commit ids, or commit_log entries were available "
                    "for commit/state consistency validation."
                ),
                "commit_log",
            )
        )

    return _result(
        "commit_log_state_mutation_consistency",
        "Commit Log / State Mutation Consistency Validator",
        findings,
        checked_items=checked_items,
        scope=(
            "Checks that stable Blackboard documents and important state mutations are "
            "explained by commit_log entries. It does not judge investment content quality."
        ),
    )


class _Context:
    def __init__(self, bundle: DebugRunBundle) -> None:
        self.bundle = bundle
        self.documents_root = _dict(bundle.belief_state.get("documents"))
        self.evidence_index = {
            _str(item.get("evidence_id")): item
            for item in bundle.evidence_refs
            if item.get("evidence_id")
        }

    def documents(self, document_type: str) -> list[JsonDict]:
        bucket = _dict(self.documents_root.get(document_type))
        records: list[JsonDict] = []
        for object_id, value in bucket.items():
            raw = _dict(value)
            document = _dict(raw.get("document")) or raw
            records.append(
                {
                    "document_type": document_type,
                    "object_id": _str(object_id),
                    "document": document,
                }
            )
        return records

    def all_documents(self) -> list[JsonDict]:
        records: list[JsonDict] = []
        for document_type in sorted(str(key) for key in self.documents_root):
            records.extend(self.documents(document_type))
        return records


def _result(
    validator_id: str,
    title: str,
    findings: list[JsonDict],
    *,
    checked_items: int,
    scope: str,
    metadata: JsonDict | None = None,
) -> JsonDict:
    error_count = sum(1 for item in findings if item.get("severity") == "error")
    warning_count = sum(1 for item in findings if item.get("severity") == "warning")
    return {
        "validator_id": validator_id,
        "title": title,
        "status": "failed" if error_count else "warning" if warning_count else "passed",
        "scope": scope,
        "checked_items": checked_items,
        "summary": {
            "error_count": error_count,
            "warning_count": warning_count,
            "finding_count": len(findings),
        },
        "findings": findings,
        "metadata": metadata or {},
    }


def _require_evidence(
    findings: list[JsonDict],
    value: Any,
    *,
    location: str,
    label: str,
    evidence_index: Mapping[str, JsonDict],
) -> None:
    refs = _dicts(value)
    if not refs:
        findings.append(
            _finding(
                "error",
                "missing_evidence_refs",
                f"{label} has no evidence_refs.",
                location,
            )
        )
        return
    for idx, ref in enumerate(refs):
        evidence_id = _str(ref.get("evidence_id"))
        ref_location = f"{location}[{idx}]"
        if not evidence_id:
            findings.append(
                _finding(
                    "error",
                    "evidence_ref_missing_id",
                    f"{label} has an evidence ref without evidence_id.",
                    ref_location,
                    details={"evidence_ref": ref},
                )
            )
            continue
        if evidence_id not in evidence_index:
            findings.append(
                _finding(
                    "error",
                    "evidence_ref_not_hydrated",
                    (
                        f"{label} references evidence_id {evidence_id}, but it is "
                        "absent from run evidence_refs."
                    ),
                    ref_location,
                    details={"evidence_id": evidence_id},
                )
            )
        source = _dict(evidence_index.get(evidence_id)) or ref
        missing_fields = [
            field
            for field in _REQUIRED_EVIDENCE_FIELDS
            if _missing(source.get(field))
        ]
        if missing_fields:
            findings.append(
                _finding(
                    "error",
                    "evidence_ref_missing_required_fields",
                    f"{label} evidence_id {evidence_id} is missing required fields.",
                    ref_location,
                    details={"evidence_id": evidence_id, "missing_fields": missing_fields},
                )
            )


def _finding(
    severity: str,
    code: str,
    message: str,
    location: str,
    *,
    details: JsonDict | None = None,
) -> JsonDict:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "location": location,
        "details": details or {},
    }


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _allowed_tools_by_agent() -> dict[str, set[str]]:
    registry = default_agent_registry()
    return {
        agent_name.value: set(registry.get(agent_name).runtime.allowed_tools)
        for agent_name in registry.names()
    }


def _allowed_tools_for_entry(
    agent: str,
    entry: JsonDict,
    defaults: Mapping[str, set[str]],
) -> set[str] | None:
    workflow_node = _workflow_node_from_entry(entry)
    if workflow_node:
        node_allowed = _node_agent_allowed_tools().get((workflow_node, agent))
        if node_allowed is not None:
            return node_allowed
    return defaults.get(agent)


def _node_agent_allowed_tools() -> dict[tuple[str, str], set[str]]:
    from doxagent.workflows.initialization import NODE_AGENT_ALLOWED_TOOL_OVERRIDES

    return {
        (node.value, agent.value): set(tools)
        for (node, agent), tools in NODE_AGENT_ALLOWED_TOOL_OVERRIDES.items()
    }


def _workflow_node_from_entry(entry: JsonDict) -> str:
    payload = _dict(entry.get("payload"))
    direct = _str(_deep_get(payload, ("acceptance_audit", "workflow_node")))
    if direct:
        return direct
    result_payload = _dict(payload.get("payload"))
    for audit in _dicts(result_payload.get("model_audits")):
        node = _str(_deep_get(audit, ("metadata", "workflow_node")))
        if node:
            return node
    return ""


def _tool_names_from_entry(entry: JsonDict) -> set[str]:
    payload = _dict(entry.get("payload"))
    result_payload = _dict(payload.get("payload"))
    audit = _dict(result_payload.get("react_audit"))
    names = {
        _str(tool.get("tool_name"))
        for tool in _dicts(payload.get("tool_calls"))
        if tool.get("tool_name")
    }
    names.update(str(name) for name in _dict(audit.get("tool_counts")) if name)
    return {name for name in names if name}


def _successful_tool_names_from_entry(entry: JsonDict) -> set[str]:
    payload = _dict(entry.get("payload"))
    result_payload = _dict(payload.get("payload"))
    audit = _dict(result_payload.get("react_audit"))
    names = {
        _str(tool.get("tool_name"))
        for tool in _dicts(payload.get("tool_calls"))
        if tool.get("tool_name") and _str(tool.get("status")).lower() in _SUCCESS_STATUSES
    }
    names.update(str(name) for name in _dict(audit.get("tool_counts")) if name)
    return {name for name in names if name}


def _latest_completed_nodes(bundle: DebugRunBundle) -> set[str]:
    latest = _latest_checkpoint(bundle)
    if not latest:
        return set()
    return {_str(item) for item in _list(latest.get("completed_nodes")) if _str(item)}


def _latest_checkpoint(bundle: DebugRunBundle) -> JsonDict:
    if not bundle.checkpoints:
        return {}
    latest = bundle.checkpoints[0]
    return _dict(latest)


def _required_agents_for_completed_nodes(completed_nodes: set[str]) -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    if "BuildGlobalResearch" in completed_nodes:
        required["BuildGlobalResearch"] = {"C1", "C2", "C3", "O4"}
    if "ReviewExpectationConstruction" in completed_nodes:
        required["ReviewExpectationConstruction"] = {"A1"}
    if "GenerateExpectationDetails" in completed_nodes:
        required["GenerateExpectationDetails"] = {"O1"}
    if "ReviewExpectationFields" in completed_nodes:
        required["ReviewExpectationFields"] = {"A1", "C1", "C3", "O4"}
    if "GenerateGlobalNarrativeReport" in completed_nodes:
        required["GenerateGlobalNarrativeReport"] = {"O1"}
    if "GenerateKnownEvents" in completed_nodes:
        required["GenerateKnownEvents"] = {"O1"}
    if "GenerateMonitoringConfig" in completed_nodes:
        required["GenerateMonitoringConfig"] = {"O2"}
    if "GenerateMonitoringPolicy" in completed_nodes:
        required["GenerateMonitoringPolicy"] = {"O2"}
    return required


def _commit_target(commit: JsonDict) -> JsonDict:
    patch = _dict(commit.get("patch"))
    target = _dict(patch.get("target"))
    return {
        "commit_id": commit.get("commit_id"),
        "document_type": _str(target.get("document_type")),
        "target_object_id": _target_object_id(target),
        "document_id": _str(target.get("document_id")),
        "expectation_id": _str(target.get("expectation_id")),
        "field_path": _str(target.get("field_path")),
        "after": _dict(patch.get("after")),
    }


def _has_matching_commit(
    commit_targets: list[JsonDict],
    document_type: str,
    object_id: str,
    document: JsonDict,
) -> bool:
    document_id = _str(document.get("document_id"))
    expectation_id = _str(document.get("expectation_id"))
    for target in commit_targets:
        if target.get("document_type") != document_type:
            continue
        target_object = _str(target.get("target_object_id"))
        if document_type == "expectation_unit":
            if target_object and target_object == expectation_id:
                return True
            after_expectation = _str(_dict(target.get("after")).get("expectation_id"))
            if after_expectation and after_expectation == expectation_id:
                return True
            continue
        if target_object in {object_id, document_id, "document", ""}:
            return True
        after_document_id = _str(_dict(target.get("after")).get("document_id"))
        if after_document_id and after_document_id == document_id:
            return True
    return False


def _target_object_id(target: JsonDict) -> str:
    return (
        _str(target.get("expectation_id"))
        or _str(target.get("document_id"))
        or _str(target.get("object_id"))
    )


def _working_memory_patch_ids(entries: Iterable[JsonDict]) -> set[str]:
    patch_ids: set[str] = set()
    for entry in entries:
        payload = _dict(entry.get("payload"))
        patch_ids.update(_str(item) for item in _list(payload.get("patch_ids")) if _str(item))
        for patch in _dicts(_deep_get(payload, ("payload", "structured", "proposed_patches"))):
            patch_id = _str(patch.get("patch_id"))
            if patch_id:
                patch_ids.add(patch_id)
        for patch in _dicts(_deep_get(payload, ("payload", "proposed_patches"))):
            patch_id = _str(patch.get("patch_id"))
            if patch_id:
                patch_ids.add(patch_id)
    return patch_ids


def _dict(value: Any) -> JsonDict:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[JsonDict]:
    return [dict(item) for item in _list(value) if isinstance(item, Mapping)]


def _str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _deep_get(value: Any, path: tuple[str, ...]) -> Any:
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
