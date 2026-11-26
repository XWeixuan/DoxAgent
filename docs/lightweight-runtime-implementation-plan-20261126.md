# 轻量运行开发方案

日期：2026-11-26。状态：可指导开发的实施方案；本轮未实现或部署业务功能。

本次精简修订：保留总体架构和文件级工作包；转换改为人工收尾后的安全静止边界，删除旧 FULL 在途业务跨模式接续设计；TRADE 提示词保持原文，不新增模型可见能力字段。本轮只修改本 MD。

依据：`docs/lightweight-runtime-requirements-survey-20261126.md`，以及本轮已确定的业务决策。**本轮保留 D1 的决定取代前置报告中“是否取消 D1”的待决策项。**

## 1. 实施结论与边界

在现有初始化和 Persistent Runtime V2 中增加一个按 ticker 持久化的 `running_mode = FULL | LIGHTWEIGHT` 维度。共用初始化 Worker、Runtime、Coordinator、W3 提示词、模型配置、模板/就绪库存、资源池和数据库；在已有入口做少量明确分支。

轻量初始化：

```text
D1 ─────┐
        ├─→ O2 ────────────────┐
CDECR ──┘                     ├─→ activation.prepare → activation.commit
monitoring.prepare ───────────┘       → bus.ready → runtime.ready
```

保留 D1 原有必要子步骤、校验、发布和回执；O2 继续使用 D1 上下文，**不需要开发无 D1 的 O2**。删除的只是轻量 DAG 中的 D2、D3 和完整 O4 研究/交付节点。

轻量持续运行：

```text
消息 → 既有准入/去重 → W1 R1/R2
                         ├─ OLD（含 low）→ 归档
                         └─ NEW（含 low）→ W3 Trade → 既有交易输出/执行
                                             └─ Operations → PIN、事实候选
事实候选 → 既有 O2 增量维护 → 事件库发布/激活 → 当日记忆与跨日 W1
```

WATCH 输出保留解析和审计，但轻量下不应用、不触发路由、不产生后续监测。保留 CLOSED/Sweep/Selection；不运行 O3。技术失败继续进入既有重试、回执恢复和人工恢复机制。

转换采用**周末先在 FULL 下完成已准入业务，再人工暂停目标 ticker，经安全静止检查后提交仅激活节点的配置切换**。未完成的 FULL Case/effect、维护、Sweep、Selection 或待选择候选存在时拒绝转换，恢复 FULL 收尾后再试；不迁移这些在途业务。保留历史、消息游标、尚未准入 Runtime 的总线消息和事实记忆，不要求平仓，不提供反向转换入口。

不做：新 Runtime/Worker/资源池、按模式分叉 W3 提示词、新 WATCH 系统、全库历史回填、批量删除旧资产、跨库大事务、通用迁移框架、自动定时转换、轻量模式独立交易执行器。

## 2. 本轮深入核查带来的简化与必要补丁

| 代码事实 | 采用的最小处理 |
|---|---|
| O2 初始化和日常增量都能取得保留的 D1 | 沿用现有 D1 manifest、CDECR、O2 输入；不改事件研究算法 |
| v6 W3 已输出 LONG/SHORT/NO_TRADE，Operations 已编译事实 | 沿用 `persistent-runtime.w3.v6` 的输出 schema；不新建模型结果体系 |
| 工厂有一份 execution bundle，包含 W1/W2/W3/O2/O3 资产 | 继续共用；轻量不调用其中 W2/O3，而不是复制一份“轻量 bundle” |
| revision 3 同时包含两阶段 W3 和 Policy 元数据 | 保留 revision 3；按模式判断 Policy 是否适用，不升 revision 4 来复制编排 |
| Research MCP 现有 `d1-d2.v1` 限制最大可访问范围，实际对象来自 Case manifest | 轻量 manifest 只挂载 D1；沿用服务、凭证和工具，不另建 D1 MCP |
| W3 模板/ready 配置只含共享指令、模型和能力，业务研究按 Case 绑定 | 模板/ready 库存继续共享；只隔离实际研究上下文及其缓存身份 |
| 现有普通暂停不等于 effect 就绪队列停止：effect claim 没有按 ticker 的暂停过滤 | 仅对进行中的目标 ticker 转换操作补准入屏障和静止复查；不把普通 FULL PAUSE 全局改成冻结所有 continuation |
| 维护 CAS 只比较四类资产，没比较运行模式；恢复入口可重新 admit 历史维护 revision | 补 mode-aware CAS；历史成功回执可消费，但不能重新授权已经过时的 revision |
| `serving_fields.py` 会裁剪 run/revision 的字段 | 把新模式字段加入最小投影，否则后台正确、看板仍会误认为完整模式 |
| 远端 terminal projection 的 Policy 版本列是 NOT NULL | 做一项向后兼容的可空列调整；不能以 0 或虚构版本绕过 |

本轮按本地工作区 HEAD `434711945a93df0f520ea871087c38df14cc94b0` 及已有未提交内容审阅，Git 跟踪状态仍显示相对本地 `origin/main` 落后 39 个提交。未检查生产部署差异、未读取线上数据库。实施时应在实际开发/部署基线上复核本文列出的函数，不覆盖工作区原有修改。

## 3. 统一数据模型：一个配置事实，快照随执行固定

### 3.1 命名与权威来源

新增小模块 `src/doxagent/running_mode.py`，只放 `RunningMode`、旧数据缺省值和 `required_artifact_roles(mode)` 之类纯函数，不做配置注册中心。

- 新增 `FULL`（完整运行）、`LIGHTWEIGHT`（轻量运行）。旧数据未提供该字段时按 FULL 读取；未知字符串报错，不回退。
- 已运行 ticker 的**有效激活 revision 顶层 `running_mode` 是权威事实**。Control 的操作请求和 run 记录目标值，不能单独成为第二套生效配置。
- 与 `monitor_mode`、`REALTIME/CLOSED`、`workflow_version`、W3 单/双分支共识设置保持独立。切换不改这些值。
- 激活保留 D1/O2/config 引用，不保存文档正文或复制 Registry。新轻量 revision 不挂 D2/D3；旧完整 revision 与文档原样保留供历史查看。

### 3.2 字段落点和空值契约

| 对象/文件 | 最小改动 |
|---|---|
| `ticker_initialization/schema.py: RunRecord` | `running_mode` 默认 FULL；submit 时固定，resume/rerun 不重新套当前默认值 |
| `activation_revisions.payload` | 顶层 `running_mode`；stage/activate/rollback 仍用同一 revision 表和指针 |
| `providers.py: RuntimeInputSnapshot` | 增加模式；轻量 `projection=None`，D1 有值，D2 无值 |
| `schema.py: RuntimeVersionPin` | 增加 `running_mode` 默认 FULL；Policy/Projection 版本改为可空，非空仍须 ≥1 |
| `RuntimeCase` | 通过 `version_pin.running_mode` 取得冻结模式（可加只读 property）；不再增加一份可变 Case 配置 |
| `W3RouteCase`、`W3ContextVersionPin` | 加冻结模式；D2/Policy pin 和 W2 结果允许轻量为 None；与已有 `mode: W3Mode` 区分 |
| `TradeRecord` | 增加冻结模式默认 FULL；`w2_result`、`policy_set_version` 可空；轻量仅允许 W3 origin，无 executed/candidate Policy 身份 |
| `RuntimeTerminalProjection` | 加模式及可空 Policy 版本；旧 FULL 投影仍可读取 |
| Read/API/共享 TypeScript 契约 | ticker/初始化/Case 显示模式；不适用 W2 用明确资源状态表达 |

