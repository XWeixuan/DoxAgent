"""Production O4 node bridge; parent owns retries, continuations and activation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_monitoring_o4.schema import (
    MonitoringConfigurationPlan,
    O4Request,
    O4RequestStatus,
    O4RunResult,
    SourceNeedResolution,
    new_id,
)
from doxagent.workflows.codex_monitoring_o4.service import (
    MonitoringO4Runtime,
    build_monitoring_o4_runtime,
)

from .schema import NodeRecord, NodeResult
from .service import NodeContext


class O4InitializationAdapter:
    def __init__(
        self,
        settings: DoxAgentSettings,
        *,
        runtime_factory: Callable[[NodeContext], MonitoringO4Runtime] | None = None,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory or self._build

    def _build(self, context: NodeContext) -> MonitoringO4Runtime:
        source = context.node.inputs.get("_source_initialization")
        if source:
            from .configuration import CandidateConfiguration, candidate_bus_path

            previous = context.repository.get(source)
            if previous.ticker != context.run.ticker:
                raise ValueError("candidate import ticker mismatch")
            CandidateConfiguration(
                self.settings.message_bus_v2_sqlite_path,
                context.run.initialization_id,
                context.run.ticker,
            ).prepare(
                snapshot_from=candidate_bus_path(self.settings.message_bus_v2_sqlite_path, source)
            )
        return build_monitoring_o4_runtime(
            self.settings,
            initialization_id=context.run.initialization_id,
            ticker=context.run.ticker,
        )

    async def reconcile(self, context: NodeContext) -> NodeResult | None:
        runtime = self.runtime_factory(context)
        try:
            if context.node.key == "o4.register":
                return self._registration(runtime, context)
            request = self._owned_request(runtime, context)
            if request is None:
                return None
            if request.status in {O4RequestStatus.SUCCEEDED, O4RequestStatus.DEGRADED}:
                return self._completed(runtime, request)
            if request.status in {O4RequestStatus.PENDING, O4RequestStatus.RUNNING}:
                # Same request reattaches its frozen, idempotent HTTP dispatch.
                return self._process_result(runtime, await runtime.orchestrator.process(request))
            return None
        finally:
            await runtime.close()

    async def execute(self, context: NodeContext) -> NodeResult:
        runtime = self.runtime_factory(context)
        try:
            if context.node.key == "o4.register":
                return self._registration(runtime, context)
            if context.node.key == "o4.configure":
                policy = self._input(context, "policy_set_path")
                document2 = self._input(context, "document2_path")
                request = runtime.orchestrator.submit_configure(
                    ticker=context.run.ticker,
                    policy_set=policy,
                    document2=document2,
                    initialization_id=context.run.initialization_id,
                    execution_id=context.node.execution_id,
                )
            elif context.node.key == "o4.deliver":
                plan = self._plan(runtime, context)
                if not any(
                    n.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
                    for n in plan.source_needs
                ):
                    return NodeResult(
                        artifacts=self._refs(runtime, plan), quality_annotations=["NOOP"]
                    )
                candidates = [
                    r
                    for r in runtime.repository.list_requests(ticker=context.run.ticker)
                    if r.initialization_id == context.run.initialization_id
                    and r.node is CodexMonitoringO4Node.DELIVER
                    and r.payload.get("plan_json", {}).get("plan_id") == plan.plan_id
                ]
                pending = next((r for r in candidates if r.status is O4RequestStatus.PENDING), None)
                if pending is not None:
                    request = pending
                else:
                    previous = candidates[-1] if candidates else None
                    request = runtime.repository.enqueue(
                        O4Request(
                            request_id=f"init-o4-{context.node.execution_id}",
                            ticker=context.run.ticker,
                            node=CodexMonitoringO4Node.DELIVER,
                            initialization_id=context.run.initialization_id,
                            payload={"plan_json": plan.model_dump(mode="json")},
                            dedupe_key=f"init-deliver:{context.node.execution_id}",
                            logical_request_id=(
                                previous.logical_request_id if previous else new_id("o4_logical")
                            ),
                            continuation_seq=previous.continuation_seq + 1 if previous else 0,
                        )
                    )
                # Associate the queued continuation before dispatch, closing the gap
                # between child enqueue and the parent's receipt write.
                request.payload["initialization_execution_id"] = context.node.execution_id
                runtime.repository.save_request(request)
            else:
                raise ValueError(f"unsupported O4 initialization node: {context.node.key}")
            context.checkpoint(request_id=request.request_id, o4_database=runtime.repository.path)
            return self._process_result(runtime, await runtime.orchestrator.process(request))
        finally:
            await runtime.close()

    @staticmethod
    def _input(context: NodeContext, key: str) -> dict[str, Any]:
        reference = context.node.inputs.get(key)
        if reference is None:
            for dependency in dict.fromkeys(
                [*context.node.dependencies, *context.node.inputs.get("_replay_dependencies", {})]
            ):
                reference = context.dependency(dependency).artifacts.get(key)
                if reference is not None:
                    break
        if not isinstance(reference, str):
            raise ValueError(f"missing local published artifact reference: {key}")
        value = json.loads(Path(reference).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid artifact object: {key}")
        return value

    @staticmethod
    def _owned_request(runtime: MonitoringO4Runtime, context: NodeContext) -> O4Request | None:
        request = runtime.repository.get_request(f"init-o4-{context.node.execution_id}")
        if request is not None:
            return request
        return next(
            (
                r
                for r in runtime.repository.list_requests(ticker=context.run.ticker)
                if r.initialization_id == context.run.initialization_id
                and r.payload.get("initialization_execution_id") == context.node.execution_id
            ),
            None,
        )

    @staticmethod
    def _plan(runtime: MonitoringO4Runtime, context: NodeContext) -> MonitoringConfigurationPlan:
        refs = context.dependency("o4.configure").artifacts
        return O4InitializationAdapter._import_plan(runtime, context, refs)

    @staticmethod
    def _import_plan(
        runtime: MonitoringO4Runtime,
        context: NodeContext,
        refs: dict[str, Any],
    ) -> MonitoringConfigurationPlan:
        plan = runtime.repository.get_plan(refs["plan_id"], refs["plan_version"])
        if context.node.inputs.get("_source_initialization"):
            from doxagent.workflows.codex_monitoring_o4.repository import MonitoringO4Repository

            path = Path(refs["o4_database"])
            if not path.is_file():
                raise ValueError("source O4 configuration database is unavailable")
            source = MonitoringO4Repository(path)
            try:
                plan = source.get_plan(refs["plan_id"], refs["plan_version"])
                if plan is None or plan.ticker.upper() != context.run.ticker:
                    raise ValueError("source O4 plan missing or ticker mismatch")
                runtime.repository.save_plan(plan)
                checkpoint = source.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
                if (
                    checkpoint is not None
                    and runtime.repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
                    is None
                ):
                    runtime.repository.save_delivery_checkpoint(checkpoint)
                request_id = refs.get("request_id")
                if request_id:
                    request = source.get_request(request_id)
                    settlement = source.get_delivery_settlement(request_id)
                    if request is not None and request.status in {
                        O4RequestStatus.SUCCEEDED,
                        O4RequestStatus.DEGRADED,
                    }:
                        runtime.repository.enqueue(request)
                        if settlement is not None:
                            runtime.repository.save_delivery_settlement(settlement)
            finally:
                source.close()
        if plan is None:
            raise ValueError("configured O4 plan is unavailable")
        return plan

    @staticmethod
    def _refs(runtime: MonitoringO4Runtime, plan: MonitoringConfigurationPlan) -> dict[str, Any]:
        return {
            "plan_id": plan.plan_id,
            "plan_version": plan.plan_version,
            "o4_database": runtime.repository.path,
        }

    def _completed(self, runtime: MonitoringO4Runtime, request: O4Request) -> NodeResult:
        if request.node is CodexMonitoringO4Node.CONFIGURE:
            ref = request.payload["configuration_plan_ref"]
            plan = runtime.repository.get_plan(ref["plan_id"], ref["plan_version"])
            if plan is None:
                raise ValueError("completed O4 configuration has no plan")
        else:
            plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
            settlement = runtime.repository.get_delivery_settlement(request.request_id)
            if settlement is None:
                raise ValueError("O4 DELIVER has no terminal settlement")
            runtime.orchestrator._validate_delivery(plan, settlement)
        runtime.repository.release_ticker_lease(request.ticker, request.request_id)
        return NodeResult(
            artifacts={**self._refs(runtime, plan), "request_id": request.request_id},
            quality_annotations=[request.status.value]
            if request.status is O4RequestStatus.DEGRADED
            else [],
        )

    def _process_result(self, runtime: MonitoringO4Runtime, result: O4RunResult) -> NodeResult:
        if result.request.status not in {O4RequestStatus.SUCCEEDED, O4RequestStatus.DEGRADED}:
            raise RuntimeError(result.request.error or "O4 request has not completed")
        return self._completed(runtime, result.request)

    def _registration(self, runtime: MonitoringO4Runtime, context: NodeContext) -> NodeResult:
        delivered = context.dependency("o4.deliver")
        refs = delivered.artifacts
        plan = self._import_plan(runtime, context, refs)
        needs_delivery = any(
            item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
            for item in plan.source_needs
        )
        if needs_delivery:
            request = runtime.repository.get_request(refs.get("request_id", ""))
            if request is None:
                raise ValueError("O4 registration requires terminal DELIVER settlement")
            self._completed(runtime, request)
        bus = runtime.message_bus
        if bus is None:
            raise ValueError("Message Bus is not configured")
        bindings = bus.repository.list_bindings(ticker=context.run.ticker)
        usable = [
            b
            for b in bindings
            if b.enabled
            and b.tombstoned_at is None
            and (source := bus.repository.get_source(b.source_id)) is not None
            and source.enabled
        ]
        if not usable:
            raise ValueError("ticker has no usable registered monitoring binding")
        # The signed O4 operations already registered sources/bindings in this
        # candidate. Do not repeat probes, start polling, or mutate live configuration.
        return NodeResult(
            artifacts={
                "monitoring_configuration": {
                    "initialization_id": context.run.initialization_id,
                    "database": str(bus.repository.path),
                    "plan_id": plan.plan_id,
                    "plan_version": plan.plan_version,
                    "enabled_binding_count": len(usable),
                }
            },
            quality_annotations=delivered.quality_annotations,
        )


def adapter_factory(node: NodeRecord) -> O4InitializationAdapter:
    if node.key not in {"o4.configure", "o4.deliver", "o4.register"}:
        raise ValueError(f"O4 factory cannot execute {node.key}")
    return O4InitializationAdapter(DoxAgentSettings())
