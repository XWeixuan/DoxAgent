# 消息源反复失败：根因诊断与根本修复方案

日期：2026-10-04。只读生产勘察窗口：2026-10-03 20:57–21:07 UTC，即北京时间 2026-10-04 04:57–05:07。本文件是诊断与待实施方案，**没有执行修复、改 Registry、切出口、重新登录或重启生产服务**。

范围：Reuters Site Search、InvestorsHub ticker news、Investing.com ticker news、DIGITIMES Semiconductors More News、Barron's ticker news and Other Dow Jones（排除 IBD）。涉及正文时仍以最终发布网站归属策略，不把采集入口与正文发布者混为一谈。

## 1. 结论

本次不能按“五个网站又被风控”处理。至少有两条明确不同的故障链：

1. **四个 External Chrome 源共同失效，直接触发是 Playwright Node 控制驱动堆内存耗尽，而非 Chrome 全部退出。** 2026-10-02 16:35:56 UTC，Site Access 日志记录约 2GB JavaScript 堆 OOM；随后 Barron's、DIGITIMES English、InvestorsHub、Investing 长期返回 `RuntimeError`。正式 Chrome/CDP 仍响应，Site Access Python 服务也未退出，因此 Docker restart policy 没有触发。
2. **Reuters 是独立的持续 403 和故障治理失灵。** 它仍使用 Managed Playwright；在 External 驱动死亡后还能产生真正成功的搜索访问，但随后持续 403。搜索 recipe 提前抛异常、丢弃拒绝页内容；状态聚合又把全部搜索失败记成 `partial`，刷新成功时间、清零连续失败数。现有 fallback 因此长期没有实际发挥作用。

另外两项已确认的放大机制：

- `digitimes-nl-1` 同时承载 DIGITIMES、InvestorsHub、Investing 的列表与正文，串行容量竞争明显。爬虫默认排队期限 10 秒，公平性升档要等 30 秒，升档条件对这些请求实际上不可达。
- 驱动死亡和 403 都被压缩成笼统错误；健康检查只看 Managed driver 的 Python 对象和在途任务是否超时，不检查 External 控制链路。**快速失败时没有滞留任务，却并不代表健康。**

优先做确定性基础修复，再增加自动维护 Worker。仅给现状加一个会重启服务、换节点的模型 Worker，会重复掩盖这些缺陷。

### 1.1 已证实与尚未证实的边界

| 项目 | 证据等级 | 结论 |
| --- | --- | --- |
| Node JavaScript heap OOM、四源集中失效 | 已证实 | 日志、访问事件时间线、Chrome CDP 状态一致 |
| 驱动没有自动重建、健康检查漏掉 External | 已证实 | 运行状态与实际源码一致 |
| OOM 的具体存活对象/分配代码 | 尚未证实 | 无事发前 heap snapshot；不能直接声称是某一行确定性内存泄漏 |
| Reuters 持续 HTTP 403 | 已证实 | 持久访问事件，不是内部 RPC ConnectTimeout 的推测 |
| Reuters 403 是 IP 信誉、challenge、授权还是浏览器身份 | 尚未完全证实 | 拒绝页被 recipe 丢弃，不能仅凭状态码归因 |
| 爬虫 10 秒等待与 30 秒 aging 冲突 | 已证实 | 当前配置与 `JointBudget.rank()` |
| 所有历史故障都由同一 OOM 引起 | 不成立 | 过去确有不同的队列、RPC、依赖、出口代际、页面加载与权限问题 |

## 2. 本次生产事实

### 2.1 部署与资源

| 服务 | 实际运行镜像 | 观察 |
| --- | --- | --- |
| Message Bus | `doxagent-v2:market-news-bus-3ef317a4` | 约 37 小时运行，重启数 0 |
| Content Enrichment | `doxagent-v2:market-news-enrich-3ef317a4` | 约 37 小时运行 |
| Site Access | `doxagent-site-access:market-news-1f0ad0db` | 约 36 小时运行，重启数 0，仍报 healthy |
| Chrome Supervisor | `doxagent-chrome-supervisor:server` | 约 9 天运行，重启数 0 |

容器 `OOMKilled=false`；Node 自身 V8 堆 OOM **不等于** Docker cgroup OOM。观察时 Site Access 内存约 566MiB/6GiB、Supervisor 约 1.92GiB/5GiB；这是驱动死亡后的采样，不是事故时峰值。根分区约 25GB 可用、占用 86%，没有本次磁盘写满的证据。

