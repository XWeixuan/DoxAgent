"""Host-only, authenticated market-history bridge with a fixed read operation."""
import argparse
import hmac
import json
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .live_evaluation import remote_python


def validate(value, ticker):
    if value.get("ticker") != ticker:
        raise ValueError("ticker outside this evaluation")
    allowed = {("1 day", "10 D"), ("1 hour", "2 D"),
               ("5 mins", "14400 S"), ("5 mins", "5400 S")}
    if (value.get("bar"), value.get("duration")) not in allowed:
        raise ValueError("unsupported market-history window")
    if value.get("venue") not in {"SMART", "OVERNIGHT"}:
        raise ValueError("unsupported venue")
    stamp = datetime.fromisoformat(value["end"])
    if stamp.tzinfo is None or stamp > datetime.now(UTC):
        raise ValueError("end must be an aware past timestamp")


def serve(ticker, token, port, audit, host, servers=None):
    cache, lock = {}, threading.Lock()
    audit = Path(audit)
    audit.parent.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/history" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 2048:
                    raise ValueError("invalid body")
                value = json.loads(self.rfile.read(length))
                validate(value, ticker)
                # Reuse same-minute read receipts across parallel evaluations.
                key = (value["bar"], value["duration"], value["venue"], value["end"][:16])
                with lock:
                    cached = cache.get(key)
                    if cached and time.monotonic() - cached[0] < 60:
                        result = cached[1]
                    else:
                        script = """
import json
from datetime import datetime
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.w3_market import MarketProvider
settings=DoxAgentSettings()
provider=MarketProvider(settings)
try:
 bars=provider.history(ARGS['ticker'],ARGS['bar'],ARGS['duration'],ARGS['venue'],datetime.fromisoformat(ARGS['end']))
 print(json.dumps(dict(bars=bars),default=str))
finally:provider.close()
""".replace("ARGS", repr(value))
                        result = remote_python(script, host)
                        cache[key] = (time.monotonic(), result)
                    with audit.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(dict(at=datetime.now(UTC).isoformat(), request=value,
                                                     bar_count=len(result["bars"].get("bars", [])))) + "\n")
            except Exception as exc:
                result = dict(error=str(exc))
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    if servers is not None:
        servers.append(server)
    print(json.dumps(dict(port=server.server_port, ticker=ticker)), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--host", default="doxagent-sg")
    args = parser.parse_args()
    serve(args.ticker, args.token_file.read_text().strip(), args.port, args.audit, args.host)
