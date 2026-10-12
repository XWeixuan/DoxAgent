"""Validate a remote JSONL stream, then write ticker chunks of at most five intents."""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent / 'export'
lines = [json.loads(line) for line in sys.stdin.buffer if line.strip()]
if not lines or lines[0].get('type') != 'manifest':
    raise SystemExit('missing remote export manifest')
manifest = lines[0]
records = [line['data'] for line in lines[1:] if line.get('type') == 'record']
if len(records) != len(lines) - 1 or len(records) != manifest['intent_count']:
    raise SystemExit('remote export did not return its declared full intent count')
ids = [r['intent_id'] for r in records]
if len(set(ids)) != len(ids):
    raise SystemExit('duplicate intent IDs')
if any(not r['source_messages'] or not r['w3_decision'] or not r['trade']['intents'] for r in records):
    raise SystemExit('incomplete source, W3, or intent evidence')
groups = defaultdict(list)
for record in records:
    groups[record['ticker']].append(record)
root.mkdir(parents=True, exist_ok=True)
expected = {f'{ticker}_{i//5+1:02d}.json' for ticker, rows in groups.items()
            for i in range(0, len(rows), 5)}
existing = {file.name for file in root.glob('*.json')}
if existing - expected:
    raise SystemExit('old JSON chunks would remain outside this export: ' + ', '.join(sorted(existing - expected)))
files = []
for ticker in sorted(groups):
    rows = sorted(groups[ticker], key=lambda r: (r['intent_released_at_utc'], r['intent_id']))
    for i in range(0, len(rows), 5):
        chunk = rows[i:i+5]
        path = root / f'{ticker}_{i//5+1:02d}.json'
        payload = {'format': manifest['format'], 'ticker': ticker,
                   'window': {'since_utc': manifest['since_utc'], 'as_of_utc': manifest['as_of_utc']},
                   'part': i//5+1, 'record_count': len(chunk), 'records': chunk}
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        path.write_bytes(raw)
        files.append((path.name, len(chunk), hashlib.sha256(raw).hexdigest()))

counts = Counter(r['ticker'] for r in records)
executed_cases = {r['case_id'] for r in records if any(ex['entry'] for ex in r['trade']['executions'])}
out = ["# W3 交易意图导出清单", '',
       '逐案交易结果和复盘见 [review.md](../review.md)。', '',
       f"- 截止 UTC：{manifest['as_of_utc']}", f"- 起点 UTC：{manifest['since_utc']}（向前 21 天）",
       '- 口径：正式 `trade_intents` 的 `released_at` 落在窗口内，且内含 W3 判定结果；包括重复、过期、投递失败和未成交意图。',
       f"- 正式意图：{len(records)}；Case：{len(set(r['case_id'] for r in records))}；实际建仓 Case：{len(executed_cases)}。",
       f"- W3 判定产生但未释放为正式意图的相邻记录：{manifest['adjacent_non_intents']}。这些未混入交付 JSON。",
       '- 买卖点采用归属于该笔 lot 的真实成交加权均价；盈亏采用读投影中的已实现净盈亏，未实现部分留空。',
       '', '## 分组', '']
for ticker, count in sorted(counts.items()):
    out.append(f'- {ticker}: {count} 条')
out += ['', '## 文件', '']
for name, count, sha in files:
    out.append(f'- [{name}]({name})：{count} 条；SHA-256 `{sha}`')
(root / 'README.md').write_text('\n'.join(out) + '\n', encoding='utf-8')
print(json.dumps({'record_count': len(records), 'case_count': len(set(r['case_id'] for r in records)),
                  'counts': counts, 'files': [f[0] for f in files], 'executed_cases': len(executed_cases)},
                 ensure_ascii=False))
