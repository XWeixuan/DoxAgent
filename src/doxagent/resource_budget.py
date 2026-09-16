"""Bounded local resource admission. No business API or database access."""

from __future__ import annotations

import json
import os
import socket
import threading
from contextlib import contextmanager
from uuid import uuid4


def request(command: str, **values):
    path = os.getenv("DOXAGENT_RESOURCE_SOCKET")
    if not path:
        return {"ok": True, "disabled": True}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(0.75)
            channel.connect(path)
            channel.sendall(json.dumps({"command": command, **values}).encode() + b"\n")
            data = b""
            while b"\n" not in data and len(data) < 8192:
                part = channel.recv(8192)
                if not part:
                    break
                data += part
            return json.loads(data)
    except (OSError, ValueError):
        # Do not dispatch additional high-memory work when the guardian is unavailable.
        return {"ok": False, "reason": "RESOURCE_GUARD_UNAVAILABLE"}


def acquire(kind: str, identity: str, *, batch: str | None = None, slots: int = 1):
    result = acquire_admission(kind, identity, batch=batch, slots=slots)
    return result.get("token", "disabled") if result.get("ok") else None


def acquire_admission(kind: str, identity: str, *, batch: str | None = None, slots: int = 1):
    return request("acquire", kind=kind, identity=identity, batch=batch, slots=slots)


def release(token):
    if token and token != "disabled":
        request("release", token=token)


@contextmanager
def renew(token):
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(30):
            request("renew", token=token)

    thread = None
    if token and token != "disabled":
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=1)


@contextmanager
def work(kind: str, *, batch: str | None = None):
    token = acquire(kind, uuid4().hex, batch=batch)
    try:
        with renew(token):
            yield token is not None
    finally:
        release(token)