校验原则：

1. FULL 保留现有要求；例如 FULL TradeRecord 仍要求真实 W2 和 Policy 版本。不要把“字段可空”变成完整流程缺资产也能运行。
2. LIGHTWEIGHT 的 runtime pin 必须无 D2、Policy/Projection 版本；W2 与 alignment 不存在，`w2_skipped` 不借用作“未命中”。仍要求真实、可读的 D1 和 Event Library。
3. FULL Case 在 W1/W2 尚未完成时允许原有空结果，不能新增一项过早校验破坏故障恢复。
4. 不制造 `W2PolicyAssessmentResult(policy_hit=False, ...)`、空 PolicySet 或假 D2；None 表达不适用。
5. 旧成功回执、模型输出 schema、bundle 内容及指纹不改写；缺省 FULL 在读取时解释，无全库 UPDATE。

### 3.3 准入与版本固定时机

继续在 `execute_message()` 建立 Case 时一次读取激活快照，并把模式写入 pin。已有 Case 恢复只用原快照，不再看 ticker 当前模式重路由。

- 每种模式内，已有 Case、Sweep/维护及 Selection 判断快照按原版本恢复，不重新套当前默认值。
- 转换时要求目标 ticker 的已准入 FULL 业务已收尾；**已建 Runtime task 即属于已准入工作，不能因尚无 Case/快照就跨模式接续**。PENDING/future-due 也不能默认放行。
- 尚在 Message Bus、未建立 Runtime task 的消息保留原游标，转换成功后按轻量准入；消息原有时间和交易资格不改写。
- 已发布事件库、日冻结索引、已存在的事实记忆和 PIN 直接保留。没有归属未完成 FULL 维护 frame 的事实候选，可由转换后正常 O2 调度处理；不需要迁移 Worker/O2 回执。

因此不需要逐条改写任务模式、设计跨模式候选释放或增加“切换时间路由表”。历史快照保留用于审计和原模式故障恢复，不构成允许旧 FULL 任务在轻量下自动重开的理由。

## 4. 初始化和激活的逐项改动

### 4.1 DAG 与监测配置

修改 `ticker_initialization/catalog.py:default_plan(running_mode=FULL, ...)`：FULL 保持现有节点和依赖；LIGHTWEIGHT 只生成第 1 节中的节点。D1/CDECR 仍并行，O2 仍依赖二者；`monitoring.prepare` 是普通确定性配置节点，无模型调用，可与研究并行。

新增一个小的 `MonitoringConfigurationAdapter`，在 `adapter_factory()` 中分发该节点，复用 `CandidateConfiguration.prepare()` 和 `MessageBusV2Service.materialize_default_bindings()`：

1. 冻结当前共享来源定义/default profile 及该 ticker 已有绑定，补缺失默认绑定；不启动轮询。
2. 不重写已有绑定、人工修改或全局 SourceDefinition；不生成 D2/D3 驱动的个性化爬虫交付。
3. 校验至少有一个有效绑定，返回与既有激活消费者兼容的 `monitoring_configuration` 引用；O4 的 `plan_id/plan_version` 在该路径不必存在，不伪造 O4 plan。
4. 全新 ticker 使用当前默认公共来源组合。现有来源支持的 legacy ticker 搜索仍沿用，不顺带改变 Reuters 查询数量。额外 BY_SEARCH/分发/Massive/X/IR 个性化配置继续通过现有监测配置入口提交，不自动泛化公司简称、账号、正则或端点。
5. 统一监测词 `ticker_monitoring_terms` **目前不在 CandidateConfiguration 的快照表中**。保留其现有独立版本/CAS 管理，存在时继续生效；新订阅仍经过既有语言、正则和 Massive 配置校验。本需求不复制监测词账本到初始化配置库，也不放宽这些校验。

默认配置可以启动不等于全部可选来源已经配置。源凭证、站点可达性和监测词问题按已有健康状态显示，不为了轻量初始化新增全源探测轮次。若运行环境的自定义 default profile 本身要求额外参数，沿现有校验提示配置缺项，不默默启用一个无效订阅。

### 4.2 激活引用与就绪

修改 `activation_adapter.py`、`runtime_inputs.py`、`runtime_scheduler/service.py:admit_activation()` 及 `consumers.py`：

- LIGHTWEIGHT 只校验 D1 已发布正文/身份/校验和、Event Library、monitoring config 和 W3 readiness；FULL 的 D2/D3 provenance 和 canonical Policy/Projection 校验保留原分支。
- `_documents_available()` 按必需角色迭代，轻量只查 D1；O2 已有 D1 引用校验继续生效。
- `ActivatedRuntimeInputs.from_revision()`、`public_pin()`、`for_admission()` 均从同一个 revision 生成 mode-aware pin；轻量不能回退 `current_document2` 或当前 Policy head。
- admission 对轻量不执行 `policy_consumption.admit_revision()`，`consumers.admit_runtime_revisions()` 的稳态证明不再要求 Policy authorization head/applied。Bus/Runtime revision ACK、控制 epoch 和 Worker readiness 仍保留。
- `Policy authorization head` 的旧数据不删除；新轻量不读取或使用它。FULL 仍要求其原有授权证明。
- 无历史输入且 O2 正常 NOOP 时继续发布真实空事件库；失败仍失败，不以空库兜底。

通过相同 `stage_revision → activate → real consumer ACK` 成功后才完成初始化。初次轻量没有旧版本可回滚，失败保持精确 resume；转换/替换资产继续走既有 CAS 补偿。

## 5. Runtime、W3 和共享执行资产

### 5.1 在已有热路径分支，不复制 Service

修改 `persistent_runtime_v2/service.py`：

1. `execute_message()` 先取得 snapshot/mode。轻量跳过 Projection 加载/兼容校验、Policy usage/authorization 快照，以及依 Projection 类型决定 revision 的降级逻辑；仍沿用共享 execution bundle 的 revision 3 和 v6 W3 契约。
2. 冻结真实 index、provisional、D1/Event pin、共享 prompts/models/W3 assets；轻量不冻结 Projection、W2 contracts 或 Policy usage。共享 bundle 中仍有 W2/O3 文件供完整 ticker 使用，这是共享版本资产，不是轻量模型输入。
3. `_resume_hot()` 在解析 Projection 前分支：轻量只调用现有 `_run_w1_hot()`；NEW 无论置信度都生成 W3 effect，OLD 无论置信度都只生成 archive effect。沿用 `save_case_with_effects()` 同事务提交路由和 effects。
4. CLOSED 的 `phase='W1'` 继续只完成 W1 判断；之后的 `ALL` 阶段利用原 round receipt 得到结果并完成轻量路由，不发 W2。波次、暂停和 `w3_batch` 屏障保留；共享内部后半阶段名称不必为了去字样重写整个 Sweep 状态机，但对外不能显示轻量执行了 W2。
5. 轻量 `first_round_tags=[]`，无 W2 R1/R2/R3、Policy WATCH followup。`_w3_mode()` 给新事件入口赋现有兼容值，不对外派生 Policy 命中语义。
6. `_execute_effect()` 按模式校验：所有模式需要 Case/W1/route，只有 FULL 要求 W2。`_enqueue_route_effects()` 构造合法轻量 `W3RouteCase`，OLD 的 archive effect 同样能够完成。
7. 仍使用当前 `_call_with_retry()`、round receipt、模型 fallback、lease/heartbeat、pending effect 恢复；不添加轻量重试器。W1 技术失败不归档、不假定 OLD。

