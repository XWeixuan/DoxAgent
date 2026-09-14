# Message Bus / Persistent Runtime 消息时效与 Sweep P0 修复方案

日期：2026-09-14。按用户补充修订：没有精确时间的消息采用今天+昨天日期准入，两种流均适用，去重兜底；完全无日期以首次发现日期留痕兼容，不伪装精确发布时间。本方案已按该口径实施；生产验收与事故处置结果见同目录交付报告。

## 1. 目标与边界

修复普通流历史消息灌入、sweep 将30天补拉误作当轮输入，以及长 sweep 阻塞实时运行的问题。适用于所有 API、RSS、Crawler Plane 来源，覆盖同步入库、正文补全队列、缓冲发布和断点恢复。

只增加一套共享准入判断和必要的持久归属信息，不新增模型判断、独立审批服务、签名系统、任意消息数量上限或全局熔断。抓取窗口可以宽，业务准入窗口必须明确；去重和时效筛选各自负责一个问题。

保留既有交易策略、W1/W2/W3判断、O2/O3维护及人工激活优先规则。原始发布时间、消息进入总线时间、Case接收日期是不同事实，不通过修改显示日期掩盖准入错误。

## 2. 已确认的问题及证据边界

修复前代码审计确认：

1. `bus_orchestration.py::_source` 在无游标时使用 `cutoff - 30 days`；游标键包含 source/binding版本，配置更新也可能触发无游标回退。此逻辑由 `b2539116`（2026-09-13 23:12 +08）引入。
2. Finnhub接受该窗口逐日分页；scheduler虽有窗口过滤，但使用的就是这个30天窗口。普通polling另有默认三天回看，公共链路没有30分钟gate。
3. `RuntimeCoordinator.accept` 仅凭原发布时间早于未结束sweep cutoff认领消息，无业务下界和可信source task归属要求。
4. `_sweep` 冻结此前认领的Case，没有成员窗口复核；`run_once` 因任何未完成sweep抑制同ticker普通polling。
5. stream事件时间记录本次发布时间；Case交易日按首次admitted_at计算。这些字段本身不应改写成新闻历史日期，但不能作为新闻时效依据。
6. 正文补全回传 `collected_at=job.created_at`；如果直接拿该字段计算新鲜度，会错误豁免排队期间过期的消息。

已通过直连 SSH 独立复核运行库：本轮428条，427条来自Finnhub。按原始发布时间和本轮窗口逐条核对，418条应排除、10条应保留；关联86个Case、88条provisional，无交易，且当轮维护尚未开始。原始运行库已备份。周五修复的完整部署历史不在本次结论中作无证据推断。

## 3. 两种业务准入规则

### 3.1 REALTIME：精确时间30分钟；非精确时间今天＋昨天

设 `P` 为可靠的原始发布时间，`N` 为本次检查的当前UTC时间。

普通流允许 `0 <= N-P <= 1800秒`；恰好30分钟允许，超过拒绝。没有被去重命中也不能绕过。补抓只要仍满足该规则即可，不另加“必须是首次poll”等限制。

为容纳provider小幅时钟误差，允许P最多比N超前60秒，但保留原始时间、不修改为N；超过60秒作为异常时间隔离。这个容差不扩大历史30分钟窗口。

已有合法Runtime接收回执的Case恢复不重新计时；门槛针对首次进入工作流，不能让同日断点恢复重新执行准入并抹掉已有任务。

### 3.2 CLOSED_SWEEP：精确时间固定窗口；非精确时间今天＋昨天

有精确发布时间的日常sweep只允许 `window_start <= P < cutoff`，不使用30分钟规则。二者都是明确准入条件，sweep不是无限历史豁免。

- cutoff沿用已冻结的02:00 ET semantic boundary。
- 正常每日窗口为前一个semantic boundary到本轮cutoff；首个实际closed sweep以closed cycle起点为下界。使用日历/semantic_clock计算，不能简单减86400秒，DST日可能23/25小时。
- 最终开市日02:00 sweep覆盖前一个closed semantic day，不覆盖之前整月或整个已处理周末。
- source新增、source/binding版本变化、游标缺失、进程重启，都不得扩大业务窗口。
- ticker本轮启用较晚时，下界还应受既有控制层有效准入起点约束；不借首次sweep回填启用之前的业务。无法证明旧实例启用时间时，仍最多采用当轮窗口并记录信息缺口，不能退回30天。
- 边界cutoff上的消息属于下一业务区间；开市日02:00–02:01过渡消息保留，02:01按普通流30分钟规则处理。

