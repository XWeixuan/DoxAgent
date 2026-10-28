# Message Bus 全链路精益化优化方案

日期：2026-10-28，北京时间。状态：勘查与方案阶段，不修改生产代码、订阅、身份或部署。

依据：[现状与复发勘察](message-bus-current-state-and-recurrence-survey-20261028.md)、本对话10月23—28日诊断/方案/实测，以及本轮补充取证。目标基线为8核16G、最多20个ticker；容量按实际端点、查询、频道和新文章需求计算，不按固定9个ticker设置。

## 0. 全局设计先行：收缩正常路径

**保留独立采集，但不再把新增量送回旧的多层实时接纳/分发工作流。将实时链路收敛为：物理采集 → 文章/正文工作 → 逐ticker交付。** 同一篇公开文章只有一个正文工作；正文已经取得后，SQL、相关性、发布或投影失败只重做本地后链路。

保留SQLite、现有下载器/解析器、Chrome/Profile及对外消息契约；不引入消息中间件、新数据库服务或通用工作流平台。允许用少量薄表替代现有大量实时技术台账，而不是在旧台账上继续增加一层协调。

### 0.1 目标结构（本轮先冻结此方向，再用证据细化）

```mermaid
flowchart LR
    C[配置revision与控制epoch] --> A[独立Collector与publisher客户端]
    A --> Q[轻量采集库：cursor、证明、未ack增量]
    Q --> I[薄文章入口：article与ticker关联]
    I --> B[按文章获取BODY：一主路线与可选回退]
    B --> R[不可变正文与待提交结果]
    I -->|已有完整正文复用| R
    R --> D[逐ticker必要决策]
    D --> P[短事务原子发布]
    P --> S[Bus Stream与下游消费]
    P --> V[Read与UI异步投影]
```

1. **Collector：** publisher内复用连接、无关publisher分池；全局容量以角色预算合计保护。每物理unit保存最小checkpoint、失败恢复状态及一份物理证明；正常空/未变列表不生成业务工作。
2. **采集交接：** 变化文章批量进入统一实时文章入口；原子提交仅做按键upsert和必要工作标记。停止将实时输入转换为`poll_captures → intake slices → distribution_runs → deliveries → jobs`多轮账本接力。
3. **文章/正文：** 一套文章身份、实质内容hint及公开正文复用；必要私有权益才隔离。一个当前工作状态即可承接获取与本地提交，成功正文有可接回的不可变产物，不因后链路失败重新下载。
4. **逐ticker交付：** `(article,ticker)`唯一交付身份，L1/L2及现有ARTICLE_ONCE发布契约保留；source/roster/terms变化不自动生成新的文章身份或重抓已得公开正文。
5. **Site/Chrome：** 保留有价值的身份控制与同身份并发；按真正操作类型处理独占、取消与坏控制器。验证过的轻量列表优先；正常浏览器操作目标≤5秒，逻辑占槽与物理页/iframe/driver生命周期一并约束。
6. **存储：** 热状态为小标量；不可变输入/正文存一次、引用一次。Native展开、压缩、SHA与文件持久化不在业务写锁内；历史检索、审计和维护不能参与正常业务提交。
7. **调度与恢复：** 暂停使用CPU/IO/memory PSI硬门禁阻断既定任务，改为有界容量和公平服务；HTTP池、来源、身份、正文提交分别轻量恢复，不全栈重启。
8. **验收：** 全部应执行周期、逐必需unit、逐篇合格新文章和端到端时点。失败、延期、未完成、未发现渠道及资源增长分别保留，不再用短窗成功样本替代持续运行验证。

### 0.2 本文推进大纲

|部分|要回答的问题|完成状态|
|---|---|---|
|A 生产与调用成本|现状是否变化；超长事务的内层成本；实际数据增长与故障域|已取证并形成设计输入|
|B 最小数据与业务契约|保留哪些唯一事实；哪些旧机制删除/合并；新热表和迁移边界|已明确收缩与兼容边界|
|C HTTP与Collector|publisher池、真实取网证明、checkpoint、配置和交接|已细化|
|D 接纳、分发、正文与发布|锁外准备、极短提交、跨源复用、固定路线及本地收尾|已细化|
|E Site/Identity/Chrome与容量|单次操作成本、代理/直连、并发、取消和驻留回收|已细化，容量须按实测结算|
|F P0存储与生命周期|逐表读写/展开/CDC/保留；历史库与热路径脱钩|已细化|
|G 压力门禁、功能健康及恢复|关掉哪些硬门禁；哪些动作如何公平执行；怎样隔离恢复|已细化|
|H 执行次序与验收|依赖、文件、改动包、测试、发布/回退与长期成本门槛|已细化|
|I 统一review|跨层成本、重复状态、语义风险、验收遗漏和实施边界复查|已完成|

### 0.3 必须明确的两项技术边界

- “关闭压力硬门禁”意味着恢复TAIL/recheck等既定业务的服务机会，不等于无界同时执行1253个旧任务。容量上限与公平调度保留，不能再用PSI阈值无限延期。
- 5秒是普通瞬时故障的重试等待，不覆盖站点明确Retry-After、登录/权益缺失或挑战恢复。此类失败不能每5秒重复冲击；两条路线与重试共享尝试上限，不能形成多阶段乘法重试。

## A. 生产证据、成本归因与方案取舍

### A1. 固定证据窗口，不把不同口径混成一个成功率

完整近两天数据沿用[现状勘察第2—4节](message-bus-current-state-and-recurrence-survey-20261028.md)。本轮补充快照为 **2026-10-28 11:33:59—11:36:06 UTC，即北京时间19:33:59—19:36:06**。纽约当日仍是盘前，不能称为完整交易日验收。

|事实|已测结果|对设计的直接含义|
|---|---|---|
|此前严格新鲜周期覆盖|截至08:22 UTC最近60分钟：12,002/23,646=50.76%；156个必需unit无新鲜列表|不能只修正文，也不能把调用开始或RSS成功等同全部频道刷新|
|本轮必需unit头状态|360个：180成功、178失败、2个未有状态；182个在120秒内有capture|这是当前头状态，不是周期兑现率；与此前356个unit窗口分母不同，不能直接声称提升|
|公开HTTP组长期失败|Wccftech、SiliconANGLE等仍停在10/27 23:15—23:29 UTC，已约12小时，最新TimeoutError|共享客户端故障域及恢复缺失优先于给这些源增加浏览器|
|独立新HTTP客户端实测|相同Collector容器内8次公开GET；Wccftech美国线路200，0.772/0.236秒，直连403；SiliconANGLE直连200，1.011/0.473秒，美国线路200，1.363/0.522秒|上游并非普遍不可达；分池/坏池恢复有直接价值。不能据此把Wccftech改直连；也不能把新客户端成功当作原池失效的内存取证|
|HTTP库失效机制|已用生产curl_cffi 0.15隔离进程、无网络复现：cleanup抛错两次后2槽池为空，后续pop超时；真实Yahoo日志有remove_handle错误|库异常可造成容量遗失；但Yahoo错误不是Technology池原始故障的已证因果，需要分域验证|
|失败checkpoint|真实Collector隔离夹具：有效失败结果带`failures=1`，实际持久化checkpoint为空，只推进单次retry_not_before|失败状态推进与有效列表证明必须分开，不能以“无有效列表”为由丢失失败恢复状态|
|联合许可异常泄漏：本轮新复现|真实JointBudget内注入身份状态读取SQLite异常：实际操作0、waiters0，global/site/identity active仍各为1|取得许可的计数更新不是异常安全；同步DB读取在全局condition内，既可能阻塞全部身份，也可能永久遗失容量。不是生产已确认泄漏量|
|超长Bus事务|`accept_and_claim`一次事务内1338.208秒，BEGIN等待约0.013毫秒、commit约13毫秒；另有1248.623秒事务|不是仅在BEGIN处等锁；大量工作在已取得写权后执行，把单任务放大成全库阻塞|
|续租也过重|`renew_enrichment_claim`一次事务内80.348秒；源码SELECT完整data_json→Native展开→整模型校验，再只改lease列|续租只需标量CAS；正文、source、binding重建没有一致性收益。该续租不会因仅lease变化触发当前job状态CDC，不能误归因于CDC|
|近期新消息路径|此前最后60分钟已发布样本列表→Bus均值0.294秒、Bus→candidate均值0.303秒；同期20个新正文任务12个执行生命周期≥40秒，11个约76—136秒|前链路曾明显改善，不能假定全部延迟都在接纳；但历史超长事务仍未结构性消除。任务生命周期含重试等待和提交，不能称为纯下载时间|
|正文与发布不同|此前纽约10/28部分日合格body key 60个，FULL/SHORT_FULL 27个=45%；最后60分钟20个中7个=35%；14个可消费样本仅5个当时为FULL|“完成”“发布”“FULL”必须分指标；摘要发布不能充当正文成功|
|当前积压类型|本轮1275个queued，全部标为recheck，最老10/26；Collector待交接capture为0|不能说1275篇新文章被堵。PSI硬门禁确实使旧文更新长期饥饿；当前桥空不代表历史锁问题已修复|
|当前主机|8核、约15.24GiB；CPU PSI avg60=22.53%，IO=1.90%，memory=0.04%；MemAvailable约2.53GiB；swap近2GiB已用尽|PSI不是CPU使用率。当前IO回落仍被CPU阈值永久阻断；要减少实际CPU/驻留工作，不能单纯增加并发|
|数据库增长|Bus 27,260,915,712→27,283,914,752字节；acquisition 491,220,992→593,391,616字节，约3小时13分钟|本段acquisition约31.7MB/小时，不能据短窗线性外推长期必然值；无生命周期的inputs/first_seen和重复证明仍是持续成本。以上不含Native目录/Read副本|
|部署一致性|Collector/BODY/Site为30a2a184，Bus/API/Projector为24a5fde5，Supervisor为f2f0bcd6；本轮快照未变|实施必须同步契约依赖组件，不能把本地main已改等同生产已用新路径|

