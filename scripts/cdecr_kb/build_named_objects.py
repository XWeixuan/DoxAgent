"""Build bounded Named Objects from EPA FRS and Wikidata."""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from common import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_USER_AGENT,
    DEFAULT_WORK_DIR,
    clean_aliases,
    display_name,
    download,
    id_slug,
    read_json,
    runtime_normalize,
    stable_suffix,
    unique_id,
    write_json,
)


EPA_SINGLE = "https://ordsext.epa.gov/FLA/www3/state_files/national_single.zip"
TARGET_NAICS_PREFIXES = {"21", "22", "31", "32", "33", "48", "49", "51", "562"}
GENERIC_ORG_TOKENS = {
    "the", "company", "corporation", "corp", "inc", "incorporated", "limited", "ltd", "llc", "lp",
    "group", "holdings", "bank", "financial", "capital", "management", "partners", "services", "global",
    "international", "national", "american", "united", "first", "new", "research", "media",
    "energy", "power", "water", "waste", "resource", "resources", "technology", "technologies",
    "system", "systems", "solution", "solutions", "industry", "industries", "industrial",
    "manufacturing", "product", "products", "material", "materials", "communication", "communications",
}

WIKIDATA_CLASSES: dict[str, list[str]] = {
    "FACILITY": ["factory", "semiconductor fabrication plant", "data center", "mine", "oil refinery", "power station", "launch site"],
    "PRODUCT": ["product", "product family", "medication", "vehicle model", "software product", "electronic device"],
    "PROJECT": ["construction project", "infrastructure project", "megaproject", "business project"],
    "ASSET": ["satellite", "spacecraft", "ship", "aircraft", "oil platform"],
    "TECHNOLOGY": ["technology", "manufacturing process", "computer architecture", "semiconductor device fabrication"],
    "PROGRAM": ["government program", "research program", "space program", "economic development program"],
}
WIKIDATA_LIMITS = {"FACILITY": 7000, "PRODUCT": 9000, "PROJECT": 5000, "ASSET": 8000, "TECHNOLOGY": 6000, "PROGRAM": 5000}
TOO_BROAD_WIKIDATA_CLASSES = {"factory", "product", "technology"}


def _organization_alias_index(output: Path) -> tuple[dict[str, list[tuple[str, str]]], dict[str, str]]:
    owners_by_alias: dict[str, set[str]] = defaultdict(set)
    owner_names: dict[str, str] = {}
    for filename in ["companies.json", "institutions.json"]:
        for item in read_json(output / filename):
            owner_names[item["id"]] = item["name"]
            for raw in [item["name"], *item["aliases"]]:
                alias = runtime_normalize(raw)
                tokens = alias.split()
                if not alias or len(alias) < 5 or (len(tokens) == 1 and tokens[0] in GENERIC_ORG_TOKENS):
                    continue
                if filename == "institutions.json" and len(tokens) == 1:
                    continue
                if len(tokens) == 1 and (len(tokens[0]) < 5 or tokens[0].isdigit()):
                    continue
                owners_by_alias[alias].add(item["id"])
    unique_aliases = {
        alias: next(iter(owners))
        for alias, owners in owners_by_alias.items()
        if len(owners) == 1
    }
    token_frequency = Counter(
        token
        for alias in unique_aliases
        for token in set(alias.split())
        if token not in GENERIC_ORG_TOKENS and len(token) >= 3
    )
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for alias, owner_id in unique_aliases.items():
        usable_tokens = [
            token
            for token in set(alias.split())
            if token not in GENERIC_ORG_TOKENS and len(token) >= 3
        ]
        if not usable_tokens:
            continue
        key_token = min(usable_tokens, key=lambda token: (token_frequency[token], -len(token), token))
        index[key_token].append((alias, owner_id))
    for token in index:
        index[token].sort(key=lambda item: len(item[0]), reverse=True)
    return index, owner_names


def _match_owner(name: str, index: dict[str, list[tuple[str, str]]]) -> str | None:
    normalized = runtime_normalize(name)
    padded = f" {normalized} "
    for token in set(normalized.split()):
        for alias, owner_id in index.get(token, []):
            if f" {alias} " in padded:
                return owner_id
    return None


def _field(row: dict[str, str], *candidates: str) -> str:
    normalized = {re.sub(r"[^A-Z0-9]", "", key.upper()): value for key, value in row.items() if key}
    for candidate in candidates:
        value = normalized.get(re.sub(r"[^A-Z0-9]", "", candidate.upper()))
        if value is not None:
            return str(value).strip()
    return ""


