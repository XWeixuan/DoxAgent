"""Offline receipts, costs, token estimates and BM25 evidence-retention experiment."""

import copy
import csv
import json
import math
import re
import statistics
from collections import Counter

from run_eval import ROOT, tokens, write

from doxagent.persistent_runtime_v2.w1_r2_context import compile_fact_view


def read(name):
    return json.loads((ROOT / "calls" / name).read_text(encoding="utf8"))


def describe(values):
    vals = sorted(values)
    return dict(
        total=sum(vals),
        mean=statistics.mean(vals),
        p50=statistics.median(vals),
        p95=vals[math.ceil(len(vals) * 0.95) - 1],
        maximum=max(vals),
    )


def receipt_summary(records, historical=False):
    def usage(record):
        return (
            dict(
                prompt_tokens=record["input_tokens"],
                completion_tokens=record["output_tokens"],
                prompt_tokens_details=dict(cached_tokens=record["cached_input_tokens"]),
            )
            if historical
            else record.get("usage")
        )

    observed = [usage(r) for r in records if usage(r)]
    inputs = sum(u["prompt_tokens"] for u in observed)
    outputs = sum(u["completion_tokens"] for u in observed)
    known_cache = [(u.get("prompt_tokens_details") or {}).get("cached_tokens") for u in observed]
    cached = sum(c for c in known_cache if c is not None)
    input_price, cache_price, output_price = (0.8, 0.1, 2.7) if historical else (2, 0.57, 7)
    price_observed = sum(
        (
            (u["prompt_tokens"] - c) * input_price
            + c * cache_price
            + u["completion_tokens"] * output_price
        )
        / 1e6
        for u, c in zip(observed, known_cache, strict=True)
        if c is not None
    )
    return dict(
        calls=len(records),
        usage_observed=len(observed),
        cache_observed=sum(c is not None for c in known_cache),
        input_tokens=inputs,
        cached_tokens=cached,
        output_tokens=outputs,
        cache_ratio=cached / inputs if inputs else None,
        price_observed_cny=price_observed,
        latency_seconds=describe(
            [r["latency_ms"] / 1000 if historical else r["wall_seconds"] for r in records]
        ),
    )


