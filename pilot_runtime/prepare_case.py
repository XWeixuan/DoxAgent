"""Persistent Pilot runtime entrypoint copied to D:\\DoxAgentPilot\\runtime."""

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
    from doxagent.codex_runtime.schema import CodexD1Node
    from doxagent.pilot.case_builder import (
        PilotCaseBuilder,
        PilotCaseRequest,
        prepare_case_sync,
    )

    parser = argparse.ArgumentParser(description="Build a removable Codex D1 Pilot case")
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--node", required=True, choices=[item.value for item in CodexD1Node])
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--capability-hours", type=int, default=8)
    args = parser.parse_args()
    builder = PilotCaseBuilder(
        repo_root=repo_root,
        cases_root=Path(os.environ["DOXAGENT_PILOT_CASES_ROOT"]),
        python=Path(os.environ["DOXAGENT_PILOT_PYTHON"]),
        runtime_env_file=Path(os.environ["DOXAGENT_PILOT_ENV_FILE"]),
    )
    result = prepare_case_sync(
        builder,
        PilotCaseRequest(
            source_run=args.source_run,
            node=CodexD1Node(args.node),
            case_id=args.case_id,
            capability_hours=args.capability_hours,
        ),
    )
    print(
        json.dumps(
            {
                "case_root": str(result.case_root),
                "node": result.node.value,
                "attempt_id": result.attempt_id,
                "input_sha256": result.input_sha256,
                "task_path": str(result.task_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n--- COPY THIS INTO CODEX APP ---\n")
    print(result.task_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
