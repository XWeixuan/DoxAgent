# 企业 IR / Press Release 轻量采集开发方案

> 本文保留方案制定时的调查与实施合同；本轮实施和实际发布状态见[执行回执](corporate-ir-press-release-implementation-and-release-20261020.md)。下文“尚未实施”描述制定方案时的基线。

日期：2026-10-20，北京时间。范围：INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS。本文是实测后的开发方案，**尚未实施业务代码、注册来源、迁移订阅或部署**。

## 1. 结论与边界

采用 **一个外部 `corporate_ir_press_releases` SourceDefinition + 九个 ticker binding + 独立内部 endpoint 任务**。来源类型 `API`、采集模式 `by_ticker`；API 在现有 schema 中包括 RSS/公开 HTTP，不表示所有内部入口都是 JSON。无需新增 acquisition mode，也不走行业 relevance/model 分发。

本轮全部九家公司均找到可工作的无浏览器路径。首版正常路径是 RSS、JSON、SSR HTML；**零新增持久 Profile、零新增 Site Access identity、零常驻页面**。共享 headless Chromium 作为惰性启用的最后通道，整个池共用进程，每个内部源独立临时 Context，用完关闭。不是复用 Reuters/Barron's 等身份，也不是让所有公司共享 Cookie。

目标为每个有效内部入口每 30 秒开始一次检查，全天运行、包括非交易日。它是采集检查目标，**不是承诺公告发布后 30 秒内入流**：上游 CDN 缓存、网络、正文和投影都有延迟。保留现有消息发布时间准入；不把抓取时间变成发布时间。

单页超时、解析错误、403、429、正文失败均不得成为其他页面的轮询屏障。同一 ticker 的两个入口也独立运行、独立落库。共享浏览器进程仍是浏览器任务的共同故障域；崩溃时有界恢复，HTTP 任务继续。不能声称隔离 Context 能隔离浏览器进程崩溃或共同出口封禁。

不新增独立爬虫平台、消息总线、数据库、管理后台、常驻九浏览器、模型相关性判定、自动验证码或账号登录。

## 2. 本轮实际测试与证据

### 2.1 方法与环境

- Windows 本地：项目既有环境，`httpx` GET，解析 RSS/XML、SSR 和页面嵌入配置；固定测试 UA，未轮换身份或使用 stealth。
- 新加坡生产主机：独立标准库 `urllib` 公共 GET，明确 DIRECT、不继承环境代理。脚本从 SSH stdin 执行，不安装依赖、不改生产文件/配置/数据库。
- 最终默认12入口还在实际`doxagent-v2-v2-message-bus-1`容器内独立执行相同公共GET，全部200，约0.653–3.155秒；包括COHR两栏目、CEG JSON、SNOW最终HTML。它验证现行容器网络可达，不等于已接入daemon调度或长期稳定。
- 浏览器：本地新建的独立 headless Chromium；没有连接现有 Profile、登录会话、Site Access Worker 或人工 Challenge 页。
- 代表性公告正文：九 ticker、十一篇页面（COHR 两类、RKLB 两站），HTTP 可获得实际文章内容；通用抽取约 1,340–10,526 字符。它是可获取性证明，不等于现有 Hub 已把这些文章全部认证为 FULL。
- 短窗复测：北京时间 **19:09:28–19:10:29**，14 个候选通道、三轮、轮次起点相隔 30 秒，共 42 次 GET：28 次 200、14 次 304，无其他状态；单次最长约 1.159 秒。第二/三轮发送前一成功响应实际提供的 validator。
- 已另做异步调度机制小实验：一个任务持续超时，健康任务仍按短周期完成六次；这是设计原型，不能当最终业务实现回归。

本轮未等待真实新公告、未做长时 soak、未向实时消息流写测试新闻。地方/出口差异和候选 URL 猜测的失败均保留；不会只保留成功探针。

可复核材料位于 [eval/corporate_ir_recon_20261020](../eval/corporate_ir_recon_20261020/)：

| 文件 | 内容 |
| --- | --- |
| `probe.py`、`http-results.json`、`extra-results.json`、`discovery-results.json` | 本地初测、补充 RSS、JSON、正文与 robots 检查 |
| `sg_probe.py`、`sg-results.jsonl` | 新加坡主机初测及响应头/时间/长度/摘要 hash |
| `container_probe_generated.py`、`container-results.jsonl` | 最终12默认入口在实际Message Bus容器网络中的公共GET |
| `sg-details*.jsonl`、`sg-followup.jsonl` | 公共页面与候选 RSS/正文响应的原始取证传输；base64 不是脱敏机制 |
| `sg_cadence_generated.py`、`sg-cadence.jsonl` | 三轮 30 秒探针，含状态、validator、延迟、Age、响应字节数 |
| `browser-results.json`、`browser-resource.json` | 浏览器 403 对照与隔离 Context 资源样本 |
| `body-analysis.json`、`payload-analysis.txt` | 正文可获取性、日期异常及页面内公开配置定位 |
| `snow-followup.json`、`SNOW_adobe_robots.txt` | Adobe发布域排除规则及最终官网HTML条件请求304证据 |
| `scheduler_spike.py`、`scheduler-spike.json` | 独立 inflight、超时不阻塞健康任务的合成实验 |