def _display_name(value: str) -> str:
    value = " ".join(value.split()).strip(" ,")
    return display_name(value)


def _hash_rank(value: str) -> int:
    return int.from_bytes(hashlib.sha1(value.encode("utf-8")).digest()[:8], "big")


def _epa_facilities(
    path: Path,
    output: Path,
    matched_cap: int,
    unmatched_cap: int,
    per_owner_cap: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    alias_index, _ = _organization_alias_index(output)
    matched_by_owner: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    unmatched_heap: list[tuple[int, str, dict[str, Any]]] = []
    stats: Counter[str] = Counter()
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith((".csv", ".txt")))
        with archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            delimiter = "|" if sample.count("|") > sample.count(",") else ","
            reader = csv.DictReader(handle, delimiter=delimiter)
            for row in reader:
                stats["rows"] += 1
                if stats["rows"] % 250_000 == 0:
                    print(
                        f"EPA rows {stats['rows']}: owner_matched={stats['owner_matched']} "
                        f"target_candidates={stats['owner_matched'] + len(unmatched_heap)}",
                        flush=True,
                    )
                registry_id = _field(row, "REGISTRY_ID", "REGISTRYID", "FRS_FACILITY_DETAIL_REPORT_URL")
                raw_name = _field(row, "PRIMARY_NAME", "FACILITY_NAME", "PRIMARYNAME", "NAME")
                name = _display_name(raw_name)
                if len(name) < 3:
                    stats["blank_or_short_name"] += 1
                    continue
                city = _field(row, "CITY_NAME", "CITY")
                state = _field(row, "STATE_CODE", "STATE")
                naics = _field(row, "NAICS_CODE", "NAICS_CODES", "NAICS")
                naics_codes = re.findall(r"\d{2,6}", naics)
                if not any(any(code.startswith(prefix) for prefix in TARGET_NAICS_PREFIXES) for code in naics_codes):
                    stats["outside_target_naics"] += 1
                    continue
                dedup_key = registry_id or runtime_normalize(f"{name}|{city}|{state}")
                if not dedup_key or dedup_key in seen:
                    stats["duplicate"] += 1
                    continue
                seen.add(dedup_key)
                owner_id = _match_owner(name, alias_index)
                aliases = []
                if city and state:
                    aliases.append(f"{name}, {city}, {state}")
                record = {
                    "name": name,
                    "kind": "FACILITY",
                    "owner_id": owner_id,
                    "aliases": aliases,
                    "source_key": f"EPA:{dedup_key}",
                }
                if owner_id:
                    owner_heap = matched_by_owner[owner_id]
                    rank = _hash_rank(dedup_key)
                    item = (-rank, dedup_key, record)
                    if len(owner_heap) < per_owner_cap:
                        heapq.heappush(owner_heap, item)
                    elif item > owner_heap[0]:
                        heapq.heapreplace(owner_heap, item)
                    stats["owner_matched"] += 1
                elif unmatched_cap > 0:
                    rank = _hash_rank(dedup_key)
                    item = (-rank, dedup_key, record)
                    if len(unmatched_heap) < unmatched_cap:
                        heapq.heappush(unmatched_heap, item)
                    elif item > unmatched_heap[0]:
                        heapq.heapreplace(unmatched_heap, item)
    unmatched = [item[2] for item in unmatched_heap]
    matched_candidates = [item[2] for owner_heap in matched_by_owner.values() for item in owner_heap]
    matched = heapq.nsmallest(
        matched_cap,
        matched_candidates,
        key=lambda record: _hash_rank(record["source_key"]),
    ) if matched_cap > 0 else []
    stats["owner_matched_selected"] = len(matched)
    stats["unmatched_sampled"] = len(unmatched)
    return matched + unmatched, dict(stats)


