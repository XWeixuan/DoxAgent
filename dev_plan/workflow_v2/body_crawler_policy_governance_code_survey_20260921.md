# 正文补全与新消息爬虫统一治理：代码勘察发现

日期：2026-09-21  
勘察基线：本地与新加坡远端均为 `main@d7d1c24c`；本轮只读检查代码和生产运行形态，不修改业务实现、不写生产数据库、不触发抓取重放。

## 1. 结论摘要

当前系统已经具备三块可复用基础：

1. Message Bus V2 有持久化的消息源注册、版本、默认 Profile、ticker binding、调度和 adapter 替换能力；
2. Crawler Plane 有可版本化、认证、上线和回滚的爬虫包；
3. `body_v2.1` 有持久队列、通用正文识别、若干站点专项识别、认证浏览器优先、浏览器渲染和 reader fallback。

但这三块目前没有形成“以消息网站/域名为治理单位”的共同控制面。现状是：

- 出口配置仍是单个全局 `DOXAGENT_CRAWLER_EGRESS_PROXY_URL`，不是可持久化、可按域名选择的 egress registry；
- 正文 worker 和 message-bus worker 是两个进程，各自持有浏览器/HTTP client、冷却和 fallback 状态，没有共享的组合选择状态；
- 正文站点适配写死在 Python 分支和选择器中，不能由 Registry 替换；
- 新消息爬虫 adapter 可以被 Registry 替换，但其治理键是 `source_id`/`crawler_id`，不是正文 URL 实际所属的 publisher/domain；
- Browser Profile、Proxy Egress、站点爬虫策略、正文策略、凭据引用没有被建模为一个原子组合；
- 429 处理存在多套进程内机制，没有“组合失败后整体切换到下一个组合”的持久状态机。

因此，用户提出的四项优化不是对现有 Message Bus `SourceDefinition` 的字段补丁，而应增加一个**独立但只服务于消息采集与正文补全的 Site Policy Registry**，并让两个进程通过它解析同一个站点、同一个组合和同一个健康状态。现有消息源配置仍应保留为“采什么、多久采、用哪个 adapter”的控制面。

## 2. 两个重点问题的直接答案

### 2.1 当前正文补全是通用策略还是按网站适配？

答案：**两者并存，但治理方式以通用管线为主、站点适配以代码硬编码为主，不存在可注册的站点正文策略。**

通用部分包括：

- 通用 `article` / `itemprop=articleBody` / 常见 article class DOM 候选；
- JSON-LD `articleBody`；
- `trafilatura` 通用抽取；
- 标题相似度、正文长度、句子数量、截断/订阅墙/挑战页判定；
- direct HTTP → browser render → Jina Reader 的条件式 fallback；
- 原文链接最多 3 hop 跟随；
- 默认域名并发/节奏 profile。

当前代码中已经存在的专项适配如下：

| 网站/类型 | 当前专项逻辑 | 位置 |
| --- | --- | --- |
| Reuters | `ArticleBody` 下编号 paragraph 范围 | `content_enrichment/quality.py:300-308` |
| WSJ | paywall 正文容器及 paragraph/h2/h3 | `content_enrichment/quality.py:241-245,323-326` |
| Barron's | live coverage card 精确范围 | `content_enrichment/quality.py:309-322` |
| Seeking Alpha | content-container、文章 ID/原始标题、隐藏正文检测、有限登录恢复 | `quality.py:207-210,236-240,328-346`、`browser.py:258-307` |
| TheStreet | 作者区截断 | `content_enrichment/quality.py:102-147,278-293` |
| Finnhub 转发页 | meta/script publisher redirect 识别 | `content_enrichment/quality.py:211-226` |
| CNBC 视频 | 官方 VTT caption 及覆盖校验 | `content_enrichment/captions.py` |
| 24/7 Wall St. |公开 WordPress API | `content_enrichment/publishers.py:37-80` |
| Fool / Chartmill / Benzinga Reader | 特定 reader 模板起点与身份校验 | `content_enrichment/quality.py:375-473` |
| IBKR / Benzinga 原生正文 | provider 原生 article body 严格匹配、零网络补抓 | `content_enrichment/native.py` |

