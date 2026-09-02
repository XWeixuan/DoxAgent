# O4_CONFIGURE / O4_REPAIR 开发方案

## 重点：O4_CONFIGURE 的 Source Need 研究与 Monitoring Portfolio 设计

# 1. O4 三个 Request 的最终职责

建议继续使用：

```text
O4_CONFIGURE
O4_DELIVER
O4_REPAIR
```

其中：

```text
PolicySet + Document2 Expectation Shell
                    ↓
              O4_CONFIGURE
                    ↓
        Source Needs + Configuration Plan
                    │
       ┌────────────┴────────────┐
       │                         │
已有能力可满足             需要新 crawler
       │                         │
直接配置 / 启用                 ▼
       │                    O4_DELIVER
       └────────────┬────────────┘
                    ▼
             ACTIVE MONITORING
                    │
                  Alert
                    ▼
               O4_REPAIR
```

三个节点分别回答：

### O4_CONFIGURE

> 这个 ticker **到底应该长期监控哪些具体 Source**？

负责研究、选择、权衡、配置和形成 Source Need Plan。

### O4_DELIVER

> 已经决定需要，但系统中不存在的 crawler source，如何真正交付上线？

### O4_REPAIR

> 一个已经批准并投入运行的 source 出现故障以后，如何恢复其原有 monitoring capability？

---

# 2. O4_CONFIGURE 的最终业务对象是 Source Need

上一版我把 Monitoring Need 提得太高，这里修正。

O4 内部当然仍然需要理解：

```text
Policy 在监测什么现实变化
涉及什么主体
谁最可能披露
变化频率怎样
什么披露具有交易价值
```

但这些都是：

> **Source Need formation 的 reasoning factors。**

最终 O4_CONFIGURE 必须把它们收敛成：

```text
Source Need
```

即：

> 为满足一个或多个重要 Policy / Expectation 的持续监测，需要某一种具体信息来源能力。

例如 Policy 是：

> 主要 HBM 平台容量/BOM/部署发生变化。

O4 可以内部分析：

```text
主体 = AI 平台 / CSP / HBM supplier
现实变化 = BOM / capacity / deployment
```

但最终不能只交付：

```text
Monitoring Need:
监测 HBM 平台变化
```

而必须进一步形成：

```text
Source Need:
需要对某几个最重要 AI 平台的 first-party disclosure
获得及时、低噪声覆盖
```

最终再落实成：

```text
Existing Source configuration
或
Existing crawler Source
或
New crawler Primary / Alternatives
```

所以整个认知链是：

```text
Policy / Expectation
      ↓
分析现实与披露主体
      ↓
Source Need
      ↓
Current capability mapping
      ↓
Concrete Source choice
      ↓
Configuration Plan
```

`Source Need` 才是 CONFIGURE 的核心工作单位。

---

# 3. Policy 数量绝不能决定 Source Need 数量

你给的 MU 测试 PolicySet 有大量 Policy。

里面包括：

* HBM；
* 客户端 DRAM/NAND；
* 中国竞争供给；
* Micron 制造；
* 战略客户合同；
* 监管；
* CXL；
* 服务器部署；
* 企业级 SSD；
* 良率；
* 原材料；
* 行业价格；
* 质量事故等。

如果做：

```text
1 Policy
→
1 Source Need
```

一定会产生严重 source inflation。

正确做法是从：

```text
“谁可能最早披露这些现实变化？”
```

反向聚合。

例如：

```text
MU ASP
MU shipment
MU product qualification
MU manufacturing
MU customer agreement
MU yield
MU capacity
MU guidance
MU quality
```

虽然涉及很多 Policy，

但大量现实都可能集中由：

```text
Micron first-party disclosures
```

覆盖。

所以一个：

```text
Micron IR / official disclosure Source Need
```

可以同时承担十几个 Policy 的一部分或全部 coverage。

这应该成为 O4 最重要的压缩能力之一：

> **多个 Policy 可以共享一个 Source Need；一个 Source Need 也可以同时由多个互补 Source 承担。**

---

# 4. CONFIGURE 的第一步：建立当前 Monitoring Baseline

O4 不能从空白纸开始设计。

它首先应该读取：

```text
Default Profile
Current ticker bindings
Registered Sources
Current parameters
Current polling
Current streaming
Crawler assets
```

当前实际 Source Registry 初始已经包含：

