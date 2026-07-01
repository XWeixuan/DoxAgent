# DoxAgent 新文档体系与 Document 3: Persistent Operation 开发需求文档

> 适用性声明：本文档保留为 Document 3 生成阶段的历史设计说明，不再作为当前 Persistent Runtime Execution 阶段的开发依据。若本文与 `dev_plan/Persistent_Runtime_Execution_PRD.md` 存在冲突，以最新 Persistent Runtime Execution PRD 为准。本文已收敛旧表述中容易误导后续开发的运行时 O3、W2 分类、cache、Known Events 更新和 O3 预算边界。

## 1. 背景与目标

DoxAgent 当前需要从“Blackboard 初始化系统”进一步扩展为围绕单个 ticker 持续运行的事件驱动研究与交易 agent 系统。新的整体结构不再以一次性生成研究报告为终点，而是形成一套围绕 ticker 的长期工作文档体系，支持后续持续监测、新旧消息识别、运行时决策、交易意图生成、后台 agent 推送、ingest_queue / archive 归档处理以及未来交易复盘。

新的文档体系包含：

1. **Document 1: Global Research**
2. **Document 2: Expectation Units**
3. **Document 3: Persistent Operation**
4. **Trading Records**，后置新增，最后开发

其中，本轮开发重点是 **Document 3: Persistent Operation / 持久化运行板块**。

Document 3 的目标不是继续生成研究报告，而是把 Document 1 和 Document 2 中已经稳定下来的研究结论和预期单元，转化为后续系统可以持续运行的事件记忆、监测配置和执行规则。它应服务于后续低参数 LLM、Message Bus、后台 agent 调度和未来交易审计。

---

## 2. 新文档体系定义

### 2.1 Document 1: Global Research

Document 1 是 ticker 的全局投研底座。

它负责生成一个标的的全局研究背景，包括但不限于：

* 公司基本面
* 财务与估值
* 宏观 / 大盘环境
* 行业格局
* 供应链变量
* 竞争对手
* 市场叙事
* 行情追因
* 价格反映分析

Document 1 的核心作用是为后续预期拆解提供全局上下文，而不是直接输出交易策略。

Document 1 是 **by ticker 的全局文档**。每个 ticker 对应一份整体性的 Global Research，而不是按照 expectation unit 分裂成多个分支。

---

### 2.2 Document 2: Expectation Units

Document 2 是可交易预期单元层。

它负责把 Document 1 中的市场叙事、已兑现事实、价格反映、行业变量和市场分歧，拆解成少数几个可跟踪、可审查、可交易的 expectation units。

每个 expectation unit 需要明确：

* 市场正在交易什么预期
* 该预期方向是什么
* 哪些事实已经兑现
* 哪些事实已经被 price in
* 哪些变量仍未兑现
* 当前预期处于什么状态
* 后续需要监测什么事件或变量

Document 2 是当前体系中唯一会按 expectation unit 分支展开的文档。每个 expectation unit 进入稳定状态前，仍需经过字段补全、审查、objection 消除和委托完成等机制。

---

### 2.3 Document 3: Persistent Operation

Document 3 是持久化运行板块，是本轮新增和重构的核心。

Document 3 和 Document 1 一样，都是 **by ticker 的全局文档**。它不会像 Document 2 一样拆成多个 expectation unit 分支，而是由 agent 在阅读和理解多个 expectation units 的基础上，为该 ticker 生成一份全局运行方案。

Document 3 的任务是把多个 expectation units 汇总为后续持久化运行所需的三类内容：

1. **Known Events / 已知事件明细**
2. **Monitoring Config / 监测配置**
3. **Monitoring Execution Policy / 监测执行 Policy**

Document 3 不是单个 expectation unit 的运行方案，而是该 ticker 的整体运行方案。它需要综合考虑该 ticker 本身、相关行业、竞争对手、供应链、监管、宏观环境和市场风险偏好等信息。

因此，Document 3 的三个子板块都不能只局限于本 ticker 内部：

* Known Events 需要记录可能影响新旧消息判断的 ticker 内部、行业、宏观、监管、竞争对手等已知事件。
* Monitoring Config 需要根据 ticker、行业、供应链、竞争对手、监管和宏观变量配置监测项。
* Monitoring Execution Policy 需要结合 ticker 事件、行业事件、宏观事件和价格 / 技术面状态来制定运行时规则。

---

### 2.4 Trading Records

Trading Records 是后置新增的独立板块，最后开发。

它不是 Document 3 的内部子板块，而是一个单独的交易审计账本，用来记录 agent 每一笔交易相关行为和结果，包括：

* 触发交易的消息
* 命中的 policy
* 对应 expectation unit
* trade intent
* 实际交易动作，未来接入 broker 后
* 仓位
* 价格
* 盈亏
* 退出原因
* 复盘结论
* 是否符合原 policy
* agent 判断是否存在误判

Trading Records 的核心价值是让系统能够持续复盘和审计，而不是只记录“当时为什么买 / 卖”，却无法评估结果好坏。

