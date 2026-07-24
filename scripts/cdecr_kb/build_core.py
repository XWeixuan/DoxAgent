"""Build Company, Institution, and Financial Instrument catalogs."""

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
    configured_sec_user_agent,
    display_name,
    download,
    id_slug,
    runtime_normalize,
    stable_suffix,
    strip_corporate_suffix,
    unique_id,
    write_json,
)


SEC_TICKERS = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_MUTUAL_FUNDS = "https://www.sec.gov/files/company_tickers_mf.json"
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_OTHER = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_RIA_PAGE = (
    "https://www.sec.gov/data-research/sec-markets-data/"
    "information-about-registered-investment-advisers-exempt-reporting-advisers"
)
SEC_BD_PAGE = (
    "https://www.sec.gov/foia/frequently-requested-documents/"
    "company-information-about-active-broker-dealers"
)
FDIC_INSTITUTIONS = (
    "https://s3-us-gov-west-1.amazonaws.com/"
    "cg-2e5c99a6-e282-42bf-9844-35f5430338a5/downloads/institutions.csv"
)

MANUAL_INSTITUTIONS: list[tuple[str, list[str]]] = [
    ("U.S. Securities and Exchange Commission", ["SEC", "US SEC", "Securities and Exchange Commission"]),
    ("Federal Reserve", ["Fed", "Federal Reserve System", "U.S. Federal Reserve"]),
    ("Federal Deposit Insurance Corporation", ["FDIC"]),
    ("Financial Industry Regulatory Authority", ["FINRA"]),
    ("Commodity Futures Trading Commission", ["CFTC"]),
    ("Office of the Comptroller of the Currency", ["OCC"]),
    ("Consumer Financial Protection Bureau", ["CFPB"]),
    ("Financial Accounting Standards Board", ["FASB"]),
    ("Public Company Accounting Oversight Board", ["PCAOB"]),
    ("Nasdaq", ["NASDAQ", "Nasdaq Stock Market"]),
    ("New York Stock Exchange", ["NYSE"]),
    ("Cboe Global Markets", ["CBOE", "Chicago Board Options Exchange"]),
    ("S&P Global Ratings", ["S&P Ratings", "Standard & Poor's Ratings"]),
    ("Moody's Ratings", ["Moody's", "Moody's Investors Service"]),
    ("Fitch Ratings", ["Fitch"]),
    ("Reuters", ["Thomson Reuters", "Reuters News"]),
    ("Bloomberg", ["Bloomberg News", "Bloomberg Intelligence"]),
    ("Dow Jones Newswires", ["Dow Jones"]),
    ("The Wall Street Journal", ["WSJ", "Wall Street Journal"]),
    ("Financial Times", ["FT"]),
    ("CNBC", []),
    ("MarketWatch", []),
    ("Associated Press", ["AP", "AP News"]),
    ("Goldman Sachs", ["Goldman", "Goldman Sachs Research"]),
    ("JPMorgan", ["J.P. Morgan", "JP Morgan", "JPMorgan Chase"]),
    ("Bank of America", ["BofA", "BofA Securities"]),
    ("Morgan Stanley", ["Morgan Stanley Research"]),
    ("Citigroup", ["Citi", "Citi Research"]),
    ("UBS", ["UBS Research"]),
    ("Bernstein", ["AllianceBernstein", "Sanford C. Bernstein"]),
]

MACRO_INSTRUMENTS: list[dict[str, Any]] = [
    {"id": "INSTRUMENT_SP500", "name": "S&P 500 Index", "ticker": "SPX", "aliases": ["S&P 500", "SP500", "SPX"]},
    {"id": "INSTRUMENT_NASDAQ_COMPOSITE", "name": "Nasdaq Composite Index", "ticker": "IXIC", "aliases": ["Nasdaq Composite", "IXIC"]},
    {"id": "INSTRUMENT_NASDAQ_100", "name": "Nasdaq-100 Index", "ticker": "NDX", "aliases": ["Nasdaq 100", "NDX"]},
    {"id": "INSTRUMENT_DOW_JONES", "name": "Dow Jones Industrial Average", "ticker": "DJI", "aliases": ["Dow", "DJIA", "Dow Jones"]},
    {"id": "INSTRUMENT_RUSSELL_2000", "name": "Russell 2000 Index", "ticker": "RUT", "aliases": ["Russell 2000", "RUT"]},
    {"id": "INSTRUMENT_VIX", "name": "CBOE Volatility Index", "ticker": "VIX", "aliases": ["VIX", "volatility index", "fear index"]},
    {"id": "INSTRUMENT_WTI_CRUDE", "name": "WTI crude oil", "ticker": "CL", "aliases": ["WTI", "West Texas Intermediate", "US crude"]},
    {"id": "INSTRUMENT_BRENT_CRUDE", "name": "Brent crude oil", "ticker": "BZ", "aliases": ["Brent", "Brent crude"]},
    {"id": "INSTRUMENT_GOLD", "name": "Gold", "ticker": "XAU", "aliases": ["gold bullion", "XAU"]},
    {"id": "INSTRUMENT_SILVER", "name": "Silver", "ticker": "XAG", "aliases": ["silver bullion", "XAG"]},
    {"id": "INSTRUMENT_US10Y", "name": "U.S. 10-Year Treasury note", "ticker": "US10Y", "aliases": ["10-year Treasury", "10Y Treasury", "US 10-year"]},
    {"id": "INSTRUMENT_BITCOIN", "name": "Bitcoin", "ticker": "BTC", "aliases": ["BTC", "Bitcoin cryptocurrency"]},
    {"id": "INSTRUMENT_ETHEREUM", "name": "Ethereum", "ticker": "ETH", "aliases": ["ETH", "Ether"]},
]