```text
benzinga_news
finnhub_company_news
stocktwits_messages
tikhub_x_search
tikhub_x_user_posts
newswire_rss
```

而默认 ticker profile 只启用：

```text
benzinga_news
finnhub_company_news
```

两者默认工作日纽约时间 07:00–18:00、60 秒 polling。

所以 O4 的基础认知应该是：

> **默认 profile 是大多数 ticker 的 monitoring baseline，不是待推翻的草稿。**

O4_CONFIGURE 的任务首先是：

```text
Default coverage
        ↓
对照当前 ticker Source Needs
        ↓
哪里真的存在重要 coverage gap？
```

而不是：

```text
“我还能添加什么 Source？”
```

---

# 5. Benzinga / Finnhub 的正确定位

这两个默认 Source 应被 O4 理解成：

> **ticker-specific general news discovery layer**

它们有很强的基础价值：

```text
公司重大新闻
财报相关新闻
产品消息
部分竞争/行业消息
重大突发
```

所以对于大多数 ticker：

```text
Benzinga + Finnhub
```

已经承担了一层很广的 news discovery。

但它们存在一个明确边界：

> **事件必须与 ticker 有足够明显的新闻关联，provider 才容易把它返回给这个 ticker。**

例如：

```text
Samsung / SK hynix 工厂事故
```

可能对 Micron 极其重要，

但新闻正文如果主要谈：

```text
Samsung
```

而没有明显：

```text
MU / Micron
```

ticker-specific feed 未必稳定召回。

因此 O4 要理解：

```text
默认 news coverage
≠
所有 Policy 的完整外部事件 coverage
```

这就是 Source Need analysis 真正有意义的地方。

---

# 6. Keyword Monitoring 是补充召回能力，但必须极度克制

对于：

```text
ticker feed 不容易召回
但又具有高交易价值
```

的现实变化，

可以使用：

```text
targeted search source
```

补足。

当前已经有：

```text
tikhub_x_search
```

支持 1–3 个 `search_terms`。

未来你计划加入：

```text
Google News Search RSS
```

也属于同一类 source capability。

这类 source 最大风险是：

> **Query 扩张导致 message volume 爆炸。**

因此 O4 对 search term 的认知必须是：

```text
search terms
不是 keywords dump
```

而是：

> **少量用于召回最重要 coverage gap 的高精度监测表达式。**

例如针对 MU：

不应该因为 PolicySet 包含：

```text
HBM
DRAM
NAND
SSD
server
China
AI
memory
```

就全部建 query。

这会产生巨大噪声。

更合理的是只选择：

```text
极高交易价值
+
默认 ticker news 很可能漏掉
+
能够用高 precision query 表达
```

的少数外部事件。

---

# 7. Search Source 的成本不只是请求次数

O4 必须理解两个完全不同的成本。

## Request Cost

例如：

```text
Google Search / API
TikHub
provider quota
crawler network requests
```

这会产生直接请求成本。

因此 query 越多：

```text
polling cost ↑
```

---

## Message Processing Cost

更重要的是：

```text
source
→ RawMessage
→ StandardMessage
→ StreamItem
→ Runtime
→ W1/W2/O3
```

如果 query 每小时返回很多低相关度消息，

真正昂贵的是：

> **下游每一条都需要花 token 判断它到底是不是新事件、是不是 Policy hit。**

所以：

```text
高召回 + 低 precision
```

不一定是好 source。

O4_CONFIGURE 应始终追求：

> **高价值信息密度。**

而不是：

> “宁可抓多一点，反正 Runtime 会过滤。”

Runtime 不是免费过滤器。

---

# 8. Search Term 的基本选择标准

一个新增监测词至少应该满足：

```text
对应明确 Source Need
+
能够覆盖重要 Policy / Expectation
+
默认 ticker news coverage 可能遗漏
+
查询表达可以足够聚焦
+
预计相关消息密度可接受
```

如果只能写成：

```text
“DRAM news”
```

这通常说明 Source Need 还没有收敛。

如果能够写成类似：

```text
某竞争 supplier
+
fab outage / production interruption
```

则可能具有更强价值。

O4 应减少 query 数量，而不是最大化 query 数量。

---

# 9. Search Source 还可以通过 cadence 控制成本

某个 Source Need 有价值，但并不需要 60 秒 discovery。

例如：

```text
某竞争者制造项目更新
```

可以考虑：

```text
较低 polling cadence
```

