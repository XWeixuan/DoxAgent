# DoxAgent V2 后端开发前排查与核心决策

- 日期：2026-09-07。
- 状态：待用户评估；本文中的建议尚未成为已确认业务规则。
- 本轮范围：复核 Workflow、后端与前端需求，判断实现方向与核心决策。详细落地方案留待下一轮；不修改应用实现或直接修订 API Contract。
- 输入：[前端数据需求](DOXAGENT_V2_FRONTEND_DATA_REQUIREMENTS.md)、[PRD Part 1](DOXAGENT_V2_FRONTEND_PRD_PART1.md)（包括完整 §4.6）、[PRD Part 2](DOXAGENT_V2_FRONTEND_PRD_PART2.md)、[API Contract draft.1](DOXAGENT_V2_API_CONTRACT.md)。
- 源模型依据：[上一轮逐节点审查](DOXAGENT_V2_API_SOURCE_AUDIT.md)、[原生模型与源码指纹清单](api_contract/native-model-inventory.json)。本轮重新核验 69 份源码指纹，均一致，并补查运行接纳、暂停、交易输出、执行器、认证、存储接线及已有交付方案。
- 当前 HEAD：`e00a3cefcb7667ea29fe42aa8280e98f832b80e0`。结论依据实际源码与文档；没有连接生产数据库、模型、broker 或启动工作流。既有数据库与交易验收报告只作为历史证据，不表示本轮重新验证了线上状态。

## 1. 总体判断

V2 后端具备研究、初始化、持久运行、消息与交易执行的领域基础，尚不具备完整的新前端服务能力。不能把现有 Dashboard 接口换一个 URL 前缀就视为完成。

后续工作的主体应当是：独立 V2 API、按页面范围组织的可查询事实与聚合、可恢复的操作回执和增量，以及 ticker 运行控制与订单执行之间的明确边界。研究节点、固定版本交接、既有恢复机制和交易状态机应继续复用。

真正需要用户评估的是第 3 节的四项业务决策。数据库如何拆分、查询索引、分页、投影、游标、幂等、认证接线等由后端方案自行确定，不作为需求问题上交。

## 2. 排查结果与实现方向

### 2.1 Workflow 与原生数据模型

初始化拓扑仍为：D1 与 CDECR 并行 → O2 → D2 → D3 → O4 配置、交付、注册 → 激活准备与提交 → Bus / Runtime 实际接纳。各节点的完整依赖与 schema 见源模型审查；本轮不改变拓扑。

| 范围 | 已有、可复用的事实 | 对照前端与契约的实际缺口 |
|---|---|---|
| 初始化 | 父状态、动态节点、attempt/event、失败恢复、active revision、消费者 ACK | 六步完整失败集合和可靠时间；持久 HTTP 操作回执；用户运行意图与迟到激活之间的约束。云摘要仅保留最多 10 个失败节点，不能直接当完整进度 |
| D1 | C1/C3/C5、Future Nodes、正式报告引用、Global Bundle、历史 run | 分项读取与下载、当前引用解析；Bundle 没有独立更新时间，不能用 run.updated_at 冒充 |
| CDECR / O2 | 单文档结果、原子与 Package、冻结 epoch、DeltaBatch、Canonical Event/Fact 版本与成员关系 | 将内部身份与公开 Event/Fact 分开；结构化 Reference Delta、移除前内容、生命周期摘要；不能直接展示内部候选作为正式事件 |
| D2 | O0 Shell 构建、各 Shell 的 O1 State/Realization/Gaps、正式 Units、ShellOutcome、COMPLETE/PARTIAL | 从 active pin 精确读取，按 Shell/Unit 提供内容；失败 seed/checkpoint 不自动成为正式阶段性 Unit |
| D3 / O3 | 固定 OR 的 PolicySet、Policy source_refs、条件/calibration、Runtime projection、消费账本、维护 patch | 完整 Policy revision、实际生效链上的逐 Policy 生命周期、全部真实 hit 与消费的区分、时间线与周期聚合 |
| O4 / Bus | Source/Binding/PollState、Raw/Standard Revision、StreamItem/Member、源配置版本与窗口 | 所有 buffered member 到 Case 的关联；连续运行区间；正文补全尝试统计；可恢复的页面消息增量 |
| Runtime | Case、W1/W2 真实 turn、W3、路由、effect、candidate、trade、journal 与非阻塞恢复 | 完整阶段时间和最终结算时间；全新图、节点摘要与增量；真实交易结果关联。当前 terminal 小投影不足以支撑前端图 |
| 日结 / 休市 | 分批 sweep、O2/O3 候选维护、分支 Event Library、CAS 激活、0/1 最终候选释放、gap 隔离 | 将维护结果准确关联语义日与所消费的批次；不能将不同分支的相同数字版本或 E#/F# 合并 |
| 交易执行 | 不可变 profile、intake、ENTRY/EXIT、委托与 Fill、修订、FIFO 归属、费用记录 | ticker 级执行绑定；明确首次释放时刻；控制操作与待执行 ENTRY 的关系；按归属份额计算 EXIT 净收益 |
| 用量 / 成本 | W1/W2 nullable usage、通用 usage event、Worker telemetry | 可证明的真实调用身份和全节点归属；累计回执去重；cached 缺失表达；API/Codex 分域及固定产品计价 |

