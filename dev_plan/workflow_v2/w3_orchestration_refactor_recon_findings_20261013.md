# W3 六项迭代需求代码勘察发现

日期：2026-10-13（北京时间）。本轮交付为代码勘察，不实施重构、迁移、部署、会话删除或历史清理。

本次重构涉及 W3 输入投影、两阶段输出合同、Codex Worker 会话接口、Runtime 持久化和恢复、研究资产派生、消息来源字段及 Read/API/前端。现有实现可以复用消息快判、发布资产读取、交易输出事务和执行回执，但不能仅通过修改 Skill 或在现有 runner 中增加一次调用完成六项需求。

## 1. 需求依据与当前基线

### 1.1 依据

- [用户六项需求原文](/C:/Users/WEIXUANXIE/.codex/attachments/a0c1eaa1-edc6-4931-8ab8-d1fd4e72d585/已粘贴的文本.txt)：Template/Fork/关联 Resume、研究资产切片、72 小时交易意图继承、PIN 与 WATCH 容量、信源名称、交易优先双阶段。
- [W3 user prompt 字段删减方案](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_user_prompt_field_reduction_plan_20261013.md)：是本次迭代前的输入删减基线，不是最终目标 schema。新增能力仍须加入其必需的业务字段。
- 用户本轮明确限定：彻底勘察代码、确认相关文件和约束、输出发现 Markdown；不在本轮执行需求实现。
- 用户 AGENTS 指令要求重要代码修改追加 changelog。本轮只新增本文，不修改代码，也不覆盖已经被其他工作修改的 changelog。
- 前端适用 [frontend/v2/AGENTS.md](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/AGENTS.md)：维持 V2 独立合同和紧凑业务展示，不增加无业务用途的说明组件；实现期需要按其要求验证桌面页面。

### 1.2 本地与远端不能用 Git HEAD 简单等同

本地 HEAD 为 `dfc202b195eb2d42aea6dc58aaeb05b829344a5d`，工作区已有大量未提交改动，涉及生产采纳、消息源修复、Read/API、W3 prompt 和 changelog。本报告以当前工作区实际文件为代码基线，不能用 HEAD 的干净版本替代。

本轮通过只读 SSH 复核正式 Scheduler/Worker，远端仓库 HEAD 仍为 `b0f1caf50060b34483f13020a9d173ef57933afa`，但三个关键容器文件与本地当前文件的 SHA-256 完全相同：

| 文件 | SHA-256 |
| --- | --- |
| `persistent_runtime_v2/w3.py` | `ef5046729277b0a8da5ef65d03dcbe56f0e5357c04b2899d069b359adf0f2357` |
| `persistent_runtime_v2/schema.py` | `34c09a2edad545684dc5f29f5e0fe9ef52e0463cd001630f647ca602e820e918` |
| `codex_worker/sdk_runtime.py` | `7dc2df0e780e50d9a8bd14eb1056d69e1bba04159c2f7c4fe7ff89de7aeed172` |

这说明前一轮勘察中的“生产还是旧版 W3/Worker”已不能直接沿用；仅上述三个文件确认一致，不代表全部源文件、prompt 资产、数据库存量执行包均一致。冻结 Case 继续使用自己的 execution bundle、prompt 和输出合同，新代码不会自动改写存量 Case。

### 1.3 六项需求现状

| 需求 | 当前已有 | 主要缺口 |
| --- | --- | --- |
| 1. Template/Fork/Resume/48h 生命周期 | MAIN/FALLBACK、thread ID、临时 slot、恢复失败回执 | Template 初始化、实际 Fork 封装、Policy/Event 关联路由、最近接收任务时间、48h TTL、每日回收、母会话排除规则 |
| 2. 原文/索引/切片 | 可读完整 D1/D2/PolicySet；已有 D1 章节和 D2 对象结构 | 面向 W3 的统一派生索引、章节/Unit/Policy 文件、版本同步、v2.1 Unit 标识适配、v3 Policy 的 Shell 归属 |
| 3. 72h 交易意图上下文 | trade_intents、TradeRecord、delivery receipts、执行/成交账本 | 同 ticker 全来源意图查询、精简业务投影、执行结果关联、注入与冻结时间语义 |
| 4. PIN 与容量 | Policy WATCH、到期筛选、SET/CANCEL、READY 撤销 | PIN 全套；全 ticker WATCH 状态；WATCH/PIN 各 2 个的原子容量管理 |
| 5. 信源名称 | Bus 已有 source/publisher_name/resolved_domain | 发布时规范化语义、Runtime snapshot 传递、模型输入、展示/导出全链路一致 |
| 6. 双阶段及平行交易路径 | 单 turn 完整结果、Policy Condition 选择、Expert Trade、既有执行链路 | 阶段一合同、阶段二合同、分阶段幂等及恢复、交易先落账、同线程连续性、运营失败隔离、互斥交易选择 |

## 2. 当前端到端执行链

```text
RawMessageInput → RawMessage → StandardMessage → MaterializedStreamItem
  → SourceMessageEnvelope / SourceMessageSnapshot
  → W1 R1/R2 与 W2 R1/R2；必要时 W2 R3
  → Router + 当前 Policy WATCH follow-up
  → RuntimeEffect(route_to_w3)
  → acquire_w3_slot(ticker@execution_bundle)
  → PublishedW3ContextProvider.load(version pin)
  → 写 AGENTS、Skill、task、完整 D1/D2/PolicySet/Reference View/schema
  → CodexW3AgentRunner → ReceiptWorker → WorkerRunRequest
  → Worker Job/Capsule → thread_start 或 thread_resume → 一个 turn
  → parse_w3_result + 语义校验 → 保存完整 W3 result
  → 分配 provisional facts → 保存 Policy/Expert 交易 → coverage gap → WATCH
  → W3 RESOLVED、effect COMPLETED、Case completion → Read/API/前端
```

