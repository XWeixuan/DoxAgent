# DoxAgent 消息网站策略治理与正文补全 / 爬虫访问体系优化需求

## 1. 背景

DoxAgent 当前已经具备较完整的 Message Bus、Crawler Plane 和正文补全链路。

现有设计中，不同消息源最终可以进入统一的正文补全中台；对于 crawler 类消息源，Crawler Plane 也已经拥有独立的 crawler 开发、验证、版本和上线生命周期。因此本轮不重新设计 Message Bus，也不重新建设第二套 crawler 或正文补全系统。 

近期多轮真实测试表明，当前正文补全和网页型消息抓取的成功率主要受两个因素影响：

1. **访问环境是否被目标网站识别为异常或机器流量**，其中出口 IP、Proxy Egress、浏览器/TLS 特征、Browser Profile/session 等都会产生影响。
2. **是否针对目标网站做了正确的访问和内容识别适配**。同一个访问方式在不同网站上的结果差异明显，因此不存在一个对所有新闻网站都可靠的通用抓取方法。

与此同时，DoxAgent 后续不能仅依赖少量通用消息源覆盖全部 ticker。不同板块和不同标的会持续增加大量人工选择、配置和维护的专门消息网站，因此需要比现在更加细化、可持续维护的网站级治理能力。

本轮优化有两个核心业务目标：

**第一目标：显著提升正文补全成功率。**

**第二目标：为未来持续扩充和维护大量网页型消息源建立统一的网站级访问和策略治理能力。**

---

# 2. 总体设计思想

新增一个独立的 **Site Strategy（消息网站策略）治理层**。

它位于现有 Message Bus / Crawler Plane / Content Enrichment 与实际网络和浏览器访问能力之间：

```text
                  Message Bus
                 /           \
        网页型消息抓取      正文补全
               \             /
                \           /
                  Site Strategy
                        │
             Browser / HTTP / Proxy
```

Site Strategy 回答的核心问题不是：

> “这个 ticker 应该监测哪些消息源？”

而是：

> “当 DoxAgent 需要访问这个消息网站时，应该使用什么访问身份、什么针对性抓取策略和什么正文识别策略？”

因此它与现有 Message Bus SourceDefinition、ticker binding、polling cadence、query terms 等配置属于不同维度。

现有 Message Bus 仍然负责**消息源是什么、哪个 ticker 使用它、什么时候运行、如何进入消息流**。现有 binding 创建和更新仍保持自己的配置生命周期。

Site Strategy 负责**怎样可靠访问某个网站以及怎样从该网站获得消息和正文**。

两者不能合并。

---

# 3. 核心概念

## 3.1 Site Strategy

Site Strategy 是本轮新增的基本治理单位。

一个 Site Strategy 对应一个逻辑上的消息网站，例如：

```text
Yahoo Finance
Reuters
Barron's
TheStreet
Benzinga
Seeking Alpha
```

它不简单等于一个 hostname。

同一个逻辑网站可能包含：

```text
finance.yahoo.com
query1.finance.yahoo.com
其他明确属于 Yahoo Finance 的访问 host
```

因此每个 Site Strategy 可以维护一组明确的网站域名 / host 匹配规则。

所有需要治理的网页访问都先通过这些规则确定所属 Site Strategy。

无法匹配任何注册 Site Strategy 的网站，进入**通用策略**。

不要求所有互联网域名都提前注册，也不自动为所有新域名生成独立策略。

---

## 3.2 Access Combination

网站访问身份的基本切换单位定义为：

> **Browser Profile + Proxy Egress**

称为一个 **Access Combination**。

例如：

```text
Combination A
├── Browser Profile A
└── Proxy Egress A

Combination B
├── Browser Profile B
└── Proxy Egress B
```

Browser Profile 和 Proxy Egress 默认长期绑定。

不采用：

```text
Profile A
→ 今天 IP A
→ 明天 IP B
→ 后天 IP C
```

这种独立随机轮换出口的模式。

对于登录网站，账号身份可以与 Browser Profile 关联，但本轮暂不要求把：

```text
Account + Profile + Egress
```

设计成新的强制整体实体。

---

# 4. Site Strategy 的治理内容

一个 Site Strategy 至少能够表达以下几类信息：

```text
Site Strategy
│
├── 网站归属规则
│
├── Access Combinations
│
│     ├── Profile A + Egress A
│     ├── Profile B + Egress B
│     └── Profile C + Egress C
│
├── 网站专用新消息抓取策略
│
├── 网站专用正文补全 / 正文识别策略
│
└── 可选登录账号与登录维护相关信息
```

