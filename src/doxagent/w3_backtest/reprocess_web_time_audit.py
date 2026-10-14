"""Rebuild W3 Web Search cutoff labels from saved SDK receipts, without searches."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collect import (
    _bind_source_result_refs,
    _case_index,
    _cutoff_status,
    _filtered_result_lines,
    _normalize_sources,
    _parse_datetime,
    _read_json,
    _write_json_atomic,
)


def _replace_metadata(text: str, label: str, value: Any) -> str:
    rendered = "UNKNOWN" if value is None or value == "" else str(value)
    pattern = re.compile(rf"^(\s*-\s*{re.escape(label)}:\s*).*$", re.MULTILINE)
    return pattern.sub(lambda match: match.group(1) + rendered, text, count=1)


def _refresh_source_metadata(
    source_dir: Path, sources: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> None:
    decision_by_id = {
        row["source_id"]: row for row in decisions if isinstance(row.get("source_id"), str)
    }
    for source in sources:
        source_id = source.get("source_id")
        if not isinstance(source_id, str):
            continue
        path = source_dir / f"{source_id}.md"
        if not path.is_file():
            continue
        review = decision_by_id.get(source_id, {})
        text = path.read_text(encoding="utf-8-sig")
        fields = {
            "Published at": source.get("published_at"),
            "Time precision": source.get("time_precision") or "UNKNOWN",
            "Updated at": source.get("updated_at"),
            "Content state": source.get("content_state") or "UNKNOWN",
            "Cutoff status": source.get("cutoff_status") or "UNKNOWN",
            "Temporal review": review.get("reason"),
        }
        updated = text
        for label, value in fields.items():
            updated = _replace_metadata(updated, label, value)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def _reclassify_observations(
    observations: Any, sources: list[dict[str, Any]], cutoff: datetime
) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(observations, list):
        return [], 0
    source_by_url = {
        str(source.get("url")): source for source in sources if isinstance(source.get("url"), str)
    }
    revised: list[dict[str, Any]] = []
    changed = 0
    for item in observations:
        if not isinstance(item, dict):
            continue
        observation = copy.deepcopy(item)
        source_urls = observation.get("source_urls")
        if not isinstance(source_urls, list):
            source_urls = []
        linked_sources = [
            source_by_url[url]
            for url in source_urls
            if isinstance(url, str) and url in source_by_url
        ]
        ambiguous_same_day = False
        for field in ("event_time", "publication_time"):
            value = observation.get(field)
            if not isinstance(value, str):
                continue
            matching_precision = [
                source.get("time_precision")
                for source in linked_sources
                if source.get("published_at") == value
            ]
            precisions = matching_precision or [None]
            if any(
                _cutoff_status(value, cutoff, precision) == "UNKNOWN"
                and isinstance(value[:10], str)
                and value[:10] == cutoff.date().isoformat()
                for precision in precisions
            ):
                ambiguous_same_day = True
        if ambiguous_same_day and observation.get("time_status") != "UNKNOWN":
            observation["time_status"] = "UNKNOWN"
            changed += 1
        revised.append(observation)
    return revised, changed


def reprocess_dataset(dataset: Path, case_keys: list[str] | None = None) -> dict[str, Any]:
    dataset = Path(dataset).resolve()
    cases = _case_index(dataset)
    selected = list(case_keys) if case_keys else sorted(cases)
    unknown = [key for key in selected if key not in cases]
    if unknown:
        raise KeyError(f"Unknown case_key(s): {', '.join(unknown)}")

    totals: dict[str, int] = {
        "cases": 0,
        "sources_kept": 0,
        "sources_dropped": 0,
        "same_day_observations_relabelled_unknown": 0,
    }
    for case_key in selected:
        input_dir = dataset / "inputs" / case_key
        case_doc = _read_json(input_dir / "case.json")
        cutoff = _parse_datetime(case_doc.get("evidence_cutoff_at"))
        if cutoff is None:
            raise ValueError(f"Case {case_key} has no parseable evidence_cutoff_at")
        raw_dir = dataset / "preparation" / "web_raw" / case_key
        raw_path = raw_dir / "sdk_result.json"
        review_path = dataset / "preparation" / "web_review" / f"{case_key}.json"
        if not raw_path.is_file() or not review_path.is_file():
            raise FileNotFoundError(f"Case {case_key} lacks saved SDK result or review")

        raw = _read_json(raw_path)
        search_items = raw.get("search_items")
        raw_response = raw.get("structured_response")
        if not isinstance(search_items, list) or not isinstance(raw_response, dict):
            raise ValueError(f"Case {case_key} has malformed saved SDK result")
        response = _bind_source_result_refs(copy.deepcopy(raw_response), search_items)
        source_dir = input_dir / "evidence" / "sources"
        existing_urls: dict[str, str] = {}
        used_ids: set[str] = set()
        if source_dir.is_dir():
            for source_path in source_dir.glob("*.md"):
                source_text = source_path.read_text(encoding="utf-8-sig")
                match = re.search(
                    r"^\s*-?\s*(?:Canonical )?URL:\s*(https?://\S+)",
                    source_text,
                    flags=re.MULTILINE,
                )
                used_ids.add(source_path.stem)
                if match:
                    existing_urls[match.group(1).rstrip(".,)")] = source_path.stem

        sources, decisions = _normalize_sources(response, cutoff=cutoff, source_dir=source_dir)
        dropped_urls = {
            str(row.get("url"))
            for row in decisions
            if row.get("decision") == "DROP" and isinstance(row.get("url"), str)
        }
        archive_dir = raw_dir / "excluded_sources"
        for url in dropped_urls:
            old_source_id = existing_urls.get(url)
            if old_source_id is None:
                continue
            source_path = source_dir / f"{old_source_id}.md"
            if source_path.is_file():
                archive_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, archive_dir / source_path.name)
                source_path.unlink()

        _refresh_source_metadata(source_dir, sources, decisions)
        result_lines = _filtered_result_lines(
            search_items,
            sources,
            cutoff=case_doc["evidence_cutoff_at"],
            thread_id=str(raw.get("thread_id") or "unknown"),
            turn_id=str(raw.get("turn_id") or "unknown"),
        )
        web_path = input_dir / "evidence" / "web_search.jsonl"
        web_path.parent.mkdir(parents=True, exist_ok=True)
        web_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in result_lines
            ),
            encoding="utf-8",
        )

        review = _read_json(review_path)
        observations, changed_observations = _reclassify_observations(
            raw_response.get("observations", []), sources, cutoff
        )
        review["reviewed_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        review["review_policy"] = (
            "Known post-cutoff publication/update is dropped; unresolved publication or "
            "revision time is kept only with UNKNOWN. DAY or UNKNOWN publication precision "
            "is compared by calendar date; a same-day value remains UNKNOWN."
        )
        review["source_decisions"] = decisions
        review["observations"] = observations
        _write_json_atomic(review_path, review)

        totals["cases"] += 1
        totals["sources_kept"] += len(sources)
        totals["sources_dropped"] += sum(row.get("decision") == "DROP" for row in decisions)
        totals["same_day_observations_relabelled_unknown"] += changed_observations
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("case_keys", nargs="*")
    args = parser.parse_args()
    print(json.dumps(reprocess_dataset(args.dataset, args.case_keys or None), indent=2))


if __name__ == "__main__":
    main()