核查实际文件而非只看镜像标签：Site Access 的 `external_runtime.py`、`budget.py`、`browser_runtime.py`、`service.py` 与本地对应实现一致；Message Bus 的 `scheduler.py`、`news_adapters.py`、`service.py` 规范化换行后 SHA256 一致。10 月 1 日已修复的跨 Identity 队头阻塞与取消释放逻辑仍在，**本次没有证据表明旧修复被覆盖**。

### 2.2 当前失败矩阵

下表采用最新生产 `poll_states`，不是人为测试结果。时间为 UTC；北京时间加 8 小时。

| 来源 | ticker | 最新状态 | 最后成功起始时间范围 | 连续失败 | 访问层真正原因 |
| --- | --- | --- | --- | --- | --- |
| Barron's ticker | BE/INTC/MU/RKLB | failed | 10/02 16:31–16:32 | 805–806 | External 路径 `RuntimeError` |
| InvestorsHub | BE/INTC/MU/RKLB | failed | 10/02 16:26–16:32 | 805–806 | External 路径 `RuntimeError` |
| Investing | BE/INTC/MU/RKLB | failed | 10/02 16:29–16:34 | 805–806 | External 路径 `RuntimeError` |
| DIGITIMES English | BE/INTC/MU | failed | 10/02 16:29 | 803 | External 路径 `RuntimeError` |
| Reuters Search | BE/INTC/MU/RKLB | partial | 字段虚刷新至 10/03 05:59 | **0** | `AUTH_OR_ACCESS_UNKNOWN / http_403` |

`poll_states.last_attempt_at` 等字段采用 poll 开始的 `now`，不都是错误完成时刻；有请求在 OOM 前开始、OOM 后失败。因此不能拿 `failure_since` 早于 OOM 几分钟来否定控制驱动故障。对齐因果应使用 `access_events.occurred_at` 和带时间戳的进程日志。

所有来源包括健康 API 源的实时 poll 都在 10/03 06:00 UTC 左右停止。实际日历 `XNYS:2026-10-03`、`XNYS:2026-10-04` 均为非交易日，四 ticker 的 schedule 为 CLOSED；语义日以美东 02:00（当前对应 UTC 06:00）切换。**当前没有新实时 poll 是闭市窗口行为，不能直接推断 scheduler 死亡。** 闭市 sweep 的完成度应另看 durable receipt，不能仅看实时 poll 状态。

### 2.3 精确故障链

```text
10/02 16:35:31  Investing 列表仍成功
10/02 16:35:40  Barron's 列表仍成功
10/02 16:35:56  Playwright driver/node：约 2033MB 存活堆，GC 无法回收，fatal heap OOM
10/02 16:35:57  Barron's / InvestorsHub 等开始连续 RuntimeError
之后            四源按原调度继续失败，External driver 未恢复；Python API/readyz 仍成功
10/02 20:09:48  Reuters Managed 搜索仍有真实成功，证明不是所有浏览器模式同时死掉
10/02 21:00–10/03 06:00  Reuters 每小时约 598–600 个 403 访问结果
```

OOM 日志核心片段：

```text
2026-10-02T16:35:56.418Z Mark-Compact ... -> 2033.4 (2052.6) MB
2026-10-02T16:35:56.432Z FATAL ERROR: Ineffective mark-compacts near heap limit
Allocation failed - JavaScript heap out of memory
... /app/.venv/lib/python3.11/site-packages/playwright/driver/node
```

只读 CDP `/json/version`：Dow Jones 与 DIGITIMES NL 仍返回 `Chrome/153.0.8010.52`；目标总数分别 4 和 3，其中普通 page 2 和 1，没有大量存留页面的证据。Dow Jones instance 仍为 `0854db06758b49a68cdce663ce78e922`。DIGITIMES NL 当前 live instance 与 Registry 缓存不同，需在后续修复中同步真实实例信息；本轮没有足够证据确定这一次实例变化的时间和原因，也不把它作为四源集中失效的根因。

OOM 后仍有 Reuters FULL 正文结果（核查窗口内 57 个）和少量订阅权限失败。这进一步说明 Search、Body、Managed、External 必须分层观察；不能把“Reuters 源报错”解释为所有 Reuters 正文都不可访问。

## 3. 为什么会反复：历史案例与机制

依据 `changelog`、既有验收文档和实际源码。下表不把当时的短时恢复宣称为全天稳定性保证。

