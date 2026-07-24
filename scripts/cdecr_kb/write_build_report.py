"""Write the human-readable CDECR KB delivery report from final evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import DEFAULT_OUTPUT_DIR, DEFAULT_WORK_DIR, REPO_ROOT, read_json
from validate_catalogs import validate


def _state_summary(directory: Path) -> tuple[int, int]:
    if not directory.exists():
        return 0, 0
    total = errors = 0
    for path in directory.glob("*.json"):
        total += 1
        try:
            errors += int(bool(read_json(path).get("error")))
        except Exception:
            errors += 1
    return total, errors


def _markdown_table(stats: dict[str, Any]) -> str:
    lines = [
        "| Catalog | Records | MiB | Empty alias lists | Normalized alias collisions |",
        "|---|---:|---:|---:|---:|",
    ]
    for catalog, item in stats.items():
        lines.append(
            f"| `{catalog}.json` | {item['records']:,} | {item['mib']:.3f} | "
            f"{item['empty_alias_records']:,} | {item['normalized_alias_collisions']:,} |"
        )
    return "\n".join(lines)


def write_report(work: Path, output: Path, destination: Path) -> None:
    validation = validate(output)
    companyfacts_total, companyfacts_errors = _state_summary(work / "state" / "companyfacts")
    submissions_total, submissions_errors = _state_summary(work / "state" / "submissions")
    named_stats_path = work / "reports" / "named_object_source_stats.json"
    named_stats = read_json(named_stats_path) if named_stats_path.exists() else {}
    insider_archives = len(list((work / "downloads").glob("*_form345.zip")))
    lines = [
        "# CDECR v2 对象知识库构建交付报告",
        "",
        f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        "",
        "## 结论",
        "",
        f"12 个 catalog 已按数组式 v2 schema 生成。最终严格验证结果："
        f"`valid={str(validation['valid']).lower()}`，错误 {len(validation['errors'])} 个，"
        f"提醒 {len(validation['warnings'])} 个。提醒项是上下文允许的 alias 多义性统计，不是 schema 错误。",
        "",
        f"Catalog 内容哈希（SHA-256）：`{validation['catalog_hash']}`。",
        "",
        "本轮只生成 JSON 与离线构建/验证工具，没有修改现有运行时 loader。v2 使用独立目录，避免数组格式覆盖 v1 后破坏当前 loader。",
        "",
        "## 最终规模",
        "",
        _markdown_table(validation["stats"]),
        "",
        "## 数据获取证据",
        "",
        f"- SEC Company Facts：已处理 {companyfacts_total:,} 个 CIK 检查点，其中错误/无数据 {companyfacts_errors:,} 个；原始响应未保留。",
        f"- SEC submissions：已处理 {submissions_total:,} 个 CIK 检查点，其中错误/无数据 {submissions_errors:,} 个；只保留近五年目标表单摘要。",
        f"- SEC insider transactions：{insider_archives} 个季度 ZIP。",
        f"- EPA National Single：扫描 {named_stats.get('rows', 0):,} 行；owner 命中源记录 {named_stats.get('owner_matched', 0):,}，"
        f"最终选择 {named_stats.get('owner_matched_selected', 0):,}；未匹配工业设施样本 {named_stats.get('unmatched_sampled', 0):,}。",
        "- SEC Financial Statement Data Sets：近四个可用季度；custom tag 按 NUM 使用频率排序后限量补充。",
        "- GeoNames：countryInfo、admin1CodesASCII、cities500、US、alternateNamesV2。",
        "- FASB：2026 US GAAP Taxonomy。",
        "- Institution：SEC 当前 RIA 月度 ZIP、FDIC 当前 Institutions CSV、SEC Active Broker-Dealers、受限 Wikidata 类别。",
        "",
        "## 规模与质量决策",
        "",
        "- Place 未导入 GeoNames 千万级全集，只保留国家、一级行政区、全球 cities500，以及美国行政/人口不少于 100 的居民点；alias 每对象最多 20 个。",
        "- Metric 保留 FASB 数值型可报告概念，排除 abstract/axis/domain/member/table/text block 等结构项；SEC 十万级 custom tag 只取高频候选。",
        "- Person 只保留近五年 officer/director，不导入普通 10% 股东；跨人物重复的单词 alias 被删除。",
        "- EPA owner 命中按每 owner 最多 100 个与全局 50,000 上限做多样化，未匹配工业设施另取稳定 25,000 样本。",
        "- Artifact 的 8-K/6-K 每公司限量；Earnings Release 允许按最近 filing 合成，不要求 Exhibit 99.1 明确证据。",
        "- Fiscal Period 过滤比较期事实：filing date 与 fact end 的差必须在 0–200 天，并保留最近十个实际财年及一个 364 天平移的未来财年。",
        "",
        "## 已知边界",
        "",
        "- IARD `firm_compilation.zip` 当前只有规范、示例和说明，不含完整机构记录；因此改用 SEC 官方当前 RIA 月度 ZIP。该 ZIP 覆盖 SEC 注册 RIA，不等于全量州注册 adviser。",
        "- FINRA A–Z 页面未抓取，Broker-Dealer 使用 SEC Active Broker-Dealers 替代。",
        "- 静态 Artifact 无法覆盖全部分析师报告、公司新闻稿和协议；本次重点覆盖 SEC filing 与合成 Earnings Release。",
        "- Person 与 Named Object 按公开批量资源尽力完成；长尾卖方分析师、私有产品/项目仍可能缺失。",
        "- Alias collision 统计包含 Place 同名、Person 同名不同 org、Fiscal Period 跨公司同名等设计上需要字段上下文消歧的情况。",
        "",
        "## 复现与规则",
        "",
        "完整构建顺序、数据源、ID 冲突规则、Artifact kind 与限量策略见 `scripts/cdecr_kb/README.md`。",
        "",
        "最终校验原始报告位于 `D:\\cdecr-kb-work\\reports\\catalog_validation.json`。",
        "",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--destination",
        type=Path,
        default=REPO_ROOT / "dev_plan" / "CDECR" / "CDECR_KB_V2_BUILD_REPORT.md",
    )
    args = parser.parse_args()
    write_report(args.work_dir, args.output_dir, args.destination)
    print(args.destination)


if __name__ == "__main__":
    main()
