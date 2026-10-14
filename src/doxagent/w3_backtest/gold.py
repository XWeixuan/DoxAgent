"""Import the user's 44-case review; do not label the 29 unreviewed cases."""

from __future__ import annotations

import json
import re
from pathlib import Path

SOURCE = Path("eval/w3_trade_intent_review_20261010/revised_export")
REVIEW = "DoxAgent_W3_44_Cases_merged.md"
NEGATIVE_TAGS = {
    "门槛过低",
    "过度关联",
    "新旧／基线失准",
    "重复交易",
    "策略越界",
}
# These three reviews explicitly support the trade, rather than merely its direction.
POSITIVE_CASES = {"MU-02-02", "INTC-01-04", "BE-02-02"}


def import_gold(dataset: Path, *, source: Path = SOURCE) -> dict:
    text = (source / REVIEW).read_text(encoding="utf-8")
    summaries = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*\*\*([A-Z]+-\d\d-\d\d)\*\*(.*?)\|", line)
        if not match:
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) != 6:
            raise ValueError(f"unexpected review row {match[1]}")
        summaries[match[1]] = dict(
            title=match[2].strip(), assessment=parts[4], tags=parts[5].split("、")
        )
    if len(summaries) != 44:
        raise ValueError(f"review has {len(summaries)} summary rows, expected 44")
    details = {}
    for match in re.finditer(
        r"^\*\*([A-Z]+-\d\d-\d\d)｜.*?(?=^\*\*[A-Z]+-\d\d-\d\d｜|^### |^## |\Z)",
        text,
        re.M | re.S,
    ):
        details[match[1]] = match[0].strip()
    identities = {}
    for path in sorted(source.glob("*_[0-9][0-9].json")):
        ticker, part = path.stem.split("_")
        for number, item in enumerate(json.loads(path.read_text(encoding="utf-8"))["records"], 1):
            identities[f"{ticker}-{part}-{number:02}"] = item
    entries = []
    for path in sorted((dataset / "inputs").glob("*/case.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        key = case["case_key"]
        entry = dict(
            case_key=key,
            original_case_id=case.get("original_case_id"),
            gold_source=None,
            assessment_text=None,
            detail_text=None,
            issue_tags=[],
            expected_action=None,
            expected_direction=None,
            conditions_or_notes=None,
        )
        if key in summaries:
            review = summaries[key]
            record = identities[key]
            if case.get("original_case_id") != record["case_id"]:
                raise ValueError(f"Gold identity mismatch: {key}")
            negative = bool(set(review["tags"]) & NEGATIVE_TAGS)
            action = "NO_TRADE" if negative else "TRADE" if key in POSITIVE_CASES else None
            entry.update(
                gold_source=f"{REVIEW}#{key}",
                assessment_text=review["assessment"],
                detail_text=details.get(key),
                issue_tags=review["tags"],
                expected_action=action,
                expected_direction=record["trade_direction"] if action == "TRADE" else None,
                conditions_or_notes=(
                    "依据原评估明确的正/负判断归一；不使用盈亏作为Gold。"
                    if action
                    else "原评估认可方向但包含时点/剩余机会条件；保留原文，不强制唯一交易答案。"
                ),
            )
        entries.append(entry)
    (dataset / "gold.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8"
    )
    notes = [
        "# 首批 W3 回测 Gold 口径",
        "",
        "44条完整导入原复盘结论、逐条诊断和问题标签。额外4条及随机25条未标注，不推断为NO_TRADE。",
        "",
        "明确指出门槛、过度关联、新旧/基线、重复或策略错误的条目归一为NO_TRADE；单独时序异常不等于原研判应NO_TRADE。",
        "MU-02-02、INTC-01-04、BE-02-02原文明确支持交易，归一为原评估方向。",
        "其余只认可方向但对剩余机会有条件的条目不强制二分；expected_action为空，原文Gold保留。",
        "不得因模型输出不同而自动重写Gold；编辑Gold后可以只重做报告。",
        "",
        "| Case | Gold | 原结论 |",
        "|---|---|---|",
    ]
    notes += [
        f"| {e['case_key']} | {e['expected_action'] or '待人工对照'} "
        f"{e['expected_direction'] or ''} | {e['assessment_text'] or '未标注'} |"
        for e in entries
    ]
    (dataset / "gold_review.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return dict(
        cases=len(entries),
        reviewed=sum(e["gold_source"] is not None for e in entries),
        scorable=sum(e["expected_action"] is not None for e in entries),
        unreviewed=sum(e["gold_source"] is None for e in entries),
    )