eval 内是可再生成公共网页取证，包含完整 HTML/公开正文，不建议全部提交 Git。正式 fixture 只截取合同所需片段、结构与少量正文，不提交大体积整站快照或无关内嵌标识。

### 2.2 逐 ticker 的首版选择

| ticker / 内部 endpoint_id | 首选方法与入口 | 实测结果 / 解析合同 | 备用或覆盖注意 |
| --- | --- | --- | --- |
| INTC / `intc.press` | RSS，[原入口](https://www.intc.com/news-events/press-releases/rss) | 200，12 item，8,742B；guid/link、带时区 pubDate；正文 HTTP 可读 | RSS 无全文时仅为新增文章取详情；保留两位年份 RFC 日期解析测试 |
| MU / `mu.ir` | 新发现的官方 [IR RSS](https://investors.micron.com/rss/pressrelease.aspx) | 200，10 item，4,212B；后两轮 304；正文含有效 NewsArticle/datePublished | 不是用它替代所有主站内容 |
| MU / `mu.newsroom` | HTTP SSR，[用户原入口](https://www.micron.com/about/press/news) | 200，293,042B；12 个 Read article teaser；标题/描述日期/链接都在初始 HTML | 原列表有 Micron 主站内部文章，RSS 并非逐项覆盖；两入口都保留，规范 URL 去重 |
| BE / `be.press` | RSS，[原入口](https://investor.bloomenergy.com/rss/pressrelease.aspx) | 200，10 item，4,716B；后两轮 304；正文公开 HTTP 可读 | 不需要 Chrome；本轮用户已明确将 BE 纳入调查，旧轮次排除不适用于这项新任务 |
| COHR / `cohr.corporate` | HTTP SSR，[Corporate Releases](https://ir.coherent.com/news-events/corporate-releases) | 新加坡 DIRECT 200，95,455B，10 条；后两轮 304；article 内标题/日期/链接 | 独立于 financial；分页 `?page=1` 等；正文 JSON-LD 提供准确带时区 datePublished |
| COHR / `cohr.financial` | HTTP SSR，[Financial Releases](https://ir.coherent.com/news-events/financial-releases) | 新加坡 DIRECT 200，95,745B，10 条；后两轮 304 | 同域限制只覆盖请求，不跨整轮加锁；另一栏目失败不阻止本栏目 |
| NBIS / `nbis.press` | HTTP SSR，[Newsroom](https://nebius.com/newsroom) | 200，355,689B，17 个 `/newsroom/` 公告链接；后两轮 304 | 卡片明确标记 Press release；排除混入列表的 Blog，不引入模型；不绑定 `_next` buildId |
| CEG / `ceg.press` | 页面声明的 [JSON 列表](https://www.constellationenergy.com/content/constellationenergy/paths/newsroom.json?sortorder=desc&limit=6&offset=1) | 200，8,439B，`pagelist` 六条，`details.PageDetails`、filters；详情 JSON-LD 可提供精确时间 | 裸 URL 返回 500，必须带实际前端参数；offset 是页序号，第一页为 1；响应 Content-Type 竟为 text/html，按已知 JSON 合同解析 |
| RKLB / `rklb.ir` | 新发现 [官方 IR RSS](https://investors.rocketlabcorp.com/rss.xml) | 新加坡 DIRECT 200，10 item，106,847B，含文章 HTML description；第三轮 304；正文独立 HTTP 也可读 | 用户给的 [IR 列表](https://investors.rocketlabcorp.com/news) 200，62,175B，保留为停机补页/明确 fallback，不同时重复全量拉取 |
| RKLB / `rklb.updates` | HTTP SSR，[Updates](https://rocketlabcorp.com/updates/) | 200，67,543B，13 个 article 链接；标题、日期在卡片中 | 与 IR 不是完整相同集合，两路保留；同文使用经证实的 alias/正文证据去重，不能仅凭标题合并 |
| SNOW / `snow.press` | HTTP SSR，[原页](https://www.snowflake.com/en/news/press-releases/) | 200，508,834B，12个实际公告卡片（含外跳financial）；补充conditional请求304、0B | 从卡片/`__INITIAL_STATE__`抽取；发现的13,714B JSON只列技术候选，因下述robots约束不作默认；financial链接可跳investors.snowflake.com |
| ONDS / `onds.press` | RSS，[原入口](https://ir.ondas.com/rss) | 200，20 item，350,962B；content:encoded 自带正文 | 大 RSS 并非摘要，优先提取并验证原生全文；link 会 301 到 `/press-releases/detail/...`，保存 alias |

共 **12 个默认独立内部入口**，映射为九个 binding。MU、COHR、RKLB 各两个；其余各一个。输入原始清单是 11 个 URL，MU 增加 RSS 后为 12 个正常任务；RKLB IR RSS替换其 IR 列表的轮询方式，不丢该列表的覆盖职责。

Snowflake 的候选 JSON URL（从官网初始状态读取，没有账号、token 或私有 API key；**不是默认轮询地址**）：

```text
https://publish-p57963-e462109.adobeaemcloud.com/content/snowflake-site/global/en/news/press-releases/_jcr_content/root/responsivegrid/container/container/flexible_column_cont/flexible_column_content_container_1/filterable_resources.filter.json
```

该技术候选已验证返回首12条和总数，与官网首屏相同。但随后读取此Adobe发布域`robots.txt`得到`User-agent: * / Disallow: /`，官网robots也排除`/*/_jcr_content/`。因此不以换host方式绕过：**首版使用正常官网SSR页面+条件GET**。补充实测同一Last-Modified请求返回304/0B，未变化不重解析页面。页面内`__INITIAL_STATE__`可以直接读取，无需调用受排除的后台路径。分页/排序参数仍须从实际组件合同确认后固化fixture，不能宣称历史覆盖已通过。只有未来获得明确允许的API接入方式才重新评估此JSON候选。

### 2.3 为什么 COHR 不直接选新发现的 RSS

[COHR RSS](https://ir.coherent.com/rss.xml) 确实存在且已在新加坡验证：10 item，同时包含 corporate 与 financial 样本，提供时间和大段 HTML description；响应约 **637,317B**。本地这一域名的普通 HTTP及 headless 均返回 403，而新加坡指定 headers 的 DIRECT 请求成功。

本方案选择两个原栏目 HTTP 作为默认：首轮合计约 **191KB**，两路 validator 已连续返回 304，精确保留用户指定两个栏目的集合、独立状态和分页。RSS 包含部分不在两个栏目第一页的记录，不能只凭“有 RSS”宣称栏目过滤完全等价；它也比两页合计大三倍以上。新增文章才取详情，不反复补正文。

RSS 适配仍复用公共解析器，作为 COHR 的**可选替换配置**，不额外常规轮询。若后续确认业务接受所有官方 IR press release、且正文验证足够完整，可把 COHR 改为一个 RSS 内部源，减少请求数；这不是首版前置阻塞，也不需要另建架构。

### 2.4 不应忽略的反例

1. **403 不等于必须浏览器。** COHR/RKLB 本地 httpx 与全新 headless 都被拒；新加坡直连可读。同一主机标准库缺少 `Accept: */*` 的补充实验返回 403，补齐后成功。这是当时条件性对照，不足以把 Accept 认定为唯一根因。生产使用固定明确 headers 和路由，不自动轮换出口。
2. **CEG 500 是参数合同，不是浏览器渲染需求。** 参数来自页面 JS 的 `pressReleaseConfig` 和同一组件 fetch；修正后 JSON可读。
3. **JSON-LD 也会坏。** RKLB Updates 样本有 `2026-2-30TAD::Z` 等无效日期，必须拒绝；卡片真实日期可以 DATE，不能用 now 补精度。
4. **NBIS 午夜不等于真发布时间。** 样本 datePublished 为 `2026-10-01T00:00:00Z`，页面只显示日期；先按 DATE，保留原值与精度证据，不能把模板午夜当精确新闻时刻。dateModified 不代替 datePublished。
5. **RSS 不保证最小字节数或原生全文可信。** ONDS、COHR/RKLB 都可能带长正文；必须按已知字段验证，不允许任意 description 直接标 FULL。
6. **无新闻不是失败。** 本轮样本多为过去文章；真实实时准入会拒绝旧闻，这是正确行为。不能把本轮探针写入生产来“验收发布量”。

## 3. 与现有系统的接入位置

| 现有模块 | 已有能力 / 必须改变之处 |
| --- | --- |
| `message_bus_v2/schema.py` | `SourceDefinition`、唯一 ticker/source binding、source_parameters、30 秒 PollingConfig 均已可表达；保留现有枚举 |
| `adapters.py` 的 `NewswireRSSAdapter` | 目前按 rss_urls 串行拉取；不持久化 HTTP validator，不能直接承担内部页隔离与独立调度 |
| `scheduler.py` | `_poll` 每 binding 单次 PollResult；单靠在 adapter 内 gather 多页，仍会把快页交付/下一轮绑到慢页；普通 `_run_admitted_once` 还有整轮 gather |
| `persistent_runtime_v2/bus_orchestration.py` | 生产已有 per-binding inflight；但 IR 需要 per-endpoint，且现有 realtime 受 semantic calendar 控制，不能直接宣称周末 30 秒 |
| `service.py` 的 `accept_poll_result` | 目前同时处理内容和写 binding poll_state；多 endpoint 不能各写一次完整 binding state，否则覆盖 checkpoint、失败与健康聚合 |
| `content_enrichment/service.py`、`native.py` | 现有 Hub 租约/outbox/版本可复用；native_article 当前仅接纳 IBKR/Benzinga，IR RSS全文不能只改 metadata 就绕过 |
| Site Access `scope.py`、runtime、Identity Worker | 对高风控来源保持不动；IR 默认不注册 scope/Profile，不走 lazy publisher provision 来创建九套身份 |
| `v2_read/`、`api_v2/bus.py`、前端 source settings | 保留单外部来源，增加必要内部状态明细；UI 不从最后一个成功入口推断整个 binding 健康 |

“同一个消息源身份”在这里是外部业务来源 ID，**不是**把 SourceDefinition 和 BrowserIdentity 合成一个对象。原来的来源隔离原则继续适用于高风控轨；本任务授权增加独立、无认证、临时 Context 的轻量轨。

## 4. 数据合同：一项配置扩展、一张状态表

### 4.1 配置

新增 `message_bus_v2/corporate_ir/schema.py`、`catalog.py`：

```text
IrEndpointSpec:
  endpoint_id, owner_ticker, display_name, entry_url
  method: rss | json | html | browser
  parser_ref, parser_parameters, allowed_article_hosts
  fallback_methods/URLs（仅已验证的路径）
  date_timezone, time_precision_policy
  enabled, target_interval_seconds=30, revision
```

使用既有 `SourceDefinition.default_parameters.endpoints` 保存完整 typed catalog，经原 SourceDefinition 版本控制与 Owner service 修改；`catalog.py` 只提供种子，不在每次 bootstrap 覆盖人工修改。无需新增配置表或独立配置中心。

每 ticker binding 的参数只需：

```json
{"endpoint_ids": ["cohr.corporate", "cohr.financial"]}
```

Owner 校验 ID 存在、属于本 ticker、无重复、URL/方法组合有对应 parser；不用开放任意 URL 输入。新 ticker 没有 catalog 时显示未配置，不猜域名、不复制其他公司的入口。一个 ticker 至少一个有效 endpoint；某个 endpoint 的停用不删除其历史。

统一外部源种子：`corporate_ir_press_releases`、display name“企业 IR / Press Releases”、`builtin:corporate_ir@1`、`kind=API`、`by_ticker`、30 秒、即时发布。不要把它无条件加到全部未来 ticker 默认模板；先显式配置本次九个 binding。

### 4.2 持久状态

Bus Owner DB 新增一张 `ir_endpoint_states`，主键 `(binding_id, endpoint_id)`：

- `config_revision`、`bootstrap_complete`、`checkpoint_json`（近期 item ID/分页锚点，有界）；
- `etag`、`last_modified`、`representation_hash`、`validator_config_hash`；
- `next_due_at`、`next_dispatch_at`、`last_attempt_at`、`last_success_at`、`last_change_at`；
- `status`、`consecutive_failures`、`last_error_code/message`、`latency_ms`、`http_status`、`bytes_received`；
- `method_used`、`publication_time_basis` 摘要、`updated_at`。

validator 绑定 URL+parser/config revision+固定表示相关请求头；配置变化清该入口 validator，不清别人的 cursor。收到 304 仅表示检查成功；不创建文章、不重置新消息时间。304 的内容基线必须已成功解析并 durable 入队，不能把只下载成功的 representation 当已消费。

一进程一个 Bus Owner，复用现有 Owner 锁/StorageLane，不为首版另造分布式租约与 Redis。进程内 inflight 不持久化；重启后按 durable next_due 重派，未确认内容通过现有入队幂等性消重。

### 4.3 消息证据与去重

沿用 RawMessageInput、enrichment job、ticker stream；不新增第二条投递通道。metadata 增加 `ir_endpoint_id`、`ir_endpoint_revision`、`acquisition_method`、`fetch_url/final_url`、`time_origin`、原始日期与提取字段路径、catalog revision。展示仍提供真实公司/最终发布者；不要把外部来源名称覆盖成用户看不懂的内部任务 ID。

article identity **不能**加 endpoint_id：同文在 MU两入口、COHR两栏目、RKLB两站出现应可合并。保留 existing exact URL/已验证 redirect alias/正文业务版本规则；跨域只凭相似标题不合并。网络 URL 的 redirect alias 可以由实际重定向证明，RSS guid 单独保留。

首次初始化扫描与断网恢复仍执行现有 bootstrap/admission 合同。DATE 使用配置时区的日期容器和 `publication_time_basis=DATE`，显示不伪造时分；EXACT 才适用精确实时窗口。不能把 HTTP Last-Modified、RSS channel更新时间或 first_seen 当文章发布时间。

## 5. 调度与故障隔离：实现的核心

新增 `corporate_ir/coordinator.py`；仍在 `v2-message-bus` 进程内运行，不新增常驻服务。

1. `scheduler._run_admitted_once` 在进入现有 production/standalone 分支之前 tick IR coordinator；每 tick 只收割 done、登记到期任务，立即返回，不等全部完成。
2. coordinator 从当前 source/binding/endpoint catalog 取得有效 roster。普通 by_ticker 分支显式排除该 adapter；保证一次入口只有一个调度 owner。
3. inflight key 使用 `(binding_id, endpoint_id, mode/window)`；realtime 同一 endpoint 只允许一项。同 ticker 内两页互不加整轮锁。
4. 固定 30 秒 due 网格，按 endpoint hash 加 0–3 秒一次性相位；每轮从原 due 推进，不用“完成后再睡30秒”，不追赶积压发 burst。
5. HTTP 与 browser 两个独立队列/并发限制。HTTP 默认总并发6、同host请求并发2；浏览器默认2 Context。同域最小请求间隔起点0.5秒，只持有到单次请求结束。
6. HTTP connect 3秒、read 8秒、pool 1秒、整个 list job 12秒；浏览器单次 job 20秒。短超时不在同轮无限 retry，归还槽位后按自己的 next_dispatch 再试。
7. 403/429/传输错误/结构改变分别记录；429 按 Retry-After 延后该 endpoint。普通临时错误按30/60/120秒，持续错误最多300秒；成功恢复30秒。连续403或明确Challenge不得每轮升级新浏览器/换出口。
8. 成功入口立即持久化结果并入正文队列，不等 sibling。正文工作也不占 listing 槽位；慢正文不得阻止该入口下一次列表检查。
9. Endpoint 单独写状态；binding health由当前全部有效 endpoint 投影：全好SUCCEEDED、部分好PARTIAL、全失败FAILED、未检查NEVER_POLLED。部分失败保留具体端点原因；`last_success_at`表示当前全部所需入口的共同成功水位，按各入口last_success的最小值计算，任一未完成首次检查则为空，失败入口不会被健康入口推进。另有last_attempt最大值和每入口的due/延期，不能混用。过期未派发也不能长期借旧SUCCEEDED显示健康。
10. source关闭、binding暂停、endpoint删订阅或revision变化：取消该任务、等待 admitted同步提交收尾再关client；取消不是“线程已停止”。结果提交前复核版本/绑定，不把旧配置结果发给已取消订阅。

**不能直接并发调用现有完整 `accept_poll_result`。** 重构它为可复用内容接纳段与现有binding状态段；原所有来源保持原入口行为。新增 `accept_ir_endpoint_result`：同一StorageLane事务中接纳/幂等入队、推进该endpoint checkpoint/validator并重算binding聚合。失败不推进内容游标；cursor推进前必须完成durable入队。

断电发生在事务前则下轮重读；事务后正文作业已有持久责任。不要另加“HTTP成功后就记etag”的独立提前提交，避免下一轮304把未入队的文章永久跳过。

### 5.1 7×24 与 CLOSED_SWEEP

IR listing调度独立于交易所session日判断，但继续遵守source/binding启停和ticker all-pause。下游交易/研究执行的既有日历资格不改。

现有SOURCE_SWEEP roster仍包含此by_ticker来源，不能悄悄漏掉历史责任。为该adapter增加显式sweep入口：按冻结binding/catalog/admission window分出内部endpoint子任务，通过同一coordinator执行；各自独立分页cursor，完成后才汇总该SOURCE_SWEEP回执。实时与sweep共享请求host限额，同一endpoint的网络工作互斥，实时优先；某个失败sweep子项不阻止其他endpoint实时任务。

为避免大历史扫描长期占用入口，每个sweep分页任务至多一个12秒budget/一个分页chunk，然后释放槽位，cursor入sweep子回执，下轮继续。RSS窗口不足必须走该站已验证历史列表分页，未实现分页的recipe返回PARTIAL/coverage gap，不能标完整。复用RuntimeJournal父SOURCE_SWEEP的outputs记录子进度，不另建一套任务系统；首版必须测停机跨RSS窗口的不足和恢复结果。

## 6. HTTP、解析、正文与浏览器的执行细节

### 6.1 HTTP与parser

- 独立公共IR AsyncClient连接池，固定UA/Accept/Accept-Language，DIRECT、`trust_env=False`；不借新闻源代理或Profile。新加坡主机及实际Message Bus容器网络探针已通；实施时仍核对有效部署配置。
- 启用客户端正常支持的gzip/deflate/br压缩与keepalive；本轮标准库字节数为**未压缩实体样本**，不是线上wire带宽。
- 有validator才发conditional GET；不先HEAD再GET；Content-Length/标题不足以作为变更依据。无validator的200先hash已成功接纳的表示，未变则不反复DOM解析和补正文。
- 流式读取、解压后响应上限列表4MiB、正文4MiB，异常只失败当前入口。上限覆盖本次最大637KB RSS，避免每个response无限读；不抓图片、字体、视频。
- JSON针对已批准endpoint用结构校验，允许CEG错误Content-Type；HTML200登录壳、拒绝页、空容器必须拒绝。真正零条必须有该recipe明确空列表证据，不把parser没找到当正常空。
- SSR selectors：MU `.cmp-teaser`内的Read article、title、description日期；COHR `article.node--type-nir-news`和`.nir-widget--news--headline`；NBIS卡片类型Press release+title+日期+href；RKLB Updates `article.blog__article-container`/`.blog__article-date`/`.blog__article-title`。
- CEG读取`pagelist[].path/title/date/description`与`details.PageDetails`；SNOW从HTML内嵌状态读取`initialHits.hits[].button.buttonLink.url/title.lines/publicationDate`及其总数，或等价SSR卡片，保留外跳IR URL。不调用上文排除的后台JSON地址。
- 只对新ID/内容版本拉详情；头页覆盖到旧锚点才可以停止。发现上次锚点掉出头页、停机过久或分页不完整就记录coverage并有界补页；不每30秒全历史扫描。

### 6.2 正文不能绕回重身份路径

新增 `corporate_ir/body.py` 公共正文reader，供现有ContentEnrichmentHub按此source/endpoint recipe使用：RSS已证实全文优先，否则直接HTTP详情，按站点正文范围抽取，保留表格/项目符号/必要免责声明，剔除导航与订阅表单。CEG通用抽取样本混入末尾告警订阅文案，不能原样当最终质量标准。

同一队列/lease/renew/finalize/outbox继续使用；IR HTTP正文不先走SiteAccess.resolve，从而避免“列表没Profile、正文却自动创建九个publisher身份”。外跳限于catalog声明的公开IR host及实际发布者，跨站重新选公开reader；遇到付费/认证页报正文不可用，不借别的新闻源Cookie。

`native.py`扩展只接受此受控source和已知RSS recipe的`content:encoded`/完整HTML description，保留字段hash、原始HTML与标准化正文对应证明。防截断/Read more/空壳/墙页/标题一致性测试必须通过；不能仅因body>120字符或metadata标了provider就授予FULL。INTC/BE/MU的空description必须取详情。

执行版本要明确升级/兼容：待处理IR作业冻结source/endpoint recipe revision；源升级不偷偷改变已冻结job。其他body_v2.1/v2.2策略不回归，Hub旧任务能继续消费。正文失败留在原有重试责任内，不降级成伪FULL；列表健康与正文健康分开显示。

### 6.3 惰性共享浏览器

新增小型 `corporate_ir/browser_pool.py`，正常无任务时不启动。一个受该模块拥有的headless Chromium+Playwright控制器；每次job创建新Context和page，`domcontentloaded`后等待确定的业务selector，不等networkidle。任务结束/异常/取消均关闭自己的Context；没有persistent user_data_dir，也不保存cookie/storage_state。

仅method明确配置browser或已验证的JS渲染fallback时使用；不能把403/429自动解释为“需要Chrome”。不引入stealth、账号、验证码处理和新闻源External Chrome Supervisor。默认并发2、排队超时2秒，过期任务丢弃并记录DEFERRED；HTTP队列完全独立。空闲60秒关闭browser，下一次再按需冷启动。

Context close使用有界清理；浏览器失去响应时只终止**本pool创建的进程树**，当前browser任务重试不超过一次并带generation栅栏，排队命令不能写入旧closed pipe。绝不按进程名清全机Chrome。一个页面故障不重启整个pool；仅证明browser/driver已失活才升级。UI只暴露该endpoint的失败，不为临时Context创建人工维护会话。

本轮没有任何默认endpoint需要此轨；保留必要的最小fallback能力，不预置九套browser配置/冷备或新认证状态机。

## 7. 资源与时效预算

### 7.1 实测与估算分开

- 十二个调度槽的候选组合首轮未压缩实体总量约 **1,405,106B**；第二轮849,289B、第三轮742,442B。此组合SNOW使用实验JSON，**不是最终默认组合**；最终改用官网HTML，首轮实体量约1,900,226B，且另测HTML conditional可返回304。304有实际效果，未变化文章不再取正文。
- 默认每30秒十二个请求，约0.4请求/秒；若沿用候选组合第三轮实体量外推约 **2.14GB/日十进制未压缩实体**，仅是量级参考，不能当最终选型带宽。SNOW切换HTML后变化轮体积增加、304轮体积下降；实际取决于变化率/validator。压缩可能降低、站点变化可能提高；不要称“零带宽”。
- 本地Windows浏览器资源样本（含该测试的Node控制器，非Linux生产RSS）：无页已launch约211MiB；两个Context加载后约686MiB；关闭Context后约259MiB。browser.close后仍有Playwright Node约122MiB，随Playwright作用域退出释放。这说明只关page并不等于进程资源归零。
- 测试两个真实页在DOMContentLoaded前已发起42/31个请求；HTTP每列表一次请求即可取得业务数据。未量化CPU长期曲线，不能编造CPU节省百分比或九身份内存总和。

### 7.2 首版可调整默认值

HTTP全局并发6/每host2，列表总deadline12秒；浏览器2 Context/20秒；公开详情并发4，复用Hub现有持久队列。只缓存validator/hash/近期ID及必要待处理正文，不持久保存每30秒完整HTML。state每endpoint一行覆盖更新，不追加无界成功日志；运行指标按现有retention归属处理，禁止新建高频无限event明细。

正常状态30秒target、无重叠轮询。验收看start delay/p95、HTTP耗时、304比例、bytes、last_change与新文章first_seen→body→stream→UI延迟；分别报告上游Age/缓存语义。

已观测：MU主站`max-age=21600`；BE RSS `max-age=120`并允许stale；SNOW原页`max-age=900`；RKLB Updates `max-age=300`但Age超过155,000秒。**Age是缓存对象驻留证据，不是新闻必然晚155,000秒的证明**；仍提示应核实更新/重验证行为。不要通过随机query每30秒强制cache bust。JSON路径和conditional能降低成本，不能保证发布源及时更新。

## 8. 开发实施清单与完成条件

按下表顺序实施；这是一项可分批交付的开发任务，不需要先完成所有未来自动维护能力。

| 步骤 | 文件 / 动作 | 完成条件 |
| --- | --- | --- |
| 1. 固化合同 | 新建`message_bus_v2/corporate_ir/{schema,catalog,parsers}.py`；从本轮取证裁剪fixtures | 12endpoint全部可从fixture给出stable key/title/url/time evidence；含RSS完整/摘要、JSON、SSR、空列表与403/500反例 |
| 2. 持久状态 | `message_bus_v2/repository.py`建`ir_endpoint_states`及原子接纳函数；typed state放新模块 | 旧库幂等升级；两endpoint并发不覆盖；配置变更只失效自己的validator；崩溃点不丢文章 |
| 3. 公共transport | `corporate_ir/http.py`、parser registry，增加统一adapter注册 | async共享pool、conditional、12秒deadline、路由及限额；真实200/304均有用例；不要改全局http client |
| 4. 拆内容接纳 | `message_bus_v2/service.py`抽取内容处理段，增加endpoint事务入口 | 老来源原有状态/准入/去重结果一致；IR一次成功不会冲掉sibling失败 |
| 5. 独立调度 | `corporate_ir/coordinator.py`、`scheduler.py`、`persistent_runtime_v2/bus_orchestration.py`、factory close/start wiring | standalone/production两路径都只有一个owner；IR7×24；per-endpoint无gather屏障；sweep/realtime分开cursor且共享限额 |
| 6. 正文轨 | `corporate_ir/body.py`、`content_enrichment/{service,native,extractor}.py`必要窄接线 | ONDS完整feed不重复取详情；空description走公共详情；无Site scope自动provision；墙页/表单不可FULL；job版本冻结 |
| 7. 最小browser fallback | `corporate_ir/browser_pool.py`、adapter.close | 0请求时无browser；2Context互不串cookie；单页timeout/cancel释放；driver死亡只恢复本pool；HTTP无影响 |
| 8. 来源种子与订阅工具 | `manifests.py`、`adapters.py`、新增`scripts/ops/configure_corporate_ir_sources.py` | dry-run列出唯一外部源、9binding/12endpoint及revision；apply走Owner，不直接写生产SQLite；幂等不覆盖人工配置 |
| 9. 状态/API/UI | `v2_read/`对应native/projector、`api_v2/bus.py`、`frontend/v2/src/pages/source-settings.tsx`及shared wire schema | 新endpoint表纳入native增量与source_snapshot重建，两路径水位一致；外层一个来源/每ticker一个binding，展开显示入口/方法/状态/时间/延期；PARTIAL不会被最后成功掩盖 |
| 10. 回归与窄上线 | 相关tests、changelog、canonical release文档 | 本地通过后按授权构建Bus+Enrichment及实际API/Read/UI消费者；不重启Supervisor/现有新闻Chrome；自然验收分级记录 |

API沿用原source/binding配置接口，参数中表达endpoint_ids；只增加typed校验和read状态字段。第一版无需endpoint单独CRUD后台、任意parser上传功能或复杂路由编辑器。frontend schema生成/typecheck按既有仓库流程执行。

### 8.1 必须覆盖的机制回归

建议新增 `tests/test_corporate_ir_parsers.py`、`test_corporate_ir_scheduler.py`、`test_corporate_ir_repository.py`、`test_corporate_ir_body.py`、`test_corporate_ir_browser_pool.py`，按行为组织，不写只镜像实现的断言。

- 慢COHR corporate、正常financial和其他ticker同时运行：健康页按时继续，结果立即入队，外层COHR为PARTIAL。
- slow正文与listing分离；HTTP六槽、browser两槽互不占用；单endpoint不得排出无界待办。
- 200→解析失败→下一轮仍200；200→durable enqueue提交→304成功；无已消费baseline的304拒绝；URL/revision变化清validator。
- 入队/游标事务前后杀进程恢复；取消等待StorageLane admitted commit再释放；不消费已取消binding结果。
- RSS爆量/锚点掉出头页、分页缺页、年份切换、空feed；coverage不得被强行写COMPLETE。
- RKLB坏JSON-LD、NBIS午夜占位、DATE跨纽约日界/DST、未来/过期EXACT；dateModified不重置发布时间。
- 两入口同URL、一跳301/alias、跨域同标题不同文章、正文更新版本；不按endpoint制造重复。
- ONDS content:encoded完整；RSS teaser/read more/登录页拒绝；CEG表单清理；IR直接正文不创建Site identity。
- 7×24包含周末/假日；ticker暂停/恢复；CLOSED_SWEEP冻结catalog、独立分页cursor与真实coverage。
- 浏览器单Context挂起、取消、driver崩溃、旧generation命令与close失败；非本pool进程不受影响。
- binding聚合watermark、内部状态投影、HTTP304不改last_change、UI读水位滞后仍可识别。

可复用现有`test_message_admission.py`、`test_message_bus_storage_lane.py`、`test_content_enrichment_hub.py`、去重与BusOrchestration对应测试。老新闻源兼容是必要回归，不运行无关模型付费评估。

## 9. 注册、迁移、发布与验收

1. **迁移前只读审计**：枚举九ticker当前binding及自定义rss_urls，对比标准化feed/原始页面。列出已有BE/INTC等官方IR绑定与本次新endpoint是否重叠，区分其他官方IR栏目，不能停用整个通用newswire_rss来源来去重。
2. **Owner dry-run/apply**：工具默认dry-run；输出source/binding原revision、目标endpoint_ids、重复项及精确拟变更。获实施授权后走Owner service CAS，新增统一源与九binding，保留原配置回执。旧重叠入口只从其具体binding参数移出或停用确切旧binding，保留非重叠feed与其他ticker。
3. **受控初启**：先验证全部12入口的列表/代表性正文/内部状态，再分开验证首次bootstrap不倾倒旧文章、真实订阅ticker入流。测试旧公告使用dry-run/隔离库，不写实时流。配置双开时先以影子/不投递方式比较覆盖，正式切换在Owner操作中完成，避免故意制造重复窗口。
4. **窄部署**：使用`scripts/ops/storage_release.py`精确组件构建/发布；保存current/previous、SHA/文件hash、有效配置revision。不升级Chrome Supervisor、不动旧Profile、不启用source_maintenance Worker。
5. **自然运行**：至少30分钟验证目标30秒start delay、各endpoint成功/延期/失败、正文及投影；真实新公告出现后验证一次完整first_seen→body→dedup→stream→UI。缺新样本则该项明确待验证，不造公告。随后24小时资源/错误趋势观察另报告。
6. **回滚**：恢复前一组件镜像和已保存binding配置revision；新表向后兼容保留，不删历史/正文作业；在途IR job有明确消费者版本。回滚不得重新启用所有旧重复feed，也不清日志/outbox制造健康。

当前不需要用户维护账号、登录或解验证码。若后续特定IR明确要求认证/交互Challenge，记录为该endpoint不适合临时公共Context的证据，单独决定其专用身份例外，不能拖整个IR源进入人工阻塞。

## 10. 本轮完成与尚未证明

已完成：逐URL HTTP调查、新加坡DIRECT对照、最终12入口实际Bus容器GET、额外RSS发现、CEG/SNOW原生JSON定位及SNOW最终SSR选择、代表性正文可获取性、30秒三轮conditional复测、独立headless拒绝对照、资源取样和调度隔离机制原型。

尚未证明：实际Bus容器网络下长期表现、线上API/Read/UI集成、完整分页/历史补齐、生产新公告端到端时效、真正的最终IR专用FULL质量合同、Linux浏览器常态内存/CPU及长时资源曲线。这些在上面实施与验收步骤中逐项落实，不阻止按已验证公共HTTP路径启动开发。最后一次本地源码核对HEAD已由交接时`b630141e`推进到`62163de4`；并行会话改动不回退，最终实施前重新核对依赖diff。

优先实施顺序：**合同/状态/调度 → HTTP/RSS/JSON与正文 → 外部单源配置及状态展示 → 惰性browser兜底 → 窄上线及自然验收**。正常路径无需等待九个浏览器身份准备，也无需重构现有高风控新闻访问体系。
