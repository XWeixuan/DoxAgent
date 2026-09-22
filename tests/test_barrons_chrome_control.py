from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_control_browser_is_official_chrome_without_automation_launch_flags() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile.barrons-chrome-control").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "barrons-chrome-control-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "google-chrome-stable" in dockerfile
    assert "Google Chrome 153" in dockerfile
    assert "playwright" not in dockerfile.lower()
    assert "playwright" not in entrypoint.lower()
    assert "--remote-debugging" not in entrypoint
    assert "--enable-automation" not in entrypoint
    assert "--no-sandbox" not in entrypoint
    assert "--headless" not in entrypoint
    assert "--user-agent" not in entrypoint


def test_control_browser_isolated_profile_proxy_and_loopback_vnc() -> None:
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.barrons-chrome-control.yml").read_text(
            encoding="utf-8"
        )
    )
    service = compose["services"]["barrons-chrome-control"]

    assert service["environment"]["CHROME_PROXY_SERVER"].endswith(":18081")
    assert service["ports"] == ["127.0.0.1:5901:5900"]
    assert service["volumes"] == [
        "barrons-chrome-control-profile:/chrome-profile"
    ]
    assert "no-new-privileges:true" in service["security_opt"]
    assert "apparmor=doxagent-site-access" in service["security_opt"]
    assert compose["volumes"]["barrons-chrome-control-profile"]["name"] \
        == "doxagent-barrons-chrome-control-profile"


def test_control_desktop_launcher_has_no_terminal() -> None:
    desktop = (
        ROOT / "deploy" / "doxagent-barrons-chrome-control.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=/usr/bin/vncviewer 127.0.0.1:5901" in desktop
    assert "Terminal=false" in desktop