Trading Records 不在本轮 Document 3 生成阶段开发范围内，但 Document 3 的 policy_id、trade intent、background agent task 等输出需要为运行时 Trading Records 预留可引用的结构。运行时是否进入 Trading Records、ingest_queue 或 archive，由 Persistent Runtime Execution 阶段的 Route Engine 决定。

---

## 3. Document 3 的总体设计原则

### 3.1 全局 by ticker 文档

Document 3 是 ticker 级全局文档，不按 expectation unit 单独生成多个版本。

它需要读取：

* Document 1: Global Research
* Document 2: 所有稳定后的 Expectation Units
* 当前 ticker 的相关行情信息
* Known Events 草案
* Monitoring Config 草案
* 行业 / 宏观 / 竞争对手 / 监管相关信息

Document 3 的内部字段可以引用多个 expectation_unit_id，但它本身不应被拆成多个 expectation unit 分支。

---

### 3.2 运行导向，不是研究报告导向

Document 3 的目标是服务后续运行，而不是继续写研究解释。

它应回答：

* 后续系统如何判断新消息是不是旧消息？
* 后续 Message Bus 应该监测什么？
* 新消息进入后应该触发 trade intent、background agent task，还是交由运行时 Route Engine 归档到 ingest_queue / archive？
* 哪些规则已经通过审查，可以被运行时使用？
* 哪些配置已经 apply 到 Message Bus？
* 哪些规则和配置未来可以被审计和回滚？

---

### 3.3 轻量 Brief State

Document 1 和 Document 2 的 Brief State 可以相对严格，因为它们是初始化研究和核心认知资产。

Document 3 是持久化运行文档，未来会更频繁更新，因此不能采用过重的 promote 机制。它仍然需要保留：

* draft
* review
* objection
* promote
* commit log
* versioning

但机制应更轻量。

---

### 3.4 配置生成与运行应用分离

Document 3 中的 Monitoring Config 可以生成与 Message Bus tool input schema 一致的配置草案，但不能在草稿阶段直接 apply 到 Message Bus。

必须遵循：

1. O2 生成 proposed monitoring config
2. 通过 schema validation
3. 通过资源预算检查
4. C1 / C3 审查
5. O2 解决 objection
6. promote 到 Document 3 Brief State
7. 再调用 Message Bus 配置 tool
8. 记录 applied_config_version

这可以避免草稿阶段的错误配置直接污染运行中的 Message Bus。

---

## 4. Agent 分工与边界

### 4.1 O2: 监测配置 Agent

O2 收缩为专职的 Monitoring Config agent。

O2 的核心职责是：

* 读取 Document 1 和 Document 2
* 理解所有稳定后的 expectation units
* 生成 ticker 级全局 Monitoring Config
* 将预期单元转化为 Message Bus 可执行监测项
* 配置 by ticker、by keyword、by entity、by source 等监测项
* 利用强化 web search / source discovery 能力查找具体可监测对象
* 在配置通过审查后，通过 tool calling 调用 Message Bus 配置工具

O2 需要具备强化的 web search / source discovery 能力。例如，当需要配置 X 账号监测时，应主动查找：

* 公司官方 X 账号
* 管理层账号
* 监管机构账号
* 行业媒体账号
* 政府项目页面
* RSS / feed

并将具体账号名、关键词、页面或 source identifier 写入配置草案。

O2 在 Monitoring Config 未通过审查前，不允许调用 Message Bus apply tool。

---

### 4.2 C1 / C3: 审查与补充

C1 / C3 不只是研究生成方，也需要参与 Monitoring Config 审查。

C1 重点审查：

* 公司基本面变量是否遗漏
* 财务 / 订单 / 客户 / 融资 / 产能等监测项是否合理
* Monitoring Config 是否忽略了影响公司预期兑现的重要内部变量

C3 重点审查：

* 行业变量是否遗漏
* 竞争对手是否遗漏
* 供应链变量是否遗漏
* 监管变量是否遗漏
* 宏观或产业政策是否需要进入监测配置
* by keyword 监测是否过宽或过窄

C1 / C3 可以提出 objection，O2 负责修订和解决。

---

### 4.3 O4: 行情追踪与 Monitoring Execution Policy Agent

O4 的职责扩大。

O4 不仅负责行情追踪、价格反映、技术面观察，还负责 Document 3 内部的 Monitoring Execution Policy。

O4 在该任务节点需要具备单独的交易策略相关 internal_skill，用来定义：

* 什么类型的新消息可生成 trade intent
* 什么类型的消息需要推送后台 agent
* 什么类型的消息应交由运行时 Route Engine 归档到 ingest_queue / archive
* 消息是否已经被价格充分反映
* 技术面是否支持事件方向
* 大盘 / 行业 / 宏观环境是否抵消或放大事件影响
* 什么情况下不能生成交易意图，只能降级为 escalation

O4 生成的 policy 需要由 O2 审查，重点检查：

* policy 是否覆盖 Monitoring Config 可能捕捉到的消息类型
* policy 是否与监测范围匹配
* policy 是否出现监测项无法触发、或触发规则无法被监测管线支持的问题
* policy 是否把应归档为 ingest_queue / archive 的消息误写成 direct trade

