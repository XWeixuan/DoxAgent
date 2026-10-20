# Runtime“结果未记录”排查（2026-10-21）

本轮只读检查生产 Read DB、Runtime DB 和运行源码；未改代码、配置、历史状态，未重放 Case。

## 结论

这句话不等于“W1/W2/W3 没有输出”，也不是 Runtime 中的一种失败状态。它是前端根据 **读模型结果标签为空 + Case 已收束** 显示的兜底文案。

根因是新版 W3 的两阶段结果机制与旧读模型结果分类没有完整对齐：**不交易判定已经持久化，但不一定产生独立 ArchiveRecord；读模型又没有从该判定派生结果标签。** 因此正常的“不交易、OLD、无新增产物”路径会被误显示为“结果未记录”。

同时，一部分 Case 确实存在独立的后置 operations 失败。必须区分：展示标签缺口本身不影响编排；真实 operations 失败则会影响本该由该阶段处理的事件、WATCH、PIN 等动作，不能靠改文案修复。

## 1. 实际样本与日期口径

取证时间约 UTC 2026-10-20 19:03—19:07，即北京时间 10 月 21 日约 03:03—03:07、美东 10 月 20 日约 15:03—15:07。

Read DB 检查 semantic_day >= 2026-10-20 的 390 个 Case，其中 **17 个**符合页面“结果未记录”的精确条件。17 个均是 COMPLETED、initial_route=W3、resolved_route=ARCHIVE、W3 已 RESOLVED，且正式 trade_decision 为 NO_TRADE。

| 口径 | 此文案 Case 数 | operations COMMITTED | operations FAILED |
|---|---:|---:|---:|
| 当前美股业务交易日 2026-10-20 | 17 | 13 | 4 |
| 美东开盘后创建 | 10 | 9 | 1 |
| 北京时间 2026-10-21 创建 | 3 | 3 | 0 |

因此“今日”若严格指北京时间今日，这 3 个都属于正常执行后的展示缺口；若指界面当前业务交易日，则包含 4 个真实后置失败。

17 个原始 Case 均能读取到 w3_trade_decision。13 个 operations 成功的 Case 均为 novelty=OLD、delta_candidates=[]；route_to_w3 与 w3_post_decision 两个 Effect 都 COMPLETED。17 个 Case 均没有 archive、candidate、badcase、trade record、coverage gap 独立记录。这与各自执行路径相符，不是投影器尚未追上已有 ArchiveRecord。

明细 CSV：`eval/runtime_missing_result_audit_20261021/affected_cases.csv`。

## 2. 文案如何产生

`frontend/v2/src/pages/runtime.tsx:929` 的 CaseResults：

1. 从 CaseSummary.results 中排除 TRADE_EXECUTION、TRADE_NOT_EXECUTED，得到 judgements。
2. 如果 judgements 为空，且 result_settled=true，显示“结果未记录”。
3. 同样为空但 result_settled=false，则显示“尚未形成结果”。

这段代码**没有检查模型输出、W3 trade_decision 或 operations 是否存在**。首轮 first_round_tags 单独展示，也不算这里的 judgements。

`v2_read/trade_outcomes.py::rollup_case_trade()` 中，result_settled 的核心口径是 Case 已 COMPLETED/FAILED/UNAVAILABLE，且交易执行汇总不处于 PENDING/UNKNOWN；没有交易意图的 NOT_APPLICABLE 也满足它。这是技术收束/执行状态，不能作为“业务标签应该非空”的充分条件。

## 3. 标签为什么为空

`v2_read/projectors.py::case()` 主要保留原 summary.results；若 Case 失败则加入 FAILURE。它不会因为 resolved_route=ARCHIVE 或 trade_decision=NO_TRADE 自动加入“归档/不交易”。

其他标签由独立产物事件增加：

| 原始记录/证据 | 读模型标签 |
|---|---|
| runtime_v2_archives | ARCHIVE |
| runtime_v2_candidates | EVENT_DISCOVERY |
| runtime_v2_badcases | BADCASE |
| 正式交易意图/执行证据 | TRADE_INTENT / TRADE_EXECUTION / TRADE_NOT_EXECUTED |
| Case FAILED/UNAVAILABLE | FAILURE |

于是，没有这些独立产物、但确实完成了 NO_TRADE 的 Case，results 就会保持空数组。这是**后端 Read 投影与前端展示契约的缺口**，不只是中文枚举漏翻译；原始 W3 决策并没有丢失。

已比对实际 projector 的 projectors.py、graph.py、trade_outcomes.py，以及 scheduler 的 service.py、w3_execution.py，均与本次读取的本地实现一致；不是仅依据本地代码推测生产。