原始证据：`eval/message-bus-lean-plan-20261028/{host.json,snapshot.json,publisher-routes.json}`；既有逐周期、逐文章、慢事务、故障注入证据在`eval/message-bus-current-state-20261028/`。本轮没有重启客户端/Chrome、修改生产配置或清理生产数据。

### A2. 根因不是一个“资源紧张”，而是五条可复发放大链

1. **一个HTTP对象承载多个无关publisher → 库cleanup/槽位异常无法自愈 → 多来源长时间不再取网 → 无效轮次持续调度、记录失败。** 增并发不修坏池，增加浏览器会扩大成本。
2. **一次正常输入跨多套身份/快照/任务账本 → writer内展开Native、校验大模型、循环ticker → 单事务长占锁 → BODY续租/结果提交/新消息一起排队。** 分离Collector只切断部分等待，Bridge仍回到原接纳体系，所以故障可以复发。
3. **旧文章、原始字段与配置版本变化被当成新业务工作 → 重复正文和状态写入 → CDC/Read/Native复制与长期增长 → 原本快速的查询和维护逐渐变重。** 清积压、清历史或临时扩容只重置恶化时钟。
4. **逻辑槽限制不覆盖物理Chrome页/iframe/driver驻留；取消等待还可能继续占用 → CPU/内存持续消耗 → PSI硬门禁无限推迟TAIL/recheck → 覆盖/更新欠账。** 为降低PSI而阻断业务不能替代生命周期治理。
5. **许可计数先变更，随后在condition内做可失败的DB读取，最后才设置acquired → 中途异常不回滚 → 实际无任务仍有占槽。** 这类容量损失随异常累积，调大容量或重启只是暂时复位；必须使许可操作异常安全，并移除锁内外部工作。

已证实的是调用链、状态丢失、异常机制和上述耗时。1338秒事务中“每个SQL/Native文件各占多少秒”、Technology原池实时handle数、各浏览器操作纯CPU占比尚无完整生产span，**不伪造精确占比**。实施前置测量只补这几个缺口：现有慢事务内SQL执行/Native读取/压缩/校验/commit的独立累计计时；每种浏览器操作按导航、必需DOM、提取、关闭拆分。计时汇总到物理操作，不再增加逐字段/逐ticker台账。

## B. 最小业务事实与热路径收缩

### B1. 三种事实，三类薄表；其余不再成为实时前置条件

在**现有Bus SQLite**内承载新薄表，保留原始消息/标准消息/stream对外契约；不新增跨库发布事务。

|事实/建议表|最小字段及唯一键|只在何时写|明确删除的重复内容|
|---|---|---|---|
|`live_articles`：文章及一个当前正文工作|article_key PK；canonical_url/publisher、必要access_scope/language、material_hint、first_seen、input_ref、body_ref/body_digest/outcome、work_state、due_at、attempt、claim_token/lease_until、result_ref、revision|新文章、实质更新、状态迁移、必要续租|source/binding完整对象、完整roster、每ticker重复正文、每阶段任务副本；READY结果无需单独再做另一套job状态|
|`live_deliveries`：文章×ticker业务处理|UNIQUE(article_key,ticker)；terms_revision、decision、publication_id、decision_input_revision、due_at、必要claim标量|新增适用ticker、正文/terms真的要求重判、首次发布或原消息更新|重复binding/admission/source快照、每次poll的delivery、与公开正文权益无关的source隔离|
|`live_origins`：采集来源及身份别名|UNIQUE(origin_key)映射article_key，source/unit、原provider ID、first_seen、必要发布时间依据、适用订阅来源；canonical别名用同一键空间|新来源关联、新wrapper解出publisher、有效时间证据修正|每轮last_seen、每次raw_hash历史、重复文章正文/列表副本；频道轮询新鲜度留在采集层|

`work_state`只需`FETCH_DUE/FETCHING/COMMIT_DUE/READY/FAILED/NOT_APPLICABLE`；重试由attempt/due_at表达，不另加retry任务。正文抓取状态与L1/L2/发布状态分离；decision沿用现有必要业务值，不重造通用状态机。新表默认标量row factory，JSON小扩展字段有大小上限；大内容只放不可变ref。

### B2. 两个核心判定，避免版本乘法

1. **文章身份：** 已验证publisher canonical URL或provider声明的稳定article ID；wrapper未解出前使用provider局部临时键，解出后原子建立别名。只删除明确tracking参数，不能泛化剥掉有业务含义的查询参数。公开正文不按发现source隔离；真实权益、语言版本才保留边界。跨域转载不是同一篇canonical文章，不能仅凭标题合并。
2. **实质内容hint：** 规范化标题、有效摘要/列表正文、provider明确修订标记；正文下载后以正文digest判定实际修订。抓取时间、广告/排序/raw payload、source/binding版本、roster顺序、无意义发布时间漂移不触发新正文。hint是触发检查的线索，不是永久禁止重新检查的证明。

唯一发布约束复用`(article,ticker)`，不再叠加source×raw_hash×poll×roster版本发布身份。**ARTICLE_ONCE保持：**重要更新写原消息及revision、显示更新状态，不重新触发已经消费它的下游。新订阅ticker仍可以首次交付；来源被禁用时，只禁止该来源发起新采集，其它有效来源已经证实的公开文章可继续正常处理。

旧hint有完整正文时，新增发现来源直接复用，不要求两个发现来源的摘要完全一致。hint变化先安排单个更新检查，已有FULL在检查期间继续可读；正文digest未变只更新检查时间，不生成修订和新发布。不变hint仍按**有界活跃更新窗口**重新检查，避免遗漏无显式updated_at的实质更新；具体窗口见D3。

### B3. 实时入口与历史扫描的分工

- **实时**：Collector变更文章→锁外规范化/轻量L1→短事务upsert上述三类事实→文章正文或复用→逐ticker相关性→一次发布。Bridge只搬增量，不创建distribution run，不接旧poll intake分片。
- **CLOSED_SWEEP**：`persistent_runtime_v2/bus_orchestration.py`当前冻结window/query_plan/page_cursor，并等待job_ids。本语义保留；将等待改为当前页的article/delivery键集合，记录有限页进度/覆盖证明，不能等待全局TAIL/recheck清空，也不能为扫页复建实时roster账本。短期适配器允许读旧job_ids，但**不双写两套实时流程**。
- **TAIL**：维持每必需查询/频道的页边界与coverage，按公平容量继续扫；不得取消覆盖以提高HEAD指标。新文章发现后立刻进入同一实时文章入口，旧文只按键判定，不再逐ticker完整重走接纳。
- **停止/临时休市开关**：请求开始前复核小型控制epoch及有效期；关闭/到期不启动新临时请求，允许在途与已持久化工作收尾。不改变共享交易日历、checkpoint及下一交易日接续语义。

### B4. 明确退役，避免“精益方案”再次叠层

|现有机制|处理决定|保留的业务保证|
|---|---|---|
|实时`poll_captures/priority/inputs/leaders/verdicts`与intake分片接力|停止写；由Collector增量+薄文章入口取代|持久化后才推进有效业务cursor；重放按文章/交付唯一键无害|
|实时`distribution_runs/observation_sets/roster snapshot/deliveries`|停止创建；旧数据仅兼容查询/有限清理|覆盖证明在采集层；逐ticker唯一交付在薄表|
|`content_enrichment_jobs`与`article_body_results`双状态|新实时工作合并进article当前正文状态；旧在途逐个接续|所有权token、成功产物、失败原因和重试进度仍可恢复|
|每次claim/renew重建全模型与Native|取消；标量CAS|旧owner不能提交，进程退出后可恢复|
|重复来源版本、raw_hash、roster复核与兼容fingerprint分支|从新热路径移除；旧数据边界做一次适配|真实article身份、实质更新、当前订阅/terms的必要判定|
|发布前重新抓正文、重复持久化整个结果|取消|成功产物先持久化；本地commit/fanout可独立续作|

迁移以source为原子路由，所有ticker/频道一起切，不能同一个source一半新、一半旧。先加载现有已发布身份/正文**按需查旧、一次写新映射**，不全库展开搬迁27GB，不重跑历史L2。切换后旧表只能处理冻结的旧在途清单和既有sweep，清完即关旧worker；不保留永久双跑/双写。

## C. HTTP、Collector、checkpoint与交接

### C1. 分池与恢复：收小故障域，不新增一层分布式许可服务

改动：`message_bus_v2/technology_sources.py`、`energy_sources.py`、`news_adapters.py`、`http_routes.py`、`site_strategy/runtime.py:_http`、`content_enrichment/{managed,transport}.py`及Yahoo自己的HTTP transport。

1. 将当前一个`TechnologyHttp.session`拆为**publisher+明确出口/传输身份**的小池，多个栏目共用本publisher池；Energy/TheFly不再借用无关publisher的session。不要按ticker建池；真正不同的cookie/权益会话才单独分池。泛用httpx/Reader客户端同样禁止跨无关publisher共享一个可整体失效的transport对象。
2. 复用现有库，不自造连接池。优先用现有httpx处理已证实可行的公开端点；需要curl impersonation的端点继续用curl。用本轮相同feed夹具和真实路线测试决定，不统一强换库/出口。
3. publisher在途数与角色总在途数各保留**一个**限制。删除同一路径重复的host semaphore/外层固定gap；publisher真正需要的启动间距只实施一次。获取网络许可后仅持有到响应字节读完/取消完成，解析、checkpoint、SQL及Bridge不得继续占HTTP许可。
4. curl请求将池等待与真实socket执行分开记时；总deadline包含排队，不能先等无界semaphore再进入15秒timeout。对已知cleanup/invalid handle错误，或“publisher没有真实活动socket、pool取得阶段持续超时”的已证失活，**仅废弃该publisher transport代际**。新请求用一个新client；旧请求在自身短deadline内退出后关闭旧client。普通403/429/内容无效不触发池轮换，也不借轮换重置站点退避。
5. 针对curl 0.15的`set_curl_options`在finally之外、cleanup异常不归还容量，先做精确回归；通过升级或很小的transport适配修复。禁止吞掉cleanup异常继续把坏handle放回池。恢复后第一份有效列表即解除该池故障，无需重启Collector或改Chrome身份。
6. urllib/socket线程不能无限shield排空。只读HTTP改为可取消异步client优先；必须保留stdlib时，socket必须有真实读/连接deadline，独立小executor，取消后该实际操作未结束仍占其真实容量，但**不得占源/identity/Bus许可**。不要把假释放变成后台无界线程。
7. Site `_http`目前在`response.content`全部读完后才检查大小，改为接收时有界。feed/普通文章/SEC文件分别沿用合理容量，不统一4MiB误截完整财报；超过来源契约限额明确失败，不能截断后标FULL。成功BODY/响应只在必要地方保留一次字节，不在trace再复制一份。

