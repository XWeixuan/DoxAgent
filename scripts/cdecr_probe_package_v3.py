"""Probe the three Package Workflow V3 Responses JSON-Schema contracts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from cdecr.cli import _package_v3_client
from cdecr.config import CDECRSettings
from cdecr.package_global_clustering import PackageWorkflowV3Service, _prompt
from cdecr.package_v3_contracts import (
    PackageV3DescriptionOutput,
    PackageV3InitialClusteringOutput,
    PackageV3RollingClusteringOutput,
)
from cdecr.ports import ResponsesModelClient


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _args()
    settings = CDECRSettings()
    if settings.model_m3_provider != "dashscope":
        raise ValueError("Package V3 requires the current DashScope/Bailian M3 profile")
    client = cast(ResponsesModelClient, _package_v3_client(settings))
    service = PackageWorkflowV3Service(
        registry=object(),  # type: ignore[arg-type]
        reasoning_effort=settings.model_m3_reasoning_effort,
        description_reasoning_effort=settings.model_m2_reasoning_effort,
    )
    probes = [
        (
            "package_v3_initial_clustering",
            service._model_request(
                prompt=_prompt("package_v3_initial_clustering.md"),
                payload={
                    "parent_occurrences": [
                        {
                            "occurrence_id": "PO-000001",
                            "parent_occurrence": "Micron fiscal Q3 earnings disclosure",
                        }
                    ]
                },
                schema=PackageV3InitialClusteringOutput.model_json_schema(),
                schema_name="package_v3_initial_clustering",
                stage="package_v3_initial_clustering",
                reasoning_effort=settings.model_m3_reasoning_effort,
            ),
        ),
        (
            "package_v3_rolling_clustering",
            service._model_request(
                prompt=_prompt("package_v3_rolling_clustering.md"),
                payload={
                    "registry": [
                        {
                            "mcp_id": "MCP-000001",
                            "canonical": "Micron fiscal Q3 earnings disclosure",
                            "description": "Micron reported its fiscal Q3 results.",
                        }
                    ],
                    "new_parent_occurrences": [
                        {
                            "occurrence_id": "PO-000002",
                            "parent_occurrence": "Micron Q3 earnings call commentary",
                        }
                    ],
                },
                schema=PackageV3RollingClusteringOutput.model_json_schema(),
                schema_name="package_v3_rolling_clustering",
                stage="package_v3_rolling_clustering",
                reasoning_effort=settings.model_m3_reasoning_effort,
            ),
        ),
        (
            "package_v3_description",
            service._model_request(
                prompt=_prompt("package_v3_registry_description.md"),
                payload={
                    "mcps": [
                        {
                            "mcp_id": "MCP-000001",
                            "canonical": "Micron fiscal Q3 earnings disclosure",
                            "parent_occurrences": [
                                {
                                    "occurrence_id": "PO-000001",
                                    "parent_occurrence": (
                                        "Micron fiscal Q3 earnings disclosure"
                                    ),
                                }
                            ],
                        }
                    ]
                },
                schema=PackageV3DescriptionOutput.model_json_schema(),
                schema_name="package_v3_registry_descriptions",
                stage="package_v3_description",
                reasoning_effort=settings.model_m2_reasoning_effort,
            ),
        ),
    ]
    results: list[dict[str, object]] = []
    for stage, request in probes:
        raw = client.complete_response(request)
        if stage == "package_v3_initial_clustering":
            parsed = PackageV3InitialClusteringOutput.model_validate(raw.payload)
            PackageWorkflowV3Service._validate_initial(parsed, {"PO-000001"})
        elif stage == "package_v3_rolling_clustering":
            parsed_rolling = PackageV3RollingClusteringOutput.model_validate(raw.payload)
            PackageWorkflowV3Service._validate_rolling(
                parsed_rolling,
                expected_occurrence_ids={"PO-000002"},
                active_mcp_ids={"MCP-000001"},
            )
        else:
            parsed_description = PackageV3DescriptionOutput.model_validate(raw.payload)
            PackageWorkflowV3Service._validate_descriptions(
                parsed_description, {"MCP-000001"}
            )
        results.append(
            {
                "stage": stage,
                "model": raw.model,
                "input_tokens": raw.input_tokens,
                "output_tokens": raw.output_tokens,
                "reasoning_tokens": raw.reasoning_tokens,
                "latency_ms": raw.latency_ms,
                "response_id": raw.response_id,
                "schema_valid": True,
            }
        )
    payload = {
        "report_version": "package-v3-provider-schema-probe-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "provider": "dashscope",
        "model": settings.model_m3,
        "reasoning_effort": {
            "clustering": settings.model_m3_reasoning_effort,
            "description": settings.model_m2_reasoning_effort,
        },
        "output_mode": "json_schema",
        "results": results,
        "passed": len(results) == 3,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
