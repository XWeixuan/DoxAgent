# W3 编排与 Schema 重构：本地实现与验收记录

日期：2026-10-14。依据：`w3_orchestration_schema_refactor_plan_20261013.md`，包括纯信息标签及 OLD 入口补充规则。

本轮已实现工程链路，保留旧执行包与旧 Case 的恢复方式。没有部署、切换生产执行包或清理远端历史会话。真实交易业务模型验收仍需后续 prompt/skill 适配；本轮没有修改其内容。

## 1. 已实现的业务行为

| 范围 | 实现落点与行为 |
| --- | --- |
| 两阶段 | `w3_contracts.py`、`w3_execution.py`。阶段一只有 NO_TRADE / POLICY_TRADE / EXPERT_TRADE；严格互斥。合法结果先持久化，再在同一 Runtime 事务中提交交易输出与后置 Effect。阶段二在同一个子 Thread 上处理 Fact、Policy coverage、WATCH、PIN，不能修改交易决策。 |
| 恢复 | 已保存的合法阶段结果直接重放业务落账，不再调用模型；阶段一保存后也不依赖重新读取研究文档。后置操作单独重试、单独显示失败，不把已提交交易改为失败。纯后置 pending 不阻塞 delivery、CLOSED selection 或日维护；晚到业务记录沿已有 supplemental maintenance 补交。 |
| 交易边界 | OLD 不否决已经进入 W3 的交易。Expert 不消费 Policy、不受 Policy HIT 或方向消费状态限制；取消新合同 Expert 的旧 Event 自动去重拦截。Policy Trade 继续使用冻结 Condition、revision、generation 及既有实际输出事务校验，不自动改选 Expert。CLOSED 仍先形成候选。 |
| 入口与标签 | 新合同使用 WATCH → 任一 LOW → 正常置信 OLD 归档 → NEW 现行规则的独立入口判断。第一阶段注入唯一 case_type 与可叠加 additional_tags；标签不控制路由、权限和交易资格。RECENT_TRADE 使用 72h 内真实意图的实际 Event / Policy HIT，排除 RELATED 和 NEW 的 Prior 引用。 |
| Thread | `w3_threads.py`。Template 按 ticker、研究组合和 W3 兼容配置单次初始化；Policy HIT 原顺序优先于 RELATED，再考虑实际重复 Event。最高优先级匹配组全忙时立即 Fork；不增加 ticker 并发上限。Case 在两阶段之间保持独占。 |
| 期限与清理 | 新 Case 接收时固定 accepted_at 与 48h 期限；第二阶段及同 Case 重试不续期。后台维护检查 Worker 活动任务，释放空闲 affinity capsule 后调用真正的 SDK delete，并回收专属 `w3-case-*` 工作区。Template 排除删除。旧线程只在 Runtime 与 Worker 双重归属证据成立后登记为 LEGACY_CASE；无法证明归属的线程不删除。 |
| 研究资产 | `w3_research_assets.py`。D1 原文和自然章节、D2 完整 Unit、D3 完整 Policy 均可访问；有索引和内容 hash。无 Unit ID 使用版本内派生定位。D3 新发布增加 `policy_origins.json` sidecar，继承研究、编辑及整合来源；无来源证据的历史 Policy 标为 unassigned，不猜 Shell。研究切片按内容版本按需生成，进程内有界缓存，工作区按版本避免重复传输。 |
| 历史 | 首次接收冻结 `[t0-72h,t0)` 的成员和执行小快照，不限制条数；包含失败、过期、重复等真实意图。分析态决策单独标记为未释放意图，不参与 RECENT_TRADE；未释放 CLOSED 候选排除。正文从已有业务记录读取，不把整个历史正文复制到每个 Case 的 DB 输入。 |
| WATCH / PIN | ticker 全局各最多 2 条，事务内处理。WATCH 沿 D/D+1、D+2 到期，不因交易或 Policy 消费自动撤销；有效 WATCH Policy 保留 W2 可召回性。PIN 只允许 SET 当前实际消息，24h 到期；重复 SET 不续期，取消按已注入身份处理，过期重建后的 PIN 使用新身份，避免旧 CANCEL 误删。 |
| 信源与权限 | `source_identity.py` 沿 Raw → Standard → Member → Envelope/Snapshot 保留 source_name。混源 BUFFER 顶层 null、成员逐条保留来源；DATE 不伪装成精确零点，UNKNOWN_FIRST_SEEN 不伪装成发布时间。W3 显式 live Web Search；后续行情能力迭代已开启仅含只读 IBKR 白名单的 Data MCP，其他辅助 MCP 关闭，详见上线检查报告。 |
| 留存 | Worker ACK 在宿主持久化合法输出后进行，清理大请求、agent_loop、重复结果及事件；保留幂等身份、状态、统计与必要错误。ACK 失败登记重试。业务 Case、交易历史、WATCH/PIN 不依赖 SDK 会话存活。 |
| Read / API / UI | 共享 TS 合同与两侧生成 schema 已同步；展示交易决策、后置状态和错误、两个阶段尝试及耗时、WATCH/PIN 快照和动作接纳结果、规范信源；导出包含对应信息。母会话初始化只记用量，不冒充某个 Case 的交易尝试。 |

## 2. 新执行包接入合同

全局 `orchestration_revision` 仍为 3；新增 `contracts.w3_contract_version = persistent-runtime.w3.v4`。旧包不带该标记，继续使用旧 W3 合同。代码不会自动把现有旧 prompt 宣称为新合同兼容。

