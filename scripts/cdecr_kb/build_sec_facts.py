"""Build fiscal periods and enrich units from per-CIK SEC Company Facts.

Raw Company Facts responses are intentionally not retained. Each CIK gets a
small resumable summary file containing only period candidates and unit keys.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from common import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WORK_DIR,
    clean_aliases,
    configured_sec_user_agent,
    id_slug,
    read_json,
    stable_suffix,
    write_json,
)

FORMS = {"10-K", "10-Q", "20-F", "40-F"}
QUARTERS = {"Q1", "Q2", "Q3", "Q4"}
SUMMARY_VERSION = 2


class RateLimiter:
    def __init__(self, rate_per_second: float) -> None:
        self.interval = 1.0 / rate_per_second
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if delay:
            time.sleep(delay)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _extract_summary(payload: dict[str, Any], min_fy: int) -> dict[str, Any]:
    candidates: Counter[tuple[int, str, str, str]] = Counter()
    units: Counter[str] = Counter()
    facts = payload.get("facts", {})
    for taxonomy in facts.values():
        for fact in taxonomy.values():
            for unit_name, observations in fact.get("units", {}).items():
                units[str(unit_name)] += len(observations)
                for observation in observations:
                    form = str(observation.get("form") or "")
                    fp = str(observation.get("fp") or "").upper()
                    fy = observation.get("fy")
                    start = str(observation.get("start") or "")
                    end = str(observation.get("end") or "")
                    if form not in FORMS or fp not in QUARTERS | {"FY"}:
                        continue
                    if not isinstance(fy, int) or fy < min_fy or not start or not end:
                        continue
                    start_date, end_date = _parse_date(start), _parse_date(end)
                    filed_date = _parse_date(str(observation.get("filed") or ""))
                    if (
                        start_date is None
                        or end_date is None
                        or filed_date is None
                        or end_date < start_date
                    ):
                        continue
                    # Comparative facts carried in a current filing inherit the
                    # filing's fy/fp. A bounded filing lag removes those prior-
                    # year periods while retaining normal and foreign filings.
                    filing_lag = (filed_date - end_date).days
                    if not 0 <= filing_lag <= 200:
                        continue
                    duration = (end_date - start_date).days + 1
                    if fp in QUARTERS and not 60 <= duration <= 120:
                        continue
                    if fp == "FY" and not 300 <= duration <= 400:
                        continue
                    candidates[(fy, fp, start, end)] += 1
    compact = [
        {"fy": fy, "fp": fp, "start": start, "end": end, "count": count}
        for (fy, fp, start, end), count in candidates.most_common()
    ]
    return {
        "summary_version": SUMMARY_VERSION,
        "periods": compact,
        "units": dict(units.most_common()),
    }


def _fetch_cik(cik: str, destination: Path, limiter: RateLimiter, min_fy: int) -> tuple[str, str]:
    if destination.exists() and destination.stat().st_size > 0:
        try:
            cached = read_json(destination)
            if cached.get("summary_version") == SUMMARY_VERSION:
                if not cached.get("error"):
                    return cik, "cached"
                if cached.get("error") == "HTTP 404":
                    return cik, "cached_not_found"
            destination.unlink(missing_ok=True)
        except Exception:
            destination.unlink(missing_ok=True)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    last_error = "unknown"
    for attempt in range(4):
        limiter.wait()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": configured_sec_user_agent(),
                "Accept": "application/json",
                # urllib does not transparently decode gzip. Identity also keeps
                # peak memory predictable because json.load streams the socket.
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            summary = _extract_summary(payload, min_fy)
            summary["entity_name"] = payload.get("entityName")
            write_json(destination, summary)
            return cik, "downloaded"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 404:
                write_json(
                    destination,
                    {
                        "summary_version": SUMMARY_VERSION,
                        "error": last_error,
                        "periods": [],
                        "units": {},
                    },
                )
                return cik, "not_found"
            if exc.code not in {403, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2**attempt)
    write_json(
        destination,
        {"summary_version": SUMMARY_VERSION, "error": last_error, "periods": [], "units": {}},
    )
    return cik, "error"


def fetch_all_summaries(
    ciks: list[str],
    summary_dir: Path,
    *,
    workers: int,
    requests_per_second: float,
    min_fy: int,
) -> Counter[str]:
    summary_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(requests_per_second)
    statuses: Counter[str] = Counter()
    total = len(ciks)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _fetch_cik, cik, summary_dir / f"{int(cik):010d}.json", limiter, min_fy
            ): cik
            for cik in ciks
        }
        for index, future in enumerate(as_completed(futures), 1):
            _, status = future.result()
            statuses[status] += 1
            if index % 100 == 0 or index == total:
                print(f"companyfacts {index}/{total} {dict(statuses)}", flush=True)
    return statuses


def _best_periods(summary: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in summary.get("periods", []):
        grouped[(candidate["fy"], candidate["fp"])].append(candidate)
    selected: dict[tuple[int, str], dict[str, Any]] = {}
    for key, candidates in grouped.items():
        # Count first, then prefer dates ending nearer the modal filing period;
        # the compact input is already count-descending, so deterministic date
        # tie-breaking is sufficient.
        selected[key] = sorted(
            candidates, key=lambda item: (-item["count"], item["end"], item["start"])
        )[0]
    return selected


def _aliases(fy: int, fp: str) -> list[str]:
    if fp == "FY":
        return [f"FY{fy}", f"fiscal {fy}", f"fiscal year {fy}", f"full-year fiscal {fy}"]
    quarter = int(fp[1])
    ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth"}[quarter]
    return [
        f"FY{fy} Q{quarter}",
        f"Q{quarter} FY{fy}",
        f"FY{str(fy)[-2:]}-Q{quarter}",
        f"Q{quarter} {fy}",
        f"FQ{quarter} {fy}",
        f"fiscal Q{quarter} {fy}",
        f"fiscal {ordinal} quarter {fy}",
        f"{ordinal} quarter fiscal {fy}",
    ]


def build_fiscal_periods(
    maps: dict[str, Any],
    summary_dir: Path,
    output: Path,
    *,
    max_actual_years: int = 10,
    include_future: bool = True,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    periods: list[dict[str, Any]] = []
    unit_counts: Counter[str] = Counter()
    for cik, company_id in sorted(maps["cik_to_company"].items(), key=lambda item: int(item[0])):
        path = summary_dir / f"{int(cik):010d}.json"
        if not path.exists():
            continue
        summary = read_json(path)
        unit_counts.update({key: int(value) for key, value in summary.get("units", {}).items()})
        selected = _best_periods(summary)
        fiscal_years = sorted({fy for fy, _ in selected}, reverse=True)[:max_actual_years]
        company_token = company_id.removeprefix("COMPANY_")
        actual_by_fy: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
        for fy in sorted(fiscal_years):
            for fp in ["FY", "Q1", "Q2", "Q3"]:
                item = selected.get((fy, fp))
                if item:
                    actual_by_fy[fy][fp] = item
            annual = actual_by_fy[fy].get("FY")
            q3 = actual_by_fy[fy].get("Q3")
            if annual and q3:
                q4_start = (_parse_date(q3["end"]) + timedelta(days=1)).isoformat()  # type: ignore[union-attr]
                q4_end = annual["end"]
                duration = (_parse_date(q4_end) - _parse_date(q4_start)).days + 1  # type: ignore[operator]
                if 60 <= duration <= 130:
                    actual_by_fy[fy]["Q4"] = {"start": q4_start, "end": q4_end, "count": 0}

        for fy in sorted(actual_by_fy):
            for fp in ["FY", "Q1", "Q2", "Q3", "Q4"]:
                item = actual_by_fy[fy].get(fp)
                if not item:
                    continue
                suffix = f"FY{fy}" if fp == "FY" else f"FY{fy}_{fp}"
                name_aliases = _aliases(fy, fp)
                periods.append(
                    {
                        "id": f"COMPANY_{company_token}_{suffix}",
                        "company_id": company_id,
                        "start": item["start"],
                        "end": item["end"],
                        "aliases": name_aliases,
                    }
                )

        if include_future and actual_by_fy:
            source_fy = max(
                (fy for fy, values in actual_by_fy.items() if "FY" in values), default=0
            )
            if source_fy:
                for offset in (1, 2):
                    future_fy = source_fy + offset
                    existing_future = actual_by_fy.get(future_fy, {})
                    for fp in ["FY", "Q1", "Q2", "Q3", "Q4"]:
                        source = actual_by_fy[source_fy].get(fp)
                        if not source or fp in existing_future:
                            continue
                        suffix = f"FY{future_fy}" if fp == "FY" else f"FY{future_fy}_{fp}"
                        periods.append(
                            {
                                "id": f"COMPANY_{company_token}_{suffix}",
                                "company_id": company_id,
                                "start": (
                                    _parse_date(source["start"]) + timedelta(days=364 * offset)
                                ).isoformat(),  # type: ignore[union-attr]
                                "end": (
                                    _parse_date(source["end"]) + timedelta(days=364 * offset)
                                ).isoformat(),  # type: ignore[union-attr]
                                "aliases": _aliases(future_fy, fp),
                            }
                        )
    periods.sort(key=lambda item: item["id"])
    write_json(output / "fiscal_periods.json", periods)
    return periods, unit_counts


def enrich_units(output: Path, unit_counts: Counter[str]) -> int:
    path = output / "units.json"
    units = read_json(path)
    repaired_ids: set[str] = set()
    for item in units:
        base_id = item["id"]
        candidate = base_id
        if candidate in repaired_ids:
            candidate = f"{base_id}_{stable_suffix(item['name'])}"
            serial = 2
            while candidate in repaired_ids:
                candidate = f"{base_id}_{stable_suffix(item['name'])}_{serial}"
                serial += 1
            item["id"] = candidate
        repaired_ids.add(candidate)
    existing_ids = {item["id"] for item in units}
    existing_aliases = {alias for item in units for alias in [item["name"], *item["aliases"]]}
    added = 0
    for source_unit, _ in unit_counts.most_common():
        if not source_unit or source_unit in existing_aliases:
            continue
        unit_id = id_slug(source_unit)
        if unit_id in existing_ids:
            unit_id = f"XBRL_{unit_id}"
        if unit_id in existing_ids:
            unit_id = f"{unit_id}_{stable_suffix(source_unit)}"
        serial = 2
        base_unit_id = unit_id
        while unit_id in existing_ids:
            unit_id = f"{base_unit_id}_{serial}"
            serial += 1
        units.append(
            {
                "id": unit_id,
                "name": source_unit,
                "multiplier": 1,
                "kind": "UNIT",
                "aliases": clean_aliases(source_unit, [source_unit.replace("/", " per ")]),
            }
        )
        existing_ids.add(unit_id)
        existing_aliases.add(source_unit)
        added += 1
    units.sort(key=lambda item: (item["kind"], item["id"]))
    write_json(path, units)
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--max-companies", type=int)
    parser.add_argument("--min-fy", type=int, default=date.today().year - 11)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()

    maps = read_json(args.work_dir / "state" / "company_maps.json")
    ciks = sorted(maps["cik_to_company"], key=int)
    if args.max_companies:
        ciks = ciks[: args.max_companies]
    summary_dir = args.work_dir / "state" / "companyfacts"
    statuses = fetch_all_summaries(
        ciks,
        summary_dir,
        workers=max(1, args.workers),
        requests_per_second=min(max(args.rate, 0.5), 8.0),
        min_fy=args.min_fy,
    )
    if args.fetch_only:
        print(json.dumps({"statuses": statuses}))
        return
    periods, unit_counts = build_fiscal_periods(maps, summary_dir, args.output_dir)
    units_added = enrich_units(args.output_dir, unit_counts)
    print(
        json.dumps(
            {"statuses": statuses, "fiscal_periods": len(periods), "units_added": units_added}
        )
    )


if __name__ == "__main__":
    main()
