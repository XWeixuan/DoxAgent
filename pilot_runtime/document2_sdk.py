"""Installed Pilot runtime entry point; no Codex App project/session required."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    runtime_root = Path(__file__).resolve().parent
    load_dotenv(runtime_root / ".env.local", override=True)
    repo_root = Path(os.environ["DOXAGENT_PILOT_REPO_ROOT"]).resolve()
    sys.path.insert(0, str(repo_root / "src"))
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in {"start", "continue"}:
        defaults = {
            "--repo-root": repo_root,
            "--runtime-env-file": os.environ["DOXAGENT_PILOT_ENV_FILE"],
            "--cases-root": os.environ["DOXAGENT_PILOT_CASES_ROOT"],
            "--coordinators-root": Path(os.environ["DOXAGENT_PILOT_CASES_ROOT"])
            / "document2"
            / "_coordinators",
        }
        for key, value in defaults.items():
            if not any(arg == key or arg.startswith(key + "=") for arg in sys.argv):
                sys.argv.extend([key, str(value)])
    from doxagent.pilot.document2_sdk import main as sdk_main

    sdk_main()


if __name__ == "__main__":
    main()