专项逻辑均由 hostname/path 的 `if` 分支触发。未命中专项逻辑的网站使用通用 DOM/JSON-LD/trafilatura/reader 策略。`disabled_hosts` 也只是环境变量中的精确 hostname 集合，不是策略注册。

### 2.2 登录态目前如何持久化？

答案：正文补全和消息爬虫存在两套不同的浏览器持久化方式；当前生产配置下，它们**连接同一个 CDP 地址，但并不使用同一个 Browser Context/Profile+Proxy 组合**。

#### 正文补全 `PublisherBrowser`

- 配置 CDP 时：连接 operator 管理的 Chrome，认证域名使用 `browser.contexts[0]`。实际 cookie/localStorage/profile 由外部 Chrome 的 `--user-data-dir` 持久化；正文 worker 只新建/关闭 page，不拥有该浏览器生命周期。
- 未配置 CDP、且域名在 `authenticated_hosts`、且配置 `identity_dir` 时：按精确 hostname 使用 `identity_dir/<host>/profile` 调用 `launch_persistent_context`，是每 host 一个持久 profile。
- 未认证域名：使用普通 browser 的临时 context；如果 CDP 与 proxy 同时存在，会在 CDP browser 上创建带 proxy 的新临时 context。
- `identity_dir/<host>/status.json` 只保存 `auth_state`、`session_revision`、检查时间和 `credential_ref=host`，不保存 cookie，也不是凭据仓库。
- 登录入口 `content_enrichment.cli login` 要求操作员在可见浏览器中正常完成；代码不会输入密码、处理 MFA 或自动解 CAPTCHA。

#### Crawler Plane `PlaywrightBrowserRuntime`

- 整个 message-bus 进程只有一个 browser runtime 和一个缓存 context，不按网站拆分。
- CDP 且无 proxy：复用 CDP 默认持久 context。
- CDP 且有 proxy：调用 `browser.new_context(proxy=...)`，这是新的临时 context；不会复用默认 profile 的登录态。
- 无 CDP但有 `identity_dir`：在该单一路径上启动一个全局 `launch_persistent_context`，不是每站点一个 profile。
- CDP 不可用且已配置 proxy：自动启动一个无保存身份的 headless Chromium 作为公开爬虫 fallback。

#### 当前生产实例

2026-09-21 只读快照显示：

- `v2-content-enrichment` 和 `v2-message-bus` 均运行 3 天，proxy sidecar `doxagent-egress-clash` 运行 4 天；
- 两个 worker 均配置 CDP `http://172.17.0.1:19223` 和同一个已配置但已脱敏的全局 proxy URL；
- 正文认证域名为 Reuters、Barron's、WSJ、Seeking Alpha、MarketWatch 的裸域名与 `www` hostname；
- Crawler Plane 还配置了 `/data/browser-identities/reuters`，但 CDP+proxy 分支优先创建临时 proxy context，因此这个目录在该生产分支中不承担 Yahoo/Reuters browser context 的身份持久化；
- operator Chrome unit 明确使用 `/home/doxagent-desktop/.config/doxagent/reuters-chrome` 作为 `--user-data-dir`，是 persistent Chrome profile；
- 勘察时 `doxagent-reuters-chrome.service` 为 `inactive`，本机 CDP 探测失败。按当前代码，认证正文路径可能返回 `browser_unavailable`，Crawler Plane 则可降级到无登录态的代理 headless browser。

该快照只说明运行形态，不代表历史/未来成功率；本轮未重放文章，也未统计自然流量成功率。

## 3. 当前整体调用链

### 3.1 新消息采集

```text
SQLite source_definitions / default_profiles / ticker_source_bindings
  -> GlobalPollScheduler
  -> AdapterRegistry.resolve(adapter_ref)
     -> builtin adapter
     -> file:<path>:<factory> 动态 adapter
     -> crawler:<crawler_id> -> Crawler Plane active release
  -> PollResult(messages, failures, checkpoint, coverage)
  -> admission
  -> durable content_enrichment_jobs（启用正文补全时）
  -> Raw / Standard / Stream
```

关键事实：

