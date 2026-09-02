# O4_DELIVER 开发方案

## Monitoring Plan → Production Crawler Source Delivery

## 1. 节点定位

`O4_DELIVER` 的职责不是重新判断 ticker 需要监控什么，也不是重新检查现有数据源。

它的唯一业务目标是：

> **读取 `O4_CONFIGURE` 已发布的 Monitoring Configuration Plan，把其中明确标记为“需要新增 crawler source”的计划项逐项交付为可以正式运行的 crawler source，并形成对应的执行结算。**

因此整个 O4 workflow 的职责划分为：

```text
O4_CONFIGURE
    ↓
分析 Policy coverage
查询 Registered Sources / Crawler assets
选择现有 Source、Primary、Alternatives
完成已有 Source 配置
形成 Monitoring Configuration Plan
    ↓
O4_DELIVER
    ↓
只处理 needs-new-crawler items
开发 crawler
测试 / certification
promote
注册 Message Bus Source
建立 ticker binding
形成 Plan Settlement
    ↓

后续运行异常
    ↓
O4_REPAIR
```

这里有一个很重要的职责边界：

> **Crawler asset 的查询、复用和启用属于 `O4_CONFIGURE`。**

如果 Crawler Plane 中已有合适 ACTIVE crawler asset，那么节点一应当完成核查、必要的 Message Bus source registration 与 ticker binding，不应再把它作为“新 crawler 开发任务”交给节点二。

因此 `O4_DELIVER` 不执行：

```text
“让我再搜一下有没有现成 crawler 可以复用”
```

它相信节点一已经完成这个判断。

---

# 2. Registered Source 的统一语义

整个 O4 workflow 中统一采用：

```text
Registered Source
=
Message Bus SourceDefinition
```

而不是：

```text
Crawler Plane 中存在 CrawlerPackage
```

当前实现的真实状态也是如此：

```text
CrawlerPackage
    ↓
CERTIFIED
    ↓
ACTIVE release
```

此时 crawler 仍然只是 Crawler Plane asset。

只有：

```text
crawler_plane.register_source
```

成功之后：

```text
Message Bus SourceDefinition
kind = crawler
adapter_ref = crawler:<crawler_id>
```

才成为 Registered Source。Crawler Plane 自带的 reference crawler 也只会建立 working copy，不会自动进入 Message Bus Source Registry。

而：

```text
Registered Source
```

还不等于：

```text
Ticker 正在监控这个 Source
```

还必须：

```text
SourceDefinition
    ↓
TickerSourceBinding
```

且 ticker/source/binding/polling 四层 gate 满足以后才实际 polling。

因此 O4 中应始终区分三个状态：

```text
Crawler Asset exists

Registered Source exists

Ticker Binding exists
```

三者不能混为一谈。

---

# 3. Node 2 的输入必须是一份“可执行计划”

`O4_CONFIGURE` 交给 `O4_DELIVER` 的 crawler item 不能只是：

```text
“监控美国商务部政策”
```

而需要已经把业务判断做完。

每个待开发 crawler item 至少应在语义上提供：

```text
Monitoring coverage target
Priority
Primary source
Ordered alternatives
Planned crawler/source identity
Required source parameters
Expected polling / streaming configuration
相关 Policy / monitoring requirement
```

具体字段最终服从 Monitoring Configuration Plan schema，这里不额外创造 schema。

关键是 Node 2 必须能够明确回答：

> 我要把哪一种确定的 monitoring coverage 交付出来？

而不是重新开展 source discovery。

---

# 4. Alternatives 是封闭候选集合

这是 Node 2 很重要的一项业务约束。

如果 Node 1 给出：

```text
Primary:
BIS newsroom

Alternatives:
1. Federal Register
2. Commerce newsroom
```

Node 2 可以：

```text
BIS → Alternative 1 → Alternative 2
```

但如果三个都无法交付：

```text
结果 = FAILED / COVERAGE UNDELIVERED
```

而不是：

```text
自己去互联网再找第 4 个“差不多相关”的网站
```

因为：

> **source relevance 与 substitution 判断属于 `O4_CONFIGURE`。**

