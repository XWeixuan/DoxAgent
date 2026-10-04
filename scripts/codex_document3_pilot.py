"""Evaluate a completed real D3 workspace without mutating business artifacts."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.config import CodexRuntimeConfig
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.pilot import Document3PilotEvaluator


async def _run(run_id: str, output: Path | None, orchestration_version: str = "v2") -> int:
    config = CodexRuntimeConfig.from_settings(DoxAgentSettings())
    if not config.worker_bearer_token or not config.capability_secret:
        raise ValueError("Codex worker credentials are required")
    client = HttpCodexWorkerClient(
        str(config.worker_base_url),
        config.worker_bearer_token,
        capability_secret=config.capability_secret,
    )
    try:
        report = await Document3PilotEvaluator(client).evaluate(
            run_id, orchestration_version=orchestration_version
        )
    finally:
        await client.aclose()
    payload = report.model_dump_json(indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--orchestration-version", choices=["v2", "v2.1"], default="v2")
    args = parser.parse_args()
    return asyncio.run(_run(args.run_id, args.output, args.orchestration_version))


if __name__ == "__main__":
    raise SystemExit(main())