- `SourceDefinition` 已持久化并版本化，包含 `source_id`、kind、`adapter_ref`、参数 schema、默认参数、scheduler group/constraints、正文模式 ENRICH/SKIP 和 enabled；见 `message_bus_v2/schema.py:178-225`。
- 初始消息源和默认优先级/Profile 写死在 `message_bus_v2/manifests.py`。默认启用 Benzinga、Finnhub、Yahoo、IBKR、Reuters；Google RSS 已注册但不进入默认 Profile。
- source update 会检查所有现有 binding 的参数兼容性，支持 revision/rollback；见 `message_bus_v2/service.py:173-352` 和 `repository.py:1012-1168`。
- Crawler Plane 的 crawler package/version/checkpoint/execution/cassette/retry/alert 均持久化；ACTIVE release 可通过 `crawler:<crawler_id>` 注册成 Message Bus source。
- Crawler 子进程不能直接联网，HTTP/browser 请求由 parent-owned `ParentNetworkSession` 代理。这是未来注入站点策略解析和组合租约的有利边界。

### 3.2 正文补全

```text
PollResult message
  -> admission
  -> EnrichmentJob（冻结 source/binding/message/pipeline_version/deadline）
  -> ContentEnrichmentHub durable claim + 60s lease/20s renew
  -> native provider article，或 SharedContentExtractor
  -> body_v2.1 ArticlePipeline
     -> direct curl_cffi
     -> publisher link / caption / public API
     -> Playwright browser
     -> Jina Reader（仅非认证路径）
  -> quality / identity validation
  -> accept_message
  -> Raw / Standard / Stream
```

关键约束：

- 补全在 Raw identity/content hash 和正式发布之前执行；provider 输出先进入 durable queue。
- job 快照保存当时的 `SourceDefinition` 与 binding；后续修改 source 不会自动改变已排队 job。
- 默认 deadline 最大 180 秒，队列级最多两次实际尝试；terminal access/identity/challenge 不重试。
- worker 总并发限制 1–8；claim 会在一个有界窗口内按精确 hostname 尽量摊开。
- 成功正文才建立 original URL、redirect URL、publisher URL 的 verified alias；失败不降低正文质量门槛。
- Yahoo 抓到 Barron's 等第三方文章时，正文识别已经依据最终文章 URL/hostname 进入 Barron's 适配，而不是依据 `source_id=yahoo_finance_news`。但 job 中的采集 source 仍是 Yahoo，当前没有独立的 site-policy identity。

## 4. 出口、Profile 与 fallback 现状矩阵

| 路径 | 客户端/上下文 | 当前 proxy 行为 | 持久化 | 当前治理粒度 |
| --- | --- | --- | --- | --- |
| 正文 direct | 单个长寿命 `curl_cffi AsyncSession` | 读取全局 egress URL | cookie jar 仅进程内 | 全局；冷却按精确 hostname |
| 正文认证 browser | operator CDP 默认 context | 不把应用 proxy 绑定到默认 context；实际出口取决于外部 Chrome/主机 | 外部 Chrome user-data-dir | 认证 hostname 集合，共用一个默认 context |
| 正文公开 browser | CDP `new_context(proxy)` 或本地 browser | 全局 egress URL | 临时 context | 全局 |
| Yahoo browser 新消息 | Crawler Plane 单一 context | 生产 CDP+proxy 下为临时 proxy context | 无站点独立持久 profile | 全局 runtime |
| Yahoo NCP/RSS | `YahooTransport` 的进程共享 curl_cffi session | 全局 egress URL | cookie jar 进程内 | Yahoo 单例 |
| Reuters site search | Crawler Plane 单一 context | 全局 egress URL | 同上 | 全局 runtime |
| Crawler Plane package 的 `ctx.http` | 一个 parent `httpx.AsyncClient` | **没有读取 egress URL** | connection pool 进程内 | 全局 client |
| 其他 builtin API/RSS adapter | AdapterRegistry 共用 `httpx.AsyncClient` | **没有读取 egress URL** | connection pool 进程内 | 全局 client |
| Content Jina Reader | 同一个正文 curl_cffi session | 全局 egress URL | 进程内 | reader endpoint + publisher 双重 pacing |

