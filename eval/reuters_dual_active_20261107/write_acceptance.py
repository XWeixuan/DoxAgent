"""Summarize sanitized passive receipts without any production mutations."""
import collections,json,sys
from datetime import datetime
from pathlib import Path
p=Path(sys.argv[1]);v=json.loads(p.read_text(encoding='utf-8-sig'))
start=datetime.fromisoformat(v['since'].replace('Z','+00:00'));end=datetime.fromisoformat(v['until'])
access=[x for x in v['events'] if x['category']=='ACCESS_RESULT']
success={i:sum(x['count'] for x in access if x['identity']==i and x['purpose']=='CRAWLER' and x['disposition']=='SUCCESS') for i in ('digitimes-de-1','reuters-backup')}
deferred=collections.Counter({k:sum(x['count'] for x in access if x['purpose']==k and x['disposition']=='BUDGET_DEFERRED') for k in ('CRAWLER','BODY')})
units=[(t['ticker'],k,s) for t in v['poll_states'] for k,s in t['units'].items()]
commit=next((c.get('commit','unknown') for c in v['containers'] if c['role']=='v2-site-access'),'unknown')[:8]
rows=['# Reuters 双活最终版本自然运行验收', '', f"代码 `{commit}`，Scope revision 3；窗口 UTC {v['since']} → {v['until']}（{(end-start).total_seconds()/60:.2f}分钟）。", '', '本记录只读持久回执，不请求新闻网站、不调用打码Provider。所有过渡版本及未通过窗口保留在同目录，最终版本未混计旧成功。', '', f"- 真实CRAWLER成功：Main {success['digitimes-de-1']}、Backup {success['reuters-backup']}；不把CAPTURE_CONSUMED计作新访问。",f"- 延期：CRAWLER {deferred['CRAWLER']}、BODY {deferred['BODY']}；逐分钟记录如下，不隐去发布初期延期。", f"- 当前活跃单元 {len(units)}（"+str(dict(collections.Counter(k.split(':')[0] for _,k,_ in units)))+'）；目标间隔 '+str(dict(collections.Counter(s['schedule_interval_seconds'] for _,_,s in units)))+'。', '- 当前状态 '+str(dict(collections.Counter(s['status'] for _,_,s in units)))+'。', '- 正式正文结果 '+str(v['body'])+'；为空时表示本窗没有FULL/SHORT_FULL证明，不能宣称正文已验收。', f"- 同一成功operation跨身份重复成功：{v.get('successful_operation_ids_multiple_identities')}；不据此宣称所有不同operation的网页请求绝对不重复。", f"- PROBE started/finished {v['probes']['started']}/{v['probes']['finished']}；没有自然探测时，不宣称探测分支生产覆盖。", '', '| Ticker | QUERY数 | 完整QUERY集合 | 当前活跃TAIL数 | 当前非成功单元数 |','|---|---:|---:|---:|---:|']
for q in sorted(v['query_coverage'],key=lambda x:x['ticker']):
    owned=[(k,s) for t,k,s in units if t==q['ticker']]
    rows.append(f"| {q['ticker']} | {q['required_queries']} | {q['captured_complete_cycles']} | {sum(k.startswith('TAIL:') for k,s in owned)} | {sum(s['status']!='succeeded' for k,s in owned)} |")
rows+=['','## 延期时间线','', '| UTC分钟 | 类型 | 结果 | 数量 |','|---|---|---|---:|']
rows += [f"| {x['minute']} | {x['purpose']} | {x['disposition']} | {x['count']} |" for x in v.get('access_by_minute',[])]
rows+=['','## 实际完成间隔','', '间隔取cycles.finished_at，不能把dispatch_at间隔或配置60秒冒充真实完成时刻。TAIL会停用后再激活，跨不活跃期的长间隔不能直接等同漏轮询；失败回执仍单独保留。']
for kind in ('QUERY:','TAIL:'):
    gaps=[x for x in v['capture_gaps'] if x['unit'].startswith(kind) and x.get('steady_median_seconds') is not None]
    if gaps:
        rows.append(f"- {kind} 排除首2分钟后有连续完成样本的单元{len(gaps)}个，各单元中位间隔范围{min(x['steady_median_seconds'] for x in gaps):.2f}–{max(x['steady_median_seconds'] for x in gaps):.2f}s，最大单次间隔{max(x['steady_max_seconds'] for x in gaps):.2f}s。")
new_recoveries=[x for x in v['recoveries'] if datetime.fromisoformat(x['opened_at'].replace('Z','+00:00'))>=start]
new_jobs=[x for x in v['jobs'] if x.get('submitted_at') and datetime.fromisoformat(x['submitted_at'].replace('Z','+00:00'))>=start]
rows+=['','## 覆盖边界与资源','',f'- 新恢复事件{len(new_recoveries)}，新Provider提交{len(new_jobs)}；旧job更新时间变化不算新增提交，不把旧费用记为本轮费用。','- 双身份实际轮询已覆盖；生产未故意阻断一个身份或注入故障，单身份接替/恢复加入由回归覆盖。短窗无新Challenge不等于长期无风控。', '- 本窗未制造RETRY或购买打码，不覆盖打码解码与再次封禁分支。',f"- 磁盘可用{v['resources']['disk_free']/1024**3:.2f}GiB，MemAvailable {v['resources']['mem_available']}，Registry WAL {v['resources']['registry_wal_bytes']}字节。CPU PSI为单次主机样本，不能归因为该补丁或编造CPU分位。",'- 没有新增浏览器、身份、持久队列、截图或全量抓包；日志及页面历史仍由既有治理维护。本轮不是长期GC/内存泄漏验收。','', '完整字段和各单元完成间隔见同名JSON；实例/PID及真实预算、源码一致性见相应版本的runtime/release JSON。']
p.with_suffix('.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
print('\n'.join(rows[:13]))
