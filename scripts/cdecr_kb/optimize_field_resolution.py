"""Apply the approved Field Resolution v2 catalog cleanup deterministically."""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common import DEFAULT_OUTPUT_DIR, clean_aliases, read_json, write_json

CORE_METRICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("REVENUE", "Revenue", ("revenue", "sales", "top line")),
    ("EPS", "Earnings per share", ("EPS", "earnings per share")),
    ("NET_INCOME", "Net income", ("net income", "net profit")),
    ("CAPEX", "Capital expenditures", ("capex", "capital expenditure", "capital expenditures")),
    ("GROSS_MARGIN", "Gross margin", ("gross margin", "gross profit margin")),
    ("OPERATING_MARGIN", "Operating margin", ("operating margin", "operating profit margin")),
    ("FREE_CASH_FLOW", "Free cash flow", ("FCF", "free cashflow")),
    ("FREE_CASH_FLOW_MARGIN", "Free cash flow margin", ("FCF margin",)),
    ("FREE_CASH_FLOW_YIELD", "Free cash flow yield", ("FCF yield",)),
    ("PRICE_TARGET", "Price target", ("target price", "PT")),
    ("INDEX_LEVEL", "Index level", ("index value", "index price")),
    ("SHARE_PRICE", "Share price", ("stock price", "share price")),
    ("PRICE_CHANGE", "Price change", ("share price change", "stock price change")),
    ("MARKET_CAPITALIZATION", "Market capitalization", ("market cap",)),
    (
        "MARKET_CAPITALIZATION_CHANGE",
        "Market capitalization change",
        ("market cap change",),
    ),
)

PREDICATE_ACTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("PREDICATE_REPORT_METRIC", "report metric", ("report_metric", "reported metric")),
    ("PREDICATE_GUIDE_METRIC", "guide metric", ("guide_metric", "provide metric guidance")),
    (
        "PREDICATE_ANNOUNCE_AGREEMENT",
        "announce agreement",
        ("announce_agreement", "announced an agreement"),
    ),
    ("PREDICATE_SIGN_AGREEMENT", "sign agreement", ("sign_agreement",)),
    (
        "PREDICATE_COMPLETE_AGREEMENT",
        "complete agreement",
        ("complete_agreement", "closed an agreement"),
    ),
    ("PREDICATE_PLAN_ACTION", "plan action", ("plan_action", "planned to")),
    ("PREDICATE_EXPECT_ACTION", "expect action", ("expect_action", "expected to")),
    ("PREDICATE_EXECUTE_ACTION", "execute action", ("execute_action", "executed")),
)

QUARTER_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth"}


def _upsert(
    rows: list[dict[str, Any]],
    identifier: str,
    name: str,
    aliases: tuple[str, ...],
    *,
    kind: str | None = None,
) -> None:
    by_id = {str(row["id"]): row for row in rows}
    row = by_id.get(identifier)
    if row is None:
        row = {"id": identifier, "name": name, "aliases": []}
        if kind is not None:
            row["kind"] = kind
        rows.append(row)
    row["name"] = name
    row["aliases"] = clean_aliases(
        name, [*row.get("aliases", []), *aliases, name.replace(" ", "_")], limit=30
    )


def optimize_metrics(root: Path) -> dict[str, int]:
    path = root / "metrics.json"
    rows = read_json(path)
    duplicate_aliases: list[str] = []
    retained: list[dict[str, Any]] = []
    for row in rows:
        if row["id"] == "XBRL_CUSTOM_REVENUE":
            duplicate_aliases.extend([row["name"], *row.get("aliases", [])])
            continue
        retained.append(row)
    rows = retained
    for identifier, name, aliases in CORE_METRICS:
        extra = tuple(duplicate_aliases) if identifier == "REVENUE" else ()
        _upsert(rows, identifier, name, (*aliases, *extra))
    rows.sort(key=lambda row: row["id"])
    write_json(path, rows)
    return {"records": len(rows), "core_metrics": len(CORE_METRICS)}


