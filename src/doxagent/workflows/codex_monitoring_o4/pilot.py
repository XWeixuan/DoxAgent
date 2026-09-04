"""Isolated two-node Codex App Pilot for O4 CONFIGURE and DELIVER."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.schema import CrawlerVersionStatus
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import TickerMonitoringStatus, UpdateActor
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.schema import PolicySet

from .capability import ALL_O4_TOOLS, TOOLS_BY_NODE, O4OperationCapabilityCodec
from .orchestrator import MonitoringO4Orchestrator
from .policy import DeliveryProgressCoordinator, O4MutationPolicy, O4PlanFinalizer
from .repository import MonitoringO4Repository
from .runner import MonitoringO4AgentRunner
from .schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliveryItemStatus,
    DeliverySettlement,
    DeliveryWorkItemCheckpoint,
    MonitoringConfigurationPlan,
    O4Request,
    SourceNeedResolution,
    strict_json_schema,
)

_STATE_SCHEMA = "monitoring-o4-pilot-v1"
_ASSET_ROOT = Path("prompts/codex_v2/monitoring_o4")
_CONFIGURE_SKILL = "skills/monitoring-configuration.md"
_DELIVER_SKILL = "skills/crawler-delivery.md"


@dataclass(frozen=True)
class MonitoringO4PilotRequest:
    case_id: str
    ticker: str
    policy_set_path: Path
    document2_path: Path
    capability_hours: int = 24 * 30


@dataclass(frozen=True)
class MonitoringO4PilotEvent:
    status: Literal["created", "waiting", "advanced", "refreshed", "completed"]
    case_root: Path
    node: CodexMonitoringO4Node | None
    task_path: Path
    message: str


class MonitoringO4PilotCoordinator:
    """Materialize and advance a Pilot without touching production control-plane state."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        cases_root: str | Path,
        python: str | Path,
        capability_secret: str | None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.cases_root = Path(cases_root).resolve()
        self.python = Path(python).resolve()
        self.codec = (
            O4OperationCapabilityCodec(capability_secret) if capability_secret is not None else None
        )

    def prepare(self, request: MonitoringO4PilotRequest) -> MonitoringO4PilotEvent:
        if self.codec is None:
            raise ValueError("capability_secret is required to prepare an O4 Pilot")
        _identifier(request.case_id, "case_id")
        ticker = _ticker(request.ticker)
        if not 1 <= request.capability_hours <= 24 * 365 * 10:
            raise ValueError("capability_hours must be between 1 and 87600")
        run_id = MonitoringO4AgentRunner.run_id_for(ticker)
        case_root = self.cases_root / request.case_id / run_id
        if case_root.exists():
            raise FileExistsError(f"O4 Pilot case already exists: {case_root}")

        policy_raw = _read_object(request.policy_set_path, "PolicySet")
        policy_set = PolicySet.model_validate(policy_raw)
        if policy_set.ticker != ticker:
            raise ValueError("Pilot ticker differs from PolicySet ticker")
        document2 = _read_object(request.document2_path, "Document2")
        document2_ticker = _find(document2, "ticker")
        if document2_ticker is not None and str(document2_ticker).upper() != ticker:
            raise ValueError("Pilot ticker differs from Document2 ticker")

        case_root.mkdir(parents=True)
        for relative in (
            ".codex",
            "context",
            "requests",
            "state",
            "crawler-plane",
            "message-bus-adapters",
        ):
            (case_root / relative).mkdir(parents=True, exist_ok=True)
        policy_payload = policy_set.model_dump(mode="json")
        policy_digest = _canonical_digest(policy_payload)
        (case_root / "context" / "policy_set.json").write_text(
            json.dumps(policy_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (case_root / "context" / "document2.json").write_text(
            json.dumps(document2, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._seed_assets(case_root)

        configure = O4Request(
            request_id=f"{request.case_id}_configure",
            ticker=ticker,
            node=CodexMonitoringO4Node.CONFIGURE,
            payload={
                "policy_set_json": policy_payload,
                "document2_json": document2,
                "policy_set_sha256": policy_digest,
                "reason": "formal_pilot",
            },
            dedupe_key=f"pilot:{request.case_id}:configure",
        )
        self._write_request(case_root, configure)
        frozen = _frozen_inventory(case_root)
        state: dict[str, Any] = {
            "schema_version": _STATE_SCHEMA,
            "case_id": request.case_id,
            "run_id": run_id,
            "ticker": ticker,
            "status": "active",
            "active_node": CodexMonitoringO4Node.CONFIGURE.value,
            "configure_request_id": configure.request_id,
            "delivery_request_id": None,
            "capability_hours": request.capability_hours,
            "policy_set_sha256": policy_digest,
            "frozen_inputs": frozen,
            "created_at": _now(),
            "updated_at": _now(),
        }
        _write_json(case_root / "coordinator_state.json", state)
        _write_json(
            case_root / "case_manifest.json",
            {
                "schema_version": "codex-monitoring-o4-pilot-case-v1",
                "case_id": request.case_id,
                "workflow_version": "codex_monitoring_o4_v1",
                "run_id": run_id,
                "ticker": ticker,
                "nodes_in_scope": [
                    CodexMonitoringO4Node.CONFIGURE.value,
                    CodexMonitoringO4Node.DELIVER.value,
                ],
                "nodes_out_of_scope": [CodexMonitoringO4Node.REPAIR.value],
                "isolated_paths": self._isolated_paths(case_root),
                "frozen_inputs": frozen,
                "created_at": state["created_at"],
            },
        )
        self._activate(case_root, state, configure)
        _protect_frozen(case_root)
        return MonitoringO4PilotEvent(
            status="created",
            case_root=case_root,
            node=CodexMonitoringO4Node.CONFIGURE,
            task_path=case_root / "PILOT_TASK.md",
            message="Run O4_CONFIGURE in a new Codex App task rooted at this case.",
        )

    @staticmethod
    def clone_deliver_case(
        *,
        source_case_root: str | Path,
        target_case_root: str | Path,
    ) -> MonitoringO4PilotEvent:
        """Create a fresh DELIVER-only Pilot from a completed CONFIGURE output.

        The source case is never mutated.  The target reuses the source's
        already-issued, signed DELIVER capability so cloning does not require
        the capability secret.  The inner run directory is intentionally kept
        equal to the signed ``run_id``; the target case id is carried by the
        new outer directory and its dedupe key.
        """

        source = Path(source_case_root).resolve()
        target = Path(target_case_root).resolve()
        if source == target:
            raise ValueError("source and target Pilot cases must differ")
        if target.exists():
            raise FileExistsError(f"target O4 Pilot case already exists: {target}")

        _source_root, source_state = MonitoringO4PilotCoordinator._load(source)
        if source_state.get("active_node") != CodexMonitoringO4Node.DELIVER.value:
            raise ValueError("source O4 Pilot must be active at o4_deliver")
        run_id = str(source_state.get("run_id", ""))
        _identifier(run_id, "run_id")
        if target.name != run_id:
            raise ValueError(f"target case root must end with signed run_id {run_id!r}")
        case_id = target.parent.name
        _identifier(case_id, "target case_id")
        ticker = _ticker(str(source_state.get("ticker", "")))
        MonitoringO4PilotCoordinator._verify_frozen(source, source_state)

        configure_request_id = str(source_state.get("configure_request_id", ""))
        delivery_request_id = str(source_state.get("delivery_request_id", ""))
        _identifier(configure_request_id, "configure_request_id")
        _identifier(delivery_request_id, "delivery_request_id")
        configure_root = source / "requests" / configure_request_id
        configure_task_path = configure_root / "task.json"
        configure_output_path = configure_root / "output" / "completion.json"
        if not configure_task_path.is_file() or not configure_output_path.is_file():
            raise ValueError("source O4 CONFIGURE task and completion are required")
        configure_request = O4Request.model_validate_json(
            configure_task_path.read_text(encoding="utf-8")
        )
        configure_completion = ConfigureCompletion.model_validate_json(
            configure_output_path.read_text(encoding="utf-8")
        )
        if (
            configure_request.node is not CodexMonitoringO4Node.CONFIGURE
            or configure_request.ticker != ticker
        ):
            raise ValueError("source CONFIGURE request does not match its Pilot state")
        MonitoringO4AgentRunner._validate_correlation(configure_request, configure_completion)

        plan_path = source / "context" / "configuration_plan.json"
        plan = MonitoringConfigurationPlan.model_validate_json(
            plan_path.read_text(encoding="utf-8")
        )
        if configure_completion.plan.model_dump(mode="json") != plan.model_dump(mode="json"):
            raise ValueError("source configuration_plan.json differs from CONFIGURE completion")
        configured_digest = source_state.get("configuration_plan_sha256")
        if configured_digest and str(configured_digest) != _sha256(plan_path):
            raise ValueError("source configuration plan hash does not match coordinator state")
        configured_output_digest = source_state.get("configure_output_sha256")
        if configured_output_digest and str(configured_output_digest) != _sha256(
            configure_output_path
        ):
            raise ValueError("source CONFIGURE completion hash does not match coordinator state")
        new_items = [
            item
            for item in plan.source_needs
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
        ]
        if not new_items:
            raise ValueError("source configuration plan has no NEW_CRAWLER_REQUIRED items")

        _read_deliver_capability(source, ticker, run_id, delivery_request_id)

        source_checkpoint = _read_delivery_checkpoint(source, plan)
        prior_by_need = {
            item.source_need_id: item
            for item in (source_checkpoint.items if source_checkpoint else [])
        }
        checkpoints: list[DeliveryWorkItemCheckpoint] = []
        for item in new_items:
            candidate = item.primary_candidate
            if candidate is None or candidate.crawler_id is None:
                raise ValueError(f"source need lacks a crawler candidate: {item.source_need_id}")
            prior = prior_by_need.get(item.source_need_id)
            crawler_id = prior.crawler_id if prior and prior.crawler_id else candidate.crawler_id
            version = prior.version if prior and prior.version is not None else 1
            working_path = source / "crawler-plane" / "working" / crawler_id / f"v{version}"
            stage = "WORKING_VERSION_RETAINED" if working_path.is_dir() else "NOT_STARTED"
            checkpoints.append(
                DeliveryWorkItemCheckpoint(
                    source_need_id=item.source_need_id,
                    candidate_id=(
                        prior.candidate_id
                        if prior and prior.candidate_id
                        else candidate.candidate_id
                    ),
                    crawler_id=crawler_id,
                    version=version,
                    stage=stage,
                    cycles_used=0,
                    last_failure=None,
                    status=DeliveryItemStatus.PENDING,
                )
            )
        checkpoint = DeliveryCheckpoint(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            ticker=plan.ticker,
            items=checkpoints,
        )

        # Materialize all files only after the source has passed validation.
        target.mkdir(parents=True)
        for relative in ("AGENTS.md", "agents", "skills", "context", "crawler-plane"):
            _copy_path(source / relative, target / relative)
        if (source / "message-bus-adapters").exists():
            _copy_path(source / "message-bus-adapters", target / "message-bus-adapters")
        _copy_path(
            source / "state" / "message_bus.sqlite3",
            target / "state" / "message_bus.sqlite3",
        )
        _copy_path(
            source / "state" / "crawler_plane.sqlite3",
            target / "state" / "crawler_plane.sqlite3",
        )
        _rewrite_sqlite_paths(target / "state" / "message_bus.sqlite3", source, target)
        _rewrite_sqlite_paths(target / "state" / "crawler_plane.sqlite3", source, target)

        target_codex = target / ".codex"
        _copy_path(source / ".codex", target_codex)
        config_path = target_codex / "config.toml"
        config_text = config_path.read_text(encoding="utf-8")
        config_text = _replace_path_text(config_text, source, target)
        config_path.write_text(config_text, encoding="utf-8")

        target_configure_root = target / "requests" / configure_request_id
        _copy_path(configure_root, target_configure_root)
        target_plan_path = target / "context" / "configuration_plan.json"
        target_configure_output = target_configure_root / "output" / "completion.json"

        target_repository = MonitoringO4Repository(target / "state" / "o4.sqlite3")
        try:
            target_repository.save_plan(plan)
            target_repository.save_delivery_checkpoint(checkpoint)
        finally:
            target_repository.close()

        delivery = O4Request(
            request_id=delivery_request_id,
            ticker=ticker,
            node=CodexMonitoringO4Node.DELIVER,
            payload={"plan_json": plan.model_dump(mode="json")},
            dedupe_key=f"pilot:{case_id}:deliver",
        )
        MonitoringO4PilotCoordinator._write_request(target, delivery)

        frozen_extra = [path for path in target_configure_root.rglob("*") if path.is_file()]
        frozen_extra.append(target_plan_path)
        frozen = _frozen_inventory(target, extra_paths=frozen_extra)
        now = _now()
        state: dict[str, Any] = {
            "schema_version": _STATE_SCHEMA,
            "case_id": case_id,
            "run_id": run_id,
            "ticker": ticker,
            "status": "active",
            "active_node": CodexMonitoringO4Node.DELIVER.value,
            "configure_request_id": configure_request_id,
            "delivery_request_id": delivery_request_id,
            "capability_hours": int(source_state.get("capability_hours", 24 * 30)),
            "policy_set_sha256": source_state.get("policy_set_sha256"),
            "frozen_inputs": frozen,
            "created_at": now,
            "updated_at": now,
            "source_d3_run_id": source_state.get("source_d3_run_id"),
            "source_d3_release_id": source_state.get("source_d3_release_id"),
            "source_d3_published_artifact": source_state.get("source_d3_published_artifact"),
            "configuration_plan_sha256": _sha256(target_plan_path),
            "configure_output_sha256": _sha256(target_configure_output),
            "source_case_root": str(source),
            "clone_of_case_id": source_state.get("case_id"),
            "capability_reused": True,
        }
        _write_json(target / "coordinator_state.json", state)
        _write_json(
            target / "case_manifest.json",
            {
                "schema_version": "codex-monitoring-o4-pilot-case-v1",
                "case_id": case_id,
                "workflow_version": "codex_monitoring_o4_v1",
                "run_id": run_id,
                "ticker": ticker,
                "nodes_in_scope": [CodexMonitoringO4Node.DELIVER.value],
                "nodes_out_of_scope": [
                    CodexMonitoringO4Node.CONFIGURE.value,
                    CodexMonitoringO4Node.REPAIR.value,
                ],
                "isolated_paths": MonitoringO4PilotCoordinator._isolated_paths(target),
                "frozen_inputs": frozen,
                "source_case_root": str(source),
                "created_at": now,
            },
        )
        task = MonitoringO4PilotCoordinator._render_task(target, state, delivery)
        task = task.replace("# O4 two-node formal Pilot", "# O4 DELIVER-only Pilot rerun", 1)
        task = task.replace(
            (
                "This case tests only `O4_CONFIGURE -> O4_DELIVER`. `O4_REPAIR` is out of scope. "
                "The Message Bus,\n"
                "Crawler Plane, crawler working/releases, and their SQLite stores are Pilot-only "
                "paths under this\n"
                "case. Do not read or modify any production control-plane path or database."
            ),
            (
                "This case executes `O4_DELIVER` only. It is seeded from the completed "
                "O4_CONFIGURE output\n"
                f"in `{source}`. O4_CONFIGURE and O4_REPAIR are out of scope. The Message Bus, "
                "Crawler Plane,\n"
                "crawler working/releases, and their SQLite stores are Pilot-only paths under "
                "this case.\n"
                "Do not read or modify any production control-plane path or database."
            ),
        )
        task = task.replace(
            (
                "Run from exactly the case root above. Use this existing Codex App task for both "
                "nodes. "
                "When the\n"
                "coordinator advances to DELIVER, continue in this same task; never create a "
                "second task "
                "or thread.\n"
                "The coordinator refreshes the signed capability file in `.codex/` on every node "
                "transition. If the\n"
                "MCP tool list still shows the previous node after a refresh, reload/reconnect "
                "that MCP "
                "server in\n"
                "this same task before continuing; do not create a replacement task or thread."
            ),
            (
                "Run from exactly the case root above. This case is already at O4_DELIVER; do not "
                "run "
                "O4_CONFIGURE\n"
                "and do not create a second task or thread. The copied signed DELIVER capability "
                "is valid "
                "for this\n"
                "isolated rerun; if the MCP connection is stale, reload/reconnect it in this same "
                "task."
            ),
        )
        task = task.replace(
            "Continue O4_DELIVER in the same Codex App task; do not create a new task or thread.",
            "Execute O4_DELIVER in this Codex App task; do not create a new task or thread.",
        )
        (target / "PILOT_TASK.md").write_text(task, encoding="utf-8")
        _protect_frozen(target, extra_paths=frozen_extra)
        return MonitoringO4PilotEvent(
            status="created",
            case_root=target,
            node=CodexMonitoringO4Node.DELIVER,
            task_path=target / "PILOT_TASK.md",
            message=(
                "Created a fresh O4_DELIVER-only Pilot from the completed CONFIGURE output; "
                "the source case was not modified and no capability secret was needed."
            ),
        )

    def advance(self, case_root: str | Path) -> MonitoringO4PilotEvent:
        root, state = self._load(case_root)
        self._verify_frozen(root, state)
        active = state.get("active_node")
        if active is None:
            return MonitoringO4PilotEvent(
                status="completed",
                case_root=root,
                node=None,
                task_path=root / "PILOT_TASK.md",
                message="The two-node O4 Pilot is complete.",
            )
        node = CodexMonitoringO4Node(str(active))
        request_id = str(
            state["configure_request_id"]
            if node is CodexMonitoringO4Node.CONFIGURE
            else state["delivery_request_id"]
        )
        completion_path = root / "requests" / request_id / "output" / "completion.json"
        if not completion_path.is_file():
            return MonitoringO4PilotEvent(
                status="waiting",
                case_root=root,
                node=node,
                task_path=root / "PILOT_TASK.md",
                message=f"Waiting for {completion_path}.",
            )
        request = O4Request.model_validate_json(
            (root / "requests" / request_id / "task.json").read_text(encoding="utf-8")
        )
        if node is CodexMonitoringO4Node.CONFIGURE:
            return self._advance_configure(root, state, request, completion_path)
        return self._advance_deliver(root, state, request, completion_path)

    def refresh(self, case_root: str | Path) -> MonitoringO4PilotEvent:
        """Reissue the active node capability without changing Pilot state."""

        if self.codec is None:
            raise ValueError("capability_secret is required to refresh an O4 Pilot")
        root, state = self._load(case_root)
        self._verify_frozen(root, state)
        active = state.get("active_node")
        if active is None:
            raise ValueError("cannot refresh capability after the O4 Pilot is complete")
        node = CodexMonitoringO4Node(str(active))
        request_id = str(
            state["configure_request_id"]
            if node is CodexMonitoringO4Node.CONFIGURE
            else state["delivery_request_id"]
        )
        request_path = root / "requests" / request_id / "task.json"
        if not request_path.is_file():
            raise ValueError(f"active O4 request is missing: {request_path}")
        request = O4Request.model_validate_json(request_path.read_text(encoding="utf-8"))
        if request.node is not node or request.ticker != state["ticker"]:
            raise ValueError("active O4 request does not match coordinator state")
        self._activate(root, state, request)
        state["capability_refresh_count"] = int(state.get("capability_refresh_count", 0)) + 1
        state["capability_refreshed_at"] = _now()
        state["updated_at"] = _now()
        _write_json(root / "coordinator_state.json", state)
        return MonitoringO4PilotEvent(
            status="refreshed",
            case_root=root,
            node=node,
            task_path=root / "PILOT_TASK.md",
            message=(
                f"Reissued {node.value} capability and refreshed the MCP configuration. "
                "Reload the MCP connection in the same Codex App task if its tool list is stale."
            ),
        )

    def status(self, case_root: str | Path) -> dict[str, Any]:
        root, state = self._load(case_root)
        self._verify_frozen(root, state)
        issue_logs = sorted(
            path.relative_to(root).as_posix()
            for path in root.glob("requests/*/audit/pilot_issues.md")
        )
        active = state.get("active_node")
        expected_tools = sorted(TOOLS_BY_NODE[CodexMonitoringO4Node(str(active))]) if active else []
        return {
            **state,
            "case_root": str(root),
            "pilot_issue_logs": issue_logs,
            "capability_file": ".codex/o4_operations_capability.token",
            "expected_active_capability_tools": expected_tools,
        }

    def _advance_configure(
        self,
        root: Path,
        state: dict[str, Any],
        request: O4Request,
        completion_path: Path,
    ) -> MonitoringO4PilotEvent:
        completion = ConfigureCompletion.model_validate_json(
            completion_path.read_text(encoding="utf-8")
        )
        MonitoringO4AgentRunner._validate_correlation(request, completion)
        bus_repository = MessageBusV2Repository(root / "state" / "message_bus.sqlite3")
        try:
            plan = O4PlanFinalizer(O4MutationPolicy()).finalize(
                completion.plan,
                source_loader=lambda source_id: _require_pilot_source(bus_repository, source_id),
                binding_loader=lambda source_id: _pilot_binding(
                    bus_repository,
                    plan_ticker=completion.plan.ticker,
                    source_id=source_id,
                ),
            )
        finally:
            bus_repository.close()
        completion = completion.model_copy(update={"plan": plan})
        completion_path.write_text(completion.model_dump_json(indent=2), encoding="utf-8")
        MonitoringO4Orchestrator._validate_plan_against_input(plan, request.payload)
        repository = MonitoringO4Repository(root / "state" / "o4.sqlite3")
        try:
            repository.save_plan(plan)
        finally:
            repository.close()
        plan_path = root / "context" / "configuration_plan.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        plan_path.chmod(stat.S_IREAD)
        state["configuration_plan_sha256"] = _sha256(plan_path)
        new_items = [
            item
            for item in plan.source_needs
            if item.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED
        ]
        if not new_items:
            started = self._start_isolated_monitoring(root, plan.ticker)
            state.update(
                status="completed",
                active_node=None,
                monitoring_started=started,
                result="CONFIGURE_ONLY_NO_CRAWLER_GAP",
                updated_at=_now(),
            )
            _write_json(root / "coordinator_state.json", state)
            self._write_completed_task(root, state)
            return MonitoringO4PilotEvent(
                status="completed",
                case_root=root,
                node=None,
                task_path=root / "PILOT_TASK.md",
                message=(
                    "CONFIGURE found no new crawler Source Need; production-equivalent flow ends."
                ),
            )

        delivery = O4Request(
            request_id=f"{state['case_id']}_deliver",
            ticker=plan.ticker,
            node=CodexMonitoringO4Node.DELIVER,
            payload={"plan_json": plan.model_dump(mode="json")},
            dedupe_key=f"pilot:{state['case_id']}:deliver",
        )
        repository = MonitoringO4Repository(root / "state" / "o4.sqlite3")
        try:
            repository.save_delivery_checkpoint(
                DeliveryCheckpoint(
                    plan_id=plan.plan_id,
                    plan_version=plan.plan_version,
                    ticker=plan.ticker,
                    items=[
                        DeliveryWorkItemCheckpoint(source_need_id=item.source_need_id)
                        for item in new_items
                    ],
                )
            )
        finally:
            repository.close()
        self._write_request(root, delivery)
        state.update(
            active_node=CodexMonitoringO4Node.DELIVER.value,
            delivery_request_id=delivery.request_id,
            configure_output_sha256=_sha256(completion_path),
            updated_at=_now(),
        )
        _write_json(root / "coordinator_state.json", state)
        self._activate(root, state, delivery)
        return MonitoringO4PilotEvent(
            status="advanced",
            case_root=root,
            node=CodexMonitoringO4Node.DELIVER,
            task_path=root / "PILOT_TASK.md",
            message="Continue O4_DELIVER in the same Codex App task; do not create a new task.",
        )

    def _advance_deliver(
        self,
        root: Path,
        state: dict[str, Any],
        request: O4Request,
        completion_path: Path,
    ) -> MonitoringO4PilotEvent:
        settlement = DeliverySettlement.model_validate_json(
            completion_path.read_text(encoding="utf-8")
        )
        MonitoringO4AgentRunner._validate_correlation(request, settlement)
        plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
        repository = MonitoringO4Repository(root / "state" / "o4.sqlite3")
        try:
            checkpoint_path = root / "requests" / request.request_id / "delivery_checkpoint.json"
            submitted_checkpoint = DeliveryCheckpoint.model_validate_json(
                checkpoint_path.read_text(encoding="utf-8")
            )
            checkpoint = DeliveryProgressCoordinator().commit(
                plan=plan,
                previous=repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version),
                submitted=submitted_checkpoint,
            )
            repository.save_delivery_checkpoint(checkpoint)
            MonitoringO4Orchestrator._validate_delivery(plan, settlement, checkpoint=checkpoint)
            self._validate_completed_deliveries(root, settlement)
            repository.save_delivery_settlement(settlement)
            repository.save_delivery_checkpoint(
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
                            status=item.status,
                            last_failure="; ".join(item.constraints) or None,
                        )
                        for item in settlement.items
                    ],
                )
            )
        finally:
            repository.close()
        started = self._start_isolated_monitoring(root, plan.ticker)
        incomplete = sum(
            item.status is not DeliveryItemStatus.COMPLETED for item in settlement.items
        )
        state.update(
            status="completed",
            active_node=None,
            delivery_output_sha256=_sha256(completion_path),
            monitoring_started=started,
            result="DEGRADED" if incomplete else "SUCCEEDED",
            incomplete_delivery_items=incomplete,
            updated_at=_now(),
        )
        _write_json(root / "coordinator_state.json", state)
        self._write_completed_task(root, state)
        return MonitoringO4PilotEvent(
            status="completed",
            case_root=root,
            node=None,
            task_path=root / "PILOT_TASK.md",
            message=(
                f"O4 Pilot complete; monitoring was started in the isolated bus and "
                f"{incomplete} delivery item(s) remain incomplete."
            ),
        )

    @staticmethod
    def _write_request(root: Path, request: O4Request) -> None:
        request_root = root / "requests" / request.request_id
        (request_root / "output").mkdir(parents=True, exist_ok=True)
        audit_root = request_root / "audit"
        audit_root.mkdir(parents=True, exist_ok=True)
        issue_log = audit_root / "pilot_issues.md"
        if not issue_log.exists():
            issue_log.write_text(
                MonitoringO4PilotCoordinator._initial_issue_log(root, request), encoding="utf-8"
            )
        (request_root / "task.json").write_text(request.model_dump_json(indent=2), encoding="utf-8")
        output_model = (
            ConfigureCompletion
            if request.node is CodexMonitoringO4Node.CONFIGURE
            else DeliverySettlement
        )
        _write_json(
            request_root / "output_schema.json",
            strict_json_schema(output_model.model_json_schema()),
        )
        if request.node is CodexMonitoringO4Node.CONFIGURE:
            (request_root / "source_need_worklist.jsonl").write_text("", encoding="utf-8")
        else:
            plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
            repository = MonitoringO4Repository(root / "state" / "o4.sqlite3")
            try:
                checkpoint = repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
            finally:
                repository.close()
            (request_root / "delivery_checkpoint.json").write_text(
                checkpoint.model_dump_json(indent=2) if checkpoint else '{"items": []}',
                encoding="utf-8",
            )

    @staticmethod
    def _initial_issue_log(root: Path, request: O4Request) -> str:
        return f"""# O4 Pilot issues

- Ticker：`{request.ticker}`
- 节点：`{request.node.value}`
- Run ID：`{root.name}`
- Request ID：`{request.request_id}`
- 开始时间：`PENDING_AGENT_START`

"""

    @staticmethod
    def _validate_completed_deliveries(root: Path, settlement: DeliverySettlement) -> None:
        completed = [
            item for item in settlement.items if item.status is DeliveryItemStatus.COMPLETED
        ]
        if not completed:
            return
        crawler_repository = CrawlerPlaneRepository(root / "state" / "crawler_plane.sqlite3")
        bus_repository = MessageBusV2Repository(root / "state" / "message_bus.sqlite3")
        try:
            for item in completed:
                if not all(
                    (
                        item.crawler_id,
                        item.crawler_version,
                        item.certification_run_id,
                        item.source_id,
                        item.binding_id,
                    )
                ):
                    raise ValueError(
                        f"COMPLETED delivery lacks proof-chain identity: {item.source_need_id}"
                    )
                crawler_id = cast(str, item.crawler_id)
                crawler_version = cast(int, item.crawler_version)
                version = crawler_repository.get_version(crawler_id, crawler_version)
                certification = crawler_repository.get_certification(
                    cast(str, item.certification_run_id)
                )
                package = crawler_repository.get_package(crawler_id)
                if (
                    version is None
                    or version.status is not CrawlerVersionStatus.ACTIVE
                    or package is None
                    or package.active_version != crawler_version
                    or certification is None
                    or certification.crawler_id != crawler_id
                    or certification.crawler_version != crawler_version
                ):
                    raise ValueError(
                        f"COMPLETED delivery has no matching ACTIVE certified release: "
                        f"{item.source_need_id}"
                    )
                source = bus_repository.get_source(cast(str, item.source_id))
                binding = bus_repository.get_binding(cast(str, item.binding_id))
                if (
                    source is None
                    or source.adapter_ref != f"crawler:{crawler_id}"
                    or binding is None
                    or binding.ticker != settlement.ticker
                    or binding.source_id != source.source_id
                    or not binding.enabled
                ):
                    raise ValueError(
                        f"COMPLETED delivery has no aligned enabled ticker binding: "
                        f"{item.source_need_id}"
                    )
        finally:
            crawler_repository.close()
            bus_repository.close()

    def _seed_assets(self, root: Path) -> None:
        source_root = self.repo_root / _ASSET_ROOT
        mapping = (
            "AGENTS.md",
            "agents/o4.md",
            "skills/message-bus-operations.md",
            "skills/crawler-plane-operations.md",
            _CONFIGURE_SKILL,
            _DELIVER_SKILL,
        )
        for relative in mapping:
            source = source_root / relative
            if not source.is_file():
                raise FileNotFoundError(f"missing O4 Pilot asset: {source}")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _activate(self, root: Path, state: dict[str, Any], request: O4Request) -> None:
        (root / ".codex" / "config.toml").write_text(
            self._render_config(root, state, request), encoding="utf-8"
        )
        (root / "PILOT_TASK.md").write_text(
            self._render_task(root, state, request), encoding="utf-8"
        )

    def _render_config(self, root: Path, state: dict[str, Any], request: O4Request) -> str:
        if self.codec is None:
            raise ValueError("capability_secret is required to render O4 Pilot config")
        ttl = int(state["capability_hours"]) * 3600
        capability = self.codec.issue(
            run_id=str(state["run_id"]),
            request_id=request.request_id,
            ticker=request.ticker,
            node=request.node,
            ttl_seconds=ttl,
        )
        capability_path = root / ".codex" / "o4_operations_capability.token"
        _write_atomic_text(capability_path, capability)
        paths = self._isolated_paths(root)
        # The client-side allow-list is a stable superset for the persistent
        # ticker task.  The signed token and server-side node intersection still
        # expose only the active node's operations.
        tools = sorted(name.replace(".", "_") for name in ALL_O4_TOOLS)

        def quote(value: object) -> str:
            return json.dumps(str(value), ensure_ascii=False)

        tool_array = ", ".join(quote(item) for item in tools)
        return f"""model = "gpt-5.6-sol"
model_reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[features]
multi_agent = false

[mcp_servers.o4_operations]
command = {quote(self.python)}
args = ["-m", "doxagent.mcp.o4_operations_server"]
cwd = {quote(root)}
enabled_tools = [{tool_array}]
required = true
startup_timeout_sec = 20
tool_timeout_sec = 180

[mcp_servers.o4_operations.env]
DOXAGENT_O4_OPERATIONS_CAPABILITY = {quote(capability)}
DOXAGENT_O4_OPERATIONS_CAPABILITY_FILE = {quote(capability_path)}
DOXAGENT_O4_OPERATIONS_PUBLIC_KEY = {quote(self.codec.public_key)}
DOXAGENT_MESSAGE_BUS_V2_ENABLED = "true"
DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH = {quote(paths["message_bus_sqlite"])}
DOXAGENT_MESSAGE_BUS_V2_ADAPTER_ROOT = {quote(paths["adapter_root"])}
DOXAGENT_MESSAGE_BUS_V2_CONTENT_ENRICHMENT_ENABLED = "false"
DOXAGENT_CRAWLER_PLANE_ROOT = {quote(paths["crawler_plane_root"])}
DOXAGENT_CRAWLER_PLANE_SQLITE_PATH = {quote(paths["crawler_plane_sqlite"])}
DOXAGENT_CRAWLER_PLANE_WORKER_PROCESSES = "4"
"""

    @staticmethod
    def _render_task(root: Path, state: dict[str, Any], request: O4Request) -> str:
        node_skill = (
            _CONFIGURE_SKILL if request.node is CodexMonitoringO4Node.CONFIGURE else _DELIVER_SKILL
        )
        request_root = f"requests/{request.request_id}"
        required = [
            "AGENTS.md",
            "agents/o4.md",
            "skills/message-bus-operations.md",
        ]
        if request.node is CodexMonitoringO4Node.DELIVER:
            required.append("skills/crawler-plane-operations.md")
        required.extend(
            [
                node_skill,
                f"{request_root}/task.json",
                f"{request_root}/output_schema.json",
                f"{request_root}/audit/pilot_issues.md",
            ]
        )
        read_order = "\n".join(f"{index}. `{path}`" for index, path in enumerate(required, 1))
        return f"""# O4 two-node formal Pilot

Case root: `{root}`
Ticker: `{request.ticker}`
Current node: `{request.node.value}`
Request: `{request.request_id}`

This case tests only `O4_CONFIGURE -> O4_DELIVER`. `O4_REPAIR` is out of scope. The Message Bus,
Crawler Plane, crawler working/releases, and their SQLite stores are Pilot-only paths under this
case. Do not read or modify any production control-plane path or database.

Run from exactly the case root above. Use this existing Codex App task for both nodes. When the
coordinator advances to DELIVER, continue in this same task; never create a second task or thread.
The coordinator refreshes the signed capability file in `.codex/` on every node transition. If the
MCP tool list still shows the previous node after a refresh, reload/reconnect that MCP server in
this same task before continuing; do not create a replacement task or thread.

Read in this exact order:

{read_order}

This Pilot has two equally important but strictly separated objectives:

1. Execute the active O4 node and produce the real node completion artifact.
2. Act as a Pilot tester and record problems, difficulties, and blockers encountered while trying
   to understand, execute, or verify the node.

## Pilot issue record

The only Pilot meta-analysis file for this node is
`{request_root}/audit/pilot_issues.md`. Before the first node operation, replace
`PENDING_AGENT_START` with the actual start time. Append each issue when it is encountered; do not
wait until the end and do not manufacture issues merely to populate the file.

Use this structure:

```markdown
### O4P-001
- 发生步骤：
- 分类：MCP | Message Bus | Crawler Plane | Source Inspection | Schema | Prompt | Skill |
  Context | Workspace | Agent Loop
- 严重度：blocker | major | minor | observation
- 现象：
- 对节点执行、验证或监测质量的影响：
- 相关文件、工具、source、execution 或 artifact：
- 初步原因：
- 建议修复方向：
- 当前处置：
```

Record unclear tools or parameters, missing or contradictory state, unexpected payloads, permission
or workspace problems, source inspection/probe/certification difficulties, repeated or avoidable
calls, schema/prompt/skill conflicts, and anything that prevents independent proof of an operation.
Normal business outcomes still belong in the Plan, checkpoint, or Settlement; also record them here
only when they expose a Pilot, workflow, tool, or usability problem. Pilot analysis must never enter
the formal ConfigureCompletion, Configuration Plan, Delivery checkpoint, or DeliverySettlement.

Before returning the node completion, append:

```markdown
## Final summary
- 本轮是否完成正式节点任务：
- 正式 completion 是否通过 schema 与关联性自检：
- 控制面变更是否得到独立复读或证据验证：
- 阻碍 workflow 的问题：
- Agent 遇到的主要困难：
- MCP/Message Bus/Crawler Plane 问题：
- Prompt/Skill/Schema/Context 问题：
- 可暂缓的易用性问题：
- 建议修复顺序：
- blocker/major/minor/observation 数量：
```

If no issue was found, say so explicitly in the Final summary. The issue log is an observation
artifact, not a delivery gate: never withhold completed capabilities, delay non-blocking settlement,
or keep Message Bus monitoring disabled merely to investigate or polish this log.

Then query current state through the signed O4 operations MCP and execute the active node. The
service-returned crawler `working_path` is authoritative in this Pilot; write crawler package files
only there. All control-plane mutations must use MCP tools. Direct SQLite and release edits are
forbidden.

Write progressive work only below
`{request_root}/`, including the Pilot issue log above. Write the final strict JSON object to
`{request_root}/output/completion.json`, validate it against `output_schema.json`, and return the
same JSON as the final response. Do not alter `context/`, shared prompt/skill assets,
`case_manifest.json`, or `coordinator_state.json`.
"""

    def _write_completed_task(self, root: Path, state: dict[str, Any]) -> None:
        request_ids = [
            str(request_id)
            for request_id in (state.get("configure_request_id"), state.get("delivery_request_id"))
            if request_id
        ]
        issue_lines = "\n".join(
            f"- `requests/{request_id}/audit/pilot_issues.md`" for request_id in request_ids
        )
        (root / "PILOT_TASK.md").write_text(
            "# O4 two-node formal Pilot\n\n"
            f"Case `{state['case_id']}` is complete. Result: `{state['result']}`. "
            "Do not run O4_REPAIR in this case.\n\n"
            "Review the node-local Pilot issue logs before planning workflow improvements:\n\n"
            f"{issue_lines}\n",
            encoding="utf-8",
        )

    def _start_isolated_monitoring(self, root: Path, ticker: str) -> bool:
        settings = DoxAgentSettings(
            message_bus_v2_enabled=True,
            message_bus_v2_sqlite_path=str(root / "state" / "message_bus.sqlite3"),
            message_bus_v2_adapter_root=str(root / "message-bus-adapters"),
            message_bus_v2_content_enrichment_enabled=False,
        )
        repository, service = build_message_bus_v2_service(settings)
        try:
            service.start_ticker(ticker, actor=UpdateActor.SYSTEM)
            current = repository.get_ticker_state(ticker)
            return current is not None and current.status is TickerMonitoringStatus.RUNNING
        finally:
            repository.close()

    @staticmethod
    def _isolated_paths(root: Path) -> dict[str, str]:
        return {
            "message_bus_sqlite": str(root / "state" / "message_bus.sqlite3"),
            "adapter_root": str(root / "message-bus-adapters"),
            "crawler_plane_root": str(root / "crawler-plane"),
            "crawler_plane_sqlite": str(root / "state" / "crawler_plane.sqlite3"),
            "o4_sqlite": str(root / "state" / "o4.sqlite3"),
        }

    @staticmethod
    def _load(case_root: str | Path) -> tuple[Path, dict[str, Any]]:
        root = Path(case_root).resolve()
        try:
            state = cast(
                dict[str, Any],
                json.loads((root / "coordinator_state.json").read_text(encoding="utf-8")),
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid O4 Pilot state: {root}") from exc
        if state.get("schema_version") != _STATE_SCHEMA or root.name != state.get("run_id"):
            raise ValueError(f"unsupported or misplaced O4 Pilot case: {root}")
        return root, state

    @staticmethod
    def _verify_frozen(root: Path, state: dict[str, Any]) -> None:
        for item in state.get("frozen_inputs", []):
            path = root / str(item["path"])
            if not path.is_file() or _sha256(path) != item["sha256"]:
                raise ValueError(f"frozen O4 Pilot input changed: {item['path']}")


def _frozen_inventory(
    root: Path, *, extra_paths: list[Path] | tuple[Path, ...] = ()
) -> list[dict[str, Any]]:
    paths = [root / "context" / "policy_set.json", root / "context" / "document2.json"]
    paths.extend(
        [root / "AGENTS.md", *(root / "agents").rglob("*.md"), *(root / "skills").rglob("*.md")]
    )
    paths.extend(extra_paths)
    unique_paths = {path.relative_to(root).as_posix(): path for path in paths if path.is_file()}
    return [
        {
            "path": relative,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for relative, path in sorted(unique_paths.items())
    ]


def _protect_frozen(root: Path, *, extra_paths: list[Path] | tuple[Path, ...] = ()) -> None:
    for item in _frozen_inventory(root, extra_paths=extra_paths):
        (root / str(item["path"])).chmod(stat.S_IREAD)


def _copy_path(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target, copy_function=shutil.copy2)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    else:
        raise FileNotFoundError(source)


def _replace_path_text(value: str, source: Path, target: Path) -> str:
    source_text = str(source)
    target_text = str(target)
    # TOML and JSON encode Windows backslashes differently.  Replace the
    # escaped representation first, then ordinary path values.
    return value.replace(
        source_text.replace("\\", "\\\\"), target_text.replace("\\", "\\\\")
    ).replace(source_text, target_text)


def _rewrite_sqlite_paths(path: Path, source: Path, target: Path) -> None:
    """Keep copied crawler/message-bus metadata inside the target case."""

    source_text = str(source)
    target_text = str(target)
    source_json = source_text.replace("\\", "\\\\")
    target_json = target_text.replace("\\", "\\\\")
    connection = sqlite3.connect(path)
    try:
        tables = connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        ).fetchall()
        for (table_name,) in tables:
            columns = connection.execute(
                f'pragma table_info("{str(table_name).replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            for column in columns:
                column_name = str(column[1])
                if column_name not in {"data_json", "path"}:
                    continue
                quoted_table = '"' + str(table_name).replace('"', '""') + '"'
                quoted_column = '"' + column_name.replace('"', '""') + '"'
                connection.execute(
                    f"update {quoted_table} set {quoted_column} = "
                    f"replace(replace({quoted_column}, ?, ?), ?, ?)",
                    (source_json, target_json, source_text, target_text),
                )
        connection.commit()
    finally:
        connection.close()


def _read_deliver_capability(root: Path, ticker: str, run_id: str, request_id: str) -> object:
    config_path = root / ".codex" / "config.toml"
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        environment = parsed["mcp_servers"]["o4_operations"]["env"]
        token = str(environment["DOXAGENT_O4_OPERATIONS_CAPABILITY"])
        public_key = str(environment["DOXAGENT_O4_OPERATIONS_PUBLIC_KEY"])
    except (KeyError, OSError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("source O4 DELIVER capability config is unavailable") from exc
    claims = O4OperationCapabilityCodec.verify(token, public_key=public_key)
    if (
        claims.node is not CodexMonitoringO4Node.DELIVER
        or claims.ticker != ticker
        or claims.run_id != run_id
        or claims.request_id != request_id
    ):
        raise ValueError("source capability is not scoped to this DELIVER request")
    return claims


def _read_delivery_checkpoint(
    root: Path, plan: MonitoringConfigurationPlan
) -> DeliveryCheckpoint | None:
    repository = MonitoringO4Repository(root / "state" / "o4.sqlite3")
    try:
        return repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
    finally:
        repository.close()


def _require_pilot_source(repository: MessageBusV2Repository, source_id: str) -> dict[str, Any]:
    source = repository.get_source(source_id)
    if source is None:
        raise KeyError(f"Pilot Message Bus source not found: {source_id}")
    return source.model_dump(mode="json")


def _pilot_binding(
    repository: MessageBusV2Repository,
    *,
    plan_ticker: str,
    source_id: str,
) -> dict[str, Any] | None:
    binding = repository.get_binding(f"{plan_ticker}:{source_id}")
    return binding.model_dump(mode="json") if binding is not None else None


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must be an object")
    return cast(dict[str, Any], value)


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{hashlib.sha256(content.encode()).hexdigest()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _find(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return cast(object, value[key])
        for child in value.values():
            found = _find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find(child, key)
            if found is not None:
                return found
    return None


def _identifier(value: str, label: str) -> None:
    if not value or len(value) > 96 or not all(char.isalnum() or char in "._-" for char in value):
        raise ValueError(f"invalid {label}")


def _ticker(value: str) -> str:
    result = value.strip().upper()
    _identifier(result, "ticker")
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "MonitoringO4PilotCoordinator",
    "MonitoringO4PilotEvent",
    "MonitoringO4PilotRequest",
]