REALTIME 的 W1/W2 并行和 CLOSED 的顺序/跳过 W2 是真实差异。按需求，首轮“并行阶段平均延时”只纳入实际并行样本；休市 Case 仍呈现真实耗时与执行路径，无需为前端图改变 Workflow。

### 2.2 当前后端接线存在的关键问题

1. **模式接纳不能直接复用现状。** [scheduler](../../src/doxagent/runtime_scheduler/service.py) 的 `admit_activation()` 写入 `MonitorMode.TRADING`，`tick_ticker()` 又以该模式判断是否运行 Runtime；[消费者](../../src/doxagent/ticker_initialization/consumers.py) 会反复进行 active 接纳。新 API 不能只保存一个 Paper/Live/消息监测字段，仍让底层按旧内部枚举运行。三种前端模式都必须运行分析，只有交易释放权限不同。
2. **全局 profile 是选择入口的缺口，执行器并非只能管理一个环境。** [TradeOutputService.record](../../src/doxagent/persistent_runtime_v2/trade_output.py) 仍读 `trade_execution/active`；但 [ExecutionRepository](../../src/doxagent/trade_execution/repository.py) 已按账户保存执行与 Fill，[Executor.broker](../../src/doxagent/trade_execution/executor.py) 已按账户和连接身份管理多个会话。可复用这些能力补 ticker 绑定，不需要每次启动一个 ticker 就切换全局 profile。
3. **暂停状态并不是全链路停止证明。** scheduler 暂停会改 Bus/scheduler 状态；coordinator 另有自己的 pause 记录、已提交的 future，以及跨 ticker 的 effect/delivery pump；独立 executor 继续推进已接管任务。只看到 ticker=PAUSED，不能宣称所有新 Entry 已停止。
4. **现有读取形状与新页面不同。** Dashboard 的消息转换仍有 summary、固定 processing 状态和空 Runtime 关联；现有 HTTP 接口和内部 worker event 不能直接充当草案 DTO 或页面 SSE。
5. **读取路径必须服从真实产物来源。** 初始化 research adapter 明确本地优先，现有 research HTTP builder 则可根据配置接 hybrid/Postgres 与远端正文存储。后续必须从 V2 activation/产物登记定位来源，不能将另一 reader 的 latest 当作当前激活内容。
6. **“不可用”是数据真实性协议，不是交付豁免。** 当前不能证明的历史可以返回 coverage 缺口；新运行应该产生的 Case 时间、消息关联、增量和 usage 仍须补齐，不能用大量 UNAVAILABLE 宣称前端已完成。

### 2.3 可确定的后端方式

