"""Build a bounded, finance-news-oriented Place catalog from GeoNames."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from common import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WORK_DIR,
    clean_aliases,
    download,
    id_slug,
    runtime_normalize,
    stable_suffix,
    write_json,
)


GEONAMES_BASE = "https://download.geonames.org/export/dump/"


@dataclass
class PlaceSource:
    geoname_id: str
    name: str
    ascii_name: str = ""
    country: str = ""
    admin1: str = ""
    feature_class: str = ""
    feature_code: str = ""
    population: int = 0
    seed_aliases: list[str] = field(default_factory=list)
    external_aliases: list[tuple[int, str]] = field(default_factory=list)


def _geonames_rows(archive_path: Path, member_name: str) -> Iterable[list[str]]:
    with zipfile.ZipFile(archive_path) as archive, archive.open(member_name) as raw:
        for byte_line in raw:
            yield byte_line.decode("utf-8", errors="replace").rstrip("\n").split("\t")


def _load_countries(path: Path) -> tuple[dict[str, str], dict[str, PlaceSource]]:
    countries: dict[str, str] = {}
    places: dict[str, PlaceSource] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"):
                continue
            row = line.rstrip("\n").split("\t")
            if len(row) < 17:
                continue
            iso, iso3, _, _, name = row[:5]
            geoname_id = row[16]
            if not geoname_id.isdigit():
                continue
            countries[iso] = name
            places[geoname_id] = PlaceSource(
                geoname_id=geoname_id,
                name=name,
                ascii_name=name,
                country=iso,
                feature_class="A",
                feature_code="PCLI",
                seed_aliases=[iso, iso3, f"{name} ({iso})"],
            )
    return countries, places


def _load_admin1(path: Path, countries: dict[str, str], places: dict[str, PlaceSource]) -> dict[str, str]:
    names: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            row = line.rstrip("\n").split("\t")
            if len(row) < 4:
                continue
            code, name, ascii_name, geoname_id = row[:4]
            # GeoNames currently includes an "AD###" template/example row.
            # It is metadata, not a resolvable place identifier.
            if not geoname_id.isdigit():
                continue
            country_code, _, admin_code = code.partition(".")
            names[code] = name
            aliases = [ascii_name, code, f"{name}, {countries.get(country_code, country_code)}"]
            if country_code == "US":
                aliases.extend([admin_code, f"{name}, US", f"{name}, USA"])
            places[geoname_id] = PlaceSource(
                geoname_id=geoname_id,
                name=name,
                ascii_name=ascii_name,
                country=country_code,
                admin1=admin_code,
                feature_class="A",
                feature_code="ADM1",
                seed_aliases=aliases,
            )
    return names


def _add_geoname_row(
    row: list[str],
    places: dict[str, PlaceSource],
    *,
    us_supplement: bool,
) -> None:
    if len(row) < 15:
        return
    geoname_id, name, ascii_name = row[0:3]
    feature_class, feature_code, country, admin1 = row[6], row[7], row[8], row[10]
    try:
        population = int(row[14] or 0)
    except ValueError:
        population = 0
    if us_supplement:
        # cities500 already supplies US populated places >=500. The US file is
        # used only for administrative geographies and smaller real settlements.
        if feature_class == "P" and population < 100:
            return
        if feature_class not in {"A", "P"}:
            return
    elif feature_class != "P":
        return
    existing = places.get(geoname_id)
    if existing:
        if ascii_name and ascii_name != existing.name:
            existing.seed_aliases.append(ascii_name)
        return
    places[geoname_id] = PlaceSource(
        geoname_id=geoname_id,
        name=name,
        ascii_name=ascii_name,
        country=country,
        admin1=admin1,
        feature_class=feature_class,
        feature_code=feature_code,
        population=population,
        seed_aliases=[ascii_name],
    )


def _load_external_aliases(path: Path, places: dict[str, PlaceSource]) -> int:
    kept = 0
    language_priority = {"en": 0, "abbr": 1, "iata": 2, "icao": 3, "post": 4}
    with zipfile.ZipFile(path) as archive, archive.open("alternateNamesV2.txt") as raw:
        for byte_line in raw:
            row = byte_line.decode("utf-8", errors="replace").rstrip("\n").split("\t")
            if len(row) < 8:
                continue
            geoname_id, language, alias = row[1], row[2], row[3]
            place = places.get(geoname_id)
            if place is None or language not in language_priority:
                continue
            historic = row[7] == "1"
            if historic or not alias or len(alias) > 120:
                continue
            preferred = len(row) > 4 and row[4] == "1"
            short = len(row) > 5 and row[5] == "1"
            colloquial = len(row) > 6 and row[6] == "1"
            priority = language_priority[language] * 10 + (0 if preferred else 3) + (0 if short else 1) + (1 if colloquial else 0)
            if len(place.external_aliases) < 40:
                place.external_aliases.append((priority, alias))
                kept += 1
    return kept


def build_places(downloads: Path, output: Path) -> list[dict[str, Any]]:
    country_path = download(GEONAMES_BASE + "countryInfo.txt", downloads / "countryInfo.txt")
    admin1_path = download(GEONAMES_BASE + "admin1CodesASCII.txt", downloads / "admin1CodesASCII.txt")
    cities_path = download(GEONAMES_BASE + "cities500.zip", downloads / "cities500.zip")
    us_path = download(GEONAMES_BASE + "US.zip", downloads / "US.zip")
    aliases_path = download(GEONAMES_BASE + "alternateNamesV2.zip", downloads / "alternateNamesV2.zip", timeout=600)

    countries, sources = _load_countries(country_path)
    admin1_names = _load_admin1(admin1_path, countries, sources)
    for row in _geonames_rows(cities_path, "cities500.txt"):
        _add_geoname_row(row, sources, us_supplement=False)
    for row in _geonames_rows(us_path, "US.txt"):
        _add_geoname_row(row, sources, us_supplement=True)
    _load_external_aliases(aliases_path, sources)

    slug_counts = Counter(id_slug(source.name) for source in sources.values())
    used_ids: set[str] = set()
    records: list[dict[str, Any]] = []
    for source in sorted(sources.values(), key=lambda item: int(item.geoname_id)):
        slug = id_slug(source.name)
        if slug_counts[slug] == 1:
            place_id = f"PLACE_{slug}"
        else:
            context = "_".join(part for part in [source.country, source.admin1] if part)
            candidate = f"PLACE_{slug}_{id_slug(context, 'AREA')}"
            place_id = candidate if candidate not in used_ids else f"{candidate}_{source.geoname_id}"
        if place_id in used_ids:
            place_id = f"{place_id}_{stable_suffix(source.geoname_id)}"
        used_ids.add(place_id)

        contextual: list[str] = []
        country_name = countries.get(source.country, source.country)
        admin_name = admin1_names.get(f"{source.country}.{source.admin1}", "")
        if source.feature_class == "P":
            if admin_name:
                contextual.append(f"{source.name}, {admin_name}")
            if country_name:
                contextual.append(f"{source.name}, {country_name}")
            if source.country == "US" and source.admin1:
                contextual.append(f"{source.name}, {source.admin1}")
        external = [alias for _, alias in sorted(source.external_aliases, key=lambda item: (item[0], len(item[1]), item[1]))]
        aliases = clean_aliases(source.name, [*source.seed_aliases, *contextual, *external], limit=20)
        records.append({"id": place_id, "name": source.name, "aliases": aliases})

    records.sort(key=lambda item: item["id"])
    write_json(output / "places.json", records)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    places = build_places(args.work_dir / "downloads", args.output_dir)
    print(json.dumps({"places": len(places)}))


if __name__ == "__main__":
    main()
