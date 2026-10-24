"""Merge the additional week without changing the original 13-record evidence."""
import hashlib,json,statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root=Path(__file__).parent;prior=root/'prior_week';doc=root.parent.parent/'docs/trade-intent-return-audit-20261022.md'
def load(p):return json.loads(p.read_text(encoding='utf8'))
old=load(root/'results.json');new=load(prior/'results.json');old_up=load(root/'upside_results.json');new_up=load(prior/'upside_results.json')
rows=[dict(r,report_number=n) for n,r in enumerate(old+new,1)]
up=old_up+[dict(r,number=r['number']+13) for r in new_up]
assert len(rows)==16 and len({r['intent_id'] for r in rows})==16
assert {r['intent_id'] for r in rows}=={r['intent_id'] for r in up}
def stats(sub):
    vals=[float(r['return_pct']) for r in sub if r['return_pct'] is not None]
    return dict(count=len(sub),priced=len(vals),unpriced=len(sub)-len(vals),positive=sum(v>0 for v in vals),negative=sum(v<0 for v in vals),mean_pct=statistics.mean(vals),median_pct=statistics.median(vals),capped=sum(r['capped'] for r in sub))
pricedup=[r for r in up if r['status']=='PRICED']
summary=dict(start='2026-10-06T16:00:00+00:00',end=load(root/'snapshot.json')['captured_at'],all=stats(rows),excluding_blocked_duplicate=stats([r for r in rows if r['status']!='DUPLICATE_POLICY']),prior_week=stats(new),original_window=stats(old),upside=dict(priced=len(pricedup),mean_maximum_favorable_return_pct=statistics.mean(r['maximum_favorable_return_pct'] for r in pricedup),median_maximum_favorable_return_pct=statistics.median(r['maximum_favorable_return_pct'] for r in pricedup),mean_excess_at_peak_pp=statistics.mean(r['excess_at_peak_pp'] for r in pricedup),positive_excess_at_end=sum(r['excess_at_end_pp']>0 for r in pricedup),mean_excess_at_end_pp=statistics.mean(r['excess_at_end_pp'] for r in pricedup)))
(root/'combined_results.json').write_text(json.dumps(dict(intents=sorted(rows,key=lambda r:r['released_at']),upside=up),ensure_ascii=False,indent=2),encoding='utf8')
(root/'combined_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
(root/'combined_sha256.json').write_text(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in ['combined_results.json','combined_summary.json']},indent=2),encoding='utf8')
bj=ZoneInfo('Asia/Shanghai')
def stamp(t):return datetime.fromisoformat(t).astimezone(bj).strftime('%m-%d %H:%M:%S')
def pct(v):return '无法定价' if v is None else f'{float(v):+.2f}%'
def pp(v):return f'{v:+.2f}'
a=summary['all'];e=summary['excluding_blocked_duplicate'];u=summary['upside'];w=summary['prior_week']
prefix=f'''# DoxAgent 交易意图收益审计（2026-10-07 至 10-22）

## 扩展后全量总览

**完整范围为北京时间 2026-10-07 00:00:00 至 10-22 14:41:42**，截止点沿用首轮快照。新增 10-07 至 10-13 的 3 条，原 10-14 起的 13 条保留，合计 **16 条意图**（INTC 6、MU 4、RKLB 3、BE 1、NBIS 1、COHR 1）。其中 15 条有执行接收记录，1 条重复策略拦截；共 **12 条**按 5 分钟上限修正入场。仍有 1 条 INTC 缺同期价。

| 范围 | 全部意图 | 可定价 | 盈 / 亏 | 15:30规则平均收益 |
| --- | ---: | ---: | --- | ---: |
| 新增：10-07 至 10-13 | 3 | 3 | 1 / 2 | {pct(w['mean_pct'])} |
| 原窗口：10-14 至 10-22截止 | 13 | 12 | 7 / 5 | -0.15% |
| **扩展后合计** | **16** | **15** | **8 / 7** | **{pct(a['mean_pct'])}** |
| 扩展后剔除重复拦截 | 15 | 14 | 7 / 7 | {pct(e['mean_pct'])} |

全量可定价意图正收益比例 **{a['positive']/a['priced']*100:.2f}%**，中位收益 **{pct(a['median_pct'])}**。当日最高潜在盈利平均 **{u['mean_maximum_favorable_return_pct']:.2f}%**，中位数 **{u['median_maximum_favorable_return_pct']:.2f}%**；日末仍有正超额的为 **{u['positive_excess_at_end']}/{u['priced']}**，平均日末超额约 **{pp(u['mean_excess_at_end_pp'])} 个百分点**。均为逐意图等名义金额统计，含重复/相关催化，非账户收益或独立事件检验。

计算沿用下文统一口径：Case 耗时最多 5 分钟；固定退出按美东 15:30；最大空间按美东入场当日至 20:00；SPY 作 beta=1 的简单对照；不计滑点。新增三条逐项见文末，编号 #14–#16，原 #1–#13 保持不变。

## 原窗口：10-14 至 10-22（保留原始统计）

'''
text=doc.read_text(encoding='utf8')
if '## 原窗口：10-14 至 10-22（保留原始统计）\n\n' in text:
    original=text.split('## 原窗口：10-14 至 10-22（保留原始统计）\n\n',1)[1]