**起始容量不是最终答案：** Collector全局HTTP12、BODY全局HTTP4（含Reader），各publisher常态2；角色合计16个真实HTTP操作。多进程各自预算不能再各写“全局16”。按E3实测调整；固定间距默认0，真实429/源政策例外。池对象懒创建，空闲关闭；不是80个publisher各预热2连接。

### C2. checkpoint只承担恢复，不承载整份业务对象

改动：`collection_plane/worker.py`、`store.py`、各adapter checkpoint契约。

- 每**物理unit**保留cursor/页边界、ETag/Last-Modified和有效304基线、连续失败数、next_eligible、必要recovery token、bootstrap边界。source/ticker/roster完整对象、完整消息列表、完整历史trace移除。
- **失败总要推进失败恢复状态**；`valid=False`不能跳过`result.next_checkpoint`。同时不得推进未持久化有效页面的成功cursor或last_fresh。用显式`recovery_delta`与`committed_page_cursor`区分，避免将adapter任意完整checkpoint无条件覆盖成功页位置。
- 不能只修失败计数落盘、继而让所有TimeoutError可靠地退避到900秒。**按原因收敛next_eligible：**程序池/控制器/本地存储错误走对应局部恢复，失败次数记录但不当作publisher限流；普通瞬时取网故障可在5秒后补一次且不追赶过期轮次，后续按正常相位继续。明确429/Retry-After、真实挑战/登录恢复才执行相应外部等待。网站真限制保留，程序自己的故障不能制造15分钟静默停采；失败周期仍计未兑现。
- 页回调写完增量后，以明确`page_committed`布尔/计数判断部分成功；不再用已经`pages.clear()`的暂存list判断。多端点部分成功分别提交，不回滚健康端点；失败端点继续独立退避。
- **状态写一份，证明写一份**：一次页提交可原子完成该页delta+checkpoint；结束时仅更新真正变化的恢复状态，不再save完整unit两遍。共享unit消费者引用本物理状态，不复制9/20份请求历史。
- 调度保持fixed phase，每unit最多一个真实在途。延期记miss/late，不补发所有过期tick形成追赶风暴；业务TAIL从cursor补覆盖，不靠重复HEAD弥补历史。慢操作跨周期时，新周期仍记未兑现，不能用一个旧请求算多个新鲜周期。
- 把取网、解析/增量提交、收尾分开，不用45秒包住SQL后把已取得列表报成网络失败。源许可只覆盖真实取网/必要浏览器提取；本地写失败进入轻量重交接，不再次下载相同列表。

### C3. 配置与输入去重

当前`article_input_keys`包含source/binding version、完整roster、raw payload、metadata、各target admission结果。即使排除少数字段，仍是宽对象hash，配置变更可重放旧文章。新路径删除该函数依赖，使用B2身份与material_hint。

配置刷新改为：每秒读取**一个配置revision/控制epoch**；只有revision变化才加载并编译sources/bindings/terms/查询计划。配置修改事务递增现有或新增一个小revision值；不每秒展开所有Source、Terms、IR状态并重建计划。交易日边界、override截止时间由内存时钟判定，不需每秒展开Runtime全对象。首次加载失败不能启动无配置工作；已有快照失败保留读能力，但停用/到期控制以最新小标量为准，不能无限期依靠旧enabled快照发新请求。

输入正常路径：有效304或精确未变响应直接更新采集证据；响应有变化才解析。解析后只比较每文章小key/hint，不遍历历史全文/roster；顺序/追踪参数/raw广告变化不建业务工作。RSS响应无ETag仍需每周期真实请求，不能以本地内容未变跳过取网。新ticker挂入共享源时，对当前最近有效列表做一次有界关联，不下载/重放全历史。

### C4. CaptureBridge吞吐

- `collection_plane/bridge.py`直接调用薄文章入口，**删除**实时get_or_create_run、attach_target、polling.prepare/commit等旧接力；delta只带必要文章及目标引用。
- 每批最多32个变化文章或锁外准备约10ms工作量，先发现的新文章优先，轮转source；不能一个大列表整批占writer。
- 锁外规范化、L1、内容准备；事务内批量按PK upsert、必要delivery插入、acceptance/cursor确认。目标单批事务P95≤20ms、P99≤100ms；单文章交接+必要分发本地执行P95<1秒。
- **先保留一个Bus提交队列**，不用并行SQLite writers制造锁竞争。准备可2个并行、source轮转；短事务后若桥排队P95仍>200ms，再开2个锁外prepare消费者，共用同一提交入口。无需先做通用分片平台。
- Bus已提交、acquisition未ack崩溃：重放唯一键无害，然后ack。Bus未提交：持久delta仍在。最小acceptance保留到对应delta已ack并过重试窗口；不永久保存每个poll的接纳receipt。

### C5. 有效采集证明要变准确，也要更小

- 区分`scheduled/dispatch/client_call/transport_send/headers/body_complete/list_validated`。curl现在在`session.request`前记录，不能当真实取网；用transport请求headers发出回调/可用底层trace记录时间，httpx沿用已有发送阶段trace，Chrome用匹配实际列表请求的网络事件，SDK用实际可获得的发送证据。得不到transport证据就写UNKNOWN，不能用API调用时点冒充。
- adapter明确返回哪个`physical_request_id`产生了经过验证的本次列表，不能简单选`listing[-1]`，混入辅助HTML/错误跳转/另频道响应。共享消费者引用同一次请求及同一验证结论；不同查询/频道不能借另一请求证明。
- 原始记录保留必要响应状态、Date/Age/ETag、基线ID、查询/频道标识和小型有效性结论；不复制全部headers、完整响应、完整消费者对象及全部Site trace。源站缓存Age/更新时间另报，收到新响应不等于已知上游发布零延迟。
- 请求启动证明不成为同步SQL前置；可批量落薄证据，正在执行状态供诊断内存读取。崩溃前未落完证据的周期标UNKNOWN/ABANDONED，仍进入expected分母，不伪造成功。需要恢复业务delta/页cursor的提交仍持久化，不因“遥测可丢”丢业务。
- **业务接纳不等待审计字段齐全。** 缺取网证据只使freshness无法认证，不应丢掉已实际解析、身份正确的新文章；修统计不等于提速，也不再为了得到完美证据重复执行一次业务抓取。

## D. 接纳、BODY、交付：取一次正文，独立完成后链路

### D1. 正常路径与写事务边界

改动重点：`polling.py`、`distribution_repository.py`、`service.py`、`repository.py`、`body_results.py`、`content_enrichment/service.py`；新薄仓储可集中一个文件，避免复制一整套service。

|阶段|锁外做什么|唯一必要的短写事务|失败后从哪里继续|
|---|---|---|---|
|列表→文章|解析、身份/hint、时间依据、L1、不可变输入准备|按键新建/更新文章，新增适用ticker关联，标正文due，确认delta|同delta重交接，不重新取列表|
|正文领取|仅查询小型due索引，读小标量|`UPDATE … WHERE state/due/token` CAS领取；不载全文|lease过期后重领；已得result_ref先走提交|
|正文获取|按固定路线网络、解析、完整性验证，产物一次持久化|登记`result_ref/body_ref/outcome/digest`，转READY或COMMIT_DUE|SQL失败只重提已持久化结果|
|相关性/L2|按文章正文ref读取；每article×ticker一次必要判定|按decision_input_revision保存决策|只重做未有决策的ticker，不重新抓正文|
|发布|构造Raw/Standard/Stream引用、必要更新差异，均锁外完成|原始/标准/stream唯一插入及delivery标完成同事务；ARTICLE_ONCE冲突返回已有ID|读既有publication_id确认，禁止重发|
|Read/审计|实际业务事件异步投影；消费者领取使用当前Bus stream|投影自己的短事务|追业务事件，不回滚正文/Bus发布|

写事务中禁止网络、Pydantic整对象构造、Native展开/压缩/文件fsync、完整roster循环、模型调用、历史查询及日志全文拼装。仍需在事务内核实的只有token、当前input_revision、当前有效订阅/terms标量和唯一键。输入内容不变时无须重新比较完整raw对象。

提前做L1只用于既有语义下可确定的判定/快通道；列表标题不匹配而正文尚未知的文章不能据此新增早拒绝，否则会牺牲正文中才出现关键词的覆盖。terms变化只使必要决策失效，不使已得正文失效。CLOSED_SWEEP也按既有完成定义等待当前页工作，不追加“等所有下游任务完成”的新门禁。

现有`record_raw → PROCESSING → save_raw → standard → stream → completed → audit`重复状态写，收敛为**一次原子业务发布**；独立的“已处理”状态由publication_id/decision推导。UI要显示过程时读薄工作状态，不为展示再复制业务对象。L2确有外部调用，必须在锁外；失败只恢复L2续作。保留现有IMMEDIATE/BUFFERED配置，缓冲等待单列，不能偷偷改成即时发布后宣称达标。

删除每次发布后为证明可消费而再次完整read-back stream/正文、再独立写一条CONSUMABLE审计的必经环节。使用已经提交的stream唯一行及可读取契约记录T1；真实consumer claim另记实际发生时间，二者不混称“已被消费”。端到端抽样从真实消费API核验即可；Read的T2仍按投影实际应用时间，不能用提交时间假造。

### D2. 正文路线：明确选择，不再默认三段级联

新增/收敛现有site BODY参数为`primary`、可选`fallback`、明确eligible failure集合；每站点一个简短策略，复用现有解析器。路由依据**最终publisher**，不是仅凭Google/Yahoo发现源。每个site先从近两天attempt证据计算`真实FULL数/实际调用数`、耗时和CPU，不能用HTTP200/Site SUCCESS选路线。

