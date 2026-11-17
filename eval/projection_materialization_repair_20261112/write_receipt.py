import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

b = Path(__file__).resolve().parent
repo = Path('D:/DoxProjectionFollowup20261112')
a = json.loads((b / 'acceptance-90647e2-summary.json').read_text())
l = json.loads((b / 'latency-90647e2-final.json').read_text())
v = json.loads((b / 'release-verification-90647e2-final.json').read_text())

def fmt(n):
    return f'{n:.2f}' if n is not None else '未观测'

def clock(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')

rows = []
for source, label in [('research', '研究'), ('initialization', '初始化'), ('bus', '消息'), ('runtime', 'Runtime')]:
    x = a['sources'][source]
    lat = l['steady_mutation_latency'][source]
    rows.append(f"| {label} | {fmt(x['coordinate_lambda'])} | {fmt(x['coordinate_mu'])} | {x['distance_start']} → {x['distance_end']} | {lat['n']} | {fmt(lat.get('p95'))} | {fmt(lat.get('p99'))} | {fmt(lat.get('max'))} |")

work = []
for key, label in [('runtime:runtime_values:reference_deliveries', 'Reference'), ('runtime:runtime_values:policy_authorization_head', '授权head'), ('initialization:activation_revisions:', '激活revision登记'), ('initialization:ticker_active_revision:', '初始化指针'), ('runtime:v2_ticker_control:', 'Runtime生效切换'), ('bus:stream_members:', 'Bus成员')]:
    x = a['performance']['workloads'].get(key)
    if not x:
        work.append(f'| {label} | 无样本 |')
        continue
    target, count, bound = math.ceil(x['events'] * .95), 0, '>5秒'
    for n, limit in zip(x['latency_buckets'], ['≤50ms', '≤100ms', '≤250ms', '≤500ms', '≤1秒', '≤5秒', '>5秒']):
        count += n
        if count >= target:
            bound = limit
            break
    work.append(f"| {label} | {x['events']}条；P95桶 {bound}；最大 {fmt(x['max_seconds'])}秒 |")

p = a['resources']['doxagent-v2-v2-projector-1']
g = a['resources']['doxagent-v2-v2-maintenance-1']
h = a['host']
resources = [
    ('Projector CPU', f"平均 {fmt(p['cpu_cores_mean'])} 核，15秒区间最大 {fmt(p['cpu_cores_max_interval'])} 核", '平均≤1核'),
    ('Projector RSS', f"最大 {fmt(p['rss_mib_max'])} MiB", '≤1GiB'),
    ('Projector块写', f"平均 {fmt(p['write_mib_s_mean'])} MiB/s", '与GC合并评价'),
    ('Read GC块写', f"平均 {fmt(g['write_mib_s_mean'])} MiB/s", '未修改GC角色'),
    ('两者合计块写', f"平均 {fmt(p['write_mib_s_mean'] + g['write_mib_s_mean'])} MiB/s", '稳态≤5MiB/s，追赶≤10MiB/s'),
    ('全机CPU', f"平均 {fmt(h['cpu_busy_mean'])}%", '目标≤80%'),
    ('CPU PSI some avg60', f"均值 {fmt(h['cpu_psi_some_avg60_mean'])}%", '目标≤20%'),
    ('IO PSI some avg60', f"均值 {fmt(h['io_psi_some_avg60_mean'])}%", '目标≤5%'),
    ('根盘可用', f"核验时 {fmt(v['root_free_bytes'] / 2**30)} GiB", '15GiB停止试写/构建线'),
]
replacements = {
    '__IMAGE_ROWS__': '\n'.join(f"| {role} | `{x['image']}` | `{x['commit'][:12]}` |" for role, x in v['release_state'].items()),
    '__WINDOW__': clock(a['start']) + '–' + clock(a['end']),
    '__MINUTES__': fmt(a['seconds'] / 60),
    '__SAMPLES__': str(a['samples']),
    '__SOURCE_ROWS__': '\n'.join(rows),
    '__REPORTS__': str(a['performance']['reports']),
    '__MAPPED__': str(a['performance']['mapped']),
    '__SKIPS__': str(a['performance']['verified_skips']),
    '__WORKLOAD_ROWS__': '\n'.join(work),
    '__RESOURCE_ROWS__': '\n'.join(f'| {name} | {value} | {contract} |' for name, value, contract in resources),
}
text = (b / 'receipt-template.md').read_text(encoding='utf-8')
for key, value in replacements.items():
    text = text.replace(key, value)
assert '__SOURCE_ROWS__' not in text
(repo / 'docs/runtime-read-projection-followup-repair-receipt-20261112.md').write_text(text, encoding='utf-8')
print('receipt generated', len(text))
