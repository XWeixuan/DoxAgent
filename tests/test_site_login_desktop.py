from __future__ import annotations

import importlib.util
import os
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType

import pytest


def _load_admin() -> ModuleType:
    path = Path(__file__).parents[1] / "deploy" / "site-login-admin.py"
    spec = importlib.util.spec_from_file_location("site_login_admin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_admin()
    monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(module, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(module, "LOCK_FILE", tmp_path / "session.lock")
    monkeypatch.setattr(module, "_locked", nullcontext)
    return module


def _inventory_responses(method: str, path: str, payload=None, *, timeout: int = 45):
    del method, payload, timeout
    if path == "/v1/sites":
        return [
            {
                "head": {"enabled": True},
                "spec": {
                    "site_id": "barrons",
                    "display_name": None,
                    "auth": {
                        "requirement": "required",
                        "login_url": "https://www.barrons.com/login",
                        "verification_url": "https://www.barrons.com/articles/example",
                    },
                    "access": {
                        "combinations": [
                            {
                                "profile_id": "barrons-1",
                                "egress_id": "us-standard-5",
                                "priority": 10,
                            },
                            {
                                "profile_id": "barrons-2",
                                "egress_id": "jp-standard-6",
                                "priority": 20,
                            },
                        ]
                    },
                },
            }
        ]
    if path == "/v1/profiles":
        return [
            {
                "profile_id": "barrons-1",
                "auth_state": "VALID",
                "session_revision": 2,
            },
            {
                "profile_id": "barrons-2",
                "auth_state": "UNKNOWN",
                "session_revision": 0,
            },
        ]
    if path == "/v1/egresses":
        return [
            {
                "egress_id": "us-standard-5",
                "node_ref": "US node",
                "enabled": True,
            },
            {
                "egress_id": "jp-standard-6",
                "node_ref": "JP node",
                "enabled": True,
            },
        ]
    raise AssertionError(path)


def test_inventory_exposes_safe_profile_status_without_secrets(admin, monkeypatch) -> None:
    monkeypatch.setattr(admin, "_request", _inventory_responses)
    monkeypatch.setattr(admin, "_vnc_ready", lambda: True)
    result = admin.command_list()

    assert [row["profile_role"] for row in result["profiles"]] == ["primary", "backup"]
    assert result["profiles"][0]["auth_state"] == "VALID"
    assert result["profiles"][1]["auth_state"] == "UNKNOWN"
    assert "login_token" not in repr(result)


def test_open_persists_root_session_but_never_returns_token(admin, monkeypatch) -> None:
    def request(method: str, path: str, payload=None, *, timeout: int = 45):
        if path == "/v1/profiles/barrons-1/login:open":
            return {"login_token": "a" * 32, "url": "https://www.barrons.com/login"}
        return _inventory_responses(method, path, payload, timeout=timeout)

    monkeypatch.setattr(admin, "_request", request)
    monkeypatch.setattr(admin, "_vnc_ready", lambda: True)
    result = admin.command_open("barrons-1")

    assert result["ok"] is True
    assert "login_token" not in result
    assert result["browser_host"] == "www.barrons.com"
    assert "browser_url" not in result
    stored = admin._read_session()
    assert stored["login_token"] == "a" * 32
    if os.name == "posix":
        assert admin.SESSION_FILE.stat().st_mode & 0o777 == 0o600
    with pytest.raises(admin.AdminError, match="已在登录维护中"):
        admin.command_open("barrons-2")


def test_verify_uses_registry_url_closes_session_and_reports_entitlement(
    admin, monkeypatch
) -> None:
    admin._write_session(
        {
            "profile_id": "barrons-1",
            "site_id": "barrons",
            "site_name": "Barron's",
            "login_token": "b" * 32,
            "opened_at": "2026-09-21T00:00:00+00:00",
            "viewer_pid": None,
        }
    )
    calls: list[tuple[str, str, object]] = []

    def request(method: str, path: str, payload=None, *, timeout: int = 45):
        calls.append((method, path, payload))
        if path == "/v1/profiles/barrons-1:verify":
            assert payload == {"article_url": "https://www.barrons.com/articles/example"}
            return {
                "profile": {"auth_state": "ENTITLEMENT_MISSING"},
                "reason": "subscription_required",
            }
        if path == "/v1/profiles/login:close":
            return {"closed": True}
        return _inventory_responses(method, path, payload, timeout=timeout)

    monkeypatch.setattr(admin, "_request", request)
    monkeypatch.setattr(admin, "_vnc_ready", lambda: True)
    result = admin.command_verify("barrons-1", None)

    assert result["auth_state"] == "ENTITLEMENT_MISSING"
    assert result["closed"] is True
    assert not admin.SESSION_FILE.exists()
    assert calls[-1][1] == "/v1/profiles/login:close"


@pytest.mark.parametrize(
    "value",
    [
        "http://www.barrons.com/articles/example",
        "https://user:pass@www.barrons.com/articles/example",
        "not-a-url",
    ],
)
def test_advanced_verification_url_requires_safe_https(admin, value: str) -> None:
    with pytest.raises(admin.AdminError):
        admin._validate_override_url(value)


def test_desktop_files_keep_terminal_and_docker_access_disabled() -> None:
    root = Path(__file__).parents[1]
    desktop = (root / "deploy" / "doxagent-site-login.desktop").read_text(encoding="utf-8")
    installer = (root / "deploy" / "install-site-login-desktop.sh").read_text(
        encoding="utf-8"
    )
    assert "Terminal=false" in desktop
    assert "usermod -aG docker" not in installer
    assert "NOPASSWD: /usr/local/sbin/doxagent-site-login-admin *" in installer
