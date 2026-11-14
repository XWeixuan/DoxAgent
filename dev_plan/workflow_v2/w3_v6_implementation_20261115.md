# W3 v6 Case 隔离与运营职责重构：开发记录

2026-11-15。对应 `w3_case_isolation_and_operations_refactor_plan_20261115.md`。本轮只开发、离线验证和本地整合，没有生产发布、切换活动 Bundle 或收费模型测试。

## 已实现的行为

- 新 Case 不再按 Policy/Event 查找历史线程。公共模板保留，每个 Case 创建自己的物理线程；Trade→Operations、重试和恢复继续使用本 Case 原 record/thread/Job。创建 registry 与绑定 Trade stage 在同一短事务内完成。删除跨 Case acquire/link/O2 关联同步路径，历史关联表保留但不再写入。
- Case 线程期限固定为首次 Trade 接受后 48 小时，恢复不续期；必要的 owner/租约、原 Job 查询、结果消费、清理和失败恢复继续保留。
- 新增 `persistent-runtime.w3.v6`、`w3-entry-v6` 和三资产 Bundle。模板仍只继承 AGENTS/Skill，冻结的 `operations.md` 全文只在 Operations 本轮 JSON 前注入；Trade 不提前获得运营文件。v4/v5 仍按原契约恢复。
- v6 Trade 仍输出四字段方向决策；Operations 严格只收 `pin_actions`、`watch_actions` 两个数组，空数组也是完整成功结果。复用现有动作权限、容量、可见代次及部分动作错误记录机制。
- NEW/OLD 始终来自 W1，HIT/RELATED 来自 W2，LONG/SHORT/NO_TRADE 来自 W3。NO_TRADE 在 Trade 提交时即写终态归档；coverage gap、OLD+HIT 日记录也按上游判断确定性提交，不等待 Operations。
- v6 NEW Case 的 W1R3 与 W3 是兄弟 Effect，使用原 W1 会话、提示词和模型；W3 不等 R3。R3 错误单独保留，不影响交易终态。事实抽取窄更新与 v6 保存时保留已提交抽取，解决双向覆盖风险。
- 现有 8 线程后台池最多领取 7 个后台 Effect body，保留短 ACK/投影执行槽；实时 W3 继续直派，没有新增线程池、并发门槛或常驻服务。

## 输入及研究范围

`w3_v6.py` 集中构造白名单投影，后端冻结业务对象不删字段、不原地修改：

- Trade 删除 `case_type`、`trading_context`、WATCH、内部 Policy 身份及 W2 reason。Policy 只含标题、真实可追溯 cutoff、传导路径和精简 Condition/reference_state；同名标题按冻结身份确定性区分。HIT 的 Policy/Condition 缺失直接报错，不伪造身份或改成 NO HIT。
- 当前 Policy 原生数据没有独立可靠维护时间时，cutoff 为 null；缺失原因保存在 context manifest 的 `input_projection_issues`，不把 PolicySet 发布时间伪装成单 Policy 时间。
- `recent_trade` 与 `RECENT_TRADE` 共享一次冻结：同 ticker，消息截点前 `[cutoff-48h, cutoff)` 的正数量 ENTRY 实际成交，按 account/contract 缩小候选，再按 family/correction 选有效修订。排除 EXIT、手工单、零成交、窗口外成交；部分成交可入选，Case 去重。成员、成交证据、身份匹配和消息/四字段决策一起保存，重试不漂移。
- 标签还需真实 Event 或 Policy HIT 身份匹配；仅 RELATED/Prior 不算。新版不再查旧 72 小时意图集合生成该标签。回测缺成交证据时留空，不读取当前生产账本补齐。
- Operations 仅含已提交决策、输出处置、WATCH、PIN 和精简 Policy targets；SET 与 CANCEL 仍各自遵守原权限，能够取消旧 WATCH 不代表可以新建该 Policy WATCH。
- v6 research 只编译 D1/D2，使用固定派生范围凭证，服务端执行“调用者范围与 manifest 范围取交集”。即使持有旧 context_ref，也不能读出 Policy/Reference。catalog/search/read/raw/source/cursor 都从裁剪后的对象来源取数。生产不提供旧 state/history/evidence 旁路。
- 保留原行情、Web 和只读 IBKR 能力；通用 data MCP 的 W3 角色原本仅允许只读 IBKR 工具。离线回测保留数据集自身的模拟证据和行情，关闭实时 Web/IBKR。

## 消费者与兼容

Read/API 的 v6 最终来源是 W1/W2，W3 方向独立；不再把 W2 reason 冒充 W3 Policy 复审理由，运营新旧/命中理由标为不适用。日维护 feed 接收版本化 coverage 快照，历史记录不改写。

完整业务输入回测 runner 支持 v6、第三资产指纹、冻结运营指令和隔离研究入口。旧 curated decision-only v2 数据集仍使用其既有 v5 输入契约；它缺少完整冻结 W2/Policy 身份，不会被偷偷升级成 v6。需要测 v6 时使用包含原 Case/历史状态的完整输入数据集，并另建 run。

## 验证与上线边界

新增回归覆盖独立线程及原子绑定、恢复身份、两数组契约、方向与 HIT 解耦、Trade 先归档、R3 独立失败及双向覆盖保护、7 个阻塞后台任务下 ACK/实时 W3 可执行、同名 Policy 和错误 Condition、48 小时成交及修订/边界/冻结、MCP 旧凭证上下文越权拒绝、阶段指令注入和 v6 完整回测。

相关回归 **253 通过、1 个既有基线失败单列排除**；最终输入边界补测 **28 通过**（与前述有重叠，不累加），新增 v6 专项共 20 例。测试证据位于 `eval/w3_v6_20261115/`。静态未定义名称检查与 `git diff --check` 通过。既有回测测试中，旧模型覆盖断言和 Ready 恢复断言已对齐当前生产参数（Operations 固定 Luna max、关闭 fast）；被 mock 的 prompt snapshot 契约文件改用有效 JSON，没有放宽业务校验。

另一个旧回测用例 `test_replay_uses_production_two_stages_with_isolated_evidence` 在未修改的 `4f3c74cc4` 基线同样失败：期望 READY，实际因历史冻结时钟得到 RUNTIME_DELAY_EXCEEDED。保留此历史时效校验，未为通过测试延长超时或伪造 READY；最终相关回归单列排除该已复现的基线失败。

当前 `contract.json` 仍为 v5；新增 `operations.md` 是带明确标记的占位文件。v6 Bundle 发布及 v6 完整回测拒绝占位内容。负责提示词的对话需交付对齐后的 AGENTS/Skill/operations 三件套，再通过现有 Bundle 流程激活 v6，不能只改版本标记。

本轮没有自然生产流量，不能宣称非 SDK P95≤5秒已获生产验收；上线后仍需按原 Trade、Operations、完整 Case 口径验收，包括内部等待、锁等待与重试。没有为达标放宽 timeout 或降低实时并发。
