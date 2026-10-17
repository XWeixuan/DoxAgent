"""PID-fenced process adoption. Unknown writers are never unlocked or killed."""

import asyncio
import os
import signal
from pathlib import Path


def process_record(pid):
    path = Path(f"/proc/{int(pid)}")
    try:
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {
            "pid": int(pid),
            "start_time": fields[19],
            "parent_pid": int(fields[1]),
            "arguments": [
                part.decode(errors="replace")
                for part in (path / "cmdline").read_bytes().split(b"\0")
                if part
            ],
        }
    except (OSError, ValueError, IndexError):
        return None


class AdoptedProcess:
    def __init__(self, record):
        self.pid = record["pid"]
        self.start_time = record["start_time"]

    @property
    def returncode(self):
        current = process_record(self.pid)
        return None if current and current["start_time"] == self.start_time else 0

    def send_signal(self, signum):
        if self.returncode is None:
            os.kill(self.pid, signum)

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.1)
        return self.returncode


class MissingProcess:
    pid = 0
    returncode = 0

    def terminate(self):
        pass

    async def wait(self):
        return 0


def validated_process(record, *, required_arguments):
    current = process_record(record["pid"])
    if (
        current is None
        or current["start_time"] != record["start_time"]
        or not set(required_arguments) <= set(current["arguments"])
    ):
        raise RuntimeError("instance_manifest_process_mismatch")
    return AdoptedProcess(current)