轻量仅启用当前 v6 两阶段 W3（含事实候选字段的当前 Operations schema）。若有效 W3 资产仍是 legacy 契约，在轻量初始化/转换准备时明确拒绝；不强制改动老 FULL Case 的已冻结契约，不同时扩展所有历史 W3 版本。

### 5.2 一套 W3 提示词与输出 schema

维持 `prompts/persistent_runtime_v2/w3/` 唯一来源：`AGENTS.md`、`skills/skill.md`、`skills/operations.md`，及已有 Selection skill。FULL/LIGHTWEIGHT 不存在各自目录、不按 mode 生成两版指令，也不创建第二个 active execution 指针。

**TRADE 提示词禁止任何修改。** `AGENTS.md`、`skills/skill.md` 的内容、拼接顺序及 Trade 阶段的既有宿主指令均保持原样；不能用动态附言、模式专用前缀或额外 system/task 文案变相修改。模型、采样/推理设置、四字段方向判断和交易标准也不因轻量模式改变。

本方案同样不安排 Operations/Selection 提示词修改：优先在既有业务输入组装、对象可见范围和宿主动作应用处适配。Operations 继续输出原有 WATCH 数组，宿主忽略轻量 WATCH；Selection 对 W3-origin 候选继续按原有证据与时效规则判断。无 Policy 不能伪装成“未命中”，也不能靠给模型添加解释字段来补齐不存在的资产。

Trade 的四字段方向判断、Operations 的三个根数组均沿用 v6 schema。历史 bundle 继续保存旧提示词用于原执行恢复；这是历史快照，不是维护两套模式提示词。

在 `w3_context.py` / `w3_v6.py` **生成业务输入时**增加轻量分支，不能先调用要求 W2 的函数，再在末尾删字段：

- 保留消息、W1 依据/事件、PIN、近期实际成交背景、已有行情/搜索能力。
- 不输出 W2、policies、alignment、policy usage、Policy targets 和 W2/Policy 分类标签。
- 保留非 W2 的 `W1_LOW_CONFIDENCE`、`BODY_MISSING`、按事件关联得到的 `RECENT_TRADE`；轻量不通过 Policy 关联生成近期交易标签。
- 既有 `contexts` 只保留真实 D1 条目，Research manifest 只挂载 D1；不新增能力说明或缺省解释。

**禁止新增 `runtime_capabilities`、`policy_watch_effective`、`research_documents` 或同类模型可见字段。** `running_mode` 等必要字段只用于宿主配置、冻结 pin、缓存和审计，不额外注入 Trade/Operations/Selection task、候选正文或工具返回。轻量输入沿既有字段投影，删除不适用 W2/Policy/D2/D3 内容；保留字段的含义、形状和原值不变。现有容器确有必须保留的数组时使用合法空数组，不制造业务结果或新增提示性标签。

### 5.3 D1-only 上下文与缓存

修改 `w3.py:PublishedW3ContextProvider`、`W3PreparedContext`、`_install_context()`：

- LIGHTWEIGHT 直接按 pin 加载并校验 D1，不读取 D2、PolicySet 或全文 Reference；`document2=None`、`policy_set=None`，只安装 `components={'d1': ...}` 和 D1 cutoff。
- FULL 原路径不变。不得用轻量“允许 None”的规则包住完整路径的真实加载失败。
- `prepare_public_assets()`、`prepare_case()` 和 `_citation_context()` 允许轻量 D1 组件及已加载的 W1 Event 引用，不从缺失的研究正文反推 Event 列表。
- 优先走现有 v6 Research Store，避免把 D1 大文档再次复制进每个 Case workspace；不扩展 legacy research pack 分支来支持轻量。

隔离规则：

| 身份/缓存 | 处理方式 |
|---|---|
| D1 原文组件 | 按内容哈希共享，不因模式相同正文重复存储 |
| FULL 的旧研究 pin/cache key | 保留原算法，避免已有回执/缓存全部失效 |
| LIGHTWEIGHT 研究 pin、context cache、load lock | 明确带轻量身份及实际 D1 pin；不包含旧 D2/D3；不得命中 FULL prepared context |
| `w3_public_preparation[ticker]` | 身份包含实际轻量 pin，转模式后只准备该 ticker 的 D1 研究绑定 |
| Case/phase `context_ref` 与 manifest | 沿用既有独立绑定、校验和与过期机制；一个 Case 的上下文不随激活切换改写 |
| 模板 compatibility、ready pool | 共享指令/schema/模型/能力相同就继续共用，不加入 ticker/mode 人为分池；实际研究在 Case materialize/bind 时挂载 |

不要无条件把 FULL 缺省字段加入旧哈希输入：对 FULL 保留旧 pin 的序列化身份，轻量使用有区分的内部身份即可。内部新增的 mode/pin 字段不得因对象整体序列化而泄漏到模型输入；共享模板不持有上一 Case 的研究资料。

### 5.4 Research MCP 的最小修改

复用 `mcp/w3_research_server.py` 的现有 scope/credential 和 `w3_research_store.py`：`d1-d2.v1` 是允许访问 D1/D2 的上限，不要求两者都存在。轻量 manifest 只有 D1，因而 `catalog/search/read` 不发现 D2/D3，显式请求不存在对象返回既有不可用错误，无全局 head 回退。

检查 `scoped_manifest()`、direct_objects、原文读取和 source/provenance 展开均只遍历真实挂载组件；尤其不得将转换前旧 D2 的组件 ID 留在新的轻量 manifest。保留统一行情工具。无需新增 scope token、端点、常驻 MCP 进程或模式资源池。

### 5.5 Operations、WATCH、事实与交易记录

在 `w3_execution.py:_commit_pin_watch()` 调用 Policy 动作处理**之前**判断模式：

