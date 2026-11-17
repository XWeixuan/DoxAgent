import json,csv,collections
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('eval/source_stability_audit_20261111');v=json.loads((p/'audit.json').read_text(encoding='utf-8-sig'));d=json.loads((p/'details.json').read_text(encoding='utf-8-sig'))
def local(s):return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S') if s and '+' not in s and 'Z' not in s else datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S') if s else '无'
def diagnosis(k,g):
 sid=g['source_id'];req=g['request'];n=sum(g['recent'].values());f=g['recent'].get('FAILED',0)
 if sid=='nrc_event_notifications':return ('已确认：解析契约过严，单条报文使整日失败','P0','energy_sources.field/parse_events 支持实际表格/标签变体，单事件隔离；保留事件号、日期及正文硬校验；不能用频率修复解析错误。','保持20s')
 if k.endswith('IR:nbis.press'):return ('已确认连接池等待失败；未定位到具体遗失/占用连接','P0','corporate_ir/http.py 单 host HTTPX 池增加 pending/active/phase；取消释放回归，证实坏池后只重建该 host client，保留 checkpoint。','保持20s')
 if sid in ('technews_hardware','quantum_zeitgeist'):return ('已确认首响应200而整轮失败；补充请求为高概率阻断点','P0','拆分首页与补充页成功边界，首页独立解析提交；补充工作持久化重试并验收完整覆盖，不能直接删除第二页/分类页。','保持原档：TechNews20s / Quantum60s')
 if sid in ('benzinga_news','sec_edgar','finnhub_company_news'):return ('已确认大部分失败缺少发送证据；准入/连接阶段老化待分层证实','P0','修共享准入公平/取消守恒与容量等待记账，PoolTimeout 定点池治理；失败不得一概重建连接或当成供应商限流。','保持15/10/30s，先修内部等待')
 if sid=='calcalist_search':return ('已确认第三方 Google CSE 403，兼有本地超时','P1','CSE session/token 分型；只对明确 token 失效同路刷新一次。真实拒绝改用许可的稳定搜索入口；不可拿 HTTP200脚本证明搜索成功。','保持60s；必要时仅受限入口遵循服务端退避')
 if sid=='spacenews_rss':return ('已确认429，另有超时','P1','优先条件请求/现有获准HTTP发现路径；按 Retry-After 控制被限流 lane，不压低其他源；无证据不能承诺所有请求照发仍永不429。','不全局回调；该入口允许服务端要求的临时退避')
 if sid in ('investing_ticker_news','investorshub_ticker_news','barrons_ticker_news','yahoo_finance_news','sdxcentral_news'):return ('主要间歇准入/控制延期；不等同人工验证码','P1','分开公共HTTP与真实浏览器，检查队列公平/短3秒窗口/BODY争用；身份故障定点恢复，登录权限按BODY单列。','保持现档，不换IP/Profile掩盖')
 if sid in ('canary_media','utility_dive','quantum_computing_report','nsf_news','huggingnews_rss'):return ('HTTP/回退共同失败或200未形成有效列表；外层异常丢失细节','P1','复用现有 HTTP/RSS race；真实可用lane独立交付，保留主lane失败；分离解析/读体/等槽，按 source 保存失败形状。','保持20s（Quantum Report20s），先修调用链')
 if sid in ('army_news_releases','army_news_rss','tomshardware_rss'):return ('同一时刻停止成功；跨传输共性故障，非已证实三站同时风控','P0','优先排除共享准入、线程/存储等待和各自池退化；失败阶段确认后只重建坏资源，不重启全部服务。','保持20s')
 if sid in ('ans_nuclear_newswire','shephard_uncrewed','aviationweek_rss','aviationweek_rocketlab','unmannedairspace_counteruas'):return ('混合连接/读取超时与回退契约失败','P1','先共享层修复；按每feed保留已验证结果、分支错误和补充待办；AviationWeek RSS403与503单列，不误判全部网站拒绝。','保持现档')
 if sid.endswith('_massive'):return ('试验源：准入/请求或回退超时，未证明供应商拒绝','P1','先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。','先保持试验频率；调公平份额不等于降正式源频率')
 if sid=='power_magazine_energy':return ('双lane超时、多feed竞争，共享层压力显著','P0','18条请求/轮按真实请求算成本，保留抢先有效页，公平仲裁与次lane截止；不因待发等槽消耗全轮网络超时。','保持20s')
 if sid=='corporate_ir_press_releases':return ('IR入口混合池等待/读体/429；同网站端点不连坐','P1','PublicHttp按host池和阶段治理；BE/MU RSS429按lane退避，健康HTTP继续；RKLB urllib线程取消必须排空后释放许可。','各入口现20/30s不变')
 if f:return ('间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据','P2','不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。','保持现档')
 return ('最近1小时无FAILED；不据单窗宣称长期稳定','观察','保留现配置作为对照，不进行无证据修改。','保持现档')