- 保留现有 V2 领域仓库和不可变产物作为权威来源；HTTP 查询与控制操作分开。查询不启动研究、维护、下单或补历史任务。
- 控制请求返回持久操作身份，由现有持久执行体系完成接纳与结算；操作已接受、初始化已排队、Workflow 成功、实际成交分别表达。
- 页面消费稳定关联、摘要和预先维护的聚合事实；真实源数据在业务事件发生时补齐可查询记录。按范围增量更新，并允许依据保留证据重建读取结果，不重跑模型来“补统计”。
- 摘要、KPI、索引与正文分开；按 activation / run / snapshot 固定引用，详情按身份读取。初次加载、分页、分钟更新与 SSE 使用一致的范围和水位。
- 沿用本地持久化与非阻塞云摘要方向。Supabase 继续承担既有认证和适当的有限投影；本轮不扩成云端全量运行数据库或多租户产品。最终查询必须满足 PRD §4.6，不能先从 Supabase 拉全量数据再在 API 出口裁剪。
- 本轮没有新增“执行主机离线时，云端仍可完整查询全部历史正文”的可用性承诺；现有云摘要本身不具备该能力。

以上只确定实现边界，不提前决定表结构、文件拆分、部署拓扑、任务阶段或开发排期。

## 3. 仅需用户评估的四项核心决策

### Q1：消息监测命中 Policy 后，是否消费它？

**现有依据：** 前端明确要求消息监测运行 Runtime 分析但不释放订单。当前正式输出把 Policy 认领、TradeRecord 与 READY intent 放在同一业务事务；没有独立的消息监测结果语义。草案只规定“不释放可执行交易”，尚未规定是否模拟消费。

**建议：消息监测只分析，不消费交易 Policy。**

- W1/W2/W3、事件候选和 O2/O3 研究维护继续运行；真实 hit 可以进入命中统计。
- 交易判断作为分析结果保留，不形成可执行 READY intent，不计正式交易触发、成交或已执行 Trade，不放入 O3 的已执行交易集合。
- 切换到 Paper/Live 后不补发监测期间的分析结果；只有生效边界之后的新分析输入，才能产生新的可执行交易。切换前已接收、尚未完成的监测 Case 也不因稍后完成就转为真实订单。
- “不消费”不意味着永远冻结该 Policy：O3 仍可依据新事实修改或 retire 它。

**备选：消息监测模拟完整策略生命周期。** 命中后形成虚拟消费和模拟交易输出，虽然不发单，后续 Policy 可因此失效；还需区分虚拟交易与真实交易对 O3 的影响。

**需要确认的业务差异：** 消息监测是研究观察模式，还是会改变策略可再次交易资格的影子交易模式。这无法仅靠禁止 broker 调用决定。

### Q2：Paper 与 Live 是否共享同一 ticker 的策略生命周期？

**现有依据：** 当前 active bundle 和 Policy 消费都以 ticker 为范围，消费键不含环境；执行与仓位账本则按账户区分。前端要求各 ticker 可选 Paper/Live，但未要求同一个 ticker 同时运行两套独立策略实验。

**建议：本期维持一个 ticker 一套研究与策略生命周期，Paper/Live 仅决定执行环境。**

- 不同 ticker 可以同时分别运行 Paper 和 Live。
- 同一 ticker 任一时刻只有一个新交易释放模式；Paper 切 Live 不重新激活已消费 Policy，也不重播 Paper 信号。
- 例如 MU 的某个 Policy 边界已经在 Paper 正式输出并消费，切到 Live 后，该边界仍已消费。新的条件边界或新 Policy 才可能再触发。
- Paper/Live 切换遵守既有交易方案：新释放交易绑定新环境；此前已正式释放并固定配置的执行、在途订单和持仓留在原环境完成管理。收益继续按执行的原账户分类。

**备选：Paper 是完全不影响 Live 的独立实验。** 此时不能只给消费键加一个 environment：Paper 交易可能已经影响 O3、Policy retire、日结与后续 active bundle，需要连同相关 Runtime/维护状态一起界定隔离范围。

**需要确认的业务差异：** 模拟交易是否允许改变以后实盘能使用的策略集合。这是产品模型选择，不是账户映射的实现细节。

### Q3：暂停、移除或切到消息监测，如何处理尚未完成的 Entry？