`O4_DELIVER` 可以自主决定 crawler 的技术实现，但不能自主扩大可接受 source 集合。

这样能防止 Agent 为了把计划“打勾”，最后找一个容易抓但 monitoring coverage 已明显下降的网站。

---

# 5. Node 2 不重新配置节点一已经完成的 Source

假设 Configuration Plan 中：

```text
A → existing Benzinga
B → existing Finnhub
C → new crawler
D → new crawler
```

Node 2 的 worklist 只有：

```text
C
D
```

它不重新打开：

```text
A
B
```

也不顺手“优化”其：

```text
search terms
polling interval
buffer mode
```

只有当前 Node 2 新开发出来的 crawler 才由 Node 2 完成：

```text
promote
→ register source
→ ticker binding
```

因为在 Node 1 运行时这些 source 尚不存在。

---

# 6. 执行前只做 Preflight，不重新做 Planning

Node 2 对每个 work item 开始时仍需要读取当前状态，但目的只是防止状态漂移和重复写入。

例如检查：

```text
该 plan item 是否已经由本次 run 的前一 attempt 完成
working version 是否已经存在
crawler/source id 是否发生冲突
计划生成后是否已有其他流程注册了同名 Source
```

这里不是重新检查：

> “有没有别的 crawler 可以复用？”

而是在判断：

> “我准备执行的 Plan 是否仍然可以按原计划安全执行？”

如果计划产生后外部状态发生实质变化，例如预定的 `source_id` 已被另一个不相关 Source 占用，Node 2 应标记：

```text
PLAN_STALE / REPLAN_REQUIRED
```

而不是擅自覆盖或重新选择业务 source。

这一点尤其重要，因为当前 `crawler_plane.register_source` 对已经存在的 source 会走 update，而不是简单报 duplicate。Node 2 在注册前因此必须确认当前 source identity 与本 Plan 一致。

---

# 7. Node 2 的基本执行模型

Node 2 首先从 Plan 建立 crawler delivery worklist：

```text
Monitoring Configuration Plan
        ↓
filter needs-new-crawler
        ↓
sort by priority
        ↓
Work Item A
Work Item B
Work Item C
```

然后逐项处理：

```text
Work Item
    ↓
Preflight
    ↓
Create / resume WORKING version
    ↓
Inspect real target source
    ↓
Implement crawler
    ↓
Live Probe
    ↓
Prepare deterministic fixtures
    ↓
Certification
    ↓
    PASS?
    ├─ YES
    │    ↓
    │  Promote
    │    ↓
    │  Register Source
    │    ↓
    │  Configure ticker binding
    │    ↓
    │  Integration verification
    │    ↓
    │  COMPLETE
    │
    └─ NO
         ↓
      Analyze evidence
         ↓
      New material hypothesis?
         ├─ YES → repair
         └─ NO  → next planned alternative

所有 alternatives exhausted
         ↓
       FAILED
```

一个难点不能无限阻塞剩余 work item。

达到该 Priority 的 effort boundary 后：

```text
settle current item
→ continue next item
```

---

# 8. Coding 方式保持开放

O4 使用 GPT-5.6 Sol High，本身就是 coding agent。

因此 `crawler-delivery` skill 不规定：

```text
requests
BeautifulSoup
XPath
Playwright
JSON endpoint
HTML selector
```

的优先顺序。

也不强制它必须使用 Crawler Toolkit。

Node 2 需要理解的只是：

```text
目标是什么
平台 contract 是什么
什么工具可用
怎样获得真实运行反馈
什么状态才叫完成
```

具体 crawler architecture 由 Codex 自主决定。

但自由实现仍需满足当前 Crawler Plane execution contract。

crawler entrypoint 接收：

```text
CrawlerContext
```

并返回可校验的 `CrawlerRunOutput` / observations；正式受管网络能力通过 `ctx.http` / `ctx.browser` 暴露，artifact 通过 `ctx.artifacts.save()` 保存。

这属于平台 I/O contract，而不是 crawler 实现策略限制。

---

# 9. 1 分钟 polling 对 Node 2 的真正含义

Node 2 不实现：

```text
while True:
    crawl()
    sleep(60)
```

