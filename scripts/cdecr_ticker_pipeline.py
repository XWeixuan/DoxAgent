"""Initialize or inspect one durable ticker CDECR -> Canonical Event Library pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from doxagent.cdecr_integration.contracts import RuntimeNovelMessageBatch
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.cdecr_integration.historical_loader import (
    BenzingaHistoricalNewsProvider,
    FinnhubHistoricalNewsProvider,
    HistoricalNewsProvider,
)
from doxagent.cdecr_integration.runtime_factory import build_cdecr_workflow_runner
from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.event_library.service import EventLibraryService
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_event_library.remote_runner import (
    RemoteEventLibraryInitializer,
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-root", type=Path, default=Path(".tmp/cdecr-tickers/runtime")
    )
    parser.add_argument("--state-root", type=Path, default=Path(".tmp/cdecr-tickers/state"))
    parser.add_argument(
        "--event-library-root",
        type=Path,
        default=Path(".tmp/cdecr-tickers/event-library"),
    )
    parser.add_argument(
        "--local-workspace-root",
        type=Path,
        default=Path(".tmp/cdecr-tickers/o2-local"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--market", required=True)
    initialize.add_argument("--ticker", required=True)
    initialize.add_argument("--as-of", type=_timestamp, required=True)
    initialize.add_argument("--export-dir", type=Path, required=True)
    initialize.add_argument("--prepare-only", action="store_true")
    initialize.add_argument("--max-sources", type=int, default=500)
    initialize.add_argument("--sample-seed", type=int, default=20260824)
    initialize.add_argument("--wave-size", type=int, default=30)
    update = subparsers.add_parser("update")
    update.add_argument("--market", required=True)
    update.add_argument("--ticker", required=True)
    update.add_argument(
        "--runtime-novel-batch",
        type=Path,
        required=True,
        help="Frozen RuntimeNovelMessageBatch JSON test artifact.",
    )
    update.add_argument(
        "--export-dir", type=Path, default=Path(".tmp/cdecr-tickers/published")
    )
    update.add_argument("--prepare-only", action="store_true")
    update.add_argument("--wave-size", type=int, default=30)
    status = subparsers.add_parser("status")
    status.add_argument("--market", required=True)
    status.add_argument("--ticker", required=True)
    return parser


async def _main() -> int:
    args = _parser().parse_args()
    settings = DoxAgentSettings()
    providers: list[HistoricalNewsProvider] = []
    if settings.finnhub_api_key:
        providers.append(FinnhubHistoricalNewsProvider(settings))
    if settings.benzinga_api_key:
        providers.append(BenzingaHistoricalNewsProvider(settings))
    worker: HttpCodexWorkerClient | None = None
    if args.command in {"initialize", "update"} and not args.prepare_only:
        if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
            raise RuntimeError("Codex Worker bearer token and capability secret are required")
        worker = HttpCodexWorkerClient(
            settings.codex_worker_base_url,
            settings.codex_worker_bearer_token,
            capability_secret=settings.codex_capability_secret,
        )

    def o2_factory(service: EventLibraryService) -> RemoteEventLibraryInitializer:
        if worker is None:
            raise RuntimeError("Codex Worker is not configured")
        return RemoteEventLibraryInitializer(
            worker=worker,
            workspace=worker,
            service=service,
            local_workspace_root=args.local_workspace_root,
            model=settings.codex_model,
            model_provider=settings.codex_model_provider,
            effort=settings.codex_reasoning_effort,
            timeout_seconds=settings.codex_node_timeout_seconds,
            wave_size=args.wave_size,
        )

    coordinator = TickerCDECRPipelineCoordinator(
        registry_root=args.registry_root,
        state_root=args.state_root,
        event_library_root=args.event_library_root,
        providers=providers,
        runtime_factory=lambda binding: build_cdecr_workflow_runner(binding),
        o2_factory=o2_factory if worker is not None else None,
        sample_seed=getattr(args, "sample_seed", 20260824),
        max_sources=getattr(args, "max_sources", 500),
    )
    try:
        if args.command == "status":
            state = coordinator.status(market=args.market, ticker=args.ticker)
            print("null" if state is None else state.model_dump_json(indent=2))
            return 0
        if args.command == "update":
            batch = RuntimeNovelMessageBatch.model_validate_json(
                args.runtime_novel_batch.read_text(encoding="utf-8")
            )
            if batch.market != args.market.upper() or batch.ticker != args.ticker.upper():
                raise ValueError("CLI market/ticker do not match RuntimeNovelMessageBatch")
            result = await coordinator.update(
                batch=batch,
                export_dir=args.export_dir,
                run_o2=not args.prepare_only,
            )
            print(
                json.dumps(
                    result.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
        result = await coordinator.initialize(
            market=args.market,
            ticker=args.ticker,
            as_of=args.as_of,
            export_dir=args.export_dir,
            run_o2=not args.prepare_only,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        if worker is not None:
            await worker.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