---

### 4.4 O3: 运行时值班专家 Agent

在 Document 3 生成阶段，O3 不参与主文档生成分工；但这不表示 O3 在后续系统中留空或禁用。

根据最新 `Persistent_Runtime_Execution_PRD.md`，O3 是持久化运行阶段新增的值班专家 agent，用于处理少数高价值、高不确定性或 policy 未覆盖的运行时消息，包括：

* 新 + Escalate to Background Agent
* 新 + NULL
* social 消息通过 A2 后的研判
* Known Events 运行时更新
* objection / objection_note 发起
* 运行时异常或 policy 漏洞的轻量修正建议

O3 必须是 bounded expert，而不是开放式 agent loop。运行时目标是在 2 分钟内给出可执行结果，最多两轮模型调用，最多一次并行工具调用。O3 超时不能阻断 trade 路径；如果消息已经处在 trade 判断链路中，应正常写入 Trading Records，并在 Trading Records 与执行异常日志中记录 O3 timeout 或失败信息。

---

### 4.5 A2: 事实核查 Agent

A2 保持轻量事实核查能力。

在 Document 3 中，A2 可被用于：

* Known Events 中具体事实不确定时的核验
* Monitoring Config 中 source discovery 结果不确定时的核验
* Monitoring Execution Policy 中某些前提事实不确定时的核验

A2 不负责生成主文档，只作为按需委托工具。

---

## 5. Document 3 的整体结构

Document 3 由三个子板块组成：

1. **A. Known Events / 已知事件明细**
2. **B. Monitoring Config / 监测配置**
3. **C. Monitoring Execution Policy / 监测执行 Policy**

Document 3 是全局 by ticker 文档，三个子板块共同构成该 ticker 的持久化运行方案。

推荐生成顺序为：

1. 先生成 Known Events
2. 再生成 Monitoring Config
3. 最后生成 Monitoring Execution Policy

---

# 6. A. Known Events / 已知事件明细

## 6.1 板块定位

Known Events 的目标不是 Realized Facts 的升级版。

它不是用来继续写投资解释，也不是用来总结“哪些事实已经兑现”，而是为持久化运行中的低参数 LLM 提供一份用于新旧消息判断的事件索引表。

它的核心作用是：

> 帮助低参数 LLM 在处理新进入队列的消息时，判断该消息是否已知、是否重复、是否只是旧事件的新进展，还是一个真正的新事件。

Known Events 更接近一个结构化事件记忆库，而不是叙事型总结。

---

## 6.2 覆盖范围

Known Events 不能只局限于本 ticker 内部。

它需要围绕该 ticker 的持久化运行，记录可能在后续消息中被反复引用、误判或重新炒作的已知事件，包括：

* ticker 本身已经发生的新闻事件
* 市场已讨论过的事实
* 已知但尚未发生的事件窗口，例如财报、发布会、监管节点、交付窗口
* 旧事件的重要阶段变化，例如审批进展、签约进展、交付进展、融资进展
* 竞争对手相关事件
* 行业层面已知事件
* 供应链相关事件
* 监管和政策事件
* 宏观变量相关事件
* 已经被市场关注但未必直接来自公司公告的事件
* 可能影响多个 expectation units 的背景事件

Known Events 的记录数量应明显多于 Document 2 中的 Realized Facts，颗粒度也应更细。

---

## 6.3 记录粒度

Known Events 的颗粒度原则是：

> 一个可能被后续新消息单独引用、重复报道或推进的事实，就应成为一条 Known Event。

不能把多个事件合并成一条投资总结。

例如，不应写成：

> 公司近期订单、交付、监管和竞争格局都出现积极变化。

而应拆成多条：

* 某订单已宣布
* 某订单金额尚未披露
* 某交付窗口已被管理层提及
* 某竞争对手已获得类似订单
* 某监管规则仍处于审议阶段

Known Events 的目标是让低参数 LLM 能够做逐条对照，而不是让它读一段综合分析后再自己推理。

---

## 6.4 单条记录结构

每条 Known Event 应尽量短句化、轻结构化，避免长段描述。

LLM-facing 的核心字段包括：

* `event_id`
* `event_time / event_window`
* `core_fact`
* `duplicate_detection_keys`

其中：

### `event_id`

唯一标识，用于后续引用、匹配、审计和运行时输出。

### `event_time / event_window`

事件发生时间或时间区间。

如果事件是已知但尚未发生的窗口，可以使用 event_window 表示，例如某季度、某月、某财报窗口、某监管时间范围。

### `core_fact`

用一句话描述该事件的核心事实。

要求：

* 简短
* 明确
* 可对照
* 不写投资解释
* 不写价格反应
* 不写长背景
* 不写推理链

### `duplicate_detection_keys`

用于判重的结构化关键字段集合。

它应同时包含：

1. 事件关键词 key
2. 数值 / 状态 key

不再拆分硬判重 key 和软匹配 key，因为 agent 很难稳定地区分二者。

---

## 6.5 duplicate_detection_keys 设计

