"""Real-provider cache acceptance for W1/W2 rounds beyond R1."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.providers import (
    Document3RuntimePolicyProvider,
    PublishedEventLibraryRuntimeProvider,
)
from doxagent.persistent_runtime_v2.schema import (
    W1FactExtractionResult,
    W1NoveltyResult,
    W2PolicyResult,
)
from doxagent.persistent_runtime_v2.service import (
    _w1_canonical_event_business_payload,
    _w2_detail_business_payload,
)
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesRequest,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.repository import InMemoryDocument3PolicyRepository
from doxagent.workflows.codex_document3.schema import PolicySet

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _inputs() -> dict[str, dict[str, Any]]:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    return {item["purpose"]: item for item in manifest["frozen_inputs"]}


def _event_details(event_ids: list[str]) -> dict[str, Any]:
    inputs = _inputs()
    cache_root = ROOT / "cache_ab" / "frozen_event_library"
    target = cache_root / "US" / "MU" / "event_library.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(Path(inputs["W1 canonical Event Detail store"]["path"]), target)
    provider = PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(cache_root))
    snapshot = provider.details("MU", 1, event_ids)
    if snapshot is None or snapshot.missing_event_ids:
        raise ValueError("Event Detail fixture is unavailable")
    return {
        "canonical_events": [
            _w1_canonical_event_business_payload(event) for event in snapshot.events
        ],
        "provisional_events": [],
    }


def _policy_details(policy_ids: list[str]) -> dict[str, Any]:
    inputs = _inputs()
    policy = PolicySet.model_validate_json(
        Path(inputs["PolicySet source"]["path"]).read_text(encoding="utf-8")
    )
    repository = InMemoryDocument3PolicyRepository()
    repository.publish(policy, expected_base_version=None)
    provider = Document3RuntimePolicyProvider(repository, projection_ttl_seconds=0)
    details = provider.details("MU", 1, policy_ids)
    if details.missing_policy_ids:
        raise ValueError("Policy Detail fixture is unavailable")
    return _w2_detail_business_payload(details)


def _round_spec(round_name: str, prompts: RuntimeV2PromptSet) -> dict[str, Any]:
    if round_name == "W1_R2":
        return {
            "prompt": prompts.w1_r2,
            "output_model": W1NoveltyResult,
            "schema_name": "w1_novelty_result",
            "context_key": "event_details",
            "context": _event_details(["E14"]),
        }
    if round_name == "W2_R2":
        return {
            "prompt": prompts.w2_r2,
            "output_model": W2PolicyResult,
            "schema_name": "w2_policy_result",
            "context_key": "policy_details",
            "context": _policy_details(["pol_e28a1b1e8eedc9bf53f5"]),
        }
    return {
        "prompt": (
            f"{prompts.w1_r3}\n\n## Current Capture Mode\n\n"
            "The runtime-selected capture mode for this turn is `NEW_CAPTURE`."
        ),
        "output_model": W1FactExtractionResult,
        "schema_name": "w1_fact_extraction_result",
        "context_key": "source_message",
        "context": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", choices=("W1_R2", "W2_R2", "W1_R3"), required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    settings = DoxAgentSettings()
    prompts = RuntimeV2PromptSet.load(PROJECT / "prompts" / "persistent_runtime_v2")
    spec = _round_spec(args.round, prompts)
    messages = {item["case_id"]: item for item in _jsonl(ROOT / "messages.jsonl")}
    client = BailianRuntimeResponsesClient(
        api_key=settings.require_dashscope_api_key(),
        base_url=settings.dashscope_chat_base_url,
        model=settings.persistent_runtime_v2_model,
        reasoning_effort=settings.persistent_runtime_v2_reasoning_effort,
        timeout_seconds=settings.persistent_runtime_v2_timeout_seconds,
        session_cache=False,
    )
    records = []
    for case_id in args.case_id:
        source = messages[case_id]["source_message"]
        if args.round == "W1_R3":
            payload = {
                "source_message": source,
                "w1_final": {
                    "result": "NEW",
                    "confidence": "normal",
                    "reference_ids": [],
                    "reason": "Cache acceptance fixture for an unrecorded core fact.",
                },
            }
        else:
            payload = {
                "source_message": source,
                spec["context_key"]: spec["context"],
            }
        result = client.complete(
            RuntimeResponsesRequest(
                instructions=prompts.instructions(spec["prompt"]),
                payload=payload,
                output_model=spec["output_model"],
                schema_name=spec["schema_name"],
                cache_context_keys=(spec["context_key"],),
            )
        )
        record = {
            "case_id": case_id,
            "response_id": result.response_id,
            "input_tokens": result.input_tokens,
            "cached_input_tokens": result.cached_input_tokens,
            "latency_ms": result.latency_ms,
            "prefix_fingerprint": result.prefix_fingerprint,
        }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    total_input = sum(item["input_tokens"] or 0 for item in records)
    total_cached = sum(item["cached_input_tokens"] or 0 for item in records)
    output = {
        "run_id": args.run_id,
        "round": args.round,
        "transport": "responses_json_schema",
        "session_cache": False,
        "case_ids": args.case_id,
        "records": records,
        "totals": {
            "input_tokens": total_input,
            "cached_input_tokens": total_cached,
            "cache_share": total_cached / total_input if total_input else None,
        },
    }
    output_path = ROOT / "cache_ab" / f"{args.run_id}.json"
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output["totals"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
