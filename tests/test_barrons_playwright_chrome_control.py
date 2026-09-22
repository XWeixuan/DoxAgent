from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_playwright_control_uses_official_chrome_with_expected_variable() -> None:
    dockerfile = (
        ROOT / "deploy" / "Dockerfile.barrons-playwright-chrome-control"
    ).read_text(encoding="utf-8")
    launcher = (
        ROOT / "deploy" / "barrons-playwright-chrome-control.py"
    ).read_text(encoding="utf-8")

    assert "FROM doxagent-barrons-chrome-control:153" in dockerfile
    assert '"playwright==${PLAYWRIGHT_VERSION}"' in dockerfile
    assert 'executable_path="/usr/bin/google-chrome-stable"' in launcher
    assert "launch_persistent_context" in launcher
    assert "chromium_sandbox=True" in launcher
    assert "no_viewport=True" in launcher
    assert "ignore_default_args" not in launcher
    assert "stealth" not in launcher.lower()
    assert "user_agent" not in launcher
    assert "route(" not in launcher


def test_playwright_control_has_separate_profile_and_loopback_vnc() -> None:
    compose = yaml.safe_load(
        (
            ROOT
            / "deploy"
            / "docker-compose.barrons-playwright-chrome-control.yml"
        ).read_text(encoding="utf-8")
    )
    service = compose["services"]["barrons-playwright-chrome-control"]

    assert service["environment"]["CHROME_PROXY_SERVER"].endswith(":18081")
    assert service["ports"] == ["127.0.0.1:5902:5900"]
    assert service["volumes"] == [
        "barrons-playwright-chrome-control-profile:/chrome-profile"
    ]
    assert compose["volumes"]["barrons-playwright-chrome-control-profile"][
        "name"
    ] == "doxagent-barrons-playwright-chrome-control-profile"


def test_playwright_control_desktop_launcher_has_no_terminal() -> None:
    desktop = (
        ROOT / "deploy" / "doxagent-barrons-playwright-chrome-control.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=/usr/bin/vncviewer 127.0.0.1:5902" in desktop
    assert "Terminal=false" in desktop
