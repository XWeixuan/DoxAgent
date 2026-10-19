# 消息源共性失效：事件循环饥饿、身份路由与修复验收

日期：2026-10-19。范围：Message Bus 新消息抓取、共享分发与正文补全执行边界；不重新创建 Chrome、Profile 或 Site Registry。

## 结论与证据

“爬虫与 RSS 同时失败”确实存在一个共同的内部原因，但不能据此认定所有站点错误都来自同一个原因。

本次生产基线 main `b83382dd`，Bus 运行镜像 `027a61ab9637`。旧的 Site Access/Supervisor 网络命名空间错配本次未复现：两者处于相同的当前命名空间，Bus 服务解析指向当前 Supervisor；无全局代理环境污染、OOM 或重启证据，根盘余量约 55 GiB。

在**同一个报错 Bus 容器中另起独立 Python 进程**进行检查：Site Access `/readyz` 返回 200，TrendForce News 返回 200，The Elec RSS 返回 200/XML，分别约 1–2 秒、0.44 秒、0.75–0.9 秒。与此同时，常驻 Bus 自然轮询对这些不同服务反复记录 ConnectTimeout，延迟数十至数百秒。这排除了本轮“整个容器网络不可用”的解释，但单独此项尚不足以证明业务恢复。

随后使用独立 py-spy 采集常驻进程执行栈：15 秒、20 Hz，主线程 299 个样本中 **291 个处于同步 SQLite 路径（97.3%）**。热点包括：

- `DistributionRepository.ingest` 的逐文章连接/事务；
- `accept_poll_result → resolve_first_seen / record_admission` 的逐条入库；
- `BusOrchestration.run_once` 每个 binding 重读 ticker 的 pause、poll state；
- 每轮重新读取已经终态的全部历史 SWEEP。

例如 AWS RSS 一次召回 2,320 条，全部过期过滤，但旧实现仍为这些历史项逐条维护 first-seen 和拒绝记录，产生数千次连接/事务。这里不是“应该取消时效过滤”，而是**过滤所需的持久化成本没有批量化，并且运行在处理全部网络响应和超时的同一个 asyncio 线程上**。并发爬虫/RSS 请求无法及时推进，表现为假性网络超时；增加源和 ticker 后更容易复发。重启、单站独立 canary、换节点会暂时绕开负载，不能消除这一机制。

正文补全进程的本轮采样负载较低，不应套用 Bus 的 97% 数字；但代码也存在同步 claim、renew、finalize、outbox 事务与网络协程同线程的问题，因此一并修复相同边界。

原始证据和只读探针在 `eval/message_source_common_failure_20261019/`；不保存令牌、账号、密码、Cookie 或 Profile 内容。

## 已实现

1. `StorageLane`：每个业务服务、每个事件循环只有一个已准入存储操作，使用现有线程执行器运行完整同步 owner 操作。等待存储期间网络循环继续工作；不是无限新增 SQLite writer，也不是新建另一套维护进程。跨事件循环调用仍由 SQLite 事务保证最终一致性，不共享 asyncio Lock。
2. Bus 的采集结果接收、单消息入库、共享分发入库/决策/投递、调度状态更新退出网络线程。Runtime 的调度读取、准入与消费者 heartbeat 同样退出网络线程。
3. first-seen 一批一次事务，读取按 400 key 分块；拒绝准入记录每 200 条一事务。保留原有 unknown-date 固定、UPDATED_FALLBACK、30 分钟准入、闭市 sweep、审计与去重语义。
4. pause 每 ticker 每调度轮读取一次；SWEEP 只读取 PENDING/RUNNING，不再遍历终态历史任务。下一轮仍读取新配置，不引入长期过期缓存。
5. 正文补全的 claim/renew/requeue/finalize/outbox 使用同一服务存储通道。网络重试预算、正文识别、免费/付费规则、相关性正则/Jev 不变。
6. 正常退出先取消并收尾 Runtime 所有 in-flight 协程，再关闭 adapter；已准入的同步提交遇取消会等待完成后再释放上层 lease，避免线程仍提交而上层已让出所有权。
7. 超过 1 秒的 owner 存储操作输出 `bus_storage_slow`，使真正的存储压力有独立证据，不再只能看到下游 HTTP 超时。

