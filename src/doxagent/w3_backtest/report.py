"""Deterministic decisions/Gold comparison; unlabelled cases never become negatives."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def normalize_decision(result: dict, case: dict, dataset: Path) -> str | None:
    decision = result.get("decision") or {}
    if decision.get("decision") in {"LONG", "SHORT", "NO_TRADE"}:
        return decision["decision"]
    if decision.get("decision") == "EXPERT_TRADE":
        return (decision.get("expert_trade") or {}).get("direction")
    if decision.get("decision") == "POLICY_TRADE":
        chosen = decision.get("policy_trade") or {}
        policy_file = dataset / "research" / case["research_key"] / "policy_set.original.json"
        if not policy_file.exists():
            policy_file = policy_file.with_name("policy_set.json")
        policies = json.loads(policy_file.read_text(encoding="utf-8"))["policies"]
        for policy in policies:
            if policy["policy_id"] == chosen.get("policy_id"):
                for condition in policy["activation_conditions"]:
                    if condition["condition_id"] == chosen.get("condition_id"):
                        return condition.get("decision")
    return None


def score(result: dict, gold: dict, normalized: str | None) -> dict:
    expected = gold.get("expected_action")
    if not expected:
        return dict(
            score=None, category="CONDITIONAL_GOLD" if gold.get("gold_source") else "UNLABELLED"
        )
    if not result.get("decision") or normalized is None:
        return dict(score=None, category="TECHNICAL_ERROR")
    target = "NO_TRADE" if expected == "NO_TRADE" else gold.get("expected_direction")
    if normalized == target:
        return dict(score=True, category="MATCH")
    if target == "NO_TRADE":
        return dict(score=False, category="WRONG_TRADE")
    if normalized == "NO_TRADE":
        return dict(score=False, category="MISSED_OPPORTUNITY")
    return dict(score=False, category="WRONG_DIRECTION")


def render_report(run_root: Path, dataset: Path | None = None) -> dict:
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    dataset = dataset or Path(manifest["dataset_path"])
    gold = {g["case_key"]: g for g in read_jsonl(dataset / "gold.jsonl")}
    rows = []
    for path in sorted((run_root / "cases").glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        key = result["case_key"]
        case = json.loads((dataset / "inputs" / key / "case.json").read_text(encoding="utf-8"))
        normalized = normalize_decision(result, case, dataset)
        item = {**result, "normalized_decision": normalized, "gold": gold.get(key, {})}
        item.update(score(result, item["gold"], normalized))
        item["group"] = case.get("group")
        db_path = path.parent / "runtime.sqlite3"
        item["operations_receipt"] = None
        if db_path.exists() and result.get("replay_case_id"):
            with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                receipt = db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='w3_operations_applied' "
                    "AND key=?",
                    (result.get("replay_case_id"),),
                ).fetchone()
                if receipt:
                    item["operations_receipt"] = json.loads(receipt[0])
        rows.append(item)
    scored = [r for r in rows if r["score"] is not None]
    unlabelled = [r for r in rows if r["category"] == "UNLABELLED"]
    unlabelled_decisions = [r for r in unlabelled if r["normalized_decision"] is not None]
    summary = dict(
        cases=len(rows),
        succeeded=sum(r["status"] == "SUCCEEDED" for r in rows),
        run_failures=sum(r["status"] != "SUCCEEDED" for r in rows),
        trade_decision_missing=sum(r["normalized_decision"] is None for r in rows),
        conditional_gold=sum(r["category"] == "CONDITIONAL_GOLD" for r in rows),
        scored=len(scored),
        correct=sum(r["score"] is True for r in scored),
        accuracy=sum(r["score"] is True for r in scored) / len(scored) if scored else None,
        wrong_trades=sum(r["category"] == "WRONG_TRADE" for r in rows),
        missed_opportunities=sum(r["category"] == "MISSED_OPPORTUNITY" for r in rows),
        wrong_directions=sum(r["category"] == "WRONG_DIRECTION" for r in rows),
        technical_errors=sum(r["category"] == "TECHNICAL_ERROR" for r in rows),
        operations_succeeded=sum(
            (r.get("stages", {}).get("post_decision_operations") or {}).get("status") == "COMMITTED"
            for r in rows
        ),
        labelled_no_trade_denominator=sum(
            r["gold"].get("expected_action") == "NO_TRADE" for r in scored
        ),
        labelled_trade_denominator=sum(r["gold"].get("expected_action") == "TRADE" for r in scored),
        unlabelled=len(unlabelled),
        unlabelled_decisions=len(unlabelled_decisions),
        unlabelled_directions={
            direction: sum(r["normalized_decision"] == direction for r in unlabelled_decisions)
            for direction in ("LONG", "SHORT", "NO_TRADE")
        },
        unlabelled_trade_rate=sum(
            r["normalized_decision"] != "NO_TRADE" for r in unlabelled_decisions
        )
        / len(unlabelled_decisions)
        if unlabelled_decisions
        else None,
    )
    summary["wrong_trade_rate"] = (
        summary["wrong_trades"] / summary["labelled_no_trade_denominator"]
        if summary["labelled_no_trade_denominator"]
        else None
    )
    summary["missed_opportunity_rate"] = (
        summary["missed_opportunities"] / summary["labelled_trade_denominator"]
        if summary["labelled_trade_denominator"]
        else None
    )
    (run_root / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    (run_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    text = [
        f"# W3 本地回测：{run_root.name}",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "主评分按第一阶段模型决策；宿主拒绝不会被算成模型NO_TRADE。未标注不是负样本。",
        "",
        "| Case | 模型决策 | 评分 | 运行 | 生产入口 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        text.append(
            f"| {row['case_key']} | {row['normalized_decision'] or '无合法决策'} | "
            f"{row['category']} | {row['status']} | "
            f"{'W3' if row.get('would_enter_w3') else '仅直接回放'} |"
        )
    for row in rows:
        duration = (
            datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])
        ).total_seconds()
        text += [
            "",
            f"## {row['case_key']} · {row.get('ticker', '')}",
            "",
            row.get("source_title", ""),
            "",
            f"截止：{row.get('cutoff_at')}；实际模型：{row.get('model')}；耗时：{duration:.1f}s。",
            "",
            f"Case 类型与信息标签：{row.get('labels', {})}",
            "",
            f"证据状态：{row.get('evidence_summary', {})}",
            "",
            f"原评估：{row['gold'].get('assessment_text') or '未标注'}",
            "",
            f"问题标签：{'、'.join(row['gold'].get('issue_tags') or []) or '无标注'}",
            "",
            "模型决定：",
            "```json",
            json.dumps(row.get("decision"), ensure_ascii=False, indent=2),
            "```",
            "",
            f"技术错误：{row.get('error')}",
            "",
            "后置运营：",
            "```json",
            json.dumps(row.get("operations"), ensure_ascii=False, indent=2),
            "```",
            "",
            f"宿主受理：{(row.get('host_receipt') or {}).get('disposition')}",
            "",
            "阶段审计（真实模型选择、耗时、token 与工具调用）：",
            "```json",
            json.dumps(row.get("phase_audits", []), ensure_ascii=False, indent=2),
            "```",
            "",
            f"原生工具审计：{row.get('native_tool_audit', {})}",
            "",
            "运营动作宿主回执：",
            "```json",
            json.dumps(row.get("operations_receipt"), ensure_ascii=False, indent=2),
            "```",
        ]
    (run_root / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return summary


def compare_runs(before: Path, after: Path) -> dict:
    ma = json.loads((before / "run_manifest.json").read_text(encoding="utf-8"))
    mb = json.loads((after / "run_manifest.json").read_text(encoding="utf-8"))
    # Gold is editable without rerunning models. Refresh both reports before
    # comparison so stale score files cannot invent a version improvement.
    for root, manifest in ((before, ma), (after, mb)):
        if manifest.get("dataset_path") and Path(manifest["dataset_path"]).is_dir():
            render_report(root)
    old = {r["case_key"]: r for r in read_jsonl(before / "results.jsonl")}
    new = {r["case_key"]: r for r in read_jsonl(after / "results.jsonl")}
    pairs = []
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        # Input fingerprint includes prompts; the separate fixture identity is
        # checked at the run manifest so a prompt change remains comparable.
        gold_equal = all(
            a.get("gold", {}).get(field) == b.get("gold", {}).get(field)
            for field in ("expected_action", "expected_direction")
        )
        comparable = ma.get("dataset_fingerprint") == mb.get("dataset_fingerprint") and gold_equal
        category = (
            "NOT_COMPARABLE"
            if not comparable
            else (
                "TECHNICAL_CHANGE"
                if a.get("normalized_decision") is None or b.get("normalized_decision") is None
                else "IMPROVED"
                if a["score"] is False and b["score"] is True
                else "REGRESSED"
                if a["score"] is True and b["score"] is False
                else "UNLABELLED_CHANGED"
                if a["score"] is None
                and a.get("normalized_decision") != b.get("normalized_decision")
                else "UNCHANGED"
            )
        )
        pairs.append(
            dict(
                case_key=key,
                change=category,
                before=a.get("normalized_decision"),
                after=b.get("normalized_decision"),
            )
        )
    result = dict(
        before=before.name,
        after=after.name,
        pairs=pairs,
        improved=sum(p["change"] == "IMPROVED" for p in pairs),
        regressed=sum(p["change"] == "REGRESSED" for p in pairs),
    )
    (after / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