Message Bus Global Scheduler 才拥有 polling cadence。

“一般约一分钟 poll 一次”对 O4 的含义是：

> **crawler 应适合作为频繁重复执行的一次性增量程序。**

因此 Codex 在实现时需要考虑现实运行成本，例如：

```text
是否每次都必须重新扫描全部历史
是否应该使用 checkpoint
是否能够只检查 listing 的近期区域
```

怎样优化完全交给 Codex 判断。

Crawler Plane checkpoint 属于：

```text
crawler + binding
```

并且只有 execution 成功才推进；Message Bus 最终消息 dedupe 仍然独立存在。当前正式 crawler poll 中，Message Bus `PollState.checkpoint` 对 crawler source 始终保持 `{}`，crawler checkpoint 完全由 Crawler Plane 持有。

---

# 10. 反爬采用 Reality-Driven Complexity

低频 crawler 默认从最简单、可靠的实现开始。

Node 2 不预先假设目标网站一定需要：

```text
复杂 browser
stealth
代理
特殊 session
```

而是：

```text
implement
    ↓
live_probe
    ↓
观察真实 source behavior
```

如果实际出现：

```text
403
JS-only
session dependency
rate limiting
challenge
```

才成为新的工程事实。

Codex 根据具体 evidence 决定下一种实现。

因此：

> **实现复杂度由实际失败证据推进，而不是由 Prompt 预先指定。**

---

# 11. Effort Policy：以“Evidence-backed Build Cycle”为单位

不能用简单：

```text
max_attempts = 3
```

作为 effort 标准。

建议定义一个：

> **Evidence-backed Build Cycle**

一次有效 cycle 至少包括：

```text
一个明确 implementation hypothesis
        ↓
实际代码修改 / 实现
        ↓
live probe 或 deterministic test
        ↓
获得新的 execution/certification evidence
        ↓
形成下一步判断
```

例如连续三次：

```text
改 selector
→ 同样失败
→ 没有新分析
```

不应该被视为三个高质量 cycle。

只有实质不同、由上一轮证据支持的实现 hypothesis 才计入 effort。

---

# 12. Priority 对应不同 Effort

建议第一版采用：

| Priority | Primary Source                                                                                                      | 每个 Planned Alternative                                      |
| -------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Normal   | 初始实现 + 最多 1 个 evidence-backed repair cycle                                                                          | 做一次真实 viability implementation；若明显接近成功，可追加 1 个 repair cycle |
| High     | 初始实现 + 最多 2 个 materially distinct repair cycles                                                                     | 初始实现 + 最多 1 个 evidence-backed repair cycle                  |
| Critical | 初始实现 + 最多 3 个 materially distinct repair cycles；若最后一个 cycle 已达到 near-pass 且存在明确新 hypothesis，可再追加 1 个 focused repair | 初始实现 + 最多 2 个 materially distinct repair cycles             |

这里真正的区别不是：

```text
Critical 多跑几遍同一代码
```

而是允许它对重要 source 探索更多有依据的解决路径。

所以：

```text
Normal < High < Critical
```

是明确的。

---

# 13. Critical 仍然有明确停止点

Critical 不代表无限死磕。

即使 Critical：

```text
Primary
+
Plan 中所有 alternatives
```

达到各自 effort budget 后仍失败：

```text
Work Item = FAILED
```

Node 2 到此停止。

它不会：

```text
再自行寻找一个第四方 substitute
```

这保证 effort 高，但 business scope 仍由 Plan 控制。

---

# 14. 什么时候可以提前终止某个 Source 尝试

不需要机械消耗全部 effort budget。

如果已经出现确定性证据，例如：

```text
目标内容实际上不存在于该 endpoint
网站当前公开访问路径无法获得 Plan 要求的数据
计划所依赖的 URL 已永久失效
目标内容必须经过当前系统不存在的认证身份才能访问
```

那么继续修改 crawler 没有意义。

Agent 可以提前将该 candidate 判为：

```text
INFEASIBLE
```

并进入下一 Plan alternative。

Effort Policy 的作用是：

> 防止过早放弃。

不是：

> 强迫 Agent 明知不可行仍运行固定次数。

