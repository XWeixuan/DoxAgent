"""Build the best-effort Person catalog from SEC insiders and Wikidata."""

from __future__ import annotations

import argparse
import csv
import html
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
    download,
    id_slug,
    read_json,
    runtime_normalize,
    stable_suffix,
    unique_id,
    write_json,
)


INSIDER_PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
ORG_WORDS = re.compile(r"\b(?:LLC|L\.L\.C|LP|L\.P|INC|CORP|CORPORATION|TRUST|FUND|PARTNERS|HOLDINGS)\b", re.I)
SUFFIXES = {"JR", "SR", "II", "III", "IV"}


def _insider_links(page: Path, years: int = 5) -> list[tuple[str, str]]:
    source = page.read_text(encoding="utf-8", errors="ignore")
    found: list[tuple[int, int, str]] = []
    for match in re.finditer(r'href=["\']([^"\']+(\d{4})q([1-4])_form345\.zip)["\']', source, re.I):
        href, year, quarter = html.unescape(match.group(1)), int(match.group(2)), int(match.group(3))
        found.append((year, quarter, urllib.parse.urljoin("https://www.sec.gov", href)))
    found = sorted(set(found), reverse=True)
    if not found:
        raise RuntimeError("No SEC insider quarterly ZIP links found")
    newest_index = found[0][0] * 4 + found[0][1]
    selected = [item for item in found if newest_index - (item[0] * 4 + item[1]) < years * 4]
    return [(f"{year}q{quarter}", url) for year, quarter, url in sorted(selected)]


def _person_name(raw: str) -> str:
    value = " ".join(str(raw or "").replace(",", " ").split()).strip()
    if not value:
        return ""
    tokens = value.split()
    suffix = ""
    if tokens and tokens[-1].rstrip(".").upper() in SUFFIXES:
        suffix = tokens.pop().title().rstrip(".")
    # SEC owner names are conventionally LAST FIRST MIDDLE. Preserve the raw
    # spelling as an alias and produce natural English order as canonical name.
    if len(tokens) >= 2 and value.upper() == value:
        tokens = [*tokens[1:], tokens[0]]
    name = " ".join(token.title() if token.isupper() else token for token in tokens)
    return f"{name} {suffix}".strip()


class PersonAccumulator:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str | None], dict[str, Any]] = {}

    def add(self, name: str, org_id: str | None, aliases: Iterable[str]) -> None:
        name = " ".join(str(name or "").split()).strip(" ,")
        if len(name.split()) < 2 or ORG_WORDS.search(name):
            return
        key = (runtime_normalize(name), org_id)
        if not key[0]:
            return
        record = self.records.setdefault(key, {"name": name, "org_id": org_id, "aliases": []})
        record["aliases"] = clean_aliases(record["name"], [*record["aliases"], *aliases], limit=15)


def _parse_insider_zip(path: Path, company_by_cik: dict[str, str], acc: PersonAccumulator) -> int:
    with zipfile.ZipFile(path) as archive:
        with archive.open("SUBMISSION.tsv") as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
            accession_to_org = {
                row["ACCESSION_NUMBER"]: company_by_cik.get(str(int(row["ISSUERCIK"])))
                for row in csv.DictReader(handle, delimiter="\t")
                if row.get("ISSUERCIK", "").strip().isdigit()
            }
        count = 0
        with archive.open("REPORTINGOWNER.tsv") as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                relationship = str(row.get("RPTOWNER_RELATIONSHIP") or "").casefold()
                if "officer" not in relationship and "director" not in relationship:
                    continue
                raw_name = str(row.get("RPTOWNERNAME") or "").strip()
                name = _person_name(raw_name)
                org_id = accession_to_org.get(str(row.get("ACCESSION_NUMBER") or ""))
                if not org_id:
                    continue
                aliases = [raw_name]
                words = name.split()
                if words:
                    aliases.append(words[-1].rstrip(","))
                if len(words) >= 2 and len(words[0]) > 2:
                    aliases.append(words[0])
                acc.add(name, org_id, aliases)
                count += 1
    return count


def _org_lookup(output: Path) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for filename in ["companies.json", "institutions.json"]:
        for item in read_json(output / filename):
            for alias in [item["name"], *item["aliases"]]:
                key = runtime_normalize(alias)
                if key and key not in lookup:
                    lookup[key] = item["id"]
    return lookup