duplicate_detection_keys 是 Known Events 的关键字段，用于辅助低参数 LLM 判断新消息是否命中旧事件。

它统一由两类 key 组成：

### 1. 事件关键词 key

包括：

* ticker
* 公司名称
* 相关主体
* 竞争对手
* 监管机构
* 政府部门
* 项目名称
* 产品名称
* 事件对象
* 常见别名
* 缩写
* 关键动作词，例如发布、签约、获批、延期、取消、交付、融资、收购、合作等

### 2. 数值 / 状态 key

包括：

* 金额
* 数量
* 阶段
* 状态
* 时间窗口
* 是否官方确认
* 是否已签约
* 是否已完成
* 是否仍在审批中
* 是否已延期
* 是否已取消
* 是否从传闻变成确认
* 是否从计划进入执行

duplicate_detection_keys 不需要展开推理，只需要帮助模型做事件匹配。

---

## 6.6 novelty 判断机制

Known Events 中不应为每条事件单独写 novelty rule。

novelty 判断应沉淀为低参数 LLM 或 O1/O4 使用的全局 internal_skill。

该 internal_skill 的核心判断逻辑包括：

* 新消息是否只是重复已有事实
* 新消息是否只是对旧事件的回顾或评论
* 新消息是否提供了新的金额
* 新消息是否提供了新的数量
* 新消息是否提供了新的时间节点
* 新消息是否体现状态变化
* 新消息是否涉及新的主体
* 新消息是否提供官方确认
* 新消息是否显示市场关注度显著变化
* 新消息是否说明旧事件进入新阶段
* 新消息是否足以从 old_duplicate 升级为 material_update 或 new_event

通过全局 novelty skill，避免在每条 Known Event 中重复写规则，提升一致性和可维护性。

---

## 6.7 第一版运行策略

第一版运行时可以先采用最简单直接的方式：

> 将完整 Known Events 列表和新进入队列的消息一起提供给低参数 LLM，由模型进行对照判断。

暂时不引入候选事件检索和 Top-K 裁剪。

只有在真实测试中出现以下问题时，再考虑优化：

* Known Events 列表过长
* 上下文成本过高
* 低参数 LLM 处理能力下降
* 新旧识别准确率下降
* 模型被过多无关 Known Events 干扰

---

## 6.8 Known Events 验收标准

Known Events 板块完成后，应满足：

* 能覆盖 ticker 内部、行业、宏观、竞争对手、监管等相关已知事件
* 记录数量足够支持新旧消息判断
* 单条记录足够短，适合低参数 LLM 对照
* 每条记录具有 event_id
* 每条记录具有 event_time / event_window
* 每条记录具有 core_fact
* 每条记录具有 duplicate_detection_keys
* duplicate_detection_keys 同时包含事件关键词 key 和数值 / 状态 key
* 不在每条事件中单独写 novelty rule
* novelty 判断由全局 internal_skill 完成
* 第一版支持完整 Known Events 列表输入低参数 LLM
* 不把 Known Events 写成 Realized Facts 的升级版

---

## 6.9 运行时 Known Events 更新边界

Document 3 生成阶段仍按草案、校验、审查、promote 的流程产生初始 Known Events。

但在 Persistent Runtime Execution 阶段，O3 可以直接更新 Known Events，无需重新走初始化阶段的审查流程即可生效。该能力用于支持持续运行中的新旧识别，尤其是新事件、旧事件 material_update、已进入 Trading Records 的 Direct Trade Candidate 后续去重等场景。

运行时直接更新必须保持轻量审计，至少记录：

* `known_event_id`
* `source_ref`
* `change_reason`
* `changed_at`

O3 的 Known Events 更新不等同于重写 Document 3，也不应触发开放式研究循环。

---

# 7. B. Monitoring Config / 监测配置

## 7.1 板块定位

Monitoring Config 是 Document 3 中将稳定后的 Global Research 和 Expectation Units 转化为 Message Bus 可执行监测管线配置的板块。

它的目标是回答：

> Message Bus 接下来应该监测什么？

Monitoring Config 的职责只包括：

* 监测什么
* 通过什么 channel 监测
* 用什么 ticker / keyword / entity / source identifier 监测
* 监测优先级如何
* 监测频率如何
* 该监测项服务于哪个 expectation unit 或全局风险变量
* 为什么需要这个监测项

---

## 7.2 输出结构原则

Monitoring Config 应尽量与后续调用 Message Bus 配置 tool 的 input schema 保持一致。

不应再设计一套额外文档字段，然后再做复杂映射。

推荐结构是：

* `tool_input`
* `reasoning`

其中：

### `tool_input`

必须与后续调用 Message Bus 配置工具时所需的 input schema 一致。

真正 apply 到 Message Bus 时，应主要使用 `tool_input`。

### `reasoning`

每个配置项额外附加一个简短 reasoning 字段，用于说明：

* 为什么需要这个监测项
* 它服务于哪个 expectation unit
* 它服务于哪个行业 / 宏观 / 竞争对手 / 监管变量
* 它可能捕捉什么类型的增量消息

reasoning 不应写成长篇推理，只需要一句简短解释。

---