---

# 15. 人工介入必须采用极严格定义

Settlement 可以声明：

```text
Human Intervention Required
```

但这个状态必须非常稀有。

其定义应是：

> **完成当前 Monitoring Plan 所必需的某个外部前置状态，无法通过 O4 当前具有的 coding、Crawler Plane、Message Bus、HTTP/API 和 workspace 能力创建，只能由人类在系统外完成。**

合法例子：

```text
目标网站必须登录
当前没有可用账号
必须由人类注册账号

必须购买订阅后才能访问
需要人工完成采购/付款

目标账号要求人工 MFA/实名步骤才能首次建立有效 credential

需要组织管理员给现有 service account 授权
而 Agent 没有该权限
```

---

# 16. 以下情况不能甩给 Human Intervention

这些全部应该由 Agent 自己处理、失败或返回 replan，而不能写：

```text
“需要人工介入”
```

包括：

```text
代码写不出来
selector 不工作
403 / anti-bot 技术问题
Playwright 出错
dependency 不方便
Certification FAIL
Cassette mismatch
timeout
content extraction 不干净
Source relevance 不确定
需要安全审批
需要人工审核 crawler 代码
需要人工决定下一种技术实现
```

这些分别属于：

```text
继续技术修复
计划失败
或者 REPLAN_REQUIRED
```

不是 human intervention。

人工介入不能成为 O4 的逃生口。

---

# 17. Human Intervention Request 还必须可执行

如果确实需要人工：

Settlement 不能只写：

```text
需要人工提供账号
```

而应明确：

```text
需要人类完成什么动作
为什么 O4 无法完成
完成后系统应获得什么状态/凭据
完成后从哪个 Plan Item / crawler version 继续
```

人工完成以后应能直接恢复 Node 2，而不是重新研究整个 Plan。

---

# 18. Crawler Plane 的真实开发生命周期

当前实现不是抽象的 package system，而是非常具体的：

```text
/var/lib/doxagent/workspaces/crawler-plane/

working/<crawler_id>/vN/
releases/<crawler_id>/vN/
cassettes/
artifacts/
```

`create_version` 创建 WORKING 目录；第一次必须从 v1 开始，之后必须严格是 latest+1。

`promote` 是：

```text
move
```

不是 copy：

```text
working/<crawler>/vN
        ↓
releases/<crawler>/vN
```

之后 release 只读。

所以 Node 2 应把：

```text
working_path
```

视为唯一 crawler authoring location。

---

# 19. WORKING 版本是 Agent 的开发状态

Agent 可以：

```text
创建文件
修改 crawler.py
制作 fixtures
修改 tests/cases.json
反复 live probe
反复 certify
```

但只作用于：

```text
working/
```

当前系统没有：

```text
manifest
requirements install
per-package virtualenv
dependency lock
```

这些能力。

因此 Skill 必须基于真实运行环境来教 Agent，而不能描述尚不存在的 package manager。

---

# 20. 推荐的开发闭环

每个 candidate crawler 的标准开发流程为：

```text
create / resume working version
        ↓
implement
        ↓
live_probe
        ↓
inspect:
  observations
  diagnostics
  cassette
  artifact
        ↓
根据真实响应调整 crawler
        ↓
准备 deterministic fixtures
        ↓
certify
```

当前 `live_probe` 强制保存 response body，因此它既是：

> 实站功能验证

也是：

> Certification fixture 的主要 cassette 来源。

---

# 21. Certification 的真实含义必须写进 Skill

当前 Certification Service 一次执行六种真实检查：

```text
contract
replay
temporal_replay
synthetic_increment
determinism
failure_replay
```

其中 certification：

```text
不会访问真实网站
不会运行 package pytest
不会自动执行 live probe
```

Temporal Replay 会验证：

```text
T0 → checkpoint
T1 → expected increment
再执行 T1 → zero output
```

而 Synthetic Increment 使用指定 synthetic cassette。

因此：

> **Certification PASS 证明 deterministic crawler contract 成立，不证明当前真实网站此刻仍可抓取。**

Node 2 交付要求必须同时包含：

```text
成功 live probe
+
Certification PASS
```

