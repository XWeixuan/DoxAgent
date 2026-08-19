"""Package Workflow V3 global clustering with one versioned rolling Registry."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from cdecr.contracts import AtomicEvent, ExternalRelationType, MembershipRelation
from cdecr.models import ModelTier
from cdecr.package_v3_contracts import (
    FrozenPackageClusterV3,
    FrozenPackageExternalLinkV3,
    FrozenPackagePartitionV3,
    PackageParentOccurrenceV3,
    PackageV3Description,
    PackageV3DescriptionInput,
    PackageV3DescriptionOccurrence,
    PackageV3DescriptionOutput,
    PackageV3InitialClusteringOutput,
    PackageV3OccurrenceInput,
    PackageV3RegistryCard,
    PackageV3RollingClusteringOutput,
    PackageWorkflowV3Failure,
    PackageWorkflowV3Result,
    RollingMCPStateV3,
)
from cdecr.parent_occurrence_contracts import (
    ParentExternalLinkProposal,
    ParentProposalCard,
)
from cdecr.ports import CDECRRegistry, DecisionAuditRecord, ResponsesModelRequest

PACKAGE_WORKFLOW_CONTRACT_VERSION = "package-global-registry-v3-contract-2"
PACKAGE_REGISTRY_POLICY_VERSION = "package-global-registry-v3-rolling-2"
PACKAGE_DESCRIPTION_CONTRACT_VERSION = "package-global-registry-v3-description-2"
PACKAGE_V3_MODEL = "deepseek-v4-flash-0731"
PACKAGE_V3_REASONING_EFFORT = "low"
PACKAGE_V3_DESCRIPTION_REASONING_EFFORT = "none"
_PROMPT_ROOT = Path(__file__).parent / "prompts" / "v1"


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prompt(name: str) -> str:
    return (_PROMPT_ROOT / name).read_text(encoding="utf-8")


def _error_code(exc: Exception) -> str:
    value = getattr(exc, "code", None)
    return str(value) if value else type(exc).__name__


class PackageWorkflowV3Service:
    """The only cross-document Package clustering service in V3."""

    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        batch_size: int = 200,
        context_token_budget: int = 100_000,
        context_reserve_tokens: int = 8_000,
        description_pack_token_budget: int = 32_000,
        description_active_requests: int = 16,
        reasoning_effort: Literal["none", "low", "high", "max"] = "low",
        description_reasoning_effort: Literal["none", "low", "high", "max"] = "none",
        strict_output: bool = False,
    ) -> None:
        self.registry = registry
        self.batch_size = max(1, min(200, batch_size))
        self.context_token_budget = max(8_000, context_token_budget)
        self.context_reserve_tokens = max(1_000, context_reserve_tokens)
        self.description_pack_token_budget = max(4_000, description_pack_token_budget)
        self.description_active_requests = max(1, description_active_requests)
        self.reasoning_effort = reasoning_effort
        self.description_reasoning_effort = description_reasoning_effort
        self.strict_output = strict_output

    @staticmethod
    def _occurrence_records(
        *,
        registry: CDECRRegistry,
        registry_scope_id: str,
        proposals: Sequence[ParentProposalCard],
        external_links: Sequence[ParentExternalLinkProposal],
    ) -> list[dict[str, Any]]:
        incoming_links: dict[str, list[dict[str, object]]] = defaultdict(list)
        for link in external_links:
            incoming_links[link.target_proposal_id].append(link.model_dump(mode="json"))
        output: list[dict[str, Any]] = []
        for proposal in proposals:
            document_ref = sorted(proposal.document_refs)[0]
            source = registry.get_source(document_ref)
            if source is None:
                raise ValueError(f"Parent Occurrence source {document_ref!r} is missing")
            fingerprint = registry.get_source_fingerprint(document_ref) or _hash(
                {"title": source.title, "text": source.text, "url": source.url}
            )
            business_payload = {
                "registry_scope_id": registry_scope_id,
                "document_fingerprint": fingerprint,
                "event_ids": sorted(proposal.event_ids),
                "scope": proposal.scope,
                "membership": {
                    key: value.value for key, value in sorted(proposal.membership_by_event.items())
                },
            }
            payload: dict[str, Any] = {
                "occurrence_business_key": _hash(business_payload),
                "source_proposal_id": proposal.proposal_id,
                "parent_occurrence": proposal.label,
                "atomic_event_ids": sorted(proposal.event_ids),
                "membership_by_event": {
                    key: value.value for key, value in proposal.membership_by_event.items()
                },
                "document_refs": sorted(proposal.document_refs),
                "document_fingerprint": fingerprint,
                "published_at": source.published_at.isoformat(),
                "scope": proposal.scope,
                "external_links": incoming_links.get(proposal.proposal_id, []),
            }
            payload["source_payload_hash"] = _hash(payload)
            output.append(payload)
        return output

    @staticmethod
    def _validate_initial(
        value: PackageV3InitialClusteringOutput,
        expected_occurrence_ids: set[str],
    ) -> None:
        cluster_ids = [item.mcp_id for item in value.clusters]
        if len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("initial cluster mcp_id values must be unique")
        actual = [item for cluster in value.clusters for item in cluster.occurrence_ids]
        if len(actual) != len(set(actual)) or set(actual) != expected_occurrence_ids:
            raise ValueError("initial clusters must cover each input occurrence exactly once")
        if any(not item.canonical.strip() for item in value.clusters):
            raise ValueError("initial cluster canonical must not be blank")

    @staticmethod
    def _normalize_initial(
        value: PackageV3InitialClusteringOutput,
        *,
        expected_occurrence_ids: set[str],
        labels: Mapping[str, str],
        actions: list[dict[str, object]] | None = None,
    ) -> PackageV3InitialClusteringOutput:
        """Localize ordinary model defects without turning the whole batch into a failure."""

        used_occurrences: set[str] = set()
        used_mcp_ids: set[str] = set()
        clusters: list[dict[str, object]] = []
        for index, cluster in enumerate(value.clusters, start=1):
            unknown = [
                item for item in cluster.occurrence_ids if item not in expected_occurrence_ids
            ]
            duplicates = [item for item in cluster.occurrence_ids if item in used_occurrences]
            if actions is not None and unknown:
                actions.append({"code": "UNKNOWN_OCCURRENCE_DROPPED", "ids": sorted(set(unknown))})
            if actions is not None and duplicates:
                actions.append(
                    {
                        "code": "DUPLICATE_OCCURRENCE_FIRST_WINS",
                        "ids": sorted(set(duplicates)),
                    }
                )
            occurrence_ids = [
                item
                for item in cluster.occurrence_ids
                if item in expected_occurrence_ids and item not in used_occurrences
            ]
            if not occurrence_ids:
                if actions is not None:
                    actions.append({"code": "EMPTY_CLUSTER_DROPPED", "position": index})
                continue
            used_occurrences.update(occurrence_ids)
            mcp_id = cluster.mcp_id.strip() or f"tmp-mcp-{index}"
            if mcp_id in used_mcp_ids:
                if actions is not None:
                    actions.append({"code": "DUPLICATE_MCP_REF_RENUMBERED", "id": mcp_id})
                mcp_id = f"{mcp_id}-dup-{index}"
            used_mcp_ids.add(mcp_id)
            canonical = cluster.canonical.strip()
            if not canonical:
                if actions is not None:
                    actions.append({"code": "BLANK_CANONICAL_PROVISIONAL", "mcp_ref": mcp_id})
                canonical = labels.get(occurrence_ids[0], "Unclassified parent occurrence")
            clusters.append(
                {"mcp_id": mcp_id, "canonical": canonical, "occurrence_ids": occurrence_ids}
            )
        for index, occurrence_id in enumerate(
            sorted(expected_occurrence_ids - used_occurrences), start=1
        ):
            if actions is not None:
                actions.append({"code": "MISSING_OCCURRENCE_SINGLETON", "id": occurrence_id})
            clusters.append(
                {
                    "mcp_id": f"tmp-singleton-{index}",
                    "canonical": labels.get(occurrence_id, "Unclassified parent occurrence"),
                    "occurrence_ids": [occurrence_id],
                }
            )
        return PackageV3InitialClusteringOutput.model_validate({"clusters": clusters})

    @staticmethod
    def _normalize_initial_inplace(
        value: PackageV3InitialClusteringOutput,
        *,
        expected_occurrence_ids: set[str],
        labels: Mapping[str, str],
        actions: list[dict[str, object]] | None = None,
    ) -> None:
        normalized = PackageWorkflowV3Service._normalize_initial(
            value,
            expected_occurrence_ids=expected_occurrence_ids,
            labels=labels,
            actions=actions,
        )
        value.clusters = normalized.clusters

    @staticmethod
    def _validate_rolling(
        value: PackageV3RollingClusteringOutput,
        *,
        expected_occurrence_ids: set[str],
        active_mcp_ids: set[str],
    ) -> None:
        assigned = [
            occurrence_id
            for item in value.existing_assignments
            for occurrence_id in item.occurrence_ids
        ]
        assigned.extend(
            occurrence_id for item in value.new_mcps for occurrence_id in item.occurrence_ids
        )
        if len(assigned) != len(set(assigned)) or set(assigned) != expected_occurrence_ids:
            raise ValueError("rolling changes must assign each new occurrence exactly once")
        if any(item.mcp_id not in active_mcp_ids for item in value.existing_assignments):
            raise ValueError("rolling assignment references an unknown MCP")
        sources: list[str] = []
        keeps: list[str] = []
        for merge in value.merges:
            if merge.keep_mcp_id not in active_mcp_ids:
                raise ValueError("rolling merge keep MCP is unknown")
            if merge.keep_mcp_id in merge.merge_mcp_ids:
                raise ValueError("rolling merge cannot merge a MCP into itself")
            if any(item not in active_mcp_ids for item in merge.merge_mcp_ids):
                raise ValueError("rolling merge source MCP is unknown")
            sources.extend(merge.merge_mcp_ids)
            keeps.append(merge.keep_mcp_id)
        if len(sources) != len(set(sources)):
            raise ValueError("rolling merge source may occur only once")
        if set(sources).intersection(keeps):
            raise ValueError("rolling merge keep cannot also be a merge source")
        if any(not item.canonical.strip() for item in value.new_mcps):
            raise ValueError("new MCP canonical must not be blank")

    @staticmethod
    def _normalize_rolling(
        value: PackageV3RollingClusteringOutput,
        *,
        expected_occurrence_ids: set[str],
        active_mcp_ids: set[str],
        labels: Mapping[str, str],
        actions: list[dict[str, object]] | None = None,
    ) -> PackageV3RollingClusteringOutput:
        used: set[str] = set()
        assignments: list[dict[str, object]] = []
        new_mcps: list[dict[str, object]] = []
        for assignment in value.existing_assignments:
            unknown = [
                occurrence_id
                for occurrence_id in assignment.occurrence_ids
                if occurrence_id not in expected_occurrence_ids
            ]
            duplicates = [
                occurrence_id
                for occurrence_id in assignment.occurrence_ids
                if occurrence_id in used
            ]
            if actions is not None and unknown:
                actions.append({"code": "UNKNOWN_OCCURRENCE_DROPPED", "ids": sorted(set(unknown))})
            if actions is not None and duplicates:
                actions.append(
                    {
                        "code": "DUPLICATE_OCCURRENCE_FIRST_WINS",
                        "ids": sorted(set(duplicates)),
                    }
                )
            ids = [
                occurrence_id
                for occurrence_id in assignment.occurrence_ids
                if occurrence_id in expected_occurrence_ids and occurrence_id not in used
            ]
            if not ids:
                continue
            used.update(ids)
            if assignment.mcp_id in active_mcp_ids:
                assignments.append({"mcp_id": assignment.mcp_id, "occurrence_ids": ids})
            else:
                if actions is not None:
                    actions.append(
                        {
                            "code": "UNKNOWN_MCP_ASSIGNMENT_SINGLETON",
                            "mcp_id": assignment.mcp_id,
                            "ids": ids,
                        }
                    )
                for occurrence_id in ids:
                    new_mcps.append(
                        {
                            "canonical": labels.get(
                                occurrence_id, "Unclassified parent occurrence"
                            ),
                            "occurrence_ids": [occurrence_id],
                        }
                    )
        for new_mcp in value.new_mcps:
            unknown = [
                occurrence_id
                for occurrence_id in new_mcp.occurrence_ids
                if occurrence_id not in expected_occurrence_ids
            ]
            duplicates = [
                occurrence_id for occurrence_id in new_mcp.occurrence_ids if occurrence_id in used
            ]
            if actions is not None and unknown:
                actions.append({"code": "UNKNOWN_OCCURRENCE_DROPPED", "ids": sorted(set(unknown))})
            if actions is not None and duplicates:
                actions.append(
                    {
                        "code": "DUPLICATE_OCCURRENCE_FIRST_WINS",
                        "ids": sorted(set(duplicates)),
                    }
                )
            ids = [
                occurrence_id
                for occurrence_id in new_mcp.occurrence_ids
                if occurrence_id in expected_occurrence_ids and occurrence_id not in used
            ]
            if not ids:
                continue
            used.update(ids)
            canonical = new_mcp.canonical.strip() or labels.get(
                ids[0], "Unclassified parent occurrence"
            )
            if actions is not None and not new_mcp.canonical.strip():
                actions.append({"code": "BLANK_CANONICAL_PROVISIONAL", "id": ids[0]})
            new_mcps.append({"canonical": canonical, "occurrence_ids": ids})
        for occurrence_id in sorted(expected_occurrence_ids - used):
            if actions is not None:
                actions.append({"code": "MISSING_OCCURRENCE_SINGLETON", "id": occurrence_id})
            new_mcps.append(
                {
                    "canonical": labels.get(occurrence_id, "Unclassified parent occurrence"),
                    "occurrence_ids": [occurrence_id],
                }
            )
        candidate_keeps = {
            merge.keep_mcp_id for merge in value.merges if merge.keep_mcp_id in active_mcp_ids
        }
        # Merge direction is independent of model list order. A MCP mentioned as
        # both keep and source participates in a chain/cycle and is conservatively
        # retained rather than redirected by whichever row happens to come first.
        sources: set[str] = set()
        merges: list[dict[str, object]] = []
        for merge in value.merges:
            if merge.keep_mcp_id not in active_mcp_ids:
                if actions is not None:
                    actions.append({"code": "UNKNOWN_MERGE_KEEP_DROPPED", "id": merge.keep_mcp_id})
                continue
            valid_sources = [
                source
                for source in merge.merge_mcp_ids
                if source in active_mcp_ids
                and source != merge.keep_mcp_id
                and source not in sources
                and source not in candidate_keeps
            ]
            if not valid_sources:
                if actions is not None:
                    actions.append(
                        {
                            "code": "EMPTY_MERGE_DROPPED",
                            "keep_mcp_id": merge.keep_mcp_id,
                        }
                    )
                continue
            dropped_sources = sorted(set(merge.merge_mcp_ids) - set(valid_sources))
            if actions is not None and dropped_sources:
                actions.append(
                    {
                        "code": "INVALID_MERGE_SOURCES_DROPPED",
                        "keep_mcp_id": merge.keep_mcp_id,
                        "ids": dropped_sources,
                    }
                )
            sources.update(valid_sources)
            merges.append({"keep_mcp_id": merge.keep_mcp_id, "merge_mcp_ids": valid_sources})
        return PackageV3RollingClusteringOutput.model_validate(
            {
                "existing_assignments": assignments,
                "new_mcps": new_mcps,
                "merges": merges,
            }
        )

    @staticmethod
    def _normalize_rolling_inplace(
        value: PackageV3RollingClusteringOutput,
        *,
        expected_occurrence_ids: set[str],
        active_mcp_ids: set[str],
        labels: Mapping[str, str],
        actions: list[dict[str, object]] | None = None,
    ) -> None:
        normalized = PackageWorkflowV3Service._normalize_rolling(
            value,
            expected_occurrence_ids=expected_occurrence_ids,
            active_mcp_ids=active_mcp_ids,
            labels=labels,
            actions=actions,
        )
        value.existing_assignments = normalized.existing_assignments
        value.new_mcps = normalized.new_mcps
        value.merges = normalized.merges

    @staticmethod
    def _validate_descriptions(
        value: PackageV3DescriptionOutput,
        expected_mcp_ids: set[str],
    ) -> None:
        actual = [item.mcp_id for item in value.descriptions]
        if len(actual) != len(set(actual)) or set(actual) != expected_mcp_ids:
            raise ValueError("descriptions must cover each requested affected MCP exactly once")
        if any(not item.compressed_description.strip() for item in value.descriptions):
            raise ValueError("compressed descriptions must not be blank")

    def _model_request(
        self,
        *,
        prompt: str,
        payload: dict[str, object],
        schema: dict[str, object],
        schema_name: str,
        stage: str,
        reasoning_effort: Literal["none", "low", "high", "max"],
    ) -> ResponsesModelRequest:
        return ResponsesModelRequest(
            input=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            json_schema=schema,
            output_mode="json_schema" if self.strict_output else "json_object",
            schema_name=schema_name,
            strict=self.strict_output,
            previous_response_id=None,
            reasoning_effort=reasoning_effort,
            metadata={
                "stage": stage,
                "package_workflow": "v3",
                "transport": (
                    "responses_json_schema" if self.strict_output else "responses_json_object"
                ),
            },
        )

    def _fit_batch(
        self,
        pending: Sequence[PackageParentOccurrenceV3],
        registry_cards: Sequence[PackageV3RegistryCard],
    ) -> list[PackageParentOccurrenceV3]:
        size = min(self.batch_size, len(pending))
        registry_payload = [item.model_dump(mode="json") for item in registry_cards]
        while size:
            payload = {
                "registry": registry_payload,
                "new_parent_occurrences": [
                    PackageV3OccurrenceInput(
                        occurrence_id=item.occurrence_id,
                        parent_occurrence=item.parent_occurrence,
                    ).model_dump(mode="json")
                    for item in pending[:size]
                ],
            }
            usable_context = max(1_000, self.context_token_budget - self.context_reserve_tokens)
            if len(json.dumps(payload, ensure_ascii=False)) // 4 <= usable_context:
                return list(pending[:size])
            size //= 2
        raise ValueError("PACKAGE_V3_REGISTRY_CONTEXT_EXHAUSTED")

    def _initial_state(
        self,
        *,
        output: PackageV3InitialClusteringOutput,
        registry_scope_id: str,
        version: int,
    ) -> tuple[list[RollingMCPStateV3], dict[str, str], set[str]]:
        ordered = sorted(output.clusters, key=lambda item: item.mcp_id)
        permanent_ids = self.registry.allocate_package_mcp_ids_v3(
            registry_scope_id=registry_scope_id, count=len(ordered)
        )
        mcps = [
            RollingMCPStateV3(
                mcp_id=mcp_id,
                canonical=cluster.canonical.strip(),
                compressed_description=cluster.canonical.strip(),
                occurrence_ids=sorted(set(cluster.occurrence_ids)),
                created_registry_version=version,
                updated_registry_version=version,
            )
            for cluster, mcp_id in zip(ordered, permanent_ids, strict=True)
        ]
        return mcps, {}, {item.mcp_id for item in mcps}

    def _rolling_state(
        self,
        *,
        output: PackageV3RollingClusteringOutput,
        current: Sequence[RollingMCPStateV3],
        current_redirects: Mapping[str, str],
        registry_scope_id: str,
        version: int,
    ) -> tuple[list[RollingMCPStateV3], dict[str, str], set[str]]:
        states = {item.mcp_id: item for item in current}
        redirects = dict(current_redirects)
        affected: set[str] = set()
        merge_root: dict[str, str] = {}
        for merge in output.merges:
            keep = merge.keep_mcp_id
            for source in sorted(set(merge.merge_mcp_ids)):
                merge_root[source] = keep
                redirects[source] = keep
                states[keep] = states[keep].model_copy(
                    update={
                        "occurrence_ids": sorted(
                            set(states[keep].occurrence_ids) | set(states[source].occurrence_ids)
                        ),
                        "updated_registry_version": version,
                    }
                )
                states.pop(source)
            affected.add(keep)
        for assignment in output.existing_assignments:
            target = merge_root.get(assignment.mcp_id, assignment.mcp_id)
            states[target] = states[target].model_copy(
                update={
                    "occurrence_ids": sorted(
                        set(states[target].occurrence_ids) | set(assignment.occurrence_ids)
                    ),
                    "updated_registry_version": version,
                }
            )
            affected.add(target)
        ordered_new = sorted(
            output.new_mcps,
            key=lambda item: (item.canonical.casefold(), tuple(sorted(item.occurrence_ids))),
        )
        permanent_ids = self.registry.allocate_package_mcp_ids_v3(
            registry_scope_id=registry_scope_id, count=len(ordered_new)
        )
        for value, mcp_id in zip(ordered_new, permanent_ids, strict=True):
            states[mcp_id] = RollingMCPStateV3(
                mcp_id=mcp_id,
                canonical=value.canonical.strip(),
                compressed_description=value.canonical.strip(),
                occurrence_ids=sorted(set(value.occurrence_ids)),
                created_registry_version=version,
                updated_registry_version=version,
            )
            affected.add(mcp_id)
        return sorted(states.values(), key=lambda item: item.mcp_id), redirects, affected

    def _description_packs(
        self,
        inputs: Sequence[PackageV3DescriptionInput],
    ) -> list[list[PackageV3DescriptionInput]]:
        packs: list[list[PackageV3DescriptionInput]] = []
        current: list[PackageV3DescriptionInput] = []
        estimate = 0
        for item in inputs:
            size = len(item.model_dump_json()) // 4
            if current and estimate + size > self.description_pack_token_budget:
                packs.append(current)
                current, estimate = [], 0
            current.append(item)
            estimate += size
        if current:
            packs.append(current)
        return packs

    def _describe(
        self,
        *,
        models: Any,
        registry_scope_id: str,
        batch_id: str,
        mcps: Sequence[RollingMCPStateV3],
        affected: set[str],
        occurrences: Mapping[str, PackageParentOccurrenceV3],
    ) -> list[RollingMCPStateV3]:
        completed = {
            str(item["mcp_id"]): item
            for item in self.registry.list_package_registry_description_tasks_v3(
                registry_scope_id=registry_scope_id, batch_id=batch_id
            )
            if item["status"] == "SUCCEEDED"
        }
        descriptions: dict[str, str] = {}
        pending_inputs: list[PackageV3DescriptionInput] = []
        for mcp in mcps:
            if mcp.mcp_id not in affected:
                descriptions[mcp.mcp_id] = mcp.compressed_description
                continue
            item = PackageV3DescriptionInput(
                mcp_id=mcp.mcp_id,
                canonical=mcp.canonical,
                parent_occurrences=[
                    PackageV3DescriptionOccurrence(
                        occurrence_id=occurrence_id,
                        parent_occurrence=occurrences[occurrence_id].parent_occurrence,
                    )
                    for occurrence_id in mcp.occurrence_ids
                ],
            )
            input_hash = _hash(
                {
                    "contract": PACKAGE_DESCRIPTION_CONTRACT_VERSION,
                    "prompt": _prompt("package_v3_registry_description.md"),
                    "schema": PackageV3DescriptionOutput.model_json_schema(),
                    "model": PACKAGE_V3_MODEL,
                    "reasoning_effort": self.description_reasoning_effort,
                    "mcp": item.model_dump(mode="json"),
                }
            )
            cached = completed.get(mcp.mcp_id)
            if cached is not None and cached["input_hash"] == input_hash:
                payload = cached.get("payload")
                if isinstance(payload, dict) and isinstance(
                    payload.get("compressed_description"), str
                ):
                    descriptions[mcp.mcp_id] = str(payload["compressed_description"])
                    continue
            pending_inputs.append(item)
        packs = self._description_packs(pending_inputs)

        def complete_pack(
            pack: list[PackageV3DescriptionInput],
        ) -> tuple[list[PackageV3DescriptionInput], PackageV3DescriptionOutput]:
            expected = {item.mcp_id for item in pack}
            request = self._model_request(
                prompt=_prompt("package_v3_registry_description.md"),
                payload={"mcps": [item.model_dump(mode="json") for item in pack]},
                schema=PackageV3DescriptionOutput.model_json_schema(),
                schema_name="package_v3_registry_descriptions",
                stage="package_v3_description",
                reasoning_effort=self.description_reasoning_effort,
            )
            output = models.typed_response(
                tier=ModelTier.M2,
                execution_tier=ModelTier.M4,
                stage="package_v3_description",
                request=request,
                output_type=PackageV3DescriptionOutput,
                validator=lambda value, expected=expected: self._validate_descriptions(
                    value, expected
                ),
            )
            return pack, output

        failed_task_records: list[dict[str, Any]] = []

        def complete_pack_resilient(
            pack: list[PackageV3DescriptionInput],
        ) -> tuple[list[PackageV3DescriptionInput], PackageV3DescriptionOutput, str | None]:
            try:
                completed_pack, output = complete_pack(pack)
                return completed_pack, output, None
            except Exception as exc:
                # Description is presentation-only. Keep the canonical parent as a
                # deterministic fallback and leave the task retryable; never block
                # registry finalization on a Node-3 model defect.
                fallback = PackageV3DescriptionOutput(
                    descriptions=[
                        PackageV3Description(
                            mcp_id=item.mcp_id,
                            compressed_description=item.canonical,
                        )
                        for item in pack
                    ]
                )
                error_code = _error_code(exc)
                for item in pack:
                    failed_task_records.append(
                        {
                            "registry_scope_id": registry_scope_id,
                            "batch_id": batch_id,
                            "mcp_id": item.mcp_id,
                            "input_hash": _hash(
                                {
                                    "contract": PACKAGE_DESCRIPTION_CONTRACT_VERSION,
                                    "prompt": _prompt("package_v3_registry_description.md"),
                                    "schema": PackageV3DescriptionOutput.model_json_schema(),
                                    "model": PACKAGE_V3_MODEL,
                                    "reasoning_effort": self.description_reasoning_effort,
                                    "mcp": item.model_dump(mode="json"),
                                }
                            ),
                            "status": "FAILED_RETRYABLE",
                            "payload": {"compressed_description": item.canonical},
                            "error_code": error_code,
                        }
                    )
                return pack, fallback, error_code

        if len(packs) > 1:
            with ThreadPoolExecutor(
                max_workers=min(self.description_active_requests, len(packs))
            ) as executor:
                completed_packs = list(executor.map(complete_pack_resilient, packs))
        else:
            completed_packs = [complete_pack_resilient(pack) for pack in packs]
        task_records: list[dict[str, Any]] = []
        for pack, output, error in completed_packs:
            for description_item in output.descriptions:
                description = description_item.compressed_description.strip()
                descriptions[description_item.mcp_id] = description
                task_input = next(
                    value for value in pack if value.mcp_id == description_item.mcp_id
                )
                if error is not None:
                    continue
                task_records.append(
                    {
                        "registry_scope_id": registry_scope_id,
                        "batch_id": batch_id,
                        "mcp_id": description_item.mcp_id,
                        "input_hash": _hash(
                            {
                                "contract": PACKAGE_DESCRIPTION_CONTRACT_VERSION,
                                "prompt": _prompt("package_v3_registry_description.md"),
                                "schema": PackageV3DescriptionOutput.model_json_schema(),
                                "model": PACKAGE_V3_MODEL,
                                "reasoning_effort": self.description_reasoning_effort,
                                "mcp": task_input.model_dump(mode="json"),
                            }
                        ),
                        "status": "SUCCEEDED",
                        "payload": {"compressed_description": description},
                    }
                )
        self.registry.save_package_registry_description_tasks_v3(
            [*task_records, *failed_task_records], chunk_size=256
        )
        return [
            item.model_copy(update={"compressed_description": descriptions[item.mcp_id]})
            for item in mcps
        ]

    @staticmethod
    def _partition(
        *,
        registry_scope_id: str,
        registry_version: int,
        registry_hash: str,
        mcps: Sequence[RollingMCPStateV3],
        redirects: Mapping[str, str],
        occurrences: Mapping[str, PackageParentOccurrenceV3],
        expected_event_ids: set[str],
        affected_mcp_ids: Sequence[str],
    ) -> tuple[FrozenPackagePartitionV3, dict[str, object]]:
        candidates_by_event: dict[str, list[tuple[str, PackageParentOccurrenceV3]]] = defaultdict(
            list
        )
        for mcp in mcps:
            for occurrence_id in mcp.occurrence_ids:
                occurrence = occurrences[occurrence_id]
                for event_id in occurrence.atomic_event_ids:
                    candidates_by_event[event_id].append((mcp.mcp_id, occurrence))
        missing = expected_event_ids - set(candidates_by_event)
        if missing:
            raise ValueError(f"Package V3 partition is missing Atomics: {sorted(missing)[:20]}")
        winner_by_event: dict[str, str] = {}
        conflict_audit: dict[str, object] = {}
        for event_id, candidates in candidates_by_event.items():
            by_mcp: dict[str, list[PackageParentOccurrenceV3]] = defaultdict(list)
            for mcp_id, occurrence in candidates:
                by_mcp[mcp_id].append(occurrence)
            ranked = sorted(
                by_mcp,
                key=lambda mcp_id: (
                    -len({item.occurrence_id for item in by_mcp[mcp_id]}),
                    -Counter(
                        item.membership_by_event[event_id] for item in by_mcp[mcp_id]
                    ).most_common(1)[0][1],
                    mcp_id,
                ),
            )
            winner_by_event[event_id] = ranked[0]
            if len(ranked) > 1:
                conflict_audit[event_id] = {
                    "candidate_mcp_ids": ranked,
                    "winner_mcp_id": ranked[0],
                    "supporting_occurrence_ids": {
                        key: sorted(item.occurrence_id for item in value)
                        for key, value in by_mcp.items()
                    },
                }
        groups: list[FrozenPackageClusterV3] = []
        proposal_to_mcp: dict[str, str] = {}
        for mcp in mcps:
            mcp_occurrences = [occurrences[item] for item in mcp.occurrence_ids]
            event_ids = sorted(
                event_id for event_id, owner in winner_by_event.items() if owner == mcp.mcp_id
            )
            if not event_ids:
                continue
            relations: dict[str, MembershipRelation] = {}
            for event_id in event_ids:
                values = [
                    item.membership_by_event[event_id]
                    for item in mcp_occurrences
                    if event_id in item.membership_by_event
                ]
                relations[event_id] = sorted(
                    Counter(values).items(), key=lambda item: (-item[1], item[0].value)
                )[0][0]
            scopes = Counter(item.scope for item in mcp_occurrences)
            scope = sorted(scopes.items(), key=lambda item: (-item[1], item[0]))[0][0]
            for occurrence in mcp_occurrences:
                proposal_to_mcp[occurrence.source_proposal_id] = mcp.mcp_id
            groups.append(
                FrozenPackageClusterV3(
                    mcp_id=mcp.mcp_id,
                    canonical=mcp.canonical,
                    compressed_description=mcp.compressed_description,
                    occurrence_ids=mcp.occurrence_ids,
                    atomic_event_ids=event_ids,
                    membership_by_event=relations,
                    document_refs=sorted(
                        {value for item in mcp_occurrences for value in item.document_refs}
                    ),
                    proposal_ids=sorted({item.source_proposal_id for item in mcp_occurrences}),
                    scope=scope,
                )
            )
        external_support: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for occurrence in occurrences.values():
            for raw in occurrence.external_links:
                target = proposal_to_mcp.get(str(raw.get("target_proposal_id", "")))
                source = str(raw.get("source_event_id", ""))
                relation = str(raw.get("relation", ""))
                if not target or winner_by_event.get(source) == target:
                    continue
                external_support[(source, target, relation)].update(occurrence.document_refs)
        external_links = [
            FrozenPackageExternalLinkV3(
                source_event_id=source,
                target_mcp_id=target,
                relation=ExternalRelationType(relation),
                supporting_document_refs=sorted(documents),
            )
            for (source, target, relation), documents in sorted(external_support.items())
        ]
        payload = {
            "registry_scope_id": registry_scope_id,
            "registry_version": registry_version,
            "registry_hash": registry_hash,
            "groups": [item.model_dump(mode="json") for item in groups],
            "external_links": [item.model_dump(mode="json") for item in external_links],
            "mcp_redirects": dict(sorted(redirects.items())),
        }
        partition = FrozenPackagePartitionV3(
            registry_scope_id=registry_scope_id,
            registry_version=registry_version,
            registry_hash=registry_hash,
            partition_hash=_hash(payload),
            groups=groups,
            external_links=external_links,
            affected_mcp_ids=sorted(set(affected_mcp_ids)),
            mcp_redirects=dict(sorted(redirects.items())),
        )
        return partition, {
            "cross_mcp_atomic_conflict_count": len(conflict_audit),
            "cross_mcp_atomic_conflicts": conflict_audit,
            "empty_mcp_projection_count": len(mcps) - len(groups),
        }

    def run(
        self,
        *,
        events: Sequence[AtomicEvent],
        proposals: Sequence[ParentProposalCard],
        external_links: Sequence[ParentExternalLinkProposal],
        models: Any,
        run_id: str,
        registry_scope_id: str,
    ) -> PackageWorkflowV3Result:
        started = perf_counter()
        records = self._occurrence_records(
            registry=self.registry,
            registry_scope_id=registry_scope_id,
            proposals=proposals,
            external_links=external_links,
        )
        stored = self.registry.upsert_package_parent_occurrences_v3(
            registry_scope_id=registry_scope_id, records=records
        )
        occurrences = {
            item.occurrence_id: item
            for item in (
                PackageParentOccurrenceV3.model_validate(value)
                for value in self.registry.list_package_parent_occurrences_v3(
                    registry_scope_id=registry_scope_id
                )
            )
        }
        current_payload = self.registry.get_package_registry_v3(
            registry_scope_id=registry_scope_id
        ) or {
            "registry_scope_id": registry_scope_id,
            "registry_version": 0,
            "registry_hash": "",
            "mcps": [],
            "redirects": {},
        }
        current_mcps = [
            RollingMCPStateV3.model_validate(item) for item in current_payload.get("mcps", [])
        ]
        redirects = {str(k): str(v) for k, v in dict(current_payload.get("redirects", {})).items()}
        assigned = {item for mcp in current_mcps for item in mcp.occurrence_ids}
        pending = sorted(
            (item for item in occurrences.values() if item.occurrence_id not in assigned),
            key=lambda item: (
                item.published_at,
                item.document_fingerprint,
                item.occurrence_business_key,
                item.occurrence_id,
            ),
        )
        failures: list[PackageWorkflowV3Failure] = []
        batch_telemetry: list[dict[str, object]] = []
        last_affected = {str(item) for item in current_payload.get("affected_mcp_ids", [])}
        while pending:
            base_version = int(current_payload.get("registry_version", 0))
            base_hash = str(current_payload.get("registry_hash", ""))
            cards = [
                PackageV3RegistryCard(
                    mcp_id=item.mcp_id,
                    canonical=item.canonical,
                    description=item.compressed_description,
                )
                for item in current_mcps
            ]
            try:
                batch = self._fit_batch(pending, cards)
            except ValueError as exc:
                failures.append(
                    PackageWorkflowV3Failure(
                        stage="PACKAGE_V3_PLAN",
                        batch_id="unplanned",
                        error_code=str(exc),
                    )
                )
                break
            input_material = {
                "contract": PACKAGE_WORKFLOW_CONTRACT_VERSION,
                "policy": PACKAGE_REGISTRY_POLICY_VERSION,
                "model": PACKAGE_V3_MODEL,
                "reasoning_effort": PACKAGE_V3_REASONING_EFFORT,
                "base_registry_hash": base_hash,
                "occurrences": [item.model_dump(mode="json") for item in batch],
            }
            input_hash = _hash(input_material)
            batch_id = f"PVB-{input_hash[:20]}"
            persisted = self.registry.get_package_registry_batch_v3(
                registry_scope_id=registry_scope_id, batch_id=batch_id
            )
            staged: dict[str, Any] | None = None
            if persisted is not None and persisted["status"] in {
                "CLUSTERING_SUCCEEDED",
                "DESCRIPTIONS_PARTIAL",
                "DESCRIPTIONS_SUCCEEDED",
            }:
                raw_staged = persisted.get("staged_changes")
                if isinstance(raw_staged, dict):
                    staged = raw_staged
            try:
                if staged is None:
                    if base_version == 0:
                        request = self._model_request(
                            prompt=_prompt("package_v3_initial_clustering.md"),
                            payload={
                                "parent_occurrences": [
                                    PackageV3OccurrenceInput(
                                        occurrence_id=item.occurrence_id,
                                        parent_occurrence=item.parent_occurrence,
                                    ).model_dump(mode="json")
                                    for item in batch
                                ]
                            },
                            schema=PackageV3InitialClusteringOutput.model_json_schema(),
                            schema_name="package_v3_initial_clustering",
                            stage="package_v3_initial_clustering",
                            reasoning_effort=self.reasoning_effort,
                        )
                        expected_occurrence_ids = {item.occurrence_id for item in batch}
                        labels = {item.occurrence_id: item.parent_occurrence for item in batch}
                        normalization_actions: list[dict[str, object]] = []

                        def normalize_initial(
                            value: PackageV3InitialClusteringOutput,
                            expected: set[str] = expected_occurrence_ids,
                            current_labels: Mapping[str, str] = labels,
                            action_log: list[dict[str, object]] = normalization_actions,
                        ) -> None:
                            self._normalize_initial_inplace(
                                value,
                                expected_occurrence_ids=expected,
                                labels=current_labels,
                                actions=action_log,
                            )

                        output = models.typed_response(
                            tier=ModelTier.M3,
                            execution_tier=ModelTier.M4,
                            stage="package_v3_initial_clustering",
                            request=request,
                            output_type=PackageV3InitialClusteringOutput,
                            validator=normalize_initial,
                        )
                        staged_mcps, staged_redirects, affected = self._initial_state(
                            output=output,
                            registry_scope_id=registry_scope_id,
                            version=base_version + 1,
                        )
                        response_payload = output.model_dump(mode="json")
                    else:
                        request = self._model_request(
                            prompt=_prompt("package_v3_rolling_clustering.md"),
                            payload={
                                "registry": [item.model_dump(mode="json") for item in cards],
                                "new_parent_occurrences": [
                                    PackageV3OccurrenceInput(
                                        occurrence_id=item.occurrence_id,
                                        parent_occurrence=item.parent_occurrence,
                                    ).model_dump(mode="json")
                                    for item in batch
                                ],
                            },
                            schema=PackageV3RollingClusteringOutput.model_json_schema(),
                            schema_name="package_v3_rolling_clustering",
                            stage="package_v3_rolling_clustering",
                            reasoning_effort=self.reasoning_effort,
                        )
                        expected_occurrence_ids = {item.occurrence_id for item in batch}
                        active_mcp_ids = {item.mcp_id for item in current_mcps}
                        labels = {item.occurrence_id: item.parent_occurrence for item in batch}
                        normalization_actions = []

                        def normalize_rolling(
                            value: PackageV3RollingClusteringOutput,
                            expected: set[str] = expected_occurrence_ids,
                            active: set[str] = active_mcp_ids,
                            current_labels: Mapping[str, str] = labels,
                            action_log: list[dict[str, object]] = normalization_actions,
                        ) -> None:
                            self._normalize_rolling_inplace(
                                value,
                                expected_occurrence_ids=expected,
                                active_mcp_ids=active,
                                labels=current_labels,
                                actions=action_log,
                            )

                        output = models.typed_response(
                            tier=ModelTier.M3,
                            execution_tier=ModelTier.M4,
                            stage="package_v3_rolling_clustering",
                            request=request,
                            output_type=PackageV3RollingClusteringOutput,
                            validator=normalize_rolling,
                        )
                        staged_mcps, staged_redirects, affected = self._rolling_state(
                            output=output,
                            current=current_mcps,
                            current_redirects=redirects,
                            registry_scope_id=registry_scope_id,
                            version=base_version + 1,
                        )
                        response_payload = output.model_dump(mode="json")
                    if normalization_actions:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=f"package-v3-normalization:{run_id}:{batch_id}",
                                run_id=run_id,
                                decision_type="PACKAGE_V3_NORMALIZATION",
                                subject_id=batch_id,
                                payload={"actions": normalization_actions},
                            )
                        )
                    staged = {
                        "mcps": [item.model_dump(mode="json") for item in staged_mcps],
                        "redirects": dict(sorted(staged_redirects.items())),
                        "affected_mcp_ids": sorted(affected),
                    }
                    self.registry.save_package_registry_batch_v3(
                        registry_scope_id=registry_scope_id,
                        batch_id=batch_id,
                        base_registry_version=base_version,
                        input_hash=input_hash,
                        status="CLUSTERING_SUCCEEDED",
                        response_a_payload=response_payload,
                        staged_changes=staged,
                        affected_mcp_ids=sorted(affected),
                    )
                staged_mcps = [RollingMCPStateV3.model_validate(item) for item in staged["mcps"]]
                staged_redirects = {
                    str(k): str(v) for k, v in dict(staged.get("redirects", {})).items()
                }
                affected = {str(item) for item in staged.get("affected_mcp_ids", [])}
                described = self._describe(
                    models=models,
                    registry_scope_id=registry_scope_id,
                    batch_id=batch_id,
                    mcps=staged_mcps,
                    affected=affected,
                    occurrences=occurrences,
                )
                snapshot = {
                    "registry_scope_id": registry_scope_id,
                    "registry_version": base_version + 1,
                    "status": "FINALIZED",
                    "mcps": [item.model_dump(mode="json") for item in described],
                    "redirects": dict(sorted(staged_redirects.items())),
                    "affected_mcp_ids": sorted(affected),
                }
                registry_hash = _hash(
                    {
                        "contract": PACKAGE_WORKFLOW_CONTRACT_VERSION,
                        "policy": PACKAGE_REGISTRY_POLICY_VERSION,
                        "snapshot": snapshot,
                    }
                )
                snapshot["registry_hash"] = registry_hash
                self.registry.finalize_package_registry_batch_v3(
                    registry_scope_id=registry_scope_id,
                    batch_id=batch_id,
                    expected_base_version=base_version,
                    expected_base_hash=base_hash,
                    registry_version=base_version + 1,
                    registry_hash=registry_hash,
                    snapshot=snapshot,
                )
                current_payload = snapshot
                current_mcps = described
                redirects = staged_redirects
                last_affected = affected
                batch_telemetry.append(
                    {
                        "batch_id": batch_id,
                        "base_registry_version": base_version,
                        "final_registry_version": base_version + 1,
                        "new_occurrence_count": len(batch),
                        "registry_mcp_count": len(described),
                        "affected_mcp_count": len(affected),
                        "complete_occurrence_coverage": True,
                    }
                )
                assigned.update(item.occurrence_id for item in batch)
                pending = [item for item in pending if item.occurrence_id not in assigned]
            except Exception as exc:
                error_code = _error_code(exc)
                self.registry.save_package_registry_batch_v3(
                    registry_scope_id=registry_scope_id,
                    batch_id=batch_id,
                    base_registry_version=base_version,
                    input_hash=input_hash,
                    status=("DESCRIPTIONS_PARTIAL" if staged is not None else "FAILED_RETRYABLE"),
                    staged_changes=staged,
                    affected_mcp_ids=(staged.get("affected_mcp_ids", []) if staged else []),
                    error_code=error_code,
                )
                failures.append(
                    PackageWorkflowV3Failure(
                        stage=(
                            "PACKAGE_V3_DESCRIPTION"
                            if staged is not None
                            else "PACKAGE_V3_CLUSTERING"
                        ),
                        batch_id=batch_id,
                        error_code=error_code,
                    )
                )
                break
        if failures:
            return PackageWorkflowV3Result(
                status="PARTIAL_PACKAGE_REGISTRY",
                failures=failures,
                telemetry={
                    "parent_occurrence_count": len(stored),
                    "registry_batch_count": len(batch_telemetry),
                    "batches": batch_telemetry,
                    "package_stage_wall_ms": round((perf_counter() - started) * 1000),
                },
            )
        registry_hash = str(current_payload["registry_hash"])
        partition, dedup_telemetry = self._partition(
            registry_scope_id=registry_scope_id,
            registry_version=int(current_payload["registry_version"]),
            registry_hash=registry_hash,
            mcps=current_mcps,
            redirects=redirects,
            occurrences=occurrences,
            expected_event_ids={item.event_id for item in events},
            affected_mcp_ids=sorted(last_affected),
        )
        audit = self.registry.append_decision_audits(
            [
                DecisionAuditRecord(
                    audit_id=f"package-v3-partition:{run_id}:{partition.partition_hash[:20]}",
                    run_id=run_id,
                    decision_type="PACKAGE_V3_FINAL_PARTITION",
                    subject_id=partition.partition_hash,
                    payload={
                        "registry_scope_id": registry_scope_id,
                        "registry_version": partition.registry_version,
                        "registry_hash": registry_hash,
                        "partition_hash": partition.partition_hash,
                        "package_count": len(partition.groups),
                        "atomic_count": len(
                            {
                                event
                                for group in partition.groups
                                for event in group.atomic_event_ids
                            }
                        ),
                        **dedup_telemetry,
                    },
                )
            ]
        )
        if int(audit.get("degraded", 0)):
            raise RuntimeError("PACKAGE_V3_AUDIT_DEGRADED")
        return PackageWorkflowV3Result(
            status="FINALIZED",
            partition=partition,
            telemetry={
                "parent_occurrence_count": len(stored),
                "registry_batch_count": len(batch_telemetry),
                "batches": batch_telemetry,
                "registry_version": partition.registry_version,
                "registry_hash": registry_hash,
                "partition_hash": partition.partition_hash,
                "package_count": len(partition.groups),
                **dedup_telemetry,
                "package_stage_wall_ms": round((perf_counter() - started) * 1000),
            },
        )
