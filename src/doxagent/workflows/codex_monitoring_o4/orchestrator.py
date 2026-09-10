"""O4 node orchestration with bounded delivery and non-blocking bus startup."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, cast

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service

from .policy import (
    TERMINAL_DELIVERY_STATUSES,
    O4MutationPolicy,
    O4PlanFinalizer,
)
from .repository import MonitoringO4Repository
from .runner import O4Runner, O4TurnInterrupted
from .schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliveryItemStatus,
    DeliverySettlement,
    DeliveryWorkItemCheckpoint,
    MonitoringConfigurationPlan,
    O4Request,
    O4RequestStatus,
    O4RunResult,
    RepairFinalStatus,
    RepairSettlement,
    RepairTrigger,
    SourceNeedResolution,
    new_id,
)


class O4ConfigurationContextProvider(Protocol):
    def configuration_payload(self, ticker: str) -> dict[str, Any]: ...


class MonitoringO4Orchestrator:
    def __init__(
        self,
        *,
        repository: MonitoringO4Repository,
        runner: O4Runner,
        message_bus: MessageBusV2Service | None,
        message_bus_enabled: bool,
        context_provider: O4ConfigurationContextProvider | None = None,
        mutation_policy: O4MutationPolicy | None = None,
        initialization_only: bool = False,
    ) -> None:
        self.repository = repository
        self.initialization_only = initialization_only
        self.runner = runner
        self.message_bus = message_bus
        self.message_bus_enabled = message_bus_enabled
        self.context_provider = context_provider
        self.mutation_policy = mutation_policy or O4MutationPolicy()
        self.plan_finalizer = O4PlanFinalizer(self.mutation_policy)

    def submit_configure(
        self,
        *,
        ticker: str,
        policy_set: dict[str, Any],
        document2: dict[str, Any],
        reason: str = "document3_published",
        initialization_id: str | None = None,
        execution_id: str | None = None,
    ) -> O4Request:
        if self.initialization_only and not initialization_id:
            raise ValueError("O4 tasks require a ticker initialization operation")
        normalized = ticker.upper()
        version = int(policy_set["policy_set_version"])
        digest = hashlib.sha256(
            json.dumps(
                policy_set, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        return self.repository.enqueue(
            O4Request(
                request_id=f"init-o4-{execution_id}" if execution_id else new_id("o4_request"),
                ticker=normalized,
                node=CodexMonitoringO4Node.CONFIGURE,
                payload={
                    "policy_set_json": policy_set,
                    "document2_json": document2,
                    "policy_set_sha256": digest,
                    "reason": reason,
                },
                dedupe_key=(
                    f"configure:{normalized}:{version}:{digest}"
                    + (f":init:{initialization_id}" if initialization_id else "")
                    + (f":execution:{execution_id}" if execution_id else "")
                ),
                initialization_id=initialization_id,
            )
        )

    def submit_repair(self, *, ticker: str, trigger: RepairTrigger) -> O4Request:
        if self.initialization_only:
            raise ValueError("Runtime O4 REPAIR is disabled")
        return self.repository.enqueue(
            O4Request(
                ticker=ticker,
                node=CodexMonitoringO4Node.REPAIR,
                payload={"trigger_json": trigger.model_dump(mode="json")},
                dedupe_key=(
                    f"repair:{trigger.trigger_type}:{trigger.alert_id}:{trigger.repeat_count}"
                ),
            )
        )

    async def process_next(self) -> O4RunResult | None:
        request = self.repository.next_pending()
        return await self.process(request) if request is not None else None

    async def process(self, request: O4Request) -> O4RunResult:
        if self.initialization_only and not request.initialization_id:
            request = request.model_copy(update={"status": O4RequestStatus.HELD})
            self.repository.save_request(request)
            return O4RunResult(request=request, degraded_reasons=["initialization_only"])
        if request.initialization_id and request.node is CodexMonitoringO4Node.REPAIR:
            raise ValueError("initialization cannot execute O4 REPAIR")
        if not self.repository.acquire_ticker_lease(request.ticker, request.request_id):
            return O4RunResult(
                request=request,
                degraded_reasons=["ticker already has an active O4 turn"],
            )
        try:
            request.status = O4RequestStatus.RUNNING
            self.repository.save_request(request)
            if request.node is CodexMonitoringO4Node.CONFIGURE:
                return await self._configure(request)
            if request.node is CodexMonitoringO4Node.DELIVER:
                return await self._deliver(
                    request, start_monitoring=request.initialization_id is None
                )
            return await self._repair(request)
        except Exception as exc:
            request.status = O4RequestStatus.FAILED
            request.error = str(exc)[:4000]
            if request.initialization_id:
                self.repository.save_request(request)
                return O4RunResult(request=request, degraded_reasons=[str(exc)])
            if request.node in {
                CodexMonitoringO4Node.CONFIGURE,
                CodexMonitoringO4Node.DELIVER,
            }:
                started, reason = self._start_monitoring(request.ticker)
                request.status = O4RequestStatus.DEGRADED
                self.repository.save_request(request)
                return O4RunResult(
                    request=request,
                    monitoring_started=started,
                    degraded_reasons=[str(exc), *([reason] if reason else [])],
                )
            self.repository.save_request(request)
            return O4RunResult(request=request, degraded_reasons=[str(exc)])
        finally:
            self.repository.release_ticker_lease(request.ticker, request.request_id)

    async def _configure(self, request: O4Request) -> O4RunResult:
        policy = request.payload.get("policy_set_json", {})
        if policy and policy.get("policies") == []:
            from .schema import utc_now

            result = ConfigureCompletion(
                request_id=request.request_id,
                plan=MonitoringConfigurationPlan(
                    ticker=request.ticker,
                    policy_set_id=policy.get("policy_set_id") or request.request_id,
                    policy_set_version=policy["policy_set_version"],
                    policy_set_sha256=request.payload["policy_set_sha256"],
                    document2_ref=str(policy.get("document2_ref") or ""),
                    baseline_observed_at=utc_now(),
                    baseline_summary={},
                    source_needs=[],
                    stopping_rationale=(
                        "No policies require dedicated monitoring; existing baseline retained"
                    ),
                ),
            )
        else:
            result = cast(ConfigureCompletion, await self.runner.run(request))
        message_bus = self.message_bus

        def load_binding(source_id: str) -> dict[str, Any] | None:
            if message_bus is None:
                return None
            binding = message_bus.repository.get_binding(f"{request.ticker}:{source_id}")
            return binding.model_dump(mode="json") if binding is not None else None

        plan = self.plan_finalizer.finalize(
            result.plan,
            source_loader=(
                None
                if message_bus is None
                else lambda source_id: message_bus.require_source(source_id).model_dump(mode="json")
            ),
            binding_loader=None if message_bus is None else load_binding,
        )
        result = result.model_copy(update={"plan": plan})
        self._validate_plan_against_input(plan, request.payload)
        self.repository.save_plan(plan)
        request.payload["configuration_plan_ref"] = {
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
        }
        self.repository.save_request(request)
        new_items = [
            item
            for item in plan.source_needs
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
        ]
        if not new_items:
            if request.initialization_id:
                request.status = O4RequestStatus.SUCCEEDED
                self.repository.save_request(request)
                return O4RunResult(request=request, plan=result.plan)
            started, reason = self._start_monitoring(request.ticker)
            request.status = O4RequestStatus.SUCCEEDED if started else O4RequestStatus.DEGRADED
            self.repository.save_request(request)
            return O4RunResult(
                request=request,
                plan=result.plan,
                monitoring_started=started,
                degraded_reasons=[reason] if reason else [],
            )
        delivery_request = self.repository.enqueue(
            O4Request(
                ticker=request.ticker,
                node=CodexMonitoringO4Node.DELIVER,
                payload={"plan_json": result.plan.model_dump(mode="json")},
                dedupe_key=(
                    f"deliver:{result.plan.plan_id}:{result.plan.plan_version}"
                    + (f":init:{request.initialization_id}" if request.initialization_id else "")
                ),
                initialization_id=request.initialization_id,
            )
        )
        existing_checkpoint = self.repository.get_delivery_checkpoint(
            plan.plan_id, plan.plan_version
        )
        if existing_checkpoint is None:
            self.repository.save_delivery_checkpoint(
                DeliveryCheckpoint(
                    plan_id=result.plan.plan_id,
                    plan_version=result.plan.plan_version,
                    ticker=result.plan.ticker,
                    items=[
                        DeliveryWorkItemCheckpoint(
                            source_need_id=item.source_need_id,
                            candidate_id=(
                                item.primary_candidate.candidate_id
                                if item.primary_candidate is not None
                                else None
                            ),
                            status=DeliveryItemStatus.IN_PROGRESS,
                        )
                        for item in new_items
                    ],
                )
            )
        request.status = O4RequestStatus.SUCCEEDED
        self.repository.save_request(request)
        if request.initialization_id:
            return O4RunResult(request=request, plan=result.plan)
        delivery_result = await self._deliver(
            delivery_request, start_monitoring=request.initialization_id is None
        )
        return delivery_result.model_copy(update={"plan": result.plan})

    async def _deliver(self, request: O4Request, *, start_monitoring: bool) -> O4RunResult:
        request.status = O4RequestStatus.RUNNING
        self.repository.save_request(request)
        plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
        settlement: DeliverySettlement | None = None
        reasons: list[str] = []
        try:
            settlement = cast(DeliverySettlement, await self.runner.run(request))
            checkpoint = self.repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
            self._validate_delivery(plan, settlement, checkpoint=checkpoint)
            for item in settlement.items:
                if item.status is DeliveryItemStatus.COMPLETED:
                    binding = (
                        self.message_bus.repository.get_binding(item.binding_id)
                        if self.message_bus is not None and item.binding_id
                        else None
                    )
                    if binding is None or not binding.enabled or binding.tombstoned_at is not None:
                        item.status = DeliveryItemStatus.REPLAN_REQUIRED
                        item.constraints.append(
                            "No usable registered binding confirms this delivery"
                        )
            self.repository.save_delivery_settlement(settlement)
            self.repository.save_delivery_checkpoint(
                DeliveryCheckpoint(
                    plan_id=plan.plan_id,
                    plan_version=plan.plan_version,
                    ticker=plan.ticker,
                    items=[
                        DeliveryWorkItemCheckpoint(
                            source_need_id=item.source_need_id,
                            candidate_id=item.selected_candidate_id,
                            crawler_id=item.crawler_id,
                            version=item.crawler_version,
                            stage="SETTLED",
                            delivery_stage="SETTLED",
                            status=item.status,
                            last_failure="; ".join(item.constraints) or None,
                        )
                        for item in settlement.items
                    ],
                )
            )
            if settlement.degraded:
                request.status = O4RequestStatus.DEGRADED
                reasons.append("one or more crawler Source Needs were not delivered")
            else:
                request.status = O4RequestStatus.SUCCEEDED
        except O4TurnInterrupted as exc:
            request.status = O4RequestStatus.INTERRUPTED
            request.error = str(exc)[:4000]
            reasons.append(str(exc))
            checkpoint = self.repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
            if exc.checkpoint_committed and self._has_nonterminal_items(checkpoint):
                continuation = self._enqueue_delivery_continuation(request, plan)
                reasons.append(
                    f"delivery continuation queued: {continuation.request_id} "
                    f"(seq={continuation.continuation_seq})"
                )
            elif not exc.checkpoint_committed:
                reasons.append("delivery continuation not queued: no valid committed checkpoint")
        except Exception as exc:
            request.status = (
                O4RequestStatus.FAILED if request.initialization_id else O4RequestStatus.DEGRADED
            )
            request.error = str(exc)[:4000]
            reasons.append(str(exc))
        finally:
            started, start_reason = (
                self._start_monitoring(request.ticker) if start_monitoring else (False, None)
            )
            if start_reason:
                reasons.append(start_reason)
                request.status = O4RequestStatus.DEGRADED
            self.repository.save_request(request)
        return O4RunResult(
            request=request,
            plan=plan,
            delivery=settlement,
            monitoring_started=started,
            degraded_reasons=reasons,
        )

    async def _repair(self, request: O4Request) -> O4RunResult:
        trigger = RepairTrigger.model_validate(request.payload["trigger_json"])
        repair_key: str | None = None
        if trigger.crawler_id:
            active_version = str(trigger.metadata.get("active_version", "unknown"))
            repair_key = f"{trigger.crawler_id}:{active_version}:{trigger.alert_code}"
            claim = self.repository.claim_repair(repair_key, request.request_id)
            if claim.owner_request_id != request.request_id:
                request.status = O4RequestStatus.ASSOCIATED
                request.associated_request_id = claim.owner_request_id
                self.repository.save_request(request)
                return O4RunResult(
                    request=request,
                    degraded_reasons=[f"associated with global repair {claim.owner_request_id}"],
                )
        settlement = cast(RepairSettlement, await self.runner.run(request))
        self.repository.save_repair_settlement(settlement)
        request.status = (
            O4RequestStatus.SUCCEEDED
            if settlement.final_status is RepairFinalStatus.RESOLVED
            else O4RequestStatus.DEGRADED
        )
        self.repository.save_request(request)
        if repair_key:
            self.repository.complete_repair_claim(repair_key, request.request_id)
        if settlement.final_status is RepairFinalStatus.RECONFIGURATION_REQUIRED:
            if self.context_provider is None:
                request.error = "reconfiguration context provider is unavailable"
                self.repository.save_request(request)
            else:
                context = self.context_provider.configuration_payload(request.ticker)
                self.submit_configure(
                    ticker=request.ticker,
                    policy_set=context["policy_set"],
                    document2=context["document2"],
                    reason=f"repair:{request.request_id}:source_nonviable",
                )
        return O4RunResult(request=request, repair=settlement)

    def _start_monitoring(self, ticker: str) -> tuple[bool, str | None]:
        if not self.message_bus_enabled or self.message_bus is None:
            return False, "Message Bus v2 feature flag is disabled; configuration remains durable"
        try:
            self.message_bus.start_ticker(ticker, actor=UpdateActor.SYSTEM)
            return True, None
        except Exception as exc:
            return False, f"Message Bus start failed without blocking O4 settlement: {exc}"

    @staticmethod
    def _validate_plan_against_input(
        plan: MonitoringConfigurationPlan, payload: dict[str, Any]
    ) -> None:
        policy_set = payload["policy_set_json"]
        if plan.ticker != str(policy_set["ticker"]).upper():
            raise ValueError("configuration plan ticker differs from PolicySet")
        if plan.policy_set_version != int(policy_set["policy_set_version"]):
            raise ValueError("configuration plan version differs from PolicySet")
        if plan.policy_set_sha256 != payload["policy_set_sha256"]:
            raise ValueError("configuration plan PolicySet digest mismatch")
        expected = {str(item["policy_id"]) for item in policy_set.get("policies", [])}
        covered = {policy_id for item in plan.source_needs for policy_id in item.policy_ids}
        missing = sorted(expected - covered)
        unknown = sorted(covered - expected)
        plan.source_needs = [
            item.model_copy(update={"policy_ids": [p for p in item.policy_ids if p in expected]})
            for item in plan.source_needs
            if set(item.policy_ids) & expected
        ]
        if missing or unknown:
            plan.deliberate_omissions.append(
                f"policy coverage gaps: missing={missing}, unknown={unknown}"
            )

    @staticmethod
    def _validate_delivery(
        plan: MonitoringConfigurationPlan,
        settlement: DeliverySettlement,
        *,
        checkpoint: DeliveryCheckpoint | None = None,
    ) -> None:
        if (settlement.plan_id, settlement.plan_version, settlement.ticker) != (
            plan.plan_id,
            plan.plan_version,
            plan.ticker,
        ):
            raise ValueError("delivery settlement does not correlate to immutable plan")
        expected = {
            item.source_need_id
            for item in plan.source_needs
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
        }
        from .schema import DeliveryItemSettlement

        actual = {}
        for item in settlement.items:
            if item.source_need_id in expected:
                actual.setdefault(item.source_need_id, item)
        settled = []
        for need in sorted(expected):
            item = actual.get(need)
            if item is None:
                item = DeliveryItemSettlement(
                    source_need_id=need,
                    status=DeliveryItemStatus.REPLAN_REQUIRED,
                    constraints=["No verified terminal result; deferred from this initialization"],
                )
            elif (
                item.status not in TERMINAL_DELIVERY_STATUSES
                or item.status is DeliveryItemStatus.FAILED
            ):
                # A local exploration failure is not proof all candidates are infeasible.
                item = item.model_copy(
                    update={
                        "status": DeliveryItemStatus.REPLAN_REQUIRED,
                        "constraints": [
                            *item.constraints,
                            "Incomplete delivery retained for later recovery",
                        ],
                    }
                )
            settled.append(item)
        settlement.items = settled

    @staticmethod
    def _has_nonterminal_items(checkpoint: DeliveryCheckpoint | None) -> bool:
        return bool(
            checkpoint
            and any(item.status not in TERMINAL_DELIVERY_STATUSES for item in checkpoint.items)
        )

    def _enqueue_delivery_continuation(
        self,
        request: O4Request,
        plan: MonitoringConfigurationPlan,
    ) -> O4Request:
        next_seq = request.continuation_seq + 1
        return self.repository.enqueue(
            O4Request(
                ticker=request.ticker,
                node=CodexMonitoringO4Node.DELIVER,
                payload={"plan_json": plan.model_dump(mode="json")},
                dedupe_key=f"deliver-continuation:{request.logical_request_id}:{next_seq}",
                logical_request_id=request.logical_request_id,
                continuation_seq=next_seq,
                initialization_id=request.initialization_id,
            )
        )


__all__ = ["MonitoringO4Orchestrator", "O4ConfigurationContextProvider"]