从而在 coverage 与 cost 之间取得更好平衡。

所以 O4 不仅决定：

```text
监不监控
```

还应该决定：

```text
用什么 Source
+
用什么参数
+
用什么 polling intensity
```

这才是真正的 Portfolio Optimization。

---

# 10. 每一个新增 Source 配置都必须经过真实 Source Inspection

这是我认为你这次最重要的新约束之一。

O4 不能因为模型觉得：

```text
“NVIDIA IR 应该不错”
```

就直接添加。

每一个新的 Source Need 如果最终准备产生：

```text
新的 Source binding
```

或者：

```text
新的 crawler source
```

都必须先实际：

> **搜索并访问该 Source。**

也就是说：

```text
Candidate Source
        ↓
Web Search
        ↓
访问真实 Source
        ↓
检查历史内容
        ↓
判断 cadence / relevance / usefulness
        ↓
才能进入 Configuration Plan
```

---

# 11. Source Inspection 具体要看什么

不需要 Agent 做一份长审计。

但它至少应该获得足够证据回答六个问题：

### 1. 它实际上发布什么？

不是根据网站名字猜。

例如：

```text
Investor Relations
```

可能实际上只有：

```text
quarterly results
```

也可能包括：

```text
product releases
operations
investor presentations
customer announcements
```

必须看真实历史内容。

---

### 2. 它多久发布一次？

不是要求精确统计。

而是建立量级判断：

```text
一天多次
每周
每月
每季度
一年几次
```

---

### 3. 过去内容与 Source Need 有多相关？

例如最近几十条中：

```text
大多数都与当前 monitoring target 有关
```

和：

```text
偶尔一年出现一次相关内容
```

完全不是一回事。

---

### 4. 它通常是不是第一手 / 早期来源？

例如：

```text
政府正式公告
公司 IR
监管机构
```

通常有较高 authority。

而某些媒体只是：

```text
转载官方公告
```

则新增 crawler 的边际价值可能很低。

---

### 5. 它是否可以稳定长期访问？

例如：

```text
public
stable URL/listing
no login
```

和：

```text
Bloomberg
subscription
login
aggressive anti-bot
```

价值完全不同。

---

### 6. 它相对现有 Portfolio 增加了什么？

这是最终 admission gate：

> **这个 Source 能不能提供现有 Source 很难稳定获得的高价值信息？**

如果只是重复：

```text
Benzinga / Finnhub 已经稳定获取的普通公司新闻
```

就没有必要新增 crawler。

---

# 12. Source Inspection 是 Admission Gate，不是形式动作

O4 必须能够得出：

```text
Candidate inspected
→ REJECT
```

例如：

```text
某行业网站
```

表面相关，

但实际历史：

```text
一年 4 篇文章
其中只有 1 篇涉及 Micron
而且内容几乎都是转载
```

正确结论是：

```text
不进入 Monitoring Portfolio
```

而不是：

```text
“既然我已经研究过了，那还是加上吧。”
```

Source Research 的主要价值恰恰是：

> **帮 O4 排除 source。**

---

# 13. Source Need 必须通过“边际价值”进入 Portfolio

我会让 O4 形成这种思考：

```text
这个 Source Need
        ↓
如果不新增 Source
当前 Portfolio 是否已经足够覆盖？
```

如果：

```text
YES
```

则不添加。

如果：

```text
PARTIAL
```

继续问：

```text
新增 Candidate Source
能否补足重要缺口？
```

然后综合：

```text
交易重要性
首次发现概率
authority
timeliness
precision
unique coverage
```

对比：

```text
request cost
message volume
downstream token cost
crawler complexity
maintenance burden
```

只有：

> **Marginal Monitoring Value 显著为正**

才进入最终 Plan。

这里不需要硬编码一个数学 score。

它是一种 Portfolio 思维。

---

# 14. 极低频 Source 的判断

低频本身不是拒绝理由。

关键看：

```text
frequency
×
expected impact
```

例如：

```text
BIS export-control rule
```

可能很少出现，

但一条就足以改变 Micron 的地区可实现需求。

而你的 PolicySet 中正存在这种 regulatory exposure。

这种 Source：

```text
低频
但高 impact
且 first-party
```

持续监测仍可能很值。

相反：

```text
一年一次行业趋势报告
```

即使相关，

但：

```text
不一定第一手
不一定及时
不会直接改变核心 expectation
```

就不值得长期 polling。