两者缺一不可。

---

# 22. Node 2 必须准备三类 fixture

新 crawler 至少需要：

```text
Replay
Temporal
Synthetic
```

当前 Certification 没有这三类 case 就不能完整通过。

Node 2 因此不能只做到：

```text
live_probe 成功
```

就直接 promote。

---

# 23. Certification PASS 后进入冻结状态

当前 `promote` 会重新计算 working digest。

Certification PASS 后如果 Agent 又修改文件：

```text
promote
→ rejected
```

真实流程因此必须是：

```text
last code change
    ↓
certify PASS
    ↓
不再修改 working
    ↓
promote
```

如果确实需要修改：

```text
修改
→ 重新 certify
```

当前实现明确检查这一点。

---

# 24. Promote 不等于 Source 完成交付

`promote` 之后：

```text
Crawler ACTIVE
```

但还没有：

```text
Registered Source
```

必须继续调用：

```text
crawler_plane.register_source
```

而且当前实现要求 crawler 已经存在 ACTIVE release 才允许注册。

---

# 25. Crawler schema 与 Source schema 必须由 O4 主动保持一致

这是当前实现一个非常重要的真实缺口。

Crawler version 有：

```text
parameter_schema
```

Message Bus SourceDefinition 也有：

```text
parameter_schema
```

但是：

> **当前系统不会自动检查两者是否一致。**

而 promote 也不会自动同步 Message Bus SourceDefinition。

因此 Node 2 必须把这一点作为交付检查：

```text
CrawlerVersion.parameter_schema
        ==
SourceDefinition.parameter_schema
```

至少语义上保持一致。

否则：

```text
crawler 能运行
```

但：

```text
Message Bus binding 参数校验
```

可能完全是另一套 contract。

---

# 26. Register Source 后必须建立 ticker binding

注册以后：

```text
Source Registry
```

只是拥有 source。

Node 2 还要：

```text
monitoring.get_ticker_config
        ↓
构造完整 binding configuration
        ↓
monitoring.update_ticker_config
```

对于当前 `monitoring.update_ticker_config`，Agent 必须牢记：

> 更新 binding 时如果省略 `source_parameters`，当前工具会写成 `{}`。

因此必须遵守：

```text
read
→ merge
→ write complete source_parameters
```

这一真实实现约束。

新 crawler binding 同样应显式传完整：

```text
source_parameters
polling
streaming
```

而不是依赖工具隐式继承 source defaults。

---

# 27. Integration Verification

Node 2 完成 binding 后，需要确认：

```text
ACTIVE crawler exists
Registered Source exists
binding exists
binding parameters validate
source/binding/polling enabled state 与 Plan 一致
```

如果当前：

```text
ticker 尚未 running
active window 未到
Message Bus v2 deployment flag 未开启
```

则不能伪造：

```text
“生产 polling 已验证”
```

这些应该进入 Settlement：

> **Current Constraints**

而不是任务失败。

当前实现明确说明：

* source registration 不创建 binding；
* binding 不会让未启动 ticker 自动进入 running；
* polling 还依赖 ticker/source/binding/polling/active-window gate。

---

# 28. Node 2 的完成定义

一个新 crawler Plan Item 只有以下链路全部成功才算 `COMPLETED`：

```text
Crawler implementation complete

+
Live Probe succeeds

+
Certification overall = PASS

+
Crawler version promoted ACTIVE

+
Message Bus SourceDefinition registered

+
Crawler / Source parameter schemas aligned

+
Ticker binding created with planned parameters

+
Configuration state verified
```

“crawler.py 可以跑”远远不够。

---

# 29. Partial Success 必须保留，而不是回滚全部

例如：

```text
Crawler v1
certification PASS
promote PASS
source register FAIL
```

此时：

```text
ACTIVE crawler v1
```

已经是有效资产。

Node 2 不应该：

```text
重新写 v2
```

而应该继续解决：

```text
source registration
```

同理：

```text
source registered
binding update failed
```

则解决 binding。

整个 delivery lifecycle 应按 durable step 恢复，而不是每次失败从头来。

---

# 30. Node 2 需要 run-scoped Worklist / Checkpoint

