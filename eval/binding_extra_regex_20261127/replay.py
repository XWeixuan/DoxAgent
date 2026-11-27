"""Replay retained first-layer positives using the SAME cleanup and extra gate as live.

Usage: python eval/binding_extra_regex_20261127/replay.py [--cases PATH] [--rules PATH]
No network, model calls, config writes, historical deletions or replay publications.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from doxagent.content_enrichment.publisher_cleanup import clean_text
from doxagent.message_bus_v2.extra_relevance import extra_allows
from doxagent.message_bus_v2.schema import TickerSourceBinding

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "rules-selected.json")
    args = parser.parse_args()
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    roster = {b["binding_id"]: b for b in data["bindings"]}
    assert set(rules) <= set(roster), "rules must belong to actual subscriptions"
    bindings = {
        key: TickerSourceBinding(
            binding_id=key,
            ticker=roster[key]["ticker"],
            source_id=roster[key]["source_id"],
            extra_relevance=value,
        )
        for key, value in rules.items()
    }
    blocked, results, totals, excluded = [], [], Counter(), Counter()
    assert len({(x["ticker"], x["id"]) for x in data["cases"]}) == len(data["cases"])
    for item in data["cases"]:
        item = dict(item)
        key = item["ticker"] + ":" + item["source_id"]
        for field in ("body", "summary"):
            item[field] = clean_text(item.get(field) or "", item["url"])
        message = SimpleNamespace(**{k: item.get(k) or "" for k in ("title", "summary", "body")})
        binding = bindings.get(key)
        allowed = binding is None or extra_allows(binding, message)
        totals[key] += 1
        if not allowed:
            blocked.append(item)
            excluded[key] += 1
        results.append(
            {
                **{
                    k: item[k]
                    for k in ("id", "ticker", "source_id", "title", "url", "published_at")
                },
                "extra_allowed": allowed,
            }
        )
    summary = [
        {
            "binding_id": key,
            "total": count,
            "blocked": excluded[key],
            "retained": count - excluded[key],
        }
        for key, count in totals.items()
    ]
    (ROOT / "summary-selected.json").write_text(json.dumps(summary, indent=2) + "\n")
    (ROOT / "blocked-selected.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in blocked), encoding="utf-8"
    )
    with (ROOT / "all-history-results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    (ROOT / "manifest.json").write_text(
        json.dumps(
            {
                "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "rules_sha256": hashlib.sha256(args.rules.read_bytes()).hexdigest(),
                "bindings": len(roster),
                "cases": len(results),
                "blocked": len(blocked),
                "retained": len(results) - len(blocked),
                "unique_blocked_bodies": len({(x["title"], x["body"]) for x in blocked}),
                "published_at_min": min(x["published_at"] for x in data["cases"]),
                "published_at_max": max(x["published_at"] for x in data["cases"]),
                "denominator": "retained formal messages already accepted by the original layer",
                "cleanup": "doxagent.content_enrichment.publisher_cleanup.clean_text",
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"total": len(results), "blocked": len(blocked), "bindings": len(roster)}))


if __name__ == "__main__":
    main()