新包需显式提供以下外部资产：

- `w3/AGENTS.md`：公共角色指令。
- `w3/skills/skill.md`：两个阶段共同使用的唯一 Skill。

2026-10-14 单 Skill 接入后，新包增加 `w3_asset_layout=agents-skill.v1`，不再要求 task.md/template.md。Template 注入唯一 Skill，子 Thread 继承；阶段 user prompt 使用工程生成的阶段 JSON 和产物路径。旧四文件包仅供已冻结 Case 恢复。周末 sweep 仍由独立 selection 任务加载自己的 Skill。

`ExecutionBundles.publish(..., w3_contract_version="persistent-runtime.w3.v4", activate=False)` 可以发布待验收包而不改变 active。manifest 也支持相同标记和 `activate`。启用 W3 的持久化运行工厂现在默认发布新合同；用户完整 push/build/deploy 后的新 Case 会采用新包，历史冻结 Case 保留原合同。不需要新增逐 Case 人工审批。最新检查、外部 IBKR 依赖及验收边界见 `w3_prelaunch_check_20261014.md`。

## 3. 验证记录

以下为独立测试组，存在重叠，不累加成总测试数。

| 检查 | 已确认结果 |
| --- | --- |
| 新 W3 / Worker 生命周期 / 新 Read 投影 | 22 项通过，覆盖三种决策、保存后恢复、后置失败隔离、关联线程忙时 Fork、TTL、无条数截断的历史、PIN 并发与过期重建、OLD 可交易、CLOSED 候选、外部资产合同、来源及切片等。 |
| Runtime 编排恢复、旧 W3、Worker / native Policy 相关回归 | 多组通过：87 项 Worker/旧 W3 等回归；44 项恢复和方向使用等回归；包含新 W3、API 及编排恢复的一组 45 项通过；最终核心与恢复复核 40 项通过。 |
| D3 发布 / 维护及相关 API 回归 | 40 项通过；新 sidecar 发布仍保持 Policy / Projection 合同不变。 |
| Bus / Runtime / Read 回归 | 一组 89 项通过，另发现旧断言预期 `Reuters`，已按新规范信源 `reuters` 修正。最终新 W3 与完整 Bus / Distribution 组合 53 项通过，复核了原发者优先、域名回退及未知值不使用采集任务名。 |
| 前端 | 58 项 Vitest 通过；TypeScript 类型检查及生产构建通过。 |
| 浏览器 | Playwright 10 项通过，覆盖 1262px / 1559px；包括交易决策保留、后置失败、分阶段尝试、PIN 展开及无页面横向溢出。使用组件测试数据，未宣称为生产真实 Case UI 验收。 |
| 静态检查 | Python 编译通过；新 W3、生命周期、信源、来源 sidecar 模块及对应新测试的 Ruff 检查通过；范围内 diff whitespace 检查通过。 |
| 资产边界 | 与本轮开发开始时的 hash 基线比较，169 个受保护 prompt/skill 资产未变化。 |

真实本机 SDK 验证使用独立测试工作区和无业务数据的初始化请求，仅返回 ready；没有提交交易。验证了初始化完成后的 Fork、read、resume、list、子线程 delete 与测试母线程 delete。正式母会话保护由 Worker 注册表测试单独验证；探针删除的是本轮专门创建的测试会话。

证据文件：`eval/w3_refactor_20261013/sdk_lifecycle_receipt.json`、`scope_receipt.json`；浏览器截图在 `frontend/v2/test-results/w3-v4-1262.png` 和 `w3-v4-1559.png`。

## 4. 交付边界

本地工程代码与验收已完成；没有提交 Git commit、部署镜像、启用生产新合同或删除现有生产会话。工作区原有其他任务改动已保留。

未完成且不属于本轮工程验收的部分：新版公共 prompt/skill 的业务适配、真实消息上的完整 W3 模型业务验收、远端部署与生产延迟/压缩/存储的持续观测。现有旧文案仍包含与新业务冲突的规则，因此不能仅凭本轮 mock 与 SDK 生命周期成功宣称新版真实交易已经可上线。

原生 SDK 压缩机制仍然存在；完整 72h 正文与频繁 Resume 仍可能触发压缩。本轮没有裁切正文、偷偷限历史条数或新增模型摘要来规避这一事实。

## 5. 2026-10-14 追加：按 Case 选择 W3 模型

新 Case 在进入 W3 时，根据当时 W2 的 `policy_hit` 冻结 `w3_execution_profile`：HIT 使用 `gpt-6-sol / medium`（用户称 mid），其余使用 `gpt-6-luna / max`，包含仅 RELATED、WATCH 跟进和低置信入口但无 HIT 的 Case。

Template 初始化、子 Thread Fork、第一阶段和第二阶段请求使用同一份配置。后续 Policy 改判不会更换本 Case 的模型；模型配置参与 Thread 兼容键，Sol 与 Luna Case 不交叉复用新合同子线程。旧合同的新 Case 也采用该选择；已经入场且未保存新配置的旧 Case 维持原冻结参数及既有恢复逻辑。新 Case 的明确配置优先于旧的全局 W3 model override，避免其覆盖按 HIT 分配的模型。

改动位于 `w3_models.py`、W3 入口、两种 W3 Runner 和 Thread 路由。新增测试验证了 HIT / RELATED / OPEN 的三个实际 Worker 阶段请求、模型配置冻结及跨模型不复用；连同新旧 W3 与回执恢复回归，共 36 项通过。没有修改 prompt/skill、其他节点模型或生产部署。