这些配置应具备持久化能力，并通过一个**简单、小型 Registry** 统一治理。

Registry 应支持人工持续维护，例如：

* 给某网站增加新的出口组合；
* 替换失效节点；
* 替换 Browser Profile；
* 更新网站 crawler；
* 更新正文适配策略；
* 增加登录账号；
* 修改域名归属。

这些配置不应继续散落在环境变量、Crawler Plane、正文代码和各种网站专用 if/else 中。

---

# 5. 网站归属与正文策略选择

消息的**发现来源**和消息正文实际所属的网站必须明确分离。

正文补全策略不能单纯根据：

```text
source_id
```

决定。

应该按照：

```text
消息被发现
   ↓
canonical / publisher resolution
   ↓
获得真正目标 URL / publisher
   ↓
匹配 Site Strategy
   ↓
使用该网站的正文访问策略
```

例如：

```text
Yahoo Finance crawler
       ↓
发现 Barron's 消息
       ↓
canonical URL = barrons.com/...
       ↓
正文补全归 Barron's Site Strategy
```

而不是 Yahoo Finance Site Strategy。

同理，Google News wrapper、Finnhub redirect、聚合站跳转等都需要在真正 publisher 被确定后再选择正文策略。

如果正文抓取过程中发生跨网站跳转，并且能够确定新的 publisher 网站，也应允许重新按照真实目标网站治理，而不是继续沿用最初发现来源的网站身份。

---

# 6. Browser Profile 与 Browser Runtime

同一个 Browser Profile 必须是一个真正持久化的浏览器身份。

其 cookies、localStorage、登录 session 和其他持久浏览器状态需要持续保留。

同一个 Profile 不允许由正文补全模块和 Crawler Plane 各自独立启动、独立占用。

逻辑上应满足：

```text
一个 Profile
      ↓
唯一 Browser Runtime Owner
      ↓
Crawler / Body Completion
共同使用这个访问身份
```

本轮需求只冻结这个所有权关系。

具体 Browser Runtime 如何组织、由哪个现有组件承载、通过什么接口复用，由后续执行方案设计。

---

# 7. 一个网站支持多套访问身份

同一个 Site Strategy 可以维护多套 Access Combination：

```text
Reuters

Combination A
Profile R-A + NL Node A

Combination B
Profile R-B + JP Node B

Combination C
Profile R-C + US Node C
```

正常情况下长期使用当前有效组合。

不进行每请求随机换 IP。

发生明确的**风控相关访问失败**时，当前组合可以暂时退出使用，并切换到下一个可用组合。

因此 fallback 的单位是：

```text
Profile + Egress
```

整体切换。

---

# 8. Access Combination 失败与健康状态

需要明确区分：

## 风控相关失败

这类失败可以影响当前 Access Combination 的健康状态并触发 fallback，例如：

```text
429 / Rate Limit
WAF / Bot Challenge
被识别为自动化访问
明显的 IP / network block
与目标网站风控相关的拒绝访问
```

具体错误识别规则由开发方案根据当前已有 diagnostics 和真实测试细化。

## 非风控失败

以下问题默认**不能因为失败而切换 Proxy/Profile**：

```text
文章不存在 / 404
页面已经删除
正文 selector 失效
DOM 改版
正文识别错误
当前确实没有新消息
业务解析失败
文章本身不完整
普通内容错误
```

登录失效、账号过期等身份问题也应单独识别，不能默认等同于 Egress 风控。

这样可以避免一个 crawler/parser bug 把一个网站的全部 Proxy 节点轮流判死。

---

# 9. Combination fallback 行为

一个网站当前 Combination 因风控失败而不可用时：

```text
Combination A
      ↓
风险访问失败
      ↓
A 暂时不可用
      ↓
Combination B
```

失败组合不是永久删除，而是进入临时不可用 / cooldown 状态，并允许后续恢复。

具体：

* 失败多少次判定；
* cooldown 多久；
* 如何 probe 恢复；
* 怎样选择下一个组合；

本轮不冻结，实现方案根据当前网络行为采用简单机制即可。

原则是：

> 不建设复杂 reputation scoring 或动态权重系统，只需要稳定实现“正常使用 → 风控失败 → 暂时切换 → 后续恢复”。

---

# 10. Crawler 和正文默认共享 Access Combination

同一个网站的新消息 crawler 和正文补全默认使用同一套访问身份：

```text
Site Strategy A
       │
Access Combination A
      / \
 crawler body
```

这样一个网站长期看到的是更稳定的一套：

```text
IP
Browser Profile
cookies
session
browser identity
```

