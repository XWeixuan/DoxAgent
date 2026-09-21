#!/usr/bin/python3
"""Restricted root bridge for the xRDP Site Login maintenance GUI.

The desktop user can only invoke the finite operations defined by this module.  All
Site Access credentials and maintenance tokens stay in root-owned files or inside
the Site Access container.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - deployment target is Linux
    fcntl = None  # type: ignore[assignment]

STATE_DIR = Path("/run/doxagent-site-login")
SESSION_FILE = STATE_DIR / "session.json"
LOCK_FILE = STATE_DIR / "session.lock"
DOCKER = "/usr/bin/docker"
CONTAINER_LABELS = (
    "label=com.docker.compose.project=doxagent-v2",
    "label=com.docker.compose.service=v2-site-access",
)
PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z")
DESKTOP_USER = "doxagent-desktop"
DISPLAY_NAMES = {
    "barrons": "Barron's",
    "wsj": "WSJ",
    "seeking_alpha": "Seeking Alpha",
    "marketwatch": "MarketWatch",
}

# Executed inside the trusted Site Access container. The request comes on stdin and
# the token is read there; neither is interpolated into a shell command.
CONTAINER_REQUEST = r"""
import json, pathlib, sys, urllib.error, urllib.request
request = json.load(sys.stdin)
token = pathlib.Path('/run/secrets/site_access_admin_token').read_text().strip()
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
data = None if request.get('payload') is None else json.dumps(request['payload']).encode()
req = urllib.request.Request(
    'http://127.0.0.1:8011' + request['path'], data=data,
    headers=headers, method=request['method'])
try:
    with urllib.request.urlopen(req, timeout=request.get('timeout', 45)) as response:
        body = response.read().decode()
        print(json.dumps({'status': response.status, 'body': json.loads(body) if body else None}))
