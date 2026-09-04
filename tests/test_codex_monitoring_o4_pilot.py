from __future__ import annotations

import hashlib
import json
import stat
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.crawler_plane.repository import CrawlerPlaneRepository
from doxagent.crawler_plane.schema import CrawlerPackage, CrawlerVersion, CrawlerVersionSpec
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import TickerMonitoringStatus
from doxagent.workflows.codex_document3.schema import (
    ActivationCondition,
    Calibration,
    Document2Ref,
    Policy,
    PolicyDecision,
    PolicySet,
    PublicationState,
)
from doxagent.workflows.codex_monitoring_o4.capability import O4OperationCapabilityCodec
from doxagent.workflows.codex_monitoring_o4.pilot import (
    MonitoringO4PilotCoordinator,
    MonitoringO4PilotRequest,
)
from doxagent.workflows.codex_monitoring_o4.schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliveryItemSettlement,
    DeliveryItemStatus,
    DeliveryProgressState,
    DeliverySettlement,
    MonitoringConfigurationPlan,
    SourceCandidate,
    SourceNeedPlanItem,
    SourceNeedResolution,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _inputs(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    policy = PolicySet(
        ticker="MU",
        policy_set_version=1,
        document2_ref=Document2Ref(
            run_id="d2-mu",
            artifact_id="d2-mu-document",
            sha256="a" * 64,
            published_at=NOW,
            publication_state=PublicationState.COMPLETE,
        ),
        policies=[
            Policy(
                policy_id="pol_1",
                title="Material customer commitment",
                source_refs=[{"shell_id": "S1", "expectation_id": "E1", "gap_id": "G1"}],
                decision=PolicyDecision.LONG,
                match_scope="Official customer or issuer commitment",
                activation_conditions=[
                    ActivationCondition(
                        condition_id="C1",
                        criterion="A binding material commitment is announced",
                        calibration=Calibration(
                            reference_state="No binding commitment",
                            trigger_boundary="Binding material volume",
                        ),
                    )
                ],
            )
        ],
        published_at=NOW,
    ).model_dump(mode="json")
    document2: dict[str, object] = {
        "artifact_id": "d2-mu-document",
        "ticker": "MU",
        "shells": [{"shell_id": "S1", "expectations": ["E1"]}],
    }
    policy_path = tmp_path / "policy_set.json"
    document2_path = tmp_path / "document2.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    document2_path.write_text(json.dumps(document2), encoding="utf-8")
    return policy_path, document2_path, policy


def _coordinator(tmp_path: Path) -> MonitoringO4PilotCoordinator:
    return MonitoringO4PilotCoordinator(
        repo_root=Path(__file__).resolve().parents[1],
        cases_root=tmp_path / "cases",
        python=sys.executable,
        capability_secret="pilot-secret-that-is-at-least-32-bytes",
    )


def _prepare(tmp_path: Path) -> tuple[MonitoringO4PilotCoordinator, Path, dict[str, object]]:
    policy_path, document2_path, policy = _inputs(tmp_path)
    coordinator = _coordinator(tmp_path)
    event = coordinator.prepare(
        MonitoringO4PilotRequest(
            case_id="mu-o4-pilot-001",
            ticker="MU",
            policy_set_path=policy_path,
            document2_path=document2_path,
        )
    )
    return coordinator, event.case_root, policy


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _configure_completion(
    policy: dict[str, object],
    *,
    new_crawler: bool,
) -> ConfigureCompletion:
    resolution = (
        SourceNeedResolution.NEW_CRAWLER_REQUIRED
        if new_crawler
        else SourceNeedResolution.KEEP_DEFAULT
    )
    candidate = (
        SourceCandidate(
            candidate_id="issuer-ir",
            display_name="Issuer IR",
            url="https://example.com/ir",
            evidence=["Live publication surface inspected"],
            crawler_id="company_ir_reference",
        )
        if new_crawler
        else None
    )
    return ConfigureCompletion(
        request_id="mu-o4-pilot-001_configure",
        plan=MonitoringConfigurationPlan(
            plan_id="pilot-plan",
            ticker="MU",
            policy_set_id="MU:policy-set:1",
            policy_set_version=1,
            policy_set_sha256=_digest(policy),
            document2_ref="d2-mu-document",
            baseline_observed_at=NOW,
            baseline_summary={"pilot": True},
            source_needs=[
                SourceNeedPlanItem(
                    source_need_id="need-1",
                    policy_ids=["pol_1"],
                    disclosure_actor="issuer",
                    disclosure_channel="IR",
                    observability_target="material customer commitments",
                    resolution=resolution,
                    rationale="Covers the only policy",
                    primary_candidate=candidate,
                )
            ],
            stopping_rationale="The material coverage space is exhausted",
        ),
    )


def test_pilot_prepares_only_configure_and_deliver_with_isolated_state(tmp_path: Path) -> None:
    coordinator, root, _ = _prepare(tmp_path)

    assert root.name == "monitoring-o4-mu-main"
    manifest = json.loads((root / "case_manifest.json").read_text(encoding="utf-8"))
    assert manifest["nodes_in_scope"] == ["o4_configure", "o4_deliver"]
    assert manifest["nodes_out_of_scope"] == ["o4_repair"]
    config = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "monitoring_hard_delete_source" in config
    # The Codex client allow-list is a stable node-transition superset.  The
    # signed token remains CONFIGURE-only and is the actual authorization.
    assert "crawler_plane_create_version" in config
    assert "crawler_plane_resolve_alert" in config
    capability_path = root / ".codex" / "o4_operations_capability.token"
    claims = O4OperationCapabilityCodec("pilot-secret-that-is-at-least-32-bytes").verify(
        capability_path.read_text(encoding="utf-8"),
        public_key=O4OperationCapabilityCodec("pilot-secret-that-is-at-least-32-bytes").public_key,
    )
    assert claims.node is CodexMonitoringO4Node.CONFIGURE
    assert "crawler_plane.execute" not in claims.enabled_tool_ids
    assert str(root / "state" / "message_bus.sqlite3").replace("\\", "\\\\") in config
    issue_log = root / "requests" / "mu-o4-pilot-001_configure" / "audit" / "pilot_issues.md"
    assert issue_log.is_file()
    assert "PENDING_AGENT_START" in issue_log.read_text(encoding="utf-8")
    task = (root / "PILOT_TASK.md").read_text(encoding="utf-8")
    assert "two equally important but strictly separated objectives" in task
    assert "requests/mu-o4-pilot-001_configure/audit/pilot_issues.md" in task
    assert "Pilot analysis must never enter" in task
    assert coordinator.status(root)["pilot_issue_logs"] == [
        "requests/mu-o4-pilot-001_configure/audit/pilot_issues.md"
    ]
    assert coordinator.advance(root).status == "waiting"


def test_pilot_advances_same_case_to_deliver_and_finishes_degraded_nonblocking(
    tmp_path: Path,
) -> None:
    coordinator, root, policy = _prepare(tmp_path)
    configure = _configure_completion(policy, new_crawler=True)
    configure_output = root / "requests" / configure.request_id / "output" / "completion.json"
    configure_output.write_text(configure.model_dump_json(indent=2), encoding="utf-8")

    advanced = coordinator.advance(root)
    assert advanced.status == "advanced"
    assert advanced.node is CodexMonitoringO4Node.DELIVER
    assert advanced.case_root == root
    config = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "crawler_plane_create_version" in config
    assert "monitoring_hard_delete_source" in config
    capability_codec = O4OperationCapabilityCodec("pilot-secret-that-is-at-least-32-bytes")
    deliver_claims = capability_codec.verify(
        (root / ".codex" / "o4_operations_capability.token").read_text(encoding="utf-8"),
        public_key=capability_codec.public_key,
    )
    assert deliver_claims.node is CodexMonitoringO4Node.DELIVER
    assert set(deliver_claims.enabled_tool_ids) == {
        "crawler_plane.certify",
        "crawler_plane.add_regression",
        "crawler_plane.create_version",
        "crawler_plane.execute",
        "crawler_plane.get",
        "crawler_plane.get_cassette",
        "crawler_plane.get_execution",
        "crawler_plane.list",
        "crawler_plane.live_probe",
        "crawler_plane.promote",
        "crawler_plane.register_source",
        "monitoring.get_source",
        "monitoring.get_ticker_config",
        "monitoring.list_sources",
        "monitoring.list_status",
        "monitoring.register_source",
        "monitoring.update_source",
        "monitoring.update_ticker_config",
    }
    delivery_issue_log = root / "requests" / "mu-o4-pilot-001_deliver" / "audit" / "pilot_issues.md"
    assert delivery_issue_log.is_file()
    assert "- 节点：`o4_deliver`" in delivery_issue_log.read_text(encoding="utf-8")
    assert "requests/mu-o4-pilot-001_deliver/audit/pilot_issues.md" in (
        root / "PILOT_TASK.md"
    ).read_text(encoding="utf-8")


def test_pilot_refresh_reissues_active_node_capability_without_resetting_case(
    tmp_path: Path,
) -> None:
    coordinator, root, policy = _prepare(tmp_path)
    configure = _configure_completion(policy, new_crawler=True)
    configure_output = root / "requests" / configure.request_id / "output" / "completion.json"
    configure_output.write_text(configure.model_dump_json(indent=2), encoding="utf-8")
    coordinator.advance(root)

    refreshed = coordinator.refresh(root)

    assert refreshed.status == "refreshed"
    assert refreshed.node is CodexMonitoringO4Node.DELIVER
    state = coordinator.status(root)
    assert state["active_node"] == CodexMonitoringO4Node.DELIVER.value
    assert state["capability_refresh_count"] == 1
    assert state["expected_active_capability_tools"]

    delivery = DeliverySettlement(
        request_id="mu-o4-pilot-001_deliver",
        plan_id="pilot-plan",
        plan_version=1,
        ticker="MU",
        items=[
            DeliveryItemSettlement(
                source_need_id="need-1",
                status=DeliveryItemStatus.FAILED,
                constraints=["Pilot fixture deliberately stops before a live source"],
                evidence=["bounded deterministic fixture"],
            )
        ],
        summary="One approved crawler remains undelivered",
    )
    delivery_output = root / "requests" / delivery.request_id / "output" / "completion.json"
    delivery_output.write_text(delivery.model_dump_json(indent=2), encoding="utf-8")
    checkpoint_path = root / "requests" / delivery.request_id / "delivery_checkpoint.json"
    checkpoint = DeliveryCheckpoint.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint.items[0].candidate_id = "issuer-ir"
    checkpoint.items[0].progress_state = DeliveryProgressState.INFEASIBLE
    checkpoint.items[0].status = DeliveryItemStatus.FAILED
    checkpoint.items[0].latest_evidence_refs = ["bounded deterministic fixture"]
    checkpoint.items[0].latest_blocker = "approved source unavailable in fixture"
    checkpoint_path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")

    completed = coordinator.advance(root)
    assert completed.status == "completed"
    status = coordinator.status(root)
    assert status["result"] == "DEGRADED"
    assert status["incomplete_delivery_items"] == 1
    assert status["monitoring_started"] is True
    assert status["pilot_issue_logs"] == [
        "requests/mu-o4-pilot-001_configure/audit/pilot_issues.md",
        "requests/mu-o4-pilot-001_deliver/audit/pilot_issues.md",
    ]
    completed_task = (root / "PILOT_TASK.md").read_text(encoding="utf-8")
    assert "Review the node-local Pilot issue logs" in completed_task
    bus = MessageBusV2Repository(root / "state" / "message_bus.sqlite3")
    try:
        ticker_state = bus.get_ticker_state("MU")
        assert ticker_state is not None
        assert ticker_state.status is TickerMonitoringStatus.RUNNING
    finally:
        bus.close()


def test_clone_deliver_case_restarts_from_configure_output_without_secret(
    tmp_path: Path,
) -> None:
    coordinator, source, policy = _prepare(tmp_path)
    configure = _configure_completion(policy, new_crawler=True)
    configure_output = source / "requests" / configure.request_id / "output" / "completion.json"
    configure_output.write_text(configure.model_dump_json(indent=2), encoding="utf-8")
    coordinator.advance(source)
    MessageBusV2Repository(source / "state" / "message_bus.sqlite3").close()
    crawler_working = source / "crawler-plane" / "working" / "company_ir_reference" / "v1"
    crawler_working.mkdir(parents=True)
    crawler_repository = CrawlerPlaneRepository(source / "state" / "crawler_plane.sqlite3")
    try:
        crawler_repository.save_package(
            CrawlerPackage(crawler_id="company_ir_reference", latest_version=1)
        )
        crawler_repository.save_version(
            CrawlerVersion(
                version_id="company_ir_reference:v1",
                spec=CrawlerVersionSpec(
                    crawler_id="company_ir_reference",
                    version=1,
                    parameter_schema={"type": "object"},
                ),
                working_path=str(crawler_working),
            )
        )
    finally:
        crawler_repository.close()

    source_state_before = (source / "coordinator_state.json").read_bytes()
    source_plan_before = (source / "context" / "configuration_plan.json").read_bytes()
    target = tmp_path / "cases" / "mu-o4-pilot-001-node2-rerun" / source.name

    event = MonitoringO4PilotCoordinator.clone_deliver_case(
        source_case_root=source,
        target_case_root=target,
    )

    assert event.status == "created"
    assert event.node is CodexMonitoringO4Node.DELIVER
    assert (
        json.loads((target / "coordinator_state.json").read_text(encoding="utf-8"))["active_node"]
        == CodexMonitoringO4Node.DELIVER.value
    )
    target_state = json.loads((target / "coordinator_state.json").read_text(encoding="utf-8"))
    assert target_state["case_id"] == "mu-o4-pilot-001-node2-rerun"
    assert target_state["clone_of_case_id"] == "mu-o4-pilot-001"
    assert target_state["capability_reused"] is True
    assert target_state["source_case_root"] == str(source.resolve())
    assert not (
        target / "requests" / "mu-o4-pilot-001_deliver" / "output" / "completion.json"
    ).exists()
    assert (
        target / "requests" / "mu-o4-pilot-001_configure" / "output" / "completion.json"
    ).read_bytes() == configure_output.read_bytes()
    assert (target / "context" / "configuration_plan.json").read_bytes() == source_plan_before
    assert (source / "coordinator_state.json").read_bytes() == source_state_before

    checkpoint = json.loads(
        (target / "requests" / "mu-o4-pilot-001_deliver" / "delivery_checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["status"] for item in checkpoint["items"]] == ["PENDING"]
    assert all(item["stage"] == "WORKING_VERSION_RETAINED" for item in checkpoint["items"])
    assert all(item["cycles_used"] == 0 for item in checkpoint["items"])

    config_text = (target / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert str(source) not in config_text
    assert str(target).replace("\\", "\\\\") in config_text
    config = tomllib.loads(config_text)
    environment = config["mcp_servers"]["o4_operations"]["env"]
    codec = O4OperationCapabilityCodec("pilot-secret-that-is-at-least-32-bytes")
    claims = codec.verify(
        environment["DOXAGENT_O4_OPERATIONS_CAPABILITY"],
        public_key=environment["DOXAGENT_O4_OPERATIONS_PUBLIC_KEY"],
    )
    assert claims.node is CodexMonitoringO4Node.DELIVER
    assert claims.run_id == target.name
    assert claims.request_id == "mu-o4-pilot-001_deliver"
    assert "O4_DELIVER` only" in (target / "PILOT_TASK.md").read_text(encoding="utf-8")
    assert "do not run O4_CONFIGURE" in (target / "PILOT_TASK.md").read_text(encoding="utf-8")

    no_secret = MonitoringO4PilotCoordinator(
        repo_root=Path(__file__).resolve().parents[1],
        cases_root=tmp_path / "cases",
        python=sys.executable,
        capability_secret=None,
    )
    assert no_secret.status(target)["active_node"] == CodexMonitoringO4Node.DELIVER.value
    assert no_secret.advance(target).status == "waiting"

    crawler_repository = CrawlerPlaneRepository(target / "state" / "crawler_plane.sqlite3")
    try:
        version = crawler_repository.get_version("company_ir_reference", 1)
        assert version is not None
        assert version.working_path is not None
        assert str(target) in version.working_path
    finally:
        crawler_repository.close()


def test_pilot_ends_after_configure_when_plan_has_no_crawler_gap(tmp_path: Path) -> None:
    coordinator, root, policy = _prepare(tmp_path)
    configure = _configure_completion(policy, new_crawler=False)
    output = root / "requests" / configure.request_id / "output" / "completion.json"
    output.write_text(configure.model_dump_json(indent=2), encoding="utf-8")

    completed = coordinator.advance(root)

    assert completed.status == "completed"
    state = coordinator.status(root)
    assert state["result"] == "CONFIGURE_ONLY_NO_CRAWLER_GAP"
    assert state["delivery_request_id"] is None


def test_pilot_rejects_completed_delivery_without_real_control_plane_proof(
    tmp_path: Path,
) -> None:
    coordinator, root, policy = _prepare(tmp_path)
    configure = _configure_completion(policy, new_crawler=True)
    configure_output = root / "requests" / configure.request_id / "output" / "completion.json"
    configure_output.write_text(configure.model_dump_json(indent=2), encoding="utf-8")
    coordinator.advance(root)
    delivery = DeliverySettlement(
        request_id="mu-o4-pilot-001_deliver",
        plan_id="pilot-plan",
        plan_version=1,
        ticker="MU",
        items=[
            DeliveryItemSettlement(
                source_need_id="need-1",
                status=DeliveryItemStatus.COMPLETED,
                selected_candidate_id="issuer-ir",
                crawler_id="issuer-ir",
                crawler_version=1,
                certification_run_id="cert_missing",
                source_id="issuer-ir",
                binding_id="MU:issuer-ir",
            )
        ],
        summary="Unsupported success claim",
    )
    output = root / "requests" / delivery.request_id / "output" / "completion.json"
    output.write_text(delivery.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="ACTIVE certified release"):
        coordinator.advance(root)


def test_pilot_refuses_to_advance_after_frozen_input_tampering(tmp_path: Path) -> None:
    coordinator, root, _ = _prepare(tmp_path)
    policy_path = root / "context" / "policy_set.json"
    policy_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    policy_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="frozen O4 Pilot input changed"):
        coordinator.advance(root)