Node 2 可能一次处理多个 crawler item。

因此不能只依赖 Codex thread 记住：

```text
哪个已经做完
哪个做到 certification
哪个正在 alternative 2
```

需要一个显式 progressive work artifact / workflow checkpoint。

它至少在语义上记录：

```text
当前 Plan Item
当前 candidate
当前 crawler/version
已经完成的 delivery stage
已经消耗的 effort cycle
最近 failure evidence
最终 item status
```

具体 schema 后续设计。

目的不是详细审计 Agent，而是：

> **让一个长 Node 2 request 在 retry、timeout 或 process restart 后能够从 durable step 恢复。**

---

# 31. Plan Settlement 不修改原 Configuration Plan

Node 1 发布的 Configuration Plan 保持 immutable。

Node 2 产生独立：

> **Monitoring Delivery Settlement**

它引用原 Plan，并回答：

```text
计划完成了多少
每个 crawler work item 最终用了哪个 planned candidate
交付了什么 crawler release
注册了哪个 Source
binding 是否完成
哪些项目失败
为什么失败
当前还有哪些运行约束
是否真正需要人工介入
```

因此业务状态是：

```text
Configuration Plan
+
Delivery Settlement
```

而不是 Node 2 回头改 Node 1 artifact。

---

# 32. Settlement 的三类核心内容

## A. Plan Completion

逐项回应节点一：

```text
COMPLETED
FAILED
REPLAN_REQUIRED
```

并关联：

```text
selected planned source
crawler_id / version
certification
registered source
binding
```

---

## B. Current Constraints

这是你这次补充得非常重要的一项。

即使 crawler 已交付，也可能存在：

```text
当前处于 active window 外
ticker 当前未 running
Message Bus v2 deployment flag 当前关闭
正式 scheduler 尚未产生首次 production poll
```

这些不是失败，也不是 human intervention。

Settlement 应明确：

> 当前 monitoring capability 已经做到哪一步，什么现实条件尚未发生。

这样下游不会把：

```text
“已经配置”
```

误认为：

```text
“已经观察到生产流量”
```

---

## C. Human Intervention

只在严格满足前述 human-only gate 时产生。

每个 intervention 必须说明：

```text
External action required
Why Agent cannot perform it
What state/credential must exist afterward
Which Plan Item resumes afterward
```

---

# 33. Node 2 的 Skill 架构

O4 不应该只有一份巨大的 `node2.md`。

更适合拆成：

```text
Shared O4 Agent Prompt
        │
        ├── message-bus-operations.md
        │      O4_CONFIGURE
        │      O4_DELIVER
        │      O4_REPAIR
        │
        ├── crawler-plane-operations.md
        │      O4_DELIVER
        │      O4_REPAIR
        │
        └── node-specific skill
```

三个节点分别：

```text
O4_CONFIGURE
→ monitoring-configuration.md

O4_DELIVER
→ crawler-delivery.md

O4_REPAIR
→ source-repair.md
```

---

# 34. `message-bus-operations.md`

这是三个 O4 request 共用的基础设施说明。

它应该从现有 `agent_integration_reference.md` 中提炼：

```text
Registered Source 的真实定义
SourceDefinition
TickerSourceBinding
source/schema 查询方式
read-merge-write 更新语义
polling / streaming config
source/binding enable gates
monitoring.* tools
工具缺口与 HTTP/application service fallback
```

尤其必须保留当前几个真实坑：

```text
没有 monitoring.list_sources tool

update_ticker_config 缺 source_parameters 会写成 {}

source registration ≠ ticker binding

source registered/bound ≠ ticker 正在 polling
```

而不需要把 Message Bus 内部 scheduler/dedupe implementation 全部塞给 Agent。

---

# 35. `crawler-plane-operations.md`

由：

```text
O4_DELIVER
O4_REPAIR
```

共同注入。

主要讲真实操作方式：

```text
working/releases 路径
create_version
CrawlerContext
live_probe
cassette/artifact
certification cases
certify
promote/rollback
register_source
checkpoint
execution lineage
alerts/regression
```

其中必须根据当前实现说明：