**业务窗口由父sweep任务生成并持久化，source task只继承，不能自行从游标推导。**

## 4. 抓取窗口与业务窗口分开

`fetch_window` 描述API实际查询范围；`admission_window` 描述本轮允许的发布时间范围。provider只有日期粒度查询时，可以请求整天，再由公共准入层精确过滤。

普通Finnhub不必依赖把API窗口缩成30分钟才能安全运行；可保留较宽查询。作为流量优化，默认可收敛至覆盖最近30分钟的UTC日期范围，但这不是正确性保障。

sweep删除无游标回看30天的回退。分页cursor仅说明该source task已抓到哪一页；source历史coverage仅供诊断，不决定当前业务窗口。来源不支持历史补拉时，按每日一次抓取返回的实际内容和覆盖情况结算，不实现本期以外的完整历史补拉系统。

失败窗口记录独立gap，不自动并入下一日窗口。已存在的同一closed cycle内未结束任务可以按原冻结窗口续跑；不把过期closed cycle历史重新标记为当前cycle候选。既有调度补建的逐日任务仍各自保留原日期的一日窗口及closed cycle，不合并为当前轮的历史大窗口。

source逻辑身份、配置revision和分页checkpoint分离：普通参数/提示词/轮询频率更新不重置业务连续性；真正换数据源时记录新source身份，但仍遵守当轮窗口。

## 5. 最小持久化合同

复用现有job/raw/standard/member JSON，新增有类型的内部 `admission_context`：

| 字段 | 用途 |
|---|---|
| `policy_version` | 判定规则版本，便于排障和兼容 |
| `mode` | `REALTIME`或`CLOSED_SWEEP` |
| `sweep_id/source_task_id` | 仅sweep必须，稳定业务归属 |
| `window_start/cutoff` | 仅sweep必须，父任务冻结值 |

另外记录 `publication_time_basis`（`EXACT`、`DATE`、`UNKNOWN_FIRST_SEEN`）及精简准入结果。更新时间回退在metadata中标记`UPDATED_FALLBACK`，按日期处理；如同一身份已记录更早的原始发布时间，则沿用该历史时间，不允许更新时间覆盖它。现有可靠精确时间不要求来源为每条记录重复提供复杂证明。

上下文由scheduler/编排构造，通过显式参数传递到补全任务、Raw、Standard及流成员。API正文和爬虫metadata中的同名字段不能覆盖它；白名单解析即可，不引入密码学签名。后台重放读取持久上下文，不根据“现在周末还是工作日”重新解释原任务。

## 6. 共享判定与三个必要边界

新增无网络、无模型的纯函数 `evaluate_admission(message, context, now)`。实现只维护这一份规则。

### A. 入补全队列之前：省资源

`accept_poll_result` 在调用enrichment之前筛选。直接调用 `enqueue_enrichment` / `accept_message` 的入口也必须走该公共逻辑，不能只有scheduler调用有效。

不合格消息终结为正常筛选结果，不抓全文、不发W1/W2、不当作provider故障、不消耗重试预算。bootstrap可继续建立轻量去重基线，但不应为明显过期历史批量启动正文提取。

### B. 发布事务内：封闭排队、缓冲、重放漏洞

在公共发布位置逐成员重新判定，以实际当前时间为准；覆盖IMMEDIATE、BUFFERED、pending Raw修复和worker重启。补全任务原created_at不能作为now。

混合批次只发布合格成员；被拒成员从buffer转为已处理筛选状态，不能每轮重复扫描。全部拒绝则无stream item。已有成功发布记录的幂等重放直接返回原回执，不因为现在变旧而修改历史发布事实。

无需在每个adapter、每个正文提取重试、每个W节点再复制一套gate。worker取任务时可调用同一判定提前结束已过期工作，但不是新的业务规则。

### C. Runtime首次接收：挡住存量消息流

接收前按成员筛选，再编译模型输入。普通流用此刻年龄，sweep校验上下文与父任务冻结窗口一致。旧流没有上下文时不自动豁免：仅可从已有SOURCE_SWEEP receipt/job/raw关联恢复可信归属；无证据的旧成员按普通规则处理。

被拒成员有持久终态回执；一个stream item的所有成员已接收或拒绝后才能推进offset。筛选回执与inbox状态在Runtime库事务内写入，跨库cursor提交重试依靠既有幂等机制。不能逐条失败卡住整批，也不能只推进offset而没有原因记录。