def _latest_download_from_page(page_path: Path, extensions: tuple[str, ...]) -> str:
    source = page_path.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r'href=["\']([^"\']+)["\']', source, re.IGNORECASE):
        href = html.unescape(match.group(1))
        if href.lower().endswith(extensions):
            return urllib.parse.urljoin("https://www.sec.gov", href)
    raise RuntimeError(f"No {extensions!r} download found in {page_path}")


def _pipe_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="|")


def _company_name_from_security(name: str) -> str:
    value = re.sub(
        r"\s+-\s+(Common Stock|Class [A-Z] Common Stock|Ordinary Shares?|American Depositary Shares?|"
        r"Depositary Shares?|Preferred Stock|Warrants?|Units?|Rights?)\b.*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return strip_corporate_suffix(value)


def build_companies(downloads: Path, output: Path, state: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sec_path = download(SEC_TICKERS, downloads / "company_tickers_exchange.json", sec=True)
    nasdaq_path = download(NASDAQ_LISTED, downloads / "nasdaqlisted.txt")
    other_path = download(NASDAQ_OTHER, downloads / "otherlisted.txt")
    payload = json.loads(sec_path.read_text(encoding="utf-8"))
    fields = payload["fields"]
    rows = [dict(zip(fields, row)) for row in payload["data"]]

    by_cik: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("ticker"):
            by_cik[int(row["cik"])].append(row)

    bases = [strip_corporate_suffix(values[0]["name"]) for values in by_cik.values()]
    first_tokens = Counter(
        base.split()[0].casefold()
        for base in bases
        if len(base.split()) > 1 and len(base.split()[0]) >= 4
    )
    used_ids: dict[str, str] = {}
    companies: list[dict[str, Any]] = []
    ticker_to_company: dict[str, str] = {}
    cik_to_company: dict[str, str] = {}
    exchange_rank = {"Nasdaq": 0, "NYSE": 1, "NYSE American": 2, "Cboe BZX": 3}

    for cik, values in sorted(by_cik.items()):
        values.sort(key=lambda row: (exchange_rank.get(str(row.get("exchange")), 9), len(row["ticker"]), row["ticker"]))
        primary = values[0]
        tickers = sorted({str(row["ticker"]).strip().upper() for row in values})
        official = str(primary["name"]).strip()
        name = display_name(strip_corporate_suffix(official))
        company_id = unique_id("COMPANY", primary["ticker"], str(cik), used_ids)
        alias_candidates: list[str] = [official, *tickers]
        words = name.split()
        if len(words) > 1 and len(words[0]) >= 4 and first_tokens[words[0].casefold()] == 1:
            alias_candidates.append(words[0])
        companies.append(
            {
                "id": company_id,
                "name": name,
                "ticker": primary["ticker"].upper(),
                "aliases": clean_aliases(name, alias_candidates),
            }
        )
        cik_to_company[str(cik)] = company_id
        for ticker in tickers:
            ticker_to_company[ticker] = company_id

    # SEC already covers nearly all active issuers. Add only non-test, non-ETF
    # exchange rows whose ticker is absent, avoiding fund/security names as firms.
    listed_rows = list(_pipe_rows(nasdaq_path)) + list(_pipe_rows(other_path))
    for row in listed_rows:
        ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
        security_name = (row.get("Security Name") or "").strip()
        if not ticker or ticker.startswith("File Creation Time") or row.get("Test Issue") == "Y":
            continue
        if ticker in ticker_to_company or row.get("ETF") == "Y":
            continue
        if re.search(r"\b(fund|etf|trust|notes?|warrants?|rights?|units?)\b", security_name, re.I):
            continue
        name = display_name(_company_name_from_security(security_name))
        company_id = unique_id("COMPANY", ticker, f"nasdaq:{ticker}", used_ids)
        companies.append(
            {"id": company_id, "name": name, "ticker": ticker, "aliases": clean_aliases(name, [security_name, ticker])}
        )
        ticker_to_company[ticker] = company_id

    companies.sort(key=lambda item: item["id"])
    write_json(output / "companies.json", companies)
    maps = {"ticker_to_company": ticker_to_company, "cik_to_company": cik_to_company}
    write_json(state / "company_maps.json", maps)
    return companies, maps


def build_instruments(downloads: Path, output: Path, maps: dict[str, Any]) -> list[dict[str, Any]]:
    nasdaq_path = downloads / "nasdaqlisted.txt"
    other_path = downloads / "otherlisted.txt"
    mf_path = download(SEC_MUTUAL_FUNDS, downloads / "company_tickers_mf.json", sec=True)
    companies = json.loads((output / "companies.json").read_text(encoding="utf-8"))
    company_by_id = {item["id"]: item for item in companies}
    ticker_to_company = maps["ticker_to_company"]
    used_ids: dict[str, str] = {}
    by_ticker: dict[str, dict[str, Any]] = {}

    for ticker, company_id in ticker_to_company.items():
        company = company_by_id[company_id]
        instrument_id = unique_id("INSTRUMENT", ticker, f"equity:{ticker}", used_ids)
        name = f"{company['name']} stock"
        by_ticker[ticker] = {
            "id": instrument_id,
            "name": name,
            "ticker": ticker,
            "issuer_id": company_id,
            "aliases": clean_aliases(name, [ticker, f"{ticker} stock", f"{company['name']} shares", f"{ticker} shares"]),
        }

    for row in list(_pipe_rows(nasdaq_path)) + list(_pipe_rows(other_path)):
        ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
        security_name = (row.get("Security Name") or "").strip()
        if not ticker or ticker.startswith("FILE CREATION TIME") or row.get("Test Issue") == "Y":
            continue
        issuer_id = ticker_to_company.get(ticker)
        if ticker in by_ticker:
            by_ticker[ticker]["aliases"] = clean_aliases(
                by_ticker[ticker]["name"], [*by_ticker[ticker]["aliases"], security_name]
            )
            continue
        name = security_name or f"{ticker} security"
        instrument_id = unique_id("INSTRUMENT", ticker, f"listed:{ticker}", used_ids)
        entry: dict[str, Any] = {
            "id": instrument_id,
            "name": name,
            "ticker": ticker,
            "issuer_id": issuer_id,
            "aliases": clean_aliases(name, [ticker, f"{ticker} shares"]),
        }
        by_ticker[ticker] = entry

    mf_payload = json.loads(mf_path.read_text(encoding="utf-8"))
    mf_fields = mf_payload.get("fields", [])
    for raw in mf_payload.get("data", []):
        row = dict(zip(mf_fields, raw))
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
        if not ticker or ticker in by_ticker:
            continue
        name = str(row.get("className") or row.get("seriesName") or f"{ticker} mutual fund").strip()
        instrument_id = unique_id("INSTRUMENT", ticker, f"mutual-fund:{ticker}", used_ids)
        by_ticker[ticker] = {
            "id": instrument_id,
            "name": name,
            "ticker": ticker,
            "issuer_id": None,
            "aliases": clean_aliases(name, [ticker, str(row.get("seriesName") or "")]),
        }

    instruments = list(by_ticker.values())
    existing_ids = {item["id"] for item in instruments}
    for raw in MACRO_INSTRUMENTS:
        entry = dict(raw)
        if entry["id"] in existing_ids:
            entry["id"] += "_MACRO"
        entry["issuer_id"] = None
        entry["aliases"] = clean_aliases(entry["name"], entry["aliases"])
        instruments.append(entry)
    instruments.sort(key=lambda item: item["id"])
    write_json(output / "instruments.json", instruments)
    return instruments


class InstitutionAccumulator:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def add(self, name: str, aliases: Iterable[str] = ()) -> None:
        raw_name = " ".join(str(name or "").split()).strip(" ,")
        name = display_name(raw_name)
        if not name or len(name) < 2:
            return
        key = runtime_normalize(name)
        if not key:
            return
        record = self.records.setdefault(key, {"name": name, "aliases": []})
        candidates = [raw_name, name, *aliases]
        record["aliases"] = clean_aliases(record["name"], [*record["aliases"], *candidates], limit=40)

    def add_pair(self, primary: str, legal: str) -> None:
        primary = " ".join(str(primary or "").split())
        legal = " ".join(str(legal or "").split())
        name = primary or legal
        if name:
            self.add(name, [legal, strip_corporate_suffix(name), strip_corporate_suffix(legal)])


def _parse_ria_zip(path: Path, accumulator: InstitutionAccumulator) -> int:
    count = 0
    with zipfile.ZipFile(path) as archive:
        csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(csv_name) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                accumulator.add_pair(row.get("Primary Business Name", ""), row.get("Legal Name", ""))
                count += 1
    return count


def _query_wikidata_institutions(accumulator: InstitutionAccumulator, cache: Path) -> int:
    """Fetch bounded, well-known institution classes; failure is non-fatal."""

    classes = {
        "Q66344": 2500,   # central bank
        "Q11691": 2500,   # stock exchange
        "Q471786": 2500,  # credit rating agency
        "Q192283": 2500,  # news agency
        "Q31855": 3000,   # research institute
    }
    total = 0
    cache.mkdir(parents=True, exist_ok=True)
    for class_id, limit in classes.items():
        target = cache / f"institution_{class_id}.json"
        if target.exists():
            payload = json.loads(target.read_text(encoding="utf-8"))
        else:
            query = f"""
SELECT ?item ?itemLabel (GROUP_CONCAT(DISTINCT ?alias; separator=\"|\") AS ?aliases)
WHERE {{
  ?item wdt:P31/wdt:P279* wd:{class_id} .
  ?item rdfs:label ?itemLabel . FILTER(LANG(?itemLabel) = \"en\")
  OPTIONAL {{ ?item skos:altLabel ?alias . FILTER(LANG(?alias) = \"en\") }}
}}
GROUP BY ?item ?itemLabel
LIMIT {limit}
"""
            url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "json"})
            request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/sparql-results+json"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    payload = json.load(response)
                write_json(target, payload)
            except Exception as exc:  # bounded enrichment should not block core financial institutions
                print(f"WARN Wikidata {class_id} skipped: {exc}")
                continue
        for binding in payload.get("results", {}).get("bindings", []):
            name = binding.get("itemLabel", {}).get("value", "")
            aliases = binding.get("aliases", {}).get("value", "").split("|")
            accumulator.add(name, aliases)
            total += 1
    return total