主要入口：[service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py:1473)、[w3.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:350)、[router.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/router.py:67)、[Worker runtime](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/sdk_runtime.py:444)。

当前第一轮标签不是即时发单凭证。Router 的现行代码已将满足相应条件的消息交给 W3；旧的 FIRST_ROUND_TRADE effect 也存在重定向到 W3 的兼容分支。实施时不能依据旧方案描述重新恢复未经本次 W3 决策的快判发单。

当前工具能力也已变化：W3请求仍为 `read_only=True`、`data_mcp_enabled=False`、不允许subagents；已核对的Worker在这种组合下关闭Web Search及配置中的Source Capture。Skill仍描述Search A/B，已有测试名称仍称“有web search skill”，但这些不能证明实际工具可用。六项重构没有明确要求改工具权限，实施前应把这项不一致作为独立约束核实，不能沿用10月12日旧样本中的live搜索能力。容器隔离时SDK sandbox使用full_access，`read_only`也不等价于操作系统层面的工作区只读。

## 3. 字段删减基线如何承接

### 3.1 当前状态

[w3.py task 组装](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:417) 仍有 18 个顶层字段：Case ID、mode、ticker、时间边界、完整 source envelope、W1、两份 W2、alignment、tags、编排版本、route reason、usage、watch、版本 pin、D2 发布状态。task 写文件并再次拼进 prompt，外层仍要求读取同一 task 以及四份完整公共上下文。

删减方案尚未在这段组装代码落实。后续必须先把“模型输入投影”与“后端 Runtime 数据”分开，不能直接删 SourceMessageEnvelope、RuntimeCase 或数据库字段来实现 prompt 瘦身。

### 3.2 必须继承的原则

| 业务部分 | 删减后的基线要求 | 新需求对它的扩充 |
| --- | --- | --- |
| 当前消息 | title/body/真实发布时间/url；不带采集流水包装 | 加统一信源名称；为 PIN 操作保留后端当前消息身份绑定 |
| W1 | NEW/OLD、confidence、完整 reason；展开 Event 时间/summary/必要事实 | 为 Event→Thread 复用保留后端身份，不能让模型凭标题生成关联 |
| W2 | 唯一完整判定；区分 HIT/RELATED；附 Policy 和完整 Condition 内容 | Policy→Thread 选择使用其有序业务归因，不能按 confidence 排除 |
| alignment | 一至两个聚合布尔；不运行时不伪造 false | 不把聚合布尔作为每个 Condition 的交易授权 |
| Policy usage | 正常状态、按方向消费、executable_directions 均保留 | 第一阶段输出后仍由执行层做当下有效性及原子消费校验 |
| WATCH | 保留原因、开始语义日、到期时间 | 增加全 ticker 当前状态及容量；不是只有相关 follow-up |
| 公共研究 | 描述为名字和截止时间；完整正文始终可读 | 原文、索引、切片入口，不要求每次全文读取 |
| Case/版本流水 | 不进入模型可见业务 payload | 阶段关联、thread/phase、幂等、版本 pin 继续留后端 |
| first_round_tags | 整体改造待定，不按删减方案擅删 | 六项需求没有给出最终标签体系，本轮不补造体系 |
| 新历史上下文 | 当前没有 | 72h 所有既有交易意图的规定业务内容；有效 PIN 的规定内容 |

W1/W2 当前消息的完整 reason 与“历史意图中 W1/W2 只给最简判定”是不同投影，不能复用同一极简函数把当前判断依据也删掉。历史 W3 reasoning 指交付的业务理由、Prior/Delta 等，不是底层隐藏推理或完整 Codex Session。

当前模型输出必须回填 `w3_case_id`，runner 也做精确相等校验。删掉输入 ID 后需要同步调整模型输出合同并由后端关联；仅删除 task 中的值会造成现有输出要求无法完成。路径中的流水 ID也要与“模型无需原样回填 ID”的设计区分处理。

发布时间精度不能随字段瘦身丢失。现有Bus/Runtime的 `published_at`为datetime，`publication_time_basis`另存DATE/EXACT/UNKNOWN_FIRST_SEEN语义；仅删除basis后照常序列化datetime会把日期/未知首见时间伪装成精确发布时间。应在输入投影中保留真实日期/时间/未知表示，并保持cutoff与消息发布时间不同义；无需增加新的阻塞校验。

## 4. 需求一：Thread 生命周期与关联复用

### 4.1 当前 MAIN/FALLBACK 的真实行为

[repository.py.acquire_w3_slot](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/repository.py:1281) 只按 ticker 绑定 main_thread_id/main_case_id；service 传入的是 `ticker@execution_bundle前16位`。MAIN 空闲就恢复旧 thread，繁忙就返回 FALLBACK 且 thread_id=None，从而新建线程。

- 当前不存在 Template，也没有 parent thread ID、关联 Policy/Event、last_received_at、子会话 expires_at。
- slot 默认租约是 1200 秒的运行占用回收，不是业务 Thread 的 48 小时寿命。
- release 仅清占用并保留 MAIN ID；FALLBACK 的 ID虽保存在 W3Case，却没有供下个相关 Case 查找的关联索引。
- `max_concurrency` 参数在 SQLite 实现中被忽略；已有测试明确不按 ticker 固定容量门控。
- context cache 是版本锁定的 Python LRU 缓存，不是模型已预读背景的 Template。