- LIGHTWEIGHT 不调用 `policy_watch.apply_v4_actions()`，不访问 Projection/W2；对解析出的 WATCH SET/CANCEL 返回 `IGNORED_LIGHTWEIGHT`，放入现有 `w3_operations_applied` 审计结果，不写 `policy_watch` 状态。
- 保留原始 Operations 输出和逐项 parse issues；无需为了接受一个非空 WATCH 数组重试模型。已有局部动作解析可隔离坏 WATCH 项，PIN/事实继续提交；根字段类型/Trade/事实格式等核心错误仍遵循原有契约。
- PIN、provisional 事实分配、stage commit、Case 更新和 ACK 继续在原短事务完成，重试只应用一次。
- 轻量 `NO_TRADE` 也继续 Operations；不只为实际交易沉淀事实。
- `_v6_records()` 对轻量不产生 coverage gap 或 Policy badcase；归档依据不写“Policy 未命中”。`_trade_record()` 写真实 W3 origin 及 mode，无 W2/Policy 身份。
- 当前 ticker 为轻量时不激活任何 Policy WATCH followup；旧 WATCH 和 FULL Case 输出只保留原有历史语义。转换前必须已完成 FULL Operations/回执收尾，不为其迟到写回新增一套跨模式应用路径。

`TradeOutputService`、执行配置/禁交易日/延迟/资金/订单/持仓/退出逻辑不改。对不带 executed Policy 的 W3 Trade，不再额外要求活动 Projection；Policy 专用 guard 仍只保护真正的 Policy Trade。

## 6. O2-only 日常维护：沿用现有分支与发布事务

修改 `maintenance.py`、`ticker_initialization/repository.py:activate_runtime_bundle()` 和 `maintenance_recovery.py`，不新建维护 Service。

### 6.1 正常轻量路径

1. `_frame()` 固定当前 mode、base revision、既有 bounded membership、模型/bundle；轻量不查询 Policy consumption。
2. 复用 `branch_library()`、`RuntimeDeltaBatchAdapter`、D1 upstream context、O2 Worker 和 ReceiptWorker，执行 INCREMENTAL 及到期 Reference review。
3. 只物化该任务必需的 event_library 资产。FULL 继续物化原有 O3/open_event_atlas 资产；不能从共享 bundle 全局删除它们。
4. O2 回执校验成功后，轻量跳过 O3 feed、O3/Reference capture 专用输入和模型调用；不伪造 `o3={status:SUCCEEDED}`。checkpoint 可记录 `o3_disposition=NOT_APPLICABLE`。
5. `activate_runtime_bundle()` 的 `policy_ref` 可空，仅在 LIGHTWEIGHT 允许；更新 Event 引用、保留 D1/config/mode，始终不挂 D2/D3。FULL 参数和行为保留。
6. admission 不做 Policy 授权，继续准备事件可见性；成功后只消费已解决事实候选。visibility day 保持现有单调规则，当日索引/bridge 不重置。
7. 轻量不新增 Policy badcase/gap/consumption 输入和反馈任务；事实候选及正常交易审计按原有 O2-only 消费闭环落定。转换前尚需处理的 FULL 策略反馈在 FULL 中收尾，不设计“转轻量后批量标已消费”的路径，不改写旧记录来制造 O3 完成。

### 6.2 保留 CAS 与同模式恢复，取消跨模式回执拼接

把模式加入现有 dependency CAS；FULL 继续比较原四类资产，轻量精确比较 mode、D1 引用、Event root/version。发布时从 current revision 继承其最新 monitoring config，不以无关的监测配置变化强迫重跑 O2。**沿用依赖 CAS，不改成严格整 revision 相等。** 但 FULL base 与 LIGHTWEIGHT current 必须明确冲突，即使 D1/Event 引用完全相同。

维护恢复只保留现有同模式语义：

- FULL 的 O2 成功/O3 失败继续在 FULL 下复用原 O2 回执恢复；未收尾时禁止转换。LIGHTWEIGHT 的 O2-only 失败按自身 checkpoint/预算/幂等身份恢复。
- **删除 FULL O2 输出跨模式复用、frame 跨模式 rebase、旧 O3 回执裁剪和替换 publication identity 的开发任务。** 静止边界消除了这些正常需求，不为少数窗口编写转换器。
- 发布已提交但消费/任务终态未完成，仍在原模式下幂等收尾，收尾完成前是转换阻塞项。历史成功回执仍不能把非当前 head 的 revision 重新 admit，不能重建旧 Policy 授权。
- 在 `_frame()`、`activate_runtime_bundle()`、`recover_failed()`/人工 resume 中做简单的一致性检查：冻结模式与当前有效模式不一致时，不自动重开、改模式或重放 O3；返回原任务 ID 和版本冲突。历史终态保留，现有补偿/retired 标记沿用，不新增迁移状态机。

只有这个 mode 一致性检查和原有 CAS/lease/fence 是必须保留的保护。同模式人工换资产仍沿已有 rebase/checkpoint 恢复，不放宽原输入指纹校验，也不增加全库历史扫描。

## 7. 周末“完整 → 轻量”的最小操作路径

### 7.1 入口：复用 PAUSE 和 RESTART

新增一个人工 CLI 便利入口，例如：

```text
python -m doxagent.persistent_runtime_v2.cli switch-to-lightweight --ticker MU --expected-control-revision REVISION --idempotency-key OPERATOR_KEY --reason REASON
```

以上是**待实现命令**，不是本轮已可执行功能。命令要求先通过已有看板/控制入口 PAUSE 目标 ticker；不复用 `operations.pause` 的单一 journal 标记冒充完整控制暂停。转换 CLI 提交现有 `RESTART` 操作，body 为 `initialization=REUSE_ACTIVE, running_mode=LIGHTWEIGHT`，不传 `monitor_mode`，避免重复保存交易执行配置。

人工操作顺序固定为：在 FULL 下收尾已准入业务 → PAUSE → 提交转换 → 静止复查 → 原激活 DAG/ACK → 恢复运行。PAUSE 不是清队列或强制完成任务的命令；若检查仍有业务未结束，保持 FULL 和暂停状态，人工按原流程恢复 FULL 收尾后，用新的幂等键重试。已有订单和持仓不用清零。

不用新增 Control operation kind：扩展现有 RESTART 的可选 `running_mode` 参数及 `ControlService.step()`，让这个特定分支提交一次 `submit_activation()`，使用 `operation_kind=SWITCH_RUNNING_MODE`、原 base revision、原 cutoff、`preserve_monitoring_configuration=True`，并传递当前 control epoch/operation ID。仅激活 DAG 仍由现有 InitializationWorker 执行。

### 7.2 安全静止边界：不带未完成 FULL 业务过界

- 使用 America/New_York 当地周六/周日，且既有 MarketCalendar 判定非 session、Runtime 为 CLOSED；不根据上海日期或“当前没开盘”接受工作日转换。不需要联网查日历。
- 当前是 FULL、有已完成的有效激活，Control 为 PAUSED、无另一个进行中的控制/初始化操作。
- 目标 ticker 已准入的 FULL 业务已结束，且没有结果仍不确定的模型/Worker 调用。PAUSE ACK 或“没有 RUNNING lease”都不足以证明静止。

转换阻塞项使用一份有界检查清单：