## 7.3 覆盖范围

Monitoring Config 不能只局限于本 ticker 内部。

长期目标为可根据 Document 1 和 Document 2，综合配置以下类型的监测项：

* ticker 本身
* 公司官方渠道
* 管理层
* 投资者关系页面
* SEC / 监管文件
* 公司新闻
* Stocktwits 等 by ticker 社媒数据
* X / Twitter 账号或搜索
* 政府部门
* 监管机构
* 行业媒体
* 关键记者 / KOL
* 行业关键词
* 宏观关键词
* 政策关键词
* 政府订单关键词
* 产品 / 项目关键词
* 新闻 RSS / news search
* 其他可被 Message Bus 支持的数据源

暂时的短期开发目标为，可调用monitoring tools正常配置对应的监测管线。

---

## 7.4 O2 的 web search / source discovery 能力

O2 应被设计为强化 web search / source discovery 能力的监测配置 agent。

它不能只根据已有文档泛泛写“监测公司官方账号”或“监测行业媒体”，而应主动查找具体可监测对象。

例如，当需要配置 X 账号监测时，O2 应能够查找并确认：

* 公司官方 X 账号
* CEO / CFO / 管理层账号

当需要配置网页、RSS 或政府项目页面时，O2 应能够查找并确认：

* 页面名称
* URL 或 source identifier
* 所属机构
* 监测价值
* 对应 expectation unit 或全局变量

O2 的 source discovery 结果应写入 proposed monitoring config 中，供 C1 / C3 审查。

---

## 7.5 by ticker 与 by keyword 配置

Monitoring Config 应支持至少两类主要配置：

### 1. by ticker 监测

### 2. by keyword / entity 监测

by keyword 监测需要格外控制资源消耗，不能让 agent 无限扩张关键词，挤占 Message Bus 资源。具体参考docs/monitoring-message-bus.md。

---

## 7.6 Monitoring Config 流程

Monitoring Config 的流程应为：

1. O2 读取 Document 1、Document 2 和 Known Events
2. O2 进行必要的 web search / source discovery
3. O2 生成 proposed monitoring config
4. 系统进行 schema validation
5. 系统进行资源预算检查
6. C1 / C3 进行审查
7. 如有 objection，O2 修订
8. 所有阻塞 objection 消除后，promote 到 Document 3 Brief State
9. O2 调用 Message Bus 配置 tool
10. 记录 applied_config_version
11. 写入 commit log，支持审计和回滚

---

## 7.7 Monitoring Config 验收标准

Monitoring Config 完成后，应满足：

* 输出结构与 Message Bus 配置 tool 的 input schema 一致
* 每个配置项附带简短 reasoning
* 能覆盖 ticker、行业、宏观、供应链、监管等必要监测项
* 支持 by ticker 监测
* 支持 by keyword / entity 监测
* O2 具备 web search / source discovery 能力
* 能发现具体账号、页面、source identifier，而不是只写泛泛描述
* by keyword 配置有资源上限
* 能合并近义词和删除低信号关键词
* 通过 schema validation
* 通过资源预算检查
* 通过 C1 / C3 审查
* 未通过审查前不能 apply 到 Message Bus
* apply 后记录 applied_config_version
* 具备 commit log 和回滚依据

---

# 8. C. Monitoring Execution Policy / 监测执行 Policy

## 8.1 板块定位

Monitoring Execution Policy 是 Document 3 中用于把“新进入消息的识别结果”转化为运行时动作的规则板块。

它的目标是回答：

> 当 Message Bus 捕捉到一条新消息后，系统应该如何处理？

可能结果包括：

* 生成 trade intent
* 推送 background agent 进一步判断
* 交由运行时 Route Engine 归档到 ingest_queue 或 archive

Monitoring Execution Policy 由 O4 负责生成，由 O2 审查。

O4 在生成 policy 时，应假设自己真实地在执行交易，因此 policy 不能写成泛泛的分析建议，而要写成可触发、可审查、可复盘的运行规则。

但第一阶段不接实盘 broker，所以 policy 的输出不是实际订单。

---

## 8.2 覆盖范围

Monitoring Execution Policy 不能只局限于 ticker 内部消息。

它需要结合：

* ticker 自身事件
* 行业事件
* 竞争对手事件
* 供应链事件
* 监管事件
* 宏观事件
* 大盘风险偏好
* 板块 beta
* 价格反映
* 技术面状态
* expectation units 当前状态

例如：

* 竞争对手获得重大订单，可能削弱本 ticker 的某个预期
* 行业监管放松，可能强化多个 ticker 的行业 beta
* 宏观 risk-off，可能抵消个股利好
* 行业价格战，可能改变原本 bullish expectation 的兑现概率
* 供应链延迟，可能影响产品交付预期
* 大盘流动性变化，可能影响事件驱动 trade intent 的强度

---

## 8.3 Policy 类型

每条 policy 是一个最小规则单元。

Document 3 中的 policy 只定义可被运行时 W2 命中的正向规则。policy_type 统一分为两类：

1. **Direct Trade Candidate**
2. **Escalate to Background Agent**

