"""Run one real DeepSeek strict-schema probe for every M2/M3/M4 CDECR node shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from cdecr.config import CDECRSettings
from cdecr.cross_document_contracts import (
    AtomicDecisionBatch,
    PackageDecisionBatch,
    PackageMergeDecisionBatch,
    PackageMergeWireDecisionBatch,
    PackagePairDecisionBatch,
)
from cdecr.field_coreference_contracts import FieldCoreferenceModelOutput
from cdecr.models import DeepSeekStructuredModelClient, ModelAdapterError, ModelTier
from cdecr.ports import StructuredModelRequest
from cdecr.single_document_contracts import (
    DreamerModelOutput,
    GrounderModelOutput,
    JudgeCommandOutput,
)

PROBES: tuple[tuple[str, ModelTier, type[BaseModel]], ...] = (
    ("dreamer_m2", ModelTier.M2, DreamerModelOutput),
    ("dreamer_m3", ModelTier.M3, DreamerModelOutput),
    ("grounder", ModelTier.M3, GrounderModelOutput),
    ("field_coreference", ModelTier.M2, FieldCoreferenceModelOutput),
    ("atomic_assignment", ModelTier.M2, AtomicDecisionBatch),
    ("atomic_assignment_escalation", ModelTier.M3, AtomicDecisionBatch),
    ("package_assignment", ModelTier.M2, PackageDecisionBatch),
    ("package_pair_assignment_m2", ModelTier.M2, PackagePairDecisionBatch),
    ("package_pair_assignment_m3", ModelTier.M3, PackagePairDecisionBatch),
    ("package_merge_wire", ModelTier.M3, PackageMergeWireDecisionBatch),
    ("package_merge_legacy", ModelTier.M3, PackageMergeDecisionBatch),
    ("judge", ModelTier.M4, JudgeCommandOutput),
)


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    value: Any = root
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return cast(dict[str, Any], value)


def _sample(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    schema = _resolve(schema, root)
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list) and alternatives:
        non_null = [item for item in alternatives if item.get("type") != "null"]
        return _sample(non_null[0] if non_null else alternatives[0], root)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0])
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        return {key: _sample(value, root) for key, value in properties.items()}
    if kind == "array":
        count = max(0, int(schema.get("minItems", 0)))
        return [_sample(schema.get("items", {}), root) for _ in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    minimum = max(1, int(schema.get("minLength", 1)))
    return "x" * minimum


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", action="append", default=[])
    parser.add_argument(
        "--compare-efforts",
        action="store_true",
        help="Probe the same AtomicDecisionBatch request at low/high/max effort.",
    )
    args = parser.parse_args()
    settings = CDECRSettings()
    results: list[dict[str, object]] = []
    clients = {
        ModelTier.M2: DeepSeekStructuredModelClient(
            tier=ModelTier.M2,
            api_key=settings.require_deepseek(),
            base_url=settings.deepseek_base_url,
            model=settings.model_m2,
            reasoning_effort=settings.model_m2_reasoning_effort,
            strict=settings.model_m2_strict,
            timeout_seconds=settings.model_timeout_seconds,
        ),
        ModelTier.M3: DeepSeekStructuredModelClient(
            tier=ModelTier.M3,
            api_key=settings.require_deepseek(),
            base_url=settings.deepseek_base_url,
            model=settings.model_m3,
            reasoning_effort=settings.model_m3_reasoning_effort,
            strict=settings.model_m3_strict,
            timeout_seconds=settings.model_timeout_seconds,
        ),
        ModelTier.M4: DeepSeekStructuredModelClient(
            tier=ModelTier.M4,
            api_key=settings.require_deepseek(),
            base_url=settings.deepseek_base_url,
            model=settings.model_m4,
            reasoning_effort=settings.model_m4_reasoning_effort,
            strict=settings.model_m4_strict,
            timeout_seconds=settings.model_timeout_seconds,
        ),
    }
    selected = [probe for probe in PROBES if not args.stage or probe[0] in args.stage]
    if args.compare_efforts:
        selected = [
            ("atomic_assignment_effort_low", ModelTier.M2, AtomicDecisionBatch),
            ("atomic_assignment_effort_high", ModelTier.M3, AtomicDecisionBatch),
            ("atomic_assignment_effort_max", ModelTier.M4, AtomicDecisionBatch),
        ]
    for stage, tier, output_type in selected:
        schema = output_type.model_json_schema()
        sample = _sample(schema, schema)
        if output_type is DreamerModelOutput:
            sample = {
                "candidates": [
                    {
                        "statement": "Company reported results.",
                        "evidence_locations": [{"segment_id": "text:0", "text": "results"}],
                    }
                ]
            }
        if output_type is GrounderModelOutput:
            sample = {
                "drafts": [
                    {
                        "source_candidate_ids": ["c1"],
                        "mention": {
                            "evidence_locations": [
                                {"segment_id": "text:0", "text": "results"}
                            ],
                            "canonical_proposition": "Company reported results.",
                            "source_claim": None,
                            "event_family": "FINANCIAL_PERFORMANCE",
                            "predicate": {"raw": "reported", "normalized": "report_metric"},
                            "participants": [],
                            "locations": [],
                            "time": {
                                "event_start": None,
                                "event_end": None,
                                "precision": "UNKNOWN",
                                "reference_period_id": None,
                            },
                            "assertion_state": "ACTUAL",
                            "quantities": [],
                            "open_attributes": [],
                            "local_package_hint": None,
                        },
                    }
                ],
                "rejected_candidates": [],
                "issue_flags": [],
            }
        if output_type is FieldCoreferenceModelOutput:
            sample = {
                "decision": "UNRESOLVED",
                "canonical_id": None,
                "target_namespace": None,
            }
        started = perf_counter()
        try:
            result = clients[tier].complete(
                StructuredModelRequest(
                    system_prompt="You are a schema compatibility probe. Call the tool once.",
                    user_prompt=(
                        "Return this exact JSON value through the required tool: "
                        + json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
                    ),
                    json_schema=schema,
                )
            )
            output_type.model_validate(result.payload)
            row: dict[str, object] = {
                "stage": stage,
                "tier": tier.value,
                "schema": output_type.__name__,
                "ok": True,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "reasoning_tokens": result.reasoning_tokens,
                "visible_output_tokens": (
                    result.output_tokens - result.reasoning_tokens
                    if result.output_tokens is not None and result.reasoning_tokens is not None
                    else None
                ),
                "latency_ms": result.latency_ms,
            }
        except (ModelAdapterError, ValidationError, ValueError) as exc:
            row = {
                "stage": stage,
                "tier": tier.value,
                "schema": output_type.__name__,
                "ok": False,
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", "schema_validation_failed"),
                "status_code": getattr(exc, "status_code", None),
                "latency_ms": round((perf_counter() - started) * 1000),
            }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    payload = {
        "ok": all(bool(item["ok"]) for item in results),
        "provider": "deepseek_official",
        "model": "deepseek-v4-flash",
        "strict": True,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