| 账本/对象 | 阻塞条件 |
|---|---|
| Runtime CASE/SWEEP/MAINTENANCE/SELECTION tasks | PENDING、RUNNING、HELD 中仍有未完成工作；包括 future-due 和尚未固定快照的已建任务 |
| Case/effect/W3 stage | Case 需继续处理；effect PENDING/PENDING_RETRY/RUNNING；Trade 已完成但 Operations、业务提交、消费或必要 ACK 未收尾 |
| Worker/round receipt | 请求已发出但结果未确认、仍有阶段/线程所有权或可能迟到的业务写入；lease 到期不能替代结果确认 |
| CLOSED 候选/Selection | 有待选择、已选择但尚未释放/落定的候选，或 Selection 结果/副作用未闭合；不等待时钟到期就将其改为 EXPIRED |
| 可自动恢复的 FULL 失败工作 | `recover_failed()` 仍会重新启动的失败维护/Sweep、未补偿发布或待处理 FULL 策略反馈；先按原恢复入口收尾 |

历史上已明确终态、无待处理副作用且不再参与恢复的失败诊断不阻塞；不要求修复全部历史。**未准入 Runtime 的总线消息、已落定事实记忆/PIN、已接纳订单与存续持仓不属于这份研究工作清单。**

检查在转换操作受理、专用屏障生效后执行，并在激活提交前复查控制 epoch/静止状态；用目标 ticker 的现有索引、未完成状态和 LIMIT/有界记录集合，不扫所有历史。首轮失败返回具体 task/effect/candidate ID，不创建激活 run；提交前复查失败则不 commit，沿原 run 失败处理。两者都不改 active head、不删消息、不伪造成功、不自动取消候选。该周末已有待 Selection 业务且无法收尾时，保持 FULL 并顺延转换；不用跨模式自动接续来绕过限制。

### 7.3 只补转换操作的准入屏障，分开收尾与新业务

本轮源码核实结果及改动边界：

| 路径 | 已有行为/本次处理 |
|---|---|
| 新 task 准入/调度 | `ControlRepository.admit_in_transaction()` 拦住 `analysis_allowed=False`；`coordinator._schedule()` 复用它，调度 checkpoint 不得越过失败准入 |
| CASE/维护/Sweep/Selection 执行 | `_execute_owned()` 统一先 `ControlRepository.dispatch()`；暂停时按原 defer 处理，不进入模型/维护/选择业务。不因提前 claim 或本地 future 而绕过该检查 |
| 共享 effect pump | `claim_effects()` 本身没有普通 PAUSE 过滤，且其他 ticker 可唤醒它；**不能假设普通 PAUSE 已使全部 effect 静止** |
| 已发出结果的收尾 | `_resume_phase()`、durable receipt/Worker 结果对账、原 lease 续约/释放、已完成结果落账及必要 ACK 不能整体冻结；未收尾就拒绝转换 |
| 订单/持仓/退出 | `trade_execution/worker.py` 的 job 推进、`drain_events`、连接/订单对账、failed EXIT 恢复和独立 ExitRunner 继续运行，不受新增转换屏障控制 |

最小补丁仅针对同库 `v2_operations` 中**目标 ticker 的 ACCEPTED/RUNNING、RESTART 且请求 `running_mode=LIGHTWEIGHT` 的转换操作**：利用已有 `v2_operation_active` 索引，在 `claim_effects()` 的实际领取事务中排除它的新 effect claim，并在尚未发出的模型/下一业务阶段开始前复查。与受理/静止检查共用这条已有操作事实，不新增 pause token、常驻镜像或第二套锁。

不要在 `_resume_phase()` 顶部按 `analysis_allowed=False` 一刀切返回。已有结果仍允许按原快照、lease/fence 和输出权限完成确定性收尾；如果 continuation 还需发起新模型或新的业务阶段，则在那条分发边界按原身份 defer，不重复付费、不扣失败预算。由此留下的未完成 FULL 工作仍是转换阻塞项；本操作失败后，用户按原 FULL 流程恢复，不在转换中自动排空。

普通 FULL PAUSE、普通 RESTART、其他 ticker 和无 Control 状态的 standalone 路径保持原行为；本需求不顺带将所有暂停 ticker 从共享 effect 队列剔除，也不增加全局冻结开关。暂停造成新交易意图不可放行，继续沿现有 `TradeOutputService/output_permission` 处理，不能为“落账恢复”而重新放行交易。

退出边界必须单独验证：`Executor._step()` 的 ENTRY 限制与既有订单/EXIT 推进不同；`ExitRunner` 的 position-level `paused` 也是另一套已有控制。**Runtime PAUSE/转换不能写这个持仓 paused 字段，不能停止交易 Worker、删除 EXIT job 或重置订单/成交游标。** 其他来源仍可正常采集原始消息；Runtime 准入受同库 Control 约束，不把共享采集器或其他 ticker 一起暂停。

### 7.4 激活、ACK 与最终恢复

1. Control RESTART 已有幂等键、expected revision 和串行操作检查负责受理，并立即形成第 7.3 节的目标 ticker 屏障；静止检查不通过按明确 BUSY/版本冲突结束本次操作，不后台无限排空/重试。通过后保持 `analysis_allowed=False`，直到整个转换提交并被消费者确认。`admission_allowed/initialization_allowed=True` 允许激活准备，不能据此提前执行 Case。
2. `op.steps` 保存 activation-only run ID；用 `control_operation_id` 使创建 run 幂等。**不调用会把该 run 当普通新初始化并提前激活控制的路径**：转换操作留在原 RUNNING operation，由 step 等候 run 及 ACK；不把它登记成新的 `state.initialization_id`，保留原正式初始化记录。
3. stage 新轻量 revision：保留 D1、Event root/version、monitoring config；其余运行状态完全不复制/重置。`activate()` 用原 base revision CAS。重复执行仍是同一 run/revision。
4. Bus ACK 新 revision，但 config head 相同则不重新安装、不补默认来源、不覆盖人工修改。Runtime admission 按轻量规则校验并 ACK，取消其 Policy proof 前提；执行数据仍被 `analysis_allowed=False` 拦住。
5. `ControlService.step()` 同时确认 activation-only run SUCCEEDED、active head 为目标 revision、真实 Bus/Runtime ACK 和控制 epoch 一致，然后复用 `settle()` 恢复原 monitor mode 的 RUNNING。不改 `mode_effective_at`、removal epoch、交易配置、消息游标或订单/持仓状态。
6. CLI 丢失连接不撤销操作；既有 Control worker/InitializationWorker 继续推进同一个 operation。重新查询/同键请求取回原结果。

现有 `submit_activation()` 需增添可选 `running_mode/control_epoch/control_operation_id` 透传，并确保原 ACTIVATE/REPLACE_ARTIFACT/ROLLBACK 默认从目标/活动 revision 继承模式，不能默认把轻量资产重新标 FULL。

### 7.5 失败处理，不开发反向转换产品

- base CAS 冲突：不覆盖新 head；保持目标 ticker 暂停，返回冲突和当前 revision。
- commit 之前失败：保留原 FULL revision，按已有初始化 resume 精确恢复或重试。
- commit 后 ACK/启动失败且初始化预算耗尽：沿 `ActivationAdapter.compensate()` 和原 base CAS 回退指针，保留暂停；Bus/Runtime 在现有 admission 循环接回原 revision。补偿逻辑仍适用，不新增通用事务补偿框架。
- operation step 不能因为曾看见旧 ACK 就恢复交易；失败 run、错误 head 或未完成补偿必须明确失败/待恢复。
- 成功转换后不提供 LIGHTWEIGHT→FULL 命令；阻止普通 activate/rollback/import-policy 等入口绕过这一限制。唯一例外是**同一失败转换操作**回到其原 base 的内部补偿。这与反向补做 D2/D3 不同。
- 不主动删除旧 Policy WATCH、Policy usage 或研究文档；它们在新轻量执行中无效，历史仍可读。