| 时段 / 案例 | 当时真实机制与修复 | 本次启示 |
| --- | --- | --- |
| 9/22–9/23 登录对照 | 普通正式 Chrome + CDP 可登录，Playwright 直接启动在强风控站点失败；引入双轨 Runtime | 保留 External，不能为了维护方便退回 Managed 或清 Profile |
| 9/24 身份持久记录兼容 | 历史 challenge 字段、lifecycle 字段严格反序列化失败 | Registry 兼容、部署基线验证属于确定性修复，不是风控 |
| 9/28 TrendForce ReadError | Site Access 已成功，内部 HTTP 响应读取瞬断；同 operation ID 一次有限重试 | 必须保留请求关联，区分 RPC 与网站网络 |
| 9/30 Barron's 长期 SiteAccessError | 固定节点 IP 观测轮换被误当成 egress generation 变化，Supervisor 拒绝身份复用 | 出口观测代际与配置代际应分离；不能频繁换 Profile 掩盖身份绑定错误 |
| 9/30 Barron's 假成功/慢加载 | 错采页脚链接、客户端卡片加载、日期稍后出现；限定真实卡片与精确发布时间 | DOM 适配与采集验收独立于网络状态；恢复不得放宽 30min admission |
| 9/30 IBKR RuntimeError | 构建用了开发 Dockerfile，缺少 ibapi；按 Dockerfile.v2 窄重建 | 官方接口也可能是本地依赖错；恢复镜像必须可重建 |
| 10/01 多源超时 | 跨 Identity 队头阻塞、取消漏 permit、无界 cleanup、预算分层不一致 | 这组修复仍在；现在补生命周期失效检测，不重复盲改 timeout |
| 10/01 Reuters ConnectTimeout | Bus 同步 SQLite/重复日历热路径拖慢事件循环；批量/按轮共享查询 | 本次实际 403 是另一机制，不能照搬上次解释 |
| 10/02 新源发布 | 广告页 DOMContentLoaded 迟延、InvestingPro 模板、HTTP 403 污染浏览器健康；专用等待与 Browser-only | 这些适配继续保留，但把三站压进一个串行 Identity 后出现容量争用 |
| DIGITIMES 多次正文问题 | 列表可读不等于英文付费正文有权限；旧 auth_state 不能证明当前 entitlement | 不把权限失败当 selector bug，也不要求模型绕过订阅 |
| 本次 | 长期 Node 控制进程 OOM；无恢复、假健康、错误信息丢失 | Chrome 身份长期存在与控制进程永久不换是两回事 |

共性不是“一个万能代理节点不够好”，而是**访问故障未正确分层、共享控制链路缺乏自愈、负载治理与身份连续性没有配套、健康与验收不准确**。

## 4. 根因对应的修复方案

### P0：External 控制驱动可恢复、可回收，Chrome 不动

改动位置：`site_strategy/external_runtime.py`、`browser_runtime.py`、`service.py`、`api.py`，少量 settings 与测试。保留统一 Runtime 接口，不拆成新浏览器微服务。

1. 给 External Runtime 增加单一受锁保护的 driver lifecycle：`STARTING / READY / DRAINING / RECOVERING / FAILED`、`driver_epoch`、启动时间、实际 Node PID/启动标识、失联原因、最后恢复记录。所有 first-start/recovery 单飞，禁止并发创建多个 driver。
2. Python 对象存在不是存活证明。监听连接断开；在已有 attachment 上有限时探测 Playwright 控制通道（例如 browser CDP session 的 `Browser.getVersion`，及时 detach），另外检查已登记 Node 进程是否仍存活。探测不导航、不启动新 Chrome、不在维护页注入 JS。
3. 驱动死亡：停止接收本轨新租约，记录旧 epoch；取消/收尾旧任务、只关闭任务拥有的 target、归还旧租约一次。停止失效 Playwright attachment，重新启动控制 driver，向 Supervisor 获取当前实例，再 attach 既有 Chrome，恢复接单。不得 `browser.close()`、`context.close()` 或 `supervisor.stop()` 来完成控制层重连。
4. 只重连某个失效 attachment 时仍保持同一 Profile/Egress；整个 External driver 死亡时统一恢复该轨，不能按四个网站重复恢复同一个 Node 进程。Managed 轨不受此全局恢复锁阻塞。
5. Chrome 存活、Profile 单 writer、instance/PID 不变是验收条件。人工维护中的 Identity 保留维护标记及页面；轮换不关闭维护窗口，不让 crawler 趁重连侵入人工会话。若现有 session 无法安全重绑定，先等待维护完成，而非直接释放维护锁。
6. 控制层增加有限寿命防线：默认运行 4 小时或 Node RSS 连续 3 次超过 1.2GiB 时，在安全空闲点做上述 detach/re-attach；每分钟采样。阈值是根据此次约 7小时48分钟/2GB fatal 的**初始工程值**，需按真实基线校准，不称为已证明的泄漏消除。无法 drain 时保持有界等待，记录 deferred，不强杀 Chrome。
7. 清理错误不阻止所有后续恢复。最多一次即时恢复、失败后有限退避；恢复失败单独标记轨道失效，由维护控制器处理，不让每次 poll 又重复两遍失效 driver 调用。