def build_institutions(downloads: Path, output: Path, state: Path, *, wikidata: bool) -> list[dict[str, Any]]:
    ria_page = download(SEC_RIA_PAGE, downloads / "ria.html", sec=True)
    bd_page = download(SEC_BD_PAGE, downloads / "active_broker_dealers.html", sec=True)
    ria_url = _latest_download_from_page(ria_page, (".zip",))
    bd_url = _latest_download_from_page(bd_page, (".txt",))
    ria_path = download(ria_url, downloads / "ria_current.zip", sec=True)
    bd_path = download(bd_url, downloads / "bd_current.txt", sec=True)
    fdic_path = download(FDIC_INSTITUTIONS, downloads / "institutions.csv")

    acc = InstitutionAccumulator()
    for name, aliases in MANUAL_INSTITUTIONS:
        acc.add(name, aliases)
    _parse_ria_zip(ria_path, acc)

    with fdic_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("ACTIVE", "1")).strip() not in {"1", "Y", "TRUE", "True"}:
                continue
            name = row.get("NAME", "")
            acc.add(name, [strip_corporate_suffix(name)])

    with bd_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) >= 2 and row[1].strip():
                name = row[1].strip()
                acc.add(name, [strip_corporate_suffix(name)])

    if wikidata:
        _query_wikidata_institutions(acc, state / "wikidata")

    used_ids: dict[str, str] = {}
    institutions: list[dict[str, Any]] = []
    for key, raw in sorted(acc.records.items()):
        name = raw["name"]
        institution_id = unique_id("INSTITUTION", strip_corporate_suffix(name), key, used_ids)
        institutions.append(
            {"id": institution_id, "name": name, "aliases": clean_aliases(name, raw["aliases"], limit=40)}
        )
    institutions.sort(key=lambda item: item["id"])
    write_json(output / "institutions.json", institutions)
    return institutions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-wikidata", action="store_true")
    args = parser.parse_args()
    downloads = args.work_dir / "downloads"
    state = args.work_dir / "state"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    companies, maps = build_companies(downloads, args.output_dir, state)
    instruments = build_instruments(downloads, args.output_dir, maps)
    institutions = build_institutions(downloads, args.output_dir, state, wikidata=not args.skip_wikidata)
    print(json.dumps({"companies": len(companies), "instruments": len(instruments), "institutions": len(institutions)}))


if __name__ == "__main__":
    main()