所以 O4 判断的是：

> **低频 Source 每次真正产生消息时，是否足够值得提前捕获。**

---

# 15. 高频 Source 的判断

高频不是问题。

```text
高频 + 高 precision
```

可能非常有价值。

真正危险的是：

```text
高频 + 低 precision
```

例如：

```text
宽泛财经聚合页
Yahoo Finance generic feed
宽泛 Twitter search
宽泛 Google News query
```

这类 Source 即使免费，

也会不断制造 Runtime token cost。

所以 O4 对它们应该有明显的：

> **precision requirement。**

---

# 16. 推荐的 Crawler Source 类型参考

这里我赞成给 O4 一组 **selection priors**。

不是硬 allowlist，

而是告诉它：

> 哪些 source 类型通常比较值得优先研究。

---

## A. Issuer First-Party Disclosure

例如：

```text
Company IR
Newsroom
Press Releases
Investor Events
Product announcement
Security / quality notice
```

这是通常性价比最高的一类。

优点：

```text
ticker specificity 高
authority 高
noise 低
material information 概率高
```

所以：

> **Issuer first-party disclosure 应成为绝大多数 ticker 的强优先 Source Need。**

例如 MU 大量关于：

```text
guidance
shipment
product qualification
manufacturing
yield
capacity
customer agreements
quality
```

的 Policy 都很可能由 Micron 本身首先或最终确认。

---

## B. Government / Regulatory Official Sources

例如：

```text
Commerce / BIS
Federal Register
SEC
FTC
FDA
EPA
DoD
EU Commission
中国相关监管部门
```

适合：

```text
regulation
license
export control
approval
enforcement
policy
```

特点：

```text
低频
authority 极高
单条潜在交易价值高
通常公开
```

属于很值得 crawler 的 Source 类型。

---

## C. Critical Competitor First-Party Disclosure

例如：

```text
Samsung
SK hynix
Kioxia
Western Digital
```

但不是因为：

```text
“它是竞争对手”
```

就全部抓。

只有当 PolicySet 中存在非常重要的：

```text
competitor supply
fab outage
yield
capacity
qualification
```

Source Need 时才值得加入。

MU 当前 PolicySet 对竞争供给确实存在大量敏感条件。

---

## D. Critical Customer / Platform First-Party Disclosure

例如：

```text
NVIDIA
AMD
Microsoft
Google
Meta
Dell
Apple
Lenovo
```

适合：

```text
BOM
platform qualification
deployment
product configuration
architecture changes
```

但这一类必须非常克制。

因为理论相关主体可能很多。

O4 应只选：

> **真正能改变核心 Policy，而且该平台自身披露具有显著领先价值的少数关键主体。**

不能因为 Policy 写了：

```text
主要 CSP/OEM
```

就爬十家公司。

---

## E. Official Incident / Recall / Enforcement Sources

例如：

```text
official recall page
product advisory
service incident
plant incident announcement
regulator enforcement bulletin
```

如果 ticker 的核心风险高度依赖：

```text
质量
事故
供应中断
```

这类 Source 可能拥有很高 information value。

---

## F. Structured Official Publication Page

某些 source 虽然不是新闻网站，但拥有稳定：

```text
公告
document listing
filing listing
rule publication
```

只要：

```text
内容重要
更新可识别
访问公开
```

也很适合 crawler。

---

# 17. 一般不优先做 crawler 的 Source 类型

同样可以给 Agent 一些明显的负面 selection prior。

不是禁止，而是需要非常强的 justification。

例如：

```text
付费 / 登录制媒体
强 anti-bot 网站
宽泛新闻门户
二次转载网站
低频低影响行业博客
极高噪声论坛
内容与现有 API 高度重复的 aggregator
```

Bloomberg 就是典型：

```text
内容价值高
≠
适合作为持续 crawler Source
```

如果：

```text
subscription
login
anti-bot
maintenance complexity 高
```

而大多数 material 信息最终又会通过：

```text
Benzinga/Finnhub/first-party
```

进入系统，

新增 crawler 的边际价值就很差。

---

# 18. Existing Crawler Asset 同样属于 CONFIGURE 的能力空间

当前 Crawler Plane 已经提供：

```text
crawler_plane.list
crawler_plane.get
```

查询 CrawlerPackage 和版本。

因此 O4_CONFIGURE 在形成 Source Need 后，应依次判断：

