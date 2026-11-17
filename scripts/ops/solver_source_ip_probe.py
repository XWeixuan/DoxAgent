"""Temporary TCP accept-and-close source probe; never reads or forwards data."""
import argparse
import ipaddress
import os
from pathlib import Path
import socket
import time


def listen(output: Path, ttl_seconds: int) -> None:
    deadline = time.monotonic() + ttl_seconds
    seen = set()
    # A probe run gets a new private file. Refuse to overwrite older evidence.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as log:
        with socket.create_server(("0.0.0.0", 18792), backlog=8) as server:
            server.settimeout(1)
            print("LISTENING 18792", flush=True)
            while time.monotonic() < deadline and len(seen) < 64:
                try:
                    connection, peer = server.accept()
                except socket.timeout:
                    continue
                # Do not receive CONNECT, credentials, TLS, or any payload.
                connection.close()
                address = ipaddress.ip_address(peer[0])
                if not address.is_global or str(address) in seen:
                    continue
                seen.add(str(address))
                log.write(str(address) + "\n")
                log.flush()
    print("STOPPED", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 30 <= args.ttl_seconds <= 1800:
        parser.error("TTL must be between 30 and 1800 seconds")
    listen(args.output, args.ttl_seconds)