def optimize_predicates(root: Path) -> dict[str, int]:
    path = root / "concepts.json"
    rows = read_json(path)
    for row in rows:
        if row.get("kind") != "PREDICATE":
            continue
        row["aliases"] = clean_aliases(
            row["name"],
            [*row.get("aliases", []), row["name"].replace(" ", "_")],
            limit=30,
        )
    for identifier, name, aliases in PREDICATE_ACTIONS:
        _upsert(rows, identifier, name, aliases, kind="PREDICATE")
    rows.sort(key=lambda row: (row.get("kind", ""), row["id"]))
    write_json(path, rows)
    return {
        "records": len(rows),
        "predicates": sum(row.get("kind") == "PREDICATE" for row in rows),
    }


def _period_aliases(fiscal_year: int, quarter: int | None) -> list[str]:
    if quarter is None:
        return [
            f"FY{fiscal_year}",
            f"fiscal {fiscal_year}",
            f"fiscal year {fiscal_year}",
            f"full-year fiscal {fiscal_year}",
        ]
    short_year = str(fiscal_year)[-2:]
    ordinal = QUARTER_WORDS[quarter]
    return [
        f"FY{fiscal_year} Q{quarter}",
        f"Q{quarter} FY{fiscal_year}",
        f"FY{short_year}-Q{quarter}",
        f"Q{quarter} {fiscal_year}",
        f"FQ{quarter} {fiscal_year}",
        f"fiscal Q{quarter} {fiscal_year}",
        f"fiscal {ordinal} quarter {fiscal_year}",
        f"{ordinal} quarter fiscal {fiscal_year}",
    ]


def optimize_fiscal_periods(root: Path) -> dict[str, int]:
    path = root / "fiscal_periods.json"
    rows = read_json(path)
    existing_ids = {str(row["id"]) for row in rows}
    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        match = re.search(r"_FY(20\d{2})(?:_Q([1-4]))?$", row["id"])
        if match is None:
            continue
        fiscal_year = int(match.group(1))
        quarter = int(match.group(2)) if match.group(2) else None
        row["aliases"] = clean_aliases(
            row["id"], [*row.get("aliases", []), *_period_aliases(fiscal_year, quarter)], limit=20
        )
        by_company[row["company_id"]].append(row)
    added = 0
    for company_id, company_rows in by_company.items():
        years = {
            int(match.group(1))
            for row in company_rows
            if (match := re.search(r"_FY(20\d{2})$", row["id"]))
        }
        if not years:
            continue
        current_year = date.today().year
        latest = max((year for year in years if year <= current_year), default=max(years))
        templates = [row for row in company_rows if f"_FY{latest}" in row["id"]]
        for target_year in range(latest + 1, current_year + 3):
            offset = target_year - latest
            for template in templates:
                suffix = template["id"].split(f"_FY{latest}", 1)[1]
                identifier = f"{company_id}_FY{target_year}{suffix}"
                if identifier in existing_ids:
                    continue
                shift = timedelta(days=364 * offset)
                quarter_match = re.search(r"_Q([1-4])$", identifier)
                quarter = int(quarter_match.group(1)) if quarter_match else None
                rows.append(
                    {
                        "id": identifier,
                        "company_id": company_id,
                        "start": (date.fromisoformat(template["start"]) + shift).isoformat(),
                        "end": (date.fromisoformat(template["end"]) + shift).isoformat(),
                        "aliases": _period_aliases(target_year, quarter),
                    }
                )
                existing_ids.add(identifier)
                added += 1
    rows.sort(key=lambda row: row["id"])
    write_json(path, rows)
    return {"records": len(rows), "future_records_added": added}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    print(
        {
            "metrics": optimize_metrics(args.catalog_dir),
            "predicates": optimize_predicates(args.catalog_dir),
            "fiscal_periods": optimize_fiscal_periods(args.catalog_dir),
        }
    )


if __name__ == "__main__":
    main()
