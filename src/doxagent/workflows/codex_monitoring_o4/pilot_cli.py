"""CLI for the isolated O4 CONFIGURE -> DELIVER Pilot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pilot import (
    MonitoringO4PilotCoordinator,
    MonitoringO4PilotEvent,
    MonitoringO4PilotRequest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doxagent-monitoring-o4-pilot")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="DoxAgent repository root (default: current directory)",
    )
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=Path(os.environ.get("DOXAGENT_PILOT_CASES_ROOT", "D:/DoxAgentPilot/cases"))
        / "monitoring_o4",
    )
    parser.add_argument(
        "--python", type=Path, default=Path(sys.executable), help="Python used by the Pilot MCP"
    )
    parser.add_argument(
        "--capability-secret",
        default=os.environ.get("DOXAGENT_O4_OPERATIONS_CAPABILITY_SECRET"),
        help="At least 32 bytes; prefer the environment variable",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--case-id", required=True)
    prepare.add_argument("--ticker", required=True)
    prepare.add_argument("--policy-set", required=True, type=Path)
    prepare.add_argument("--document2", required=True, type=Path)
    prepare.add_argument("--capability-hours", type=int, default=24 * 30)
    clone = commands.add_parser(
        "clone-deliver",
        help="create a fresh DELIVER-only case from a completed CONFIGURE case",
    )
    clone.add_argument("--source-case-root", required=True, type=Path)
    clone.add_argument("--target-case-root", required=True, type=Path)
    for name in ("advance", "refresh", "status"):
        command = commands.add_parser(name)
        command.add_argument("--case-root", required=True, type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "clone-deliver":
        clone_event = MonitoringO4PilotCoordinator.clone_deliver_case(
            source_case_root=args.source_case_root,
            target_case_root=args.target_case_root,
        )
        print(
            json.dumps(
                {
                    "status": clone_event.status,
                    "case_root": str(clone_event.case_root),
                    "node": clone_event.node.value if clone_event.node is not None else None,
                    "task_path": str(clone_event.task_path),
                    "message": clone_event.message,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return
    if not args.capability_secret and args.command not in {"advance", "status"}:
        raise SystemExit(
            "DOXAGENT_O4_OPERATIONS_CAPABILITY_SECRET or --capability-secret is required "
            "for prepare/refresh"
        )
    coordinator = MonitoringO4PilotCoordinator(
        repo_root=args.repo_root,
        cases_root=args.cases_root,
        python=args.python,
        capability_secret=args.capability_secret,
    )
    if args.command == "prepare":
        value: MonitoringO4PilotEvent | dict[str, object] = coordinator.prepare(
            MonitoringO4PilotRequest(
                case_id=args.case_id,
                ticker=args.ticker,
                policy_set_path=args.policy_set,
                document2_path=args.document2,
                capability_hours=args.capability_hours,
            )
        )
    elif args.command == "advance":
        value = coordinator.advance(args.case_root)
    elif args.command == "refresh":
        value = coordinator.refresh(args.case_root)
    else:
        value = coordinator.status(args.case_root)
    if isinstance(value, MonitoringO4PilotEvent):
        value = {
            "status": value.status,
            "case_root": str(value.case_root),
            "node": value.node.value if value.node is not None else None,
            "task_path": str(value.task_path),
            "message": value.message,
        }
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