恢复 epoch 必须 fence 旧任务：迟到结果不能覆盖新健康状态、旧 lease 不得归还新 epoch 的额度。不用“把 semaphore 数字直接重置”代替正确回收。

先复现 `.stop()`/driver detach 不关闭现有 Chrome、维护页面与 Cookie，再实现轮换。本仓库已有 Site Access 重建而 Chrome 不重建的生产记录，是可行依据，不代替这一精确回归。

**不以提高 `--max-old-space-size` 为主修复。** 增大堆只能延迟故障并扩大资源影响；按观测释放资源和有限控制进程生命周期才是本轮必须交付的恢复机制。

### P1：追踪内存增长，修正健康与错误契约

1. diagnostics 增加 Managed/External 分轨状态、driver PID/epoch/RSS、attachment 数、owned page 数、CDP session 数、恢复次数/原因；不输出令牌、Cookie、完整认证 URL。
2. `/healthz` 表示 Python 存活；`/readyz` 必须识别已启用轨道失效，输出 degraded 明细。但访问 API 继续为健康轨道提供服务，不因为 External 失效拒绝所有 Managed/HTTP 请求。Docker unhealthy 本身不会自动重启服务，不能把 healthcheck 当完整 watchdog。
3. `classify_exception()` 对明确的 driver closed/disconnected/unavailable 优先返回 `RUNTIME_UNAVAILABLE`，再判网络 timeout。安全、稳定的 reason_code 保留底层语义，不仅返回类型名；详尽异常在脱敏日志中与 operation ID 关联。
4. 区分真实浏览器实例代际、driver epoch、Registry 写入 generation。`_prewarm_identities()` 仅在状态确实变化时写入；不能每分钟无条件 generation+1，当作 Chrome 重建计数或成功证明。
5. 在隔离环境复现生产列表/正文混合负载，记录 Python/Node/Chrome 三者内存。比较 page/CDP session/旧 attachment/response/监听器存活数量，尤其检查重连淘汰旧 entry 是否只关 raw CDP 而残留 Playwright attachment。修复已复现的未释放对象；没有复现不编造某类泄漏。
6. 目前浏览器路径主要是页面访问，不应未经核实把 APIRequestContext 缓存认定为根因；若以后引入此接口，响应体生命周期需显式释放。heap snapshot 仅在可丢弃测试进程中采集，不对生产发送调试信号，也不采集人工登录页面的敏感堆。

### P2：Reuters 拒绝页、fallback 与搜索成功语义

改动位置：`reuters_sources.py`、`runtime.py`、`service.py`、`health.py`、`news_adapters.py`、Message Bus `schema.py/service.py`。

