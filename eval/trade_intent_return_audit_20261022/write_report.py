import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

root=Path(__file__).parent;repo=root.parent.parent
rows=json.loads((root/'results.json').read_text(encoding='utf8'))
s=json.loads((root/'summary.json').read_text(encoding='utf8'))
bj=ZoneInfo('Asia/Shanghai')
def stamp(t):return datetime.fromisoformat(t).astimezone(bj).strftime('%m-%d %H:%M:%S')
def pct(x):return '无法定价' if x is None else f'{float(x):+.2f}%'
def price(t):return '缺价' if not t else str(t['price'])
events=['Fab 28 爆炸','Neutron 首飞延至 2027','同一 Neutron 公告（重复拦截）','KeyBanc 下调 2027 收入预期','Kiryat Gat 新增 NF3 泄漏','Modal 采用 NVIDIA Vera','Modal/Vera 后续报道','Nscale/Anthropic 新增算力谈判','欣兴基板材料与设备缺口','台湾工会罢工排期','TPU 推理生态竞争','SpaceX 提高算力部署目标','NVIDIA CPO 扩产计划']
text=[
'# DoxAgent 交易意图收益审计（2026-10-14 至 10-22）',
'',
'审计窗口：**北京时间 2026-10-14 00:00:00 至 10-22 14:41:42**。只读查询新加坡生产 Runtime 账本及远端 IBKR 历史成交；未下单、补单、改动生产记录或重启服务。',
'',
'## 结论',
'',
'共 **13 条正式意图记录**（INTC 5、RKLB 3、MU 3、NBIS 1、COHR 1）；12 条被执行模块接收，1 条为 `DUPLICATE_POLICY` 拦截。**10 条研判耗时超过 5 分钟，已按要求前移入场时点。**',
'',
'- 全量 13 条：12 条可定价，**7 盈、5 亏**，正收益比例 **58.33%**；逐条等名义金额平均收益 **-0.15%**，中位数 **+0.40%**。',
'- 剔除重复拦截的 1 条：12 条中 11 条可定价，**6 盈、5 亏**，平均 **-0.25%**。',
'- 同一批样本按原始意图释放时点入场，12 条可定价记录平均 **+0.01%**；5 分钟修正后减少约 **0.16 个百分点**。更快研判并不保证每笔收益提高。',
'- 最大盈利为 #1 INTC 爆炸事件 SHORT **+2.61%**；最大亏损为 #4 RKLB SHORT **-4.83%**。',
'',
'上述平均数是每条意图等名义金额的算术平均，**不是账户收益率、累计复利或实际成交盈亏**；不同意图存在持仓重叠和同催化重复。未指定假设仓位，因此不推算总美元盈亏。',
'',
'## 计算口径',
'',
'1. 按 `intent_id` 去重，以 `released_at` 决定是否落入窗口。97 条全量持久意图中有 13 条落入窗口；另与 W3 交易决策、正式 trade records 交叉核对：13 个 W3 交易 Case 全部对应意图，12 条 trade records 对应已接收意图，差额即重复拦截记录。',
'2. 研判耗时 = `released_at − Case.created_at`，包括 Case 内部排队与 W1/W2/W3；不是仅模型调用耗时，也不含消息发布至 Case 创建之前的采集延迟。入场时点 = `min(released_at, Case.created_at + 5 分钟)`。本轮对全部日期使用同一上限，原耗时不足 5 分钟的保持原时点；真实成交记录也统一改用此假设时点。',
'3. 卖出沿用执行配置的收盘前 30 分钟，即**美东 15:30**；若入场已过当日 15:30，顺延至下一交易日 15:30。本区间涉及的退出日无周末/休市歧义。北京时间均为次日 03:30。本次样本计划退出时点均已过去。',
'4. 行情来自 IBKR TWS 官方 API `TRADES` 历史逐笔：入场含盘前盘后，取假设时点后 **60 秒内首笔正常报告成交**；无正常报告成交则采用该窗口首笔其他有效成交并保留标记。退出取 15:30 后 **5 秒内首笔正常报告成交**。请求时间向上取整到秒，避免用入场前的成交。没有同期价格则记缺失，不用数十分钟后的价格冒充。',
'5. LONG 收益 = `(卖出价−买入价)/买入价`；SHORT 收益 = `(开空价−回补价)/开空价`。按要求不计滑点；同时不计佣金、融资及借券成本。提前入场是按历史最终决策回填的**反事实情景**，未验证该决策所需全部证据能在修正时点前获得，不能视为已证明的生产可实现收益。',
'',
'## 逐条结果',
'',
'时间均为北京时间；“释放→入场”为原始释放时点及 5 分钟修正后的假设时点；退出列为日期，当日 **03:30**。价格为美元，SHORT 的价格依次为开空价→回补价。事件简述以保存的 W3 决策理由为准，未仅依据触发文章标题判断。',
'',
'| # / ID尾8位 | 标的/方向 | 决策催化 | 原释放→假设入场 | 原耗时(分) | 入场→退出价 | 退出日 | 修正收益 | 原释放收益 |',
'| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: |',
]
for n,(r,event) in enumerate(zip(rows,events),1):
    orig=stamp(r['released_at']);entry=stamp(r['entry_at']);timing=orig+' → '+entry if r['capped'] else orig+'（不变）'
    text.append(f"| {n} / `{r['intent_id'][-8:]}` | {r['ticker']} {r['direction']} | {event} | {timing} | {r['latency_minutes']:.2f} | {price(r['entry_tick'])} → {price(r['exit_tick'])} | {stamp(r['exit_at'])[:5]} | **{pct(r['return_pct'])}** | {pct(r['raw_return_pct'])} |")
