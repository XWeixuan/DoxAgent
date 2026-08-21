"""Persistent Pilot doctor entrypoint copied to D:\\DoxAgentPilot\\runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    runtime_root = Path(__file__).resolve().parent
    load_dotenv(runtime_root / ".env.local", override=True)
    repo_root = Path(os.environ["DOXAGENT_PILOT_REPO_ROOT"]).resolve()
    sys.path.insert(0, str(repo_root / "src"))
    from doxagent.pilot.doctor import run_doctor_sync

    parser = argparse.ArgumentParser(description="Verify a generated Codex D1 Pilot case")
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    result = run_doctor_sync(args.case)
    print(
        json.dumps(
            {
                "passed": result.passed,
                "report_path": str(result.report_path),
                "checks": result.checks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
