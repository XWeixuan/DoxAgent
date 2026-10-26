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
    "digitimes": "DIGITIMES English",
    "digitimes_tw": "DIGITIMES Taiwan",
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
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except BlockingIOError as exc:
            raise AdminError(
                "BRIDGE_BUSY", "Another desktop command is saving its state.", "Retry in a moment."
            ) from exc
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
    if (
        not isinstance(payload, dict)
        or not payload.get("profile_id")
        or not payload.get("login_token")
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
    command = [
        DOCKER,
        "ps",
        "--filter",
        CONTAINER_LABELS[0],
        "--filter",
        CONTAINER_LABELS[1],
        "--format",
        "{{.ID}}",
    ]
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
        if isinstance(detail, dict) and detail.get("code") == "MAINTENANCE_OPEN_FAILED":
            code = "MAINTENANCE_OPEN_FAILED"
            stage = detail.get("stage")
            labels = {
                "page_allocation": "browser page allocation",
                "navigation_gate": "navigation protection",
                "viewer_selection": "browser viewer selection",
            }
            message = "Could not complete " + labels.get(stage, "browser startup") + "."
            suggestion = (
                "No login window was allocated. Retry or ask the administrator "
                "to inspect the browser runtime."
            )
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
        "identity_id": session.get("identity_id"),
        "runtime_kind": session.get("runtime_kind"),
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
    inventory = _request("GET", "/v1/maintenance/inventory", timeout=3)
    inventory["vnc_ready"] = _vnc_ready()
    return inventory


def command_list() -> dict[str, Any]:
    inventory = _inventory()
    inventory["ok"] = True
    return inventory


ACTIONS = {
    "OPEN_LOGIN",
    "OPEN_CHALLENGE",
    "VERIFY_LOGIN",
    "VERIFY_CHALLENGE",
    "RELEASE_VIEWER",
    "CLOSE_LOGIN",
    "REOPEN_LOST_CHALLENGE",
    "ACTIVATE_PRIMARY",
}


def command_action(arguments) -> dict[str, Any]:
    if arguments.action not in ACTIONS:
        raise AdminError("INVALID_ACTION", "Unsupported maintenance action.")
    payload = {"action": arguments.action, "idempotency_key": arguments.idempotency_key}
    for field in ("scope_id", "identity_id", "session_id", "task_id"):
        value = getattr(arguments, field)
        if value:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_:.-]{0,199}", value):
                raise AdminError("INVALID_REFERENCE", "Invalid source or session reference.")
            payload[field] = value
    if arguments.url:
        payload["verification_url"] = _validate_override_url(arguments.url)
    if getattr(arguments, "confirm_discard", False):
        if arguments.action != "CLOSE_LOGIN" or not arguments.task_id:
            raise AdminError("INVALID_ACTION", "Discard requires a Challenge task reference.")
        payload["confirm_discard"] = True
    operation = _request("POST", "/v1/maintenance/operations", payload, timeout=3)
    # Network and Docker have completed before taking the short local state lock.
    with _locked():
        reference = STATE_DIR / "operation.json"
        temporary = reference.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"operation_id": operation["operation_id"]}, stream)
        os.replace(temporary, reference)
    return {"ok": True, **operation}


def command_operation(operation_id: str, *, cancel=False) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", operation_id):
        raise AdminError("INVALID_REFERENCE", "Invalid operation reference.")
    suffix = ":cancel" if cancel else ""
    result = _request(
        "POST" if cancel else "GET", f"/v1/maintenance/operations/{operation_id}{suffix}", timeout=3
    )
    return {"ok": True, **result}


def _read_session_locked():
    with _locked():
        return _read_session()


def _write_session_locked(session):
    with _locked():
        existing = _read_session()
        if existing and existing["login_token"] != session["login_token"]:
            raise AdminError(
                "SESSION_CHANGED", "Another session was selected; refresh the desktop tool."
            )
        _write_session(session)


def _delete_session_locked(token):
    with _locked():
        current = _read_session()
        if current and current["login_token"] == token:
            _delete_session()