## 验证与边界

离线新增测试覆盖：慢同步事务不阻断事件循环、存储操作有界、取消等待提交完成、千条过期消息批量处理且审计保留、未知时间与更新回退不得刷新成新消息。原有去重跨线程/跨事件循环、采集、闭市 sweep、分发和正文队列回归继续执行。

发布采用当前精确应用镜像为基线的窄不可变镜像，仅更新 Message Bus 与 Content Enrichment；不更新 Site Access 或 Chrome Supervisor，不清理任何业务数据。上线验收必须观察自然轮询和执行栈，再按剩余错误分类；HTTP 200、`/readyz`、手工改状态均不代表消息业务验收。

## 生产复核与补充修复

首阶段产品提交 `805dfaea` 已发布到 Bus/Enrichment，镜像 `35c1b6dc0fcb`。15 秒复采样中主线程有 76 个活跃样本，**SQLite owner 路径为 0**；忙碌的事务现在位于存储线程，不再占住网络主线程。自然轮询已恢复：TrendForce 两入口、DIGITIMES 英文/台湾、GlobeNewswire Search/RSS、HuggingNews、The Elec、ETNews、Tom's Hardware、Yahoo、Benzinga、Finnhub、IBKR，以及此前数百秒延迟的行业/官方 RSS。只用实际新轮询覆盖旧状态，没有手工将失败状态改绿；恢复采集不等于已经对所有文章完成正文验收。

复核也发现并区分了以下独立问题：

1. **InvestorsHub 验收丢失 ticker 上下文**：短 URL `/stock-market/NASDAQ/COHR/news` 不符合 Site 验收器原来的“公司 slug-TICKER”推断，ticker 为空，解析器排除了全部卡片，被报为 `crawler_contract_failed`。现在由采集 adapter 显式将 ticker 通过现有 AccessRequest.recipe_parameters 传入，客户端保留该参数；不放松验收、跨 ticker 检查或反风控规则，也不需要重启 Site Access。真实 INTC/COHR canary 已返回 SUCCESS/200 并分别解析 6/14 条，不发布 canary 历史消息。
2. **全局默认源与静态 locator 覆盖不一致**：Investing.com 仅登记 MU/INTC/BE/RKLB，NBIS/COHR/ONDS/CEG/SNOW 的错误实际为 `investing_ticker_locator_missing`，不是 challenge。补齐已核验入口，并在 bootstrap 幂等补齐 Source 与复制了旧 map 的 Binding 缺项；保留人工 listing_url、自定义 URL、启停与其他参数。未知新 ticker 仍必须提供可信 locator，不猜 URL、不误用其他公司的页面。
3. **IBKR 退出后线程仍存活**：发布时旧 Bus 已进入 Python `_shutdown`，业务与 DB 写入结束，但残留 IBAPI socket reader 导致退出挂住。原因是取消 `to_thread` 的协程不会取消线程，旧生命周期还允许 in-flight poll 在 gateway.close 后重新连接。增加 poll 取消后的线程 drain，配合先收尾 scheduler 再 close adapters。仅在确认旧进程已无 DB 工作后结束该残留 Bus；没有结束 Chrome。首轮发布曾因 Docker Mounts 列表顺序变化触发过严的比较而自动回滚，改为比较实际 Destination/Type/Source/RW 后重发成功，未改变物理挂载。

