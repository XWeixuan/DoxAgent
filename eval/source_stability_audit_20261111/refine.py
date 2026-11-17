import csv,json,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('eval/source_stability_audit_20261111');v=json.loads((p/'audit.json').read_text(encoding='utf-8-sig'));d=json.loads((p/'details.json').read_text(encoding='utf-8-sig'))
plan=Path('dev_plan/workflow_v2/message_sources_post_cadence_stability_root_cause_repair_plan_20261111.md');md=plan.read_text(encoding='utf-8-sig')
rows=list(csv.DictReader((p/'per-source-diagnosis.csv').open(encoding='utf-8-sig')))
def local(s):
 if not s:return '无'
 t=datetime.fromisoformat(s.replace('Z','+00:00'));t=t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
 return t.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')
def esc(s):return str(s).replace('|','&#124;').replace('\n',' ')
def counts(c):return '，'.join(str(k)+':'+str(n) for k,n in sorted(c.items())) or '无记录'
def specific(k,g):
 r=g['recent_reasons'];failed=g['last3h'].get('FAILED',0);empty=g['request']['empty_trace_failed']
 if k.startswith('corporate_ir_press_releases|IR:'):
  ep=k.split('|IR:')[1]
  if ep in ['be.press','mu.ir']:return 'HTTP/RSS双lane共同失败才阻塞；RSS已有429，健康HTTP可覆盖但自身超时仍使发现中断。按lane遵循Retry-After，修HTTP准入/取消及分支证据；不重跑/重置另一lane。'
  if ep in ['cohr.corporate','cohr.financial','mu.newsroom','onds.press','snow.press','ceg.press']:return f'以TimeoutError为主，近3h失败{failed}次，其中{empty}次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。'
  if ep=='intc.press':return 'HTTP/RSS双lane超时；近3h 63次失败中59次无发送事件，成功响应为200。优先共同准入/取消回收，保留两lane独立checkpoint；不因双lane配置存在就视作可接替。'
  if ep=='rklb.ir':return 'HTTP/RSS双lane少量超时，绝大多数304/200有效；保留race现状，复用准入与截止修复，不为偶发故障迁移浏览器。'
  if ep=='rklb.updates':return 'TimeoutError/URLError且urllib读线程参与；取消后线程仍须排空，不能提前释放许可造成假容量。记录线程排队与实际读体阶段，修单入口host资源，不牵连rklb.ir。'
 if k=='digitimes_tw_rss':return '近3h256次失败，126次无发送事件；其余记录未获状态；281次200对应成功。没有401/403/付费门槛证据。修直接HTTP的准入/超时与取消释放，保留“仅免费正文”业务规则。'
 if k=='enterprise_apps_official_rss':return '按入口拆分：Workday响应前等待153次，SAP153次；Salesforce三API分别8/30/13次。共享层先修，Workday30s及SAP/Salesforce20s保留，禁止单入口失败拖累另四入口。'
 if k=='lakehouse_platform_official_rss':return '两次ReadError，近3h1059次200、6次未获状态；是读传输间歇失败而非已证实解析/鉴权。保持20s，走既有有界读失败恢复与下一正常周期。'
 if k=='ibkr_news':return '最近小时1次TimeoutError、1次UNPROVEN_LISTING，其余1775成功；IBKR trace没有HTTP状态属正常，不能归入HTTP池/网站拒绝。保留20s，检查SDK完成/空结果证明及截止后的旧回调隔离。'
 if k=='google_news_search_rss':return '2次publisher_response_timeout、1次HTTPStatusError；近3h1次503与2次无状态，其他4154次200。保留当前周期，沿既有请求后有界恢复，不能因3次偶发故障重构整个搜索源。'
 if k=='finnhub_company_news':return '同时有publisher_response_timeout与外层TimeoutError，另有ReadError/pre-send；近3h387次失败中235次无发送事件。先共享层阶段与资源回收，再修实际读响应失败；不归为单一供应商慢或429。'
 if k=='sdxcentral_news':return 'SiteAccessError与publisher_endpoint_backoff混合，最近失败形状ConnectTimeout；近3h17/18失败无发送事件。先修准入/连接恢复，只有真实Challenge证据才走人工维护，不能只延长冷却。'
 if k=='yahoo_finance_news':return '近小时13次TimeoutError、5次RuntimeError、1次browser_coverage_pending。HTTP与CDP覆盖状态分别记账，保留30s；coverage待补不得当完整成功，也不因BODY错误冻结有效listing。'
 if k=='calcalist_search':return '搜索JSON近3h274次403、68次有效200、261次无状态；cse.js另有91次200。拒绝与本地超时并存；只对可证实token失效刷新，保留60s，明确拒绝时按入口恢复或更换许可查询途径。'
 if k=='aviationweek_rss':return '五个HTTP分类都能取得有效列表，超时比例不同，另有1次503；fallback错误保留。按分类隔离与分lane提交，不能据某一分支失败宣称全站403。'
 if k=='aviationweek_rocketlab':return '28次RuntimeError及8次UNPROVEN_LISTING；近3h大量304/200成功。区分真实fetch失败与无验证证明/恢复待办，保持baseline与已验证304链，不把所有空列表当失败。'
 if k=='defensescoop_space':return '当前小时主要UNPROVEN_LISTING，而近3h已有502次有效304/200；最新另见endpoint TimeoutError。检查backoff/304基线如何丢失采集证明，不仅重试网络，不能把未知当成功。'
 if k=='nist_news':return 'publisher_endpoint_backoff包裹TimeoutError；大多数304有效。恢复时保留已验证baseline并正确安排下一到期，避免退避截止后仍只重复旧错误。'
 if k=='canary_media':return '持续HTTP_LISTING_FAILED_RSS_FALLBACK；近3h202个发送事件均无响应状态，未发现首200。分解连接后/响应头/读体与fallback期限，先公共层，再按现有可用lane交付，不能套用WP第二页根因。'
 if k=='utility_dive':return '近3h296个200仅92个验证，223轮失败；没有WordPress强制第二页路径。优先读体完成/解析与验证证据边界，有限失败样本复现后窄修，尚不能唯一指定网站侧根因。'
 if k=='nsf_news':return '近3h198次200/184次采集成功，148次发送后无状态，主要fallback失败。修期限和有效结果提交，单列200未验证样本，不把外层TimeoutError当根因。'
 if k=='quantum_computing_report':return '近3h148次200/138次采集成功、157次发送后无状态；主HTTP与fallback失败混合。先修共享准入与响应完成，再独立保留有证据的fallback结果，保持20s。'
 if k=='huggingnews_rss':return '375次200形成成功；64个发送后无状态、1个失败周期无事件，主要HTTP_LISTING_FAILED_RSS_FALLBACK。响应阶段问题占可见记录，不能只靠重建未发送池解决；修分支截止、失败阶段与有证据的RSS交付。'
 if k=='power_magazine_energy':return '近3h1371轮失败中1278轮无发送事件，成功1131；9条ticker绑定对应7个不同FEED，双lane潜在14条请求/全源一轮而非18。修公平准入/取消与按feed去重计费，保留抢先有效结果。'
 if k=='ans_nuclear_newswire':return '近3h219个200、630个发送后无状态，HTTP/RSS共同超时；失败周期并非无发送。分解响应等待与双lane预算，保留有证明的先到结果，不只增加准入重试。'
 if k=='shephard_uncrewed':return '近3h725个发送后无状态、153次200，另66个失败无事件；RuntimeError包裹双endpoint超时。隔离endpoint与有效列表提交，先公共层再定位连接/读体，未见明确403/429证据。'
 if k=='unmannedairspace_counteruas':return '近3h多feed有304/200成功、345个发送后无状态、23个失败无事件。HTTP/RSS双lane超时需按feed与phase治理，不能整站重试或由一条lane错误清另一条checkpoint。'
 return None
