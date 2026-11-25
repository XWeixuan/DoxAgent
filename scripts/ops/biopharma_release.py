"""Reuse the locked per-role release owner; preserve unrelated production overlays."""

import subprocess
import sys

import dynamic_ownership_release as release

if __name__ == "__main__":
    commit = sys.argv[sys.argv.index("--commit") + 1]
    release.ROLES = [
        "v2-api",
        "v2-message-collector",
        "v2-message-bus",
        "v2-content-enrichment",
        "v2-initialization",
        "v2-o4",
    ]
    # Initializer/O4 images predate some publisher modules. Copy this complete
    # bounded package, preserving their separate progress/repair/trading code.
    release.FILES = subprocess.check_output(
        [
            "git",
            "-C",
            str(release.REPO),
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "src/doxagent/message_bus_v2",
        ],
        text=True,
    ).splitlines()
    release.FILES += ["src/doxagent/api_v2/bus.py"]
    release.COPY_ROOTS=('src/doxagent/message_bus_v2',)
    release.main()
