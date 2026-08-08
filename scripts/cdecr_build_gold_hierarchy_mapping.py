"""Build a redirect-resolved Gold-to-current CDECR hierarchy mapping."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--mention-evaluation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-partial", action="store_true")
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.registry}?mode=ro", uri=True)
    current_atomic = {
        str(mention_id): str(event_id)
        for event_id, mention_id in connection.execute(
            """
            SELECT h.event_id, m.mention_id
            FROM atomic_event_heads h
            JOIN atomic_event_mentions m
              ON m.event_id = h.event_id AND m.event_version = h.current_version
            """
        )
    }
    current_package = {
        str(event_id): str(package_id)
        for package_id, event_id in connection.execute(
            "SELECT package_id, event_id FROM active_package_memberships"
        )
    }
    gold_source = json.loads(args.gold.read_text(encoding="utf-8"))
    gold = {
        str(item["id"]): item
        for document in gold_source["documents"]
        for item in document.get("gold", document.get("gold_mentions", []))
    }
    evaluation = json.loads(args.mention_evaluation.read_text(encoding="utf-8"))
    accepted = {"STRICT_TP", "PARTIAL"} if args.include_partial else {"STRICT_TP"}
    items: list[dict[str, str]] = []
    seen_gold: set[str] = set()
    for document in evaluation["documents"]:
        for judgment in document["judgments"]:
            gold_id = judgment.get("gold_id")
            mention_id = judgment.get("mention_id")
            if (
                judgment.get("verdict") not in accepted
                or not isinstance(gold_id, str)
                or not isinstance(mention_id, str)
                or gold_id in seen_gold
                or gold_id not in gold
            ):
                continue
            atomic_id = current_atomic.get(mention_id)
            package_id = current_package.get(atomic_id or "")
            if atomic_id is None or package_id is None:
                continue
            seen_gold.add(gold_id)
            items.append(
                {
                    "source_occurrence_id": gold_id,
                    "mention_id": mention_id,
                    "gold_atomic_id": str(gold[gold_id]["atomic_id"]),
                    "gold_package_id": str(gold[gold_id]["package_id"]),
                    "predicted_atomic_id": atomic_id,
                    "predicted_package_id": package_id,
                    "mention_verdict": str(judgment["verdict"]),
                }
            )
    payload = {
        "report_version": "cdecr-gold-hierarchy-mapping-v1",
        "include_partial": args.include_partial,
        "gold_count": len(gold),
        "mapped_count": len(items),
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("gold_count", "mapped_count")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
