"""CLI entry point for explicit D3 initialize and maintenance runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from doxagent.settings import DoxAgentSettings

from .service import build_document3_orchestrator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-document3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--ticker", required=True)
    initialize.add_argument("--document2-run-id")
    initialize.add_argument("--event-library-version", type=int)
    initialize.add_argument("--run-id")
    maintain = subparsers.add_parser("maintain")
    maintain.add_argument("--ticker", required=True)
    maintain.add_argument("--event-library-version", type=int)
    maintain.add_argument("--run-id")
    for command in (initialize, maintain):
        command.add_argument("--orchestration-version", choices=["v2", "v2.1"], default="v2")
        command.add_argument("--node-assets", "--node-assets-manifest", type=Path)
        command.add_argument("--as-of", type=datetime.fromisoformat)
        command.add_argument("--additional-material", action="append", default=[])
        command.add_argument("--materials-manifest", type=Path)
    initialize.add_argument("--source-global-run-id")
    maintain.add_argument("--base-policy-version", "--base-policy-set-version", type=int)
    maintain.add_argument("--maintenance-feed", type=Path)
    maintain.add_argument("--delta", type=Path)
    maintain.add_argument("--explicit-maintenance", action="store_true")
    resume = subparsers.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--orchestration-version", choices=["v2.1"], default="v2.1")
    resume.add_argument("--node-assets", "--node-assets-manifest", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> int:
    orchestrator = build_document3_orchestrator(
        DoxAgentSettings(),
        orchestration_version=args.orchestration_version,
        node_assets=json.loads(args.node_assets.read_text(encoding="utf8"))
        if args.node_assets
        else None,
    )
    if args.command == "resume":
        from .recovery import resume_v21

        result = await resume_v21(orchestrator, args.run_id)
        print(result.model_dump_json(indent=2))
        return 0
    if args.orchestration_version == "v2.1":
        if args.as_of is None:
            raise ValueError("V21 requires --as-of with timezone")
        common = dict(
            ticker=args.ticker,
            as_of=args.as_of,
            event_library_version=args.event_library_version,
            run_id=args.run_id,
            additional_materials=_materials(args),
        )
        if args.command == "initialize":
            result = await orchestrator.initialize(
                **common,
                document2_run_id=args.document2_run_id,
                source_global_run_id=args.source_global_run_id,
            )
        else:
            if args.base_policy_version is None:
                raise ValueError("V21 MAINTAIN requires --base-policy-version")
            result = await orchestrator.maintain(
                **common,
                base_policy_version=args.base_policy_version,
                maintenance_feed=json.loads(args.maintenance_feed.read_text(encoding="utf8"))
                if args.maintenance_feed
                else None,
                delta=json.loads(args.delta.read_text(encoding="utf8")) if args.delta else None,
                explicit_maintenance=args.explicit_maintenance,
            )
        print(result.model_dump_json(indent=2))
        return 0
    if args.command == "initialize":
        if args.document2_run_id is None:
            raise ValueError("V2 requires --document2-run-id")
        result = await orchestrator.initialize(
            ticker=args.ticker,
            document2_run_id=args.document2_run_id,
            event_library_version=args.event_library_version,
            run_id=args.run_id,
        )
    else:
        result = await orchestrator.maintain(
            ticker=args.ticker,
            event_library_version=args.event_library_version,
            run_id=args.run_id,
        )
    print(result.model_dump_json(indent=2))
    return 0


def _materials(args: argparse.Namespace) -> list:
    materials = list(args.additional_material)
    if args.materials_manifest:
        records = json.loads(args.materials_manifest.read_text(encoding="utf8"))
        if not isinstance(records, list):
            raise ValueError("--materials-manifest requires a JSON list of material descriptors")
        materials.extend(records)
    return materials


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