```text
promote 是 move
certification 不跑 live site
live_probe 保存完整 cassette body
certify 后修改 working 会让 promote 失败
register_source 需要 ACTIVE release
crawler schema 不自动同步 source schema
```

这些都是 Agent 真正需要知道的运行事实。

---

# 36. `crawler-delivery.md`

这是 Node 2 唯一真正控制行为方式的专用 Skill。

它不教 crawler 编程。

它只负责：

```text
从 Plan 建 worklist
只处理 new crawler items
尊重 Priority / Alternatives
使用 evidence-backed effort
不扩展 Plan source 集合
逐阶段 delivery
completion definition
失败 / replan / human intervention
Settlement
```

因此它应该明显短于两个基础设施操作说明。

---

# 37. Shared O4 Prompt

三个 O4 request 共用一个角色 Prompt 是合理的。

Prompt 只需要定义稳定身份：

> **O4 owns ticker monitoring coverage configuration, source delivery and source operational recovery.**

它处理的是：

```text
Monitoring coverage
Source configuration
Crawler delivery
Source recovery
```

而不是一般 research。

Prompt 还应确定两个长期原则：

```text
Published artifacts / current service state
是业务事实源

Thread
只是可恢复执行上下文
```

节点行为由注入的 Internal Skill 决定。

这样 Prompt 不需要重复三套流程。

---

# 38. Thread 模型：每 ticker 一个长期 O4 Thread

采用你的方案②：

```text
MU → O4 thread MU
NVDA → O4 thread NVDA
AMD → O4 thread AMD
```

同 ticker：

```text
O4_CONFIGURE
      ↓
O4_DELIVER
      ↓
未来 O4_REPAIR
```

可以恢复该 ticker 的同一 O4 role thread。

这样 Agent 可以保留：

```text
这个 ticker 的 monitoring reasoning continuity
```

同时避免：

```text
MU / NVDA / AMD hidden context 混杂
```

---

# 39. Global crawler reuse 不依赖 Global O4 Thread

这一点特别重要。

Crawler asset 虽然：

```text
global reusable
```

但复用事实存在于：

```text
Crawler Registry
Message Bus Source Registry
```

不是存在于 O4 thread memory。

例如：

```text
MU 以前开发 SEC crawler
```

以后：

```text
NVDA O4_CONFIGURE
```

通过当前 Registry 就能发现并启用它。

不需要：

```text
NVDA thread 读取 MU thread
```

所以 per-ticker thread 不会损失 crawler 全局复用能力。

---

# 40. Node 2 Request 的运行输入

每次 Node 2 request 建议显式注入：

```text
ticker

Configuration Plan artifact
Plan version / identity

本轮 request context

message-bus-operations skill
crawler-plane-operations skill
crawler-delivery skill
```

不依赖 thread history 提供 Plan。

运行开始后再通过 service 查询：

```text
当前 Crawler Plane 状态
当前 Message Bus 状态
```

这些 runtime state 不应冻结进很早生成的静态 context。

---

# 41. Node 2 Tool / Capability 面

Node 2 最核心的 Agent tools 是：

```text
crawler_plane.get
crawler_plane.create_version
crawler_plane.live_probe
crawler_plane.certify
crawler_plane.promote
crawler_plane.register_source
crawler_plane.get_execution

monitoring.get_ticker_config
monitoring.update_ticker_config
monitoring.list_status
monitoring.recent_events
```

必要时通过当前受支持的 HTTP/application service 查询 Source Registry。

Node 2 不需要：

```text
crawler_plane.resolve_alert
crawler_plane.update_alert_policy
crawler_plane.add_regression
```

作为常规 delivery 主流程工具；这些主要属于 O4_REPAIR。

---

# 42. Node 2 开发阶段

## Phase 1 — Workflow Contract

确定：

```text
O4_DELIVER request
Configuration Plan handoff
Delivery Settlement
progressive work checkpoint
```

同时冻结：

```text
Node 1 什么 item 才进入 Node 2
Priority / alternative semantics
```

---

## Phase 2 — O4 shared infrastructure skills

根据已经落地的实现编写：

```text
message-bus-operations.md
crawler-plane-operations.md
```

