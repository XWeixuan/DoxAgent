from datetime import UTC, datetime

import pytest

from cdecr.bulk_epoch.task_ledger import BulkTaskLedger
from cdecr.registry import SQLiteCDECRRegistry
from doxagent.ticker_initialization import InitializationRepository, NodeContext, NodeSpec
from doxagent.ticker_initialization.cdecr_tasks import NativeTaskObserver
from doxagent.ticker_initialization.schema import BudgetExhausted, NodeResult


def test_native_retry_budget_survives_parent_retry_and_manual_resume(tmp_path):
    control = InitializationRepository(tmp_path / "control.db")
    run = control.submit("MU", datetime.now(UTC), [NodeSpec(key="cdecr", block="CDECR")])
    lease = control.claim("owner")
    parent = control.begin(lease, "cdecr", {})
    registry = SQLiteCDECRRegistry(tmp_path / "native.db")
    registry.initialize()
    registry.start_bulk_epoch(
        epoch_id="epoch", manifest_hash="input", orchestrator_version="v2", message_ids=[]
    )
    observer = NativeTaskObserver(NodeContext(control, lease, parent))
    registry.initialization_task_observer = observer
    ledger = BulkTaskLedger(registry=registry, epoch_id="epoch")
    task = dict(stage="N9", task_id="mention", input_hash="input", snapshot_hash="snapshot")
    for _ in range(2):
        ledger.start(**task)
        ledger.fail(**task, error_code="offline", status="FAILED_RETRYABLE")
    with pytest.raises(BudgetExhausted):
        ledger.start(**task)
    assert registry.list_bulk_epoch_tasks("epoch")[0]["attempt_count"] == 2
    child = next(n for n in control.nodes(run.initialization_id) if n.inputs.get("managed_by"))
    control.fail(lease, "cdecr", "native exhausted")
    control.finish(lease, error="native exhausted")
    control.resume(run.initialization_id, node_key=child.key, reason="operator retry")
    lease = control.claim("new-owner")
    parent = control.begin(lease, "cdecr", {})
    registry.initialization_task_observer = NativeTaskObserver(NodeContext(control, lease, parent))
    ledger.start(**task)
    ledger.finish(**task, decision_ref={"decision": "usable"})
    control.complete(lease, "cdecr", NodeResult())
    assert control.finish(lease).status == "SUCCEEDED"
    assert control.nodes(run.initialization_id)[1].ordinal == 1


def test_committed_degraded_and_crash_receipts_reconcile_without_new_attempt(tmp_path):
    control = InitializationRepository(tmp_path / "control.db")
    run = control.submit("MU", datetime.now(UTC), [NodeSpec(key="cdecr", block="CDECR")])
    lease = control.claim("owner")
    parent = control.begin(lease, "cdecr", {})
    registry = SQLiteCDECRRegistry(tmp_path / "native.db")
    registry.initialize()
    registry.start_bulk_epoch(
        epoch_id="epoch", manifest_hash="input", orchestrator_version="v2", message_ids=[]
    )
    observer = NativeTaskObserver(NodeContext(control, lease, parent))
    registry.initialization_task_observer = observer
    ledger = BulkTaskLedger(registry=registry, epoch_id="epoch")
    task = dict(stage="N9", task_id="mention", input_hash="input", snapshot_hash="snapshot")
    ledger.start(**task)
    ledger.fail(**task, error_code="partial")
    # Native engine accepts a fail-open decision; that is usable, not a third retry.
    registry.initialization_task_observer = None
    ledger.finish(**task, decision_ref={"degraded": True})
    observer.reconcile(registry)
    child = next(n for n in control.nodes(run.initialization_id) if n.inputs.get("managed_by"))
    assert child.status == "SUCCEEDED" and child.ordinal == 1


def test_finalized_epoch_reconciles_stale_running_native_receipts(tmp_path):
    control = InitializationRepository(tmp_path / "control.db")
    run = control.submit("MU", datetime.now(UTC), [NodeSpec(key="cdecr", block="CDECR")])
    lease = control.claim("owner")
    parent = control.begin(lease, "cdecr", {})
    registry = SQLiteCDECRRegistry(tmp_path / "native.db")
    registry.initialize()
    registry.start_bulk_epoch(
        epoch_id="epoch", manifest_hash="input", orchestrator_version="v2", message_ids=[]
    )
    observer = NativeTaskObserver(NodeContext(control, lease, parent))
    registry.initialization_task_observer = observer
    ledger = BulkTaskLedger(registry=registry, epoch_id="epoch")
    task = dict(stage="FIELD", task_id="stale", input_hash="input", snapshot_hash="snapshot")
    ledger.start(**task)

    registry.update_bulk_epoch("epoch", status="FINALIZED", current_stage="FINALIZED")
    observer.reconcile(registry)

    child = next(n for n in control.nodes(run.initialization_id) if n.inputs.get("managed_by"))
    assert child.status == "SUCCEEDED" and child.ordinal == 1
    control.complete(lease, "cdecr", NodeResult())
    assert control.finish(lease).status == "SUCCEEDED"