## 8. CLOSED、Selection 与历史恢复

### 8.1 Sweep

`coordinator.py:_sweep()` 的输入快照支持 `projection=None` 与冻结模式；反序列化时不调用 `parse_runtime_projection(None)`。保留既有成员排序、wave size、W1 barrier、w3_batch、maintenance 和 Selection 时间；轻量后半阶段完成路由而非 W2 调用，不产生合成 W2。

FULL 与 LIGHTWEIGHT 各自在本模式内保留原波次/断点恢复。**转换前未完成的 FULL Sweep 必须先结束，不支持其跨模式接续，也不把其后续维护改挂轻量 base。** 只读取旧成功 Sweep 供历史展示；新轻量 Sweep 从正常调度建立，游标和事件记忆保留。

### 8.2 Selection

在 `selection.py` 内按本次判断快照的模式选择输入，不另建 selector：

- 新轻量 Selection 使用真实 D1、现有 Event/Reference 背景、W3 候选依据及实时输出去重背景，不要求 Projection/D2/D3。沿用现有 Reference View 是该 Selector 的业务输入，不扩大 v6 Trade 的 Research MCP 作用域。
- 候选原始账本不改；构造**模型可见**轻量候选时使用既有字段投影，去掉 W2/Policy 派生内容及新增内部 mode/pin 字段。保留源消息、原 W3 决策/理由、时间及有效事实依据，不增加能力字段、提示性包装或新指令。
- 当前上下文按当前 snapshot/pin 构造合法研究请求；不要 `model_copy` 一份旧 FULL Case，仅替换 mode/pin 却留下矛盾 W2 字段。
- W3-origin 无 executed Policy 的候选不以活动 Projection 是否存在判断有效性，仍通过原有输出权限、候选时效、日内重复和账户配置约束。
- FULL 的 Policy-origin/专家候选沿原规则在 FULL 中处理，轻量只处理其本模式新生成的 W3-origin 候选；不开发跨模式重评、Policy 候选降级或混合候选释放。
- 同模式内，已冻结 `judgment_inputs/selection_result` 仍按原快照恢复，不再次问模型。转换前有未选择/未释放 FULL 候选或未完成 Selection 时直接阻塞；不改方向、不重绑轻量上下文、不强制过期，也不删除它们来完成转换。

### 8.3 历史及管理入口

- 同模式 Case/effect/W3 stage 按冻结 pin/契约恢复；历史 W2 标签、来源、成本和 trade origin 不改写。若过时 FULL 工作因异常或人工恢复在轻量下重新出现，按模式一致性拒绝重开并保留证据，不增加自动跨模式恢复分支。
- 旧 FULL 执行的回执无需迁移；新轻量记录在缺省 FULL 旧代码中不兼容，因此部署前先升级所有消费者，不能一边产生轻量记录一边让旧读取服务继续处理它们。
- `rerun-node/rerun-block`、replace-artifact、显式 activate/rollback、强制初始化与修复 Agent 必须遵守 run/目标 revision 模式；不存在 D2/D3 节点时明确“不适用”，不自动补建完整 DAG。
- 已经成功切成轻量的 ticker 后续 restart/reuse/force-initialize 都继承轻量；显式 FULL 请求拒绝反向转换，不因缺参回到 FULL。
- 本次面向当前 managed runtime；旧手工 `daily-close` 在 managed 环境已有拒绝入口，保持该边界，不另行开发第二条轻量维护通道。

## 9. API、Read、数据库及前端的最小闭环

### 9.1 请求与显示

- `StartTickerRequest` 增可选 `running_mode`，新 ticker 缺省 FULL；RESTART/REUSE/强制初始化已有 ticker 缺省继承活动模式。将参数透传 Control→run→revision。
- 转换先做人工 CLI 即可，不增加专门转换向导。Overview 现有模式/初始化信息附近显示当前“完整运行/轻量运行”；启动表单提供选择。
- 轻量初始化进度由实际 DAG 推导适用阶段：D1、事件库、来源/激活、启动；不存在的 EXPECTATIONS/POLICIES 不再算 PENDING。
- 轻量当前图隐藏 W2 及 Policy 标签节点；历史 FULL Case/历史周期仍按实际 attempts 显示，不能只依据 ticker 当前模式隐藏它们。Case 的 `first_round_shape` 增加 `SINGLE` 表达仅 W1，FULL 原 PARALLEL/SEQUENTIAL 不变。Case 详情的 W2 资源复用 `state=NOT_PRODUCED, data=null, reason=NOT_APPLICABLE`，而不是“尚未形成判定”；只补必要原因/枚举契约，不另建状态体系。
- W2/Policy KPI 对轻量显示不适用；混合汇总只统计适用的 FULL 样本。热路径耗时对轻量仅按 W1 R1/R2 计时，FULL 维持现有双 lane+W2 R3 口径；成本仍按实际调用贡献。
- D1 页面照常；D2/D3 当前资产不适用，历史已发布文档仍允许查看，不新造空报告。

同步修改 `v2_read/serving_fields.py` 的 activation/run/W3 row 裁剪、`projectors.py`、`initialization.py`、`runtime.py`、`graph.py`、API Case detail/effective_policy 逻辑。显示模式从当前 revision、历史 Case pin 各自取得；不把“无 Policy”显示为 false。

### 9.2 存储变更范围

本地主要是 JSON payload 扩展；复用现有表、主键、outbox、账本和内容存储，不增加每 Case 一条模式事件，不回填终态历史，不新增定时扫描。

远端已确认的一处必须调整：`doxagent.persistent_runtime_v2_terminal_cases.policy_set_version` 在 `supabase/migrations/202608290001_persistent_runtime_v2_projections.sql:12` 为 NOT NULL。实施时创建**新的增量 migration**，DROP NOT NULL，保留正数 CHECK；用 `projection_json` 的 mode 增加 FULL 必须有 Policy version、LIGHTWEIGHT 必须为 NULL 的条件检查（缺省 mode 按 FULL），确保约束与本地 validator 一致。不改旧 migration 文件、不填 0。

`projection.py` sink/outbox 和 DTO 一起接受 mode-aware nullable 投影；旧 FULL outbox 继续正常 flush。上线前对实际表 DDL 和 outbox 开关做一次定点核对，验证是否还有外部消费者依赖非空列；不因此开展全库迁移。原 schema 权限/RLS 不变，不新开放表。

前后端契约从 `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts` / 现有生成链更新到两份 wire schema，禁止手工只修 JSON。`frontend/v2/AGENTS.md` 的紧凑布局、无装饰文案及桌面验收约束继续适用。

## 10. 开发顺序与文件级任务单