sweep冻结时做一次轻量一致性断言：所有成员的owner和窗口匹配。发现不匹配只隔离相关成员，不将整批失败；这不是再次引入另一套时效规则。

## 7. 去重与所有权

复用既有消息身份和provider修订规则，不把poll_run_id/sweep_id加入内容身份来制造重复消息。

- 时效拒绝不是“已经消费”；普通流过期拒绝不能阻止相同消息以后进入其合法的sweep窗口。
- 在有效窗口已经完成业务接管的同一逻辑消息，不因polling/sweep同时发现而创建两个Case。
- 任意旧文章updated变化，不能刷新其REALTIME有效期；仍使用原created/published时间。
- 如未来要把“文章更新”视作独立业务事件，应另定合同，本期不隐式支持。
- BUFFERED只能合并相同准入mode且相同sweep owner的成员；先分组再按既有数量/长度规则打包。不能让latest成员的时间代表整个批次资格。

## 8. 各来源时间处理

| 来源 | 修复要求 |
|---|---|
| Finnhub | 使用datetime原始发布时间；宽查询与逐日分页结果都走公共规则 |
| Benzinga | created用于准入；updated只存诊断，不用来让旧文重获普通流或sweep资格 |
| Stocktwits / TikHub X | 使用created时间；缺失时按首次发现日期兼容，明确标记非精确时间 |
| RSS | 优先published/pubDate；只有updated时标识不可靠，不当精确原发布时间 |
| Yahoo / Google News / IBKR News | 保留现有查询过滤作为优化，最终受公共规则约束 |
| Reuters日期级搜索 | 不再把中午占位时间当精确发布时间；能从文章标准元数据确定时间则补齐，否则按日期/首次发现日期准入 |
| Crawler Plane | 校验并透传可靠发布时间；缺失或日期级产出按日期规则准入并标识精度 |

仅日期记录在普通流及sweep均允许今天、昨天（America/New_York自然日，不是滚动48小时），不再要求日期区间完全落在sweep窗口。sweep日期级回放仍使用任务冻结的cutoff所属日期作为“今天”，避免恢复时日期漂移。完全无可解析日期时按首次发现日期兼容准入，标记UNKNOWN_FIRST_SEEN；再次抓取不重置日期。不得据此跳过去重，也不得覆盖已知旧发布时间。

## 9. 非阻塞运行与边界竞争

恢复已冻结合同：**开市日02:01恢复实时polling，不等待final sweep/O2/O3完成。** 删除“存在未完成sweep便禁用普通polling”的ticker级屏障。

同binding的实际网络请求仍复用限流和短期互斥；sweep每页让出调度，实时任务优先取得可用执行槽。普通polling与sweep使用独立checkpoint，sweep不能覆盖普通poll_state的next_due/bootstrap进度。sources失败按原有限重试+gap结算。

sweep冻结完成后，迟到的有效同窗口消息只进入显式关联原sweep的supplemental批次，不允许靠时间上界吸入任意历史。不重新打开已结束closed cycle的候选选择。

维护继续使用冻结成员与输入；final sweep尚未发布新bundle时，实时任务使用最后可用Active Bundle。其失败按现有旧版本回退运行，不加新的“必须等维护成功”条件。

## 10. 日常可观测性

复用SQLite并新增一张精简、带索引的准入终态/审计表（也可扩展现有终态表），以消息身份+准入scope做幂等键。保存发布时间、检查时间、来源、stage、reason、owner、规则版本；不重复保存整篇正文。

PollExecutionResult及PollState记录received / filtered / published等累计数；准入表按stage/reason汇总expired / outside_recent_dates / outside_sweep_window等原因，Runtime独立记录skip与接收编组。未知精度是时间basis，不是必然拒绝原因。本期不另做前端统计页面。正常过滤不产生每条告警；批量比例异常按poll聚合记录，供定位provider或配置问题，不增加阻断机制。

抓取成功但全部旧消息仍是正常成功。sweep `coverage=UNKNOWN`不能展示成完整历史覆盖；覆盖状态与“本轮已结束”分开，失败gap可存在而工作流继续。

## 11. 本次事故与存量迁移

### 11.1 先生成定向处置清单

只读核对运行代码hash、`sweep:MU:2026-09-14`父/source任务、receipt窗口、版本化游标、428个成员原时间/总线时间/来源/状态，并追踪W1/W2、provisional、候选、O2/O3和交易副作用。将用户报告的427条拆成“实际窗口内”与“窗口外”，不能仅按Finnhub来源全部删除。

备份相关SQLite，保留原始任务和冻结manifest。先部署止新增机制，再处理积压；不能先清空去重库使历史重新涌入。

