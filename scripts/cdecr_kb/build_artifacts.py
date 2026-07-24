"""Build recent SEC filing and synthetic earnings-release Artifacts."""

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
    write_json,
)


TARGET_FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "40-F", "S-1", "S-4", "DEF 14A"}
HIGH_VOLUME_FORMS = {"8-K", "6-K"}


class RateLimiter:
    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if delay:
            time.sleep(delay)


def _recent_rows(payload: dict[str, Any], cutoff: date) -> list[dict[str, str]]:
    recent = payload.get("filings", {}).get("recent", {})
    fields = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
    length = max((len(recent.get(field, [])) for field in fields), default=0)
    rows: list[dict[str, str]] = []
    for index in range(length):
        row = {field: str(recent.get(field, [""] * length)[index] or "") for field in fields}
        try:
            filing_date = datetime.strptime(row["filingDate"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if row["form"] in TARGET_FORMS and filing_date >= cutoff:
            rows.append(row)
    rows.sort(key=lambda item: (item["filingDate"], item["accessionNumber"]), reverse=True)
    return rows


def _fetch(cik: str, path: Path, limiter: RateLimiter, cutoff: date) -> tuple[str, str]:
    if path.exists() and path.stat().st_size > 0:
        try:
            cached = read_json(path)
            if not cached.get("error"):
                return cik, "cached"
            if cached.get("error") == "HTTP 404":
                return cik, "cached_not_found"
            path.unlink(missing_ok=True)
        except Exception:
            path.unlink(missing_ok=True)
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    last_error = "unknown"
    for attempt in range(4):
        limiter.wait()
        request = urllib.request.Request(
            url,
            headers={"User-Agent": configured_sec_user_agent(), "Accept": "application/json", "Accept-Encoding": "identity"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
            write_json(path, {"name": payload.get("name"), "filings": _recent_rows(payload, cutoff)})
            return cik, "downloaded"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code == 404:
                write_json(path, {"error": last_error, "filings": []})
                return cik, "not_found"
            if exc.code not in {403, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2 ** attempt)
    write_json(path, {"error": last_error, "filings": []})
    return cik, "error"


def fetch_submissions(ciks: list[str], state: Path, *, workers: int, rate: float, cutoff: date) -> Counter[str]:
    state.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(rate)
    statuses: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch, cik, state / f"{int(cik):010d}.json", limiter, cutoff): cik
            for cik in ciks
        }
        for index, future in enumerate(as_completed(futures), 1):
            _, status = future.result()
            statuses[status] += 1
            if index % 100 == 0 or index == len(ciks):
                print(f"submissions {index}/{len(ciks)} {dict(statuses)}", flush=True)
    return statuses


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_label(period_id: str) -> str:
    match = __import__("re").search(r"(FY\d{4})(?:_(Q[1-4]))?$", period_id)
    if not match:
        return period_id
    return f"{match.group(1)} {match.group(2)}" if match.group(2) else match.group(1)


def build_artifacts(work: Path, output: Path, *, high_volume_cap: int = 5) -> list[dict[str, Any]]:
    maps = read_json(work / "state" / "company_maps.json")
    companies = {item["id"]: item for item in read_json(output / "companies.json")}
    fiscal_periods = read_json(output / "fiscal_periods.json")
    periods_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for period in fiscal_periods:
        periods_by_company[period["company_id"]].append(period)

    state = work / "state" / "submissions"
    artifacts: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    filings_by_company: dict[str, list[dict[str, str]]] = defaultdict(list)
    for cik, company_id in maps["cik_to_company"].items():
        path = state / f"{int(cik):010d}.json"
        if path.exists():
            filings_by_company[company_id].extend(read_json(path).get("filings", []))

    for company_id, filings in sorted(filings_by_company.items()):
        company = companies[company_id]
        company_token = company_id.removeprefix("COMPANY_")
        period_end_to_id = {period["end"]: period["id"] for period in periods_by_company.get(company_id, [])}
        per_form: Counter[str] = Counter()
        for filing in sorted(filings, key=lambda item: (item["filingDate"], item["accessionNumber"]), reverse=True):
            form = filing["form"]
            if form in HIGH_VOLUME_FORMS and per_form[form] >= high_volume_cap:
                continue
            per_form[form] += 1
            accession = filing["accessionNumber"]
            suffix = accession.replace("-", "")[-10:] or id_slug(filing["filingDate"])
            artifact_id = f"ARTIFACT_{company_token}_{id_slug(form)}_{filing['filingDate'].replace('-', '')}_{suffix}"
            if artifact_id in used_ids:
                continue
            used_ids.add(artifact_id)
            name = f"{company['name']} {form} filing on {filing['filingDate']}"
            period_id = period_end_to_id.get(filing.get("reportDate", ""))
            artifacts.append(
                {
                    "id": artifact_id,
                    "name": name,
                    "kind": "SEC_FILING",
                    "owner_id": company_id,
                    "date": filing["filingDate"],
                    "period_id": period_id,
                    "aliases": clean_aliases(name, [accession]),
                }
            )

        dated_filings = [(filing, _parse_date(filing["filingDate"])) for filing in filings]
        q4_ends = {
            period["end"]
            for period in periods_by_company.get(company_id, [])
            if period["id"].endswith("_Q4")
        }
        for period in periods_by_company.get(company_id, []):
            period_end = _parse_date(period["end"])
            if period_end is None or period_end > date.today() or period_end < date.today() - timedelta(days=5 * 366):
                continue
            if period["end"] in q4_ends and not period["id"].endswith("_Q4"):
                continue
            candidates = [
                (filing, filing_date)
                for filing, filing_date in dated_filings
                if filing_date and period_end <= filing_date <= period_end + timedelta(days=150)
            ]
            preferred = [item for item in candidates if item[0]["form"] in HIGH_VOLUME_FORMS]
            selected = min(preferred or candidates, key=lambda item: item[1], default=(None, None))
            filing, release_date = selected
            if release_date is None:
                release_date = period_end + timedelta(days=45)
                if release_date > date.today():
                    continue
            label = _period_label(period["id"])
            artifact_id = f"ARTIFACT_{company_token}_{label.replace(' ', '_')}_EARNINGS"
            if artifact_id in used_ids:
                continue
            used_ids.add(artifact_id)
            name = f"{company['name']} {label} earnings release"
            aliases = [
                f"{company['name']} {label} results",
                f"{company['name']} {label} earnings",
            ]
            artifacts.append(
                {
                    "id": artifact_id,
                    "name": name,
                    "kind": "EARNINGS_RELEASE",
                    "owner_id": company_id,
                    "date": release_date.isoformat(),
                    "period_id": period["id"],
                    "aliases": clean_aliases(name, aliases),
                }
            )
    artifacts.sort(key=lambda item: item["id"])
    write_json(output / "artifacts.json", artifacts)
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--max-companies", type=int)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    maps = read_json(args.work_dir / "state" / "company_maps.json")
    ciks = sorted(maps["cik_to_company"], key=int)
    if args.max_companies:
        ciks = ciks[: args.max_companies]
    cutoff = date.today() - timedelta(days=5 * 366)
    statuses = fetch_submissions(
        ciks,
        args.work_dir / "state" / "submissions",
        workers=max(1, args.workers),
        rate=min(max(args.rate, 0.5), 8.0),
        cutoff=cutoff,
    )
    if args.fetch_only:
        print(json.dumps({"statuses": statuses}))
        return
    artifacts = build_artifacts(args.work_dir, args.output_dir)
    print(json.dumps({"statuses": statuses, "artifacts": len(artifacts)}))


if __name__ == "__main__":
    main()