结论：应用层只有一个 proxy endpoint。Mihomo sidecar 可以在其内部按域名把 Reuters 分到荷兰、其他流量分到 fallback 节点，但该路由不属于 DoxAgent Registry，DoxAgent 不知道最终节点/出口 IP，也无法把它与 Browser Profile、健康状态和 fallback generation 原子绑定。

## 5. 429 与失败切换现状

“现行的一次 429 冻结机制”在代码中实际分成三类：

1. 正文 `PublicTransport`：任一 direct/browser 429 将**精确 hostname** 写入进程内 `cooldowns`；尊重 `Retry-After`，缺省 30 秒。后续同 hostname 返回 `domain_cooldown`。进程重启即丢失。
2. 正文 durable queue：429/5xx/网络错误可在 deadline 内进行一次延迟重试；`challenge_required`、登录/订阅、identity mismatch 等不重试。
3. Yahoo：
   - `YahooTransport` 按 `rate_scope` 维护进程共享 circuit，连续 429 为 300/900/1800 秒并尊重更长的 Retry-After；
   - Yahoo browser probe 在请求前预留 300 秒；普通失败也冷却 300 秒，429 采用同样的递增退避；
   - NCP/API 与 RSS circuit 分离。

这些机制只是在当前路径上等待或切到另一**获取路线**，不是切换 `Browser Profile + Proxy Egress` 组合。正文 direct/browser/reader 也不是组合轮换；同一个全局 proxy URL 可能最终仍落在同一出口。

不能简单删除所有 429 backpressure。这样会把被风控的组合继续并发打满，放大封禁。正确替代关系应是：

- 429/403/challenge/网络故障更新组合健康；
- 当前组合进入有界 quarantine；
- 站点的 crawler 与正文 resolver 共同切换 active combination generation；
- 域名级最小请求间隔仍保留；
- 所有组合都不可用时返回 PARTIAL/UNAVAILABLE 并记录下一次可探测时间，而不是无界重试。

## 6. 域名匹配与覆盖现状

当前没有统一 Domain Resolver，各模块各自处理 hostname：

- 正文专项抽取通常只做 `www.` 去除后与根域精确比较；
- `authenticated_hosts`、`disabled_hosts`、browser lock/context、cooldown 使用 URL 中的精确 hostname；
- `DomainFetchController` 只去掉 `www.`，不计算 registrable domain；
- Message Bus source registry 不声明它治理哪些域名；
- Yahoo adapter 会记录 `publisher_domain`，但只是消息 metadata，不参与策略路由；
- redirect 后会重新解析 hostname，但没有 alias、subdomain、canonical owner 或优先级冲突规则。

这不足以满足“域名规则完整覆盖”。至少需要显式建模：

- canonical `site_id`，例如 `yahoo_finance`、`barrons`；
- exact host、suffix host、明确 alias 和排除规则；
- 裸域/`www`/地区域名/登录域名/API 域名/CDN 与 reader endpoint 的角色；
- URL redirect 后重新 resolve；
- 多条规则命中时的优先级与拒绝歧义；
- 未注册域名的 generic policy；
- 正文按最终 publisher URL 归属，而非 acquisition source_id 归属。

不建议仅用 eTLD+1 自动合并全部子域名：同一公司的 API、登录、图片 CDN 和新闻正文可能需要不同访问规则。Registry 应允许 canonical site 拥有多条显式 host pattern，并保留 exact-host override。

## 7. 现有 Registry 与目标 Registry 的边界

### 7.1 现有 Message Bus Source Registry

适合继续管理：

- 消息源是否启用；
- 采集 adapter/crawler 引用；
- 参数 schema 和 ticker 级参数；
- polling/streaming；
- scheduler group 与请求节奏；
- ENRICH/SKIP；
- source/profile/binding 版本和回滚。

它不应被改造成站点访问身份 Registry，因为：

- `source_id` 不等于 publisher site；Yahoo 消息可能链接 Barron's；
- NCP/RSS/API 等非爬虫路线不应被强行纳入站点 Browser/Profile 治理；
- 同一站点策略需要被正文 worker 和 crawler worker 跨进程解析；
- 站点组合健康与账号状态的生命周期不同于 ticker binding 和 source revision。

