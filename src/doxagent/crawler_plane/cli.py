"""Local operator CLI for the Crawler Plane application service."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.schema import CrawlerExecutionRequest, CrawlerVersionSpec, new_id
from doxagent.settings import DoxAgentSettings


def _json_object(value: str) -> dict[str, Any]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doxagent-crawler-plane")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    get = commands.add_parser("get")
    get.add_argument("crawler_id")
    get.add_argument("version", type=int)
    create = commands.add_parser("create-version")
    create.add_argument("crawler_id")
    create.add_argument("version", type=int)
    create.add_argument("--entrypoint", default="crawler.py:crawl")
    create.add_argument("--parameter-schema", type=_json_object, default={"type": "object"})
    create.add_argument("--checkpoint-schema-version", type=int, default=1)
    create.add_argument("--base-version", type=int)
    for name in ("certify", "promote", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("crawler_id")
        command.add_argument("version", type=int)
    run = commands.add_parser("run")
    run.add_argument("crawler_id")
    run.add_argument("ticker")
    run.add_argument("source_id")
    run.add_argument("binding_id")
    run.add_argument("--parameters", type=_json_object, default={})
    run.add_argument("--version", type=int)
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    service = build_crawler_plane_service(DoxAgentSettings())
    try:
        value: BaseModel | Sequence[BaseModel]
        if args.command == "list":
            value = list(service.list_crawlers())
        elif args.command == "get":
            value = service.get_version(args.crawler_id, args.version)
        elif args.command == "create-version":
            value = service.create_version(
                CrawlerVersionSpec(
                    crawler_id=args.crawler_id,
                    version=args.version,
                    entrypoint=args.entrypoint,
                    parameter_schema=args.parameter_schema,
                    checkpoint_schema_version=args.checkpoint_schema_version,
                ),
                base_version=args.base_version,
            )
        elif args.command == "certify":
            value = await service.certify_version(args.crawler_id, args.version)
        elif args.command == "promote":
            value = service.promote_version(args.crawler_id, args.version)
        elif args.command == "rollback":
            value = service.rollback_version(args.crawler_id, args.version)
        else:
            value = await service.execute(
                CrawlerExecutionRequest(
                    crawler_id=args.crawler_id,
                    version=args.version,
                    ticker=args.ticker,
                    source_id=args.source_id,
                    binding_id=args.binding_id,
                    source_parameters=args.parameters,
                    poll_run_id=new_id("cli_poll"),
                    commit_checkpoint=False,
                )
            )
        payload: object
        if isinstance(value, BaseModel):
            payload = value.model_dump(mode="json")
        else:
            payload = [item.model_dump(mode="json") for item in value]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        await service.close()


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