### 4.2 SDK 能力与工程缺口

[WorkerRunRequest](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/schema.py:50) 只有 `thread_id`；[CodexWorkerClient](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_runtime/client.py:27) 只封装 run/cancel。SDK runner 只实现 start/resume。

本轮只读检查生产安装的 AsyncCodex：有 `thread_fork(thread_id, cwd=..., config=..., model=..., ...)` 和 `thread_archive(thread_id)`，未见同级 `thread_delete` 方法。没有调用这些方法，没有新建或删除会话。

官方文档确认 Fork 复制存储的历史为新 thread ID；archive 只是移动日志，delete 才永久删除。[OpenAI Docs App Server](https://learn.chatgpt.com/docs/app-server)。当前文档列出的全部参数/方法不能假定都已被生产 Python wrapper 暴露，删除能力仍需核对实际二进制协议或对应 SDK 封装。

现有 [WorkspaceStore.fork_snapshot](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/workspace_store.py:65) 是文件目录复制，不包含 Codex 会话 Fork，不能替代本需求。会话 Fork 也不能被当作已完成工作区文件复制。两种对象需独立处理，避免子会话仍指向共享可变 Case 文件。

### 4.3 Policy 关联路由所需现有数据

[W2PolicyAssessmentResult](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:379) 有 `policy_hit`、`policy_related`、有序 `policy_ids`、HIT attribution 与 RELATED attribution。不能误把 `matched_condition_ids` 当成 HIT，它在当前 W2 合同中表示 RELATED。

目前没有 Policy→Thread 数据结构。复用排序需要忠实实现 HIT 优先、原排名优先，并保持低 confidence 仍参与。不能用 set/sorted 重排后再宣称保留业务排名；当前 WATCH follow-up 的 sorted 遍历只能用于 WATCH，不能直接拿来当 Thread 路由排序。

一个 Policy 可能已有多个有效子 Thread，多个 Case 也可能同时想 Resume 同一线程。用户规定了跨 Policy 的排序，但未规定同 Policy 多线程的次级选取、忙碌时等待还是另 Fork；这是需要明确的有限路由规则，不应随意回退到无关联的全局 MAIN。

### 4.4 Event 关联不是简单建立 E编号→Thread

当前 W3 输出的是 `RuntimeFactCandidate`，随后由 [allocate_provisional](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/repository.py:1083) 分配 provisional E编号；正式 Event 由 O2 后续归并/创建。一个 NEW Case 可以有多个候选，也可以归入已有 Event，不等于 W3 当场创建一个永久 EVENT01。

provisional ID 有交易日和版本范围，repository 的跨日汇集还可能重新分配展示 alias；正式事件存在合并/redirect。参考 [O2 合同](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/event_library/contracts.py:378) 和 [事件状态解析](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/event_library/repository.py:685)。

因此需要把源 Case/候选身份、临时 Event、O2 正式 resolution 与子 Thread 建立可延续的关联；不能只用裸 E数字作为跨日全局键。只关联“旧事件被用于 Prior 的引用”也不够，因为这种引用不代表当前消息与该事件重复。

### 4.5 生命周期与并发边界

- 48h 从最近一次真正接收新研判任务计时；不能用文件 mtime、worker.updated_at、最后工具调用、阶段二重试代替。
- 阶段一和阶段二之间，线程仍属于同一 Case；其他 Case 不应插入其间污染上下文。
- [JobManager 线程互斥](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/jobs.py:273) 只覆盖单个 Job；阶段一 Job结束后，它不会自动保留“直到阶段二结束”的 Case 级占用。
- [Capsule affinity](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/capsules.py:164) 依据请求的 thread ID；30 秒 idle capsule 回收只是进程回收，不是 Session 删除。
- Template 不能承接实际 Case，也不能把 Case级 PIN、WATCH、历史交易写回母线程；上游版本更新应有模板代际/重建边界，避免把新文件与旧母线程记忆混用。
- 每日清理必须同时识别 Template、运行中的两阶段 Case、排队/待恢复任务和实际线程归属。数据库关系过期、Session 物理删除、工作区删除是不同动作。
- 用户要求母会话永久排除常规清理；模板版本变化不自动授权旧母会话被定时删除。

## 5. 需求二：研究原文、结构索引与语义切片

### 5.1 当前资产加载可复用

[PublishedW3ContextProvider.load](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:120) 按 Case pin 读取 D2、指定 D1、PolicySet 和 Reference View。已具备 ticker 一致性、发布正文 size/SHA校验、版本缓存；D1 可由显式 pin 覆盖 D2 的 provenance。派生切片应该绑定这一组实际输入，而不是另行追随 current 版本。

### 5.2 D1

[Global Research assembler](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_global_research/assembler.py:7) 将 C1、C3、C5 原报告拼接成 Markdown。这是确定的上层章节边界，内部报告还有自然章节。原报告的 heading level 并不保证统一，因此简单按所有 `#` 行等长分块可能破坏层级。

未发现 W3 专用章节索引/切片。需要保留章节原文、编号、标题和边界；不能把“自然章节切片”改为模型摘要。文件名还要处理中文、重复标题、路径特殊字符和长度，但不重命名原始业务对象。

### 5.3 D2 的 v2/v2.1 结构差异

- 旧版 Shell/Unit 使用 `shell_id`/`expectation_id` 等字段。
- [v2.1 seed](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py:278) 的 Shell和Unit为 `name/scope/.../ref`，没有独立 `unit_id`。
- [ExpectationUnitV21](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py:356) 的完整内容包括 state、expectation_baseline、realization_factors、potential_gaps。

需求中的“保留原 Unit ID”对旧合同可直接实现，对 v2.1 必须忠实保留现有 name/ref，并明确派生文件的定位编号不是上游新造的正式 Unit ID。切片不能只包含 State，也不能遗漏 expectation_baseline。编号/标题/父 Shell应在索引中稳定定位，同名对象不能覆盖。

### 5.4 D3 的 Shell 归属是实质缺口

[PolicyV3](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/schema_v21.py:105) 有 policy_id/title/ref/transmission/match_scope/Conditions，但没有 shell_id 或强类型 source_refs。`ref` 是自由业务引用，不保证每条能唯一映射一个 D2 Shell。

[Read formal.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/formal.py:258) 明确不从任意 v3 ref 推导 Shell，现代 Policy 的 shell_ids 留空。因此不能宣称已存在可靠的 Policy→Shell 映射。

D3 研究阶段另有 prepared topology、owner_shell、source_directory、coverage_map等线索，可在 [orchestrator_v21.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/orchestrator_v21.py:632) 和 [integration_v21.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/integration_v21.py:12) 追踪。但 open/supplement/integration/maintenance可产生跨来源 Policy，目录来源不等于最终业务归属。

落实“按所属 Shell 分类”需要生成可靠的派生归属元数据，并覆盖跨 Shell、开放发现、维护新建/迁移的 Policy。不能用按 title 猜测或默认塞进第一个 Shell替代，也不应为归属不明阻塞全部 W3。

### 5.5 同步与切片内容要求

派生处理不改上游研究逻辑及权威正文。D3 必须保留 Policy业务 ref/transmission和全部 Condition的 direction/trigger_layer/calibration；不能仅从 [展示 Markdown](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/publication_v21.py:18) 切片，因为它不是完整 JSON 业务对象的等价表达。

索引需和原文作为同一版本派生结果被读取。更新来源包括初始化、D2/D3维护、正式采纳和手动激活；只在首次研究结束生成一次，会在维护后失效。版本绑定及完整性校验留后端，模型只看有用的标题/主题/入口/截止时间。

Reference View 不是 D3，六项需求只明确 D1/D2/D3 切片，本轮不把它扩展为另一个事件检索系统；当前 W1 已引用 Event 内容应按删减基线直接展开。

## 6. 需求三：72 小时交易意图继承

### 6.1 当前 W3 没有这项注入

w3 task只有 policy_usage、相关 WATCH和公共研究，没有近期交易意图列表。线程历史无法覆盖快判/其他线程/执行失败的全部意图，不可替代 DB查询。

### 6.2 现有业务数据源

| 数据源 | 能提供什么 | 使用限制 |
| --- | --- | --- |
| `runtime_values.trade_intents` | 意图身份、ticker、case、trade、status、released_at、submitted、execution pin | 包含未送达、拒绝、过期或重复等状态，不能只选 READY/成功 |
| `runtime_v2_trade_records` | 已落账 TradeRecord及 W1/W2/W3业务结果 | 不能作为“全部曾形成意图”的唯一全集；不少非 READY意图不在此表 |
| `runtime_values.trade_receipts` | 下游交付/接收回执 | EXECUTION_ACCEPTED不是成交 |
| `te_executions`、fills/jobs | 实际执行结果、filled_qty、失败/拒绝等 | 无 execution不等于已拒绝或已成交，需保留未执行/未知区别 |
| RuntimeCase/TradeRecord.source | 原消息快照、发布时间、URL | 旧数据缺少新信源字段；历史不应假造新格式字段 |

主要依据：[TradeOutputService.record](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/trade_output.py:87)、[TradeRecord](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:583)、[Executor admit](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/trade_execution/repository.py:144)、[结果归类](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/trade_outcomes.py:1)。

需求使用意图发起时间的 72 小时窗口，应与 source publication、Case创建时间、交易语义日和成交时间区分；不能用最近三个交易日或只查当日替代。closed-cycle被选中的释放意图也须按真实释放时间考虑，未释放候选不是已形成执行意图。

这里区分的是“已发起执行意图”和“CLOSED已作出的交易候选判断”；若最终产品希望后者也进入近期交易决策背景，应明确作为另一类业务状态呈现，不能把候选写成已送出的意图。

当前 W3未参与的 Policy意图可能没有 W3结论，历史输入应真实表达不存在，不能补造 reasoning。执行结果要从业务账本关联，避免依赖最终一致、展示用途的 Read模型决定交易上下文。

### 6.3 与精简及去重机制的关系

全量72h消息正文可能很大；按需求不能静默 top-N、截正文或只保留成交记录。Template和切片能减少无关历史，但无法保证72h全量业务内容始终不会触发压缩，应单独测量这块开销。

[event_trade_key](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/trade_output.py:20) 当前依赖恰好一个 W1 Event/Fact attribution、一个 delta candidate和 occurrence_date，仅覆盖有限事实转变，不是广义相似消息去重。双阶段把 delta移到第二轮后，第一轮不能再等待此函数所需的新 Fact；这是实际链路依赖，需要调整，不能为了保留旧去重函数破坏交易优先目标。

历史意图用于 Agent判断增量，不应新增“72h有过同方向交易一律拒绝”的硬规则。现有 Policy方向消费和幂等仍是执行完整性约束，不与这项业务上下文功能混为一谈。

## 7. 需求四：PIN 与 WATCH 容量

### 7.1 WATCH 当前已完成的能力与边界

[policy_watch.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_watch.py:13) 通过 Runtime journal保存 `(ticker,policy_id)` 行及按 Case的 applied receipt：

- follow-up只选择当前 W2明确 HIT/RELATED 的 Policy，不是该 ticker全部有效 WATCH。
- 有效 follow-up覆盖 Router，将 OLD等消息也送入 W3，并采用 REVALIDATE模式。
- SET仅允许当前 W2相关范围内的 Policy，当前最终不交易、不是 CLOSED候选、仍有可执行方向，且无更新的 READY竞态。
- expires为 `boundary(case.trading_date+2天)`，有效期按语义日，不是从 SET滚动48h；已有有效 WATCH拒绝续期。
- 同一 Policy动作中 CANCEL优先；撤销核对冻结来源，避免旧 Case取消新 Case状态。
- READY方向消费会在同事务撤销 WATCH。
- 当前没有每 ticker最多2个的全局容量检查。

### 7.2 PIN 全套不存在

在 W3/Worker相关代码中未发现 PIN actions、PIN状态、24h到期、全局注入。其他模块的 source pin、版本 pin、execution pin不是业务重要消息 PIN，不能复用其含义。

新增 PIN必须由后端把 SET绑定到本次消息，不应让模型自由指定任意历史消息、外部URL或篡改消息正文。SET所需 reasoning由模型输出，title/body/source/publish time取当前真实消息快照。CANCEL则需要可见的既有 PIN操作引用；这是必要业务标识，与删除无意义 Case流水 ID不冲突。

### 7.3 容量与状态注入的工程影响

- WATCH/PIN各自最多2个，分别计数；两个并发 Case同时看到一个空位时，最终写入必须在同一事务内重新判断，不能只靠 prompt约束。
- 应先处理明确取消、清除过期状态，再判断本次新增是否有空位；不能让动作列表顺序导致不必要的容量失败。
- 当前仅提供相关 WATCH，Agent不能看到无关但占容量的 WATCH，也不能按现有 eligibility取消它；全局状态注入与取消权限要一起调整。
- PIN24h按创建wall-clock，Thread48h按接收任务wall-clock，WATCH维持语义日规则；三个时钟不能共用一个 expires函数。
- PIN只改变 W3输入，不能把它加入 Router的进入条件。
- 需求没有明确取消现有“交易后不允许 SET WATCH”等业务资格，不能因移到第二阶段就默认撤掉。是否放宽需作为独立语义决定；PIN资格也不能不加区分地照搬 WATCH资格。

## 8. 需求五：信源名称全链路

### 8.1 现有字段并非从零开始

[Bus schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/schema.py:486) 的 Raw/Standard/MaterializedMember已有 `source`、`publisher_name`、`resolved_domain`。service构造raw时采用 `publisher_name or source or source_definition.display_name`，因此当前source可能是展示名称或采集源名，不保证为规范化 `reuters`。

标准化及流成员构造保留上述字段：[service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/service.py:1322)、[repository materialization](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/repository.py:2353)。

### 8.2 明确的丢失点

[SourceMessageSnapshot](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:143) 只有 ticker/title/body，设计说明还明确将source等留在外部。`SourceMessageEnvelope.from_stream_item`保留采集 source_id，但丢弃最新 member的source/publisher_name/resolved_domain；所以 W1/W2看到的snapshot和W3完整envelope都没有统一发布者名称。

W1/W2请求分别由 [service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py:644) 组装；不仅加 Bus字段，还要改这条转换链。BUFFERED消息会组合多个member正文、移除单个title，需明确组合消息的source语义，避免只展示latest来源却暗示全部正文同源。

### 8.3 前端现在展示的是采集源定义

[DomainProjectors.source_label](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/projectors.py:79) 从 SourceDefinition生成 `{source_id,binding_id,name,kind}`；MessageSummary.source不是规范化发布者字符串。

需求中的Source应与这个采集渠道对象分开或明确兼容，不能将对象直接替换为字符串而破坏筛选、配置和绑定。相关展示有消息卡、Runtime消息/Case、交易/历史导出及共享消息组件。

接口来源是 [API types](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts:556)，后端/前端各有生成wire schema；新增字段必须走类型、生成物、projector和UI链路，不能只改一份JSON。

### 8.4 生成语义需要收敛

明确识别原发者才采用原发者；其余按当前链接域名可靠回退，不新增联网溯源、正文猜测或阻塞。已有publisher_name能否作为可信原发标识，需按adapter赋值来源核对，不能无条件信任任何自由字符串。

需求三/四说Source为新闻域名，需求五例子要求 `reuters`而非完整URL。最终需统一规范化发布者名称与域名回退的表示，不能在当前消息、历史意图和PIN中各自重新解析并生成不同值；也不应仅为这个区别扩出复杂信源解析schema。

## 9. 需求六：交易优先双阶段和互斥路径

### 9.1 当前单阶段的硬绑定

[W3CaseResult及V3](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:746) 把novelty/policy/expert_trade/delta_candidates/watch_actions放在同一结果：

- NEW必须有至少一个 delta candidate。
- OLD禁止delta和Expert评估。
- 有Policy hit禁止Expert评估。
- NEW+HIT必须有policy_execution；selected Condition必须属于最终HIT归因。
- UNCOVERED_NEW在runner层强制NEW且无Policy。

这些是代码校验，不是仅有Skill建议。“Policy与Expert平行互斥”需要拆掉旧有先Policy后Expert的合同依赖，同时保留所选执行引用的真实性、方向和消费校验。

当前[_apply_w3_v3](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py:1697) 先分配新事实，再保存交易，然后coverage gap和WATCH。若新Fact阶段失败，交易还没输出；只调整JSON字段顺序不会改变这一行为。

### 9.2 两份阶段合同所需能力，不是本轮最终字段命名

| 模块 | 必须能够表达 | 不应进入此输出 |
| --- | --- | --- |
| Trade Decision | NO_TRADE / POLICY_TRADE / EXPERT_TRADE三选一；核心理由；Policy的Policy/Condition选择或Expert方向及必要分析 | WATCH/PIN/新Fact、完整运营结果、执行日志 |
| Post-Decision Operations | 新Fact、WATCH/PIN动作、必要coverage反馈等；已提交的阶段一结论作为输入 | 再次授权、覆写或重新输出阶段一交易 |
| 后端Case状态 | 两阶段各自的结果、完成/失败、绑定thread、阶段身份与提交状态 | 不必把Case/Job/版本流水暴露给模型 |

互斥路径应有简单的结构判别并验证相应必需字段，不靠深层分支优先级解释“双路径都true”。方向仍从选中v3 Condition取值，旧合同仍按其已有方向，不默认LONG。Broker数量/账户/价格等仍由既有Executor配置和风控负责，需求没有授权让W3重新设计整套下单参数。

第一阶段可能为了决定Policy交易必须保留必要新旧事实判断，但不应为了输出运营用Fact列表而延迟提交。应区分“交易成立所需业务判断”和“运营提取字段”，而不是把所有novelty语义也一起移走。

### 9.3 当前幂等会阻止第二轮执行

[ReceiptWorker.run](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/worker_receipts.py:32) 对所有 `persistent_runtime_w3`固定 `phase='case'`，identity包含scope/run/node/phase。仅改变attempt_id无法区分两个阶段；第一次成功后，第二次会直接返回第一次receipt。失败后也会沿用冻结request。

必须在后端明确区分Template初始化、Fork/线程绑定、交易阶段、运营阶段和业务重试身份；这是当前执行协议的必要扩展，不是多加一个用户prompt字段即可解决。

### 9.4 决策必须先持久化并交给既有下游

当前 `_execute_w3`在保存完整结果后才应用业务，已有“结果已生成、输出失败，只重试输出”的恢复路径可以继承。但新流程需要以阶段一为提交点，而非等完整W3CaseResult生成。

[TradeOutputService](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/trade_output.py:87) 已负责Case级幂等、READY/过期、Policy方向消费、控制权限和transaction。[service._save_trade](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py:1851) 还会核对选中Condition和当前authorization。新Trade Decision应适配这条链，不能另建绕过消费或账户控制的发单通道。

阶段一输出落地后，delivery/executor应能独立推进；运营重试不能让同Case重新交易、回滚消费或把已成立意图改成NO_TRADE。

### 9.5 阶段二的失败隔离不限于runner

- 当前只有一个ROUTE_TO_W3 effect，失败会记录W3失败，Case completion遇任意failed effect会将Case标为FAILED。
- [Case completion](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py:1997)、[bounded maintenance输入](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/bounded_inputs.py:1)、O2/O3馈送依赖终态/候选/gap。需要同时表达“交易已提交、运营未完成”，不能等第二阶段成功才让下游看到交易。
- 同线程连续执行应有Case级占用跨越两Job，并在运营失败恢复期间保持可定位的thread；用户允许的关联Resume不能插到两阶段之间。
- 两轮复用同一Skill/Prompt的业务说明，使用明确的阶段指令和各自schema；不应强制第二轮重新读取四份研究正文或重建整套分析。
- 两轮有各自耗时/结果身份；不能仅给整个Case一个新超时后声称第一轮已做到即时交付。

### 9.6 CLOSED模式与其他W3调用必须兼容

`TradeOutputService.record`在CLOSED模式先保存candidate，最终由 [WeekendSelection](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/selection.py:35) 选中并释放意图。这也是node=W3，但使用SelectionResult和weekend Skill；不能全局把所有W3 node请求强制套为两阶段Case合同。

“第一阶段立即交下游”应保留现有模式含义：REALTIME可交付意图，CLOSED先记录候选，不擅自改为盘外自动下单。最终selection也会读候选trade及W3结果，必须能消费第一阶段冻结结论，不以运营未成功否定候选。

## 10. 持久化现状与最小必要留存

当前并没有把全部CodexSession直接复制进Runtime DB，但保存了多份request/result/telemetry，另有SDK自己的Session和历史库：

| 位置 | 当前保留 | 与需求的关系 |
| --- | --- | --- |
| RuntimeCase.frozen_inputs | prompt资产、projection、引用、schema、模型、原输入 | 运行/重试需保留必要冻结信息，不能因模型字段删减直接删 |
| runtime_v2_w3_cases / TradeRecord / coverage gap | source和完整W3结果的多处业务存档 | 后续应按价值选择业务结果和必要关联，减少重复payload |
| runtime_values.worker_requests / worker_receipts / worker_invocations | 完整prompt/schema、WorkerJob及重复回执 | 当前恢复/Read/成本统计依赖；不应永久保留所有低价值过程 |
| Worker JobStore | request、job payload、events | 控制在途、幂等和结果恢复；终态后可缩减，但不能删在途身份 |
| workspaces/audit/jobs、sdk_loop、agent_loop、summary | 结果和工具调用摘要等重复审计文件 | W3应有明确留存策略，不必保存所有过程副本 |
| worker-results | 子进程已提交结果的durable receipt | 防止“模型完成但父进程未收到”的重复执行，需要保留有效恢复窗口 |
| codex-home sessions/内部库 | 真实模型上下文及SDK历史/日志 | 48h子线程Resume需要Session，不能提前全删；archive不等于释放空间 |

依据：[JobStore](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/job_store.py:15)、[Worker结果提交](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/result_receipt.py:12)、[审计持久化](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/jobs.py:625)。

需求中的“主动舍弃”应落实到W3拥有的过程数据和副本，不应反向要求长期保存整段会话、外移全部历史或新增庞大归档框架。保留小型业务结果、阶段提交身份、交易/执行真相和Policy/Event关系，足以支撑72h历史注入及后续路由；Thread48h删除不等于业务结果48h删除。

既有[磁盘留存审计](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/docs/disk-p4-retention-audit-20261013.md)说明共享codex-home和其他研究任务仍有活跃依赖。新W3生命周期应准确拥有并清理其子线程，不能借此清理其他D1/D2/D3任务、直接删内部SQLite/WAL或把所有SDK数据统称低价值。本轮没有执行任何删除动作。

## 11. 相关文件及改动传播范围

| 子系统 | 主要文件 | 后续实现时涉及的合同 |
| --- | --- | --- |
| W3输入/资产/runner | [w3.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py) | 精简payload、展开引用、Template上下文、索引入口、阶段执行 |
| W3/Case结果与状态 | [schema.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py) | 旧结果兼容、新两阶段合同、业务source、必要阶段/线程元信息 |
| 快判与路由、输出落地 | [service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py)、[router.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/router.py) | 进入条件、线程选择、交易先提交、运营完成/失败区分 |
| Thread/业务DB | [repository.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/repository.py)、[journal.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/journal.py) | 关联查询、TTL、互斥、事务容量、迁移及序列化 |
| WATCH/方向消费 | [policy_watch.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_watch.py)、[policy_consumption.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_consumption.py) | 全局状态、SET/CANCEL容量、READY撤销、generation |
| 执行包与装配 | [execution_bundle.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/execution_bundle.py)、[factory.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/factory.py) | 冻结新合同/Skill、模板代际、旧Case恢复 |
| Worker API/客户端 | [Worker schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/schema.py)、[app.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/app.py)、[client.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_runtime/client.py)、[local_client.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/local_client.py) | Fork/生命周期接口、阶段身份、HTTP与本地一致 |
| SDK/Job/Capsule | [sdk_runtime.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/sdk_runtime.py)、[jobs.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/jobs.py)、[capsules.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/capsules.py) | 真正Fork、同线程调度、恢复、物理删除能力及留存 |
| 分阶段回执 | [worker_receipts.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/worker_receipts.py) | 避免两个阶段碰撞、成功结果不重复研判 |
| 交易/执行历史 | [trade_output.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/trade_output.py)、[执行repository](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/trade_execution/repository.py) | 72h意图全集、未成交保留、阶段一适配 |
| CLOSED/维护 | [selection.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/selection.py)、[coordinator.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/coordinator.py)、[bounded_inputs.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/bounded_inputs.py)、[maintenance.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/maintenance.py) | 候选输出、运营补交、Event正式归并后的关系延续、每日清理调度 |
| 研究资产 | [D1 assembler](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_global_research/assembler.py)、[D2 schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py)、[D3 publication](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/publication_v21.py) | 派生切片、业务完整性、Shell来源及维护同步 |
| Bus信源字段 | [Bus schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/schema.py)、[Bus service](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/service.py)、[Bus repository](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/repository.py) | 发布基础字段、规范化、raw/standard/member传递 |
| Read/API | [projectors.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/projectors.py)、[runtime.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/runtime.py)、[API types](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts) | 决策/运营分阶段展示、业务source、reasoning和attempt记录 |
| 前端 | [message-bus.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/message-bus.tsx)、[runtime.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/runtime.tsx)、[runtime-case.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/runtime-case.tsx)、[runtime-case-export.ts](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/core/runtime-case-export.ts) | 来源展示/导出、Policy/Expert三选一、交易已完成而运营未完成 |
| W3角色与Skill | [agent.md](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/persistent_runtime_v2/w3/agent.md)、[uncovered_new.md](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/persistent_runtime_v2/w3/skills/uncovered_new.md)、[revalidate_then_evaluate.md](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/persistent_runtime_v2/w3/skills/revalidate_then_evaluate.md) | 同Case两阶段职责、平行路径、索引优先、历史增量、WATCH/PIN |

## 12. 现有测试约束与新增验收空白

本轮阅读了相关测试，未执行测试或真实模型请求。表中“已有”指测试代码已有断言，不代表本轮运行通过。

| 测试文件 | 已有重要约束 | 六项需求的新覆盖 |
| --- | --- | --- |
| [test_persistent_runtime_w3.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_persistent_runtime_w3.py) | pin/cache、MAIN/FALLBACK、无ticker容量门、strict schema、retry、delta/gap、Skill注入 | Template完成后Fork、母会话不污染、引用投影、切片入口、阶段schema |
| [test_runtime_policy_direction_usage.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_runtime_policy_direction_usage.py) | LONG/SHORT独立、generation/CAS、WATCH不续期/READY取消、OLD+RELATED、输出失败重用结果 | 两种路径互斥、WATCH容量/全局取消、PIN、交易提交后运营失败 |
| [test_persistent_runtime_worker_receipts.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_persistent_runtime_worker_receipts.py) | 失败request模型仅在显式override下刷新 | trade/operations不同identity、重试只重做失败阶段、Fork失败恢复 |
| [test_worker_resource_budget.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_worker_resource_budget.py) | 同thread串行、独立attempt并行、capsule affinity/reclaim、HTTP恢复 | 同Case两Job连续占用、不同Case Fork并发、TTL与运行/恢复互斥 |
| [test_runtime_orchestration_execution.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_runtime_orchestration_execution.py) / [recovery.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_runtime_orchestration_recovery.py) | 交易幂等/过期、CLOSED候选、冻结输入、租约、selection恢复 | 阶段一已落账各崩溃点不重复交易；阶段二独立恢复/补交 |
| [test_message_bus_v2_news_sources.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_message_bus_v2_news_sources.py) / [test_runtime_orchestration_bus.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_runtime_orchestration_bus.py) | Bus消息与Runtime传递 | 原发者可信值、URL回退、BUFFERED、正文补全、字段跨转换不丢失 |
| [test_document3_v21_publication.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_document3_v21_publication.py) / [maintenance.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_codex_document3_v21_maintenance.py) | 原生Policy发布/维护完整性 | 切片原文等价、Shell映射、跨Shell/开放Policy、维护更新同步 |
| [test_wire.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/v2_backend/test_wire.py) / [test_trade_outcomes.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/v2_backend/test_trade_outcomes.py) | wire闭合、真实执行结果归类 | 两阶段DTO/source/导出、无成交和未知执行结果、旧记录兼容 |

另须覆盖：Policy HIT/RELATED排序且不按confidence排除；Event provisional/正式归并/跨日；48h临界且仅新任务续期；每天一次清理不触碰Template；PIN24h各时钟边界；并发SET不超2；72h边界保留失败意图；索引不遗漏完整Unit/Policy；低价值过程回收后业务历史仍可查询。性能验收应分开记录阶段一决策延迟和阶段二耗时，并以逐次用量评估，不能只看最后一次SDK usage。

## 13. 明确冲突与需要收敛的有限设计问题

### 13.1 已确认不能直接照旧实现的冲突

1. `ReceiptWorker phase='case'`会使第二轮返回第一轮回执；必须扩展阶段身份。
2. 现有NEW必须产Fact、Policy HIT禁止Expert的校验与交易优先/平行路径冲突；必须拆合同和更新解析/路由/消费者。
3. 旧Expert事件去重依赖第二阶段Fact，不能原样挡住第一阶段交易。
4. 当前v2.1 D2没有独立Unit ID；v3 Policy没有可靠单一Shell归属，不能直接从旧合同字段取值。
5. archive不是delete；会话Fork不是目录snapshot Fork；不能用近似动作宣称完成生命周期需求。
6. Thread48h和交易历史72h是不同保留对象；删会话不能删掉历史交易业务数据。
7. 只有单Job线程锁不能保证两阶段连续，阶段一Job完成时释放锁会允许其他Case插入。
8. 当前快判snapshot丢source，前端source是采集渠道对象；Bus新增字段不能自动完成全链路。

### 13.2 需要在后续设计中明确，不作为本轮阻塞

- Template粒度及上游变更时的代际边界；固定背景加载到何种程度，动态72h/PIN/WATCH必须按Case提供。
- 同Policy多有效Thread、同Event多引用、命中线程忙碌时的有限次级规则；不能削弱用户已确定的Policy优先级。
- v3 Policy跨Shell/开放发现的派生目录归属及维护同步来源；不能靠自由ref猜测强关联。
- PIN与历史上下文的Source统一表示；BUFFERED聚合消息如何真实呈现来源。
- 72h窗口在新任务启动时冻结的参照时刻、旧Case恢复是否延用已冻结内容；既要符合实际发起边界，也要避免重试持续漂移。
- 全局WATCH取消范围与现有SET资格是否有意变化；不把容量重构扩展为未授权的WATCH生命周期重写。
- 阶段二失败时Case/运营状态、维护补交和线程有效恢复的处理；交易结论保持已提交不可覆写。
- 物理删除在实际SDK/二进制中的可用接口；不能直接手删共享内部库完成表面验收。

这些问题不要求增加复杂状态机、额外模型摘要、信源溯源流程或人工门控。应围绕现有明确业务和实际完整性约束收敛。

## 14. 勘察验证边界

已完成：六项原文和字段删减方案阅读；本地输入/输出/schema/路由/消费/WATCH/回执/Worker/研究资产/Bus/执行账本/Read/API/前端/测试代码追踪；正式容器三个关键文件SHA对照；生产AsyncCodex方法签名只读检查；官方Fork/archive/delete语义核对。

未执行：模型Template初始化或真实Fork/Resume实验、删除API调用、数据库迁移、业务代码修改、测试运行、性能评测及部署。10月12日样本只能作为历史现象，不作为此次v2.1全链路验收。本文列的是当前现状、依赖和实现约束，不是最终schema或已经实现的方案。

本次迭代的schema设计必须同时满足两点：继承字段删减基线“业务依据直接给足、重复和流水包装留后端”的原则；为Thread关联、72h交易历史、PIN/WATCH状态及两阶段能力提供必要的新合同，不能把删减后的字段数量误当成最终上限。