### 7.2 现有 Crawler Plane Registry

适合继续管理站点专项“新消息爬虫实现”：crawler package、version、certification、active release、rollback、checkpoint、replay/cassette 和 item retry。它已经比把爬虫函数硬编码进 SourceDefinition 更接近目标，但仍缺少域名所有权、egress/profile binding 和与正文策略的共同治理。

### 7.3 目标 Site Policy Registry 应新增的最小实体

以下是从现有约束推导出的最小边界，不是本轮实现：

- `SitePolicy`：`site_id`、domain match rules、crawler strategy ref、body strategy ref、generic fallback flags、版本/状态；
- `AccessCombination`：不可变的 `profile_ref + egress_ref + credential_ref`，priority、enabled、health/quarantine、generation；
- `BrowserProfile`：profile owner/browser service/CDP endpoint/user-data-dir identity，不直接保存 cookie；
- `ProxyEgress`：稳定 endpoint、预期 route/node identity、可选观测出口 fingerprint；
- `CredentialRef`：只保存 secret manager/受限配置引用，不保存明文用户名密码；
- `SiteRuntimeState`：当前 active combination、失败证据、quarantine 到期、最近成功、人工 reauth 状态；
- `GenericPolicy`：未注册域名的通用正文和通用 crawler 行为。

## 8. 对四项需求的逐项差距

| 需求 | 已有能力 | 主要缺口 |
| --- | --- | --- |
| 1. 同域名正文与新消息统一出口、不同域名不同节点 | 两边部分路径读取同一全局 proxy；Mihomo 可内部按域名分流 | 无统一 domain resolver；普通 crawler HTTP/多数 adapter 不走 proxy；无 per-site egress ref；应用看不到实际节点/IP |
| 2. 同站点统一持久 Profile+Egress，多组合 fallback，移除一次 429 冻结 | 正文有认证 profile；Crawler Plane 有一个 persistent context 选项；有 route fallback/cooldown | 两 worker 不共享 context/组合状态；生产 CDP+proxy 导致 crawler 临时 context；无多组合池、健康/quarantine、原子切换 |
| 3. 小型持久 Site Policy Registry | Message Bus/Crawler Plane 都有 SQLite registry/versioning 可参考 | 没有 site/domain policy schema；没有凭据引用、profile/egress 绑定；不能把 crawler/body strategy 一起替换；source registry 与 publisher ownership 不同 |
| 4. 每站点专项新消息和正文策略，缺省通用策略 | Crawler adapter 可动态替换；Crawler Plane package 可发布；正文已有若干专项硬编码和通用 fallback | 正文 strategy 不可注册；crawler 不是按 domain 解析；没有注册策略时的统一 resolver 和策略 provenance |

## 9. 必须明确的风险与技术冲突

### 9.1 Registry 不应保存账号密码明文

把用户名/密码作为普通 Registry 数据持久化会把凭据带入 SQLite backup、revision、日志、诊断和 agent tool 输出，风险不可接受。Registry 应只持久化 `credential_ref`；秘密本体放在权限受限的 secret store/环境挂载中。浏览器 cookie/profile 也只应由 Browser Profile owner 读取。

### 9.2 “共享同一个 profile”不能让两个进程同时打开同一 user-data-dir

Chromium persistent profile 有单写锁。正文 worker 和 message-bus worker 分别 `launch_persistent_context` 指向同一目录会冲突或损坏 profile。真正共享必须通过一个 browser service/CDP owner，让两个模块申请该 owner 中同一个 context，或把 browser fetch 统一下沉为服务调用。

### 9.3 Profile 与出口绑定要求出口身份稳定

如果一个 proxy endpoint 背后的 Mihomo fallback 自动更换节点，那么应用看到的 `egress_ref` 未变，但实际出口 IP 已变，违反“同一 Profile 绑定同一出口”的严格语义。每个组合需要稳定的节点/route endpoint，或至少持久记录 route generation/出口 fingerprint；健康切换应切换整个组合，而不是透明旋转 Profile 下的出口。

### 9.4 Playwright context 的 proxy 在创建时确定

不能在已有 persistent context 上热切换 proxy。组合切换必须选择另一个已绑定 profile 的 browser owner/context；不能只改一个环境变量或复用当前 CDP 默认 context。

