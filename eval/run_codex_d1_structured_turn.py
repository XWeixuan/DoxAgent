"""Run one minimal real file-first/progressive Codex D1 structured turn."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document1.attempt_bundle import (
    AttemptBundleSeeder,
    AttemptOutputValidator,
)
from doxagent.workflows.codex_document1.prompts import CodexD1PromptLoader
from doxagent.workflows.codex_document1.schema import NODE_OUTPUT_SCHEMA, NodeOutput


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low", choices=("low", "medium", "high"))
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = DoxAgentSettings()
    if not settings.codex_worker_bearer_token or not settings.codex_capability_secret:
        raise RuntimeError("persistent Codex worker bearer/capability secrets are required")
    run_id = args.run_id or f"codex-structured-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    attempt_id = "c2-structured-1"
    client = HttpCodexWorkerClient(
        settings.codex_worker_base_url,
        settings.codex_worker_bearer_token,
        capability_secret=settings.codex_capability_secret,
        poll_seconds=0.25,
    )
    try:
        horizontal = {
            "schema_version": "d1-horizontal-agent-input-v1",
            "program_values": [],
            "target_status": [],
            "optional_metrics": [],
            "candidate_format": {
                "metric_key": "string",
                "meaning": "string",
                "value": "string | number | boolean",
                "unit": "string | null",
                "as_of": "ISO date/datetime | null",
                "source_aliases": ["O#"],
                "method": "string",
                "confidence": "high | medium | low",
            },
            "freeform_metrics_allowed": True,
            "instructions": ["This is a functional file-protocol probe."],
        }
        seeder = AttemptBundleSeeder(
            client,
            Path("prompts/codex_v2/document1/compatibility/legacy_document1"),
        )
        seeded = await seeder.seed(
            run_id=run_id,
            node=CodexD1Node.C2,
            attempt_id=attempt_id,
            context_payload={
                "ticker": "TEST",
                "research_brief": (
                    "Functional protocol probe only. Do not call tools. Write one concise "
                    "sentence under each required section and no observation candidates."
                ),
            },
            horizontal=horizontal,
        )
        prompt = CodexD1PromptLoader().render(
            node=CodexD1Node.C2,
            attempt_id=attempt_id,
            task_path=seeded.task_path,
        )
        job = await client.run(
            WorkerRunRequest(
                run_id=run_id,
                ticker="TEST",
                node=CodexD1Node.C2,
                agent_role=CodexAgentRole.C2,
                attempt_id=attempt_id,
                prompt=prompt,
                output_schema=NODE_OUTPUT_SCHEMA,
                model=args.model,
                effort=args.effort,
                timeout_seconds=300,
                allow_subagents=False,
                max_subagents=0,
            )
        )
        if job.status != "succeeded" or not job.final_response:
            raise RuntimeError(job.error_message or "structured turn returned no response")
        output = NodeOutput.model_validate_json(job.final_response)
        await AttemptOutputValidator(client).validate(
            run_id=run_id,
            node=CodexD1Node.C2,
            seeded=seeded,
            output=output,
        )
        return {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "status": job.status,
            "input_sha256": seeded.input_sha256,
            "report_chars": len(output.report_markdown),
            "required_sections": list(seeded.required_sections),
            "progressive_output_valid": True,
        }
    finally:
        await client.aclose()


def main() -> None:
    print(json.dumps(asyncio.run(_run(_parser().parse_args())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