Investing 的补充入口以官方页面为证据：[NBIS](https://www.investing.com/equities/yandex-nv-news)、[COHR](https://www.investing.com/equities/coherent-news)、[ONDS](https://www.investing.com/equities/ondas-news)、[CEG](https://www.investing.com/equities/constellation-energy-news)、[SNOW](https://www.investing.com/equities/snowflake-inc-news)。这些入口核验不是远端登录成功的证据。

Reuters、Barron's、Investing.com 的既有身份目前另有持久化人工维护阻塞/丢页任务，主备并非都已可用。不能在未通过真人 Challenge/登录与业务验证前清掉阻塞，或宣称换线程已经恢复这些源。BE 官方 IR RSS 按既有要求不处理；TikHub 的上游 400 与本轮网络饥饿不是同一问题。

4. **启动 DDL 与运行期共用 50 ms 锁预算**：追加发布曾遇到 RuntimeJournal 构造函数 `BEGIN IMMEDIATE → database is locked`，容器自动重启后恢复。50 ms 原用于避免网络线程等待，却同时使正常 Runtime 写入足以打断 Bus 启动。存储调用已经移出网络循环后，将 Bus Journal 的有界忙等待改为 5 秒；保留事务 fencing、有限重试，不增加无限重启或全局 DB 串行锁。

生产自然新消息记录也出现实际 FULL 正文：Google RSS/INTC 6,152 字符，官方 RSS/SNOW 7,040 字符，Yahoo/MU 7,082 字符，Tom's Hardware/MU 与 INTC 各 9,673 字符，GlobeNewswire 多条 1,436–15,856 字符。另两条 Google 召回消息的最终发布站点仍返回 `risk_combinations_exhausted`，应归属发布站点风控，而不是 RSS 没有工作；保留其失败证据，不宣传正文 100% 恢复。

5. **CEG 官方 RSS 的实际网络路径失效**：从已修复的同一 Bus 容器探测，直连仍 ConnectTimeout；既有 `de-standard-1` 出口返回 200、56,275 字节有效 XML。这不是数据库饥饿的残留。为通用 Newswire RSS 增加显式 binding 级 approved HTTP route（最多主备两项），默认保持直连；只对 CEG 设置已验证 DE 路由。传输失败最多换一次已批准备用，403/429 不换出口，整体 25 秒预算，保留 60 秒轮询。不会让普通 RSS 继承浏览器登录身份或创建 Chrome。

6. **跨消息源正文路由绕过已登录的网站默认身份**：这是除事件循环外另一个架构级共性原因。`ScopeResolver` 仅在 `origin_scope_id is None` 时使用站点指定的 `default_publisher`；正常正文 job 恰恰携带原消息源 scope，所以 Google/Yahoo 召回的第三方网站文章会绕开对应网站的既有身份。已经自动生成的空 publisher scope 又比显式默认配置优先，导致换页、重新登录既有身份后，生产正文仍可能使用另一套空 Profile。

   生产只读路由探针确认：Google → Barron's 选择 `publisher-44465cea08be1472`，Google → Yahoo 选择 `publisher-ad2ccd5708de9bbc`，Yahoo → Reuters 试图新建身份（只读模式阻止写入），而相应网站已经明确指定默认 source scope。修复优先级为：**同站 origin 自己的独立 scope → 最终发布站明确指定的默认 scope → 既有 publisher scope → 必要时新建**。通用未知域名仍按域名隔离；TrendForce Press 同站任务继续使用自己的 Press 身份，不合并到 News。既有空 Profile 不删除、不导入 Cookie、不重写 Registry。

   这一项需要对 Site Access 做一次仅含 `scope.py` 的不可变更新。发布前确认没有 OPEN 维护会话、排队/运行中的维护操作或活的待处理 Challenge 页面；保留 Supervisor 容器、External Chrome/Profile 和挂载，验证共享网络命名空间仍一致。Managed Chrome 由自身生命周期正常关闭并按需再启，不承诺它的进程跨 Site Access 容器替换保持不变。

## 最终上线验收（2026-10-19 19:30–19:45 CST）

- **233 个不同测试通过**（合并本目录各次 JUnit testcase 去重；不是把重复重跑相加），涵盖 Bus/准入/跨线程去重、分发/正文队列、源解析、HTTP 路由、网站隔离/维护与最终发布者路由；Ruff 与 diff 检查通过。
- Bus/Enrichment 当前产品提交 `c3503289`，精确镜像 `aa21a1ecd637`；Site Access 当前产品提交 `76c581cd`，精确镜像 `9f4a946af756`。全部代码修复已进入 main、推送 origin 并同步生产 checkout；不是未归并的远端热补丁。发布 manifest 校验对应源文件 SHA-256 与持久挂载，更新 canonical Compose 与组件 current/previous 记录。
- Supervisor 保持原容器 `617b4e1d59f9`，未重启。Site Access 与它的网络命名空间仍一致，`/readyz` 健康；替换前没有 OPEN 维护会话/执行中的维护操作/存活 Challenge 页，本轮没有人为清除待办或重建 Profile。Site 只更换 `scope.py`，没有把整个 Bus 修复镜像覆盖到浏览器服务。
- 只读生产路由复测：Google → Barron's 是 `source:barrons_ticker_news`/`dowjones-main` 主备；Google → Yahoo 是 `source:yahoo_finance_news` 主备；Yahoo → Reuters 是 `source:reuters_site_search` 主备；TrendForce Press 仍是 `source:trendforce_press_releases`，保留独立身份。没有创建新的 publisher Profile。
- Site Access 替换期间确实出现过短暂 ConnectError，未把启动健康当作最终验收。随后保持服务运行并观察自然轮询，**124 个生产启用订阅中 95 个 succeeded、29 个 failed**。此前有共性连接错误的 TrendForce 两入口、DIGITIMES 英/台、行业 RSS、Google RSS、GlobeNewswire 两入口、Yahoo、官方 RSS、API 均恢复采集；InvestorsHub 全部 **9/9 ticker 自然轮询成功**；CEG 官方 RSS 改用已核验出口后自然成功。未手工改 poll status、清 failure_since 或发布过期 canary 消息。

### 剩余事项，不能称为全部业务恢复

19:45 交接前再次读取生产状态，仍为 **95 succeeded / 29 failed**，失败来源仍仅为下表五项。Bus、Site Access、Enrichment、Supervisor 与 Clash 均没有重启计数增长或 OOM。该复核晚于 Site Access 更新约 18 分钟，不是刚启动的瞬时健康快照；也不据此承诺以后绝无单次网络抖动。

| 来源 | 当前结果 | 后续所需 |
| --- | --- | --- |
| Reuters | 9 个 ticker 待人工，主备旧 Challenge 均 TARGET_LOST | 在维护工具重新打开丢失任务页面，完成必要 Challenge 并 Verify；不是继续换 RSS 或加 timeout |
| Barron's | 9 个 ticker `challenge_target_lost` | 对 `dowjones-main` 的 Challenge 待办重新开页并验证 |
| Investing.com | 9 个 ticker `manual_attention_required` | 主备 Challenge 验证；五个新 ticker 缺 locator 的复合问题已修复 |
| BE 官方 IR RSS | 403 | 按既有要求排除本轮修复，不绕过显式封禁 |
| TikHub X User Posts / MU | 上游 400 | 独立真实请求重现，供应方 JSON 也返回 400/重试或联系支持提示；不是本地 ConnectTimeout，需另外进行供应方/参数专项处理 |

Reuters 与 Barron's 的主身份认证事实仍记录为 VALID，故不能直接把上述状态解释成“密码失效、必须先退出重新登录”。阻塞点是遗留 Challenge 待办未完成验证；本轮没有为了变绿擅自清掉它们。DIGITIMES 英文主身份认证仍 VALID，列表已恢复；它的每篇付费正文、所有备用身份仍需逐项真实业务验收，不能从列表成功推定全部订阅权限有效。

### 保留边界

- 本轮解决了执行边界、跨来源身份路由和实测复合故障；没有宣称能够消除网站风控，也没有自动处理 CAPTCHA、MFA 或账号密码。
- 历史过期消息不补发。已有失败正文不会通过改元数据冒充 FULL；发布站点拒绝、订阅权限与人工待办继续保留。
- 存储慢操作仍可能达到 1–3 秒，现已有独立日志且不占网络循环；本轮不扩张为更换数据库、全局串行浏览器或新的自动维护 Agent。新增 ticker 仍须提供可信 Investing locator，不能把补齐当前九个 ticker 误认为任意 ticker 自动识别已实现。
- 维护 Codex Worker 仍默认禁用、没有部署。本轮没有清理业务数据、Cookie、Profile 或代码；宿主机既有发布资源定时器按原生命周期运行。
