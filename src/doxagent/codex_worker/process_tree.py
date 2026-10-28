"""Owned capsule process identities. Linux descendants include detached MCP children."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from pathlib import Path

_snapshot = {}
_sampled_at = 0.0
_snapshot_lock = threading.Lock()


def process_owners(*, refresh=False):
    """One host scan serves all owners; signals still check PID start ticks."""
    global _snapshot, _sampled_at
    with _snapshot_lock:
        if refresh or time.monotonic() - _sampled_at >= 1:
            _snapshot = _scan_owners()
            _sampled_at = time.monotonic()
        return _snapshot


def owned_processes(identity: str, *, refresh=False) -> dict[int, tuple[str, int]]:
    return process_owners(refresh=refresh).get(identity, {}).copy()


def _scan_owners():
    result = {}
    if os.name != "posix" or not Path("/proc").is_dir():
        return result
    marker = b"DOXAGENT_CAPSULE_ID="
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            identity = next((part[len(marker):].decode() for part in
                (directory / "environ").read_bytes().split(b"\0") if part.startswith(marker)), None)
            if not identity:
                continue
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z":
                result.setdefault(identity, {})[int(directory.name)] = (
                    fields[19],
                    int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
                )
        except (OSError, ValueError, IndexError):
            continue
    return result


def signal_owned(owned: dict[int, tuple[str, int]], sig: int) -> None:
    for pid, (started, _) in owned.items():
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if fields[19] == started:
                os.kill(pid, sig)
        except (OSError, IndexError):
            pass


class PopenProcess:
    """Async view of the SDK's synchronous Popen; retain it across client.close."""

    def __init__(self, process):
        self.process = process

    @property
    def pid(self):
        return self.process.pid

    @property
    def returncode(self):
        return self.process.poll()

    async def wait(self):
        # Popen.wait in a thread cannot be cancelled if the process is stuck.
        # Poll only while this bounded cleanup is alive; leave no blocked thread.
        while self.returncode is None:
            await asyncio.sleep(0.05)
        return self.returncode


async def terminate_owned(process, identity: str, *, kill_group=True) -> None:
    if os.name == "nt":
        if process is None:
            return
        if process.returncode is None:
            import subprocess

            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await asyncio.wait_for(killer.wait(), 10)
        await asyncio.wait_for(process.wait(), 5)
        return
    # Match a random execution identity and process start time, not executable name.
    for sig, deadline in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        end = asyncio.get_running_loop().time() + deadline
        while True:
            owned = await asyncio.to_thread(owned_processes, identity)
            await asyncio.to_thread(signal_owned, owned, sig)
            if (
                kill_group
                and process is not None
                and process.returncode is None
                and process.pid in owned
            ):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
            if not owned and (process is None or process.returncode is not None):
                if process is not None:
                    await process.wait()
                return
            if asyncio.get_running_loop().time() >= end:
                break
            await asyncio.sleep(0.1)
    raise RuntimeError("CAPSULE_CLEANUP_QUARANTINED")