def bm25(case):
    details = copy.deepcopy(case["details"])
    event = next(e for e in details["canonical_events"] if e["event_id"] == "E4")
    stop = set(
        (
            "a an and are as at be by for from has in is it its of on or that the this "
            "to was were will with"
        ).split()
    )

    def lex(s):
        return [t for t in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", s.lower()) if t not in stop]

    documents = [lex(f["proposition"]) for f in event["facts"]]
    dfs = Counter(term for doc in documents for term in set(doc))
    count, average = len(documents), sum(map(len, documents)) / len(documents)
    query = case["requests"]["original"]["messages"][-1]["content"]
    # Query is current source only; do not leak the known-facts document into ranking.
    query = json.loads(query.split("# Current Case Input\n", 1)[1])["source_message"]
    terms = set(lex((query.get("title") or "") + "\n" + (query.get("body") or "")))
    scored = []
    for ordinal, (doc, fact) in enumerate(zip(documents, event["facts"], strict=True)):
        tf = Counter(doc)
        score = sum(
            math.log(1 + (count - dfs[t] + 0.5) / (dfs[t] + 0.5))
            * tf[t]
            * 2.2
            / (tf[t] + 1.2 * (0.25 + 0.75 * len(doc) / average))
            for t in terms
            if t in tf
        )
        scored.append(dict(fact_id=fact["fact_id"], score=score, ordinal=ordinal, fact=fact))
    ranked = sorted(scored, key=lambda r: (-r["score"], r["ordinal"]))
    # Human-selected coverage/state witnesses, independent of historical model attribution.
    witnesses = {
        "price_and_supply_background": {"F175", "F1239", "F1240"},
        "different_period_price_readings": {"F145", "F397", "F821", "F822"},
        "countervailing_or_update_state": {"F398", "F525", "F823"},
    }
    runs = []
    for limit in [16, 32, 64, len(documents)]:
        selected = {r["fact_id"] for r in ranked[:limit]}
        projected = copy.deepcopy(details)
        target = next(e for e in projected["canonical_events"] if e["event_id"] == "E4")
        target["facts"] = [f for f in target["facts"] if f["fact_id"] in selected]
        runs.append(
            dict(
                limit=limit,
                fact_view_tokens=tokens(compile_fact_view(projected)),
                kept_ids=[f["fact_id"] for f in target["facts"]],
                witnesses={
                    k: dict(kept=sorted(v & selected), missing=sorted(v - selected))
                    for k, v in witnesses.items()
                },
                old_price_450_coverage=False,
            )
        )
    result = dict(
        case_ordinal=5,
        event_id="E4",
        facts=count,
        method="BM25 k1=1.2 b=0.75; current title+body; original order on output; offline only",
        ranked=ranked,
        experiments=runs,
        conclusion=(
            "Keep full facts in production. No fact contains the exact new DDR5 6000 "
            "32GB $449.97 price; absence cannot be established using a truncated subset."
        ),
    )
    write(ROOT / "bm25.json", result)
    return result


def main():
    cohort = json.loads((ROOT / "cohort.json").read_text(encoding="utf8"))["cases"]
    records = {
        arm: [read(f"{arm}-{c['ordinal']:02}.json") for c in cohort]
        for arm in ["original", "optimized", "explicit"]
    }
    complete_original = [
        read(f"original-supplement-{r['ordinal']:02}.json") if r["status"] != "OK" else r
        for r in records["original"]
    ]
    assert all(r["status"] == "OK" for r in complete_original)
    rows = []
    for i, case in enumerate(cohort):
        old, new, marked = complete_original[i], records["optimized"][i], records["explicit"][i]
        rows.append(
            dict(
                ordinal=case["ordinal"],
                ticker=case["ticker"],
                title=case["title"],
                historical_qwen=case["baseline"]["output"]["result"],
                original_first_status=records["original"][i]["status"],
                original=old["output"]["result"],
                optimized=new["output"]["result"],
                marked=marked["output"]["result"],
                original_refs=old["output"]["reference_ids"],
                optimized_refs=new["output"]["reference_ids"],
                original_facts=old["output"]["fact_attributions"],
                optimized_facts=new["output"]["fact_attributions"],
                original_seconds=old["wall_seconds"],
                optimized_seconds=new["wall_seconds"],
                original_usage=old["usage"],
                optimized_usage=new["usage"],
                marked_usage=marked["usage"],
            )
        )
    first_success_indices = [i for i, r in enumerate(records["original"]) if r["status"] == "OK"]
    serial = {1, 2, 3, 5, 7, 8}
    summary = dict(
        first_attempts={
            arm: {**receipt_summary(recs), "valid": sum(r["status"] == "OK" for r in recs)}
            for arm, recs in records.items()
        },
        completed_paired_20={
            "original": receipt_summary(complete_original),
            "optimized": receipt_summary(records["optimized"]),
            "cache_control_exploratory": receipt_summary(records["explicit"]),
            "historical_qwen": receipt_summary([c["baseline"] for c in cohort], True),
        },
        first_success_paired_18={
            arm: receipt_summary([records[arm][i] for i in first_success_indices])
            for arm in records
        },
        agreement=dict(
            original_optimized=sum(r["original"] == r["optimized"] for r in rows),
            optimized_marked=sum(r["optimized"] == r["marked"] for r in rows),
            historical_optimized=sum(r["historical_qwen"] == r["optimized"] for r in rows),
            total=20,
        ),
        serial_cache={
            arm: receipt_summary([r for r in recs if r["ordinal"] in serial])
            for arm, recs in records.items()
        },
        serial_cache_following_first={
            arm: receipt_summary([r for r in recs if r["ordinal"] in serial - {1}])
            for arm, recs in records.items()
        },
        token_estimates={
            k: describe([c["token_estimates"][k] for c in cohort])
            for k in cohort[0]["token_estimates"]
        },
        rows=rows,
    )
    write(ROOT / "analysis.json", summary)
    with (ROOT / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    bm25(cohort[4])
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in {"rows", "token_estimates"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