每项在同一代码基线上顺序完成；先使读取兼容，再产生轻量数据。以下拆分是可执行工作包，不是新运行架构。

| 顺序 | 文件/函数 | 实际交付与完成条件 |
|---|---|---|
| 1 | 新 `running_mode.py`；`ticker_initialization/schema.py,repository.py`；`persistent_runtime_v2/schema.py,providers.py,projection.py` | mode 缺省/校验、可空契约、mode pin；旧 fixture/回执可读，FULL 缺必需资产仍失败，轻量无法夹带 W2/Policy |
| 2 | `runtime_inputs.py`；`w3.py`；`w3_research_store.py`、`w3_v6.py` | D1-only load/install/manifest/cache；原 FULL cache key 可命中，轻量不能读取旧 D2/Policy；保留同一模板/ready 库存 |
| 3 | `catalog.py`、小型 monitoring adapter、`activation_adapter.py,consumers.py`；`runtime_scheduler/service.py` | 新轻量 DAG 与真实 ACK；无 D2/D3/O4 模型调用；默认来源可用、人工配置不覆盖；轻量不查 Policy proof |
| 4 | `service.py:_resume_hot/_execute_effect/_enqueue_route_effects`；`router.py` | 完整路由不变；轻量 NEW/OLD 四种置信度组合正确；无 Projection 也能归档/送 W3；失败恢复用原身份 |
| 5 | `w3_context.py,w3_v6.py,w3_execution.py,policy_watch.py,case_outcomes.py`；现有 prompt pack 仅做不变性校验 | 无 W2 的输入组装和宿主提交；TRADE 提示词/宿主指令不改，无新增模型可见字段；WATCH 无效但 PIN/事实/交易正常 |
| 6 | `maintenance.py`、`activate_runtime_bundle()`、`maintenance_recovery.py` | O2-only 发布、mode CAS、同模式 O2 回执/发布消费恢复；不开发 FULL→轻量 O2 回执拼接，过时模式不自动重开 |
| 7 | `coordinator.py:_sweep`、`selection.py`、`trade_output.py` 的必要 guard | 两模式各自 CLOSED/Selection 及本模式恢复；未结束 FULL Sweep/候选阻塞转换，无跨模式重评/释放 |
| 8 | `v2_control/repository.py,service.py`；`ticker_initialization/operations.py`；runtime CLI、effect claim/分发边界 | PAUSE+安全静止检查+activation-only RESTART；转换专用准入屏障、BUSY 返回、真实 ACK/失败补偿；普通 FULL 暂停与订单/EXIT 恢复不改 |
| 9 | API/共享类型/Read/前端/增量 SQL；`changelog`、操作说明 | 新建/重启/转换/历史可见且契约一致；远端可空投影可写；记录代码变更和实际验收范围 |

跨包必须共同检查的遗漏点：`public_pin()`、W3RouteCase 创建、`_v6_records()`、`_trade_record()`、`effective_policy()`、Sweep bundle 序列化、Selection 候选模型输入、Read 字段裁剪、显式 activate/rollback 模式继承、维护早期成功恢复分支，以及 effect 的 claim 与 `_resume_phase()` 收尾/新分发的区别。不要用全局搜索后批量替换 `w2` 或“所有 None 都当空对象”的方式修改。

## 11. 验证与上线边界

### 11.1 必需的自动化测试

围绕真实行为和故障边界写测试，复用现有 fixture，不为纯 getter/default 写大量镜像实现测试。

| 测试组 | 核心断言 |
|---|---|
| 新轻量初始化 | D1 完整必要流程/CDECR/O2/消息配置/真实 ACK 成功；未执行 D2/D3/完整 O4；空历史、D1失败、O2失败可区分 |
| 同服务混跑 | FULL 和 LIGHTWEIGHT ticker 交错执行；FULL 原路由/Policy授权/维护不变；轻量无 W2/O3 调用，线程池/ready 库存数量不按模式翻倍 |
| 路由和效果 | NEW normal/low 均 W3，OLD normal/low 均 archive；无 W2 的归档/W3 effect 可完成；W1失败原重试和预算不变 |
| W3 提示词/输入红线 | TRADE 的 AGENTS/skill 字节哈希、宿主拼接指令/顺序与业务判断不变；同一 prompt pack/schema；比较最终任务与工具返回，轻量无 W2/Policy/D2/D3 旁路，绝无能力字段、mode 标签或其他不必要的新模型输入 |
| WATCH/Operations | 非空及局部无效 WATCH 不阻断 PIN/事实提交，数据库无轻量 WATCH 写入；NO_TRADE 仍沉淀事实；重启不重复应用 |
| 缓存/模板 | 相同 D1 内容组件可共享；FULL→LIGHT 不复用旧 FULL prepared context，旧 FULL Case 历史可读；同模式重试仍用原快照，模板不携带上个 Case 资料 |
| O2-only 生命周期 | 同日记忆、bridge、跨日去重、Reference review；轻量同模式发布后崩溃/消费重试不重复执行；FULL O2成功/O3失败阻塞转换，在 FULL 原回执上恢复，不新增跨模式复用 |
| 维护并发 | 资产相同但 mode 不同仍挡住旧 CAS；仅配置不相关变更不迫使重新研究；历史成功回执不能重新 admit 旧 Policy |
| CLOSED/Selection | 两模式各自波次/断点/候选释放正确，冻结结果不重判；PENDING/future-due FULL Sweep/Selection、未释放候选均阻塞，不强制过期、不重绑轻量输入 |
| 周末转换 | 工作日/非暂停/非静止拒绝；无 RUNNING 但有 PENDING、Operations未完、可恢复失败仍拒绝；幂等冲突、base CAS冲突、commit前后崩溃、ACK超时/补偿、同键重连 |
| 暂停影响范围 | 同一服务普通 FULL PAUSE/RESTART 行为不变；转换专用 gate 不领取目标新 effect、不误拦其他 ticker；已发出结果落账/ACK、订单事件对账、EXIT/ExitRunner/failed EXIT 恢复继续，Runtime PAUSE 不写持仓 paused |
| 数据保持 | D1/Event/config root/version、游标、当日记忆、PIN、原始Case、交易账户配置、订单/持仓/退出管理未被转换重建或清空 |
| Read/DB/API | 三种缺省/合法/非法 mode，FULL 必需值、轻量 nullable，旧 outbox；混合周期图/统计、历史 FULL Case、初始化适用阶段 |

### 11.2 回归与实际验证

先跑受影响的初始化、Runtime、W3 v6、Research/ready、维护恢复、Control、Read/API 测试；前端运行 schema/typecheck、相关 Vitest 和 build。前端实际改动按既有约束在 1262px、1559px 桌面宽度验证新建选择、轻量进度、两类历史 Case 和混合图，静态构建不能替代此验证。

性能验收对比同一服务相同输入负载：模型调用数、DB事务/SQL耗时、RSS、现有线程/ready 数量、事件库分支与 workspace 增量。轻量 OLD 不新增模型/写入阶段；轻量 NEW 仅删除 W2工作，仍保留原 W3调用方式。正常处理不新增跨库查询/全历史扫描/周期性写模式快照；转换屏障复用同库 active operation 索引，静止检查只在人工转换时有界执行，不扫描所有暂停 ticker/历史Case建立常驻镜像。