def _wikidata_people(acc: PersonAccumulator, output: Path, cache: Path) -> int:
    occupations = [
        "chief executive officer",
        "chief financial officer",
        "financial analyst",
        "investment analyst",
        "government official",
        "central banker",
        "spokesperson",
    ]
    org_lookup = _org_lookup(output)
    cache.mkdir(parents=True, exist_ok=True)
    total = 0
    for occupation in occupations:
        target = cache / f"people_{id_slug(occupation).lower()}.json"
        if target.exists():
            payload = read_json(target)
        else:
            query = f"""
SELECT ?person ?personLabel ?employerLabel
       (GROUP_CONCAT(DISTINCT ?alias; separator=\"|\") AS ?aliases)
WHERE {{
  ?occupation rdfs:label \"{occupation}\"@en .
  ?person wdt:P106 ?occupation ; rdfs:label ?personLabel .
  FILTER(LANG(?personLabel) = \"en\")
  OPTIONAL {{ ?person skos:altLabel ?alias . FILTER(LANG(?alias) = \"en\") }}
  OPTIONAL {{ ?person wdt:P108 ?employer . ?employer rdfs:label ?employerLabel . FILTER(LANG(?employerLabel) = \"en\") }}
}}
GROUP BY ?person ?personLabel ?employerLabel
LIMIT 2500
"""
            url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
            request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/sparql-results+json"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = json.load(response)
                write_json(target, payload)
            except Exception as exc:
                print(f"WARN Wikidata people {occupation!r} skipped: {exc}", flush=True)
                continue
        for binding in payload.get("results", {}).get("bindings", []):
            name = binding.get("personLabel", {}).get("value", "")
            employer = binding.get("employerLabel", {}).get("value", "")
            org_id = org_lookup.get(runtime_normalize(employer))
            aliases = binding.get("aliases", {}).get("value", "").split("|")
            acc.add(name, org_id, aliases)
            total += 1
    return total


def build_persons(work: Path, output: Path, *, wikidata: bool = True) -> list[dict[str, Any]]:
    downloads = work / "downloads"
    page = download(INSIDER_PAGE, downloads / "insider.html", sec=True)
    links = _insider_links(page, years=5)
    maps = read_json(work / "state" / "company_maps.json")
    company_by_cik = maps["cik_to_company"]
    acc = PersonAccumulator()
    for label, url in links:
        path = download(url, downloads / f"{label}_form345.zip", sec=True, timeout=600)
        count = _parse_insider_zip(path, company_by_cik, acc)
        print(f"insiders {label}: {count}", flush=True)
    if wikidata:
        _wikidata_people(acc, output, work / "state" / "wikidata")

    used_ids: dict[str, str] = {}
    people: list[dict[str, Any]] = []
    surname_counts = Counter(record["name"].split()[-1].casefold() for record in acc.records.values())
    single_alias_counts = Counter(
        runtime_normalize(alias)
        for record in acc.records.values()
        for alias in record["aliases"]
        if len(runtime_normalize(alias).split()) == 1
    )
    for (normalized_name, org_id), record in sorted(
        acc.records.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        uniqueness_key = f"{normalized_name}|{org_id or ''}"
        person_id = unique_id("PERSON", record["name"], uniqueness_key, used_ids)
        aliases = record["aliases"]
        surname = record["name"].split()[-1]
        if surname_counts[surname.casefold()] > 1:
            aliases = [alias for alias in aliases if runtime_normalize(alias) != runtime_normalize(surname)]
        aliases = [
            alias
            for alias in aliases
            if len(runtime_normalize(alias).split()) != 1
            or single_alias_counts[runtime_normalize(alias)] == 1
        ]
        people.append({"id": person_id, "name": record["name"], "org_id": org_id, "aliases": clean_aliases(record["name"], aliases)})
    people.sort(key=lambda item: item["id"])
    write_json(output / "persons.json", people)
    return people


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-wikidata", action="store_true")
    args = parser.parse_args()
    people = build_persons(args.work_dir, args.output_dir, wikidata=not args.skip_wikidata)
    print(json.dumps({"persons": len(people)}))


if __name__ == "__main__":
    main()