运行时 W2 的输出类型则为：

1. **Direct Trade Candidate**
2. **Escalate to Background Agent**
3. **NULL**
4. **Irrelevant**

其中，NULL 表示“相关但未命中 policy”，Irrelevant 表示“误召回 / 低相关 / 低质量”。NULL 和 Irrelevant 都不是 Document 3 policy_type。cache 不再作为 policy 类型或 W2 输出类型，而是 Route Engine 在 W1 / W2 / A2 / O3 之后执行的后续归档结果。

### 1. Direct Trade Candidate

用于定义在什么消息条件下可以生成 trade intent。

注意：这里不是直接下真实订单，而是在假设真实交易环境下生成交易意图。

输出应是：

* side
* conviction
* size_bucket
* reasoning
* risk_guard 检查后的 trade intent

### 2. Escalate to Background Agent

用于定义哪些重要但不够确定的消息需要推送后台 agent 进一步判断。

例如：

* 事件重要但事实不清楚
* 事件可能改变 expectation unit，但需要 C1 / C3 / O1 重新判断
* 事件影响方向不明确
* 事件与价格表现冲突
* 宏观或行业背景需要进一步解释
* 需要 A2 做事实核查

### 3. NULL / Irrelevant 的运行时含义

NULL 不是 cache。它表示消息与 ticker、expectation 或当前监测目标相关，但未被 Document 3 的 Monitoring Execution Policy 覆盖。NULL 通常应进入 O3，由 O3 判断是否重大、是否 price in、是否需要更新 blackboard、Known Events 或产生 objection / objection_note。

Irrelevant 也不是 NULL。它表示消息属于误召回、低相关、低质量，或不服务于当前 ticker 运行目标。Irrelevant 通常由 Route Engine 进入 archive。

旧消息重复、已知事件回顾、低价值消息、弱相关消息、社媒重复转发等不再通过 `cache` policy 表达，而应由 W1 / W2 / A2 / O3 的结构化结果交给 Route Engine 归档为 ingest_queue 或 archive。

---

## 8.4 Policy 基础字段

三类 policy 共用同一套基础字段：

* `policy_id`
* `policy_type`
* `scope`
* `trigger`
* `confirmation`
* `action`
* `risk_guard`
* `reasoning`

字段应保持精简，避免第一版过度复杂。

---

## 8.5 字段说明

### `policy_id`

唯一标识，用于后续审计、运行时命中、trade intent、background task 和未来 Trading Records 引用。

### `policy_type`

枚举值：

* `direct_trade`
* `escalate`

### `scope`

用于绑定该 policy 的适用范围。

scope 应包括：

* 相关 expectation_unit
* event_type
* 必要时可包括 ticker / industry / macro / competitor / supply_chain 等范围

scope 的作用是避免 policy 泛化成：

* 所有利好都买
* 所有利空都卖
* 所有监管消息都推送
* 所有重复消息都作为 policy 命中

policy 必须绑定明确的预期单元或全局运行变量。

### `trigger`

trigger 只描述消息内容本身满足什么条件会命中该 policy。

trigger 可以包含：

* 是否出现新的金额
* 是否出现新的数量
* 是否出现新的状态变化
* 是否出现新的主体
* 是否出现官方确认
* 是否出现关键事件推进
* 是否与某个 expectation unit 直接相关
* 是否与行业 / 宏观 / 竞争对手变量相关

### `confirmation`

confirmation 用于补充 O4 的价格与技术面判断。

它说明消息命中 trigger 后，还需要满足什么市场确认条件，才能生成 trade intent 或决定是否降级为 escalation。

confirmation 可以包括：

* 价格是否尚未充分反映
* 是否已经明显 price in
* 是否出现异常成交量
* 是否出现明显反向走势
* 是否被大盘风险偏好抵消
* 是否被行业 beta 放大或削弱
* 是否与技术面趋势冲突
* 是否需要等待 O4 的价格 / 技术面判断
* 是否存在消息方向与价格反应不一致的情况

confirmation 不需要写成完整风控系统，只需要表达运行时所需的关键市场确认条件。但是不允许写得严苛，以导致卡掉任何潜在交易。

### `action`

action 根据 policy_type 不同而变化。

#### direct_trade 的 action

应包括：

* `side`
* `conviction`
* `size_bucket`

其中：

* side 表示 long、short、exit 方向
* conviction 表示 low、medium、high 等信心强度
* size_bucket 表示 small、normal、aggressive 等动作大小

第一阶段不要求 agent 输出具体仓位百分比，也不生成真实订单。

#### escalate 的 action

应包括：

* `send_to`
* `question`
* `priority`

其中：

* send_to 可以是 O1、O4、C1、C3、A2 等
* question 用来明确后台 agent 要回答什么问题
* priority 用来表示任务优先级

#### 归档类处理

Document 3 policy 不再定义 `cache` action，也不再输出 `cache_label` / `handling` 作为 policy 字段。

运行时归档只保留两层：

* `ingest_queue`：后续仍可被 DoxAtlas 或投研 agent 消费。
* `archive`：只留审计痕迹，不进入后续分析消费。

