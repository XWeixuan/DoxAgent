"""Operator-driven Massive replay. No production writes or automatic tuning."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "eval/massive_distribution_gold_20261112"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from distribution_relevance_debug import comparison, metrics, note, read, regex_trace, rows, sha, write, write_rows
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms, validate_terms
from doxagent.message_bus_v2.relevance import _match, _normalize, regex_relevant
from doxagent.message_bus_v2.schema import RawMessageInput

TICKERS = "INTC MU BE COHR NBIS CEG RKLB SNOW ONDS".split()


def now():
    return datetime.now(UTC).isoformat()


def evaluate(ticker, attempt, split):
    directory = HERE / ticker / "regex" / attempt
    candidate = directory / "candidate.json"
    terms = TickerMonitoringTerms.model_validate(read(candidate))
    dataset = DATA / "final" / ticker / f"{split}-gold.jsonl"
    selected = rows(dataset)
    assert len(selected) == 50 and len({r["pair_id"] for r in selected}) == 50
    validate_terms(terms, {"en"})
    assert terms.ticker == ticker
    baseline = read(DATA / "baseline-terms" / f"{ticker}.json")
    assert all(read(candidate)[k] == v for k, v in baseline.items() if k != "l2")
    engine = {f.__name__: hashlib.sha256(inspect.getsource(f).encode()).hexdigest()
              for f in [regex_relevant, _match, _normalize]}
    assert engine == read(DATA / "baseline-integrity.json")["function_sha256"]
    metadata = dict(candidate_sha256=sha(candidate), context_sha256=sha(DATA / "frozen-context.json"),
                    engine_sha256=engine, model=None, threshold=None, decision_url=None,
                    dataset_sha256=sha(dataset), language_policy="original row language then native en fallback")
    output = directory / split
    if (output / "metadata.json").exists():
        assert read(output / "metadata.json") == metadata, "Frozen run configuration changed"
        return read(output / "metrics.json")
    results, details = [], []
    for r in selected:
        message = RawMessageInput(**r["evidence"], url=r["canonical_url"], raw_payload={}, metadata={"language": r["language"]})
        prediction = regex_relevant(terms, r["language"], message)
        result = dict(pair_id=r["pair_id"], gold=r["gold"]["label"], prediction=prediction, error=None)
        results.append(result)
        details.append({**r, "prediction": prediction, "hits": regex_trace(terms, [r["language"], "en"], message)})
    write(output / "metadata.json", metadata)
    write_rows(output / "predictions.jsonl", results)
    write_rows(output / "details.jsonl", details)
    write_rows(output / "badcases.jsonl", [r for r in details if r["gold"]["label"] != "INSUFFICIENT" and r["prediction"] != (r["gold"]["label"] == "RELATED")])
    write(output / "metrics.json", metrics(results))
    write(output / "receipt.json", {"completed_at": now(), "count": len(results)})
    return metrics(results)


def record(ticker, lesson, validation=False):
    note(HERE / ticker, ticker, "regex", f"MASSIVE-{ticker}-20261112-01", lesson, with_validation=validation)


def freeze(ticker):
    root = HERE / ticker / "regex"
    assert not list(root.glob("attempt-*/validation/metrics.json"))
    candidates = list(sorted(root.glob("attempt-*/candidate.json")))
    assert 1 <= len(candidates) <= 5
    receipt = {"frozen_at": now(), "candidates": {p.parent.name: sha(p) for p in candidates},
               "development_best": comparison(root)["development_best"]}
    write(HERE / ticker / "regular-freeze.json", receipt)
    return receipt


def finish(ticker):
    root = HERE / ticker / "regex"
    frozen = read(HERE / ticker / "regular-freeze.json")
    assert all(sha(root / a / "candidate.json") == h for a, h in frozen["candidates"].items())
    report = comparison(root, True)
    assert report["all_attempts_paired_and_complete"]
    write(HERE / ticker / "comparison.json", report)
    selected = report["final_best"]
    shutil.copyfile(root / selected / "candidate.json", HERE / "selected" / f"{ticker}.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["evaluate", "freeze", "finish"])
    parser.add_argument("ticker", choices=TICKERS)
    parser.add_argument("attempt", nargs="?")
    parser.add_argument("split", nargs="?", choices=["development", "validation"])
    args = parser.parse_args()
    if args.command == "evaluate": result = evaluate(args.ticker, args.attempt, args.split)
    elif args.command == "freeze": result = freeze(args.ticker)
    else:
        (HERE / "selected").mkdir(exist_ok=True)
        result = finish(args.ticker)
    print(json.dumps(result, ensure_ascii=False))