|来源/类型|实施起点|禁止/需要验证|
|---|---|---|
|官方IR、有效完整RSS、SEC已组装文件|RSS正文已完整则零额外抓取；否则官方HTTP/JSON一次|SEC正文/表格/exhibits契约及既定排除范围不变；片段不能标FULL|
|GlobeNewswire、ETNews、TrendForce及公开publisher|优先已经有效的HTTP正文；仅有实测收益才保留Reader或Browser之一|本轮Globe BODY两个HTTP Site SUCCESS耗时291/618ms，但仍需正文质量核验；ETNews真实短稿以结构/结尾判SHORT_FULL，不只按字数|
|Apple RSS|正确XML命名空间、CDATA/escaped HTML、原链接解析；完整RSS直用|不得把条目摘要当全文；失败parser不升级为全站浏览器|
|Barron’s/Reuters/DIGITIMES等真实身份/权益站|使用已有已验证BODY能力的Chrome身份，能browser_fetch就不重导航|登录/权益未满足不能用Reader绕过或反复公开HTTP；相同Chrome身份可并发普通业务页，维护仍独占|
|Investing/InvestorsHub|按实际站点/国家域固定有效路线；先恢复控制器和列表能力，再验证BODY轻量fetch|不得将国家域重定向一律视为原文，不得以浏览器200挑战页判FULL|
|Google News/Yahoo等聚合源|wrapper只解析一次、缓存alias；跳转后按publisher策略；已有publisher FULL跨发现源复用|解析失败保留未解析身份与失败分母；不能按标题随意合并，也不能每ticker重新解析wrapper|
|TheFly、AviationWeek及其它预览/付费稿|保留合法可用正文能力；无权益时明确失败/预览|不能靠放宽FULL门槛达到80%|

**尝试预算：** 正文publisher最多2次实际获取：primary成功结束；明确可换方法的失败才fallback；没有fallback时可同方法5秒后重试一次。二者二选一，不是primary+fallback后再各重试。wrapper解析单独最多一次正常解析，瞬时失败最多补一次，记入总成本，不能用多跳隐藏尝试。普通HTTP目标≤3秒、起始硬deadline5秒；Reader目标≤5秒、起始硬deadline8秒；正常warm浏览器目标≤5秒、起始操作deadline8秒。冷连接/真实挑战恢复独立分类，不把全局预算统一缩成5秒导致覆盖骤降。站点实测必要的更长限时单独登记原因并纳入长尾，不全局延长。

publisher路线预算通常20秒内收束（含普通重试等待），不是每个阶段各自重新获得180秒；站点例外按实测明确配置。现有`_stage_budget`已经把body上下文的deadline传入RPC，这个有效机制保留；修复的是Site入口外的工作/取消排空和RPC额外缓冲，不误称整条链完全未传预算。`managed.fetch`的递归redirect改为共享绝对deadline、visited集合和有界redirect循环，不以重定向重置尝试预算。

实施路线清单复用Site配置，每项写明primary、可选fallback及适用失败、质量夹具、实际调用数/FULL数/耗时；缺可比证据时沿用已证明的有效方式并先做小规模对照，不能预先宣称全站最优。`managed.py`当前丢弃`publisher_url`，Reader分支须保留原publisher上下文，选择其公开/权益策略与分池键；访问目标仍是批准Reader域，不能把所有publisher变成generic策略或借Reader跨越权益边界。

失败分类：pool/transport瞬时故障→可重试；有效HTML但正文抽取不完整→有证据的另一种方法；429→明确Retry-After；登录/权益/挑战→等待该身份能力恢复，**不每5秒重试**；非文章/错误publisher/不支持媒体→终态。Site准入失败不消耗一次“实际获取”，但必须保留延期年龄和队列预算；不给无限隐形重排机会。

### D3. 复用、重要更新与旧文recheck

- 对公开publisher，仅要求canonical文章一致、正文确实FULL/SHORT_FULL、语言/版本兼容；取消发现source scope、发现摘要hint完全相同、无意义TTL等复用门禁。付费/授权内容按真实access_scope隔离，不按无关source ID过度隔离。
- 内容digest不变：不重写raw/body/standard/stream，不生成revision，不重复L2。只有source provenance新关联时插一条小origin。
- 实质hint变化：同文章至多一个更新检查；有FULL时继续展示旧完整正文并标“更新检查中”，不降为失败摘要。新digest产生revision；已经消费的ticker不重触发，尚未交付的ticker使用当前版本。
- 无明显更新hint仍需发现重要更新。先将现有检查表示为article的`next_check_at`，**停止提前物化上千个独立旧job**，按原due恢复公平服务。当前代码对成功正文每1800秒再次到期，失败也最终每1800秒到期，未设历史终点；这不是ARTICLE_ONCE的必要条件，持续运行会把全部历史变成永久定时下载。具体业务收缩建议及覆盖边界见G3，不能悄悄改完再称“覆盖完全不变”。
- 新文章与recheck共用下载器但有公平份额：至少一个后台服务机会、每次只领可实际启动的工作；新文章不排在上千个旧job后面，后台也不能被持续新流永久饿死。具体容量见E3，不使用PSI长期禁止。

### D4. 成功下载与本地提交分离

正文验证后先把一个不可变结果产物写好，包含article key、当前revision、fetch generation、取得时间、body ref/digest、质量结论和必要来源证据；用**article/revision/generation结果位置+原子rename**形成待提交文件，现有ContentFiles可复用，不再持久化第二份完整HTML/正文台账。其目录本身是短暂提交spool，文件名/小头即可恢复，无需新增通用outbox/job表。不同owner的文件不能互相覆盖；接回时按当前revision/代际核对，不以“目录里最后一个文件”覆盖较新结果。

Bus SQL失败：保留该小产物，5秒内重新尝试**本地commit**，不重新进入HTTP/Browser/Reader；owner重启时先接回当前文章已有结果。成功DB确认后删spool标记，body不可变内容由业务ref继续保有。关闭文件→DB提交→ack各点做崩溃测试。过时revision结果可以保留修订证据/清理，不覆盖新revision；token失效只影响提交权，不要求丢掉可验证成功正文。

`RESUME_STAGE`字符串不足以续作；正常固定路线最多两次后无需通用stage engine。确有wrapper解析成功、正文已完成等昂贵中间事实，保留最终URL或结果ref即可。**不要为了“精确恢复所有阶段”再加完整多阶段快照。**

## E. Site / Identity / Chrome：先减单次成本，再兑现容量

### E1. 新取证：空闲全局槽不等于身份具有可用能力

11:10:33—11:40:33 UTC，索引限定半小时取得1604条ACCESS_RESULT（未触发5000条上限）：

|Site/用途|实际结果|耗时解释|
|---|---|---|
|InvestorsHub CRAWLER|264条：220 BUDGET_DEFERRED、44 SERVICE_UNAVAILABLE，0 SUCCESS|其中191个site_queue_timeout；这是attempt事件，不是264个独立周期。含零执行的network_ms均值不能当浏览器成本|
|Investing CRAWLER|1条SERVICE_UNAVAILABLE，identity_worker_disconnected|该次network_ms=37.985秒；不能把其它没有事件的应执行周期当作没需求|
|Yahoo CRAWLER|490条：434 SUCCESS、56 BUDGET_DEFERRED|成功network_ms均值1.525秒，P95约6.873秒；只有部分达到≤5秒。成功样本未证明每个必需频道周期兑现|
|GlobeNewswire CRAWLER|752条：666 SUCCESS、86 BUDGET_DEFERRED|成功network_ms均值0.374秒、P95约0.735秒；不应为这类请求配置重浏览器级全链路等待|
|DIGITIMES CRAWLER|31条：30 SUCCESS、1延期|成功network_ms均值10.138秒、P95约22.544秒；存在真实单次成本偏高|
|generic BODY|32条：6 SUCCESS、24延期、1恢复中、1耗尽|不能将generic聚合为“正文成功率”；需要拆最终publisher及路线。有Reader成功本身耗时7.269秒|

11:41 UTC诊断：global active4/8、BODY0/2；Investing身份public-markets-nl占2槽，第三个请求等待；IHub操作年龄23.471秒。IHub主控制器和DIGITIMES某控制器DISCONNECTED约9910秒，备控制器仍有执行。**旧主控制器未被使用时长期断连，不等于所有请求都必然卡在该死进程**；备链路错误及业务能力恢复仍须独立排查。`stalled=false`只是现有45秒budget+grace规则，不能用作5秒或业务健康证明。

证据：`site-recent.json`、`site-diagnostics.json.gz`。`network_ms`包含内部运行阶段，不能称纯网络时间；本轮未切换这些高风控身份的代理、登录或Profile。

另一个直接样例：同一BODY关联`7fa2350c0ecc4b1be8ee`，HTTP在11:34:36.939返回SUCCESS，browser阶段在11:35:02.105返回`access_budget_exhausted`、`network_ms=0/queue_ms=0`；不能由这两个0推断没有花时间或全在网络。`service._execute_deadlined`存在进入其内部timeout前的resolve，`registry_io`取消时继续shield排空；内部还重复resolve、候选解释和候选选择。精确每段占比需W0计时，但**正常路径重复检查和deadline未覆盖所有同步工作是代码可确认的问题**。

### E2. 浏览器执行与生命周期的具体收缩

改动：`site_strategy/runtime.py`、`external_runtime.py`、`worker_manager.py`、`identity_worker.py`、`fetch_anchors.py`、`storage.py`、`budget.py`及source recipe。

