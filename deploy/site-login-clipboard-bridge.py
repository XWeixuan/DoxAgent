#!/usr/bin/python3
"""Forward desktop clipboard text into the maintenance Chrome VNC display."""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import struct
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def _read_exact(connection: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise ConnectionError("VNC connection closed")
        data.extend(part)
    return bytes(data)


def _send_cut_text(value: str) -> None:
    payload = value.encode("utf-8")
    with socket.create_connection(("127.0.0.1", 5900), timeout=3) as connection:
        connection.settimeout(3)
        version = _read_exact(connection, 12)
        if version not in (b"RFB 003.007\n", b"RFB 003.008\n"):
            raise ConnectionError("Unsupported VNC version")
        connection.sendall(version)
        count = _read_exact(connection, 1)[0]
        choices = _read_exact(connection, count)
        if 1 not in choices:
            raise ConnectionError("VNC clipboard connection unavailable")
        connection.sendall(b"\x01")
        if _read_exact(connection, 4) != b"\x00" * 4:
            raise ConnectionError("VNC clipboard connection rejected")
        connection.sendall(b"\x01")
        header = _read_exact(connection, 24)
        name_length = struct.unpack("!I", header[20:24])[0]
        if name_length > 4096:
            raise ConnectionError("Invalid VNC server name")
        _read_exact(connection, name_length)
        connection.sendall(b"\x06\x00\x00\x00" + struct.pack("!I", len(payload)) + payload)
        time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer-pid", type=int, required=True)
    viewer_pid = parser.parse_args().viewer_pid
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    last_digest: bytes | None = None
    last_sent_at = 0.0

    def forward() -> bool:
        nonlocal last_digest, last_sent_at
        try:
            value = clipboard.wait_for_text()
            if value is None:
                return False
            digest = hashlib.sha256(value.encode("utf-8")).digest()
            now = time.monotonic()
            if digest == last_digest and now - last_sent_at < 1:
                return False
            _send_cut_text(value)
            last_digest, last_sent_at = digest, now
        except (ConnectionError, OSError, ValueError):
            pass
        return False

    def viewer_alive() -> bool:
        try:
            os.kill(viewer_pid, 0)
        except OSError:
            Gtk.main_quit()
            return False
        return True

    clipboard.connect("owner-change", lambda *_: GLib.idle_add(forward))
    GLib.idle_add(forward)
    GLib.timeout_add_seconds(2, viewer_alive)
    Gtk.main()


if __name__ == "__main__":
    main()
