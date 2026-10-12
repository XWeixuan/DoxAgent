"""Produce reviewed earnings exclusions and enrich unfilled intentions from saved prices."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "revised_export"
PRICES = ROOT.parent / "w3_trade_intent_winrate_20261010" / "results"

# Manually reviewed against source_messages and W3 expert_trade. Stable case IDs,
# rather than a calendar-day filter, preserve unrelated events on release day.
DIRECT = """
76daab2721144507adea0864992665e6 1487b1a263604dc48a012ee36208649d
97556cf8a95f48a894e069e51abe3003 c7ff51b1caf2466b9da87dddbfa0a9b3
b93e4a1e9acf4fc39398b8538bd33bbc d932a7af4c8c479eb848ced3fe153a60
969d4bfbe2be46d1a67f334860731dd2 dae80fcc0e85433a974a1be38d9aa7c9
81382a4d64df496c87a4dcd8d02e5327 c6da7907ff384cc1abe2dfe9c5864674
14623f83899b4c8fbb66425095cb7852 fbe49110a00d469ab64e65d48ecb1797
6fcd239435c1444489abad0b2345a6fa e7748b15cb774bbb995ec088f911d947
b1fee01319f849f0b843ff5bbcc50bd4 e08f25fa32e346ffa7409ff88d5f08a1
c8dd5fc9989f485f894f86d895f9f13e 3579f7c17dd74fa5884057e630a03f18
6fb522f8a460499884be7c2412495616 d433f8989c27400db445b7abae50be93
e2a56aa848914a749454fb0f00fb5dac b9d7e5c059f9453da8215a6616cb57af
e708b912f90345ecab56f1b6c453f298 7ba6774785aa4dcfb454f8a569be059b
6b461942358e4d30a647650c5b09516c 052e1d974d504de6bc9aea2278d8e85b
89fed837b7f143cfa9a94c502effba1a ef54c553351241fc9488f1deed91e0f6
c9353f9848c545cea4c046fcb58c4f98
""".split()
FOLLOWUP = {
    "c4aac6c4a449459bbccfc631526b734b": "次日拆解本次财报的收入、ASP、出货与产品结构",
    "d5b51cd84fad41b8a14a3253387dfe4d": "正文明确将 NVHBM 产品经济性披露归于 9 月 30 日财报发布活动",
    "b4052481d40b4d78862e0f460ceafacf": "解读本次财报及电话会的毛利率指引与管理层解释",
    "b7e889fed45e44b6b178e1a988fc3f78": "W3 依据财报后 10 月 1 日券商研报上调 FY2027 EPS 预测产生意图",
}
EXCLUDED = {"case_" + case: "本次财报结果、指引、电话会披露或其当日解读" for case in DIRECT}
EXCLUDED.update({"case_" + case: reason for case, reason in FOLLOWUP.items()})


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    paths = sorted((ROOT / "export").glob("*.json"))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    originals = [row for path in paths for row in read(path)["records"]]
    price_rows = [row for path in sorted(PRICES.glob("*.json")) for row in read(path)["records"]]
    prices = {row["intent_id"]: row for row in price_rows}
    assert len(originals) == len(prices) == 77
    assert {r["intent_id"] for r in originals} == set(prices)
    assert len(EXCLUDED) == 33 and set(EXCLUDED) <= {r["case_id"] for r in originals if r["ticker"] == "MU"}
    kept = [copy.deepcopy(row) for row in originals if row["case_id"] not in EXCLUDED]
    assert len(kept) == 44
    for row in kept:
        if row["trade_result"]["entry"]:
            continue
        price = prices[row["intent_id"]]
        estimate = {
            "state": "ESTIMATED" if price["classification"] != "unpriceable" else "UNAVAILABLE",
            "basis": "saved IBKR historical TRADES ticks; intention-time entry and 15:30 America/New_York cycle exit",
            "entry_reference_price_usd": price.get("entry_price_usd"),
            "exit_reference_price_usd": price.get("exit_price_usd"),
            "entry_target_utc": price["entry_at_utc"],
            "cycle_end_utc": price["cycle_end_utc"],
            "cycle_end_et": price["cycle_end_et"],
            "direction": price["direction"],
            "return_pct": price.get("return_pct"),
            "pnl_usd_per_share": None,
            "classification": price["classification"],
            "estimated_total_pnl_usd": None,
            "total_pnl_unavailable_reason": "No simulated position quantity was specified; use percent return and USD per share.",
            "entry_evidence": price["entry_evidence"],
            "exit_evidence": price["exit_evidence"],
            "pricing_unavailable_reason": None,
            "cost_assumptions": "No slippage, bid-ask spread, fees or financing costs.",
        }
        if estimate["state"] == "ESTIMATED":
            sign = Decimal(1) if price["direction"] == "LONG" else Decimal(-1)
            estimate["pnl_usd_per_share"] = str((sign * (Decimal(price["exit_price_usd"]) - Decimal(price["entry_price_usd"]))).quantize(Decimal("0.0001")))
        else:
            estimate["pricing_unavailable_reason"] = "No historical trade within 60 seconds after intention; next returned trade is hours later at 04:00 ET."
        row["estimated_pnl"] = estimate

    DEST.mkdir(exist_ok=True)
    window = read(paths[0])["window"]
    output_files = []
    for ticker in sorted({r["ticker"] for r in kept}):
        subset = [r for r in kept if r["ticker"] == ticker]
        for start in range(0, len(subset), 5):
            name = f"{ticker}_{start // 5 + 1:02d}.json"
            write(DEST / name, {"format": "doxagent.w3-trade-intent-review.v2", "ticker": ticker,
                               "window": window, "part": start // 5 + 1,
                               "record_count": len(subset[start:start + 5]), "records": subset[start:start + 5]})
            output_files.append(name)

    def stats(rows: list[dict]) -> dict:
        counts = Counter(prices[r["intent_id"]]["classification"] for r in rows)
        priced = len(rows) - counts["unpriceable"]
        return {"total": len(rows), "win": counts["win"], "flat": counts["flat"],
                "loss": counts["loss"], "unpriceable": counts["unpriceable"],
                "priced_win_rate_pct": round(100 * counts["win"] / priced, 2),
                "all_intent_win_rate_bounds_pct": [round(100 * counts["win"] / len(rows), 2),
                    round(100 * (counts["win"] + counts["unpriceable"]) / len(rows), 2)]}

    summary = {"created_at_utc": datetime.now(UTC).isoformat(), "original_count": 77,
               "excluded_mu_earnings_count": 33, "retained_count": len(kept),
               "actual_entry_count": sum(bool(r["trade_result"]["entry"]) for r in kept),
               "unfilled_count": sum("estimated_pnl" in r for r in kept),
               "estimated_pnl_count": sum(r.get("estimated_pnl", {}).get("state") == "ESTIMATED" for r in kept),
               "unavailable_estimate_count": sum(r.get("estimated_pnl", {}).get("state") == "UNAVAILABLE" for r in kept),
               "by_ticker": {ticker: stats([r for r in kept if r["ticker"] == ticker]) for ticker in sorted({r["ticker"] for r in kept})},
               "all": stats(kept), "record_files": output_files}
    write(DEST / "summary.json", summary)
    write(DEST / "filter_manifest.json", {"event": "MU FY2026 Q4 earnings released 2026-09-30 ET",
          "rule": "Exclude trades whose W3 catalyst is this earnings release, call, or interpretation/revision derived from it; retain independent industry events and pre-release analyst forecasts.",
          "source_sha256": hashes, "excluded_case_ids": sorted(EXCLUDED),
          "retained_mu_case_ids": [r["case_id"] for r in kept if r["ticker"] == "MU"]})
    audit = ["# MU 财报事件筛选审计", "", "49 条逐案审阅：剔除 33 条，保留 16 条。依据是触发消息正文与 W3 的交易理由，非按日期整日删除。", "",
             "9 月 30 日的 TrendForce 行业价格更新和 BofA AI 支出预测保留；10 月 5 日的 WSTS 数据与独立行业价格预测保留。9 月 24 日 UBS 和 9 月 29 日 JPMorgan 的财报前预测保留。", "",
             "| 结果 | 意图时间 UTC | Case | 消息标题 | 理由 |", "| --- | --- | --- | --- | --- |"]
    for row in originals:
        if row["ticker"] != "MU":
            continue
        reason = EXCLUDED.get(row["case_id"], "独立行业/公司事件或财报发布前的分析师预期更新")
        if row["case_id"] == "case_4be7a686d63943aba06c2d337561135e":
            reason = "W3 的新增交易依据为 WSTS 八月实际数据与独立 DRAM 价格预测；财报只作为背景"
        title = row["case_summary"]["title"]["value"].replace("|", "\\|")
        audit.append(f"| {'剔除' if row['case_id'] in EXCLUDED else '保留'} | {row['intent_released_at_utc']} | `{row['case_id']}` | {title} | {reason} |")
    (DEST / "filter_audit.md").write_text("\n".join(audit) + "\n", encoding="utf-8")
    # Verify source integrity and exported membership, plus every unfilled estimate.
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    delivered = [r for name in output_files for r in read(DEST / name)["records"]]
    assert len(delivered) == len({r["intent_id"] for r in delivered}) == 44
    assert all(read(DEST / name)["record_count"] <= 5 for name in output_files)
    assert all(("estimated_pnl" in r) == (not bool(r["trade_result"]["entry"])) for r in delivered)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