1. **保留有效轻量路径。** 当前Yahoo/Investing/IHub已有origin fetch、canonical listing、warm anchor；Barron’s已有API/客户端频道提取。先按每次操作的capture_method确认是否实际命中，失效为何落回完整导航。不能把“代码存在”算作已兑现。
2. 对同源fetch获取的新列表，只校验请求ticker/查询/频道、有效正文结构和主文档/接口状态；成功后直接解析所需行。取消每次成功recipe后再`page.content()`抓取整个广告DOM、重复generic inspect、全页截图/完整iframe遍历。挑战/错误场景才抓一次有界证据；正常成功只保留字段和小证明。
3. 正常访问等待`commit + 必需DOM/接口响应`，不等所有广告/DCL/无关iframe；现有这些网站commit导航保持。Investing冷路径目前可进入最长30秒capture_listing，拆明“文档尚未到/真实挑战/新闻列表提取/控制器未响应”；健康DOM提取不得统一等待30秒。真实挑战移交专用恢复，不在每个ticker请求内反复恢复。
4. warm anchor按**identity+origin**复用，只缓存可取网的环境，不复用旧ticker结果。默认每identity一个空闲anchor、短TTL约30秒；下一次查询总要真实取网。若保留2页能在实测中减少明显冷启动而无额外CPU，则允许2，不设教条值。冻结失败立刻退出idle池并关闭自有BUSINESS页，不在90秒内继续跑广告。归还前移除本次request/response监听器、临时CDP session，防止回调/句柄随操作增长。
5. Profile和cookies保留；自有临时BUSINESS页/iframe随操作释放，维护/人工/原始challenge页按明确owner保留。controller重连时按既有owner清理失去活任务关联的BUSINESS target，不遍历/关闭未知人类页面。正常路径不执行完整历史target对账，只处理本操作owner；低频回收再做有界差量核对。
6. controller无响应：到操作deadline取消该命令，最多约1秒确认结束；不可恢复时退役**该控制器及其自有操作页**。旧代际token不能提交新结果。共享同controller的其它命令将明确收到失败并各自续作，不能假装它们还在正常执行。保留External Chrome/Profile进程；只有已确认原生Chrome本身卡死且安全维护窗口成立时，才按既有所有权规则有序重启单身份，不能全栈restart。
7. 普通只读`registry_io`不再无界shield：读使用query_only、短busy/progress预算、独立小read executor，取消不持有Site/Identity执行槽；必要事务/target所有权提交必须等自己的真实结果，但只许标量短事务。源与Site、identity、Chrome不得重复多级先占后等；联合许可只取得一次。
8. 同身份普通访问用隔离临时页并发，login/recovery/profile写维护保持独占。不同Context不能保证同一持久Profile并发写安全；不能为追求并发复制Profile。复用现有OperationGate读共享、维护写独占，不新增一套锁。

### E2.1 Site正常入口也必须做减法

这部分与浏览器轻提取同为P0，不能只优化page.goto：

- `service._shared_key → _execute_deadlined → _execute_uncached`中的重复resolve合并为一次编译策略读取。当前`effective_candidates`既为解释读取profile/egress/auth/identity/health，随后又实际选择一次；**从正常执行移除完整解释构造**，诊断API按需生成，失败时最多附实际用过候选的小原因。
- compiled Site策略、批准出口、认证要求按catalog/scope revision缓存；普通请求只看小型当前owner/维护epoch与必要实时健康标量，不每次扫描所有组合、展开全部策略/identity模型。WorkerManager已有snapshot缓存保留，将每次跨线程sum revision查询改为现有revision变更通知或单标量revision读取。
- **JointBudget计数异常安全是P0。** 现有`permit`先递增active/site/identity，再调用`identity_interval()`，之后才设acquired；service的回调会同步`get_identity_runtime`。本轮`joint_budget_fault.py`实测数据库异常后实际操作0、三个计数各残留1。将身份节奏小标量预先读入/按状态更新缓存，condition内仅做内存比较和一次无外部调用的计数提交；所有异常/取消路径都有对应释放。给出“0个实际permit→active全0”不变量测试，禁止靠周期性粗暴重置计数掩盖泄漏。
- **HTTP_PUBLIC专用薄分支**：使用publisher client、明确批准出口、URL/重定向边界、本站HTTP容量及真实失败状态。没有真实身份/权益要求时，不以Chrome Profile可用、driver、session/target和浏览器维护状态作为公开HTTP前置。保留auth.required站点禁止公开路径这一实质规则，不把所有公共GET都套浏览器候选治理。Reader同样使用自己HTTP资源，不先领取一个Chrome身份。
- 当前共享key序列化完整request和identity/runtime/egress对象，`_poll_due_at`等调度字段还能改变key；改为固定的`endpoint+query+channel+access context`小键，**只合并同时在途的同一真实请求**。每位消费者必须引用相同物理proof；结束后的旧缓存不当下一周期fresh。源/策略revision只使编译配置失效，不把完整配置重新带入每次hash。
- Site入口总deadline从第一步开始，HTTP RPC与body stage复用一个绝对deadline，不每层再送新预算及额外15秒缓冲；本地需要短暂收尾的成本单列且有界。取消到同步线程/IPC的真实结束要可测，不用层层shield让caller超时后永久占槽。
- `content_enrichment/pipeline.py:_managed_browser_read`还有独立的进程内固定2槽，已由Site管理时删除这层重复限制及“slots.locked即返回deferred”空转。Site统一承担BODY与CRAWLER容量和公平性；仅不经Site的独立测试/旧适配器保留自己的实际资源上限。
- `AccessResult`新增/统一`total_elapsed_ms`与`resolve_ms/admission_ms/execute_ms/finalize_ms`小阶段；超时也带截至当时的阶段。`network_ms=0`不能用于展示总耗时，进入失败分母的无取网请求必须呈现真实等待年龄。

**5秒是正常warm业务操作的目标，包含控制器、取网、提取和自有页收尾；冷启动/挑战分别计入总端到端长尾，不从验收中剔除。** 先消除长期执行/驻留泄漏，再测试同identity2→3→4，完整性、登录、拒绝率和真实CPU均满足才调高；不存在“同Chrome身份技术上只能串行”的一般限制。

代理取舍先按本轮公开feed冷/暖各两次结果保持既有效出口；高风控身份只在专门canary中比较现有已批准出口，保持原验证身份绑定，不临时切出口制造风控。无本轮真实对比的身份不凭代理标签判断快慢。

### E3. 容量按实际工作量计算，避免限制层层相乘

每物理unit的需求为`1/P`，不同endpoint/query/channel累加，真正相同请求共享后再算；TAIL、重试、wrapper与recheck另加。对资源域r：

`λr = Σ(该物理请求进入r的概率 / 配置周期)`

`平均占用 = λr × E[实际占槽秒数]`，起始容量`ceil(平均占用/0.7)`；同时检查**CPU core需求=Σλ×实际CPU秒**、驻留内存与尾部。Wall时间×并发不是CPU估算，失败成本必须加入。source许可、joint Site、identity、browser worker任何一层不足都要列出来，不能只输出一个“adaptive”值。

当前旧快照非TAIL物理需求约5.052请求/秒（9 ticker），不是9个任务/周期；20 ticker必须从实际编译出的端点/查询重新枚举，不简单将所有共享源乘20/9。以下仅是三类来源的明确场景验算，**不是现有承载通过**：

|30秒、20ticker场景|物理需求λ|若单次5秒，70%利用率所需槽|若同身份仅2槽，平均单次须≤|
|---|---:|---:|---:|
|Barron’s每ticker两个必需独立请求|40/30=1.333/s|10|1.05秒|
|IHub每ticker一个请求|20/30=0.667/s|5|2.10秒|
|Investing每ticker一个请求|20/30=0.667/s|5|2.10秒|

因此“全部浏览器≤5秒”仍不足以自动兑现30秒；要把大部分列表变成约0.3—1.5秒的HTTP/origin-fetch，或经验证放宽身份并发/采用真实完整共享请求。**不能用RSS替代未刷新必需浏览器频道。** 若实际查询多于此表、真实站点拒绝相同身份并发、或需要长期30秒执行，应如实报告该source缺口并提出官方端点/合法付费feed/额外身份等具体选择；不静默放长周期或剔除分母。

起始角色预算：HTTP见C1；浏览器global8，new BODY常态2、空闲可借至4，CRAWLER在需求存在时有服务保障；后台任务至少一个服务机会，由同一个joint队列轮转，不再有PSI禁用。BODY worker只领取能进入实际资源的工作，不预领8个后长期持lease排浏览器；HTTP与本地提交可以继续。后台容量有上限，无需再复制一套专用Chrome/Profile。全站不同身份总并发仍被global8限制，不能每identity提高到4后变成几十个原生页面。

global8是试运行初值，不是永远正确的8核阈值。削减单次成本后，按同一公式及CPU/RSS实测验证8→10→12是否有净收益，再决定稳定上限；若8已经承载不了真实20ticker需求，不得仅因旧配置写8就拒绝调整，也不能在资源曲线未知时直接开到20。HEAD/BODY借用和后台份额共享同一个物理总量，不能分别配置后相加超出预算。

`scheduler._limiter`现有按scheduler_group最小ceiling、Yahoo需求粗除2、source demand与group key混用需要移除；新值直接由**本资源域实际物理unit**计算。保留一个稳定上限、一个需求估计与唯一节奏规则；容量至多每30秒重算、平滑调整，不每轮改变造成震荡。配置展示必须能解释“需求/单次占槽/容量/限制层”。

## F. 【P0】数据库、NativeContent、CDC与长期增长

### F1. 先停止无业务价值的读写，再讨论锁与VACUUM

