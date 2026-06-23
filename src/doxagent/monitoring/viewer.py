"""Local web viewer for the Monitoring Message Bus."""

# ruff: noqa: E501

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from doxagent.monitoring.schema import MonitoringParameters, UpdateActor
from doxagent.monitoring.service import MonitoringBusService, snapshot_to_agent_payload
from doxagent.settings import DoxAgentSettings

CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


@dataclass(frozen=True)
class RemoteCommandResult:
    ok: bool
    payload: dict[str, Any]
    stdout: str
    stderr: str
    latency_ms: int
    command: str


class MonitoringViewerRuntime:
    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings or DoxAgentSettings()
        self.started_at = datetime.now(UTC)
        self.service = MonitoringBusService.from_settings(self.settings)

    @property
    def uptime_seconds(self) -> int:
        return int((datetime.now(UTC) - self.started_at).total_seconds())

    def local_status(self, *, ticker: str | None = None, limit: int = 100) -> dict[str, Any]:
        snapshot = self.service.status_snapshot(ticker=ticker, limit=limit)
        return {
            "scope": "local",
            "ok": True,
            "meta": self._meta(latency_ms=0, remote_command=None),
            **snapshot_to_agent_payload(snapshot),
        }

    def remote_status(self, *, ticker: str | None = None, limit: int = 100) -> dict[str, Any]:
        args = ["status", "--limit", str(limit)]
        if ticker:
            args.extend(["--ticker", ticker])
        result = self.run_remote_monitoring_cli(args)
        payload = result.payload if result.ok else _empty_snapshot()
        payload.update(
            {
                "scope": "remote",
                "ok": result.ok,
                "meta": self._meta(
                    latency_ms=result.latency_ms,
                    remote_command=result.command,
                    remote_error=(result.stderr or result.stdout) if not result.ok else None,
                ),
            }
        )
        return payload

    def poll_due(self, *, scope: str) -> dict[str, Any]:
        if scope == "remote":
            result = self.run_remote_monitoring_cli(["poll-due"])
            return result.payload if result.ok else _remote_error_payload(result)
        results = self.service.poll_due_once()
        return {"ok": True, "results": [item.model_dump(mode="json") for item in results]}

    def unbind(self, data: dict[str, Any], *, scope: str) -> dict[str, Any]:
        ticker = _required_text(data, "ticker").upper()
        source_id = _required_text(data, "source_id")
        if scope == "remote":
            result = self.run_remote_monitoring_cli(["unbind", ticker, "--source", source_id])
            return result.payload if result.ok else _remote_error_payload(result)
        removed = self.service.delete_ticker_source(ticker, source_id)
        return {"ok": True, "removed": removed, "ticker": ticker, "source_id": source_id}

    def delete_ticker(self, data: dict[str, Any], *, scope: str) -> dict[str, Any]:
        ticker = _required_text(data, "ticker").upper()
        if scope == "remote":
            result = self.run_remote_monitoring_cli(["delete-ticker", ticker])
            return result.payload if result.ok else _remote_error_payload(result)
        deleted_count = self.service.delete_ticker_config(ticker)
        return {"ok": True, "deleted_count": deleted_count, "ticker": ticker}

    def bind(
        self,
        data: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        ticker = _required_text(data, "ticker").upper()
        source_id = _required_text(data, "source_id")
        enabled = bool(data.get("enabled", True))
        replace = bool(data.get("replace", False))
        reason = _optional_text(data.get("reason"))
        parameters = MonitoringParameters(
            keywords=_string_list(data.get("keywords")),
            usernames=_string_list(data.get("usernames")),
            search_terms=_string_list(data.get("search_terms")),
            rss_urls=_string_list(data.get("rss_urls")),
            source_filters=_string_list(data.get("source_filters")),
        )
        poll_interval_seconds = data.get("poll_interval_seconds")
        if scope == "remote":
            args = ["bind", ticker, "--source", source_id]
            if not enabled:
                args.append("--disabled")
            if replace:
                args.append("--replace")
            if reason:
                args.extend(["--reason", reason])
            for value in parameters.keywords:
                args.extend(["--keyword", value])
            for value in parameters.usernames:
                args.extend(["--username", value])
            for value in parameters.search_terms:
                args.extend(["--search-term", value])
            for value in parameters.rss_urls:
                args.extend(["--rss-url", value])
            for value in parameters.source_filters:
                args.extend(["--source-filter", value])
            result = self.run_remote_monitoring_cli(args)
            parsed_interval = _optional_int(poll_interval_seconds)
            if parsed_interval is not None:
                interval_result = self.run_remote_monitoring_cli(
                    ["set-poll-interval", source_id, str(parsed_interval)]
                )
                if not interval_result.ok:
                    return _remote_error_payload(interval_result)
            return result.payload if result.ok else _remote_error_payload(result)

        binding = self.service.configure_ticker_source(
            ticker,
            source_id,
            parameters=parameters,
            enabled=enabled,
            updated_by=UpdateActor.USER,
            updated_reason=reason,
            merge=not replace,
        )
        source = None
        parsed_interval = _optional_int(poll_interval_seconds)
        if parsed_interval is not None:
            source = self.service.set_source_poll_interval(
                source_id,
                seconds=parsed_interval,
                updated_by=UpdateActor.USER,
            )
        return {
            "ok": True,
            "binding": binding.model_dump(mode="json"),
            "source": source.model_dump(mode="json") if source else None,
        }

    def run_remote_monitoring_cli(self, args: list[str]) -> RemoteCommandResult:
        remote_command = build_remote_monitoring_command(self.settings, args)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["ssh", self.settings.monitoring_remote_ssh_alias, remote_command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.monitoring_remote_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            stdout = _decode_subprocess_text(exc.stdout)
            stderr = _decode_subprocess_text(exc.stderr)
            return RemoteCommandResult(
                ok=False,
                payload={},
                stdout=stdout,
                stderr=(
                    stderr
                    or f"Remote monitoring CLI timed out after "
                    f"{self.settings.monitoring_remote_timeout_seconds}s."
                ),
                latency_ms=latency_ms,
                command=remote_command,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        payload: dict[str, Any] = {}
        ok = completed.returncode == 0
        if ok:
            try:
                decoded = json.loads(stdout)
                if isinstance(decoded, dict):
                    payload = decoded
                else:
                    ok = False
                    stderr = "Remote monitoring CLI did not return a JSON object."
            except json.JSONDecodeError as exc:
                ok = False
                stderr = f"Remote monitoring CLI returned invalid JSON: {exc}"
        return RemoteCommandResult(
            ok=ok,
            payload=payload,
            stdout=stdout,
            stderr=stderr,
            latency_ms=latency_ms,
            command=remote_command,
        )

    def _meta(
        self,
        *,
        latency_ms: int,
        remote_command: str | None,
        remote_error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "viewer_started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "viewer_uptime_seconds": self.uptime_seconds,
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "refresh_seconds": self.settings.monitoring_viewer_refresh_seconds,
            "remote_alias": self.settings.monitoring_remote_ssh_alias,
            "remote_path": self.settings.monitoring_remote_path,
            "latency_ms": latency_ms,
            "remote_command": remote_command,
            "remote_error": remote_error,
        }


def build_remote_monitoring_command(settings: DoxAgentSettings, args: list[str]) -> str:
    quoted_path = shlex.quote(settings.monitoring_remote_path)
    quoted_args = " ".join(shlex.quote(part) for part in args)
    return (
        f"cd {quoted_path} && "
        "if docker compose ps --services --status running 2>/dev/null | grep -qx debug-viewer; then "
        f"docker compose exec -T debug-viewer python -m doxagent.monitoring.cli {quoted_args}; "
        "elif command -v uv >/dev/null 2>&1; then "
        f"uv run python -m doxagent.monitoring.cli {quoted_args}; "
        "else "
        f"python -m doxagent.monitoring.cli {quoted_args}; "
        "fi"
    )


def run_server(*, host: str = "127.0.0.1", port: int = 8766) -> None:
    runtime = MonitoringViewerRuntime()

    class MonitoringViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            _handle_get(self, runtime)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            _handle_post(self, runtime)

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            _write_preflight(self)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[monitoring-viewer] {self.address_string()} - {format % args}")

    server = ThreadingHTTPServer((host, port), MonitoringViewerHandler)
    print(f"DoxAgent monitoring viewer listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDoxAgent monitoring viewer stopped.")
    finally:
        server.server_close()


def _handle_get(handler: BaseHTTPRequestHandler, runtime: MonitoringViewerRuntime) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)
    try:
        if path in {"/", "/index.html", "/monitoring.html"}:
            _write_html(handler, INDEX_HTML)
            return
        if path == "/api/monitoring/local":
            _write_json(
                handler,
                runtime.local_status(
                    ticker=_first(query.get("ticker")),
                    limit=_safe_int(_first(query.get("limit")), default=100),
                ),
            )
            return
        if path == "/api/monitoring/remote":
            _write_json(
                handler,
                runtime.remote_status(
                    ticker=_first(query.get("ticker")),
                    limit=_safe_int(_first(query.get("limit")), default=100),
                ),
            )
            return
        _write_json(handler, {"error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)
    except Exception as exc:
        _write_json(
            handler,
            {"error": "monitoring_viewer_error", "message": _safe_error_message(exc)},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


def _handle_post(handler: BaseHTTPRequestHandler, runtime: MonitoringViewerRuntime) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)
    try:
        if path == "/api/monitoring/bind":
            payload = _read_json_body(handler)
            scope = _first(query.get("scope")) or str(payload.get("scope") or "remote")
            if scope not in {"local", "remote"}:
                raise ValueError("scope must be local or remote.")
            _write_json(handler, runtime.bind(payload, scope=scope))
            return
        if path == "/api/monitoring/poll-due":
            scope = _first(query.get("scope")) or "remote"
            if scope not in {"local", "remote"}:
                raise ValueError("scope must be local or remote.")
            _write_json(handler, runtime.poll_due(scope=scope))
            return
        if path == "/api/monitoring/unbind":
            payload = _read_json_body(handler)
            scope = _first(query.get("scope")) or str(payload.get("scope") or "remote")
            if scope not in {"local", "remote"}:
                raise ValueError("scope must be local or remote.")
            _write_json(handler, runtime.unbind(payload, scope=scope))
            return
        if path == "/api/monitoring/delete-ticker":
            payload = _read_json_body(handler)
            scope = _first(query.get("scope")) or str(payload.get("scope") or "remote")
            if scope not in {"local", "remote"}:
                raise ValueError("scope must be local or remote.")
            _write_json(handler, runtime.delete_ticker(payload, scope=scope))
            return
        _write_json(handler, {"error": "not_found", "path": path}, status=HTTPStatus.NOT_FOUND)
    except Exception as exc:
        _write_json(
            handler,
            {"error": "monitoring_viewer_error", "message": _safe_error_message(exc)},
            status=HTTPStatus.BAD_REQUEST,
        )


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length") or "0"
    length = int(raw_length)
    if length <= 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8")
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise ValueError("JSON body must be an object.")
    return decoded


def _write_html(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    try:
        handler.send_response(HTTPStatus.OK)
        _write_common_headers(handler, "text/html; charset=utf-8", len(body))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_json(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    *,
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    try:
        handler.send_response(status)
        _write_common_headers(handler, "application/json; charset=utf-8", len(body))
        handler.end_headers()
        handler.wfile.write(body)
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_preflight(handler: BaseHTTPRequestHandler) -> None:
    try:
        handler.send_response(HTTPStatus.NO_CONTENT)
        _write_common_headers(handler, "text/plain; charset=utf-8", 0)
        handler.end_headers()
    except CLIENT_DISCONNECT_ERRORS:
        return


def _write_common_headers(
    handler: BaseHTTPRequestHandler,
    content_type: str,
    content_length: int,
) -> None:
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(content_length))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Private-Network", "true")


def _first(value: list[str] | None) -> str | None:
    if not value:
        return None
    item = value[0].strip()
    return item or None


def _safe_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        candidates = value
    else:
        candidates = str(value).replace("\n", ",").split(",")
    return [str(item).strip() for item in candidates if str(item).strip()]


def _required_text(data: dict[str, Any], key: str) -> str:
    value = _optional_text(data.get(key))
    if not value:
        raise ValueError(f"{key} is required.")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value))


def _decode_subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return value.strip()


def _empty_snapshot() -> dict[str, Any]:
    return {
        "sources": [],
        "bindings": [],
        "poll_states": [],
        "recent_raw_messages": [],
        "recent_standard_messages": [],
        "recent_events": [],
    }


def _remote_error_payload(result: RemoteCommandResult) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "remote_monitoring_command_failed",
        "message": result.stderr or result.stdout or "Remote command failed.",
        "meta": {
            "latency_ms": result.latency_ms,
            "remote_command": result.command,
        },
    }


def _safe_error_message(exc: Exception) -> str:
    message = str(exc)
    if "postgresql://" in message:
        return "Database connection failed; check DOXAGENT_DATABASE_URL."
    return message[:800]


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DoxAgent Monitoring</title>
  <style>
    :root {
      color-scheme: light;
      --background: #f8fafc;
      --foreground: #111827;
      --card: #ffffff;
      --card-foreground: #111827;
      --muted: #f1f5f9;
      --muted-foreground: #64748b;
      --border: #d9e2ec;
      --input: #d9e2ec;
      --primary: #0f766e;
      --primary-foreground: #ffffff;
      --secondary: #eef2ff;
      --secondary-foreground: #3730a3;
      --accent: #fef3c7;
      --accent-foreground: #92400e;
      --destructive: #b91c1c;
      --destructive-foreground: #ffffff;
      --ring: #0f766e;
      --ok: #15803d;
      --warn: #b45309;
      --bad: #b91c1c;
      --violet: #6d28d9;
      --blue: #0369a1;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-width: 320px;
      background: var(--background);
      color: var(--foreground);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    button, input, select, textarea {
      font: inherit;
      letter-spacing: 0;
    }
    button {
      border: 1px solid var(--border);
      border-radius: 6px;
      min-height: 36px;
      padding: 0 12px;
      background: var(--card);
      color: var(--foreground);
      cursor: pointer;
    }
    button.primary {
      background: var(--primary);
      border-color: var(--primary);
      color: var(--primary-foreground);
      font-weight: 650;
    }
    button.ghost {
      background: transparent;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.62;
    }
    input, select, textarea {
      width: 100%;
      min-height: 36px;
      border: 1px solid var(--input);
      border-radius: 6px;
      background: var(--card);
      color: var(--foreground);
      padding: 8px 10px;
    }
    textarea {
      min-height: 76px;
      resize: vertical;
    }
    label {
      display: flex;
      flex-direction: column;
      gap: 6px;
      color: var(--muted-foreground);
      font-size: 12px;
      font-weight: 650;
    }
    .app {
      display: grid;
      grid-template-columns: 244px minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid var(--border);
      background: #ffffff;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      position: sticky;
      top: 0;
      height: 100vh;
    }
    .brand {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }
    .brand-title {
      font-size: 19px;
      font-weight: 760;
    }
    .brand-subtitle {
      color: var(--muted-foreground);
      font-size: 12px;
    }
    .nav {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .nav button {
      justify-content: flex-start;
      text-align: left;
      display: flex;
      align-items: center;
      gap: 9px;
      width: 100%;
      border-color: transparent;
      background: transparent;
    }
    .nav button.active {
      background: var(--muted);
      border-color: var(--border);
    }
    .dot {
      display: inline-block;
      flex: 0 0 auto;
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--muted-foreground);
    }
    .dot.ok { background: var(--ok); }
    .dot.warn { background: var(--warn); }
    .dot.bad { background: var(--bad); }
    .main {
      min-width: 0;
      padding: 18px 20px 26px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      min-height: 44px;
    }
    .topbar h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: center;
      gap: 8px;
    }
    .scope {
      display: flex;
      align-items: center;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 3px;
      background: var(--card);
    }
    .scope button {
      border: 0;
      min-height: 30px;
      border-radius: 5px;
      background: transparent;
    }
    .scope button.active {
      background: var(--primary);
      color: var(--primary-foreground);
    }
    .grid {
      display: grid;
      gap: 12px;
    }
    .kpi-grid {
      grid-template-columns: repeat(4, minmax(160px, 1fr));
    }
    .stream-grid {
      grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.8fr);
      align-items: start;
    }
    .config-grid {
      grid-template-columns: minmax(320px, 0.85fr) minmax(0, 1.15fr);
      align-items: start;
    }
    .card {
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--card);
      color: var(--card-foreground);
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
      overflow: hidden;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
      padding: 14px 14px 10px;
    }
    .card-title {
      margin: 0;
      font-size: 14px;
      font-weight: 720;
    }
    .card-description {
      margin: 3px 0 0;
      color: var(--muted-foreground);
      font-size: 12px;
    }
    .card-content {
      padding: 0 14px 14px;
    }
    .metric {
      font-size: 26px;
      font-weight: 760;
      line-height: 1.12;
    }
    .metric-sub {
      margin-top: 4px;
      color: var(--muted-foreground);
      font-size: 12px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 650;
      color: var(--muted-foreground);
      background: var(--muted);
      white-space: nowrap;
    }
    .badge.ok {
      border-color: #bbf7d0;
      background: #f0fdf4;
      color: #166534;
    }
    .badge.warn {
      border-color: #fde68a;
      background: #fffbeb;
      color: #92400e;
    }
    .badge.bad {
      border-color: #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .badge.media {
      border-color: #bae6fd;
      background: #f0f9ff;
      color: #075985;
    }
    .badge.social {
      border-color: #ddd6fe;
      background: #f5f3ff;
      color: #5b21b6;
    }
    .feed {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: calc(100vh - 285px);
      min-height: 420px;
      overflow: auto;
      padding-right: 4px;
    }
    .feed-item {
      display: grid;
      grid-template-columns: 82px minmax(0, 1fr);
      gap: 11px;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    .feed-time {
      color: var(--muted-foreground);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .feed-title {
      font-weight: 720;
      line-height: 1.25;
      margin-bottom: 5px;
      overflow-wrap: anywhere;
    }
    .feed-body {
      color: var(--muted-foreground);
      font-size: 13px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .feed-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .stack {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .health-row, .failure-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
    }
    .failure-row {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .poll-ring {
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: conic-gradient(var(--ring) var(--progress, 0deg), var(--muted) 0);
      display: grid;
      place-items: center;
      border: 1px solid var(--border);
      flex: 0 0 auto;
    }
    .poll-ring::after {
      content: "";
      width: 22px;
      height: 22px;
      border-radius: inherit;
      background: var(--card);
    }
    .poll-ring.bad {
      --ring: var(--bad);
    }
    .poll-ring.warn {
      --ring: var(--warn);
    }
    .health-stats {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .new-count {
      color: var(--foreground);
      font-weight: 750;
      font-variant-numeric: tabular-nums;
    }
    .row-title {
      font-weight: 700;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .row-sub {
      color: var(--muted-foreground);
      font-size: 12px;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .form-wide {
      grid-column: 1 / -1;
    }
    .field-hidden {
      display: none;
    }
    .check-row {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--foreground);
      font-size: 13px;
      font-weight: 650;
    }
    .check-row input {
      width: 16px;
      min-height: 16px;
    }
    .task-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    details.task {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }
    details.task summary {
      cursor: pointer;
      padding: 12px;
      list-style: none;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
    }
    details.task summary::-webkit-details-marker {
      display: none;
    }
    .task-body {
      border-top: 1px solid var(--border);
      padding: 12px;
      background: #fcfdff;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .task-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .task-source-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .task-source {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
    }
    .kv {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 9px;
      background: #fff;
      min-width: 0;
    }
    .kv-name {
      color: var(--muted-foreground);
      font-size: 12px;
      font-weight: 650;
    }
    .kv-value {
      margin-top: 3px;
      font-weight: 680;
      overflow-wrap: anywhere;
    }
    .kv-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    button.danger {
      border-color: #fecaca;
      color: #991b1b;
      background: #fef2f2;
    }
    .stream-controls {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }
    .stream-controls select {
      width: min(240px, 100%);
    }
    .hidden {
      display: none !important;
    }
    .empty {
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 22px;
      text-align: center;
      color: var(--muted-foreground);
      background: #fff;
    }
    .alert {
      border: 1px solid #fed7aa;
      border-radius: 8px;
      background: #fff7ed;
      color: #9a3412;
      padding: 10px 12px;
      overflow-wrap: anywhere;
    }
    .footer-meta {
      color: var(--muted-foreground);
      font-size: 12px;
    }
    @media (max-width: 1120px) {
      .app {
        grid-template-columns: 1fr;
      }
      .sidebar {
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .nav {
        flex-direction: row;
        flex-wrap: wrap;
      }
      .nav button {
        width: auto;
      }
      .stream-grid, .config-grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 760px) {
      .main {
        padding: 14px;
      }
      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }
      .toolbar {
        justify-content: flex-start;
      }
      .kpi-grid, .form-grid, .task-body {
        grid-template-columns: 1fr;
      }
      .feed-item {
        grid-template-columns: 1fr;
      }
      .health-row, .task-source {
        grid-template-columns: 1fr;
      }
      .health-stats {
        justify-content: flex-start;
        flex-wrap: wrap;
      }
      .kv-grid {
        grid-template-columns: 1fr;
      }
      .feed {
        max-height: none;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">DoxAgent Monitoring</div>
        <div class="brand-subtitle">Message Bus Control Plane</div>
      </div>
      <nav class="nav" aria-label="Monitoring views">
        <button id="tab-stream" class="active" type="button"><span class="dot ok"></span>Live Message Stream</button>
        <button id="tab-config" type="button"><span class="dot"></span>Monitoring Tasks</button>
      </nav>
      <div class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Remote</h2>
            <p class="card-description" id="remote-label">doxagent-hk</p>
          </div>
          <span id="remote-badge" class="badge warn">checking</span>
        </div>
        <div class="card-content footer-meta" id="remote-meta">Waiting for first refresh.</div>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div>
          <h1 id="page-title">Live Message Stream</h1>
          <div class="footer-meta" id="checked-at">No data loaded.</div>
        </div>
        <div class="toolbar">
          <div class="scope" role="group" aria-label="Data scope">
            <button id="scope-remote" class="active" type="button">Remote</button>
            <button id="scope-local" type="button">Local</button>
          </div>
          <input id="ticker-filter" aria-label="Ticker filter" placeholder="Ticker" style="width: 96px;" />
          <button id="refresh-button" class="primary" type="button">Refresh</button>
        </div>
      </div>
      <div id="alert-slot"></div>

      <section id="stream-view" class="stack">
        <div class="grid kpi-grid" id="kpi-grid"></div>
        <div class="grid stream-grid">
          <div class="card">
            <div class="card-header">
              <div>
                <h2 class="card-title">Live Message Stream</h2>
                <p class="card-description">Newest persisted events and normalized messages.</p>
              </div>
              <div class="stream-controls">
                <select id="source-filter" aria-label="Filter messages by source"></select>
                <span id="stream-count" class="badge">0 items</span>
              </div>
            </div>
            <div class="card-content">
              <div id="feed" class="feed"></div>
            </div>
          </div>
          <div class="stack">
            <div class="card">
              <div class="card-header">
                <div>
                  <h2 class="card-title">Source Health</h2>
                  <p class="card-description">Poll state, latency, and failures by source.</p>
                </div>
              </div>
              <div class="card-content stack" id="health-list"></div>
            </div>
            <div class="card">
              <div class="card-header">
                <div>
                  <h2 class="card-title">Recent Failures</h2>
                  <p class="card-description">Latest failed sources and reasons.</p>
                </div>
              </div>
              <div class="card-content stack" id="failure-list"></div>
            </div>
          </div>
        </div>
      </section>

      <section id="config-view" class="grid config-grid hidden">
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Configure Monitoring</h2>
              <p class="card-description">User-side ticker binding and polling controls.</p>
            </div>
          </div>
          <div class="card-content">
            <form id="config-form" class="form-grid">
              <label>Ticker<input name="ticker" value="AAPL" required /></label>
              <label>Source<select name="source_id" id="source-select"></select></label>
              <label data-field="keywords">Keywords<textarea name="keywords" placeholder="MU earnings, HBM supply"></textarea></label>
              <label data-field="usernames">Usernames<textarea name="usernames" placeholder="username, another_user"></textarea></label>
              <label data-field="search_terms">Search Terms<textarea name="search_terms" placeholder="MU AI memory event"></textarea></label>
              <label data-field="rss_urls">RSS URLs<textarea name="rss_urls" placeholder="https://example.com/rss.xml"></textarea></label>
              <label class="form-wide" data-field="source_filters">Source Filters<textarea name="source_filters" placeholder="Newswire, SEC, product launch"></textarea></label>
              <label>Poll Interval Seconds<input name="poll_interval_seconds" type="number" min="30" placeholder="User-only" /></label>
              <label>Reason<input name="reason" placeholder="Coverage adjustment" /></label>
              <label class="check-row"><input name="enabled" type="checkbox" checked />Enabled</label>
              <label class="check-row"><input name="replace" type="checkbox" />Replace existing parameters</label>
              <div class="form-wide" style="display:flex; gap:8px; flex-wrap:wrap;">
                <button class="primary" type="submit">Save Configuration</button>
                <button id="poll-due-button" type="button">Poll Due</button>
              </div>
            </form>
          </div>
        </div>
        <div class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Monitoring Tasks</h2>
              <p class="card-description">Ticker monitoring tasks with expanded source bindings.</p>
            </div>
            <span id="task-count" class="badge">0 tasks</span>
          </div>
          <div class="card-content task-list" id="task-list"></div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      tab: "stream",
      scope: "remote",
      data: null,
      timer: null,
      loading: false
    };

    const $ = (id) => document.getElementById(id);
    const SOURCE_FIELDS = {
      benzinga_news: [],
      finnhub_company_news: [],
      stocktwits_messages: [],
      tikhub_x_search: ["keywords", "search_terms", "source_filters"],
      tikhub_x_user_posts: ["usernames"],
      newswire_rss: ["rss_urls"]
    };
    const FIELD_PLACEHOLDERS = {
      benzinga_news: {},
      finnhub_company_news: {},
      stocktwits_messages: {},
      tikhub_x_search: {
        keywords: "MU earnings, HBM, AI memory",
        search_terms: "MU HBM supply OR Micron earnings",
        source_filters: "Official, semiconductor analysts, product launch"
      },
      tikhub_x_user_posts: {
        usernames: "microntech, another_user"
      },
      newswire_rss: {
        rss_urls: "https://example.com/rss.xml"
      }
    };

    function setTab(tab) {
      state.tab = tab;
      $("tab-stream").classList.toggle("active", tab === "stream");
      $("tab-config").classList.toggle("active", tab === "config");
      $("stream-view").classList.toggle("hidden", tab !== "stream");
      $("config-view").classList.toggle("hidden", tab !== "config");
      $("page-title").textContent = tab === "stream" ? "Live Message Stream" : "Monitoring Tasks";
    }

    function setScope(scope) {
      state.scope = scope;
      $("scope-remote").classList.toggle("active", scope === "remote");
      $("scope-local").classList.toggle("active", scope === "local");
      refresh();
    }

    async function refresh() {
      if (state.loading) return;
      state.loading = true;
      $("refresh-button").disabled = true;
      const ticker = $("ticker-filter").value.trim();
      const query = new URLSearchParams({ limit: "100" });
      if (ticker) query.set("ticker", ticker.toUpperCase());
      try {
        const response = await fetch(`/api/monitoring/${state.scope}?${query.toString()}`, { cache: "no-store" });
        const data = await response.json();
        state.data = data;
        render(data);
      } catch (error) {
        renderError(String(error));
      } finally {
        state.loading = false;
        $("refresh-button").disabled = false;
      }
    }

    function render(data) {
      const meta = data.meta || {};
      $("remote-label").textContent = `${meta.remote_alias || "doxagent-hk"} / ${state.scope}`;
      $("remote-badge").textContent = data.ok ? "connected" : "attention";
      $("remote-badge").className = data.ok ? "badge ok" : "badge bad";
      $("remote-meta").textContent = `${formatDuration(meta.viewer_uptime_seconds || 0)} uptime, ${meta.latency_ms || 0} ms check`;
      $("checked-at").textContent = meta.checked_at ? `Last checked ${formatTime(meta.checked_at)} from ${state.scope}` : "No data loaded.";
      if (!data.ok && meta.remote_error) {
        $("alert-slot").innerHTML = `<div class="alert">${escapeHtml(meta.remote_error)}</div>`;
      } else {
        $("alert-slot").innerHTML = "";
      }
      renderSourceOptions(data.sources || []);
      renderSourceFilterOptions(data.sources || []);
      updateSourceFields();
      renderKpis(data);
      renderFeed(data);
      renderHealth(data);
      renderFailures(data);
      renderTasks(data);
    }

    function renderError(message) {
      $("alert-slot").innerHTML = `<div class="alert">${escapeHtml(message)}</div>`;
      $("remote-badge").textContent = "error";
      $("remote-badge").className = "badge bad";
    }

    function renderKpis(data) {
      const sources = data.sources || [];
      const states = data.poll_states || [];
      const messages = data.recent_standard_messages || [];
      const events = data.recent_events || [];
      const failures = states.filter((item) => item.status === "failed" || item.last_error_message);
      const sourceCounts = countBy(messages, "source_id");
      const sourceSummary = Object.entries(sourceCounts).slice(0, 3).map(([key, value]) => `${labelSource(key)} ${value}`).join(" | ") || "No messages yet";
      const latency = states.length ? Math.round(avg(states.map((item) => Number(item.last_latency_ms)).filter((item) => Number.isFinite(item)))) : 0;
      const healthy = states.filter((item) => item.status === "succeeded").length;
      const cards = [
        ["Runtime", formatDuration((data.meta || {}).viewer_uptime_seconds || 0), "Local viewer session"],
        ["By Source Messages", String(messages.length), sourceSummary],
        ["Errors / Failed", String(failures.length), failures.length ? "Needs attention" : "No recent failures"],
        ["Health / Latency", `${healthy}/${sources.length}`, `${formatMs(latency)} average delay`],
      ];
      $("kpi-grid").innerHTML = cards.map(([title, value, sub]) => `
        <article class="card">
          <div class="card-header"><h2 class="card-title">${escapeHtml(title)}</h2></div>
          <div class="card-content">
            <div class="metric">${escapeHtml(value)}</div>
            <div class="metric-sub">${escapeHtml(sub)}</div>
          </div>
        </article>
      `).join("");
    }

    function renderFeed(data) {
      const selectedSource = $("source-filter").value || "";
      const messages = [...(data.recent_standard_messages || [])]
        .filter((item) => !selectedSource || item.source_id === selectedSource)
        .sort((a, b) => Date.parse(b.normalized_at || b.collected_at || 0) - Date.parse(a.normalized_at || a.collected_at || 0));
      $("stream-count").textContent = `${messages.length} items`;
      if (!messages.length) {
        $("feed").innerHTML = `<div class="empty">No normalized monitoring messages yet.</div>`;
        return;
      }
      $("feed").innerHTML = messages.map((item) => `
        <article class="feed-item">
          <div class="feed-time">${escapeHtml(formatTime(item.published_at || item.collected_at || item.normalized_at))}</div>
          <div>
            <div class="feed-title">${escapeHtml(item.title || item.body || "Untitled message")}</div>
            <div class="feed-body">${escapeHtml(item.body || "")}</div>
            <div class="feed-meta">
              <span class="badge">${escapeHtml(item.ticker || "N/A")}</span>
              <span class="badge ${item.source_type === "social" ? "social" : "media"}">${escapeHtml(item.source_type || "source")}</span>
              <span class="badge">${escapeHtml(labelSource(item.source_id))}</span>
              ${item.author ? `<span class="badge">${escapeHtml(item.author)}</span>` : ""}
            </div>
          </div>
        </article>
      `).join("");
    }

    function renderHealth(data) {
      const states = data.poll_states || [];
      const sources = Object.fromEntries((data.sources || []).map((item) => [item.source_id, item]));
      if (!states.length) {
        $("health-list").innerHTML = `<div class="empty">No poll state recorded.</div>`;
        return;
      }
      $("health-list").innerHTML = states.map((item) => {
        const status = item.status === "failed" ? "bad" : item.status === "succeeded" ? "ok" : "warn";
        const source = sources[item.source_id] || {};
        const progress = pollProgress(item, source);
        const newCount = Number(item.last_event_count || 0);
        const statusText = item.status === "failed" ? "failed" : formatHourMinute(item.last_success_at);
        return `
          <div class="health-row">
            <div class="poll-ring ${status}" style="--progress:${Math.round(progress * 360)}deg" title="${escapeHtml(nextPollLabel(item, source))}"></div>
            <div>
              <div class="row-title"><span class="dot ${status}"></span> ${escapeHtml(source.display_name || labelSource(item.source_id))}</div>
              <div class="row-sub">${escapeHtml(item.ticker)} | ${escapeHtml(source.interface_type || "unknown")} | ${latencyLabel(item)} | delay ${escapeHtml(formatMs(item.last_latency_ms))}</div>
            </div>
            <div class="health-stats">
              <span class="badge">+<span class="new-count">${escapeHtml(String(newCount))}</span></span>
              <span class="badge ${status}">${escapeHtml(statusText)}</span>
            </div>
          </div>
        `;
      }).join("");
    }

    function renderFailures(data) {
      const failures = (data.poll_states || []).filter((item) => item.last_error_message || item.status === "failed");
      if (!failures.length) {
        $("failure-list").innerHTML = `<div class="empty">No recent failures recorded.</div>`;
        return;
      }
      $("failure-list").innerHTML = failures.map((item) => `
        <div class="failure-row">
          <div>
            <div class="row-title">${escapeHtml(labelSource(item.source_id))} / ${escapeHtml(item.ticker)}</div>
            <div class="row-sub">${escapeHtml(item.last_error_message || "Failed without message")}</div>
          </div>
          <span class="badge bad">${escapeHtml(formatTime(item.last_error_at || item.updated_at))}</span>
        </div>
      `).join("");
    }

    function renderTasks(data) {
      const sources = Object.fromEntries((data.sources || []).map((item) => [item.source_id, item]));
      const states = Object.fromEntries((data.poll_states || []).map((item) => [item.binding_id, item]));
      const groups = new Map();
      for (const binding of (data.bindings || [])) {
        if (!binding.enabled) continue;
        if (!groups.has(binding.ticker)) groups.set(binding.ticker, []);
        groups.get(binding.ticker).push(binding);
      }
      const tasks = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
      $("task-count").textContent = `${tasks.length} tasks`;
      if (!tasks.length) {
        $("task-list").innerHTML = `<div class="empty">No enabled ticker monitoring tasks.</div>`;
        return;
      }
      $("task-list").innerHTML = tasks.map(([ticker, bindings]) => {
        const sortedBindings = bindings.sort((a, b) => a.source_id.localeCompare(b.source_id));
        const byTicker = sortedBindings.filter((binding) => (sources[binding.source_id] || {}).interface_type === "by_ticker").length;
        const byParameter = sortedBindings.length - byTicker;
        const latestState = sortedBindings
          .map((binding) => states[binding.binding_id])
          .filter(Boolean)
          .sort((a, b) => Date.parse(b.updated_at || 0) - Date.parse(a.updated_at || 0))[0] || {};
        const failed = sortedBindings.some((binding) => (states[binding.binding_id] || {}).status === "failed");
        return `
          <details class="task">
            <summary>
              <div>
                <div class="row-title">${escapeHtml(ticker)}</div>
                <div class="row-sub">${sortedBindings.length} sources | ${byTicker} by ticker | ${byParameter} by parameter | ${latencyLabel(latestState)}</div>
              </div>
              <span class="badge ${failed ? "bad" : "ok"}">${failed ? "attention" : "active"}</span>
            </summary>
            <div class="task-body">
              <div class="task-actions">
                <span class="badge">${escapeHtml(ticker)} monitoring task</span>
                <button type="button" class="danger" data-action="delete-ticker" data-ticker="${escapeHtml(ticker)}">Delete Task</button>
              </div>
              <div class="task-source-list">
                ${sortedBindings.map((binding) => renderTaskSource(binding, sources[binding.source_id] || {}, states[binding.binding_id] || {})).join("")}
              </div>
            </div>
          </details>
        `;
      }).join("");
    }

    function renderTaskSource(binding, source, stateItem) {
      const params = binding.parameters || {};
      const sourceStatus = stateItem.status === "failed" ? "bad" : stateItem.status === "succeeded" ? "ok" : "warn";
      return `
        <div class="task-source">
          <div>
            <div class="row-title">${escapeHtml(source.display_name || labelSource(binding.source_id))}</div>
            <div class="row-sub">${escapeHtml(source.source_type || "source")} | ${escapeHtml(source.interface_type || "unknown")} | every ${escapeHtml(String(source.poll_interval_seconds || "-"))}s | ${latencyLabel(stateItem)} | delay ${escapeHtml(formatMs(stateItem.last_latency_ms))}</div>
            <div class="kv-grid">
              ${kv("Keywords", (params.keywords || []).join(", ") || "-")}
              ${kv("Search Terms", (params.search_terms || []).join(", ") || "-")}
              ${kv("Usernames", (params.usernames || []).join(", ") || "-")}
              ${kv("RSS URLs", (params.rss_urls || []).join(", ") || "-")}
              ${kv("Inserted", stateItem.raw_inserted_count ?? 0)}
              ${kv("Duplicates", stateItem.duplicate_count ?? 0)}
            </div>
          </div>
          <div class="health-stats">
            <span class="badge ${sourceStatus}">${escapeHtml(stateItem.status || "configured")}</span>
            <button type="button" class="danger" data-action="unbind" data-ticker="${escapeHtml(binding.ticker)}" data-source="${escapeHtml(binding.source_id)}">Remove</button>
          </div>
        </div>
      `;
    }

    function renderSourceOptions(sources) {
      const current = $("source-select").value;
      $("source-select").innerHTML = sources.map((source) => `<option value="${escapeHtml(source.source_id)}">${escapeHtml(source.display_name || source.source_id)}</option>`).join("");
      if (current) $("source-select").value = current;
    }

    function renderSourceFilterOptions(sources) {
      const current = $("source-filter").value;
      const options = [`<option value="">All sources</option>`].concat(
        sources.map((source) => `<option value="${escapeHtml(source.source_id)}">${escapeHtml(source.display_name || source.source_id)}</option>`)
      );
      $("source-filter").innerHTML = options.join("");
      if (current && sources.some((source) => source.source_id === current)) {
        $("source-filter").value = current;
      }
    }

    function updateSourceFields() {
      const sourceId = $("source-select").value;
      const fields = new Set(SOURCE_FIELDS[sourceId] || []);
      const placeholders = FIELD_PLACEHOLDERS[sourceId] || {};
      document.querySelectorAll("[data-field]").forEach((label) => {
        const field = label.getAttribute("data-field");
        const enabled = fields.has(field);
        label.classList.toggle("field-hidden", !enabled);
        const control = label.querySelector("textarea, input, select");
        if (!control) return;
        control.disabled = !enabled;
        if (!enabled) control.value = "";
        if (placeholders[field]) control.placeholder = placeholders[field];
      });
    }

    async function submitConfig(event) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const payload = Object.fromEntries(form.entries());
      payload.enabled = form.get("enabled") === "on";
      payload.replace = form.get("replace") === "on";
      try {
        const response = await fetch(`/api/monitoring/bind?scope=${state.scope}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Save failed");
        await refresh();
      } catch (error) {
        renderError(String(error));
      }
    }

    async function handleTaskAction(event) {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      const ticker = button.dataset.ticker;
      if (!ticker) return;
      const endpoint = action === "delete-ticker" ? "delete-ticker" : "unbind";
      const payload = { ticker };
      if (action === "unbind") payload.source_id = button.dataset.source;
      const label = action === "delete-ticker"
        ? `Delete all monitoring bindings for ${ticker}?`
        : `Remove ${button.dataset.source} from ${ticker}?`;
      if (!window.confirm(label)) return;
      button.disabled = true;
      try {
        const response = await fetch(`/api/monitoring/${endpoint}?scope=${state.scope}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Delete failed");
        await refresh();
      } catch (error) {
        renderError(String(error));
      } finally {
        button.disabled = false;
      }
    }

    function kv(name, value) {
      return `<div class="kv"><div class="kv-name">${escapeHtml(name)}</div><div class="kv-value">${escapeHtml(String(value ?? "-"))}</div></div>`;
    }

    function countBy(items, key) {
      return items.reduce((acc, item) => {
        const value = item[key] || "unknown";
        acc[value] = (acc[value] || 0) + 1;
        return acc;
      }, {});
    }

    function avg(values) {
      if (!values.length) return 0;
      return values.reduce((sum, value) => sum + value, 0) / values.length;
    }

    function latencySeconds(item) {
      const stamp = item.last_success_at || item.last_attempt_at || item.updated_at;
      if (!stamp) return null;
      return Math.max(0, Math.round((Date.now() - Date.parse(stamp)) / 1000));
    }

    function latencyLabel(item) {
      const seconds = latencySeconds(item);
      return seconds === null ? "no poll yet" : `${formatDuration(seconds)} ago`;
    }

    function pollProgress(item, source) {
      const interval = Number(source.poll_interval_seconds || 0);
      const stamp = item.last_attempt_at || item.last_success_at;
      if (!interval || !stamp) return 0;
      const elapsed = Math.max(0, (Date.now() - Date.parse(stamp)) / 1000);
      return Math.max(0, Math.min(1, elapsed / interval));
    }

    function nextPollLabel(item, source) {
      const interval = Number(source.poll_interval_seconds || 0);
      const stamp = item.last_attempt_at || item.last_success_at;
      if (!interval || !stamp) return "No poll scheduled";
      const elapsed = Math.max(0, Math.round((Date.now() - Date.parse(stamp)) / 1000));
      const remaining = Math.max(0, interval - elapsed);
      return remaining ? `Next poll in ${formatDuration(remaining)}` : "Due now";
    }

    function formatMs(value) {
      const number = Number(value);
      if (!Number.isFinite(number) || number < 0) return "-";
      if (number >= 1000) return `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}s`;
      return `${Math.round(number)}ms`;
    }

    function labelSource(sourceId) {
      const labels = {
        benzinga_news: "Benzinga",
        finnhub_company_news: "Finnhub",
        stocktwits_messages: "Stocktwits",
        tikhub_x_search: "TikHub Search",
        tikhub_x_user_posts: "TikHub Users",
        newswire_rss: "Newswire RSS"
      };
      return labels[sourceId] || sourceId || "unknown";
    }

    function formatTime(value) {
      if (!value) return "-";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }

    function formatHourMinute(value) {
      if (!value) return "-";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function formatDuration(totalSeconds) {
      const seconds = Math.max(0, Number(totalSeconds) || 0);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const rest = seconds % 60;
      if (hours) return `${hours}h ${minutes}m`;
      if (minutes) return `${minutes}m ${rest}s`;
      return `${rest}s`;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    $("tab-stream").addEventListener("click", () => setTab("stream"));
    $("tab-config").addEventListener("click", () => setTab("config"));
    $("scope-remote").addEventListener("click", () => setScope("remote"));
    $("scope-local").addEventListener("click", () => setScope("local"));
    $("refresh-button").addEventListener("click", refresh);
    $("config-form").addEventListener("submit", submitConfig);
    $("source-select").addEventListener("change", updateSourceFields);
    $("source-filter").addEventListener("change", () => {
      if (state.data) renderFeed(state.data);
    });
    $("task-list").addEventListener("click", handleTaskAction);
    $("poll-due-button").addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/monitoring/poll-due?scope=${state.scope}`, { method: "POST" });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Poll failed");
        await refresh();
      } catch (error) {
        renderError(String(error));
      }
    });
    $("ticker-filter").addEventListener("keydown", (event) => {
      if (event.key === "Enter") refresh();
    });

    refresh();
    state.timer = window.setInterval(refresh, 5000);
  </script>
</body>
</html>
"""