而不是 crawler 和正文分别使用两个完全无关的访问环境。

但是这是一条**默认原则，而不是硬编码限制**。

如果后续真实测试证明同一个网站：

* 列表页；
* 搜索页；
* 正文页；

使用不同访问方式明显更可靠，则 Site Strategy 可以允许 crawler strategy 或 body strategy 显式覆盖默认 Access Combination。

首版不需要为了这种少数例外提前设计复杂继承系统。

---

# 11. Site-level Access Budget

既然 crawler 和正文开始共享网站访问身份，也需要共享一个轻量的：

> **Site-level Access Budget**

其作用是避免：

```text
Crawler 认为自己流量不高
+
Content Enrichment 认为自己流量也不高
=
同一个网站实际上被并发连续访问
```

Access Budget 只需要治理同一网站的：

* 基本并发；
* 最小访问间隔；
* crawler 与正文之间的竞争。

其中：

> **正文补全优先级高于新消息 crawler。**

当两种请求同时等待访问某网站时，正文补全优先。

限速无需设计得非常保守。DoxAgent 的实际使用中，同一网站短时间出现大量并发正文请求并不是常态，本轮目标是避免无意义的突发和内部竞争，而不是人为大幅降低实时性。

不建设复杂全局 Rate Limit Scheduler。

---

# 12. 两层 fallback 必须分离

本轮需要明确区分两种完全不同的 fallback。

## Access Identity fallback

例如：

```text
Yahoo crawler
 ↓
Profile A + Egress A
 ↓ 风控失败
Profile B + Egress B
 ↓
继续执行 Yahoo crawler
```

这是 Site Strategy 内部访问身份的 fallback。

---

## Message Acquisition fallback

例如 Yahoo 当前：

```text
页面 crawler
 ↓
NCP
 ↓
RSS
```

这是 Message Source 自己的消息获取策略 fallback。

页面 crawler、NCP 和 RSS不是三个 Proxy 节点，而是三种不同 acquisition path。

因此 Yahoo 的行为应该理解为：

```text
Browser Crawler
    │
    ├─ Combination A
    ├─ Combination B
    └─ Combination C

所有可用 Access Combination 都无法完成 crawler
    ↓
页面 crawler 策略整体失败
    ↓
进入 Yahoo 原有 NCP fallback
    ↓
必要时 RSS fallback
```

当前 Yahoo、Reuters 等 source 仍然属于 Message Bus acquisition 逻辑，本轮不能因为新增 Site Strategy 而重写为另一个 Source 系统。现有 adapter / Crawler Plane source 生命周期仍然保留。

---

# 13. 移除现有“一次 429 冻结”的主治理逻辑

目前正文和部分消息源存在各自独立的 429 cooldown / circuit 行为。

本轮调整以后：

> 单次 429 不再直接导致整个网站访问路径冻结并等待恢复。

风控相关失败应该优先进入 Access Combination 健康管理：

```text
Combination A 429
      ↓
A暂时退出
      ↓
尝试 Combination B
```

只有当该网站可用 Access Combination 全部不可用时，才认为对应网页访问策略当前失败。

底层仍然可以保留必要的：

```text
Retry-After
短时间 request pacing
单次请求保护
```

但它们属于 transport hygiene，不能继续取代多组合 fallback。

---

# 14. 网站专用新消息抓取策略

不同网站可能需要不同的新消息发现方式。

因此每个 Site Strategy 可以注册一个针对该网站的新消息 crawler strategy。

例如：

```text
Reuters
→ Reuters-specific search crawler

Yahoo Finance
→ Yahoo page crawler

某 IR 网站
→ site-specific listing crawler
```

Crawler Strategy 应继续复用现有 Crawler Plane 的 crawler 资产与生命周期，而不是在 Site Strategy Registry 里重新复制和维护 crawler 代码版本。

现有 Crawler Plane 已经拥有 crawler 创建、Live Probe、Certification、Promote、Register 等治理过程，本轮保持该职责。

Registry 只需要能够指向 / 选择对应 crawler strategy。

没有针对性 crawler strategy 的网站，使用现有通用网页抓取策略，或按照当前 source 本身的行为运行。

---

# 15. 网站专用正文补全策略

正文补全同样支持：

```text
site-specific body strategy
```

例如不同网站可以有：

* 特殊正文容器；
* 特殊页面结构；
* 特定 browser requirement；
* 特定 article API；
* 登录后正文；
* “continue reading”展开；
* 特殊 canonical 解析；
* 特殊正文识别规则。

但这些策略应当是现有统一正文补全中台的**网站适配能力**，而不是每个网站各自建设完整正文 pipeline。

