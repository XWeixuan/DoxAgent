"""Lease-owned CDECR subprocess: nonblocking parent, bounded orphan lifetime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from doxagent.cdecr_integration.contracts import CDECRWorkflowResult, RuntimeRegistryBinding

from .repository import InitializationRepository
from .schema import Lease
from .service import NodeContext


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


async def execute_cdecr(
    context: NodeContext,
    binding: RuntimeRegistryBinding,
    message_ids: list[str],
    as_of: datetime,
) -> CDECRWorkflowResult:
    root = context.repository.path.parent / "cdecr-dispatches" / str(context.node.execution_id)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "input.json"
    output = root / "result.json"
    payload = {
        "control": str(context.repository.path),
        "lease": context.lease.model_dump(),
        "node_key": context.node.key,
        "binding": binding.model_dump(),
        "message_ids": message_ids,
        "as_of": as_of.isoformat(),
        "output": str(output),
    }
    _atomic_json(manifest, payload)
    context.checkpoint(cdecr_dispatch=str(manifest))
    # Only the child writes this log; never relay model/provider logs to CLI stdout.
    with (root / "worker.log").open("ab") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "doxagent.ticker_initialization.cdecr_process",
            "--input",
            str(manifest),
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            code = await process.wait()
        except BaseException:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise
    context.repository.assert_lease(context.lease)
    if code != 0 or not output.is_file():
        raise RuntimeError(f"CDECR child stopped (exit={code}); dispatch={root.name}")
    result = CDECRWorkflowResult.model_validate_json(output.read_text(encoding="utf-8"))
    if result.ticker != binding.ticker or result.runtime_scope != binding.runtime_scope:
        raise ValueError("CDECR child result identity mismatch")
    return result


@contextmanager
def _exclusive_registry(path: Path) -> Iterator[None]:
    """OS-owned lock releases even when a stale process is forcibly terminated."""
    lock_path = path.with_suffix(path.suffix + ".initialization.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import importlib

            fcntl: Any = importlib.import_module("fcntl")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run_child(payload: dict[str, Any], *, runner_factory: Callable[..., Any] | None = None) -> None:
    from doxagent.cdecr_integration.runtime_factory import build_cdecr_workflow_runner

    control = InitializationRepository(payload["control"])
    lease = Lease.model_validate(payload["lease"])
    control.assert_lease(lease)
    stopped = threading.Event()

    def watchdog() -> None:
        while not stopped.wait(0.5):
            try:
                control.assert_lease(lease)
            except Exception:
                # Do not permit model threads to continue after their owner vanished.
                os._exit(75)

    guard = threading.Thread(target=watchdog, daemon=True, name="cdecr-owner-watchdog")
    guard.start()
    try:
        binding = RuntimeRegistryBinding.model_validate(payload["binding"])
        with _exclusive_registry(Path(binding.registry_path)):
            control.assert_lease(lease)
            registry, runner = (runner_factory or build_cdecr_workflow_runner)(binding)
            observer = None
            if registry is not None and payload.get("node_key"):
                from .cdecr_tasks import NativeTaskObserver

                node = next(
                    n
                    for n in control.nodes(lease.initialization_id)
                    if n.key == payload["node_key"]
                )
                observer = NativeTaskObserver(NodeContext(control, lease, node))
                cast(Any, registry).initialization_task_observer = observer
                observer.reconcile(registry)
            try:
                result = runner.run(
                    payload["message_ids"], as_of=datetime.fromisoformat(payload["as_of"])
                )
            finally:
                if observer is not None:
                    observer.reconcile(registry)
            control.assert_lease(lease)
            _atomic_json(Path(payload["output"]), result.model_dump(mode="json"))
    finally:
        stopped.set()
        guard.join(timeout=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    run_child(json.loads(args.input.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
