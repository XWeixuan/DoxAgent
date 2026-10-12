"""Generate the audit narrative from the validated grouped JSON files."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

base = Path(__file__).resolve().parent
files = sorted((base / 'export').glob('*.json'))
records = []
windows = set()
for file in files:
    payload = json.loads(file.read_text(encoding='utf-8'))
    assert 1 <= len(payload['records']) <= 5
    assert all(record['ticker'] == payload['ticker'] for record in payload['records'])
    windows.add((payload['window']['since_utc'], payload['window']['as_of_utc']))
    records.extend(payload['records'])
assert len(windows) == 1 and len(records) == 77
assert len({r['intent_id'] for r in records}) == len(records)
assert all(r['decision_origin'] == 'W3' and r['w3_decision']['expert_trade']['trade'] for r in records)
assert all(len(r['source_messages']) == r['case_summary']['member_count'] for r in records)

local = timezone(timedelta(hours=8))
def cst(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(local).strftime('%Y-%m-%d %H:%M:%S')

def money(value, places='0.000001'):
    return format(Decimal(value).quantize(Decimal(places)), 'f')

def reason(record):
    status = record['intent_status']
    if status.startswith('DUPLICATE_'):
        return '重复意图'
    if status == 'DELIVERY_FAILED':
        return '投递配置校验失败'
    linked = record['trade']['executions']
    if not linked:
        return '执行摘要缺失'
    code = linked[0]['execution']['entry_reason'] or ','.join(linked[0]['execution']['execution_reason_codes'])
    if code.startswith('IBKR_201') or code == 'INSUFFICIENT_MARGIN':
        return '保证金或可用资金不足'
    names = {'PORTFOLIO_CAPACITY_EXHAUSTED':'组合容量不足',
             'FRESH_QUOTE_UNAVAILABLE':'实时行情不可用',
             'RETRY_BUDGET_EXHAUSTED':'重试预算耗尽',
             'BROKER_REJECTED':'券商拒单'}
    return names.get(code, code)

by_ticker = defaultdict(list)
for record in records:
    by_ticker[record['ticker']].append(record)
filled = [r for r in records if r['trade_result']['entry']]
unfilled = [r for r in records if not r['trade_result']['entry']]
assert len(filled) == 12 and len(unfilled) == 65
assert all(r['trade_result']['exit'] and r['trade_result']['realized_pnl_usd'] is not None and
           r['trade_result']['pnl_state'] == 'REALIZED' for r in filled)
assert sum(r['trade_result']['read_projection_gap'] for r in records) == 2
since, as_of = next(iter(windows))

out = [
    '# 近 21 天 W3 交易意图复盘', '',
    f'只读拉取窗口：北京时间 **{cst(since)} 至 {cst(as_of)}**。以正式 `trade_intents.released_at` 为准，',
    '要求意图内有 W3 判定结果；包括最后未成交、投递失败及重复意图。此次 77 条均为 W3 专家判断产生的正式意图。', '',
    '## 总览', '',
    '| Ticker | 正式意图 / Case | 实际建仓且已平仓 | 零建仓成交 | 已实现净盈亏 USD |',
    '|---|---:|---:|---:|---:|',
]
for ticker, rows in sorted(by_ticker.items()):
    won = [r for r in rows if r['trade_result']['entry']]
    pnl = sum((Decimal(r['trade_result']['realized_pnl_usd']) for r in won), Decimal(0))
    out.append(f'| {ticker} | {len(rows)} | {len(won)} | {len(rows)-len(won)} | {money(pnl)} |')
total_pnl = sum((Decimal(r['trade_result']['realized_pnl_usd']) for r in filled), Decimal(0))
out.append(f'| 合计 | {len(records)} | {len(filled)} | {len(unfilled)} | {money(total_pnl)} |')
out += ['',
        '意图最终状态：' + '、'.join(f'`{key}` {count}' for key,count in sorted(Counter(r['intent_status'] for r in records).items())) + '。',
        '零建仓成交原因分组：' + '、'.join(f'{key} {count}' for key,count in Counter(reason(r) for r in unfilled).most_common()) + '。',
        '原因分组来自记录中的状态或错误码，原始原因与完整上下文保留在各 JSON。', '',
        '## 实际成交的 12 笔', '',
        '买卖点为归属该笔 lot 的真实 fill 加权均价；SHORT 的建仓为卖出、平仓为买入。',
        '盈亏是已实现净盈亏（USD），不是用买卖均价直接计算的毛盈亏。时间均为北京时间。', '',
        '| Ticker / Case | 方向 | 建仓 | 平仓 | 已实现净盈亏 USD |',
        '|---|---|---|---|---:|']
for r in sorted(filled, key=lambda r:(r['ticker'],r['intent_released_at_utc'])):
    entry, exit_ = r['trade_result']['entry'], r['trade_result']['exit']
    entry_text = f"{entry['side']} {entry['quantity']} 股 @ ${money(entry['average_price_usd'], '0.0001')}，{cst(entry['first_fill_at'])}"
    exit_text = f"{exit_['side']} {exit_['quantity']} 股 @ ${money(exit_['average_price_usd'], '0.0001')}，{cst(exit_['first_fill_at'])}"
    out.append(f"| {r['ticker']} `{r['case_id']}` | {r['trade_direction']} | {entry_text} | {exit_text} | {money(r['trade_result']['realized_pnl_usd'])} |")

gaps = [r for r in records if r['trade_result']['read_projection_gap']]
out += ['', '## 读模型差异与范围', '',
        'MU 有 2 条 `DUPLICATE_EVENT_TRADE` 正式意图：',
        *[f"- `{r['case_id']}`：意图已持久化、未投递且无成交；Case 读摘要仍显示 `intent_count=0`，执行摘要缺失。" for r in gaps],
        '',
        '这两条仍计入正式意图及零成交。另有 4 条带 W3 判定的 CLOSED 候选（3 条 `NOT_SELECTED`、1 条 `DUPLICATE_REALTIME_OUTPUT`）未释放正式意图，未混入 77 条交付记录。',
        '',
        '远端 Runtime/Executor 库和 V2 Read 投影分别以只读事务读取，未形成跨库原子快照；交易状态以本次拉取到的持久证据为准。没有重放 Case、提交订单或修改远端状态。',
        '文件分组及 SHA-256 见 [export/README.md](export/README.md)。', '',
]
(base / 'review.md').write_text('\n'.join(out), encoding='utf-8')
print(json.dumps({'records':len(records),'files':len(files),'filled':len(filled),'no_fill':len(unfilled),
                  'pnl_usd':money(total_pnl),'read_projection_gaps':len(gaps)},ensure_ascii=False))
