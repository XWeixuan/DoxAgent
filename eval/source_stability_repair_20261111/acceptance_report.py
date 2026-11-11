"""Render saved evidence only; no production requests."""
import json,csv,collections,sys
from pathlib import Path
root=Path(__file__).parent;prefix=sys.argv[1];x=json.loads((root/(prefix+'.json')).read_text(encoding='utf-8-sig'));s=json.loads((root/(prefix+'-summary.json')).read_text(encoding='utf-8-sig'))
rows=list(csv.DictReader((root/(prefix+'-per-source.csv')).open(encoding='utf-8-sig')));cad=list(csv.DictReader((root/(prefix+'-per-unit-cadence.csv')).open(encoding='utf-8-sig')))
lines=['# 消息源稳定性修复：最终自然运行验收','',f"窗口：UTC {x['since']} — {x['until']}。排除 Reuters；不额外请求网站。",'', '## 计数边界','', '以下是按 physical_run_id 去重的有效采集周期，不是独立网站请求数、新文章数或正文成功数；CAPTURED 可包含明确标注的 PARTIAL 首页覆盖。未到期/在途不进完成分母。对比窗口长度不同，仅比较比例，不比较绝对吞吐。', '', f"修复前正式入口 {s['before_formal']['success_pct']}%；最终窗口 {s['after_formal']['success_pct']}%（{s['after_formal']['counts']}）。试验源单列 {s['after_test']['success_pct']}%（{s['after_test']['counts']}）。",'', f"配置轮询间隔漂移：{len(s['interval_drift'])}。这不代表实际成功间隔均达到目标；下面按每个 unit 统计，完整 P50/P95/最大值见 `{prefix}-per-unit-cadence.csv`。",'', '## 逐入口结果','', '| 入口 | 模式 | 有效周期/失败 | 有效率 | 最慢unit P95 / 最大间隔(s) |','|---|---|---:|---:|---:|']
for r in rows:
 sid=r['scope'].split(':')[0];cs=[u for u in cad if u['source_id']==sid and (':' not in r['scope'] or u['unit']==r['scope'].split(':',1)[1])]
 p95=max([float(u['p95_seconds']) for u in cs if u['p95_seconds']] or [0]);mx=max([float(u['max_seconds']) for u in cs if u['max_seconds']] or [0])
 lines.append(f"| {r['scope']} | {r['mode']} | {r['after_success']}/{r['after_failed']} | {r['after_success_pct']}% | {p95:.2f} / {mx:.2f} |")
lines+=['','## 补充覆盖与正文','',f"补充页真实传输证明：`{json.dumps(s['supplement_proofs'],ensure_ascii=False)}`。head_only不得称完整两页覆盖；覆盖欠账继续保留。",'', '正文为 body_outcomes 回执计数，不是唯一文章数；无新正文机会的入口不能凭列表成功宣称正文验收。','', '| final_site | outcome | reason | 回执 |','|---|---|---|---:|']
for b in x['body']:lines.append(f"| {b['final_site_id']} | {b['outcome']} | {b['reason'] or ''} | {b['n']} |")
lines+=['','## 真实交付','', '按本窗口 collected_at 及既有状态索引读取；raw条目按ticker计数，不能当独立文章数。','', '| source | processing | raw | standardized | published |','|---|---|---:|---:|---:|']
for d in x.get('delivery',[]):lines.append(f"| {d['source_id']} | {d['processing_status']} | {d['raw_entries']} | {d['standardized']} | {d['published_entries']} |")
r=x['resources'];lines+=['','## 资源与保留边界','',f"截止样本磁盘剩余 {r['disk_free']/1024**3:.2f} GiB；MemAvailable {r['meminfo']['MemAvailable']}；acquisition {r['acquisition_bytes']/1024**3:.2f} GiB；WAL {r['wal_bytes']/1024**2:.2f} MiB。PSI原样保存在JSON，单点不推导整窗分位。",'', 'Supervisor/Profile未重建；不改出口、Cookie、主备或既定周期。测试源、外部429、权限/认证、排队延期与完整覆盖欠账必须分开，不宣称全部长期稳定。30分钟短窗不替代24小时/跨原复发点老化验收。']
(root/(prefix+'.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8');print(root/(prefix+'.md'))
