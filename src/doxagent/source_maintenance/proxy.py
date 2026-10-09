"""Model-only CONNECT tunnel. No MITM, public DNS destinations, no arbitrary HTTP forwarding."""

from __future__ import annotations

import ipaddress
import os
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def destination(authority: str, allowed: set[str]) -> tuple[str, int]:
    host, separator, port = authority.rpartition(":")
    if not separator or port != "443" or host.lower() not in allowed:
        raise ValueError("destination outside model egress allowlist")
    return host.lower(), 443


class ModelProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_CONNECT(self):
        allowed = {
            x.strip().lower()
            for x in os.getenv(
                "SOURCE_MAINTENANCE_MODEL_HOSTS", "chatgpt.com,api.openai.com,auth.openai.com"
            ).split(",")
        }
        try:
            host, port = destination(self.path, allowed)
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("model DNS resolved to non-public network")
            family, kind, proto, _, address = addresses[0]
            with socket.socket(family, kind, proto) as upstream:
                upstream.settimeout(10)
                upstream.connect(address)
                self.send_response(200, "Connection established")
                self.end_headers()
                streams = [self.connection, upstream]
                for stream in streams:
                    stream.settimeout(60)
                while True:
                    readable, _, _ = select.select(streams, [], [], 60)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
        except (ValueError, OSError):
            self.close_connection = True

    def do_GET(self):
        self.send_error(403)

    def log_message(self, *args):
        pass  # Never log URL/headers/authentication.


def main():
    ThreadingHTTPServer(("0.0.0.0", 8030), ModelProxy).serve_forever()


if __name__ == "__main__":
    main()
