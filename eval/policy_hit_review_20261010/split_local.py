"""Validate a remote JSONL stream, then write ticker chunks of at most five hits."""
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
if len(records) != len(lines) - 1 or len(records) != manifest['hit_count']:
    raise SystemExit('remote export did not return its declared full hit count')
ids = [r['policy_hit_id'] for r in records]
if len(set(ids)) != len(ids):
    raise SystemExit('duplicate policy hit IDs')
if any(not r['source_messages'] or not r['policy']['frozen_match'] for r in records):
    raise SystemExit('incomplete source or frozen policy evidence')
groups = defaultdict(list)
for record in records:
    groups[record['ticker']].append(record)
root.mkdir(parents=True, exist_ok=True)
files = []
for ticker in sorted(groups):
    rows = sorted(groups[ticker], key=lambda r: (r['hit_observed_at_utc'], r['policy_hit_id']))
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
executed_cases = {r['case_id'] for r in records if r['case_summary']['trade']['state'] == 'EXECUTED'}
out = [f"# Policy 命中导出清单", '',
       f"- 截止 UTC：{manifest['as_of_utc']}", f"- 起点 UTC：{manifest['since_utc']}（向前 21 天）",
       '- 口径：Policy 命中观测发生时间；含历史修订中曾命中的记录。',
       f"- 命中记录：{len(records)}；Case：{len(set(r['case_id'] for r in records))}；实际建仓 Case：{len(executed_cases)}。",
       '- 同一个 Case 命中多条 Policy 时逐条导出，交易只按 Case 关联；`hit_policy_released_for_trade` 标明该 Policy 是否为释放交易的 Policy。',
       '- 买卖点采用归属于该笔 lot 的真实成交加权均价；盈亏采用读投影中的已实现净盈亏，未实现部分留空。',
       '', '## 分组', '']
for ticker, count in sorted(counts.items()):
    out.append(f'- {ticker}: {count} 条')
out += ['', '## 文件', '']
for name, count, sha in files:
    out.append(f'- `{name}`：{count} 条；SHA-256 `{sha}`')
(root / 'README.md').write_text('\n'.join(out) + '\n', encoding='utf-8')
print(json.dumps({'record_count': len(records), 'case_count': len(set(r['case_id'] for r in records)),
                  'counts': counts, 'files': [f[0] for f in files], 'executed_cases': len(executed_cases)},
                 ensure_ascii=False))
