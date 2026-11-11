"""Append conclusions to a saved natural-window report; no production access."""
import collections
import csv
import json
from datetime import datetime
from pathlib import Path

root = Path(__file__).parent
prefix = "final-30m"
x = json.loads((root / f"{prefix}.json").read_text(encoding="utf-8-sig"))
s = json.loads((root / f"{prefix}-summary.json").read_text(encoding="utf-8-sig"))
rows = list(csv.DictReader((root / f"{prefix}-per-source.csv").open(encoding="utf-8-sig")))
first = json.loads((root / "last-first.json").read_text(encoding="utf-8-sig"))
elapsed = (datetime.fromisoformat(x["until"]) - datetime.fromisoformat(x["since"])).total_seconds()
assert elapsed >= 1800, "The final release has not completed its observation window"
formal = s["after_formal"]
tests = s["after_test"]
full = sum(r["n"] for r in x["body"] if r["outcome"] in ("FULL", "SHORT_FULL"))
cadence = list(csv.DictReader((root / f"{prefix}-per-unit-cadence.csv").open(encoding="utf-8-sig")))
gaps = [r for r in cadence if r["observed_gap_gt_2_period"] == "True"]
lines = [
    "", "## 最终结论及未完成的老化边界", "",
    f"最终连续自然窗口 {elapsed / 60:.2f} 分钟；正式有效采集周期 {formal['counts'].get('CAPTURED', 0)}/{formal['completed']}（{formal['success_pct']}%），试验源 {tests['counts'].get('CAPTURED', 0)}/{tests['completed']}（{tests['success_pct']}%）。这些仍不是新文章数。",
    f"全窗无成功的失败入口：{json.dumps(s['blocked_scopes'], ensure_ascii=False)}；截止失败/部分状态 unit：{json.dumps(s['current_bad_units'], ensure_ascii=False)}。截止投影与完整历史分开。",
    f"共 {len(cadence)} 个 ticker×unit 有本窗口记录，{len(gaps)} 个出现大于两倍配置周期的内部或窗口边缘空窗；逐项见 CSV。不能将总体成功率当作全部时效目标达标。",
    f"正式正文 FULL/SHORT_FULL 合计 {full} 条回执；其他权限/鉴权/需渲染回执在上表单列，不能称所有文章正文均已恢复。", "",
    "### 仍出现失败的入口", "",
    "| 入口 | 有效周期 | 失败 | 已有回执原因 |", "|---|---:|---:|---|",
]
for row in rows:
    if int(row["after_failed"]):
        lines.append(f"| {row['scope']} | {row['after_success']} | {row['after_failed']} | {row['after_reasons']} |")
lines += ["", "### 重点分支和验收限制", ""]
for sid in ("calcalist_search", "nrc_event_notifications", "tomshardware_rss", "digitimes_semiconductors", "investing_ticker_news", "investorshub_ticker_news"):
    g = x["groups"].get(sid)
    if g:
        lines.append(f"- `{sid}`：{json.dumps(g['status'], ensure_ascii=False)}；原因 {json.dumps(g['reasons'], ensure_ascii=False)}。")
cse = [u for u in x["units"] if u["source_id"] == "calcalist_search"]
resets = [json.loads(u["timing"]).get("cse_context_resets_total") for u in cse if u.get("timing")]
lines += [
    f"- Calcalist 当前既有 unit 累计上下文修复计数 {resets}。这是同一进程的累计数，不按 ticker 相加；与同窗真实 CAPTURED 结合判断，自身不是新请求成功数。",
    "- DIGITIMES 自然列表成功不能证明原先所有 302 都属于规范跳转；若窗口没有真实 302→200 分支，只能将该分支列为离线验证通过、生产触发未覆盖。",
    "- 可选 http_timing 字段在无新 HTTP 标量的 SiteClient 周期可能保留最近值，因此不将它当成每轮独立网络证明；以实际请求回执/ACCESS_RESULT/周期终态为准。",
    "- 后半窗 DefenseScoop 先有30个失败物理周期（UTC18:52:41—18:57:31），18:58快照两个入口自然恢复；18:59后再次失败，最终共55次 public_listing_http_403，同轮另有200，截止两个入口均failed。属于真实主HTTP拒绝与复发，不能写成全网准入故障，也不能仅凭另一请求200认定完整回退已成功。该源未通过持续稳定验收；保留独立主失败与RSS证明，不重启/换路由掩盖或降低整批周期。最终有界核对见 final-residuals-end.json。",
    "- ONDS IR仍有ConnectTimeout，RKLB IR出现TLS握手/读超时；这些不是已证实的坏池PoolTimeout。已保留正常后续周期与其他host，不将普通网络超时触发成无界client重建。尚不能承诺各单源均达到99.5%。",
    "- 真实 429 遵循既有受限 lane 的 Retry-After；共享浏览器容量仍为两槽，队列偶发延期与网站 504/鉴权问题不能靠扩大等待伪装成功。",
    "- 此次没有降低任何配置轮询间隔、增加 Collector＋Site 的总 HTTP 槽数或重建 Supervisor/Profile；没有操作 Reuters 的身份/出口/验证码。",
    "- 短窗满足与否与长期老化分开：尚未跨越此前约 18 小时复发点，方案的 24 小时或大于故障前两倍请求次数的老化验收尚未完成。未创建长期观察任务，不能据此宣称长期无人值守稳定。", "",
    "### 资源样本", "",
    f"首次快照 {first['until']} 与截止 {x['until']}：磁盘空闲 {first['resources']['disk_free'] / 1024**3:.2f}→{x['resources']['disk_free'] / 1024**3:.2f} GiB；acquisition {first['resources']['acquisition_bytes'] / 1024**3:.3f}→{x['resources']['acquisition_bytes'] / 1024**3:.3f} GiB；WAL {first['resources']['wal_bytes']}→{x['resources']['wal_bytes']} bytes。这是两点样本，不证明长期增长有界，也不能把全部磁盘变化归因消息源。",
    "本轮诊断复用既有小字段、许可队列内存通知和现有索引；没有新增逐请求诊断表、抓包、截图或高频监控写入。CPU/IO PSI 仍须按原始样本评估，不编造整窗分位。", "",
]
with (root / f"{prefix}.md").open("a", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(json.dumps({"minutes": round(elapsed / 60, 2), "formal": formal, "test": tests, "body_full_receipts": full, "unit_rows": len(cadence), "gap_units": len(gaps)}, ensure_ascii=False))
