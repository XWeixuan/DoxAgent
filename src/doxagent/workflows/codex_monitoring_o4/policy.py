"""O4-only canonicalization and delivery policies.

The generic Message Bus and Crawler Plane services intentionally remain
configurable. These rules apply only while the signed O4 operations boundary
still has reliable agent provenance.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import (
    DeliveryCheckpoint,
    DeliveryItemStatus,
    DeliveryProgressState,
    DeliveryWorkItemCheckpoint,
    MonitoringConfigurationPlan,
    SourceNeedPriority,
    SourceNeedResolution,
)

TIKHUB_ADAPTERS = frozenset({"builtin:tikhub_x_search", "builtin:tikhub_x_user_posts"})
TIKHUB_USER_POSTS_ADAPTER = "builtin:tikhub_x_user_posts"
TERMINAL_DELIVERY_STATUSES = frozenset(
    {
        DeliveryItemStatus.COMPLETED,
        DeliveryItemStatus.FAILED,
        DeliveryItemStatus.REPLAN_REQUIRED,
        DeliveryItemStatus.HUMAN_INTERVENTION_REQUIRED,
    }
)


@dataclass(frozen=True)
class O4MutationPolicy:
    standard_poll_seconds: int = 60
    tikhub_poll_seconds: int = 600
    alert_after_seconds: int = 1800
    tikhub_user_account_cap: int = 2

    def polling(self, adapter_ref: str, current: object = None) -> dict[str, Any]:
        value = dict(current) if isinstance(current, Mapping) else {}
        value["target_interval_seconds"] = (
            self.tikhub_poll_seconds
            if adapter_ref in TIKHUB_ADAPTERS
            else self.standard_poll_seconds
        )
        value["alert_after_seconds"] = self.alert_after_seconds
        return value

    def canonicalize_source_registration(self, value: Mapping[str, Any]) -> dict[str, Any]:
        output = dict(value)
        output["default_polling_config"] = self.polling(
            str(output.get("adapter_ref", "")), output.get("default_polling_config")
        )
        return output

    def canonicalize_source_update(
        self,
        value: Mapping[str, Any],
        *,
        current_source: Mapping[str, Any],
    ) -> dict[str, Any]:
        output = dict(value)
        nested = output.get("patch")
        patch = (
            dict(nested)
            if isinstance(nested, Mapping)
            else {
                key: item
                for key, item in output.items()
                if key not in {"source_id", "reason", "binding_patches"}
            }
        )
        if "default_polling_config" not in patch:
            return output
        adapter_ref = str(patch.get("adapter_ref", current_source.get("adapter_ref", "")))
        patch["default_polling_config"] = self.polling(
            adapter_ref, patch.get("default_polling_config")
        )
        if isinstance(nested, Mapping):
            output["patch"] = patch
        else:
            output.update(patch)
        return output

    def canonicalize_binding(
        self,
        value: Mapping[str, Any],
        *,
        source: Mapping[str, Any],
        existing_binding: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        output = dict(value)
        base = (
            existing_binding.get("polling")
            if existing_binding is not None
            else source.get("default_polling_config")
        )
        requested = output.get("polling")
        merged = dict(base) if isinstance(base, Mapping) else {}
        if isinstance(requested, Mapping):
            merged.update(requested)
        output["polling"] = self.polling(str(source.get("adapter_ref", "")), merged)
        return output

    def canonicalize_profile(
        self,
        value: Mapping[str, Any],
        *,
        source_loader: Callable[[str], Mapping[str, Any]],
    ) -> dict[str, Any]:
        output = dict(value)
        entries: list[dict[str, Any]] = []
        usernames: set[str] = set()
        raw_entries = output.get("entries", [])
        if not isinstance(raw_entries, list):
            return output
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, Mapping):
                entries.append(dict(raw_entry))
                continue
            entry = dict(raw_entry)
            source = source_loader(str(entry.get("source_id", "")))
            entry["polling"] = self.polling(
                str(source.get("adapter_ref", "")), entry.get("polling")
            )
            entries.append(entry)
            if source.get("adapter_ref") == TIKHUB_USER_POSTS_ADAPTER:
                usernames.update(_usernames(entry.get("source_parameters")))
        self._validate_account_cap(usernames)
        output["entries"] = entries
        return output

    def validate_ticker_account_cap(
        self,
        *,
        bindings: Sequence[Mapping[str, Any]],
        source_loader: Callable[[str], Mapping[str, Any]],
    ) -> None:
        usernames: set[str] = set()
        for binding in bindings:
            if not bool(binding.get("enabled", True)):
                continue
            source = source_loader(str(binding.get("source_id", "")))
            if source.get("adapter_ref") == TIKHUB_USER_POSTS_ADAPTER:
                usernames.update(_usernames(binding.get("source_parameters")))
        self._validate_account_cap(usernames)

    def _validate_account_cap(self, usernames: set[str]) -> None:
        if len(usernames) > self.tikhub_user_account_cap:
            raise ValueError(
                "O4 may monitor at most "
                f"{self.tikhub_user_account_cap} TikHub user-post accounts per ticker/profile; "
                f"got {len(usernames)}: {', '.join(sorted(usernames))}"
            )


class O4CrawlerPromotionPolicy:
    error_message = (
        "O4 crawler delivery requires content-level observation assertions before promotion."
    )

    @classmethod
    def validate(cls, package_path: str | Path) -> None:
        cases_path = Path(package_path) / "tests" / "cases.json"
        try:
            cases = json.loads(cases_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(cls.error_message) from exc
        if not isinstance(cases, list):
            raise ValueError(cls.error_message)
        for case in cases:
            if not isinstance(case, Mapping):
                continue
            assertions = case.get("observation_assertions")
            if not isinstance(assertions, list):
                continue
            for assertion in assertions:
                if not isinstance(assertion, Mapping) or not assertion.get("external_id"):
                    continue
                if (
                    assertion.get("body_contains")
                    or assertion.get("body_min_length") is not None
                    or bool(assertion.get("body_forbidden_patterns"))
                ):
                    return
        raise ValueError(cls.error_message)


class O4PlanFinalizer:
    def __init__(self, mutation_policy: O4MutationPolicy) -> None:
        self._mutation_policy = mutation_policy

    def finalize(
        self,
        plan: MonitoringConfigurationPlan,
        *,
        source_loader=None,
        binding_loader=None,
    ) -> MonitoringConfigurationPlan:
        items = []
        omissions = list(plan.deliberate_omissions)
        for item in plan.source_needs:
            try:
                result = self._finalize_one(
                    plan.model_copy(update={"source_needs": [item]}),
                    source_loader=source_loader,
                    binding_loader=binding_loader,
                )
                items.extend(result.source_needs)
            except (ValueError, KeyError) as exc:
                from doxagent.codex_runtime.recovery import bounded_text

                omissions.append(f"{item.source_need_id}: {bounded_text(exc, 1000)}")
        return plan.model_copy(update={"source_needs": items, "deliberate_omissions": omissions})

    def _finalize_one(
        self,
        plan: MonitoringConfigurationPlan,
        *,
        source_loader: Callable[[str], Mapping[str, Any]] | None = None,
        binding_loader: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> MonitoringConfigurationPlan:
        items = []
        has_plan_admission_evidence = bool(plan.admission_evidence)
        for item in plan.source_needs:
            desired = dict(item.desired_binding)
            if desired:
                source: Mapping[str, Any] = {}
                source_id = item.existing_source_id or (
                    str(desired.get("source_id", ""))
                    if item.resolution is SourceNeedResolution.CONFIGURE_REGISTERED_SOURCE
                    else ""
                )
                if source_id and source_loader is not None:
                    source = source_loader(source_id)
                adapter_ref = str(source.get("adapter_ref", ""))
                desired["polling"] = self._mutation_policy.polling(
                    adapter_ref, desired.get("polling")
                )
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED:
                candidates = [item.primary_candidate, *item.alternative_candidates]
                if any(candidate is None or not candidate.evidence for candidate in candidates):
                    raise ValueError(
                        f"new crawler Source Need lacks admission evidence: {item.source_need_id}"
                    )
            parameters = desired.get("source_parameters")
            if (
                item.resolution
                in {
                    SourceNeedResolution.CONFIGURE_REGISTERED_SOURCE,
                    SourceNeedResolution.ENABLE_EXISTING_CRAWLER,
                }
                and isinstance(parameters, Mapping)
                and any(
                    parameters.get(key)
                    for key in ("usernames", "rss_urls", "search_terms", "keywords")
                )
                and not has_plan_admission_evidence
            ):
                raise ValueError(
                    f"new monitored capability lacks admission evidence: {item.source_need_id}"
                )
            if (
                item.resolution
                in {
                    SourceNeedResolution.CONFIGURE_REGISTERED_SOURCE,
                    SourceNeedResolution.ENABLE_EXISTING_CRAWLER,
                }
                and binding_loader is not None
            ):
                if not item.existing_source_id:
                    raise ValueError(
                        f"applied existing capability lacks source_id: {item.source_need_id}"
                    )
                binding = binding_loader(item.existing_source_id)
                if binding is None or not _mapping_contains(binding, desired):
                    raise ValueError(
                        "configuration Plan does not match reread ticker binding: "
                        f"{item.source_need_id}"
                    )
            items.append(
                item.model_copy(
                    update={
                        "priority": SourceNeedPriority.NORMAL,
                        "desired_binding": desired,
                    }
                )
            )
        return plan.model_copy(update={"source_needs": items})


class DeliveryProgressCoordinator:
    """Validate and canonicalize one turn-boundary progressive checkpoint."""

    STALL_LIMIT = 4

    def commit(
        self,
        *,
        plan: MonitoringConfigurationPlan,
        previous: DeliveryCheckpoint | None,
        submitted: DeliveryCheckpoint,
    ) -> DeliveryCheckpoint:
        if (submitted.plan_id, submitted.plan_version, submitted.ticker) != (
            plan.plan_id,
            plan.plan_version,
            plan.ticker,
        ):
            raise ValueError("delivery checkpoint differs from immutable plan identity")
        expected_items = [
            item
            for item in plan.source_needs
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
        ]
        previous_by_id = {
            item.source_need_id: item for item in (previous.items if previous else [])
        }
        items = []
        submitted_by_id = {}
        for item in submitted.items:
            submitted_by_id.setdefault(item.source_need_id, item)
        for need in expected_items:
            prior = previous_by_id.get(need.source_need_id)
            item = submitted_by_id.get(need.source_need_id) or prior
            if item is None:
                item = DeliveryWorkItemCheckpoint(
                    source_need_id=need.source_need_id,
                    status=DeliveryItemStatus.REPLAN_REQUIRED,
                    last_failure="checkpoint item missing",
                )
            try:
                item = self._commit_item(plan_item=need, previous=prior, submitted=item)
            except ValueError as exc:
                item = prior or item.model_copy(
                    update={
                        "status": DeliveryItemStatus.REPLAN_REQUIRED,
                        "last_failure": str(exc)[:1000],
                        "candidate_id": None,
                    }
                )
            items.append(item)
        return submitted.model_copy(update={"items": items})

    def _commit_item(
        self,
        *,
        plan_item: Any,
        previous: DeliveryWorkItemCheckpoint | None,
        submitted: DeliveryWorkItemCheckpoint,
    ) -> DeliveryWorkItemCheckpoint:
        candidates = [plan_item.primary_candidate, *plan_item.alternative_candidates]
        candidate_ids = [item.candidate_id for item in candidates if item is not None]
        current_id = submitted.candidate_id or (candidate_ids[0] if candidate_ids else None)
        if current_id is not None and current_id not in candidate_ids:
            raise ValueError(f"checkpoint candidate is outside immutable Plan: {current_id}")
        prior = previous or DeliveryWorkItemCheckpoint(
            source_need_id=submitted.source_need_id,
            candidate_id=current_id,
        )
        exhausted = list(
            dict.fromkeys([*prior.exhausted_candidate_ids, *submitted.exhausted_candidate_ids])
        )
        stage = submitted.delivery_stage or submitted.stage
        updates: dict[str, Any] = {
            "candidate_id": current_id,
            "stage": stage,
            "delivery_stage": stage,
            "exhausted_candidate_ids": exhausted,
        }
        if submitted.status in TERMINAL_DELIVERY_STATUSES and (
            submitted.status is not DeliveryItemStatus.FAILED
        ):
            return submitted.model_copy(update=updates)
        terminal_failed = submitted.status is DeliveryItemStatus.FAILED
        state = submitted.progress_state
        if state is DeliveryProgressState.PROGRESSING:
            if self._verified_progress(prior, submitted):
                updates["consecutive_stalled_cycles"] = 0
            else:
                state = DeliveryProgressState.STALLED
        if state is DeliveryProgressState.STALLED:
            updates["progress_state"] = state
            updates["consecutive_stalled_cycles"] = prior.consecutive_stalled_cycles + 1
            if updates["consecutive_stalled_cycles"] >= self.STALL_LIMIT:
                advanced = self._advance_candidate(
                    submitted, candidate_ids, current_id, exhausted, updates
                )
                if terminal_failed and advanced.delivery_stage != "ALL_CANDIDATES_EXHAUSTED":
                    raise ValueError("delivery FAILED while an approved alternative remains")
                return advanced
        elif state is DeliveryProgressState.INFEASIBLE:
            advanced = self._advance_candidate(
                submitted, candidate_ids, current_id, exhausted, updates
            )
            if terminal_failed and advanced.delivery_stage != "ALL_CANDIDATES_EXHAUSTED":
                raise ValueError("delivery FAILED while an approved alternative remains")
            return advanced
        else:
            updates["progress_state"] = DeliveryProgressState.PROGRESSING
        if terminal_failed:
            raise ValueError(
                "delivery FAILED requires INFEASIBLE evidence or four consecutive STALLED cycles"
            )
        return submitted.model_copy(update=updates)

    @staticmethod
    def _verified_progress(
        previous: DeliveryWorkItemCheckpoint,
        submitted: DeliveryWorkItemCheckpoint,
    ) -> bool:
        if _stage_rank(submitted.delivery_stage or submitted.stage) > _stage_rank(
            previous.delivery_stage or previous.stage
        ):
            return True
        has_new_evidence = bool(
            (
                submitted.latest_execution_id
                and submitted.latest_execution_id != previous.latest_execution_id
            )
            or set(submitted.latest_evidence_refs) - set(previous.latest_evidence_refs)
        )
        if not has_new_evidence:
            return False
        blocker_moved = bool(
            submitted.latest_blocker and submitted.latest_blocker != previous.latest_blocker
        )
        hypothesis_changed = bool(
            submitted.next_hypothesis and submitted.next_hypothesis != previous.next_hypothesis
        )
        return blocker_moved or hypothesis_changed

    @staticmethod
    def _advance_candidate(
        submitted: DeliveryWorkItemCheckpoint,
        candidate_ids: list[str],
        current_id: str | None,
        exhausted: list[str],
        updates: dict[str, Any],
    ) -> DeliveryWorkItemCheckpoint:
        if current_id and current_id not in exhausted:
            exhausted.append(current_id)
        next_candidate = next((item for item in candidate_ids if item not in exhausted), None)
        if next_candidate is None:
            updates.update(
                exhausted_candidate_ids=exhausted,
                consecutive_stalled_cycles=0,
                delivery_stage="ALL_CANDIDATES_EXHAUSTED",
                stage="ALL_CANDIDATES_EXHAUSTED",
            )
        else:
            updates.update(
                candidate_id=next_candidate,
                exhausted_candidate_ids=exhausted,
                progress_state=DeliveryProgressState.PROGRESSING,
                consecutive_stalled_cycles=0,
                delivery_stage="NOT_STARTED",
                stage="NOT_STARTED",
                previous_blocker=submitted.latest_blocker or submitted.last_failure,
                latest_blocker=None,
                latest_execution_id=None,
                latest_evidence_refs=[],
            )
        return submitted.model_copy(update=updates)


def _usernames(value: object) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    raw = value.get("usernames", [])
    if not isinstance(raw, list):
        return set()
    return {str(item).strip().casefold() for item in raw if str(item).strip()}


def _stage_rank(value: str) -> int:
    normalized = value.strip().upper()
    ordered = (
        "NOT_STARTED",
        "WORKING",
        "LIVE",
        "CERTIFIED",
        "ACTIVE",
        "REGISTERED",
        "BOUND",
        "SETTLED",
    )
    return next((index for index, stage in enumerate(ordered) if stage in normalized), 0)


def _mapping_contains(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key in {"ticker", "source_id", "binding_id"}:
            continue
        actual_value = actual.get(key)
        if isinstance(expected_value, Mapping):
            if not isinstance(actual_value, Mapping) or not _mapping_contains(
                actual_value, expected_value
            ):
                return False
        elif actual_value != expected_value:
            return False
    return True


__all__ = [
    "DeliveryProgressCoordinator",
    "O4CrawlerPromotionPolicy",
    "O4MutationPolicy",
    "O4PlanFinalizer",
    "TERMINAL_DELIVERY_STATUSES",
    "TIKHUB_ADAPTERS",
    "TIKHUB_USER_POSTS_ADAPTER",
]