**现有依据：** PRD 要求停止运行和监测；草案已提出不停止必要对账与持仓退出，但未明确已提交 Entry 的剩余委托是否撤单、是否继续重试、未执行信号恢复后是否补发。当前独立 executor 不受 ticker 状态字段自动约束。

**建议：停止继续建仓，既有持仓按原退出计划管理。**

- 未发送 Entry 停止并记录结算原因；已经发送的 Entry 对剩余未成交量发起撤单并对账，不继续下一次建仓重试。
- 撤单过程中发生的真实 Fill 照常入账；已成交部分保留原定 Exit，必要的对账和 Exit 不随页面隐藏而停止。
- 不因暂停/移除主动立即平仓，也不把该操作当成全账户紧急停止。
- 恢复后不自动补发因本次人为停止而被压制的旧交易。已正式输出并消费的 Policy 不因撤单或未成交自动重新激活，沿用既有“正式输出时消费”规则。
- 在途研究结果可以留作分析与恢复证据，但不能绕过停止边界继续下单。未完成分析的续跑仍遵守现有语义日与过期规则。

**备选：只停止新分析/新 intent，已接管的 Entry 继续原重试与成交流程。** 这样操作可以较快结束，但用户点暂停或删除后，原执行仍可能继续增仓。

**需要确认的业务差异：** “暂停/删除”是否应停止既有 Entry 继续增加仓位。两种解释均不能从“停止运行”四个字唯一推出。只有停单边界已被确认，才能报告对应控制操作成功；未知委托不能伪称撤回。

### Q4：移除 ticker 后，能否通过“启动新标的”重新添加同一代码？

**现有依据：** PRD 禁止专门的“已移除”筛选和恢复入口，但没有明确禁止再次提交同一 ticker。草案 §4.2 进一步规定 `POST /tickers` 返回 `TICKER_REMOVED`，实际上将前端移除扩展成了该身份不可再次启动；这条是草案新增解释，并非已确认需求。

**建议：允许显式再次启动，不新增恢复按钮。**

- 移除后持续隐藏，后台重启、旧初始化回执、旧 SSE 或历史成交都不能自行恢复可见性。
- 只有用户重新在现有启动表单提交同一代码，才允许重新接纳；仍选择复用有效 active 或强制初始化。
- 保留同一 ticker 的研究、交易和审计历史，不创建第二份并行 ticker 身份，也不抹去此前消费事实。重新可见后，窗口 KPI 按该 ticker 的真实保留历史统计。
- 旧运行的迟到操作不能覆盖新启动的控制意图；保留原持仓退出责任。

**备选：维持草案的禁止重加。** 移除后的 ticker 不得通过当前前端再次启动，即使历史成果仍在；将来需要另行定义后台恢复能力。

**需要确认的业务差异：** 删除是结束当前监控，还是永久禁止该 ticker 经本期前端重新运行。

## 4. 已自行确定、不再要求用户评估的事项