### 11.2 按执行阶段处理

| 阶段 | 动作 |
|---|---|
| enrichment/raw/buffer未发布 | 对窗口外消息写筛选终态并结束对应队列任务 |
| 已发布但未接收 | Runtime新接收gate记录跳过并推进cursor |
| CASE已排队但尚未开始 | 本次事故定向标记invalid admission，不发模型任务 |
| 模型调用正在执行 | 不强杀全局worker；在结果提交边界拦截该事故成员的新增业务副作用，回执留档 |
| 已形成provisional/candidate等 | 按来源关联修复派生投影，禁止直接删Raw或篡改历史结果 |
| 已进入维护/发布 | 未发布分支剔除污染成员重建；已发布版本需查清影响后定向重建/激活，不覆盖期间人工激活 |
| 已成交交易 | 不回滚broker事实，不重发entry；继续原有归属份额Exit管理 |

该“事故invalid admission”是迁移修复，不是对所有恢复中的合法Case重新计时。禁止仅修改已冻结`sweep_members`数组便继续使用旧W3/维护产物；须持久保存排除清单和reconciliation revision，使消费者使用一致的有效成员集合。

本次远端副作用已查明，处置工具先在原库只读生成清单，再在备份副本完整执行及验证幂等，最后应用原库。工具保留原始manifest和Case历史，写入`invalid_admissions`与`runtime_v2_admission_exclusions`，维护及provisional查询统一使用排除记录。只有涉及已发布业务事实或交易的重大歧义才升级用户决策；无需为正常旧队列筛除逐条请示。

## 12. 代码落点与实施顺序

1. **合同与纯函数**：Message Bus schema / 新admission模块；冻结REALTIME与SWEEP规则、时间可信度。
2. **总线入口与发布**：service、repository、content_enrichment；上下文持久化、筛选结果和buffer正确出队。
3. **sweep窗口与调度**：coordinator创建完整窗口，bus_orchestration删30天回退、分页与普通checkpoint分离，02:01实时恢复。
4. **Runtime接收和冻结**：成员级gate、可信owner、durable skip receipt、兼容历史无上下文消息。
5. **适配器时间归一**：消除created→updated伪新鲜和日期→中午伪精确；不重写无关抓取实现。
6. **迁移与事故处置工具**：新字段/表向后读取兼容；备份、dry-run、分批执行及进度回执。
7. **测试、灰度、交付**：先离线事故回放，再无模型的真实poll→筛选验收，确认后恢复受影响任务。追加changelog和运行报告。

本期不开发完整历史补拉、不新增消息内容分类模型、不改交易策略、不自动清空任何全局账本。

## 13. 必须通过的验收

| 场景 | 预期 |
|---|---|
| 普通消息29:59 / 30:00 / 30:00.001 | 前两项允许，后一项拒绝 |
| 第一次发现3天旧消息、同旧文新revision | 均不能因未去重或updated改变通过 |
| 入队时29分钟、发布/首次Runtime接收时31分钟 | 在相应边界拒绝；已合法接管Case恢复不重判 |
| 同一buffer新旧混合 | 逐成员筛选，无空批次，无旧成员重复卡buffer |
| 无sweep cursor、版本升级、重启 | 业务窗口不扩大到30天 |
| 30天API结果与1天sweep窗口 | 仅本轮窗口内可入流和冻结 |
| 两轮sweep / final sweep与realtime并行 | owner明确，边界不串轮，同消息不重复Case |
| 伪造payload sweep字段 | 无法获得豁免 |
| 补全跨日、Raw恢复、重复finalize | 上下文不变，未发布按规则处理，已发布幂等回执不变 |
| 日期级今天/昨天/前天；完全无日期重启；updated-only | 前两天允许、前天拒绝；首次发现日期持久不重置；已知旧原文不能靠updated刷新 |
| 任一source失败 / sweep仍运行至02:01 | 其余源继续，实时polling恢复，gap不阻断维护回退 |
| 已发布历史积压与cursor崩溃重启 | 拒绝有回执，cursor可续推，不新增旧Case |
| 本次428成员快照回放 | 对每条可解释准入/排除；窗口外消息不调用W1/W2，不生成候选/维护输入 |
| 已有合法Case/交易恢复 | 不因修复而重复执行、丢回执或停止Exit |

最终交付必须给出真实窗口、过滤数、正式流数、Runtime新建Case数及积压处置结果，不能只以“API请求成功”“去重命中率提高”或单元测试通过证明P0已消除。