具体进入哪一层，由 Persistent Runtime Execution 阶段的 Route Engine 根据 W1、W2、A2、O3 的结构化结果决定。

### `risk_guard`

risk_guard 只保留策略级风险约束，不做完整交易风控系统。

它应说明什么情况下不能生成 trade intent，或只能降级为 escalation。

例如：

* 消息被判定为旧消息
* 事件被否认
* 事件与 expectation unit 的关系不成立
* 价格已经明显提前反映
* 消息方向与价格反应严重冲突
* 大盘风险环境与事件方向严重冲突
* 行业环境抵消个股事件影响
* 事件事实不确定
* 事件只是传闻
* 事件缺少关键金额 / 数量 / 状态变化
* 该事件不在 Monitoring Config 可覆盖范围内

### `reasoning`

reasoning 只保留一句简短解释，用于说明该 policy 为什么存在、服务于哪个 expectation unit 或全局运行变量。

不应展开长篇投资推理。

---

## 8.6 不应作为 policy 字段的内容

### 1. 不写真实订单字段

第一版不需要：

* broker_order_type
* limit_price
* stop_loss
* take_profit
* exact_position_size
* account_id
* order_id

这些属于未来交易执行模块，不属于当前 Document 3。

---

## 8.7 Monitoring Execution Policy 流程

Monitoring Execution Policy 的流程应为：

1. O4 读取 Document 1
2. O4 读取所有 Document 2 Expectation Units
3. O4 读取 Known Events
4. O4 读取 Monitoring Config
5. O4 结合价格、技术面、行业和宏观状态
6. O4 通过交易策略 internal_skill 生成 proposed execution policies
7. 系统进行结构校验
8. O2 审查 policy 是否与 Monitoring Config 匹配
9. 如有 objection，O4 修订
10. 阻塞 objection 消除后，promote 到 Document 3 Brief State
11. 运行时低参数 LLM 使用这些 policies 做动作决策

---

## 8.8 运行时使用方式

运行时新消息进入队列后，低参数 LLM 应按以下顺序处理：

1. 读取新消息
2. 读取完整 Known Events
3. 判断新消息是 old_duplicate、known_event_recap、material_update 还是 new_event
4. 判断事件类型
5. 读取 Monitoring Execution Policies
6. 匹配对应 policy
7. 根据 policy 命中结果输出 W2 type：

   * Direct Trade Candidate
   * Escalate to Background Agent
   * NULL
   * Irrelevant

运行时输出不应直接变成真实订单。Direct Trade Candidate 只表示可进入 Trading Records 的 trade intent；Escalate to Background Agent 和 NULL 通常进入 O3；Irrelevant 通常进入 archive。ingest_queue / archive 是 Route Engine 的后续归档结果，不是 W2 policy_type。

---

## 8.9 Monitoring Execution Policy 验收标准

Monitoring Execution Policy 完成后，应满足：

* 由 O4 生成
* 由 O2 审查
* O4 假设真实交易环境生成 policy
* 第一阶段不接实盘 broker
* 输出可供 W2 命中的 Direct Trade Candidate 或 Escalate to Background Agent 规则
* policy_type 仅包含 direct_trade、escalate
* W2 运行时输出类型仅包含 Direct Trade Candidate、Escalate to Background Agent、NULL、Irrelevant
* NULL 表示相关但未命中 policy
* Irrelevant 表示误召回、低相关或低质量
* cache 不作为 policy_type，也不作为 W2 输出类型
* 基础字段包含 policy_id、policy_type、scope、trigger、confirmation、action、risk_guard、reasoning
* 不包含时间字段
* 不包含 source_condition 字段
* source 可信度相关规则写入低参数 LLM system prompt
* direct_trade action 包含 side、conviction、size_bucket
* escalate action 包含 send_to、question、priority
* 不包含 cache action、cache_label 或 handling 字段
* ingest_queue / archive 由运行时 Route Engine 根据 W1 / W2 / A2 / O3 结果决定
* risk_guard 只保留策略级风险约束
* 能结合 ticker、行业、宏观、竞争对手、供应链、监管等消息
* 能结合价格和技术面信息
* 能与 Monitoring Config 的监测范围匹配
* 不生成真实 broker order

---

# 9. Document 3 Brief State 与版本机制

## 9.1 状态流转

Document 3 建议采用轻量状态流转：

1. `draft`
2. `proposed`
3. `reviewed`
4. `brief_state`
5. `applied_runtime_state`，仅 Monitoring Config 需要

其中：

### draft

agent 初始生成内容。

### proposed

通过基础结构校验，准备进入审查。

### reviewed

相关 reviewer 已审查，并给出意见。

### brief_state

已通过必要审查，无阻塞 objection，可以作为稳定版本供运行时使用。

### applied_runtime_state

仅用于 Monitoring Config，表示该版本已经被实际 apply 到 Message Bus。

---

## 9.2 Commit Log

Document 3 的所有关键变更都应写入 commit log。

commit log 至少记录：