rows=[]
for k,g in sorted(v['groups'].items()):
 n=sum(x for st,x in g['recent'].items() if st!='DISPATCHED');f=g['recent'].get('FAILED',0)+g['recent'].get('DEFERRED',0);cause,pri,fix,freq=diagnosis(k,g)
 rows.append({'scope':k,'mode':g['delivery'] or 'production','last_hour_success':g['recent'].get('CAPTURED',0),'last_hour_failed_or_deferred':f,'last_hour_completed':n,'success_pct':round(g['recent'].get('CAPTURED',0)/n*100,2) if n else None,'last_success_beijing':local(g['last_success']),'classification':'持续阻塞' if n and g['recent'].get('CAPTURED',0)==0 else '高频间歇失败' if n and f/n>=.1 else '低频间歇失败' if f else '本小时无失败','cause':cause,'priority':pri,'repair':fix,'frequency':freq,'recent_reasons':json.dumps(g['recent_reasons'],ensure_ascii=False),'empty_trace_failed_3h':g['request']['empty_trace_failed']})
with (p/'per-source-diagnosis.csv').open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
formal=[r for r in rows if r['mode']!='test'];counts=collections.Counter(x['classification'] for x in formal)
md=['# 消息源提频后持续／间歇失败：根因排查与可执行修复方案（2026-11-11）','',
'## 结论与边界','',
'优先保留现有业务轮询目标，修复内部准入／连接池生命周期、首页与补充工作成功边界、NRC解析契约与分支恢复。没有证据支持将全部失败归因于提频或网站风控，也没有证据支持一键回调所有频率。真正的429/明确拒绝须独立处理：不能承诺在服务端明确限流时仍无条件按原请求次数发送。保留“业务发现周期”与“某受限lane的实际请求退避”的区分。',
'本轮只排查及产出方案：没有修改业务代码、配置、出口、Profile，没有重启/发布，没有主动向新闻网站或验证码供应商发送测试请求。Reuters在SQL与方案中排除，不处理其问题。','',
'## 时间、数据与版本','',
f'- 主窗口：北京时间 11-11 03:54:30 至 {local(v["end"])}，约18小时46分。近期细查窗为最近3小时；状态表为最近1小时（{local(v["recent_since"])}—{local(v["end"])}）。不是完整24小时，更不是连续一个自然日。',
f'- 去重检查 {sum(sum(g["status"].values()) for g in v["groups"].values()):,} 个采集周期、{len(rows)} 个源/IR入口范围。按 physical_run_id 去掉 ticker fanout；CAPTURED仅表示周期取得有效采集，不等于所有阶段无错误、新文章或一次新的外部网络访问。',
'- 当前 Collector 仍为 c232f094、容器自03:53:43持续运行；SiteAccess/Supervisor05d60622，Bus8c203e62、BODY9a38f508。源治理容器本窗口无重启/OOM。远端主线虽已到3348f2fc，不代表这些容器已加载该主线的全部代码。不得把其他角色发布假定为Collector已更新。',
'- 上轮首窗与最终窗见 docs/message-source-cadence-rollout-20261111.md：最终窗11198 CAPTURED、253 FAILED、224截止时在途，本就“部分通过”；用户后来观察到的恢复段在小时历史中确实存在，随后再次衰减。',
f'- 正式源/入口最近小时分类：{dict(counts)}。试验 Massive 单列，不冒充正式源故障率。表格按源/入口聚合，不能据聚合最大连续失败数声称每个ticker都相同。','',
'## 从“恢复→复发”的机制看问题','',
'### A. 长期连接／准入资源会衰减，已有恢复机制覆盖不到等待阶段（P0）','',
'- SEC在北京时间11-11 06:00–07:00（UTC10日22时）有3173次成功、0失败，当前小时505成功/230失败；Benzinga早期约1900成功/小时，当前1430成功/120失败。当前3小时，Benzinga334个FAILED周期中320个没有请求事件；SEC667个中642个无请求事件。并非大量返回429，不能用“供应商太慢”解释。',
'- NBIS IR前数小时每小时约178–179成功/180次，16:04:47最后成功，此后当前三小时0成功/540失败；有明确PoolTimeout，近期无发送回执。连接池等待阶段已确认；究竟是连接未归还、长读体占槽还是池对象状态损坏，现有遥测不能唯一证明。',
'- Army新闻、Army RSS、Tom\'s Hardware原本数小时每小时约180成功，最后成功均集中于北京时间21:49:07–08，随后大面积超时。跨不同站与传输同时衰减更支持公共调度/等待因素；不能直接断言三站同时触发风控，也不能武断说它们共用同一个HTTP连接池。',
'- 代码链：publisher_http.joint_permits 用多个Semaphore的 locked/_value 与 held 预约来回移动，缺乏显式FIFO/阶段等待证据；外层45/30/12秒截止含准入排队，但 PublisherClients 的8秒计时与 _transport_timeout 只在 joint admission 后才启动。若卡在准入，外层先取消，既没有发送回执，也不会进入已有“连续pre-send timeout更换client”的恢复路径。该覆盖缺口是代码确认；具体某一次是否为活锁/饥饿须后续可复现测试与阶段指标证明。',
'- corporate_ir/http.py 的host HTTPX client固定2连接、pool timeout1秒，_client只创建不做坏池恢复；重复PoolTimeout不会重建该host。poll_trace.install先占host与全局许可，等待时间又与网络执行混在外层截止中。',
'- 修复边界：不通过全局重启、加大所有timeout或清Cookie恢复；也不能把“无发送记录”一律视作坏池（DNS/TLS/追踪缺口也可能无发送）。','',
'### B. 部分首页其实已返回200，但整轮被强制补充步骤拖死（P0）','',
'- TechNews近3小时WordPress JSON有456次200，但主接口和分类HTML总共仅2个验证结果；最近1小时0成功/180失败。Quantum Zeitgeist首接口169次200，仅2次验证，最近1小时0/60。两者checkpoint的listings为空，与长期未建立完整成功状态一致。',
'- public_listing._one 的同一个12秒scope覆盖主请求及补充工作：wordpress强制offset100第二页；technews_embed强制再抓分类页。任一补充失败就抛弃整轮，转RSS；fallback仍附带主失败、清空next_checkpoint/recovery_delta，且不能续主爬虫健康。首次主响应成功不等于整个采集契约通过，必须把二者分开。',
'- 不应简单删第二页/分类页，那会损失现有覆盖。改成“验证首页先提交＋补充待办独立有界重试”，保留页游标、未完成覆盖和错误；补充完成前不能宣称FULL coverage。仍使用现有unit/checkpoint/on_page，不新增一个平台。',
'- Utility Dive主页200但仅部分被验证；其代码没有上述WordPress强制第二页，不能套用同一精确根因。应单独分解读体、解析及已有 fallback 的错误，保留为未完全定位项。Canary则近3小时请求没有收到状态码，是另一个“发出后等待”分支，不可也写成首200被第二页阻断。','',
'### C. NRC是确定的解析失败循环，频率无关（P0）','',
'- 当前小时0/180，原上轮发布前就15/15失败；当前540次页面响应均200、0验证。稳定异常 nrc_event_licensee_or_facility_missing。持久endpoint状态failures已4016，仍反复重抓同一整日页面。',
'- energy_sources.field只认精确<b>Label:</b>及node.tail；parse_events遍历任意en数字块，一条缺Licensee/Facility就raise整批。合法非反应堆事件字段差异与真实DOM变化尚需保存HTML样本确认，不能把缺字段直接判成真实无效事件。',
'- 改为标签规范化（b/strong/td、空白/冒号、相邻单元格），依据稳定事件号切分；每事件独立解析。事件号/报告日/正文必须有效，缺主体则依据正式报文类型走受控兼容或隔离该事件，不编造licensee、不把坏页当空列表成功。坏事件不得吞掉同页其他可用事件；解析失败与网络重试分类分开。','',
'### D. 真实外部拒绝与分支健康不能混成全源不可用（P1）','',
'- Calcalist通过Google CSE；近3小时搜索JSON274次403，cse.js能返回200并不证明查询可用。现有token失效单次刷新可保留；真实拒绝不得无限刷新/换IP。需要许可稳定搜索入口，必要时该源单独降级，而非降低其他源。',
'- SpaceNews仍保持60秒，近3小时8次429；BE/MU IR RSS各约246/249次429，HTTP仍有427/309个有效响应。维持业务目标，正确遵循受限lane的Retry-After、让健康lane持续发现；不能为宣称“不回调”无视明确服务端限流。',
'- public_listing fallback主动保留primary failure是有意设计，不应把RSS消息成功伪装成HTTP爬虫成功。但SOURCE可用性、主HTTP健康、RSS能力应分别表达；已获有效传输/解析证明的RSS可继续交付并标主lane退化。优先复用已有rss_http_race，避免新增复杂状态机。','',
'### E. 浏览器与BODY问题要按用途分开（P1）','',
'- Investing当前小时1187成功/11队列失败，InvestorsHub1178/20，Barron\'s3459/41，Yahoo3578/19，SDx116/4；大量回执属于BUDGET_DEFERRED而非验证码。SiteAccess的3秒队列截止、BODY争用、单身份实际容量与公平性应单独查。',
'- 网站维度最近3小时另有Barron\'s14次subscription_required、Investing10次login_required；这是BODY/权限线索，不应据此把所有ticker listing失败解释为掉号。本轮没有打开浏览器，不能据DB代替人工确认登录态。保留认证与订阅错误，不自动重登/清Profile。','',
'## 可执行实施顺序（本轮不执行）','',
'### P0-1：阶段账与守恒回归，先解决公共故障','',
'1. 修改 publisher_http.py / poll_trace.py / corporate_ir/http.py / collection_plane/worker.py：已有周期中增加 admission_wait、host_wait、pool_wait、connect/TLS、headers、body、parse、commit_wait 的轻量耗时及最后阶段。使用现有ContextVar/trace，不完整抓包、不持续截图、不每100ms写SQLite。正常成功只存标量；失败附带owner/client-generation/attempt-id与有限shape。',
'2. 给 joint_permits 做确定性竞争/取消复现：两个publisher、共享role、相反到达顺序、等待者取消、source pacing、动态shrink；断言全部许可最终归还且饥饿有界。若证实预约循环，改成同一event loop内的FIFO eligibility仲裁；不得等待host时长期占全局容量，也不把共享_Source并发硬上限偷偷放大。',
'3. 对PoolTimeout或句柄枯竭实现按host/client generation的单次恢复：标记DRAINING、停止新租约、旧租约在有界期限内释放，再切新client；不关其他host，不清checkpoint。容量排队不触发client重建，DNS/真实403/429不算坏池；HTTPX及Curl均覆盖。',
'4. 将准入截止与已发出请求截止分开记账：前者返回可追踪的内部延期并保留下一正常轮询机会；不重试同一个未确认提交的网络请求。不要仅把45秒改成120秒。',
'5. 查线程/存储竞争：记录已有executor pending、ledger writer wait与maintenance批次耗时；本轮source容器未重启而渐进恶化，不能仅靠资源快照认定具体GC代码有错。修复GC/读取工作不能长期占默认executor或热路径writer，复用现有bounded lane，不新增第二套维护服务。','',
'### P0-2：逐源窄修复','',
'- NRC按C修解析与单事件隔离，使用生产失败页fixture。',
'- TechNews/Quantum按B拆首页/补充成功边界；定义supplement_pending及覆盖欠账，复用现有checkpoint持久化。',
'- NBIS IR先用P0-1的pool守恒/host恢复回归证明，再应用相同host策略；不能以重启一次成功交付。',
'- RSS fallback的验证证据必须带回Caller，主HTTP失败独立保留；只有RSS真实有效才允许其支撑SOURCE降级交付，不能将403 HTML或未验证200当成功。','',
'### P1：仍复现的单站问题与调度容量','',
'- 按下表逐入口验收；共享层修好后仍有的read-body、解析或拒绝才改adapter/route。',
'- 保持已核定source/identity硬预算及所有QUERY/TAIL业务目标；用公平错峰、真实request_multiplier（双lane/第二页）记账。GLOBAL一刀切扩容不能替代计量。现有试验Massive占用算入负载，并给正式源明确可用份额；不将其当成不消耗预算。',
'- 修正配置覆盖清单：API评估曾建议DIGITIMES English listing按HTTP20秒，但上轮SOURCE_INTERVALS没有列入它，目前仍60秒。这是执行名单漏项而非运行漂移；它当前健康，先修公共层，再独立确认是否纳入20秒，不能偷偷把频率缺口说成已全部兑现。CTEE60秒是明确保留例外。','',
'## 逐源／IR入口诊断与决策','',
'下表包含当前小时每一个有失败的正式源，以及试验源。全部健康入口、完整小时走势、最新失败shape另见CSV/JSON。共享层分类是明确的失败阶段/修复路线，不等于每个网站内部原因已经被唯一证明。','',
'| 范围 | 模式 | 最近1h成功/失败 | 分类 / 优先级 | 诊断与窄修复 | 频率 |','|---|---|---:|---|---|---|']
for row in rows:
 if row['last_hour_failed_or_deferred'] or row['mode']=='test':md.append(f'| {row["scope"]} | {row["mode"]} | {row["last_hour_success"]}/{row["last_hour_failed_or_deferred"]} | {row["classification"]} / {row["priority"]} | {row["cause"]}；{row["repair"]} | {row["frequency"]} |')
