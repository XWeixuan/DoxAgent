"""Append a concise extension without rewriting the original audit."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root=Path(__file__).parent;report=root.parent.parent/'docs/trade-intent-return-audit-20261022.md'
rows=json.loads((root/'upside_results.json').read_text(encoding='utf8'))
labels={1:'较强且保持至日末',2:'较明显，盘后仍保持',3:'重复意图，仅作记录',4:'空间很小，随后反向',5:'较强且保持至日末',6:'较强；同催化重复',7:'较强；同催化重复',8:'短暂小幅，日末消失',10:'几乎无有效有利窗口',11:'空间很小，随后反向',12:'有短暂线索，日末较弱',13:'有短暂线索，随后反向'}
bj=ZoneInfo('Asia/Shanghai')
def f(v):return f'{v:+.2f}'
text=[
'## 补充：当日最高潜在盈利与大盘对照',
'',
'沿用上文相同的 13 条意图、5 分钟修正及入场价。**“当日”按美东入场所在日期，自入场后至当晚 20:00，含盘前盘后**；不跨到下一交易日。此处评估的是全天机会空间，窗口可能晚于原策略的 15:30 退出点，不能当成原退出规则已经赚到的收益。',
'',
'- LONG 用入场后最高成交价、SHORT 用最低成交价计算最大有利收益；亏损不当作盈利空间，最低为 0。使用新增 IBKR **1 分钟 TRADES K 线**，剔除零成交量/零成交笔数的填充 bar；为避免把入场前的价格计入，排除入场 tick 所在的未完整分钟。因此可能漏掉入场后不足一分钟内的更优价格，是分钟级近似，极值时点仅精确到分钟。',
'- 简单以 **SPY 代表大盘、beta=1**。SPY 起点为首个完整分钟开盘价，终点为个股极值所在分钟收盘价；与个股的精确入场/极值存在不足一分钟的时间差。`极值超额（百分点）= 最大潜在盈利% − 同方向持有SPY的收益%`：LONG 减 SPY 涨幅，SHORT 加 SPY 涨幅。表中“SPY同期”始终显示 SPY 自身涨跌。',
'- 选取事后个股极值会使超额空间偏乐观，故另用同一入场价与接近 20:00 的末笔有效分钟收盘价计算**日末超额**。只作消息相关性的粗筛，不做回归、行业中性或因果认定；个股 beta、行业走势、后续新闻仍可能解释差异。',
'',
'**结果：12 条可定价意图的最大潜在盈利平均为 2.03%，中位数 2.07%；另 1 条仍缺入场价。**在各自事后最佳时点，12 条的超额空间都为正；但到当日结束，仅 7 条超额仍为正，平均日末超额约 **+0.01 个百分点，接近零**。因此“盘中曾有空间”不等于“普遍存在持续的消息 alpha”。该统计含 #3 重复拦截记录及同催化 Case，不代表独立事件样本。',
'',
'下表极值时间沿用**北京时间**；SPY同期区间为各自入场附近至各自极值分钟，不是 SPY 全日涨跌。极值价为 LONG 卖出/SHORT 回补价。',
'',
'| # | 标的/方向 | 当日最大潜在盈利 | 极值价（美元） | 极值分钟（北京时间） | SPY同期涨跌 | 极值超额（百分点） | 日末超额（百分点） | 消息 alpha 粗评 |',
'| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |',
]
for r in rows:
    n=r['number'];name=r['ticker']+' '+r['direction']
    if r['status']!='PRICED':
        text.append(f'| {n} | {name} | 无法估算 | — | — | — | — | — | 缺同期入场价 |');continue
    stamp=datetime.fromisoformat(r['peak_minute']).astimezone(bj).strftime('%m-%d %H:%M')
    text.append(f"| {n} | {name} | **{f(r['maximum_favorable_return_pct'])}%** | {r['extreme_price']} | {stamp} | {f(r['spy_return_to_peak_pct'])}% | {f(r['excess_at_peak_pp'])} | {f(r['excess_at_end_pp'])} | {labels[n]} |")
text += [
'',
'简要判断：',
'',
'- **INTC #1、#5、#6/#7：相对大盘的线索较强。**#1 爆炸事件空头最大空间 5.71%，其中同期大盘下跌约 1.55%，仍剩约 4.16 个百分点超额，日末仍约 3.79；#5 NF3 泄漏及 #6/#7 Modal/Vera 空头在大盘接近平盘或小跌时仍有约 1.95–2.40 个百分点极值超额，日末仍保持。与负面消息方向一致，但不足以证明全部由各条消息造成，#6/#7 也不能重复算作两次独立验证。',
'- **RKLB #2/#3：首飞推迟消息有相对大盘的反应线索，主要机会延续至盘后。**#2 最大空间 2.22%、极值超额约 1.94 个百分点；#3 数值更高源于更晚、更高的假设开空价，且已被正常重复拦截。相比之下，#4 下调收入预期空头最大仅 0.34%，日末超额 -4.73 个百分点，未见有用的持续 alpha。',
'- **MU #10、NBIS #11：原方向基本没有值得利用的当日盈利空间。**最大仅 0.13% / 0.43%，随后日末超额分别 -3.51 / -5.25 个百分点；本样本价格表现不支持将其视为有效的负面消息交易。',
'- **MU #8/#12、COHR #13：有短暂空间，持续性弱。**MU 两笔 LONG 最大约 0.67% / 1.04%，日末超额约 -0.04 / +0.31 个百分点。COHR 最大约 2.15%，同期 SPY 仅 +0.08%，有短暂个股强势线索；但日末超额反为 -2.34 个百分点，不能据此认定消息带来持续 alpha。',
'',
'补充证据：[14 组标的/日期的分钟行情原始回包](../eval/trade_intent_return_audit_20261022/upside_market.jsonl)、[逐条极值与 SPY 对照](../eval/trade_intent_return_audit_20261022/upside_results.json)、[汇总](../eval/trade_intent_return_audit_20261022/upside_summary.json)、[SHA-256](../eval/trade_intent_return_audit_20261022/upside_sha256.json)。运行 `python eval/trade_intent_return_audit_20261022/calculate_upside.py` 可离线复算。本补充未改变上文原始统计及 15:30 退出收益。',
]
marker='\n## 补充：当日最高潜在盈利与大盘对照\n'
original=report.read_text(encoding='utf8')
if marker in original:original=original.split(marker)[0].rstrip()+'\n'
report.write_text(original.rstrip()+'\n\n'+'\n'.join(text)+'\n',encoding='utf8')
print(report)