不得宣称总成本/延迟必然下降：轻量会把完整模式原本归档的一部分 NEW/RELATED 送入 W3。用混跑负载验证完整 ticker 的准入和 W3延迟，沿用现有容量限制，不借机增加并发或资源池。

上线顺序：

1. 在实际部署源码上复核差异，保留已有 dirty 工作；完成兼容读取、nullable 契约与数据库增量调整，再升级所有已有消费者。
2. 所有 ticker 尚为 FULL 时完成完整模式回归和现有服务健康检查；核对 TRADE 提示词及实际宿主指令不变，不能因轻量发布顺带升级提示词。旧冻结 bundle 不覆盖。
3. 在隔离测试环境完成轻量初始化和转换故障注入；真实模型验证不作为无限回测项目，只验证实际 D1-only 输入、Operations 和数据闭环。
4. 首个生产轻量 ticker/周末转换由人工明确触发；本方案不授权本轮进行生产切换或实盘测试。验收真实 ACK、W1/W3/O2 回执、无 W2/O3，以及已有 FULL ticker 的自然消息链。

产生轻量记录后，不能直接回滚到不认识 mode/null Policy 的旧镜像；代码故障回退应保留兼容读取补丁，暂停受影响轻量 ticker 并修复。这是数据契约兼容边界，不要求开发业务上的反向转换。

## 12. 本轮验证记录与源码定位

### 12.1 前次方案核查记录（本次精简未重跑）

前次制定方案时使用现有离线测试，共 **32 项：31 通过，1 失败，35.40 秒**。覆盖 W3 v6 schema/动作、Research 隔离、共享 ready 库存/旧版本恢复、监测词、维护恢复和 V2 Control；该结果原样保留，不作为本次精简修订的新测试结果。

失败为 `tests/test_message_bus_monitoring_terms.py::test_terms_revision_language_and_search_plans`：第 67 行期待 Reuters QueryPlan 有 3 条查询，当前 `message_bus_v2/search_plan.py:31` 对 Reuters/Google 取首个概念，实际是 1 条。此次未修改源码，故这是当前基线的测试/代码口径差异；不把它算作轻量已引入的回归，也不为让本方案“全绿”修改查询规则。实施合并前应核对该查询规则的既定产品口径并单独处理断言，保留对其他语言/CAS行为的校验。

执行命令（折行列出测试目标；实际一条命令）：

```text
.venv/Scripts/python.exe -m pytest -q
  tests/test_w3_v6.py::test_schema_and_upstream_authority
  tests/test_w3_v6.py::test_scoped_research_cannot_recover_legacy_policy
  tests/test_w3_v6.py::test_v6_commits_without_novelty_and_no_duplicate_actions
  tests/test_w3_v6.py::test_v6_invalid_root_action_containers_are_not_silently_empty
  tests/test_w3_research_v2.py::test_complete_pages_exact_join_and_context_isolation
  tests/test_w3_ready_v5.py::test_bound_ready_inventory_is_idempotent_concurrent_and_not_per_ticker
  tests/test_w3_ready_v5.py::test_old_version_recovery_cannot_retire_current_public_inventory
  tests/test_message_bus_monitoring_terms.py
  tests/test_runtime_orchestration_recovery.py
  tests/v2_backend/test_control.py
```

前置勘察报告的 41 项通过结果也是历史核查记录，本次没有重新运行。上述均不证明尚未实现的轻量模式已可运行；本次只做源码复核与文档一致性检查，没有运行新功能/模型测试、浏览器验收或生产数据库验证。

### 12.2 直接开发定位

下列路径均相对仓库根目录；行号为本轮工作区参考，开发以函数名为准。

| 主题 | 主要位置 |
|---|---|
| DAG、激活、恢复 | `src/doxagent/ticker_initialization/catalog.py:14`；`activation_adapter.py:25`；`runtime_inputs.py:36,61`；`repository.py:1699,1732,1784`；`operations.py:11` |
| 默认来源/监测词 | `src/doxagent/message_bus_v2/service.py:597,646`；`manifests.py:394`；`monitoring_terms.py:95,179`；`src/doxagent/ticker_initialization/configuration.py:55,62,98` |
| 热路径/契约 | `src/doxagent/persistent_runtime_v2/service.py:507,803,1817,1875,2328`；`schema.py:237,550,643,894,909`；`router.py:84` |
| 共享 W3/研究/ready | `w3.py:65,183,225,594,675,883,911,1772`；`w3_execution.py:72,252,391,787,849,1156`；`w3_context.py:95,399`；`w3_v6.py:123,151,172`；`execution_bundle.py:16`；`factory.py:229`（均在 persistent_runtime_v2） |
| MCP 权限/实际对象 | `src/doxagent/mcp/w3_research_server.py:23,185`；`src/doxagent/persistent_runtime_v2/w3_research_store.py:430,481,633` |
| 维护与 CLOSED | `persistent_runtime_v2/maintenance.py:84,142,276,369,478,546`；`maintenance_recovery.py:118`；`coordinator.py:710,772,863`；`selection.py:65,152,195` |
| 暂停与转换控制 | `src/doxagent/v2_control/repository.py:221,420,507,561`；`service.py:101,218`；`persistent_runtime_v2/fencing.py:34`；`repository.py:1092`；`service.py:2118` |
| 分发/收尾与交易退出边界 | `src/doxagent/persistent_runtime_v2/coordinator.py:150,363,657`；`service.py:1910,2033`；`src/doxagent/trade_execution/worker.py:227,266,284`；`executor.py:271,498`；`src/doxagent/exit_strategy/runner.py:249,263`；`repository.py:275` |
| 读写投影 | `persistent_runtime_v2/projection.py:19,102`；`v2_read/serving_fields.py:16`；`initialization.py:16,81`；`runtime.py:18`；`graph.py:73`；`api_v2/case_detail.py:400`（均在 src/doxagent） |
| 前端/公共契约 | `frontend/v2/src/pages/overview/start-form.tsx`、`runtime.tsx`、`runtime-case.tsx`；`dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`；`frontend/v2/AGENTS.md` |

### 12.3 本次修订范围

本次只修改本方案 MD，不追加 changelog、不改业务代码/提示词/数据库/运行配置。复核了 Control/task/effect 的准入与 continuation、维护恢复、Selection 候选状态及交易 Worker/ExitRunner，保留原架构、九项文件级工作包、版本一致性、CAS/ACK/幂等和双模式隔离验收。

文档一致性检查通过：顶层章节和九项工作包保留，旧跨模式接续/能力注入要求已移除；对照修订前的 106 个相关源码、契约及 W3 提示词文件，内容哈希均未改变。本次未重跑业务测试。

删除的开发范围是：旧 FULL O2 回执跨模式拼接、旧 FULL Sweep 自动续跑及后续维护改挂、旧候选跨模式重评/释放、普通暂停的全局语义重写、TRADE 提示词修改和模型可见能力字段注入。实施时仍须按工作包记录真正发生的重要代码变更和实际验收，不把文档静态核查当作功能验收。