except urllib.error.HTTPError as exc:
    body = exc.read().decode()
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    print(json.dumps({'status': exc.code, 'body': parsed}))
"""


class AdminError(RuntimeError):
    def __init__(self, code: str, message: str, suggestion: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion


def _emit(payload: dict[str, Any], *, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(exit_code)


def _fail(error: AdminError) -> None:
    _emit(
        {
            "ok": False,
            "error": error.code,
            "message": error.message,
            "suggestion": error.suggestion,
        },
        exit_code=2,
    )


def _ensure_root() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise AdminError(
            "ROOT_REQUIRED",
            "The maintenance tool does not have administrator access.",
            "Start it from the desktop shortcut.",
        )
    caller = os.environ.get("SUDO_USER")
    if caller not in {None, "root", DESKTOP_USER}:
        raise AdminError("CALLER_DENIED", "This user cannot run site login maintenance.")


@contextlib.contextmanager
def _locked() -> Any:
    if fcntl is None:
        raise AdminError("UNSUPPORTED_HOST", "The maintenance bridge requires a Linux host.")
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    descriptor = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(LOCK_FILE, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)  # type: ignore[attr-defined]
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)  # type: ignore[attr-defined]
        os.close(descriptor)


def _read_session() -> dict[str, Any] | None:
    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AdminError(
            "SESSION_CORRUPT",
            "The previous maintenance session record is corrupted.",
            "Ask the administrator to check /run/doxagent-site-login.",
        ) from exc
    if not isinstance(payload, dict) or not payload.get("profile_id") or not payload.get(
        "login_token"
    ):
        raise AdminError("SESSION_CORRUPT", "The previous maintenance session is incomplete.")
    return payload


def _write_session(payload: dict[str, Any]) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="session-", dir=STATE_DIR)
    path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        path.replace(SESSION_FILE)
        os.chmod(SESSION_FILE, 0o600)
    finally:
        path.unlink(missing_ok=True)


def _delete_session() -> None:
    SESSION_FILE.unlink(missing_ok=True)


def _container_id() -> str:
    command = [DOCKER, "ps", "--filter", CONTAINER_LABELS[0], "--filter", CONTAINER_LABELS[1],
               "--format", "{{.ID}}"]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdminError(
            "SERVICE_UNAVAILABLE",
            "The Site Access service is unavailable.",
            "Ask the administrator to check the container service.",
        ) from exc
    ids = [value for value in completed.stdout.splitlines() if value]
    if completed.returncode or len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{12,64}", ids[0]):
        raise AdminError(
            "SERVICE_UNAVAILABLE",
            "The Site Access service is not running.",
            "Ask the administrator to restart the service.",
        )
    return ids[0]


def _request(
    method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: int = 45
) -> Any:
    if method not in {"GET", "POST"} or not re.fullmatch(r"/[A-Za-z0-9_./:-]+", path):
        raise AdminError(
            "INTERNAL_POLICY", "The maintenance tool rejected an unauthorized request."
        )
    request = json.dumps(
        {"method": method, "path": path, "payload": payload, "timeout": timeout},
        separators=(",", ":"),
    )
    command = [DOCKER, "exec", "-i", _container_id(), "python", "-c", CONTAINER_REQUEST]
    try:
        completed = subprocess.run(
            command,
            input=request,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdminError(
            "SERVICE_UNAVAILABLE",
            "The Site Access service timed out.",
            "Refresh later or contact the administrator.",
        ) from exc
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AdminError(
            "SERVICE_UNAVAILABLE",
            "The Site Access service returned an invalid response.",
            "Ask the administrator to inspect the service logs.",
        ) from exc
    status = int(envelope.get("status", 0))
    body = envelope.get("body")
    if completed.returncode or not 200 <= status < 300:
        detail = body.get("detail") if isinstance(body, dict) else None
        if status == 404:
            code = "NOT_FOUND"
            message = "The profile or maintenance session was not found."
        elif status == 409:
            code = "PROFILE_BUSY"
            message = "The profile is busy or cannot enter maintenance mode."
        elif status == 422:
            code = "VERIFY_REJECTED"
            message = "The verification article is invalid or belongs to another site."
        else:
            code = "SERVICE_UNAVAILABLE"
            message = "The Site Access service is temporarily unavailable."
        suggestion = "Refresh the status and try again."
        if isinstance(detail, str) and "subscription" in detail.lower():
            suggestion = "The account may not have the required subscription."
        raise AdminError(code, message, suggestion)
    return body


def _vnc_ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5900), timeout=1.5) as connection:
            return connection.recv(4) == b"RFB "
    except OSError:
        return False


def _validate_profile_id(value: str) -> str:
    if not PROFILE_ID.fullmatch(value):
        raise AdminError("INVALID_PROFILE", "The profile ID is invalid.")
    return value


def _validate_override_url(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 2048:
        raise AdminError("INVALID_URL", "The verification article URL is too long.")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AdminError("INVALID_URL", "The verification article must use a complete HTTPS URL.")
    return value.strip()


def _public_session(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "profile_id": session["profile_id"],
        "site_id": session.get("site_id"),
        "site_name": session.get("site_name"),
        "opened_at": session.get("opened_at"),
        "viewer_pid": session.get("viewer_pid"),
        "viewer_running": _viewer_running(session.get("viewer_pid")),
    }


def _viewer_running(pid: Any) -> bool:
    return isinstance(pid, int) and Path(f"/proc/{pid}").exists()


def _find_viewer_pid() -> int | None:
    try:
        completed = subprocess.run(
            ["/usr/bin/pgrep", "-u", DESKTOP_USER, "-f", "vncviewer.*127.0.0.1.*5900"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for value in completed.stdout.splitlines():
        if value.isdigit() and Path(f"/proc/{value}").exists():
            return int(value)
    return None


def _inventory() -> dict[str, Any]:
    sites = _request("GET", "/v1/sites")
    profiles = _request("GET", "/v1/profiles")
    egresses = _request("GET", "/v1/egresses")
    egress_by_id = {item["egress_id"]: item for item in egresses}
    profile_by_id = {item["profile_id"]: item for item in profiles}
    rows: list[dict[str, Any]] = []
    for site_entry in sites:
        head = site_entry["head"]
        spec = site_entry["spec"]
        auth = spec.get("auth") or {}
        if not (auth.get("maintenance_url") or auth.get("login_url")):
            continue
        combinations = sorted(
            (item for item in spec["access"]["combinations"] if item.get("enabled", True)),
            key=lambda item: item["priority"],
        )
        runtime = _request("GET", f"/v1/combinations/{spec['site_id']}")
        combination_runtime = runtime.get("combinations") or {}
        for index, combination in enumerate(combinations):
            profile = profile_by_id.get(combination["profile_id"])
            egress = egress_by_id.get(combination["egress_id"])
            if profile is None or egress is None:
                continue
            rows.append(
                {
                    "site_id": spec["site_id"],
                    "site_name": spec.get("display_name")
                    or DISPLAY_NAMES.get(spec["site_id"], spec["site_id"]),
                    "site_enabled": bool(head["enabled"]),
                    "profile_id": profile["profile_id"],
                    "profile_role": "primary" if index == 0 else "backup",
                    "priority": combination["priority"],
                    "combination_id": combination.get("combination_id"),
                    "egress_id": egress["egress_id"],
                    "egress_node": egress["node_ref"],
                    "egress_enabled": bool(egress["enabled"]),
                    "auth_state": profile["auth_state"],
                    "operational_state": profile.get("operational_state", "AVAILABLE"),
                    "session_revision": profile["session_revision"],
                    "verification_configured": bool(auth.get("verification_url")),
                    "verification_kind": auth.get(
                        "verification_kind", "subscription_article"
                    ),
                    "observed_ip": egress.get("observed_ip"),
                    "observed_at": egress.get("observed_at"),
                    "manual_attention_required": bool(
                        (
                            combination_runtime.get(combination.get("combination_id"))
                            or {}
                        ).get(
                            "manual_attention_required"
                        )
                    ),
                }
            )
    return {"profiles": rows, "vnc_ready": _vnc_ready()}


def command_list() -> dict[str, Any]:
    with _locked():
        inventory = _inventory()
        inventory.update({"ok": True, "session": _public_session(_read_session())})
        return inventory


def command_open(profile_id: str) -> dict[str, Any]:
    profile_id = _validate_profile_id(profile_id)
    with _locked():
        existing = _read_session()
        if existing:
            raise AdminError(
                "SESSION_ACTIVE",
                f"{existing['profile_id']} is already under login maintenance.",
                "Continue or cancel the current session before opening another profile.",
            )
        inventory = _inventory()
        selected = next(
            (item for item in inventory["profiles"] if item["profile_id"] == profile_id), None
        )
        if selected is None or not selected["site_enabled"] or not selected["egress_enabled"]:
            raise AdminError(
                "PROFILE_DISABLED",
                "The profile is missing, the site is disabled, or the egress is unavailable.",
            )
        if not inventory["vnc_ready"]:
            raise AdminError(
                "VNC_UNAVAILABLE",
                "The browser viewer service is not running.",
                "Ask the administrator to check local port 5900.",
            )
        started = time.monotonic()
        response = _request("POST", f"/v1/profiles/{profile_id}/login:open", {}, timeout=40)
        token = response.get("login_token") if isinstance(response, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise AdminError("BROWSER_START_FAILED", "The login browser failed to start.")
        session = {
            "profile_id": profile_id,
            "site_id": selected["site_id"],
            "site_name": selected["site_name"],
            "login_token": token,
            "opened_at": dt.datetime.now(dt.UTC).isoformat(),
            "viewer_pid": None,
        }
        _write_session(session)
        return {
            "ok": True,
            "profile_id": profile_id,
            "site_name": selected["site_name"],
            "egress_node": selected["egress_node"],
            "browser_host": urlsplit(str(response.get("url") or "")).hostname,
            "opened_in_seconds": round(time.monotonic() - started, 2),
        }


def _close_token(session: dict[str, Any]) -> bool:
    try:
        _request("POST", "/v1/profiles/login:close", {"login_token": session["login_token"]})
        return True
    except AdminError as exc:
        if exc.code == "NOT_FOUND":
            return True
        return False


def command_verify(profile_id: str, override_url: str | None) -> dict[str, Any]:
    profile_id = _validate_profile_id(profile_id)
    override_url = _validate_override_url(override_url)
    with _locked():
        session = _read_session()
        if session is None:
            raise AdminError("NO_SESSION", "No login maintenance session is active.")
        if session["profile_id"] != profile_id:
            raise AdminError("PROFILE_MISMATCH", "The active session belongs to another profile.")
        inventory = _inventory()
        selected = next(
            (item for item in inventory["profiles"] if item["profile_id"] == profile_id), None
        )
        if selected is None:
            raise AdminError("PROFILE_DISABLED", "The current profile was disabled or deleted.")
        article_url = override_url
        if article_url is None:
            site = next(
                item["spec"]
                for item in _request("GET", "/v1/sites")
                if item["spec"]["site_id"] == selected["site_id"]
            )
            article_url = (site.get("auth") or {}).get("verification_url")
        if not article_url:
            raise AdminError(
                "VERIFY_URL_MISSING",
                "This site has no verification article configured.",
                "Use the advanced option to provide another article.",
            )
        result: Any = None
        failure: AdminError | None = None
        try:
            result = _request(
                "POST",
                f"/v1/profiles/{profile_id}:verify",
                {"article_url": article_url, "login_token": session["login_token"]},
                timeout=45,
            )
        except AdminError as exc:
            failure = exc
        closed = _close_token(session)
        if closed:
            _delete_session()
        if failure is not None:
            if not closed:
                failure.suggestion = (
                    "Verification failed and the session could not close. Refresh, then cancel it."
                )
            raise failure
        if not closed:
            raise AdminError(
                "CLOSE_FAILED",
                "Verification finished, but the maintenance browser did not close safely.",
                "Refresh and cancel the maintenance session.",
            )
        profile = result.get("profile", {})
        state = profile.get("auth_state", "UNKNOWN")
        reason = result.get("reason")
        messages = {
            "VALID": "Signed in with access to subscription articles.",
            "REAUTH_REQUIRED": "The login is still invalid. Sign in again.",
            "ENTITLEMENT_MISSING": (
                "The account is valid but lacks the subscription required for this article."
            ),
            "UNKNOWN": "Verification failed. The article may have expired or was not recognized.",
        }
        message = (
            "Public access is ready. This check does not assert a subscription login."
            if reason == "public_access_ready"
            else messages.get(state, "The verification result is unknown.")
        )
        return {
            "ok": True,
            "profile_id": profile_id,
            "auth_state": state,
            "message": message,
            "reason": reason,
            "closed": True,
        }


def command_close() -> dict[str, Any]:
    with _locked():
        session = _read_session()
        if session is None:
            return {"ok": True, "closed": False, "message": "No maintenance session is active."}
        if not _close_token(session):
            raise AdminError(
                "CLOSE_FAILED",
                "The maintenance session cannot be closed right now.",
                "Do not reopen the profile. Try again later.",
            )
        _delete_session()
        return {"ok": True, "closed": True, "message": "The maintenance session was closed."}


def command_recover() -> dict[str, Any]:
    with _locked():
        session = _read_session()
        if session is None:
            return {"ok": True, "session": None}
        try:
            inspection = _request(
                "POST",
                "/v1/profiles/login:inspect",
                {"login_token": session["login_token"]},
                timeout=8,
            )
        except AdminError as exc:
            if exc.code != "NOT_FOUND":
                raise
            _delete_session()
            return {
                "ok": True,
                "session": None,
                "recovered": "stale_session_removed",
                "message": "The stale maintenance session was removed after the service restart.",
            }
        if not _viewer_running(session.get("viewer_pid")):
            viewer_pid = _find_viewer_pid()
            if viewer_pid:
                session["viewer_pid"] = viewer_pid
                _write_session(session)
        return {
            "ok": True,
            "session": _public_session(session),
            "browser": {
                "host": urlsplit(str(inspection.get("url") or "")).hostname,
                "title": inspection.get("title"),
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", add_help=False)
    open_parser = subparsers.add_parser("open", add_help=False)
    open_parser.add_argument("profile_id")
    verify_parser = subparsers.add_parser("verify", add_help=False)
    verify_parser.add_argument("profile_id")
    verify_parser.add_argument("--url", dest="verification_url")
    subparsers.add_parser("close", add_help=False)
    subparsers.add_parser("recover", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        _ensure_root()
        arguments = build_parser().parse_args(argv)
        if arguments.command == "list":
            result = command_list()
        elif arguments.command == "open":
            result = command_open(arguments.profile_id)
        elif arguments.command == "verify":
            result = command_verify(arguments.profile_id, arguments.verification_url)
        elif arguments.command == "close":
            result = command_close()
        else:
            result = command_recover()
        _emit(result)
    except AdminError as exc:
        _fail(exc)
    except SystemExit:
        raise
    except Exception:
        _fail(
            AdminError(
                "INTERNAL_ERROR",
                "The login maintenance tool encountered an internal error.",
                "Ask the administrator to inspect the system logs.",
            )
        )


if __name__ == "__main__":
    main()