else:original=text.split('\n',1)[1].lstrip()
marker='\n## 新增前一周：10-07 至 10-13\n'
if marker in original:original=original.split(marker)[0].rstrip()+'\n'
events=['Intel 自测 Xeon 6 代理工作负载吞吐优势','大摩上修机架功耗及现场供电需求','据报 Apple 削减 iPhone 18 Pro 组件订单']
tail=[
'## 新增前一周：10-07 至 10-13',
'',
'该周只读扫描全部持久意图记录，新增 **3 条**，均有正式 trade record 与执行接收记录，没有重复拦截；与原 13 条的 intent_id 无交集。此时仍是旧版决策结构：INTC/MU 的 Expert 决策保存在 `w3_result`，BE 是 W2 直接命中 Policy，未经过 W3；不能仅查新版 `trade_decision` 字段，否则会漏掉这三条。新增记录的原耗时分别为 5.18、2.67、6.93 分钟。',
'',
'以下时间均为北京时间；退出日均为当日 03:30。“最大空间”与“极值超额”仍使用前述美东入场当日至 20:00 的窗口。#14 虽在北京时间 10-07 产生，但所属美东交易日为 **10-06**，行情和 SPY 均按 10-06 配对，未误用 10-07 行情。',
'',
'| # / ID尾8位 | 标的/方向 | 决策催化 | 原释放→假设入场 | 入场→退出价（美元） | 退出日 | 15:30规则收益 | 原释放收益 |',
'| --- | --- | --- | --- | ---: | --- | ---: | ---: |',
]
for n,(r,event) in enumerate(zip(new,events),14):
    t=stamp(r['released_at'])+' → '+stamp(r['entry_at']) if r['capped'] else stamp(r['entry_at'])+'（不变）'
    tail.append(f"| {n} / `{r['intent_id'][-8:]}` | {r['ticker']} {r['direction']} | {event} | {t} | {r['entry_tick']['price']} → {r['exit_tick']['price']} | {stamp(r['exit_at'])[:5]} | **{pct(r['return_pct'])}** | {pct(r['raw_return_pct'])} |")
tail += ['', '| # | 当日最大潜在盈利 | 极值价（美元） | 极值分钟（北京时间） | SPY同期涨跌 | 极值超额（百分点） | 日末超额（百分点） | 消息 alpha 粗评 |','| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |']
judgments=['短暂相对强势，日末反向','短暂相对强势，未维持','相对大盘较明显，日末保持']
for n,(r,j) in enumerate(zip(new_up,judgments),14):
    tail.append(f"| {n} | **{pct(r['maximum_favorable_return_pct'])}** | {r['extreme_price']} | {stamp(r['peak_minute'])[:-3]} | {pct(r['spy_return_to_peak_pct'])} | {pp(r['excess_at_peak_pp'])} | {pp(r['excess_at_end_pp'])} | {j} |")
tail += [
'',
'- **#14 INTC LONG**：依据 Intel 自测的 Xeon 6 代理工作负载性能优势，未确认客户采用或订单。盘中最大空间约 1.11%、极值超额约 1.04 个百分点，但 15:30 基本持平、日末超额 -1.16 个百分点；支持短暂反应线索，不支持持续 alpha。',
'- **#15 BE LONG**：W2/Policy 理由为 Morgan Stanley 上修 Vera Rubin/Rubin Ultra 机架功耗，并连接现场供电缺口及 Bloom 燃料电池潜在需求。最大空间约 0.93%，大盘同期下跌约 0.51%，有相对抗跌线索；但固定退出亏损 2.23%，日末超额 -0.57 个百分点，未持续兑现。',
'- **#16 MU SHORT**：实际催化是据报 Apple 削减 iPhone 18 Pro/Pro Max 组件订单至少 15%，不是触发文章标题中的“大盘期货/油价”本身。最大空间约 3.42%，同期 SPY 接近平盘，固定退出盈利 2.44%，日末超额约 +2.52 个百分点；新增三条中，这是消息方向与相对价格表现最一致的一条。该订单消息当时尚未经 Reuters 独立核实，也未直接确认 Micron 订单减少，故仍只作关联线索。',
'',
'新增证据：[3 条完整意图及旧版决策快照](../eval/trade_intent_return_audit_20261022/prior_week/snapshot.json)、[正式交易记录核对](../eval/trade_intent_return_audit_20261022/prior_week/crosscheck.json)、[8 次逐笔查询](../eval/trade_intent_return_audit_20261022/prior_week/market.jsonl)、[6 组分钟行情](../eval/trade_intent_return_audit_20261022/prior_week/upside_market.jsonl)、[收益明细](../eval/trade_intent_return_audit_20261022/prior_week/results.json)、[最大空间/SPY明细](../eval/trade_intent_return_audit_20261022/prior_week/upside_results.json)。',
'',
'扩展后 [16 条合并明细](../eval/trade_intent_return_audit_20261022/combined_results.json)及[合并汇总](../eval/trade_intent_return_audit_20261022/combined_summary.json)可供复核。新增周计算脚本位于同目录 `prior_week/calculate.py`、`prior_week/calculate_upside.py`；原证据、追加周证据及合并结果分别保留 SHA-256 清单。全程只读生产数据库和行情，未改生产配置或执行交易。',
]
doc.write_text(prefix+original.rstrip()+'\n\n'+'\n'.join(tail)+'\n',encoding='utf8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
