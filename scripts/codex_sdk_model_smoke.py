"""Minimal real Codex SDK calls; contains no DoxAgent business inputs."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort

CASES = (
    ("gpt-6-luna", "max"),
    ("gpt-6.1-sol", "medium"),
    ("gpt-6.1-sol", "high"),
    ("gpt-6.1-sol", "max"),
)


async def probe() -> dict[str, object]:
    report: dict[str, object] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "sdk": importlib.metadata.version("openai-codex"),
        "cli": importlib.metadata.version("openai-codex-cli-bin"),
        "calls": [],
    }
    client = AsyncCodex(CodexConfig(client_name="doxagent-model-smoke"))
    try:
        account = await client.account()
        report["authenticated"] = account.account is not None
        report["account_type"] = type(getattr(account.account, "root", account.account)).__name__
        report["models"] = [model.id for model in (await client.models()).data]
        for model, effort in CASES:
            record: dict[str, object] = {"model": model, "effort": effort}
            try:
                thread = await client.thread_start(
                    ephemeral=True,
                    model=model,
                    cwd=tempfile.gettempdir(),
                    approval_mode=ApprovalMode.deny_all,
                    sandbox=Sandbox.read_only,
                    base_instructions="Return the requested marker. Do not use tools.",
                )
                result = await asyncio.wait_for(
                    thread.run(
                        'Return an object with marker equal to "OK".',
                        effort=ReasoningEffort(effort),
                        output_schema={
                            "type": "object",
                            "properties": {"marker": {"type": "string", "enum": ["OK"]}},
                            "required": ["marker"],
                            "additionalProperties": False,
                        },
                    ),
                    timeout=180,
                )
                record.update(
                    status=result.status.value,
                    thread_id=thread.id,
                    turn_id=result.id,
                    response=result.final_response,
                    passed=result.status.value == "completed"
                    and json.loads(result.final_response or "null") == {"marker": "OK"},
                )
            except Exception as exc:
                record.update(passed=False, error=str(exc))
            report["calls"].append(record)
            print(json.dumps(record), flush=True)
    finally:
        await client.close()
    report["passed"] = bool(report["authenticated"]) and all(
        call["passed"] for call in report["calls"]
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(probe())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