|对象/现路径|保留|删除/合并/轻量化|生命周期/验证|
|---|---|---|---|
|sources/bindings/terms/config|当前业务配置、修改版本|每秒全量load/model_validate/plan重建→revision变化才做|9→20ticker配置无变化时仅标量读取，Native展开0|
|Collector units/checkpoint|每物理unit当前必要恢复状态|按ticker重复state、完整对象、消息列表、重复save|失败/部分页/304/重启夹具；状态体通常≤2KiB，真实复杂cursor单列测量|
|cycles/物理证明|实际起返、有效性、请求键、结果/原因、consumer引用|逐logical consumer复制JSON；计划tick空转也写完整run；多层start/finish/receipt|原始物理证明48小时；按unit小时聚合7天；活动配置/缺失区间可重算应执行周期|
|captures/inputs/first_seen|未ack增量；必要首次发现/时间依据|已ackpayload长期重复留存；所有未变input key永久增长|ack后保留24小时小摘要，payload删；inputs短TTL；first_seen并入文章/origin小身份事实，不能清掉后使旧UNKNOWN稿变成新稿|
|旧poll/distribution技术表|冻结旧在途、既有sweep、必要旧查询|停止所有新实时run/snapshot/observation_sets/priority/verdict写|旧队列收尾后关闭worker；按主键范围逐步删除技术历史，不先全库迁移|
|live_articles/deliveries/origins|B节三种唯一业务事实|不变消息不写last_seen、不刷新完整JSON，不按poll生成新行|体量按新文章/真实关联/修订增长，不按轮询次数增长|
|旧body results/jobs|已有成功正文、真实当前失败原因、待接续的在途|双状态、多任务模板、prior_attempts完整数组、永久30分钟全历史重抓|结果按需接入新键；有界失败摘要，真实正文版本保留引用|
|raw/standard/stream|对外业务身份、首次发布、真实修订、消费与审计要求|过程PENDING/PROCESSING/COMPLETED重复全对象写；每源/每ticker重复正文|一份不可变正文被多记录引用；stream唯一性保留；保留已有业务保留期|
|NativeContent/files|内容实体、引用、一次持久化、损坏可识别|row_factory隐式展开、大对象递归反复序列化、每次全块反复校验|按ref显式读取；小型LRU见F2；不为benchmark清OS cache|
|artifact/ref/CDC|明确正文/输入引用、真实发布/修订/决策事件|每次状态UPDATE递归json_tree全对象、技术事件全历史投影|新薄表显式增删少数ref；BUSINESS内容仍有引用，不按诊断TTL乱删|
|Read/UI指标|当前source/unit freshness、小型工作状态、业务消息与真实质量|页面刷新全历史JOIN/COUNT/Native展开、将技术台账变成发布前置|批量读薄head；指标短缓存5秒；历史明细按需分页，不能阻塞新消息|
|维护/清理/备份|必要可恢复业务与有界过期清理|启动全表修复、业务事务内全库扫描/目录遍历/压缩/fsync|索引cursor、小批次；离线备份维护，不参与单次请求|

### F2. NativeContent与事务的硬边界

本轮隔离夹具：1,050,000字节正文被分9块，连续两次decode分别调用9次ContentFiles.read；每次包含manifest读取、gzip解压、块SHA，再做整体SHA。**这是工作次数证明，不是生产耗时benchmark。** 当前encode还递归`json.dumps`判大小、压缩、文件fsync；在writer中反复调用会把磁盘/CPU波动放大成全库停顿。

实施规则：

1. 新薄仓储不使用自动Native row factory。claim/renew/select status仅取标量；无Native/Pydantic/文件读取。续租`UPDATE … WHERE article_key/token/state`，用rowcount判所有权，不先读全job。
2. 不可变输入/正文锁外编码一次；业务提交只插ref，旧Raw/Standard对外适配器复用同ref。拒绝“先把ref展开成全文，再各自encode证明内容相同”的路径。
   内部仓储分`prepare_payload`与`commit_prepared`：前者输出可直接提交的编码/refs，后者不再次调用NativeContent.encode；保留外部输入禁止伪造reserved marker的规则。冷读取整值时只加载一次manifest并依次校验/读块，不每个range重新打开同一个manifest；range接口供真正分段读取保留。
3. 原始内容首次写入验证digest；本地不可变文件按digest缓存解析结果，Bus/BODY/API/Projector每个角色**最多32MiB原始内容或64项，先到为准**，同进程共享一个上限，不按每个NativeContent实例/identity worker各建一份；全部署名义cache不超过128MiB，Python对象实际RSS另测。缓存验证以文件身份/变更检测为边界。命中不重复解压/SHA，未命中一次验证后复用。损坏/文件变化即失效，必要备份/离线校验仍做完整验证。无需每次renew重验内容，也不需要先增加整套分布式cache。
4. 新thin表显式维护少数`artifact_ref`，不安装通用递归JSON trigger。现有业务表改写前确认current triggers：job只有status变化CDC、artifact是UPDATE OF data_json；别把已做的瘦身再实现一遍。需要兼容的CDC只发布小标量/ref，消费者按需读取实体。
5. 一份正文在Bus、Read、逐ticker记录间只保留一份可引用内容；跨库transport优先传ref及必要字段，已有可达性/备份语义保留。不能删除仍被W1/W2/W3或历史业务引用的文件。

### F3. 事务、查询与维护

- 每个正常写事务只处理当前小batch，目标P95≤20ms、P99≤100ms；出现>1秒事务即不通过该改动验收。不是加超时杀死事务，而是禁止重活进入事务；SQLite提交中不可任意取消，提交结果未知时按唯一键查询确认，不整段业务重放。
- 领取用`(state,due_at,key)`等标量索引，foreground/background可用两个小的索引查询合并候选；取消OR+宽JOIN+JSON表达式排序。已经证实走精确PK索引的publication查询不为“优化”重写成更重结构。
- 所有必要读取指定列，禁止热路径`SELECT *`、完整data_json和历史全量COUNT。不得对27GB库临时执行无预算dbstat/ANALYZE/VACUUM来“找大表”。先用已有索引、少量样本及分段计数，维护窗口再取得完整物理空间分布。
- 维护每批例如32—128个标量ID：锁外选ID/引用、SQL小事务删除，再锁外清文件；用固定小cursor续作，不每批重扫所有表定义。`source_native_gc.reconcile`当前在BEGIN内枚举所有表和row；新Bus热表走显式refs，不再把通用全库可达性扫描当清理每个技术记录的前置。
- SQLite WAL保留；检查长读事务与checkpoint进展。定期PASSIVE checkpoint、短读快关；不在请求里强制TRUNCATE、全库VACUUM或整库压缩。旧大库页复用优先，真正缩文件放离线维护/受控新库迁移，不能作为业务恢复前置。

### F4. 体量预算与增长成本

实时业务新增量由**真实文章+真实修订+article×ticker关联**决定；空轮询应只有一份很小的物理证明/当前head，不产生业务CDC/Native文件。

- 按当前5.052物理请求/秒，48小时约87.3万份物理证明。实施目标每份含索引均摊≤768字节，约0.67GB；20ticker按实际需求重新计算，不以“库目前还小”省略预算。必要字段超预算要给真实成本，不删失败证明凑数。
- physical证明48小时后压成逐unit小时计数/区间摘要7天，保留expected、wire、valid、late、failed、unknown、最大开放缺口；文章与修订业务证据不随诊断过期。验证脚本不得把聚合后的历史重新假装成逐请求精确时间。
- 历史raw/standard/stream不擅自按年龄删除；先停止重复写、引用正文一次、退役技术表。业务存量随新闻增长是合理成本，永久按poll次数增长不是。
- 对已经过滤/抑制/旧文的稳定身份，保留origin/文章上的紧凑first_seen及必要终态；大payload可过期，但不能把去重/首次发现小事实也删掉，导致下次未知时间旧稿重新接纳。无需额外再建一套逐raw_hash永久墓碑。
- 压力测量使用相同PID/starttime及cgroup计数的5—10分钟差分：读写bytes/s、CPU秒/有效请求、事务数/新文章、Native读写次数/新文章、常驻RSS与page/driver数。容器累计TB不是当前I/O速率。
- 稳定后用同样输入replay至少72小时等效量：技术存储进入保留期平台；空/未变消息Native新文件和业务CDC为0；正文下载次数按唯一article/revision计，不按ticker/source倍增。业务事实增长另列，不能以GC删业务数据兑现平台。

## G. 关闭压力硬门禁，恢复有界服务；健康按实际进展

### G1. 关闭范围与保留范围

删除`message_bus_v2/pressure.py:background_allowed()`对业务执行的阻断调用：

|调用方|本轮方案动作|
|---|---|
|`collection_plane/worker.py`、`corporate_ir/coordinator.py`|TAIL/恢复页不再因PSI禁止；保留独立unit、真实站点退避和公平容量|
|`content_enrichment/service.py`、`rechecks.py`|恢复recheck领取，不再受PSI和“已有8个旧job所以不再调度”的双层饥饿；改article due+真实可用槽|
|`scheduler.py`、`persistent_runtime_v2/bus_orchestration.py`|去掉PSI导致的sweep/后台业务无限延后；已由新Collector负责的实时旧调度关闭，不能双跑|
|`codex_worker/jobs.py`的MESSAGE_PIPELINE_RESOURCE_RESERVE|撤掉本次消息优化引入的PSI启动硬阻断；恢复自身既有调度/容量，不新增W1/W2/W3限制|
|`v2_read/maintenance.py`、`transport_archive.py`|不再因PSI永远停止GC；用小批次/索引cursor和真实业务队列公平调度；维护不能长占writer|

**保留**：站点明确429/身份挑战/必要维护独占、实际物理资源容量、DB真实损坏/磁盘满导致的不可写状态、owner/token一致性。PSI只做观测，不再决定允许/禁止。也不新增“另一个名字的全局压力开关”。

服务公平采用现有joint队列/Bus队列的小改：HEAD/new BODY优先但有界连续处理，等待超过一个配置周期的TAIL/recheck有一次服务机会；后台1个实际操作在途、空闲可借容量。业务新流持续时后台等待仍要有最大年龄告警；若平均服务率不足，则按E3暴露容量缺口，不允许仅靠aging掩盖过载。

### G2. 小型功能健康与定向恢复

复用每unit head、每article当前状态、现有worker代际，不再建一套健康账本。每10秒内存汇总、最多小字段批量落head；无新文章时BODY没有完成数不是故障。