整体仍然保持：

```text
Content Enrichment
       ↓
根据真实 publisher
       ↓
Site Strategy
       ↓
site-specific strategy
       ↓
没有则 Generic Strategy
```

现有统一 enrichment intake 不改变。

---

# 16. Generic Strategy

并非每一个新闻网站都需要提前人工创建 Site Strategy。

任何没有注册针对性治理的网站应继续能够工作。

因此系统必须保留：

```text
Generic Access Strategy
Generic Body Strategy
Generic Crawler / HTTP Strategy
```

只有经过人工判断确实值得长期维护的网站，才加入 Registry。

这一点非常重要，因为未来会不断出现：

```text
Google News发现的新 publisher
Finnhub发现的新 publisher
Yahoo发现的新 publisher
临时消息来源
```

不能要求“未注册网站 = 无法正文补全”。

---

# 17. Site Strategy 与 ticker 的边界

Site Strategy 原则上是：

> **网站级的全局能力。**

而不是 ticker 级配置。

例如 Reuters 的：

```text
Proxy
Browser Profile
正文识别方式
搜索页面结构
```

并不会因为 MU、NVDA 或 INTC 而改变。

Ticker 相关内容，例如：

```text
搜索词
company_short_name
ticker
poll cadence
是否启用该 source
```

仍然属于 Message Bus Source / Binding / crawler parameters。

因此：

```text
Ticker A ─┐
Ticker B ─┼── Reuters Source
Ticker C ─┘
               ↓
         Reuters Site Strategy
```

多个 ticker 共用同一网站访问治理。

这也是本轮能够支持未来大量人工扩展消息源，而又避免配置爆炸的关键。

---

# 18. Registry

需要新增一个简单、持久化的 Site Strategy Registry。

它的职责是成为以下配置的唯一清晰入口：

```text
有哪些 Site Strategy
每个 Site 覆盖哪些 domain / host
当前使用哪些 Access Combination
当前组合状态
crawler strategy 指向哪里
body strategy 指向哪里
是否需要登录
对应的账号 / credential 信息在哪里
```

Registry 必须支持人工查询和修改。

具体：

* SQLite 还是现有数据库；
* schema；
* tool/API；
* UI；
* 配置文件还是 repository；

由后续技术方案决定。

本轮不要求建设复杂配置中心。

---

# 19. 登录网站与账号信息

某些网站正文需要登录后才能正常访问。

Site Strategy 因此允许维护登录相关信息，例如：

```text
是否需要登录
使用哪个 Browser Profile
账号信息 / credential
登录页面
当前登录状态
```

本轮不强制设计专门的 secrets infrastructure，也不强制账号密码必须采用某种特殊保存方式。

核心要求只有两个：

### 第一

这些登录信息必须有一个**明确、统一、容易被 DoxAgent 找到的归属位置**，而不是散落在人工笔记、服务器文件和临时环境变量中。

### 第二

当前数据模型需要为未来下面这种能力保留自然入口：

```text
发现 Profile 登录状态失效
        ↓
启动 Codex SDK / login maintenance task
        ↓
使用对应账号
        ↓
Browser Automation 完成正常登录
        ↓
更新 Browser Profile
        ↓
重新验证 session
```

本轮**不实现完整 Codex 自动登录维护系统**，但当前 Site Strategy / Profile / credential 的组织方式不得阻碍以后加入这一能力。

---

# 20. Proxy Egress

Proxy Egress 是 Site Strategy 的独立可维护资源。

不同网站可以选择不同节点：

```text
Reuters → NL node
Yahoo → JP node
Barron's → US node
```

一个网站也可以维护多套节点用于 fallback。

需要区分：

```text
Proxy Node
```

与：

```text
实际公网 Exit IP
```

因为多个代理节点可能最终共享同一个公网出口，一个节点的实际公网出口也可能发生变化。

因此系统至少应能够观测当前 Combination 实际使用的公网 exit IP。

如果某节点对应的公网出口发生变化，应被视为该访问身份发生了变化并留下可观察信息。

本轮不要求复杂 IP reputation 管理。

---

# 21. 可观测性

由于本轮优化最核心的目标是**提升正文补全成功率**，Site Strategy 不能只是一个不可观察的配置层。

至少需要能够回答：

```text
某网站当前使用哪个 Combination？
实际 Exit IP 是什么？
当前 Combination 是否健康？
最近发生过什么风控失败？
是否发生过 fallback？
Crawler 成功率如何？
正文补全成功率如何？
失败主要来自 access 还是 extraction？
哪些网站长期使用 Generic Strategy？
```