1. **完全 V2 隔离。** 独立 API 与 DTO；内部 schema 名称的 `_v1` 后缀不作为工作流代际判断依据；不接已废弃 Workflow。
2. **当前与历史分开。** 当前 D1/D2/Policy/Event 依同一 active bundle；历史按指定 run/version/snapshot 读取。强制初始化期间保持旧 active 与原实际模式，候选成功接纳才切换；失败不提前改变现行成果。
3. **固定引用保留来源差异。** 人工独立替换 D2 而保留 D3 时，不把 Policy 原 source D2 改写为新的 D2；保留无法解析的 Shell 关联与“全部 Shell”可见性，不擅自重算下游。
4. **Policy 全文修改不等于重新允许交易。** 本期沿用原生 OR 条件/calibration 消费边界，单独增加全文 revision 以展示实质修改。特别是同 policy_id 仅改 decision 或 match_scope，已消费 Policy 不会自动重新获得交易资格；这是必须如实呈现的现有语义，API 不偷偷改 hash 或清账本。改变这一 Workflow 规则不作为补页面接口的隐含工作。
5. **新旧与命中服从最终裁定。** W3 未完成不制造最终结论；W3 完成后覆盖前轮结果。全部 hit、唯一 Policy、消费、intent、执行接收、真实 Fill 分开统计。
6. **交易策略保持既有约定。** 不重新讨论订单金额、报价/重试、FIFO、退出时点、账户保险丝，也不增加逐单 AI 风控或 Live 二次弹窗。Paper/Live 可用性仍依真实账户能力，已有握手报告不能当成交验收。
7. **历史按证据接纳。** 保留现有 V2 业务历史；能从正式记录确定性恢复的查询事实可回填，无法证明的时间、用量和关联返回 NOT_RECORDED/PARTIAL。验收、测试和候选记录不能仅因位于 V2 目录就进入业务 KPI；不重跑模型、抓取或旧订单来“修齐历史”。
8. **没有新增事实就不伪造字段。** D1 独立更新时间缺失保持缺失；D2 失败 Shell 保留正式成果和失败信息，不为满足 UI 发布未验收 checkpoint。
9. **时间与 KPI 沿用需求/契约。** ET 02:00、有效交易日集合、窗口完整性、固定价格与汇率不重新选择。Overview 的事件时间统计与 Runtime 的 Case cohort 统计保持明确区别；ENTRY Fill 才计执行，Overview PnL 只计需求明确的 EXIT 归属净收益。
10. **读服务补足真实事实。** 完整消息成员关联、阶段时间、正文补全 attempts、逐 Policy 生命周期、Reference 前后快照、真实 usage 身份与页面 SSE 都是后端需要完成的能力，不要求用户选择是否要做。
11. **访问与资源边界沿用现状。** Supabase 认证和可信开发者访问；不新增用户配额、租户隔离产品、多账户配置页面、收益审计正文或 O4 Repair 自动链。
12. **工程选择自行收敛。** 保留协议外部语义，具体聚合存储、索引、outbox、操作并发、游标过期、读取一致性和测试划分在下一轮详细方案中给出，不逐项上交审批。

## 5. API 草案需在决策后收口的范围

| 决策 | 需要明确或修订的契约位置 | 主要联动 |
|---|---|---|
| Q1 消息监测消费 | §4.2、§9 Runtime 与结果、§10 正式触发、Policy effective/consumed | 分析交易结果、O3 输入角色、切换前 Case 是否有交易资格 |
| Q2 Paper/Live 生命周期 | §4.2 模式、§6 Policy 消费、§9/§10 交易事实 | ticker 模式生效边界、跨环境消费、在途交易固定引用 |
| Q3 暂停/移除 Entry | §4.1/§4.2 Operation、§9/§10 执行状态 | 停单完成条件、Entry 取消/压制原因、保留的 Exit 责任 |
| Q4 移除后重加 | §4.2 `TICKER_REMOVED` 与启动条件 | 持久可见性、新旧控制操作、历史统计范围 |

不在本轮直接修改草案，以免建议被误认为用户已确认。四项决策确定后，下一轮再形成详细后端开发方案，并同步收口契约与配套类型中受影响的部分。

## 6. 复核与交付边界

- 已复核前端数据需求的全部业务范围、PRD Part 1 §4.6、API 草案与节点审查记录；重新核验 69 份源码指纹一致。
- 重点补查源文件：runtime_scheduler/service、ticker_initialization/consumers 与 runtime_inputs、persistent_runtime_v2/coordinator/trade_output/maintenance、trade_execution/repository/executor/worker/intake、D3 identity/runtime_projection、dashboard_api app/auth/research_lanes。
- 已查阅现有初始化、Runtime、交易执行方案与交付/操作记录，排除了其中已决定的热切换、正式输出消费、账户归属和恢复问题，不把它们重新作为需求问卷。
- 本轮新增本审查文档；未修改应用代码、API 草案或配套类型，未运行新测试、启动进程或改变业务状态。
- 用户可按 Q1–Q4 分别回复“采用建议”或说明不同口径。当前没有需要凭据、外部服务或账户操作才能继续构思方案的技术阻塞。