md+=['',
'## 验收与发布边界','',
'1. 先离线回归：许可守恒、公平性、取消、坏池代际、HTTP200主结果＋补充超时、RSS真实成功、NRC异构事件。不得用镜像重启或单次200替代回归。',
'2. 优先Collector窄发布；仅当浏览器准入模块实际改动才发布SiteAccess，Supervisor/Profile不重建。保留原始Challenge任务和当前出口；本轮不碰Reuters。数据迁移只修明确的坏client/无效retry状态，不清整库、全量checkpoint或消息去重键。',
'3. 发布后先30分钟低频自然业务观察，再至少跨越此前复发点：24小时或大于故障前请求次数的2倍。每5–15分钟读累计账；不能只看最近100条，也不额外制造网站请求。',
'4. 验收必须按ticker×QUERY/FEED列实际成功间隔P50/P95/最大值、准入延期、网络失败、valid listing与真实发现/正文证据；成功率分母排除未到期/在途，PARTIAL与完整覆盖分别列。只读“最新绿灯”不够。',
'5. 目标：NRC/坏池等连续故障消失；普通正式入口完整周期有效率目标≥99.5%，被外部明确拒绝单列；没有周期性许可损失、client/线程/target/WAL无界增长。10/15/20/30秒是目标，不用大幅延后成功后再填“已调度”美化。',
'6. 回调频率为有证据的最后手段：公共层修复和完整周期容量测量后，仍出现明确429/Retry-After或可证明单站容量低于需求，才提出该lane/入口的临时方案。没有证据不降低整批频率；不能保证外部拒绝在不改变请求行为时必定可修。','',
'## 证据缺口与资源风险','',
f'- 主机快照：CPU PSI some avg60约48.5%，I/O some avg60约21.4%，可用内存约{5832688/1024**2:.2f}GiB，Swap几乎用尽；磁盘可用约{v["resources"]["free_disk"]/1024**3:.2f}GiB。和发布时相比余量减少，但单次PSI不能当作18小时分位或证明某一role独占。acquisition文件仍约13.18GiB，不足以把约25GB总空间减少全归因消息账本。不得本轮顺手删数据。',
'- 生产未保存这些失败页面的完整HTML、每一个准入层的等待快照、坏池活动租约与取消堆栈。因此NRC字段兼容的具体HTML分支、Canary发出后停在哪一段、TechNews补充页与准入各占多大比例，都必须在实施阶段用受控fixture/有限失败样本进一步证明；不能声称已经穷尽。',
'- 这份方案确认了可修的内部覆盖缺口与多个失败阶段，保留尚待验证项；不是承诺所有外部403/429或订阅权限都能靠程序消除。','',
'证据目录：eval/source_stability_audit_20261111/，包含完整窗口聚合、最新失败形状、checkpoint摘要、最近3小时有限日志形状和逐源CSV；排除Reuters。']
path=Path('dev_plan/workflow_v2/message_sources_post_cadence_stability_root_cause_repair_plan_20261111.md');path.parent.mkdir(parents=True,exist_ok=True);path.write_text('\n'.join(md)+'\n',encoding='utf-8');print(json.dumps({'plan':str(path),'scopes':len(rows),'formal_classification':counts},ensure_ascii=False))