重点不是复制 800 行 implementation reference，而是提炼：

> Agent 真正执行任务时需要知道的状态模型、工具顺序和高风险误用点。

---

## Phase 3 — `crawler-delivery.md`

实现本文描述的：

```text
worklist
priority
effort cycle
planned alternatives only
delivery stages
completion
failure
current constraints
human intervention
settlement
```

---

## Phase 4 — Codex Orchestrator

建立：

```text
O4 role
per-ticker persistent thread
O4_DELIVER request
GPT-5.6 Sol High
working volume access
required tool allowlist
```

Node 2 retry 恢复同一 ticker O4 thread，并从 progressive checkpoint 恢复 durable work state。

---

## Phase 5 — Reference Pilot

选择一个 ticker 的 Plan，至少包含：

```text
1 个简单 crawler
1 个需要一次 repair 的 crawler
1 个 primary 失败后切 alternative 的 crawler
```

验证 O4 能够：

```text
正确执行 effort policy
完成 live probe
构造三类 fixtures
通过 certification
promote
register source
bind ticker
产生 Settlement
```

---

## Phase 6 — Failure Pilot

故意构造：

```text
Primary source 无法交付
Alternative 1 也失败
Alternative 2 成功
```

确认：

```text
不会自行搜索 Alternative 3
```

再构造：

```text
所有 alternatives 失败
```

确认：

```text
返回 FAILED
```

而不是为了完成率随意寻找新 source。

---

## Phase 7 — Human Intervention Pilot

构造：

```text
目标网站必须登录
且系统没有 credential
```

确认 Agent 可以产生合法 intervention。

同时构造：

```text
403
selector failure
certification failure
```

确认 Agent 不会把这些技术问题推给人类。

---

# 43. Node 2 的最终验收标准

一个合格的 `O4_DELIVER` 应该满足：

```text
不会重新开展 Monitoring Planning

不会重新配置 existing-source items

不会自己扩大 Alternative 集合

不会把写完 crawler 当作任务完成

不会把 Certification PASS 当作 live-site proof

不会在 certify 后继续修改 working 再直接 promote

不会把 Crawler Asset 和 Registered Source 混淆

不会省略 ticker binding

不会因 update_ticker_config 参数替换语义破坏 binding

不会用 Human Intervention 逃避技术问题
```

同时它应该能够：

```text
按照 Priority 合理投入不同 effort

从真实 execution evidence 修复 crawler

逐 work item 前进而不是死磕一个 source

可靠交付：
crawler ACTIVE
→ Source Registered
→ ticker bound

在部分失败情况下保留已经完成的 durable stage

清晰结算原 Monitoring Plan

明确当前运行约束

只在真正 agent-unresolvable 的情况下要求人工动作
```

---

# 44. 最终形态

整个 Node 2 可以压缩成：

```text
           Monitoring Configuration Plan
                         │
                         ▼
                   O4_DELIVER
                         │
                 build worklist
                         │
                         ▼
                Planned Source A
                         │
                   implement
                         │
                    live probe
                         │
                   certification
                         │
             ┌───────────┴───────────┐
             │                       │
            PASS                    FAIL
             │                       │
             ▼                       ▼
          promote              evidence-backed
             │                   repair cycle
             ▼                       │
      register_source                │
             │                 effort exhausted?
             ▼                   │           │
       ticker binding             NO          YES
             │                   │            │
             ▼                   └──loop      ▼
         verify state                     next planned
             │                           alternative
             ▼                                │
         COMPLETE                     all exhausted
                                              │
                                              ▼
                                            FAILED

                         ↓
              Monitoring Delivery Settlement
              ├─ Plan completion
              ├─ Current constraints
              └─ Strict human intervention
```

这个节点真正的本质不是“爬虫开发 Agent”，而是：

> **把一个已经完成业务判断的 Monitoring Configuration Plan，有边界、有 effort、有证据地兑现成生产可用的 crawler monitoring capability。**

Crawler code 只是其中的一段；真正的完成状态是 **从 Plan Item 一直走到 ACTIVE Crawler → Registered Message Bus Source → ticker binding → Settlement 的完整交付链**。