### 9.5 当前认证 host 匹配是精确字符串

`reuters.com` 与 `www.reuters.com` 需要分别列入生产配置，其他子域不会自动继承。未来 domain rule resolver 上线前不能假设根域配置已经完整覆盖。

### 9.6 账号可见正文与第三方 reader 必须继续隔离

当前管线明确不把认证/订阅内容发送到 Jina Reader。统一策略时必须保留这条安全边界，generic fallback 不能越过 `credential_ref`/subscription policy。

## 10. 推荐的实现落点与顺序

本轮不实施，但从现有代码边界看，风险最低的顺序是：

1. 先增加独立 Site Policy schema/repository/domain resolver，并保持所有现有 source/profile/binding 不变；先做只读 shadow resolve 和覆盖率审计。
2. 抽象 `AccessCombinationLease`，把组合选择、健康、quarantine 和 generation 持久化；保留域名 pacing，替代各模块自行解释 429 的组合冻结语义。
3. 建立单一 Browser Profile owner/service。正文和 Crawler Plane 不再各自决定 CDP/default/new context，而是按 `site_id + combination_id` 请求页面。
4. 将正文站点分支封装为可注册 `BodyStrategy`，先迁移现有 Reuters/WSJ/Barron's/SA/TheStreet/CNBC/24/7 逻辑；generic strategy 保持现有质量门槛。
5. 让 Crawler Plane/browser 和 parent HTTP、Yahoo transport、需要代理的 builtin adapter 都通过同一个 site resolver/egress factory；IBKR/NCP/RSS 等不适用路线按明确 capability 跳过，而不是被 source 级策略一刀切。
6. 最后启用 combination fallback，并移除/收敛现有 Yahoo browser freeze、Yahoo circuit、正文 hostname cooldown 中与组合切换重复的部分。迁移期应保留兼容指标，不能一次性删除所有 backpressure。

建议重点修改/新增的入口：

- `src/doxagent/site_policy/`：新 schema、repository、resolver、health/lease；
- `content_enrichment/extractor.py`、`pipeline.py`、`browser.py`：消费 resolver/combination lease，不再直接读全局 proxy/host set；
- `crawler_plane/runtime.py`、`service.py`：parent HTTP 和 browser 都按 combination 获取 transport；
- `message_bus_v2/adapters.py`、`news_adapters.py`、`yahoo_transport.py`：传递 site policy context，移除自有出口选择；
- `settings.py`/Compose：只保留 bootstrap/control-plane 地址，站点策略转入持久 Registry；
- `tools/providers/monitoring.py` 或独立 tool provider：提供站点策略查看、版本、启停、组合健康与人工 reauth 操作，但不返回秘密。

## 11. 验证与已知测试基线

本轮运行 10 个相关测试文件，共 152 项：`151 passed, 1 failed`。

唯一失败为 `tests/test_crawler_plane.py::test_crawler_poll_lineage_and_checkpoint_ownership`。失败不是本轮修改造成：fixture 固定 `published_at=2026-09-01T12:00:00Z`，测试使用真实 `utc_now()`；在 2026-09-21 已被 `admission.py` 的 REALTIME `>1800s` 规则过滤，因此 `raw=[]`。爬虫 execution、checkpoint 和 telemetry 断言此前均已通过。该测试属于日期漂移 fixture 问题，不能把它解释为 crawler transport/registry 失败。

其余覆盖包括正文 pipeline/browser/native/publisher/hub、Yahoo transport/page/RSS、四消息源和 Crawler Plane browser/runtime。未运行真实外网写入测试、未做正文重放，也未以历史 99 项通过结果冒充当前全量成功率。

## 12. 本轮没有做的事

- 没有修改正文、消息源、Crawler Plane、代理或 Chrome 代码；
- 没有启动/重启远端 Chrome、容器或 proxy；
- 没有读取或输出代理凭据、cookies、账号密码；
- 没有修改 `.env`/`.env.v2`；
- 没有写生产数据库、重放历史消息或触发交易；
- 没有把 sidecar 存活、poll succeeded 或 HTTP 200 等同于完整覆盖或正文成功。