尤其正文补全需要能够按：

```text
site/domain
body strategy
Access Combination
failure category
```

统计结果。

首版不要求做复杂前端 Dashboard。

但底层必须保留这些诊断信息，使后续真实测试可以比较优化前后的正文成功率。

---

# 22. 与当前系统的兼容边界

本轮明确**不做**以下事情：

### 不重构 Message Bus Source 系统

现有：

```text
SourceDefinition
Ticker Binding
Polling / Streaming
Source Parameters
```

继续存在。

Site Strategy 不替代它们。

---

### 不重构 Crawler Plane 的资产生命周期

Crawler Plane 继续负责：

```text
crawler代码
版本
测试
Live Probe
Certification
Promote
```

Site Strategy 只引用/选择 crawler capability。

---

### 不建设第二套正文补全 pipeline

仍然只有统一 Content Enrichment。

Site-specific body strategy 是其适配能力。

---

### 不代理 DoxAgent 的所有互联网流量

本轮治理范围只包括：

```text
网页型消息抓取
+
正文补全
```

数据库、LLM、IBKR、其他 API 和普通系统通信不因为 Site Strategy 改变出口。

---

### 不要求所有 Source 都进入 Site Strategy

例如：

```text
IBKR
RSS
标准 API
其他不依赖网页访问身份的数据源
```

如果本身不涉及网站 crawler/browser，不需要因为本轮改造被强行纳入。

Yahoo 也只有：

```text
页面 crawler
```

进入 Site Strategy。

其 NCP / RSS fallback 仍由 Yahoo Source 自己治理。

---

### 不做复杂自动路由系统

首版不需要：

```text
自动学习最佳 IP
动态权重
reputation score
机器学习路由
自动发现 Site Strategy
分布式 Registry
```

人工维护 + 简单健康 fallback 已足够。

---

# 23. 预期运行形态

完成本轮后，系统应能表达类似：

```text
Yahoo Finance

Domains:
- finance.yahoo.com
- ...

Access:
1. Profile yahoo-a + Proxy jp-a
2. Profile yahoo-b + Proxy us-a

Crawler Strategy:
Yahoo Page Crawler

Body Strategy:
Yahoo Body Strategy

Acquisition fallback:
Page Crawler
→ NCP
→ RSS
```

和：

```text
Barron's

Domains:
- barrons.com
- www.barrons.com

Access:
1. Profile barrons-a + Proxy us-a
2. Profile barrons-b + Proxy jp-b

Body Strategy:
Barrons Authenticated Body Strategy

Login:
required
credential available

Crawler:
none / generic
```

如果 Yahoo 抓到 Barron's 新闻：

```text
Yahoo crawler
     ↓
Barron's canonical URL
     ↓
Barron's Site Strategy
     ↓
Barron's Access Combination
     ↓
Barron's Body Strategy
```

---

# 24. 本轮验收方向

本轮最终验收不应仅看：

> “Registry 是否创建成功。”

而应该关注这套设计是否真正改变了访问行为。

至少需要证明：

1. 同一网站 crawler 和正文可以共享统一的 Site Strategy 和默认访问身份。
2. 不同网站可以稳定使用不同 Proxy Egress。
3. 同一网站可以存在多个 `Profile + Egress` Combination。
4. 风控类失败可以切换 Combination，而内容解析类失败不会误切 Proxy。
5. 一个 Combination 不可用不会立即冻结整个网站。
6. 所有 Combination 都失败后，网页 crawler 才进入原有 source acquisition fallback。
7. 正文根据最终 publisher / canonical domain 选择 Site Strategy，而不是根据发现 source。
8. 未注册网站仍然可以通过 Generic Strategy 工作。
9. 现有 API/RSS/IBKR 等非网页型消息获取路径不被破坏。
10. 能够按网站观察正文补全成功/失败情况，并用于比较改造前后的正文补全成功率。

---

# 25. 本需求的核心判断标准

如果后续 Codex 在细化技术方案时存在多个实现选择，可以用下面三个问题判断是否偏离需求：

**第一：这个改动是否让“网站”真正成为 crawler 和正文共同的访问治理单位？**

**第二：是否让 `Browser Profile + Proxy Egress` 成为稳定、可 fallback 的访问身份，而不是继续各模块自己管理 IP/session？**

**第三：它是否直接服务于两个目标——提高正文补全成功率，以及让未来不断增加的网站型消息源可以低成本维护？**

如果某个设计明显不能改善这两个目标，或者只是为了架构整洁增加新的复杂组件，就不应该纳入本轮。