```text
Source Need
    ↓
当前 ticker 已绑定的 Source？
    ↓ NO
已有 Registered Source？
    ↓ NO
已有可用 Crawler Asset？
    ↓ NO
需要新 crawler
```

如果：

```text
已有 Crawler Asset
```

则 CONFIGURE 自己完成：

```text
必要时 register_source
+
ticker binding
```

不交给 DELIVER。

---

# 19. Current Source Capability Mapping

这一步不能只是：

```text
source_id 匹配
```

而是要理解 source 能力。

例如：

### Benzinga / Finnhub

```text
role:
general ticker news discovery
```

### TikHub User Posts

```text
role:
specific official / executive account monitoring
```

### TikHub Search

```text
role:
targeted external-event search
```

### Newswire RSS

```text
role:
specific RSS publication monitoring
```

未来：

### Google News Search RSS

```text
role:
targeted keyword news discovery
```

### Crawler Source

```text
role:
direct first-party / official publication acquisition
```

O4 应先用这些现有 primitive 组合 Source Need，

而不是一遇到新的主体就：

```text
new crawler
```

---

# 20. Configuration Change 必须最小化

当前 default 已经适用于大多数 ticker。

因此 CONFIGURE 应有：

> **Minimal Configuration Diff**

倾向。

例如：

```text
default:
Benzinga
Finnhub
```

然后 MU 可能增加：

```text
Micron IR crawler
+
Micron official X username
+
1 个非常聚焦的 competitor disruption search
+
1 个关键 regulatory source
```

这比：

```text
20 sources
30 search terms
10 crawler
```

更可能是一个优秀方案。

O4 的成功不由配置改动数量衡量。

---

# 21. 对现有 Source 的修改也需要理由

例如：

```text
enable tikhub_x_user_posts
usernames = [...]
```

它不是因为：

```text
“Twitter 也许有用”
```

而是需要：

```text
明确 Source Need
+
真实账号 inspection
+
历史内容确实经常发布相关信息
```

同理新增一个 RSS URL：

```text
必须实际访问 RSS / publication page
```

确认：

```text
内容
频率
相关度
```

才能加入。

---

# 22. Web Verification Gate

因此 CONFIGURE 最核心的一道执行 Gate 可以正式定义成：

```text
Any newly admitted monitoring source
        ↓
must have source inspection evidence
```

包括：

```text
新 crawler candidate
新 RSS
新 X account
新 search-based source strategy
```

对于：

```text
纯粹保留 default Benzinga / Finnhub
```

不需要重新研究 provider。

对于：

```text
已有配置原样保留
```

也不需要重复 inspection。

只有：

> **本轮决定新增的 monitoring capability**

需要这个 gate。

---

# 23. Source Candidate 研究应该是“够用即止”

不要让 GPT-5.6 Sol High 因为 Web 能力很强就开始研究几十个网站。

对于每个 Source Need：

```text
找少量最高概率 candidate
↓
访问
↓
评估
```

一旦找到：

```text
明显高质量 Primary
+
必要数量的真实 Alternatives
```

就停止 candidate expansion。

Source research 的目的不是建立行业网站数据库。

而是：

> **解决当前 Source Need。**

---

# 24. Alternatives 的形成

一个 `needs_new_crawler` Source Need 最终应该提供：

```text
Primary
Alternative 1
Alternative 2
```

但数量不需要固定。

如果：

```text
Primary 明显优于其他所有 candidate
```

可能只有：

```text
Primary
```

也可以。

Alternatives 只有在：

```text
业务 coverage 足够等价
+
真实可访问
+
值得作为 fallback
```

时才写。

不能为了让 O4_DELIVER “有备选”强行填三个网站。

---

# 25. Portfolio Stopping Rule

O4_CONFIGURE 不应该要求：

```text
所有 Policy 都有独立 Source
```

它应该在：

```text
核心 Source Needs 已有足够 coverage
+
下一 Source 的 marginal value 已明显低于成本
```

时停止。

允许明确出现：

```text
某些边缘 Policy
没有新增 dedicated Source
```

原因可以是：

```text
默认 news 已有合理 coverage

或

事件发生概率极低且影响有限

或

唯一 source 成本过高

或

只能通过周期 structured data 获得

或

新增 source 的 noise / maintenance 不值得
```

这不是 coverage failure。

这是 Portfolio Optimization 的正常结果。

---

# 26. O4_CONFIGURE 的推荐工作流程