text += [
'',
'## 需要注意的记录',
'',
'- **#9 INTC 缺同期价**：修正后为美东 10-20 03:37:55，原释放为 03:42:27。IBKR 两次查询返回的首笔成交均为 **04:00:00、90.95 美元**，分别晚约 22 分钟和 18 分钟；因此两种口径均无法定价。该条仍在总数内，不按零收益处理，平均收益仅覆盖其余 12 条。',
'- **#3 RKLB 是重复拦截**，不是交易执行故障；为了“全部意图”仍列其假设收益，但同时提供剔除后的结果。#6/#7 是同一 Modal/Vera 催化的两条不同 Case，均被接收；保留原账本事实，不把两笔盈利解读成两个独立事件。',
'- **#11 NBIS 于美东 15:34 产生**，退出为下一交易日 15:30，非事后使用已经过去的当日退出价。',
'- **#13 COHR**：入场后 60 秒内未见正常报告成交，采用首笔带 `unreported` 标记的成交（晚约 12 秒）；这是价格代理限制。其余已定价入场均有正常报告成交，全部已定价退出均有正常报告成交。',
'- 为检查逐笔选择敏感性，改用窗口内首笔任意有效成交重算，仍为 **7 盈、5 亏**，平均 **-0.12%**；主结论未因是否优先正常报告成交而改变。',
'',
'## 证据与复核',
'',
'完整意图 ID、时间字段、状态和收益见 [逐条台账 CSV](../eval/trade_intent_return_audit_20261022/intent_audit.csv)；原账本快照为 [snapshot.json](../eval/trade_intent_return_audit_20261022/snapshot.json)，跨表核对为 [crosscheck.json](../eval/trade_intent_return_audit_20261022/crosscheck.json)，31 次行情查询及逐笔回包为 [market.jsonl](../eval/trade_intent_return_audit_20261022/market.jsonl)，精确计算及被选 tick 为 [results.json](../eval/trade_intent_return_audit_20261022/results.json)。',
'',
'行情采集于北京时间 10-22 **14:43:10–14:44:15**，IBKR server version 225、ibapi 10.49.2，使用独立只读行情 client ID 221042。原始文件 SHA-256 见 [sha256.json](../eval/trade_intent_return_audit_20261022/sha256.json)。运行 `python eval/trade_intent_return_audit_20261022/calculate.py` 可离线重算；已核验 13 个唯一 ID、W3/正式记录对应关系、10 条封顶、31 个完整无错误行情回包及价格时间窗口。',
]
path=repo/'docs'/'trade-intent-return-audit-20261022.md'
path.write_text('\n'.join(text)+'\n',encoding='utf8')
print(path)
