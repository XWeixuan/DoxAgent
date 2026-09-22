from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_chrome_is_started_outside_playwright_then_attached_over_cdp() -> None:
    entrypoint = (
        ROOT / "deploy" / "barrons-cdp-attach-entrypoint.sh"
    ).read_text(encoding="utf-8")
    controller = (ROOT / "deploy" / "barrons-cdp-attach-control.py").read_text(
        encoding="utf-8"
    )

    assert "google-chrome-stable" in entrypoint
    assert "--remote-debugging-address=127.0.0.1" in entrypoint
    assert "--remote-debugging-port=9222" in entrypoint
    assert "--enable-automation" not in entrypoint
    assert "--no-sandbox" not in entrypoint
    assert "--headless" not in entrypoint
    assert "--user-agent" not in entrypoint
    assert "connect_over_cdp" in controller
    assert "launch(" not in controller
    assert "launch_persistent_context" not in controller
    assert "stealth" not in controller.lower()
    assert "route(" not in controller


def test_cdp_endpoint_is_internal_and_profile_is_isolated() -> None:
    compose = yaml.safe_load(
        (
            ROOT / "deploy" / "docker-compose.barrons-cdp-attach-control.yml"
        ).read_text(encoding="utf-8")
    )
    service = compose["services"]["barrons-cdp-attach-control"]

    assert compose["name"] == "barrons-cdp-attach-control"
    assert service["environment"]["CHROME_PROXY_SERVER"].endswith(":18081")
    assert service["ports"] == ["127.0.0.1:5903:5900"]
    assert all("9222" not in port for port in service["ports"])
    assert service["volumes"] == [
        "barrons-cdp-attach-control-profile:/chrome-profile"
    ]
    assert compose["volumes"]["barrons-cdp-attach-control-profile"][
        "name"
    ] == "doxagent-barrons-cdp-attach-control-profile"


def test_cdp_attach_desktop_launcher_has_no_terminal() -> None:
    desktop = (
        ROOT / "deploy" / "doxagent-barrons-cdp-attach-control.desktop"
    ).read_text(encoding="utf-8")
    assert "Exec=/usr/bin/vncviewer 127.0.0.1:5903" in desktop
    assert "Terminal=false" in desktop