|故障域/判据|轻量恢复|明确不做|
|---|---|---|
|publisher HTTP池：计划有需求但无真实取网，且pool阶段失活|C1只轮换本publisher client；下一个正常周期验证|重启所有Collector、改Chrome Profile/出口、抹失败计数|
|单endpoint：持续无有效列表|保留精确原因、checkpoint和本站退避；解析/URL问题用一次真实probe确认|一个端点失败熔断所有IR/ticker；每周期抓截图|
|controller：进程死/IPC断连/操作取消不确认|退役该controller代际，自有页收尾，重新attach原Chrome；健康浏览器身份保持|DISCONNECTED但全部功能仍显示healthy；整Site/Chrome全重启|
|身份/站点：真实challenge、登录/权益缺失|沿用单身份恢复任务/人工能力；其它来源继续，证明缺口留在全量指标|普通BODY每5秒抢登录/解挑战；无限轮换出口|
|BODY已得结果但DB未提交|从result spool接回，重试本地commit/fanout|重新下载或把已得FULL降为UNAVAILABLE|
|Bus writer：有待办但commit无进展|展示当前操作、开始时间和内层阶段；已领取工作不再续加大batch；短事务改造后应自然恢复|自动kill未知提交中的writer、重启所有角色、无限延长SQL timeout|
|Read：业务head与checkpoint差、最老业务事件年龄持续增长|重连本投影器、按真实业务事件接续；技术状态旁路薄head|用Owner已发布掩盖UI/下游Read陈旧；重放全部Native历史|

告警判据以业务周期表达：某必需unit连续2P无有效列表、当前BODY超过其明确预算、结果待提交>2秒、交接队列年龄>1秒等。指标缺证据显示UNKNOWN，不能显示0秒或健康。自动恢复一次动作后在下一正常请求验证；同原因反复出现只更新故障状态/告警，不每10秒创建一个恢复任务。

### G3. 必须正视的业务边界：无限历史重抓不能在固定资源上永远兑现

现库至少有2426 READY、781 FAILED正文结果（此前固定快照；并非全都已确认eligible）。若其中3207篇均每30分钟检查一次，仅历史检查就需约**1.78次/秒、15.4万次/日**；每新增一天文章会永久增加底座需求。即使消灭I/O重复，这一工作量也会无界增长。**“固定8核16G、保留无限历史每30分钟全量BODY重抓、实时接近100%且新消息≤10秒”不能长期同时保证。** 这是业务工作集合的设计问题，不能再用压力门禁压住后不解释。

本方案推荐明确收缩为：

1. 新文章首24小时保留30分钟条件检查；有ETag/Last-Modified时做同publisher轻量条件取网，304不重抽取/写正文；无条件接口则用固定有效主路线。新hint/官方修订信号立即触发一次检查，不等30分钟。
2. 24小时后不再全历史定时下载；仍通过**全部既定来源/频道/TAIL**发现provider修订、文章重新进入列表、官方更正/修订通道，从而重开同article更新检查；不设“文章太旧就禁止更新”的门禁。
3. 已存在recheck积压按原due公平消化并检查一次；不直接把1275项标完成或全部删除。之后不再将离开活跃更新窗口的每个历史结果永久复制成job。

这是明确的业务边界建议：极旧URL发生无任何列表/更正信号的静默修改，可能只能由人工触发发现。**不能声称此类更新覆盖与无限轮询完全等价。** 来源列表覆盖、真实已发现更新、ARTICLE_ONCE和正文质量不变；若用户要求所有历史URL也永久30分钟检测，需单独接受其线性增长成本并建设额外更新采集容量，不能在本次8核资源内作虚假保证。实施可先完成其它收缩与门禁移除；本项业务策略必须在开发验收记录中明确采用哪一个边界，不能隐式上线。

## H. 可直接执行的工作包、迁移与验收

### H1. 实施顺序与依赖

恢复性小改与整体收缩属于同一个设计；允许先部署能停止大范围失败的改动，不必等待所有薄表迁移完成。**P0是周期兑现和整个数据库热路径；P1是有足够服务能力的真实正文质量。**

|工作包|优先级/依赖|具体交付与主要位置|完成判据/成本|
|---|---|---|---|
|W0 契约与基线|P0，首先|补物理取网/阶段计时，固化当前source/unit编译清单、外部Raw/Standard/Stream、CLOSED_SWEEP/override/ARTICLE_ONCE夹具；复用现有eval脚本|不再扫描全文；每物理操作一个小span，不按ticker复制；基线包含失败/无事件unit|
|W1 故障域止损|P0，W0后可独立|C1分池/坏池恢复、C2失败checkpoint、E2.1联合许可计数异常安全、G1关PSI硬门禁并保留公平服务|隔离注入池损坏后其它publisher继续；许可DB异常后active全归零；失败数1→2→重启仍正确；TAIL/recheck真实执行，不是只进入queued|
|W2 写路径基础|P0，W0；支持W3/W4|F2标量claim/renew、显式Native、锁外不可变内容准备；去业务事务内维护；薄查询索引|续租Native读取0；热点事务无文件/模型/网络；P99≤100ms，>1秒失败并定位；本包不等新架构即可修旧路径|
|W3 实时路径收缩|P0，W2|B三类薄状态、C4直接文章入口、D1一次业务发布，旧PollJournal/Distribution实时退役；适配sweep/相关性/UI|稳定列表重复100轮无业务写/Native增长；100文章×20ticker共享文章一次；不双写实时路径|
|W4 正文与本地收尾|P1兼P0，W2；合入W3|D2固定有效路线/最多一次fallback或retry、5秒普通重试；D3别名/公开复用；D4结果spool；Apple/ETNews/国家域/权益来源夹具|SQL故障不重复下载；Google/Yahoo解wrapper跨源复用；真实FULL质量回归，失败不拿summary充数|
|W5 浏览器与容量|P0，与W2/W3并行推进，依赖W0证据|E2轻提取/生命周期/控制器取消；E3实际unit容量，修scheduler_group/identity限制重复|IHub/Investing恢复真实有效列表；Yahoo必需两频道各有证据；同身份并发实验按CPU秒/拒绝率/页残留验收|
|W6 增长与运维契约|P0长期，W3后|F生命周期、技术CDC停止、UI薄head、GC小cursor；G2功能健康；G3明确更新政策|48小时后技术体量平台；保留业务引用；健康能识别本轮实际故障；后台无数小时静默饥饿|
|W7 全量生产验收|W1—W6完成|全部source/必需unit真实运行、20ticker离线/受控负载、24小时+交易峰值+72小时成本验证|按H4逐项出未达标清单；一次1小时smoke不能宣布长期目标完成|

不要把工作包演变成新平台：新增薄仓储/实时入口、publisher client映射和已有队列的小改足够；复用现有parser、budget、owner、ContentFiles、stream。删除旧路径与新路径同包完成，不只添加新代码并保留旧worker空转。

### H2. 必要测试集

|测试类型|必须覆盖的具体情形|
|---|---|
|HTTP故障隔离|cleanup异常、池容量遗失、请求options异常、池排队超时、取消/close并发；故障publisher恢复而其它publisher的取网周期不变；403不乱换出口；keepalive仍有效|
|许可异常安全|JointBudget身份状态读异常、取消发生在准入/取得许可/实际执行/释放各边界；空运行最终global/site/identity/BODY计数全0；一个身份异常不阻塞其它身份；不能周期归零损坏仍在途任务|
|checkpoint|成功→失败、首次失败无message、已提交一页后第二页失败、多个端点一坏一好、304、停止/重启；失败计数/退避推进且未提交cursor不前进|
|去重/更新/订阅|raw广告变、列表排序变、source/binding/roster版本变都不重抓旧文章；真实标题/正文更正有修订；公开跨Google/Yahoo/直源复用；语言/真实权益隔离；新ticker首次交付|
|所有权与崩溃|领取后取消、lease过期旧owner回来、spool写后SQL失败、commit成功ack丢失、发布后Read失败、混合已完成/未完成ticker续作；全部保证下载不重做、发布不重复、不丢新文章|
|事务/成本|生产大小Source/roster/正文夹具，20ticker、大列表大量未变+少量新稿、慢磁盘/锁竞争、L2慢；断言writer内无网络/Native解压/fsync/模型调用，统计SQL与Native次数|
|浏览器生命周期|卡死page.evaluate、断连driver、关闭失败、冻结失败、iframe增生、cancel无回执、同identity2/3/4访问不同ticker；Profile/cookie及challenge原页不被删；旧listener不累积|
|功能语义|CLOSED_SWEEP固定窗口/冻结query/cursor/等待当前工作；临时开关开启/关闭/到期/重启/下交易日衔接；W1/W2/W3启动、在途及消费行为无新增限制|
|真实正文质量|预览/登录页/挑战页/摘要不能FULL；ETNews短完整稿、Apple XML、Globe/IR、Investing国家域、Reuters/DIGITIMES实际身份、SEC表格文件；按最终publisher核验|
|长期增长|72小时等效输入、历史库背景、全部未变轮次、重复来源别名、retention跨界、业务refs存续；技术表/Native/Chrome页数无每轮累积斜率|

性能门槛在固定8核16G基线和可复算replay测，不把本地Windows微秒单测当生产性能。真实站点测试先小规模能力验证，随后全量自然窗口；不得用一个测试ticker或只成功的接口替代全部频道。

### H3. 迁移、发布与回退

1. 先核对本地main、origin/main、远端repo及各角色实际image revision，登记其它会话改动；本轮方案未提交/部署任何功能。实施时避免覆盖当前Google规则、交易、UI等并行工作，先核对契约冲突及相关测试，再按届时授权的范围整合。
2. schema只做小表/索引增量，不启动全库Native迁移/ANALYZE。部署W1/W2可先修现有大范围失效；保留现有数据、配置、Profile、Chrome原进程。
3. source切换时停止其旧worker发起新工作，冻结在途ID；在途已取正文优先接入新结果，未完成的checkpoint/delta照原键恢复；同source所有ticker/必需频道切入新实时入口。首先完整覆盖一个公开共享源和一个浏览器源，再切全部，不长期双跑两套pipeline。
4. 已发布身份/正文按需读取旧索引并登记新alias/delivery；不按source改名或重置first_seen制造新文章。sweep适配等待work keys，旧job_ids只为冻结在途兼容。
5. 按实际变更重build Collector、Bus、BODY、Site、API/Projector等必要生产容器。Worker/Runtime只在其调用契约确有变化时同步build；保护W1/W2/W3在途。Supervisor若未改无需为了统一commit数字重启；**契约版本兼容比全部容器同hash更重要**。使用正式compose服务，不用临时容器替代生产。
6. 回退路由前先停新领取、接回spool、确认已发布ID可被旧兼容适配识别；保留additive schema和既有发布，不反向重置cursor/去重。不可通过切回原始旧版本重新发布已交付消息。预先验证回退适配，不临场全库迁移。
7. 发布后只清已验证无运行/模板/动态依赖的旧镜像，保留当前及一个已验证回退版本；不删volume/Profile。changelog记录重要代码/契约/更新政策改变。发布记录列出source切换与角色版本，防止再次把不同版本行为混在一起判断。

