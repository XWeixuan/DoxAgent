"""Run the frozen MU W1/W2 corpus against the real Bailian Runtime transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from doxagent.event_library.contracts import CanonicalAssertionState
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.providers import (
    Document3RuntimePolicyProvider,
    PublishedEventLibraryRuntimeProvider,
)
from doxagent.persistent_runtime_v2.repository import (
    InMemoryPersistentRuntimeV2Repository,
)
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCase,
    RuntimeFactCandidate,
    RuntimeSideEffect,
    SourceMessageEnvelope,
    SourceMessageSnapshot,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import BailianRuntimeResponsesClient
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document3.repository import (
    InMemoryDocument3PolicyRepository,
)
from doxagent.workflows.codex_document3.schema import PolicySet

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
EASTERN = ZoneInfo("America/New_York")
PRINT_LOCK = threading.Lock()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _seed_candidates(producer_case_id: str) -> list[RuntimeFactCandidate]:
    if producer_case_id == "MU-W12-013":
        return [
            RuntimeFactCandidate(
                proposition=(
                    "Orion Cloud completed production qualification of Micron's 245TB "
                    "QLC SSD for its approved storage environment."
                ),
                assertion_state=CanonicalAssertionState.ACTUAL,
                occurrence_date=date(2027, 2, 16),
                entities=["Orion Cloud", "Micron Technology"],
            ),
            RuntimeFactCandidate(
                proposition=(
                    "Orion Cloud accepted the first commercial batch of Micron 245TB "
                    "QLC SSDs for that qualified environment."
                ),
                assertion_state=CanonicalAssertionState.ACTUAL,
                occurrence_date=date(2027, 2, 16),
                entities=["Orion Cloud", "Micron Technology"],
            ),
        ]
    if producer_case_id == "MU-W12-018":
        return [
            RuntimeFactCandidate(
                proposition=(
                    "An unplanned cooling-water utility interruption affected a critical "
                    "Micron advanced-packaging area on November 12, 2026."
                ),
                assertion_state=CanonicalAssertionState.ACTUAL,
                occurrence_date=date(2026, 11, 12),
                entities=["Micron Technology"],
            ),
            RuntimeFactCandidate(
                proposition=(
                    "Micron reduced expected saleable-bit output for the affected packages "
                    "during the recovery period."
                ),
                assertion_state=CanonicalAssertionState.ACTUAL,
                subject_time="recovery period through 2027-01-31",
                entities=["Micron Technology"],
            ),
            RuntimeFactCandidate(
                proposition=(
                    "Micron revised delivery dates for customer commitments affected by the "
                    "advanced-packaging interruption."
                ),
                assertion_state=CanonicalAssertionState.ACTUAL,
                subject_time="deliveries scheduled before the end of December 2026",
                entities=["Micron Technology"],
            ),
            RuntimeFactCandidate(
                proposition=(
                    "Micron expects the affected advanced-packaging area to remain in recovery "
                    "through January 31, 2027."
                ),
                assertion_state=CanonicalAssertionState.EXPECTED,
                subject_time="through 2027-01-31",
                entities=["Micron Technology"],
            ),
        ]
    raise ValueError(f"No provisional seed definition for {producer_case_id}")


def _make_source(message: dict[str, Any]) -> SourceMessageEnvelope:
    published_at = datetime.fromisoformat(message["published_at"].replace("Z", "+00:00"))
    case_id = message["case_id"]
    return SourceMessageEnvelope(
        source_message_id=f"mu-w1-w2-real-{case_id}",
        source_id=f"eval-{message['source_kind'].lower()}",
        binding_id="eval-mu-future-v1",
        url=f"https://eval.invalid/mu/{case_id.lower()}",
        published_at=published_at,
        collected_at=published_at,
        message_bus_event_time=published_at,
        stream_item_id=f"stream-{case_id.lower()}",
        member_count=1,
        snapshot=SourceMessageSnapshot.model_validate(message["source_message"]),
    )


def _run_case(
    *,
    message: dict[str, Any],
    gold: dict[str, Any],
    output_path: Path,
    responses: BailianRuntimeResponsesClient,
    known_events: PublishedEventLibraryRuntimeProvider,
    policies: Document3RuntimePolicyProvider,
    prompts: RuntimeV2PromptSet,
    max_event_numeric_id: int,
) -> dict[str, Any]:
    if output_path.is_file():
        loaded = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Invalid saved Case result: {output_path}")
        return loaded

    case_id = message["case_id"]
    repository = InMemoryPersistentRuntimeV2Repository()
    source = _make_source(message)
    from doxagent.semantic_clock import semantic_day
    trading_date = semantic_day(source.message_bus_event_time)
    seeded_ids: list[str] = []
    dependency = gold["w1"]["r1"]["provisional_dependency"]
    if dependency is not None:
        producer = dependency["producer_case_id"]
        for index, candidate in enumerate(_seed_candidates(producer)):
            seeded = repository.allocate_provisional(
                ticker="MU",
                trading_date=trading_date,
                source_message_id=f"mu-w1-w2-real-{producer}",
                candidate_index=index,
                candidate=candidate,
                published_max_event_numeric_id=max_event_numeric_id,
            )
            seeded_ids.append(seeded.provisional_event_id)

    service = PersistentRuntimeV2Service(
        repository=repository,
        responses=responses,
        known_events=known_events,
        policies=policies,
        prompts=prompts,
        retry_delays_seconds=(5.0, 10.0),
        dispatch_effects=False,
    )
    started = datetime.now().astimezone()
    try:
        case = service.execute_message(source, admitted_at=source.message_bus_event_time)
        if case.route and RuntimeSideEffect.EMIT_DELTA in case.route.side_effects:
            effect = next(
                item
                for item in repository.list_effects(case.case_id)
                if item.effect_type is RuntimeSideEffect.EMIT_DELTA
            )
            service._execute_r3(case, effect)  # noqa: SLF001 - acceptance exercises exact runtime path
            case = repository.get_case(case.case_id) or case
        result = {
            "case_id": case_id,
            "status": "COMPLETED",
            "started_at": started.isoformat(),
            "completed_at": datetime.now().astimezone().isoformat(),
            "seeded_provisional_event_ids": seeded_ids,
            "prediction": {
                "w1_r1": case.w1_round1.model_dump(mode="json") if case.w1_round1 else None,
                "w1_final": case.w1_final.model_dump(mode="json") if case.w1_final else None,
                "w1_r3": case.w1_extraction.model_dump(mode="json") if case.w1_extraction else None,
                "w2_r1": case.w2_round1.model_dump(mode="json") if case.w2_round1 else None,
                "w2_final": case.w2_final.model_dump(mode="json") if case.w2_final else None,
                "route": case.route.model_dump(mode="json") if case.route else None,
            },
            "turns": [item.model_dump(mode="json") for item in repository.list_turns(case.case_id)],
        }
    except Exception as exc:  # keep every attempted Case auditable
        turns = []
        saved: RuntimeCase | None = repository.get_case_by_source(source.source_message_id)
        if saved is not None:
            turns = [
                item.model_dump(mode="json") for item in repository.list_turns(saved.case_id)
            ]
        result = {
            "case_id": case_id,
            "status": "FAILED",
            "started_at": started.isoformat(),
            "completed_at": datetime.now().astimezone().isoformat(),
            "seeded_provisional_event_ids": seeded_ids,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "turns": turns,
        }
    finally:
        service.close()

    _atomic_json(output_path, result)
    with PRINT_LOCK:
        print(f"{case_id}: {result['status']}", flush=True)
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _set_counts(predicted: list[str], required: list[str], allowed: list[str]) -> dict[str, Any]:
    predicted_set = set(predicted)
    required_set = set(required)
    allowed_set = set(allowed)
    true_positive = len(predicted_set & allowed_set)
    return {
        "predicted": predicted,
        "required": required,
        "allowed": allowed,
        "tp": true_positive,
        "fp": len(predicted_set - allowed_set),
        "fn": len(required_set - predicted_set),
        "precision": _ratio(true_positive, len(predicted_set)),
        "required_recall": _ratio(len(predicted_set & required_set), len(required_set)),
        "pass": required_set.issubset(predicted_set) and predicted_set.issubset(allowed_set),
    }


def _score(result: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    if result["status"] != "COMPLETED":
        return {"technical_pass": False, "reason": "case_execution_failed"}
    prediction = result["prediction"]
    required_outputs = ("w1_r1", "w1_final", "w2_r1", "w2_final", "route")
    missing_outputs = [name for name in required_outputs if prediction.get(name) is None]
    if missing_outputs:
        unavailable_turns = [
            {
                "lane": turn["lane"],
                "round_name": turn["round_name"],
                "error_code": turn.get("error_code"),
            }
            for turn in result["turns"]
            if turn["status"] == "UNAVAILABLE"
        ]
        return {
            "technical_pass": False,
            "reason": "required_runtime_outputs_missing",
            "missing_outputs": missing_outputs,
            "unavailable_turns": unavailable_turns,
        }
    seeded = result["seeded_provisional_event_ids"]
    w1_r1_gold = gold["w1"]["r1"]
    r1_required = list(w1_r1_gold["must_include_event_ids"])
    r1_allowed = [*r1_required, *w1_r1_gold["may_include_event_ids"]]
    if w1_r1_gold["provisional_dependency"] is not None:
        r1_required.extend(seeded)
        r1_allowed.extend(seeded)
    w1_r1 = _set_counts(prediction["w1_r1"]["event_ids"], r1_required, r1_allowed)

    w1_final_gold = gold["w1"]["final"]
    reference_gold = w1_final_gold["reference_expectation"]
    if reference_gold["mode"] == "static":
        reference_required = reference_gold["must_include"]
        reference_allowed = [*reference_required, *reference_gold["may_include"]]
    else:
        reference_required = seeded[: reference_gold["max_items"]]
        reference_allowed = seeded
    w1_reference = _set_counts(
        prediction["w1_final"]["reference_ids"],
        reference_required,
        reference_allowed,
    )
    w1_expected = w1_final_gold["expected_output"]
    w1_final = {
        "predicted_result": prediction["w1_final"]["result"],
        "expected_result": w1_expected["result"],
        "verdict_pass": prediction["w1_final"]["result"] == w1_expected["result"],
        "confidence_pass": prediction["w1_final"]["confidence"] == w1_expected["confidence"],
        "reference": w1_reference,
    }
    w1_final["pass"] = all(
        (w1_final["verdict_pass"], w1_final["confidence_pass"], w1_reference["pass"])
    )

    w2_r1_expected = gold["w2"]["r1"]["expected_output"]
    w2_r1_required = w2_r1_expected["policy_ids"]
    w2_r1_allowed = list(
        dict.fromkeys([*w2_r1_required, *gold["w2"].get("near_miss_policy_ids", [])])
    )
    w2_r1_predicted = prediction["w2_r1"].get(
        "candidate_policy_ids",
        prediction["w2_r1"].get("policy_ids", []),
    )
    w2_r1_policies = _set_counts(
        w2_r1_predicted,
        w2_r1_required,
        w2_r1_allowed,
    )
    w2_r1 = {
        "policies": w2_r1_policies,
        "order_pass": (
            w2_r1_predicted == w2_r1_required
            if gold["w2"]["r1"]["policy_order_is_significant"]
            else True
        ),
    }
    w2_r1["pass"] = w2_r1_policies["pass"] and w2_r1["order_pass"]

    w2_final_expected = gold["w2"]["final"]["expected_output"]
    w2_final_policies = _set_counts(
        prediction["w2_final"]["policy_ids"],
        w2_final_expected["policy_ids"],
        w2_final_expected["policy_ids"],
    )
    actual_conditions = prediction["w2_final"]["matched_condition_ids"]
    w2_final = {
        "policies": w2_final_policies,
        "confidence_pass": prediction["w2_final"]["confidence"]
        == w2_final_expected["confidence"],
        "conditions_pass": actual_conditions
        == w2_final_expected["matched_condition_ids"],
        "order_pass": (
            prediction["w2_final"]["policy_ids"] == w2_final_expected["policy_ids"]
            if gold["w2"]["final"]["policy_order_is_significant"]
            else True
        ),
    }
    w2_final["pass"] = all(
        (
            w2_final_policies["pass"],
            w2_final["confidence_pass"],
            w2_final["conditions_pass"],
            w2_final["order_pass"],
        )
    )
    predicted_activation_ids = (
        prediction["w2_final"]["policy_ids"]
        if prediction["w2_final"]["confidence"] == "normal"
        and prediction["w2_final"]["matched_condition_ids"]
        else []
    )
    expected_activation_ids = (
        w2_final_expected["policy_ids"]
        if w2_final_expected["confidence"] == "normal"
        and w2_final_expected["matched_condition_ids"]
        else []
    )
    w2_final["activations"] = _set_counts(
        predicted_activation_ids,
        expected_activation_ids,
        expected_activation_ids,
    )

    expected_r2_invocation = bool(w2_r1_predicted)
    actual_r2_turns = [
        turn for turn in result["turns"] if turn["lane"] == "W2" and turn["round_name"] == "R2"
    ]
    w2_r2 = {
        "expected": expected_r2_invocation,
        "invoked": bool(actual_r2_turns),
        "invocation_pass": bool(actual_r2_turns) == expected_r2_invocation,
    }
    if expected_r2_invocation and actual_r2_turns:
        expected_r2_output = w2_final_expected
        final_r2_output = actual_r2_turns[-1]["output"]
        w2_r2["output_pass"] = (
            final_r2_output is not None
            and final_r2_output["policy_ids"] == expected_r2_output["policy_ids"]
            and final_r2_output["confidence"] == expected_r2_output["confidence"]
            and final_r2_output["matched_condition_ids"]
            == expected_r2_output["matched_condition_ids"]
        )
    else:
        w2_r2["output_pass"] = not expected_r2_invocation and not actual_r2_turns
    w2_r2["pass"] = w2_r2["invocation_pass"] and w2_r2["output_pass"]

    expected_r3 = gold["w1"]["r3"]["expected_execution"]
    actual_r3 = prediction["w1_r3"] is not None
    w1_r3 = {
        "expected": expected_r3,
        "invoked": actual_r3,
        "invocation_pass": expected_r3 == actual_r3,
        "semantic_review_required": actual_r3,
    }

    expected_route = gold["diagnostic_route"]
    route = {
        "primary_pass": prediction["route"]["primary_route"]
        == expected_route["primary_route"],
        "side_effects_pass": prediction["route"]["side_effects"]
        == expected_route["side_effects"],
    }
    route["pass"] = route["primary_pass"] and route["side_effects_pass"]
    return {
        "technical_pass": True,
        "w1_r1": w1_r1,
        "w1_r2": w1_final,
        "w1_r3": w1_r3,
        "w2_r1": w2_r1,
        "w2_r2": w2_r2,
        "w2_final": w2_final,
        "route": route,
    }


def _sum_set_counts(scored: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any]:
    values = []
    for row in scored:
        value: Any = row
        for key in path:
            value = value[key]
        values.append(value)
    tp = sum(item["tp"] for item in values)
    fp = sum(item["fp"] for item in values)
    fn = sum(item["fn"] for item in values)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "exact_cases": sum(item["pass"] for item in values),
        "cases": len(values),
    }


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_completed = [
        item for item in records if item["result"]["status"] == "COMPLETED"
    ]
    completed = [item for item in records if item["score"]["technical_pass"]]
    scored = [item["score"] for item in completed]
    turns = [turn for item in records for turn in item["result"]["turns"]]
    novelty_pairs = [
        (item["w1_r2"]["expected_result"], item["w1_r2"]["predicted_result"])
        for item in scored
    ]

    def novelty_class(label: str) -> dict[str, Any]:
        tp = sum(expected == label and predicted == label for expected, predicted in novelty_pairs)
        fp = sum(expected != label and predicted == label for expected, predicted in novelty_pairs)
        fn = sum(expected == label and predicted != label for expected, predicted in novelty_pairs)
        return {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
        }

    return {
        "cases": len(records),
        "runtime_completed": len(runtime_completed),
        "completed": len(completed),
        "failed": len(records) - len(completed),
        "strict_output_cases": len(completed),
        "rounds": {
            "w1_r1_event_recall": _sum_set_counts(scored, ("w1_r1",)),
            "w1_r2": {
                "novelty": {"NEW": novelty_class("NEW"), "OLD": novelty_class("OLD")},
                "verdict_accuracy": _ratio(
                    sum(item["w1_r2"]["verdict_pass"] for item in scored), len(scored)
                ),
                "confidence_accuracy": _ratio(
                    sum(item["w1_r2"]["confidence_pass"] for item in scored), len(scored)
                ),
                "reference": _sum_set_counts(scored, ("w1_r2", "reference")),
                "exact_cases": sum(item["w1_r2"]["pass"] for item in scored),
            },
            "w1_r3": {
                "expected_invocations": sum(item["w1_r3"]["expected"] for item in scored),
                "actual_invocations": sum(item["w1_r3"]["invoked"] for item in scored),
                "invocation_exact_cases": sum(
                    item["w1_r3"]["invocation_pass"] for item in scored
                ),
                "semantic_review_pending": sum(
                    item["w1_r3"]["semantic_review_required"] for item in scored
                ),
            },
            "w2_r1": {
                "candidate_policy": _sum_set_counts(scored, ("w2_r1", "policies")),
                "exact_cases": sum(item["w2_r1"]["pass"] for item in scored),
            },
            "w2_r2": {
                "expected_invocations": sum(item["w2_r2"]["expected"] for item in scored),
                "actual_invocations": sum(item["w2_r2"]["invoked"] for item in scored),
                "invocation_exact_cases": sum(
                    item["w2_r2"]["invocation_pass"] for item in scored
                ),
                "output_exact_expected_cases": sum(
                    item["w2_r2"]["expected"] and item["w2_r2"]["output_pass"]
                    for item in scored
                ),
            },
            "w2_final": {
                "candidate_policy": _sum_set_counts(scored, ("w2_final", "policies")),
                "activation_policy": _sum_set_counts(
                    scored, ("w2_final", "activations")
                ),
                "confidence_accuracy": _ratio(
                    sum(item["w2_final"]["confidence_pass"] for item in scored), len(scored)
                ),
                "condition_accuracy": _ratio(
                    sum(item["w2_final"]["conditions_pass"] for item in scored), len(scored)
                ),
                "exact_cases": sum(item["w2_final"]["pass"] for item in scored),
            },
            "route": {
                "exact_cases": sum(item["route"]["pass"] for item in scored),
                "cases": len(scored),
            },
        },
        "usage": {
            "turns": len(turns),
            "retry_turns": sum(turn["attempt_number"] > 1 for turn in turns),
            "input_tokens": sum(turn.get("input_tokens") or 0 for turn in turns),
            "cached_input_tokens": sum(turn.get("cached_input_tokens") or 0 for turn in turns),
            "output_tokens": sum(turn.get("output_tokens") or 0 for turn in turns),
            "reasoning_tokens": sum(turn.get("reasoning_tokens") or 0 for turn in turns),
            "latency_ms_sum": sum(turn["latency_ms"] for turn in turns),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--prompt-root",
        type=Path,
        help="Explicit prompt revision override; input corpus stays frozen",
    )
    parser.add_argument("--max-workers", type=int, default=5)
    parser.add_argument("--case-id", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.max_workers <= 5:
        raise ValueError("max-workers must be between 1 and 5")

    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    messages = _jsonl(ROOT / "messages.jsonl")
    gold_rows = {item["case_id"]: item for item in _jsonl(ROOT / "gold.jsonl")}
    if args.case_id:
        wanted = set(args.case_id)
        messages = [item for item in messages if item["case_id"] in wanted]
        missing = wanted - {item["case_id"] for item in messages}
        if missing:
            raise ValueError(f"Unknown case IDs: {sorted(missing)}")

    run_dir = ROOT / "runs" / args.run_id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    frozen_root = run_dir / "frozen_event_library"
    frozen_db = frozen_root / "US" / "MU" / "event_library.sqlite3"
    frozen_db.parent.mkdir(parents=True, exist_ok=True)
    inputs = {item["purpose"]: item for item in manifest["frozen_inputs"]}
    for item in manifest["frozen_inputs"]:
        if args.prompt_root and item["purpose"].startswith("prompt "):
            continue
        source_path = Path(item["path"])
        if not source_path.is_file() or _sha256(source_path) != item["sha256"]:
            raise ValueError(f"Frozen input hash mismatch: {item['purpose']}")
    source_db = Path(inputs["W1 canonical Event Detail store"]["path"])
    if not frozen_db.exists():
        shutil.copy2(source_db, frozen_db)
    if _sha256(frozen_db) != inputs["W1 canonical Event Detail store"]["sha256"]:
        raise ValueError("Frozen Event Library copy hash mismatch")

    policy_path = Path(inputs["PolicySet source"]["path"])
    policy_set = PolicySet.model_validate_json(policy_path.read_text(encoding="utf-8"))
    policy_repository = InMemoryDocument3PolicyRepository()
    policy_repository.publish(policy_set, expected_base_version=None)
    policies = Document3RuntimePolicyProvider(policy_repository, projection_ttl_seconds=0)
    event_reader = PublishedEventLibraryReader(frozen_root)
    known_events = PublishedEventLibraryRuntimeProvider(event_reader)
    index = known_events.current_index("MU")
    index_source = Path(inputs["W1 candidate-recall index"]["path"])
    if index is None or index.known_event_index != index_source.read_text(encoding="utf-8"):
        raise ValueError("Compiled Known Event Index does not match the frozen manifest")
    max_event_numeric_id = known_events.max_event_numeric_id("MU", index.version)
    if max_event_numeric_id is None:
        raise ValueError("Frozen Event Library maximum Event ID is unavailable")

    settings = DoxAgentSettings()
    responses = BailianRuntimeResponsesClient(
        api_key=settings.require_dashscope_api_key(),
        base_url=settings.dashscope_chat_base_url,
        model=settings.persistent_runtime_v2_model,
        reasoning_effort=settings.persistent_runtime_v2_reasoning_effort,
        timeout_seconds=settings.persistent_runtime_v2_timeout_seconds,
        session_cache=settings.persistent_runtime_v2_session_cache_enabled,
    )
    prompt_root = args.prompt_root or PROJECT / "prompts" / "persistent_runtime_v2"
    prompts = RuntimeV2PromptSet.load(prompt_root)
    prompt_hashes = {name: _sha256(prompt_root / f"{name}.md") for name in vars(prompts)}
    prior_manifest = run_dir / "run_manifest.json"
    if prior_manifest.exists():
        prior = json.loads(prior_manifest.read_text(encoding="utf-8"))
        if prior.get("prompt_hashes") != prompt_hashes:
            raise ValueError("existing evaluation prompt revision differs; use a new run ID")
    saved_case_starts = [
        json.loads(path.read_text(encoding="utf-8"))["started_at"]
        for case_id in (item["case_id"] for item in messages)
        if (path := cases_dir / f"{case_id}.json").is_file()
    ]
    run_manifest = {
        "run_id": args.run_id,
        "dataset_id": manifest["dataset_id"],
        "prompt_hashes": prompt_hashes,
        "prompt_override": str(args.prompt_root) if args.prompt_root else None,
        "evaluation_profile": "w1_w2_round_isolated",
        "started_at": min(saved_case_starts, default=datetime.now().astimezone().isoformat()),
        "model": responses.model,
        "provider": responses.provider,
        "reasoning_effort": responses.reasoning_effort,
        "strict_json_schema": True,
        "session_cache": responses.session_cache,
        "cache_mode": "implicit_prefix" if not responses.session_cache else "session",
        "cache_layout": "readonly_business_reference_then_dynamic_case",
        "responses_endpoint_profile": "compatible",
        "max_workers": args.max_workers,
        "case_ids": [item["case_id"] for item in messages],
        "dataset_manifest_sha256": _sha256(manifest_path),
        "messages_sha256": _sha256(ROOT / "messages.jsonl"),
        "gold_sha256": _sha256(ROOT / "gold.jsonl"),
        "frozen_event_library_sha256": _sha256(frozen_db),
        "policy_set_sha256": _sha256(policy_path),
    }
    _atomic_json(run_dir / "run_manifest.json", run_manifest)

    futures = {}
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        for message in messages:
            case_id = message["case_id"]
            futures[
                executor.submit(
                    _run_case,
                    message=message,
                    gold=gold_rows[case_id],
                    output_path=cases_dir / f"{case_id}.json",
                    responses=responses,
                    known_events=known_events,
                    policies=policies,
                    prompts=prompts,
                    max_event_numeric_id=max_event_numeric_id,
                )
            ] = case_id
        results_by_id = {futures[future]: future.result() for future in as_completed(futures)}

    records = []
    for message in messages:
        case_id = message["case_id"]
        result = results_by_id[case_id]
        records.append(
            {"case_id": case_id, "result": result, "score": _score(result, gold_rows[case_id])}
        )
    predictions_path = run_dir / "predictions.jsonl"
    predictions_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            for item in records
        ),
        encoding="utf-8",
    )
    metrics = _aggregate(records)
    metrics["run_id"] = args.run_id
    metrics["model"] = responses.model
    metrics["provider"] = responses.provider
    metrics["reasoning_effort"] = responses.reasoning_effort
    metrics["completed_at"] = max(item["result"]["completed_at"] for item in records)
    metrics["predictions_sha256"] = _sha256(predictions_path)
    _atomic_json(run_dir / "metrics.json", metrics)
    run_manifest["completed_at"] = metrics["completed_at"]
    run_manifest["predictions_sha256"] = metrics["predictions_sha256"]
    run_manifest["metrics_sha256"] = _sha256(run_dir / "metrics.json")
    _atomic_json(run_dir / "run_manifest.json", run_manifest)
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 0 if metrics["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
