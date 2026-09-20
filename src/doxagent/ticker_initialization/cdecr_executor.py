"""Single-purpose durable CDECR execution worker."""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

from doxagent.settings import DoxAgentSettings

from .provenance import execution_version
from .repository import InitializationRepository
from .schema import Lease, LeaseLost, NodeResult
from .service import NodeContext


class CDECRExecutionWorker:
    def __init__(
        self,
        repository: InitializationRepository,
        settings: DoxAgentSettings,
        *,
        identity: str,
        lease_seconds: float = 60,
    ) -> None:
        self.repository = repository
        self.settings = settings.model_copy(
            update={
                # This worker is already the selected production execution domain.
                # A stale prebuilt reference must not reactivate the archived path.
                "cdecr_execution_mode": "LOCAL_ONLY",
                "cdecr_dispatch_identity": identity,
            }
        )
        self.identity = identity
        self.lease_seconds = lease_seconds
        self.owner = f"cdecr-executor-{uuid4().hex}"

    async def run_once(self) -> bool:
        claimed = self.repository.claim_cdecr_dispatch(
            self.owner,
            self.identity,
            execution_version=execution_version(),
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            return False
        dispatch, lease = claimed
        dispatch_id = str(dispatch["dispatch_id"])
        generation = int(dispatch["generation"])
        heartbeat = asyncio.create_task(
            self._heartbeat(lease, dispatch_id, generation),
            name=f"cdecr-heartbeat:{dispatch_id}",
        )
        try:
            await self._execute(dispatch, lease, generation)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _heartbeat(self, lease: Lease, dispatch_id: str, generation: int) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            self.repository.heartbeat_cdecr_dispatch(
                lease,
                dispatch_id,
                generation,
                lease_seconds=self.lease_seconds,
            )

    async def _execute(self, dispatch: dict[str, object], lease: Lease, generation: int) -> None:
        from .research_adapter import ResearchInitializationAdapter

        owned = lease
        dispatch_id = str(dispatch["dispatch_id"])
        node_key = str(dispatch["node_key"])
        node = next(
            item
            for item in self.repository.nodes(owned.initialization_id)
            if item.key == node_key
        )
        if node.status == "SUCCEEDED":
            self.repository.settle_cdecr_dispatch(
                owned,
                dispatch_id,
                generation,
                status="SUCCEEDED",
                result_ref=f"initialization-node:{owned.initialization_id}:{node_key}",
            )
            return
        if not self.repository.cdecr_dispatch_input_matches(dispatch, node):
            self.repository.settle_cdecr_dispatch(
                owned,
                dispatch_id,
                generation,
                status="CANCELLED",
                error="CDECR dispatch input identity changed before execution",
            )
            return
        if node.status != "RUNNING" or node.execution_id != dispatch["execution_id"]:
            self.repository.settle_cdecr_dispatch(
                owned,
                dispatch_id,
                generation,
                status="CANCELLED",
                error="node execution identity changed before CDECR claim",
            )
            return
        context = NodeContext(self.repository, owned, node)
        adapter = ResearchInitializationAdapter(self.settings)
        previous_role = os.environ.get("DOXAGENT_CDECR_EXECUTOR_PROCESS")
        os.environ["DOXAGENT_CDECR_EXECUTOR_PROCESS"] = "1"
        try:
            result = await adapter.execute(context)
            if not isinstance(result, NodeResult):
                raise TypeError("CDECR executor returned an invalid node result")
            self.repository.complete(owned, node_key, result)
            try:
                await adapter.after_complete(context, result)
                self.repository.clear_post_complete_warning(owned, node_key)
            except Exception as exc:
                self.repository.post_complete_warning(
                    owned, node_key, f"{type(exc).__name__}: {exc}"[:1000]
                )
            self.repository.settle_cdecr_dispatch(
                owned,
                dispatch_id,
                generation,
                status="SUCCEEDED",
                result_ref=f"initialization-node:{owned.initialization_id}:{node_key}",
            )
        except LeaseLost:
            raise
        except Exception as exc:
            from doxagent.codex_runtime.recovery import failure_details

            try:
                context.checkpoint(failure=failure_details(exc))
                self.repository.fail(owned, node_key, f"{type(exc).__name__}: {exc}"[:4000])
                self.repository.settle_cdecr_dispatch(
                    owned,
                    dispatch_id,
                    generation,
                    status="FAILED",
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                )
            except LeaseLost:
                raise
        finally:
            if previous_role is None:
                os.environ.pop("DOXAGENT_CDECR_EXECUTOR_PROCESS", None)
            else:
                os.environ["DOXAGENT_CDECR_EXECUTOR_PROCESS"] = previous_role


async def run(
    *,
    once: bool,
    wait_seconds: float,
    identity: str | None,
) -> int:
    settings = DoxAgentSettings()
    path = settings.ticker_initialization_control_path or Path(
        ".tmp/ticker_initialization/control.sqlite3"
    )
    repository = InitializationRepository(path)
    worker = CDECRExecutionWorker(
        repository,
        settings,
        identity=identity or settings.cdecr_dispatch_identity,
    )
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        if await worker.run_once():
            if once:
                return 0
            continue
        if once and time.monotonic() >= deadline:
            return 0
        await asyncio.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doxagent-cdecr-executor")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0)
    parser.add_argument("--identity")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    if args.healthcheck:
        settings = DoxAgentSettings()
        InitializationRepository(
            settings.ticker_initialization_control_path
            or Path(".tmp/ticker_initialization/control.sqlite3")
        )
        return 0
    return asyncio.run(
        run(once=args.once, wait_seconds=args.wait_seconds, identity=args.identity)
    )


if __name__ == "__main__":
    raise SystemExit(main())
