"""Runtime delivery completes on durable acceptance, never on a synthetic fill."""

from typing import Any

from doxagent.persistent_runtime_v2.trade_output import LocalTradeSink

from .repository import ExecutionRepository


class ExecutionIntake:
    def __init__(self, journal: Any) -> None:
        self.repository = ExecutionRepository(journal)
        self.local = LocalTradeSink(journal)

    async def readiness(self) -> Any:
        return {"ready": True, "mode": "durable_execution_intake"}

    async def submit(self, intent: Any) -> Any:
        if not intent.get("execution_pin"):
            return await self.local.submit(intent)
        return self.repository.admit(intent)

    async def reconcile(self, intent_id: Any) -> Any:
        if self.repository.get("executions", intent_id):
            return {"intent_id": intent_id, "status": "EXECUTION_ACCEPTED"}
        return await self.local.reconcile(intent_id)