## 4. 新 W3 的真实机制

`persistent_runtime_v2/w3_execution.py`：

- 第一阶段提交 trade_decision；NO_TRADE 路径不创建 TradeRecord，正式阶段 disposition 为 NO_TRADE。
- `_restore_trade()` 记录 w3_trade_decision、decision_completed_at，并将 resolved_route 设为 ARCHIVE、side_effects=[]。**这里的 ARCHIVE 表示“不交易的终态路由”，不等于已经写入 runtime_v2_archives。**
- 同一提交流程排入 w3_post_decision Effect；无需等待后置模型才能收束交易判定。
- 第二阶段仅在有 delta_candidates 时分配 provisional；NEW 且无 Policy 时记录 coverage gap；OLD 且存在 Policy 时记录 badcase；另执行 WATCH/PIN 等动作并提交 operations。
- 正常 OLD、无 Policy、无 delta 的后置结果无需产生这些独立产物；当前这一路径也不额外创建 ArchiveRecord。

`service.py::_refresh_case_completion()` 对 w3.v4 两阶段契约明确排除后置 Effect 对交易判定完成状态的影响：decision_completed_at 存在即可将 Case 标为 COMPLETED/OK。后置失败单独记录在 w3_operations_status/error 与对应 Effect。

所以“Case COMPLETED + operations FAILED”是现有设计的状态组合，不应通过把所有这类 Case 改成整体 FAILED 来掩盖两阶段边界。

## 5. 是否影响后续执行

### 13 个正常收束样本

- NO_TRADE 已正式提交，未生成交易意图；不交易本来就是结果，不应额外重跑交易研判。
- operations 已提交；没有新增事实是该阶段的明确输出，不是因为结果标签为空而被跳过。
- 调度器、Effect 重试、交易输出、WATCH/PIN 不读取 CaseSummary.results 或 result_settled 决定执行，因此空标签不会反向阻止它们。
- daily.close 从原始候选、交易、badcase、coverage gap 清单取输入，不依赖读库 ARCHIVE 标签。本批正常 OLD 且无新增产物，不会仅因缺 ArchiveRecord 丢失应有日终输入。
- **展示/统计仍受影响**：graph.py 的结果节点、结果计数依赖 summary.results，因此这些 Case 不会进入 ARCHIVE 结果节点和对应统计。若按“归档”筛选，它们也不能靠空标签正确归类。这属于读侧可见性缺口，不是交易执行阻塞。

### 4 个真实后置失败样本

| Case | 标题简述 | 后置失败原因 |
|---|---|---|
| case_1a0f3650a92e4a66a33d45d107fb39dd | JPMorgan finally turns optimistic on Intel | OLD 缺少合法 Reference View 引用 |
| case_14fd3da985384721993f77fb319b9b3d | Unimicron IC Substrate Crunch | 引用不在 pinned Reference View 范围 |
| case_f30c1d6292df4889803b52098270a192 | Google DeepMind / Minecraft | Worker jobs 接口 409 Conflict |
| case_59fc7f7248b84709bfaa0af665214b99 | Command Alkon / Gedis | START_THREAD timeout |

这 4 个均为 route_to_w3 COMPLETED、w3_post_decision FAILED。交易判定不丢失，也不会因为 operations 失败自动重发交易。后置阶段未正常完成，其本来可能产生的 delta、覆盖缺口或 WATCH/PIN 动作不能视为已完成；也不能在没有有效 operations 结果时断言“一定丢了某条事实”。

这些失败是独立问题。把“结果未记录”改成“不交易”后，仍应保留后置失败标识和错误详情；后续若修复恢复，只处理原 operations 阶段，保留已提交交易判定。

## 6. 建议的修复边界（本轮未实施）

1. 读侧根据正式提交的 W3 决策/终态路由表达“不交易/归档”，而不是要求每个正常 Case 必须额外产生副产物才有标签。不能仅凭 Case COMPLETED 或结果数组为空，就猜成不交易。
2. 让页面清楚区分“交易判定完成”和“后置 operations 失败/待处理”；保留已有后置状态字段，避免“结果未记录”遮蔽真实 NO_TRADE。
3. 同步结果节点、计数和筛选投影；单改一处前端文案无法修复图表分类。
4. 不为补一个展示标签而重跑 W1/W2/W3、补交易意图、或放松 Event 引用范围。历史读模型可从正式决策重投影，无需改原始业务回执。

证据固定在 `eval/runtime_missing_result_audit_20261021/`：read.json 是 Read CaseSummary 和读侧源码；native.json 是对应原始决策、operations、Effects、stages 与独立产物核对；affected_cases.csv 是 17 条清单。生产持续运行，这些数字为本轮快照。
