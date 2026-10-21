"""Ten unique frozen W2R1 cases, native Vertex adapter, serial cache validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from doxagent.persistent_runtime_v2.openrouter import build_w2_r1_cached_request
from doxagent.persistent_runtime_v2.schema import W2Round1RecallResult
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError, RuntimeResponsesRequest
from doxagent.persistent_runtime_v2.vertex import VertexClaudeClient, vertex_body

ROOT = Path(__file__).parent
SOURCE = ROOT.parent / "w2_r1_model_low_20261016/dataset.json"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def sha(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def request(case, project):
    frozen = case["frozen_round"]
    return RuntimeResponsesRequest(
        instructions=frozen["instructions"],
        payload=frozen["payload"],
        cache_context_keys=tuple(frozen["cache_context_keys"]),
        output_model=W2Round1RecallResult,
        schema_name="w2_round1_recall_result",
        model="claude-haiku-5-5",
        reasoning_effort="high",
        timeout_seconds=180,
        provider="vertex",
        provider_endpoint="global",
        cache_ttl="1h",
        metadata={"lane": "W2", "round": "R1", "vertex_project": project},
    )


def prepare():
    data = json.loads(SOURCE.read_text(encoding="utf8"))
    # Five recalled + five empty, retaining naturally shared prefixes to test
    # warming with different messages rather than repeating the same case.
    by_ordinal = {c["ordinal"]: c for c in data["cases"]}
    selected = [by_ordinal[n] for n in (1, 16, 17, 9, 2, 10, 5, 13, 14, 15)]
    assert len({c["case_id"] for c in selected}) == 10
    # Group identical frozen prefix versions, never substitute another case's policies.
    selected.sort(
        key=lambda c: (
            c["ticker"],
            c["instruction_sha256"],
            sha(c["frozen_round"]["payload"]["runtime_policy_projection"]),
            c["ordinal"],
        )
    )
    for i, case in enumerate(selected, 1):
        case["eval_ordinal"] = i
        req = request(case, "")
        original, fingerprint = build_w2_r1_cached_request(req)
        native, other = vertex_body(req)
        assert fingerprint == other
        assert native["system"] == original["messages"][0]["content"]
        assert native["messages"] == original["messages"][1:]
        case["native_request"] = native
        case["prefix_fingerprint"] = fingerprint
    write(
        ROOT / "dataset.json",
        dict(
            source=str(SOURCE.relative_to(ROOT.parent.parent)),
            source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            groups={"RECALLED": 5, "EMPTY": 5},
            cases=selected,
        ),
    )
    return selected


def summarize(records, total):
    valid = [r for r in records if r["status"] == "OK"]
    usage = [r for r in records if r.get("input_tokens") is not None]
    incoming = sum(r["input_tokens"] for r in usage)
    readable = [r for r in usage if r.get("cached_input_tokens") is not None]
    reads = sum(r["cached_input_tokens"] for r in readable)
    return dict(
        requested_cases=total,
        attempted=len(records),
        successful=len(valid),
        unattempted=total - len(records),
        usage_observed_cases=len(usage),
        observable_input_tokens=incoming if usage else None,
        observable_cache_read_tokens=reads if readable else None,
        weighted_cache_read_ratio=reads / incoming
        if incoming and len(readable) == len(usage)
        else None,
        cache_write_tokens=sum(
            r["cache_write_input_tokens"]
            for r in usage
            if r.get("cache_write_input_tokens") is not None
        )
        if any(r.get("cache_write_input_tokens") is not None for r in usage)
        else None,
        average_seconds=sum(r["wall_seconds"] for r in valid) / len(valid) if valid else None,
        recall_match_historical=sum(
            r.get("candidate_set_matches_historical") is True for r in valid
        ),
        rows=records,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--project", default=os.environ.get("DOXAGENT_VERTEX_PROJECT", ""))
    parser.add_argument("--auth", choices=["api-key", "adc"], default="api-key")
    parser.add_argument("--label", default="api-key")
    args = parser.parse_args()
    cohort = prepare()
    if args.prepare:
        print("Prepared 10 unique cases: 5 RECALLED + 5 EMPTY; full-policy prefixes preserved")
        return
    if not args.project:
        parser.error(
            "Google Cloud Project ID is required; no projectless Claude endpoint is assumed"
        )
    key = os.environ["DOXAGENT_VERTEX_API_KEY"] if args.auth == "api-key" else None
    client = VertexClaudeClient(args.project, api_key=key)
    out = ROOT / args.label
    write(
        out / "config.json",
        dict(
            provider="vertex",
            model="claude-haiku-5-5",
            endpoint="global",
            project=args.project,
            auth=args.auth,
            effort="high",
            ttl="1h",
            max_tokens=16384,
            timeout_seconds=180,
            concurrency=1,
            automatic_retries=0,
            created_at=datetime.now(UTC).isoformat(),
            no_production_writes=True,
        ),
    )
    records = []
    try:
        for case in cohort:
            target = out / f"{case['eval_ordinal']:02}.json"
            if target.exists():
                record = json.loads(target.read_text(encoding="utf8"))
                records.append(record)
                if record["status"] != "OK":
                    break
                continue
            started = perf_counter()
            record = dict(
                ordinal=case["eval_ordinal"],
                source_ordinal=case["ordinal"],
                case_id=case["case_id"],
                ticker=case["ticker"],
                group=case["group"],
                prefix_fingerprint=case["prefix_fingerprint"],
                status="ERROR",
            )
            try:
                result = client.complete(request(case, args.project))
                record.update({k: v for k, v in asdict(result).items() if k != "value"})
                record["output"] = result.value.model_dump(mode="json")
                allowed = {
                    p["policy_id"]
                    for p in case["frozen_round"]["payload"]["runtime_policy_projection"][
                        "policies"
                    ]
                }
                assert set(result.value.candidate_policy_ids) <= allowed, (
                    "Policy ID outside frozen projection"
                )
                record["candidate_set_matches_historical"] = set(
                    result.value.candidate_policy_ids
                ) == set(case["historical_ids"])
                record["status"] = "OK"
            except RuntimeResponsesError as exc:
                record.update(
                    error_code=exc.code, error=str(exc), retryable=exc.retryable, **exc.usage
                )
            except Exception as exc:
                record.update(
                    error_code="evaluation_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    retryable=False,
                )
            finally:
                record["wall_seconds"] = perf_counter() - started
                write(target, record)
                records.append(record)
                write(out / "summary.json", summarize(records, len(cohort)))
                print(
                    json.dumps(
                        {
                            k: record.get(k)
                            for k in [
                                "ordinal",
                                "ticker",
                                "status",
                                "error",
                                "input_tokens",
                                "cached_input_tokens",
                                "cache_write_input_tokens",
                                "wall_seconds",
                            ]
                        }
                    ),
                    flush=True,
                )
            # Do not issue nine identical authentication/permission failures.
            if record["status"] != "OK" and not record.get("retryable", True):
                break
    finally:
        client.client.close()


if __name__ == "__main__":
    main()
