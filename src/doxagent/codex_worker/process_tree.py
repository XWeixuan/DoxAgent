"""Owned capsule process identities. Linux descendants include detached MCP children."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path


def owned_processes(identity: str) -> dict[int, tuple[str, int]]:
    result: dict[int, tuple[str, int]] = {}
    if os.name != "posix" or not Path("/proc").is_dir():
        return result
    marker = ("DOXAGENT_CAPSULE_ID=" + identity).encode()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            if marker not in (directory / "environ").read_bytes().split(b"\0"):
                continue
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z":
                result[int(directory.name)] = (
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


async def terminate_owned(process: asyncio.subprocess.Process, identity: str) -> None:
    if os.name == "nt":
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
            if process.returncode is None and process.pid in owned:
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
            if not owned and process.returncode is not None:
                await process.wait()
                return
            if asyncio.get_running_loop().time() >= end:
                break
            await asyncio.sleep(0.1)
    raise RuntimeError("CAPSULE_CLEANUP_QUARANTINED")