* 修改时间
* 修改发起 agent
* 修改的子板块
* 修改前后版本
* 修改原因
* 涉及的 expectation units
* 涉及的 known_event_id / monitor_config_item / policy_id
* 审查结果
* objection 处理情况
* 是否进入 brief_state
* 是否 apply 到 runtime
* applied_config_version，若适用

---

## 9.3 Objection 机制

Document 3 的 objection 机制应轻量化。

阻塞型 objection 包括：

* Monitoring Config 不符合 tool input schema
* Monitoring Config 超出 keyword 预算
* Monitoring Config 缺少关键监测对象
* Monitoring Config 包含明显低信号 / 过宽配置
* Monitoring Execution Policy 与 Monitoring Config 不匹配
* Policy 可能把旧消息误判成 direct trade
* Policy 缺少必要 risk_guard
* Known Events 写成了长篇研究解释
* Known Events 无法支持新旧消息判断
* 明显遗漏关键行业 / 宏观 / 竞争对手事件

非阻塞建议可以进入 commit log，但不应阻止整个 Document 3 promote。

---

# 10. 整体生成流程

Document 3 的整体生成流程为：

1. 确认 Document 1 已稳定
2. 确认 Document 2 的 expectation units 已稳定
3. O1 生成 Known Events 草案
4. 系统校验 Known Events 是否短句化、结构化、适合新旧消息判断
5. O2 读取 Document 1、Document 2、Known Events
6. O2 进行 web search / source discovery
7. O2 生成 Monitoring Config 草案
8. 系统执行 schema validation 和资源预算检查
9. C1 / C3 审查 Monitoring Config
10. O2 解决 objection
11. Monitoring Config promote 到 Document 3 Brief State
12. O2 调用 Message Bus 配置 tool
13. 记录 applied_config_version
14. O4 读取 Document 1、Document 2、Known Events、Monitoring Config
15. O4 结合价格 / 技术面 / 行业 / 宏观信息生成 Monitoring Execution Policy
16. 系统结构校验
17. O2 审查 policy 与 Monitoring Config 是否匹配
18. O4 解决 objection
19. Monitoring Execution Policy promote 到 Document 3 Brief State
20. Document 3 作为 ticker 级全局运行文档进入可用状态

---

# 11. 第一阶段开发范围

第一阶段需要实现：

* Document 3 的整体数据结构
* Document 3 by ticker 全局文档机制
* Known Events 子板块
* Monitoring Config 子板块
* Monitoring Execution Policy 子板块
* O2 Monitoring Config agent 能力
* O2 web search / source discovery 能力
* O4 Monitoring Execution Policy agent 能力
* C1 / C3 对 Monitoring Config 的审查
* O2 对 Monitoring Execution Policy 的审查
* Document 3 轻量 Brief State
* Document 3 commit log
* Monitoring Config schema validation
* by keyword 资源预算检查
* Monitoring Config apply 前审查
* Message Bus apply 后 applied_config_version 记录
* 运行时低参数 LLM 读取 Known Events 和 Policies 的基础接口
* Direct Trade Candidate / Escalate to Background Agent / NULL / Irrelevant 四类 W2 运行时输出

---

# 12. 总体验收标准

本轮开发完成后，系统应能够实现：

1. 每个 ticker 生成一份全局 Document 3，而不是按 expectation unit 分裂成多个文档。
2. Document 3 能读取并综合 Document 1 和 Document 2 的结果。
3. Document 3 能同时考虑 ticker 内部、行业、宏观、竞争对手、供应链、监管等相关信息。
4. Known Events 能作为低参数 LLM 的事件记忆索引，支持新旧消息判断。
5. Monitoring Config 能生成与 Message Bus tool input schema 一致的配置草案。
6. Monitoring Config 每项有简短 reasoning。
7. O2 能通过 web search / source discovery 找到具体可监测对象。
8. by keyword 配置具备资源上限和质量约束。
9. Monitoring Config 通过 C1 / C3 审查后才能 apply。
10. Message Bus apply 后记录 applied_config_version。
11. Monitoring Execution Policy 由 O4 生成，由 O2 审查。
12. Policy 分为 direct_trade、escalate 两类；cache 不再作为 policy 类型。
13. Policy 字段保持精简，不包含时间字段和 source_condition。
14. Source 可信度相关规则进入低参数 LLM system prompt，而不是每条 policy 字段。
15. Direct Trade Candidate 输出 trade intent，而不是真实订单。
16. Escalate 输出 background agent task。
17. W2 可输出 NULL 或 Irrelevant；NULL 表示相关但未命中 policy，Irrelevant 表示误召回、低相关或低质量。
18. Route Engine 将后续归档收敛为 ingest_queue / archive 两层；ingest_queue 可供后续 DoxAtlas 或投研 agent 消费，archive 只留审计。
19. Document 3 采用轻量 Brief State，不使用过重 promote 机制。
20. 所有关键变更进入 commit log，支持审计和回滚。
21. Trading Records 作为后置独立模块预留引用接口；Persistent Runtime Execution 阶段实现最小 trade intent 记录能力。
22. 持久化运行阶段 O3 可以直接更新 Known Events，且必须保留最小审计字段。