完整工作流建议收敛为：

```text
PolicySet
+
Expectation Shell
+
Current ticker state
        ↓
────────────────────────
1. SOURCE NEED DISCOVERY
────────────────────────
理解 material future-change space

按披露主体 / 渠道 / 可观测性聚合 Policy

形成少量 Source Need candidates
        ↓
────────────────────────
2. CURRENT CAPABILITY MAP
────────────────────────
Default profile
Registered Sources
Ticker bindings
Crawler assets

判断：
covered
configurable
missing
        ↓
────────────────────────
3. SOURCE NEED PRIORITIZATION
────────────────────────
交易重要性
信息领先性
缺口大小
持续监控适配度
        ↓
仅保留值得持续监测的 Source Needs
        ↓
────────────────────────
4. CANDIDATE SOURCE RESEARCH
────────────────────────
仅针对 unresolved high-value Source Needs

Web search
→ visit source
→ inspect history

判断：
authority
content
frequency
precision
timeliness
accessibility
feasibility
portfolio overlap
        ↓
────────────────────────
5. PORTFOLIO OPTIMIZATION
────────────────────────
Existing source config?
Existing crawler asset?
Targeted search?
New crawler?

比较 marginal value / cost

删除：
低价值
高噪声
高重复
低可行
的 candidate
        ↓
────────────────────────
6. APPLY EXISTING CAPABILITIES
────────────────────────
已有 Registered Source
→ config/binding

已有 Crawler Asset
→ register if needed
→ binding
        ↓
────────────────────────
7. PUBLISH CONFIGURATION PLAN
────────────────────────
已经完成的 configuration
+
New crawler Source Needs
+
Primary / Alternatives / Priority
+
明确 deliberately uncovered / no-change 判断
```

---

# 27. CONFIGURE 最终 Plan 的中心应该是 Source Need

最终计划不要按：

```text
Policy 1
Policy 2
Policy 3
```

排列。

而更适合按：

```text
Source Need A
covers Policy X / Y / Z

Source Need B
covers Policy A / B

Source Need C
covers Policy ...
```

然后每个 Source Need 有一个 resolution：

```text
KEEP DEFAULT
CONFIGURE REGISTERED SOURCE
ENABLE EXISTING CRAWLER
NEW CRAWLER REQUIRED
NO DEDICATED SOURCE
```

这里是语义设计，不代表现在就锁最终 JSON enum。

真正重要的是：

> **O4_DELIVER 接到的是具体 unresolved Source Need，而不是抽象 monitoring topic。**

---

# 28. `O4_CONFIGURE` 专用 Skill 应该控制什么

建议：

```text
monitoring-configuration.md
```

只控制六件事：

```text
以 Source Need 为最终规划单位

Default / existing capability first

每个新增 Source 必须真实 inspection

精度与下游 message cost 属于 monitoring cost

以 marginal coverage value 控制 source 数量

只有经过研究的 Primary / Alternatives
才能交给 O4_DELIVER
```

不要把：

```text
所有 source 类型
所有 API 参数
所有 tool schema
```

重复塞进去。

这些属于 shared：

```text
message-bus-operations.md
```

---

# 29. 可以给 Skill 一个简洁的 Crawler Selection Prior

CONFIGURE Skill 中很值得保留这样一段认知：

```text
通常优先研究：

1. issuer first-party disclosure
2. material government / regulatory publication
3. critical competitor first-party disclosure
4. critical customer / platform disclosure
5. official incident / recall / enforcement channel
6. stable official document publication pages
```

同时告诉 Agent：

```text
宽泛 aggregator
登录/付费媒体
高噪声论坛
低频低影响行业出版物
```

通常具有较差 crawler economics。

但这是：

> **prior**

而不是固定规则。

最终仍需要真实 Source Inspection。

---

# 30. O4_CONFIGURE 的执行 Artifact

为了防止研究型节点越跑越散，我建议至少有一个 progressive workspace artifact：

```text
Source Need Worklist
```

它可以记录：

```text
Source Need
关联的 material Policies
当前 coverage
candidate status
inspection status
最终 resolution
```

这不是新的业务 schema。

它主要是：

> Agent 的 run-scoped working state。

这样 GPT-5.6 Sol High 在处理几十条 Policy 时不会：

```text
前面已经归并过一次
后面又重新发明一个相同 Source Need
```

---

# 31. CONFIGURE 的完成条件