### H4. 全链路验收标准与诚实分母

时间统一UTC持久化、展示北京时间/纽约时间；语义日按现有纽约02:00边界另列，不能把自然日04:00UTC当交易语义边界。所有窗口按配置生效期、日历、override范围计算expected，不能用成功run倒推需求。

|指标|定义与分母|目标及报告要求|
|---|---|---|
|实际请求启动周期兑现|每必需unit应执行周期中，有匹配物理**真正取网**起点的周期；调用HTTP SDK前不算wire|全量≥99%；逐unit/频道列出，样本充分unit≥98%；真实失败仍进分母|
|有效新鲜列表周期覆盖|全部expected中，匹配查询/频道/消费者、响应及列表校验成立，按该次取网可证明fresh的周期；304需既有有效基线|全量≥99%，逐unit≥98%；不能以平均掩盖0%频道。两者是“接近100%”的执行验收阈值，不是当前已达|
|实际请求/有效列表间隔|同unit真实相邻时点，含窗口首尾开放缺口、0次请求unit|均值≤1.05P、P95≤1.2P；>2P逐项解释，真实拒绝仍算未达。过期/失败不能只显示上次成功耗时|
|首次采集→可消费|新canonical article的首个有效列表body到达为T0，关联每个应交付article×ticker的Bus stream可claim为T1；Read/UI可读另记T2|尽可能≤10秒；执行目标≥95%应交付项在10秒内、P99≤30秒，报告最大值与未完成年龄。L2/BUFFERED/重试分别拆，但仍留全量分母|
|发现覆盖/上游延迟|必需渠道、分页窗口和独立官方/浏览器对照清单；发布时间可信时另算publish→首次采集|不得用已发现文章自身证明没有漏采；稳定期每必需来源/频道至少做一次独立列表对照，失败频道标未知/缺口|
|BODY真实完整率|新canonical article中既定eligible文章，截止时已取得经质量规则及抽查确认的FULL/SHORT_FULL / 全部eligible新文章|每天及完整交易窗口>80%；未完成/失败/未解wrapper保留，权益受阻另列仍计；复用命中与新下载分别报；不把NOT_APPLICABLE任意扩大|
|单次业务成本|每有效物理请求CPU/IO、每新文章事务/SQL/Native/CDC、每唯一正文下载次数|未变输入业务写/Native新文件=0；一次正文结果不因SQL/Read失败再次下载；writer及交接门槛见C4/F3|
|浏览器正常操作|warm/cold/挑战全分类；navigation、必需提取、关闭、worker等待、真实取网与CPU|健康warm P95≤5秒；总分布保留cold/失败/取消。每1000次操作后自有页/driver/内存无单调增长|
|后台及故障恢复|TAIL/recheck实际启动/完成与最老due；池/身份/DB/Read分域故障注入|单publisher不扩散；可恢复池/控制器故障应在下1—2个周期恢复；不以queued增加代替执行；长期欠账必须解释容量|
|持续资源/生命周期|相同实例计数差分，覆盖CPU、IO、RSS、swap、WAL、Native、技术表和Chrome target|无持续swap-in/out恶化、无OOM/反复controller重启、无技术状态按poll永久增长；不新增W1/W2/W3阻断换取指标|

T1不能只取最快ticker首条，必须同时报告每article所有适用ticker最后交付时间；不相关有终态理由不计应发布项，但未完成L2不能提前标不相关排除。T2若只是UI投影延迟，不能假说Bus没发布；反之仅Owner写入不能称下游Read已可用。

Body key可能有别名/跨源重复，验收以合并后canonical article计，并列raw-key结果便于对账；无法解wrapper不得猜测合并。重要修订单独cohort，不能把上周已发布文章的first_seen当成今天新文章制造27小时“下载耗时”。Body标签必须抽查最终publisher的可见完整稿、首尾/段落/表格完整性；分层抽查高流量及历史高失败站，报告样本和不确定性，不能仅看程序标签。

连续窗口：快速smoke只验证链路/恢复；稳定性至少24小时连续自然运行，覆盖一个完整交易时段和W1/W2/W3峰值，再看72小时增长曲线。样本太少、无新消息、来源访问受阻则标“尚未证明”，不补合成消息充自然成功分子。负载replay用于20ticker容量与崩溃验证，与自然运行指标分开。

验收脚本固定输入配置时间线、逐物理证明、逐article状态与publication IDs，输出**source×mandatory unit缺口表、逐新文章时点表、资源差分/增长表**。窗口末尾未完成样本保留，后续补报完成曲线，不回填成当时已达标。

## I. 全文统一review与最终实施取舍

### I1. 为什么这次不会再只把堵塞移到下一层

|本对话此前处理|保留已有收益|本方案进一步删除的复发条件|
|---|---|---|
|10/23正文预算、文章复用、同身份多页|执行开始才计fetch预算；ARTICLE_ONCE；已验证身份|source scope/hint过度限制复用、无收益三段回退、本地收尾失败再次下载|
|10/24固定相位、实际间隔、短写优先/分片|按unit固定周期，不回到ticker串行|不再仅增加intake分片；删除分片内部完整对象/多层台账工作，不让writer长任务继续支配全部分片|
|10/26物理证明、warm fetch、head/tail|必需频道分别验收、真实共享取网、轻量路径|证明不按ticker复制；不把客户端调用当wire；控制器/页生命周期及真实30秒容量重新核验|
|10/27业务增量、CDC瘦身、L2幂等|Read可追平、未变输入减少、已完成交付不重做|去掉过宽raw/roster指纹和实时distribution run；一次短原子发布，结果与本地续作分离|
|10/27独立Collector/小账本、窄续租、Registry offload|网络不依赖Bus writer、现有Chrome身份保持|Bridge不再回到旧实时workflow；窄列更新之前不完整hydrate；读取消不无界shield；小账本也有生命周期|
|本轮新增故障发现|坏HTTP池可隔离、失败checkpoint有精确复现|联合许可异常安全、condition内无DB、公开HTTP不走Chrome候选治理、历史重抓集合有明确边界|

此前1小时末次成功率、10/11 FULL、或12/12小canary不能覆盖整交易日/20ticker/长期增长；本次H4不以这些小样本作为整体验收。也不把先前已有效的Collector、Read瘦身、同身份并发全部推倒重写。

### I2. 必须保留的机制及更轻实现

|必要业务约束|最轻保留方式|不需要的旧实现|
|---|---|---|
|不漏消息/可续采|每物理unit已持久化页cursor+未ack delta；TAIL窗口覆盖|每轮业务run、完整历史快照、每ticker复制checkpoint|
|去重/发布唯一|canonical article/alias、article×ticker唯一键及小first_seen事实|多套source版本/raw_hash/roster指纹乘法|
|内容更新与ARTICLE_ONCE|hint触发、正文digest修订、更新原publication|每次raw变化重抓/重发；永远全历史半小时重下载|
|并发一致性|短CAS token/revision及一次发布事务|全模型重校验、长事务整批ticker、跨进程重复锁|
|身份安全/权益|owner+profile/egress绑定、维护独占、真实auth要求|公共GET也检查Chrome会话；成功路径全页挑战证据；保留所有历史页/driver|
|完整正文质量|站点有效解析、FULL/SHORT_FULL正确规则、合法获取路线|所有站默认HTTP→Browser→Reader；摘要伪装FULL|
|持久性/损坏识别|不可变内容一次持久化、显式refs、未命中/备份校验|每次lease/status读取全部块解压验SHA；重复正文副本|
|恢复/审计|当前薄状态+结果产物、唯一键重放、两天物理证明+聚合|永久技术台账、全库扫描/修复、每阶段额外一次业务read-back|

### I3. 资源与收益复核

- **应减少的实际工作**：正常配置不再每秒全展开；不变列表无业务台账；一篇公开文章不因来源/ticker倍增下载；续租不读正文；L2/发布失败不重抓；成功浏览器不重复全DOM/iframe取证；技术CDC和Native重复写显著下降。先用这些量验收，才谈提高并发。
- **有成本但必要的新东西**：三类薄业务状态替代旧实时台账；每publisher懒client替代一个大故障域；很小的待提交结果spool替代重复下载；单次计时替代多层复制trace。这些不得与旧流程永久双写，否则收益抵消。
- **不会新增的东西**：消息中间件/新DB服务、通用工作流引擎、分布式许可服务、全局压力硬门禁、逐阶段完整快照、按ticker独立Chrome、W1/W2/W3限制。
- **不能承诺exactly-once网络**：进程在取得响应但尚未持久化结果时崩溃，恢复可能需要一次受控重复GET；业务发布依旧唯一。不能为消灭这一小概率重复，再给每个正常网络操作叠加昂贵两阶段协调。
- 新文章≤10秒预算应优先留给：交接/接纳<1秒、有效正文常态≤5秒、决策/发布/投影其余时间。需要长L2、合法付费能力缺失、冷挑战或BUFFERED等待的样本会超过10秒；保留并分析，不能把摘要先发当成完成优化。真实限制若使全部目标无法在8核下同时兑现，应呈现具体source/资源缺口和收益明确的替代能力，不能再用系统内部等待解释一切。

### I4. 本轮边界与交付检查

已完成：生产薄快照与Site近期真实数据、两publisher出口冷/暖测试、Native重复读取工作次数验证、JointBudget异常槽泄漏隔离复现；结合既有完整两天周期/正文/事务证据和历轮复发记录，逐段补齐本方案并整体review。

本轮未修改业务源码、生产客户端、配置、身份、容器或数据；未宣称性能目标达标。`eval/message-bus-lean-plan-20261028/`为只读勘查脚本/结果及本地隔离夹具。实施前仍须补齐A2列出的精确内层计时、E3真实20ticker容量，以及G3历史静默更新检测的业务边界；它们不妨碍先执行已证实的P0止损和删减。