1. Reuters recipe 的页面导航与状态/挑战检测进入统一访问层。遇到 401/403 时返回状态、脱敏页面检测结果与实际 HTML 给现有分类器，再决定是否解析搜索卡片；不要先抛只带 status 的 RuntimeError 丢掉拒绝页。
2. challenge、明确 block、登录、订阅与未知 403 分别处理。不能把所有 403 当账号过期，也不能根据 403 自动擦除 Cookie/auth_state。
3. 对持续 `AUTH_OR_ACCESS_UNKNOWN` 增加**该 Site、该用途、该组合**的有界失败证据：例如 15 分钟内至少 3 个不同真实操作持续拒绝后，进入临时拒绝状态并尝试已注册备用组合；一次未知 403 不永久封身份。网页内容确认 challenge 时使用现行 challenge/cooldown 流程。不用主站探测成功清掉 search 路径持续失败状态。
4. Reuters 主/备分别是 Managed NL、Managed DE，另有 direct 候选。先做实际 Search 及公开正文对照，按真实结果选主备。若人工验证证明 Managed 启动导致挑战不可通过，才迁移 External；不因为 Barron's 的实验结论直接迁移 Reuters，也不默认挤入已超负载的 NL 共享 Identity。
5. 搜索 PollResult 增加每个 query 的结果状态：`SUCCESS_WITH_ITEMS / SUCCESS_EMPTY / FAILED / DEFERRED`。独立保存真实查询成功数/失败数、operation ID、访问 disposition/category/reason、query_key；不要只保存 `SiteAccessError`。
6. 所有 query FAILED 时 poll 为 FAILED，保留旧 last_success_at、增加失败数；一部分成功一部分失败为 PARTIAL，增加独立 degraded 计数/起点，保留有效结果；真正成功但零新闻是成功，不能按消息数量判失败。全部 DEFERRED 不虚记成功也不算网站失败。
7. 无故障、无延期而仅分页未证明完整的 coverage PARTIAL/UNKNOWN 不等于访问失败；**不能简单把所有 PARTIAL 都改成 FAILED**。完整窗口覆盖与实际采集 I/O 健康分别记录。
8. 保留 60 秒目标间隔、最多 3 个 L1 概念、实时 30min 与闭市 sweep 窗口；fallback/风险治理不篡改这些业务配置，不使用旧新闻补发制造恢复证据。

### P3：共享 Identity 的可达公平性与容量修复

当前 DIGITIMES + InvestorsHub + Investing 共用 `digitimes-nl-1`，各站点单并发、Identity 单并发。真实历史事件已有大量 `site_queue_timeout`，即使 OOM 前成功请求也与队列失败交错。

1. `_JointWaiter` 保存本请求 queue deadline，使 crawler aging 在期限内可达，例如 `min(5s, queue_cap/2)` 后参与 BODY FIFO。LOGIN 仍优先；PROBE 仍低优先；不提高同 Profile 并发。
2. 为同 Identity 统计纯 service_ms、queue_ms、BODY/CRAWLER 到达率与完成率、超期原因；当前 ACCESS_RESULT 的 network_ms 包含服务总耗时，不能直接当纯浏览器执行时长计算利用率。
3. 适配单次实际页加载耗时设置有限 queue deadline，不盲目延长到几分钟。只有公平性不足但平均容量足够时，修改 aging 才能解决；持续利用率超过 1 必须分担负载。
4. 预期将两个公开市场源移至一个新的共享 External Identity（先按当前可用 NL 路线实测），让付费 DIGITIMES 保留原独立身份。Barron's/WSJ/MarketWatch 的同认证体系共享关系不变。这样只增加一个必要公开源身份，不每站新建一个 Chrome。
5. 建新 Identity 前核实 Supervisor 容量与实际资源；公开源使用新的独立 Profile，不复制 DIGITIMES 登录 Cookie。若既有容量没有余量，先确认无业务/维护占用的遗留测试身份，再制定单独容量变更；不自动删除未知 Chrome 或扩大内存上限。
6. Queue timeout、资源延期与网站拒绝分别上报；复用当前有限重试，不把负载延期当成节点风控，更不能让 maintenance worker 见队列错误就换节点。

### P4：单源适配与生产验收

| 来源 | 基础修复后必须补做的验收 | 不允许的“修复” |
| --- | --- | --- |
| Barron's | 主身份真实双标签卡片、精确发布时间；至少一篇有权限正文；主备分别验证，控制重连前后 instance 不变 | 清 Profile、把无日期卡片用首次发现发布、漏掉 Other Dow Jones/纳入 IBD |
| DIGITIMES English | More News 真实条目与至少一篇完整订阅正文；列表与 entitlement 分开报告 | 将摘要算全文、把旧 VALID 当新验证、绕付费墙 |
| InvestorsHub | 当前 ticker 真卡片；原生 iHub 与 ADVFN 最终域名正文；新身份真实测试 | 抓页脚/其他股票列表、只以主页 200 判恢复 |
| Investing | 对应 instrument URL；公开正文；Pro 单独准确返回权限结果 | 付费失败污染整个身份、updated 时间冒充 datePublished |
| Reuters | 每个 ticker 的已配置 L1 query 真搜索结果或真空结果；失败保留拒绝页分类；公开正文独立验收 | 所有 query 失败仍刷成功、未知 403 不受任何临时治理 |