def _org_exact_lookup(output: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    ambiguous: set[str] = set()
    for filename in ["companies.json", "institutions.json"]:
        for item in read_json(output / filename):
            for raw in [item["name"], *item["aliases"]]:
                key = runtime_normalize(raw)
                if key in result and result[key] != item["id"]:
                    ambiguous.add(key)
                elif key:
                    result[key] = item["id"]
    for key in ambiguous:
        result.pop(key, None)
    return result


def _wikidata_named_objects(output: Path, cache: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cache.mkdir(parents=True, exist_ok=True)
    org_lookup = _org_exact_lookup(output)
    records: list[dict[str, Any]] = []
    stats: dict[str, int] = {}
    for kind, labels in WIKIDATA_CLASSES.items():
        target = cache / f"named_objects_{kind.lower()}.json"
        if target.exists():
            payload = read_json(target)
        else:
            values = " ".join(json.dumps(label) + "@en" for label in labels)
            query = f"""
SELECT ?item ?itemLabel (SAMPLE(?orgLabel) AS ?ownerLabel)
       (GROUP_CONCAT(DISTINCT ?alias; separator=\"|\") AS ?aliases)
WHERE {{
  VALUES ?classLabel {{ {values} }}
  ?class rdfs:label ?classLabel .
  ?item wdt:P31/wdt:P279* ?class ; rdfs:label ?itemLabel .
  FILTER(LANG(?itemLabel) = \"en\")
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = \"en\") }}
  OPTIONAL {{
    VALUES ?ownerProperty {{ wdt:P127 wdt:P137 wdt:P176 wdt:P178 }}
    ?item ?ownerProperty ?org .
    ?org rdfs:label ?orgLabel . FILTER(LANG(?orgLabel) = \"en\")
  }}
}}
GROUP BY ?item ?itemLabel
LIMIT {WIKIDATA_LIMITS[kind]}
"""
            url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
            request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/sparql-results+json"})
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    payload = json.load(response)
                write_json(target, payload)
            except Exception as exc:
                print(f"WARN Wikidata named objects {kind} combined query failed: {exc}; trying split queries", flush=True)
                bindings: list[dict[str, Any]] = []
                specific_labels = [label for label in labels if label not in TOO_BROAD_WIKIDATA_CLASSES]
                per_class_limit = max(500, WIKIDATA_LIMITS[kind] // max(1, len(specific_labels)))
                for class_label in specific_labels:
                    class_cache = cache / f"named_objects_{kind.lower()}_{id_slug(class_label).lower()}.json"
                    if class_cache.exists():
                        class_payload = read_json(class_cache)
                    else:
                        class_query = f"""
SELECT ?item ?itemLabel (SAMPLE(?orgLabel) AS ?ownerLabel)
       (GROUP_CONCAT(DISTINCT ?alias; separator=\"|\") AS ?aliases)
WHERE {{
  ?class rdfs:label {json.dumps(class_label)}@en .
  ?item wdt:P31/wdt:P279* ?class ; rdfs:label ?itemLabel .
  FILTER(LANG(?itemLabel) = \"en\")
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = \"en\") }}
  OPTIONAL {{
    VALUES ?ownerProperty {{ wdt:P127 wdt:P137 wdt:P176 wdt:P178 }}
    ?item ?ownerProperty ?org .
    ?org rdfs:label ?orgLabel . FILTER(LANG(?orgLabel) = \"en\")
  }}
}}
GROUP BY ?item ?itemLabel
LIMIT {per_class_limit}
"""
                        class_url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": class_query, "format": "json"})
                        class_request = urllib.request.Request(
                            class_url,
                            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/sparql-results+json"},
                        )
                        try:
                            with urllib.request.urlopen(class_request, timeout=180) as response:
                                class_payload = json.load(response)
                            write_json(class_cache, class_payload)
                        except Exception as class_exc:
                            print(f"WARN Wikidata {kind}/{class_label} skipped: {class_exc}", flush=True)
                            continue
                    bindings.extend(class_payload.get("results", {}).get("bindings", []))
                if not bindings:
                    stats[f"wikidata_{kind.lower()}_error"] = 1
                    continue
                # Deduplicate items that are instances of multiple selected classes.
                by_item = {
                    binding.get("item", {}).get("value", f"row:{index}"): binding
                    for index, binding in enumerate(bindings)
                }
                payload = {"results": {"bindings": list(by_item.values())}}
                write_json(target, payload)
        count = 0
        for binding in payload.get("results", {}).get("bindings", []):
            item_url = binding.get("item", {}).get("value", "")
            qid = item_url.rsplit("/", 1)[-1]
            name = binding.get("itemLabel", {}).get("value", "")
            if not name or name == qid:
                continue
            owner_label = binding.get("ownerLabel", {}).get("value", "")
            owner_id = org_lookup.get(runtime_normalize(owner_label))
            aliases = binding.get("aliases", {}).get("value", "").split("|")
            records.append(
                {
                    "name": name,
                    "kind": kind,
                    "owner_id": owner_id,
                    "aliases": aliases,
                    "source_key": f"WIKIDATA:{qid}",
                }
            )
            count += 1
        stats[f"wikidata_{kind.lower()}"] = count
    return records, stats


def build_named_objects(
    work: Path,
    output: Path,
    *,
    matched_epa_cap: int = 50_000,
    unmatched_epa_cap: int = 25_000,
    wikidata: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    epa_path = download(EPA_SINGLE, work / "downloads" / "national_single.zip", timeout=1200)
    raw_records, stats = _epa_facilities(epa_path, output, matched_epa_cap, unmatched_epa_cap)
    if wikidata:
        wikidata_records, wikidata_stats = _wikidata_named_objects(output, work / "state" / "wikidata")
        raw_records.extend(wikidata_records)
        stats.update(wikidata_stats)

    used_ids: dict[str, str] = {}
    seen_sources: set[str] = set()
    records: list[dict[str, Any]] = []
    for raw in sorted(raw_records, key=lambda item: (item["kind"], runtime_normalize(item["name"]), item["source_key"])):
        if raw["source_key"] in seen_sources:
            continue
        seen_sources.add(raw["source_key"])
        object_id = unique_id(raw["kind"], raw["name"], raw["source_key"], used_ids)
        records.append(
            {
                "id": object_id,
                "name": raw["name"],
                "kind": raw["kind"],
                "owner_id": raw["owner_id"],
                "aliases": clean_aliases(raw["name"], raw["aliases"], limit=25),
            }
        )
    records.sort(key=lambda item: item["id"])
    write_json(output / "named_objects.json", records)
    write_json(work / "reports" / "named_object_source_stats.json", stats)
    return records, stats


def enrich_existing_with_wikidata(work: Path, output: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Add Wikidata kinds without re-streaming the multi-gigabyte EPA CSV."""

    raw_records, stats = _wikidata_named_objects(output, work / "state" / "wikidata")
    wikidata_facility_ids: set[str] = set()
    for raw in raw_records:
        if raw["kind"] != "FACILITY":
            continue
        candidate = f"FACILITY_{id_slug(raw['name'])}"
        wikidata_facility_ids.update({candidate, f"{candidate}_{stable_suffix(raw['source_key'])}"})
    existing = [
        item
        for item in read_json(output / "named_objects.json")
        if item.get("kind") == "FACILITY" and item.get("id") not in wikidata_facility_ids
    ]
    used_ids = {item["id"] for item in existing}
    seen_sources: set[str] = set()
    for raw in sorted(raw_records, key=lambda item: (item["kind"], runtime_normalize(item["name"]), item["source_key"])):
        if raw["source_key"] in seen_sources:
            continue
        seen_sources.add(raw["source_key"])
        candidate = f"{raw['kind']}_{id_slug(raw['name'])}"
        object_id = candidate if candidate not in used_ids else f"{candidate}_{stable_suffix(raw['source_key'])}"
        used_ids.add(object_id)
        existing.append(
            {
                "id": object_id,
                "name": raw["name"],
                "kind": raw["kind"],
                "owner_id": raw["owner_id"],
                "aliases": clean_aliases(raw["name"], raw["aliases"], limit=25),
            }
        )
    existing.sort(key=lambda item: item["id"])
    write_json(output / "named_objects.json", existing)
    previous_stats_path = work / "reports" / "named_object_source_stats.json"
    previous = read_json(previous_stats_path) if previous_stats_path.exists() else {}
    previous.update(stats)
    for kind in WIKIDATA_CLASSES:
        if stats.get(f"wikidata_{kind.lower()}", 0):
            previous.pop(f"wikidata_{kind.lower()}_error", None)
    write_json(previous_stats_path, previous)
    return existing, previous


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--matched-epa-cap", type=int, default=50_000)
    parser.add_argument("--unmatched-epa-cap", type=int, default=25_000)
    parser.add_argument("--skip-wikidata", action="store_true")
    parser.add_argument("--reuse-existing-epa", action="store_true")
    args = parser.parse_args()
    if args.reuse_existing_epa:
        if args.skip_wikidata:
            raise SystemExit("--reuse-existing-epa requires Wikidata enrichment")
        records, stats = enrich_existing_with_wikidata(args.work_dir, args.output_dir)
    else:
        records, stats = build_named_objects(
            args.work_dir,
            args.output_dir,
            matched_epa_cap=max(0, args.matched_epa_cap),
            unmatched_epa_cap=max(0, args.unmatched_epa_cap),
            wikidata=not args.skip_wikidata,
        )
    print(json.dumps({"named_objects": len(records), "source_stats": stats}))


if __name__ == "__main__":
    main()
