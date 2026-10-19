# 消息源共性失效：事件循环饥饿根因与修复

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

最终追加发布和持续自然轮询验收继续记录在本文件。真实 Challenge、登录失效、上游权限仍可能存在，不承诺绕过这些限制。
