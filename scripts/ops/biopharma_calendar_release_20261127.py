"""Publish only the calendar listing and local delivery guard on current images."""

import dynamic_ownership_release as release

if __name__ == "__main__":
    release.ROLES = ["v2-message-collector", "v2-message-bus"]
    release.FILES = [
        "src/doxagent/message_bus_v2/" + name
        for name in (
            "biopharma_parsers.py",
            "live.py",
        )
    ]
    release.main()