def _legacy_open(profile_id: str, site_id: str | None = None) -> dict[str, Any]:
    profile_id = _validate_profile_id(profile_id)
    existing = _read_session_locked()
    if existing:
        raise AdminError(
            "SESSION_ACTIVE",
            f"{existing['profile_id']} is already under login maintenance.",
            "Continue or cancel the current session before opening another profile.",
        )
    inventory = _inventory()
    selected = next(
        (
            item
            for item in inventory["profiles"]
            if (
                item["profile_id"] == profile_id
                or item["identity_id"] == profile_id
                or item["row_id"] == profile_id
            )
            and (site_id is None or item["site_id"] == site_id)
        ),
        None,
    )
    if selected is None or not selected["site_enabled"] or not selected["egress_enabled"]:
        raise AdminError(
            "PROFILE_DISABLED",
            "The profile is missing, the site is disabled, or the egress is unavailable.",
        )
    started = time.monotonic()
    if (
        selected["runtime_kind"] == "managed_playwright"
        and selected["identity_id"] == selected["profile_id"]
    ):
        path = f"/v1/profiles/{selected['profile_id']}/login:open"
        payload: dict[str, Any] = {}
    else:
        path = f"/v1/identities/{selected['identity_id']}/login:open"
        payload = {"site_id": selected["site_id"]}
    response = _request("POST", path, payload, timeout=40)
    token = response.get("login_token") if isinstance(response, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise AdminError("BROWSER_START_FAILED", "The login browser failed to start.")
    session = {
        "profile_id": selected["profile_id"],
        "identity_id": selected["identity_id"],
        "runtime_kind": selected["runtime_kind"],
        "site_id": selected["site_id"],
        "site_name": selected["site_name"],
        "login_token": token,
        "opened_at": dt.datetime.now(dt.UTC).isoformat(),
        "viewer_pid": None,
        "challenge_url": selected.get("challenge_url"),
    }
    _write_session_locked(session)
    viewer_deadline = time.monotonic() + 5
    while not _vnc_ready():
        if time.monotonic() >= viewer_deadline:
            raise AdminError(
                "VNC_UNAVAILABLE",
                "The browser started, but the viewer is unavailable.",
                "The maintenance session is retained; retry the viewer or explicitly cancel.",
            )
        time.sleep(0.1)
    return {
        "ok": True,
        "profile_id": selected["profile_id"],
        "identity_id": selected["identity_id"],
        "site_name": selected["site_name"],
        "egress_node": selected["egress_node"],
        "browser_host": urlsplit(str(response.get("url") or "")).hostname,
        "opened_in_seconds": round(time.monotonic() - started, 2),
        "navigation_state": response.get("navigation_state", "PENDING"),
    }


def _close_token(session: dict[str, Any]) -> bool:
    try:
        _request("POST", "/v1/profiles/login:close", {"login_token": session["login_token"]})
        return True
    except AdminError as exc:
        if exc.code == "NOT_FOUND":
            return True
        return False


def _legacy_verify(
    profile_id: str, override_url: str | None, site_id: str | None = None
) -> dict[str, Any]:
    profile_id = _validate_profile_id(profile_id)
    override_url = _validate_override_url(override_url)
    session = _read_session_locked()
    if session is None:
        raise AdminError("NO_SESSION", "No login maintenance session is active.")
    if profile_id not in {session["profile_id"], session.get("identity_id")}:
        raise AdminError("PROFILE_MISMATCH", "The active session belongs to another profile.")
    if site_id is not None and session.get("site_id") != site_id:
        raise AdminError("PROFILE_MISMATCH", "The active session belongs to another site.")
    inventory = _inventory()
    selected = next(
        (
            item
            for item in inventory["profiles"]
            if item["profile_id"] == session["profile_id"]
            and item["site_id"] == session.get("site_id")
        ),
        None,
    )
    if selected is None:
        raise AdminError("PROFILE_DISABLED", "The current profile was disabled or deleted.")
    article_url = session.get("challenge_url") or override_url
    if session.get("challenge_url") and override_url not in {None, article_url}:
        raise AdminError(
            "CHALLENGE_ARTICLE_MISMATCH",
            "Verify the article that triggered the challenge in the preserved tab.",
        )
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
        if session.get("identity_id") and session["identity_id"] != session["profile_id"]:
            path = f"/v1/identities/{session['identity_id']}:verify"
            payload = {
                "site_id": session["site_id"],
                "article_url": article_url,
                "login_token": session["login_token"],
            }
        else:
            path = f"/v1/profiles/{profile_id}:verify"
            payload = {"article_url": article_url, "login_token": session["login_token"]}
        result = _request("POST", path, payload, timeout=45)
    except AdminError as exc:
        failure = exc
    if session.get("challenge_url"):
        if failure is not None:
            raise failure
        profile = result.get("profile", {})
        state = (result.get("auth") or {}).get("auth_state") or profile.get("auth_state")
        public_ready = result.get("reason") == "public_access_ready"
        if (state != "VALID" and not public_ready) or not profile.get("challenge_verified"):
            return {
                "ok": True,
                "profile_id": profile_id,
                "auth_state": state or "UNKNOWN",
                "message": (
                    "The original article is not verified yet. Keep this tab open "
                    "and retry after resolving the challenge."
                ),
                "reason": result.get("reason"),
                "closed": False,
            }
    if failure is not None:
        raise failure  # Verification errors must preserve the original login page.
    profile = result.get("profile", {})
    state = (result.get("auth") or {}).get("auth_state") or profile.get("auth_state", "UNKNOWN")
    if state != "VALID" and result.get("reason") != "public_access_ready":
        return {
            "ok": True,
            "closed": False,
            "profile_id": profile_id,
            "auth_state": state,
            "reason": result.get("reason"),
            "message": "Access is not verified. The original page is preserved.",
        }
    closed = _close_token(session)
    if closed:
        _delete_session_locked(session["login_token"])
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
    state = (result.get("auth") or {}).get("auth_state") or profile.get("auth_state", "UNKNOWN")
    reason = result.get("reason")
    messages = {
        "VALID": "Signed in with access to subscription articles.",
        "REAUTH_REQUIRED": "The login is still invalid. Sign in again.",
        "ENTITLEMENT_MISSING": (
            "Subscription access is unavailable. Sign in with an entitled account and verify again."
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


def _legacy_close() -> dict[str, Any]:
    session = _read_session_locked()
    if session is None:
        return {"ok": True, "closed": False, "message": "No maintenance session is active."}
    if not _close_token(session):
        raise AdminError(
            "CLOSE_FAILED",
            "The maintenance session cannot be closed right now.",
            "Do not reopen the profile. Try again later.",
        )
    _delete_session_locked(session["login_token"])
    return {"ok": True, "closed": True, "message": "The maintenance session was closed."}


def _legacy_recover() -> dict[str, Any]:
    session = _read_session_locked()
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
        if (
            session.get("runtime_kind") == "external_chrome"
            and session.get("identity_id")
            and session.get("site_id")
        ):
            try:
                inspection = _request(
                    "POST",
                    f"/v1/identities/{session['identity_id']}/login:recover",
                    {
                        "site_id": session["site_id"],
                        "login_token": session["login_token"],
                    },
                    timeout=20,
                )
            except AdminError:
                pass
            else:
                return {
                    "ok": True,
                    "session": _public_session(session),
                    "browser": {
                        "host": urlsplit(str(inspection.get("url") or "")).hostname,
                        "title": None,
                    },
                    "recovered": "external_chrome_reattached",
                    "message": "The long-running Chrome maintenance page was reattached.",
                }
        return {
            "ok": True,
            "session": _public_session(session),
            "recovered": "target_presence_unverified",
            "message": "Original target presence is unverified; the session reference is retained.",
        }
    return {
        "ok": True,
        "session": _public_session(session),
        "browser": {
            "host": urlsplit(str(inspection.get("url") or "")).hostname,
            "title": inspection.get("title"),
            "navigation_state": inspection.get("navigation_state"),
            "navigation_error": inspection.get("navigation_error"),
        },
    }


def _compat_action(action, **references):
    arguments = argparse.Namespace(
        action=action,
        idempotency_key=os.urandom(16).hex(),
        scope_id=None,
        identity_id=None,
        session_id=None,
        task_id=None,
        url=None,
        confirm_discard=False,
    )
    for key, value in references.items():
        setattr(arguments, key, value)
    return command_action(arguments)


def command_open(profile_id, site_id=None):
    inventory = _inventory()
    selected = next(
        (
            row
            for row in inventory["profiles"]
            if profile_id in {row["profile_id"], row["identity_id"], row["row_id"]}
            and (site_id is None or row["site_id"] == site_id)
        ),
        None,
    )
    if selected and selected.get("scope_id"):
        if not selected["site_enabled"] or not selected["egress_enabled"]:
            raise AdminError("PROFILE_DISABLED", "The source or egress is disabled.")
        return _compat_action(
            "OPEN_LOGIN", scope_id=selected["scope_id"], identity_id=selected["identity_id"]
        )
    return _legacy_open(profile_id, site_id)


def command_verify(profile_id, override_url=None, site_id=None):
    inventory = _inventory()
    identities = {
        row["identity_id"]
        for row in inventory["profiles"]
        if profile_id in {row["profile_id"], row["identity_id"], row["row_id"]}
        and (site_id is None or row["site_id"] == site_id)
    }
    matches = [
        session
        for session in inventory.get("sessions", [])
        if session["identity_id"] in identities and session["state"] != "CLOSED"
    ]
    if len(matches) == 1:
        session = matches[0]
        return _compat_action(
            "VERIFY_CHALLENGE" if session["kind"] == "CHALLENGE" else "VERIFY_LOGIN",
            session_id=session["session_id"],
            url=override_url,
        )
    if len(matches) > 1:
        raise AdminError("AMBIGUOUS_SESSION", "Select the exact session in the desktop tool.")
    return _legacy_verify(profile_id, override_url, site_id)


def command_close():
    inventory = _inventory()
    session = next((s for s in inventory.get("sessions", []) if s["state"] == "OPEN"), None)
    if session:
        if session["kind"] == "CHALLENGE":
            return _compat_action("RELEASE_VIEWER", session_id=session["session_id"])
        return _compat_action("CLOSE_LOGIN", session_id=session["session_id"])
    return _legacy_close()


def command_recover():
    inventory = _inventory()
    if "sessions" in inventory:
        session = next((s for s in inventory["sessions"] if s["state"] == "OPEN"), None)
        return {
            "ok": True,
            "session": session,
            "sessions": inventory["sessions"],
            "message": "Select Continue to explicitly reopen the viewer; no browser was changed.",
        }
    return _legacy_recover()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", add_help=False)
    evidence = subparsers.add_parser("evidence", add_help=False)
    evidence.add_argument("task_id")
    evidence.add_argument("phase", choices=("first", "confirmed", "latest_actionable", "after_action"), default="confirmed", nargs="?")
    open_parser = subparsers.add_parser("open", add_help=False)
    open_parser.add_argument("profile_id")
    open_parser.add_argument("--site-id")
    verify_parser = subparsers.add_parser("verify", add_help=False)
    verify_parser.add_argument("profile_id")
    verify_parser.add_argument("--url", dest="verification_url")
    verify_parser.add_argument("--site-id")
    subparsers.add_parser("close", add_help=False)
    subparsers.add_parser("recover", add_help=False)
    action = subparsers.add_parser("action", add_help=False)
    action.add_argument("action", choices=sorted(ACTIONS))
    action.add_argument("--idempotency-key", required=True)
    action.add_argument("--confirm-discard", action="store_true")
    for field in ("scope-id", "identity-id", "session-id", "task-id", "url"):
        action.add_argument("--" + field)
    for name in ("status", "cancel-operation"):
        command = subparsers.add_parser(name, add_help=False)
        command.add_argument("operation_id")
    return parser


def main(argv: list[str] | None = None) -> None:
    try:
        _ensure_root()
        arguments = build_parser().parse_args(argv)
        if arguments.command == "action":
            result = command_action(arguments)
        elif arguments.command in {"status", "cancel-operation"}:
            result = command_operation(
                arguments.operation_id, cancel=arguments.command == "cancel-operation"
            )
        elif arguments.command == "list":
            result = command_list()
        elif arguments.command == "evidence":
            task_id = _validate_profile_id(arguments.task_id)
            value = _request(
                "GET", f"/v1/challenges/{task_id}/evidence/{arguments.phase}", timeout=3
            )
            result = {"ok": True, **value}
        elif arguments.command == "open":
            result = command_open(arguments.profile_id, arguments.site_id)
        elif arguments.command == "verify":
            result = command_verify(
                arguments.profile_id, arguments.verification_url, arguments.site_id
            )
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
