"""Add high-frequency custom XBRL metrics from the latest four SEC quarters."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from common import DEFAULT_OUTPUT_DIR, DEFAULT_WORK_DIR, clean_aliases, download, id_slug, read_json, runtime_normalize, stable_suffix, write_json


QUARTERS = ["2025q2", "2025q3", "2025q4", "2026q1"]
BASE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/"
NUMERIC_TYPES = {"monetary", "shares", "perShare", "percent", "pure", "integer", "decimal"}


def _member(archive: zipfile.ZipFile, suffix: str) -> str:
    return next(name for name in archive.namelist() if name.lower().endswith(suffix.lower()))


def _split_camel(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return " ".join(value.split())


def collect(downloads: Path) -> tuple[Counter[str], dict[str, dict[str, str]]]:
    counts: Counter[str] = Counter()
    metadata: dict[str, dict[str, str]] = {}
    for quarter in QUARTERS:
        path = download(BASE_URL + f"{quarter}.zip", downloads / f"{quarter}_financial_statements.zip", sec=True, timeout=900)
        with zipfile.ZipFile(path) as archive:
            custom_tags: set[str] = set()
            with archive.open(_member(archive, "tag.txt")) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    tag = str(row.get("tag") or "").strip()
                    datatype = str(row.get("datatype") or "").strip()
                    if not tag or str(row.get("custom")) != "1" or str(row.get("abstract")) == "1":
                        continue
                    if datatype not in NUMERIC_TYPES or tag.endswith(("Axis", "Domain", "Member", "Table", "TextBlock", "Abstract")):
                        continue
                    custom_tags.add(tag)
                    current = metadata.get(tag)
                    candidate = {
                        "label": str(row.get("tlabel") or "").strip(),
                        "doc": str(row.get("doc") or "").strip(),
                    }
                    if current is None or len(candidate["label"]) < len(current["label"] or "~"):
                        metadata[tag] = candidate
            with archive.open(_member(archive, "num.txt")) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    tag = str(row.get("tag") or "").strip()
                    if tag in custom_tags:
                        counts[tag] += 1
        print(f"SEC metrics {quarter}: {len(custom_tags)} custom tags, {sum(counts.values())} uses", flush=True)
    return counts, metadata


def enrich(output: Path, counts: Counter[str], metadata: dict[str, dict[str, str]], limit: int) -> int:
    path = output / "metrics.json"
    metrics = read_json(path)
    existing_ids = {item["id"] for item in metrics}
    alias_to_index: dict[str, int] = {}
    name_to_index: dict[str, int] = {}
    for index, item in enumerate(metrics):
        name_to_index.setdefault(runtime_normalize(item["name"]), index)
        for alias in [item["name"], *item["aliases"]]:
            alias_to_index.setdefault(runtime_normalize(alias), index)
    added = 0
    for tag, _ in counts.most_common(limit):
        info = metadata.get(tag, {})
        label = info.get("label") or _split_camel(tag)
        if len(label) > 180:
            label = _split_camel(tag)
        aliases = [tag, _split_camel(tag)]
        matching = name_to_index.get(runtime_normalize(label))
        if matching is not None:
            item = metrics[matching]
            item["aliases"] = clean_aliases(item["name"], [*item["aliases"], *aliases], limit=20)
            continue
        if runtime_normalize(tag) in alias_to_index:
            continue
        metric_id = f"XBRL_CUSTOM_{id_slug(tag)}"
        if metric_id in existing_ids:
            metric_id = f"{metric_id}_{stable_suffix(tag)}"
        metrics.append({"id": metric_id, "name": label, "aliases": clean_aliases(label, aliases, limit=12)})
        existing_ids.add(metric_id)
        name_to_index[runtime_normalize(label)] = len(metrics) - 1
        added += 1
    metrics.sort(key=lambda item: item["id"])
    write_json(path, metrics)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=750)
    args = parser.parse_args()
    counts, metadata = collect(args.work_dir / "downloads")
    added = enrich(args.output_dir, counts, metadata, max(0, args.limit))
    print(json.dumps({"custom_metric_uses": sum(counts.values()), "custom_metric_tags": len(counts), "added": added}))


if __name__ == "__main__":
    main()