只有满足：

```text
所有 material Policies / Expectations
已经经过 Source Need analysis

Current Registered Sources
和 ticker config 已被读取

Crawler assets 已被检查

所有新增 Source
都完成真实 Source Inspection

所有 Existing Capability changes
已经实际 applied

所有 New Crawler Source Needs
都有足够明确的 Primary / Alternatives / Priority

Portfolio 已执行 marginal-value stopping
```

才算完成。

---

# 32. O4_REPAIR 的定位保持简单

`O4_REPAIR` 不重新做以上 Portfolio Research。

它负责：

> **恢复已经批准的 Source Need 对应的 monitoring capability。**

流程：

```text
Alert
 ↓
读取 Message Bus / Crawler Plane lineage
 ↓
定位 failure layer
 ↓
config?
crawler?
runtime?
diagnostic policy?
 ↓
最小修复
 ↓
验证健康
```

---

# 33. REPAIR 的故障分类

至少需要区分：

```text
Message Bus configuration failure

Polling / scheduler state

Crawler execution failure

Crawler discovery drift

Crawler content drift

Transport failure

Alert policy mismatch

Source itself no longer viable
```

只有确定：

```text
crawler code
```

有问题，

才进入：

```text
failure artifact
→ regression
→ new version
→ replay
→ repair
→ live probe
→ certify
→ promote
```

当前 Crawler Plane 已经完整提供这条 repair lifecycle。

---

# 34. REPAIR 的边界：不重新选择 Source

如果：

```text
Source 仍然有价值
只是 crawler broken
```

→ Repair。

如果：

```text
目标 Source 已经消失
永久改为必须登录
不再发布原有内容
已经无法承担原 Source Need
```

那么：

```text
REPAIR
→ RECONFIGURATION_REQUIRED
→ O4_CONFIGURE
```

因为：

> **寻找新的替代 Source 属于 Portfolio Design，不属于 Repair。**

---

# 35. REPAIR 的一个特殊问题：Crawler 是 Global Asset

一个：

```text
crawler:<crawler_id>
```

可能服务很多 ticker。

所以：

```text
MU O4_REPAIR
```

修改一个 crawler 时，

实际可能同时影响：

```text
NVDA
AMD
AVGO
...
```

这意味着 repair orchestration 后续应该有：

```text
crawler_id + active_version
```

级别的 repair dedupe / lease。

避免多个 ticker 同时因为同一 crawler failure：

```text
各自创建 v7
各自修复
```

业务 thread 可以继续 per-ticker，

但：

> **同一个 global crawler asset 同一时刻只应该有一个 repair owner。**

这是 O4_REPAIR 开发时需要特别解决的基础设施问题。

---

# 36. 最终三个 Node 的认知分工

现在三者已经可以非常清楚地区分：

```text
O4_CONFIGURE

“What concrete Sources are worth continuously monitoring
for this ticker, and how should they be configured?”
```

核心：

```text
Source Need
research
source inspection
portfolio optimization
minimal configuration
```

---

```text
O4_DELIVER

“Turn approved missing crawler Source Needs
into production monitoring capability.”
```

核心：

```text
implementation
live probe
certification
promotion
registration
binding
settlement
```

---

```text
O4_REPAIR

“Restore an approved monitoring capability
to its intended healthy state.”
```

核心：

```text
diagnosis
reproduction
minimal repair
regression
safe rollout
```

---

# 37. O4_CONFIGURE 最终应该形成的 Agent 性格

它不应该是：

```text
“为了安全，多加几个 source。”
```

也不应该是：

```text
“Policy 里出现一个主体，我就给它建 crawler。”
```

而应该形成一种很明确的工作习惯：

```text
先相信 Default Portfolio

只有明确 Source Need 才增加 coverage

优先使用已有 capability

搜索词极少且精准

Crawler 优先选择 first-party / official / high-authority source

所有新增 Source 都先访问真实历史内容

宁可少而高质量
也不要高 volume 低 precision

Coverage 已充分以后主动停止
```

我认为这才是 `O4_CONFIGURE` 最核心的调优目标。

它最终不是在回答：

> **“互联网上还有什么和 MU 有关？”**

而是在回答：

> **“如果 DoxAgent 要用尽可能少的长期 acquisition 和 Runtime 成本，稳定抓住最可能改变 MU 核心 expectation / policy 状态的消息，究竟值得留下哪些具体 Source？”**