for row in rows:
 k=row['scope'];g=v['groups'][k];new=specific(k,g)
 if new:row['repair']=new
 row['frequency']='保持'+ '/'.join(sorted(g['periods'],key=float))+'s；真实429仅对受限lane按Retry-After退避' if k in ['spacenews_rss','corporate_ir_press_releases|IR:be.press','corporate_ir_press_releases|IR:mu.ir'] else '保持'+ '/'.join(sorted(g['periods'],key=float))+'s'
 row['window_success']=g['status'].get('CAPTURED',0);row['window_failed']=g['status'].get('FAILED',0);row['window_inflight']=g['status'].get('DISPATCHED',0)
 base=g['hourly'].get('2026-11-10T20',{});row['baseline_04_05_success']=base.get('CAPTURED',0);row['baseline_04_05_failed']=base.get('FAILED',0)
 row['last3h_success']=g['last3h'].get('CAPTURED',0);row['last3h_failed']=g['last3h'].get('FAILED',0)
 row['last3h_response_codes']=json.dumps(g['request']['codes'],ensure_ascii=False)
 row['recent_failure_shape']=json.dumps(d['latest'].get(k,[])[:1],ensure_ascii=False)
with (p/'per-source-diagnosis.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
lines=['| 范围 | 模式 | 首完整小时成功/失败 → 当前小时成功/失败 | 最近3h证据 | 修复决策 | 频率 |','|---|---|---:|---|---|---|']
for row in rows:
 if not int(row['last_hour_failed_or_deferred']) and row['mode']!='test':continue
 k=row['scope'];g=v['groups'][k];rq=g['request']
 evidence=f"周期成功/失败 {row['last3h_success']}/{row['last3h_failed']}；HTTP记录 {counts(rq['codes'])}；失败无发送事件 {rq['empty_trace_failed']}。最近小时原因 {counts(g['recent_reasons'])}"
 latest=d['latest'].get(k,[])[:1]
 if latest and latest[0]['message'] not in ['None','TimeoutError:']:evidence+='；最新形状 '+latest[0]['message']
 fix=f"{row['priority']}："+(row['repair'] if specific(k,g) else row['cause']+'；'+row['repair'])
 lines.append('| '+' | '.join(esc(x) for x in [k,row['mode'],f"{row['baseline_04_05_success']}/{row['baseline_04_05_failed']} → {row['last_hour_success']}/{row['last_hour_failed_or_deferred']}",evidence,fix,row['frequency']])+' |')
start=md.index('| 范围 |');end=md.index('\n## 验收与发布边界',start);md=md[:start]+'\n'.join(lines)+'\n'+md[end:]
md=md.replace('当前三小时0成功/540失败','当前三小时0成功/539失败（另1次在途）')
md=md.replace('### A. 长期连接／准入资源会衰减，已有恢复机制覆盖不到等待阶段（P0）','### A. 多源随运行时间衰减，已有恢复机制覆盖不到等待阶段（P0）')
md=md.replace('### B. 部分首页其实已返回200，但整轮被强制补充步骤拖死（P0）','### B. 首响应与整轮成功强耦合，补充步骤是明确风险点（P0）')
md=md.replace('每5–15分钟读累计账','每5–15分钟读增量累计账（远端聚合后仅取脱敏小结果；全窗重扫留到阶段截止）')
md=md.replace('下表包含当前小时每一个有失败的正式源，以及试验源。全部健康入口、完整小时走势、最新失败shape另见CSV/JSON。共享层分类是明确的失败阶段/修复路线，不等于每个网站内部原因已经被唯一证明。','下表包含当前小时每一个有失败的正式源，以及试验源。首完整小时为北京时间04:00–05:00；当前小时为21:40:17–22:40:17。全部92个入口的全窗、首完整小时、当前小时统计另见CSV；小时走势在audit.json。最近失败形状来自14:47 UTC前的补充读取，可能晚于主快照22:40:17北京截止，不能将其次数混入主统计。\n\nHTTP记录是采集账中请求事件的状态分布，不等同独立物理请求总量，None表示未记录响应状态；IBKR为SDK，None不是HTTP失败。失败无发送事件仅定位观测边界，不能单独证明许可泄漏。原因计数可能包含部分成功轮中的分支错误，因此不强求与FAILED轮数相等。聚合初小时已失败的入口须按“遗留/持续故障”处理，不能都称提频新故障。共享层分类与修复路线不等于每个网站内部原因已经被唯一证明。')
md=md.replace('acquisition文件仍约13.18GiB','acquisition文件仍约13.18GiB')
# Add operational implementation pointers without expanding runtime design.
block='''
### 实施文件与最小完成条件

| 工作 | 现有文件/接口 | 完成条件 |
|---|---|---|
| 等待与连接恢复 | `src/doxagent/message_bus_v2/publisher_http.py`：`joint_permits`、`PublisherClients`；`poll_trace.py`：`TRANSPORT_PROGRESS`、`httpx_request`、`install` | 保留真实发送证明；既有周期只增加有限阶段标量。HTTPX/hcore未暴露的pool等待不凭空计算，记unknown并以外部租约计数/阶段定位。取消后许可与client租约守恒，容量等待不误触发重建。 |
| IR长期退化 | `corporate_ir/http.py`：`PublicHttp._client/fetch/_stdlib` | NBIS坏host可在不重启Collector时恢复；旧client有界排空，线程仍在工作时不释放假许可，保留正常host与checkpoint。 |
| 首页/补充及回退 | `public_listing.py`：`PublicListingAdapter._one`；`rss_http_race.py`；`collection_plane/worker.py`现有`on_page`与checkpoint提交 | 首页通过解析才提交；补充页独立欠账、到期重试，保留历史覆盖；RSS成功带其自己的验证证据，主HTTP失败持续可见。不能凭200绕过parser/transport gate。 |
| NRC事件契约 | `energy_sources.py`：`field`、`parse_events`；`technology_sources.py`现有endpoint状态 | 复现当前字段错误的fixture通过；一条坏报文隔离、同页正常报文继续，合法空页/异常壳严格区分；修复后只释放对应parser版本的旧重试状态。 |
| 浏览器延期 | Site Access现有调度／身份队列及调用方SiteAccessError分类 | 并发硬上限不变，CRAWLER/BODY公平与deadline分开；排队延期不自动生成人工Challenge，也不能仅改错误名而不改善实际成功间隔。 |

不引入新守护服务、新数据库或通用状态平台。复用已有lease/client generation、checkpoint和结果模型；确需新增字段时同时检查已部署旧Bus的严格schema兼容。重要代码修改写changelog。离线回归先锁定取消/释放和结果边界，再在自然流量中验证，不以“监控补齐”替代真实修复。

当前`worker`在valid分支已提交`next_checkpoint`，未证实通用的“零新增文章就丢checkpoint”缺陷；不得顺手重写。POWER当前9条ticker绑定实际对应7个唯一FEED，预算按唯一FEED及真实lane计算，不能按9×2误算独立18请求。
'''
md=md.replace('\n## 逐源／IR入口诊断与决策','\n'+block+'\n## 逐源／IR入口诊断与决策')
# Link evidence at the bottom using relative paths within repository docs.
md=md.replace('证据目录：eval/source_stability_audit_20261111/，包含完整窗口聚合、最新失败形状、checkpoint摘要、最近3小时有限日志形状和逐源CSV；排除Reuters。','证据：[`audit.json`](../../eval/source_stability_audit_20261111/audit.json)、[`details.json`](../../eval/source_stability_audit_20261111/details.json)、[`逐源CSV`](../../eval/source_stability_audit_20261111/per-source-diagnosis.csv)。包含完整窗口聚合、最新失败形状、checkpoint摘要、最近3小时有限日志形状；排除Reuters。')
plan.write_text(md,encoding='utf-8')
print(json.dumps({'rows':len(rows),'plan_bytes':plan.stat().st_size,'csv_bytes':(p/'per-source-diagnosis.csv').stat().st_size}))