账号权限和 challenge 当前没有做新的页面验证，故本轮不能给出“某站必须重新登录”的确定结论。修复控制层后再统一输出需要人工操作的身份/站点/原因/维护入口。

### P5：可重建发布与长期观测

生产使用每服务现行依赖基线 + 窄代码 overlay，包含正文专用补丁，不是一份仓库 HEAD 的统一镜像。发布记录必须列出：base image digest、变更 commit、文件清单及 SHA256、SDK/Playwright/Chrome 版本、Registry revision、Compose 最终 overlay、回滚 image digest。

当前 Message Bus SDK 是 0.144.4，初始化 Guardian 是 0.159.3；不要为了消息源修复顺便把所有业务服务 SDK 更新。代码范围需要保持窄，基线需完整保留。

完成后至少跨过本次约 8 小时的失效点，进行 24–48 小时观测；包含真实列表/正文混合、有限取消、driver 恢复/轮换、人工维护排他、闭市与下一实时窗口。验收分别统计采集成功、部分失败、排队延期、body FULL、权限/challenge、实时入库时效，不要求源在没有新闻时产生新消息。

旧故障期不自动补发到实时总线。需要审计缺失覆盖时另开历史任务，遵循原 admission/sweep receipt 语义。

## 5. 实施顺序与完成标准

1. 保存 OOM/访问事件证据，交付 reason_code、driver 分轨诊断；增加故障回归。
2. 实现 External driver 单飞恢复、有限寿命回收、epoch fencing；不重建 Chrome。
3. 修正 Reuters 拒绝页与 QueryOutcome/失败聚合，再补持续拒绝的用途级 fallback。
4. 修正 10秒/30秒 aging 冲突，实测容量后迁移两个公开源至一个必要身份。
5. 逐源列表 + 正文 + 自然 poll 验收；最后做长时间观测和可重建发布记录。

必须增加的测试：driver 异常退出后单次恢复；恢复失败有限退避；并发恢复只启动一次；旧 lease 不污染新 epoch；人工维护窗口不被关；Chrome instance/Cookie 不变；两轨互不阻塞；403 拒绝页完整分类；所有 query 失败不刷成功；真空搜索成功；部分成功保持有效新闻；DEFERRED 不算故障；短 queue aging 可达；原队头/取消/cleanup/正文延期回归；发布 manifest 不丢现有补丁。

本轮未执行上述开发，也未运行新修复测试。既有测试通过记录只用于追溯历史，不代表本方案已验收。

## 6. 证据索引与外部技术依据

- 历史记录：`docs/message-source-reliability-repair-20261001.md`、`docs/market-news-sources-20261002.md`、`changelog`；历史修复 `ced70346 / 392bd994`，新源 `3ef317a4 / 1f0ad0db`。
- 生命周期/健康：`site_strategy/external_runtime.py:start/_entry/close`、`runtime.py:PersistentBrowserPool.driver_ready`、`service.py:browser_driver_ready/_prewarm_identities/_attempt/classify_exception`。
- 预算：`site_strategy/budget.py:_JointWaiter.rank/JointBudget.permit`；生产 Registry crawler queue=10000ms。
- Reuters：`message_bus_v2/reuters_sources.py:capture_reuters_search`、`news_adapters.py:ReutersSiteSearchAdapter.poll/_poll_query`、`service.py:accept_poll_result`。
- 窗口：`persistent_runtime_v2/bus_orchestration.py`、`semantic_clock.py`；只读核查 `runtime_values` 日历与 schedule。
- 生产只读来源：Bus `poll_states/acquisition_failures`；Site `access_events/site_runtime/identity_runtime/body_outcomes`；管理员 diagnostics；Supervisor status/CDP JSON；带时间戳容器日志及资源状态。SQLite 查询使用 URI `mode=ro` 与 `query_only`，未实例化会迁移数据库的 repository。
- [Playwright CDP 接入官方文档](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)：CDP 与 Playwright 协议能力不同；1.63 已支持 `no_defaults`，可作为减少默认环境改动的单独回归项，不据此认定 OOM 或 Reuters 403 已找到根因。
- [Playwright APIResponse 官方文档](https://playwright.dev/python/docs/api/class-apiresponse#api-response-dispose)：API 请求响应的释放责任；仅作未来审计参考，不是本次已确认根因。

关联文件：[消息源维护 Codex Worker 可行性与开发方案](message_source_maintenance_codex_worker_feasibility_and_implementation_plan_20261004.md)。
