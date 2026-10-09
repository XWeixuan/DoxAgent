# BE 10 月 5 日 Policy 命中但未交易：生产追溯

日期：2026-10-06，Asia/Shanghai。范围：指定新闻的只读生产取证、代码核对和隔离复现；未修改生产状态、重放 selection 或提交订单。

目标标题：Up 228% in the Past Year, Bloom Energy Expands Manufacturing Capacity - Yahoo Finance。

Case：`case_c35b297c6dec48f88f44c00f96128821`；消息：`std_2a0fbd2fee844bbdaf880c882b2994ba`。

## 结论

没有交易的直接原因在 CLOSED 候选筛选链路：消息产生了 LONG 候选，但本轮 selection 早已完成，其不可变快照不包含该候选。正式 intent 和 Executor execution 均为 0，没有到达券商，不能归因于保证金、行情或拒单。

15 小时 33 分主要是 sweep 等待与分波串行排队，模型并未持续推理十几个小时。Yahoo SOURCE_SWEEP 迟迟未收尾，全来源屏障使已经入队的 Google News 消息一起等待。已有来源优先调度修复使 Yahoo 在 20:53 UTC 收尾；候选筛选缺陷仍存在。

## 时间线

下表为北京时间；括号内是 UTC。系统 trading_date 为 `2026-10-05`，closed_cycle_id 为 `2026-10-03`。

| 时间 | 持久证据 |
| --- | --- |
| 10/5 06:17:57（10/4 22:17:57） | 新闻 published_at |
| 10/5 14:07:54（06:07:54） | Case 入队，模式 CLOSED、属于 final sweep |
| 10/5 15:45（07:45） | selection_snapshot 截止点；只包含 9/28 遗留 SHORT 候选 |
| 10/5 19:47:57（11:47:57） | selection SUCCEEDED，candidate_id=null；理由讨论 Oracle/项目延期等 SHORT 前提 |
| 10/6 04:53:29（10/5 20:53:29） | Yahoo SOURCE_SWEEP SUCCEEDED，pending_job_count=0 |
| 10/6 05:22:00（21:22:00） | 目标 Case 首次 W1 请求开始 |
| 10/6 05:24:52（21:24:52） | W1 R3 完成 |
| 10/6 05:35:41（21:35:41） | W2 R1 开始；一次 120 秒 provider_timeout，第二次成功 |
| 10/6 05:40:33（21:40:33） | W2 最终命中 `pol_f7cde392e3eb2e1ef3d3` 的 C1 |
| 10/6 05:40:35（21:40:35） | create_trade_record Effect 实际生成 CLOSED LONG 候选，PENDING |
| 10/6 05:40:36（21:40:36） | Case COMPLETED；两个 Effect 均 COMPLETED |
| 10/6 05:50:44（21:50:44） | final SWEEP SUCCEEDED |
| 10/6 07:25:26（23:25:26） | sweep maintenance SUCCEEDED，Event V16 / Policy V10 |

入队到 W1 首次请求约 **15 小时 14 分**；首次请求到 Case 完成约 **18 分 36 秒**。6 次 provider 请求的 latency 合计约 **7 分 38 秒**，包含一次 W2 超时。W1/W2 之间还有波次屏障及其他消息等待。Case 的 `hot_path_latency_ms=293883` 不表示入队到完成的总耗时。

## 四个具体问题

1. **全来源屏障放大单源迟滞。** `RuntimeCoordinator._sweep` 等全部 SOURCE_SWEEP 进入 SUCCEEDED/FAILED 才处理消息。Google News 在 06:10 UTC 已收尾，目标消息却等待 Yahoo 直到 20:53。旧 BusOrchestration 先创建实时轮询，再看 sweep；相同 binding 的 `_inflight` 被实时占据，sweep 被反复跳过。当前 Message Bus 镜像 `doxagent-v2:sweep-priority-20261006` 已核实先调度 sweep；不能把这一项说成尚未修复，也不能把其他容器里未使用的旧 Bus 模块误认为当前抓取路径。

2. **候选快照提前冻结，等待对象混用。** coordinator 在检查 final maintenance 前就调用 `_freeze_selection`，按 candidate.created_at <= selection.due_at 冻结。即使稍后 sweep 正常完成，已经入队但迟生成的候选也永远不能进入快照。`_waiting_final_maintenance` 只等 due_at 后 4 小时，之后即便 sweep 仍未完成也执行 selection。这里应区分候选分析未结束和维护未结束：旧 bundle fallback 不能替代尚未分析的候选。

3. **未限定 closed cycle，旧候选跨周进入。** `_freeze_selection` 只过滤 ticker、PENDING、origin_trade_eligible、created_at，没有 closed_cycle_id。实际 10/5 selection 仅审阅 `case_9b40ca0b301f400abace361f98c49333`，属于 `2026-09-26` cycle，semantic_day 为 9/28，方向 SHORT；它与本次 `2026-10-03` cycle 的 LONG 候选无关。

4. **迟到候选没有结束处置。** 目标候选在本轮 selection 结束近 10 小时后创建，仍为 PENDING，无 selection_id 或终结说明。本轮不会再筛它；因第 3 项缺陷，它还可能进入未来其他周的 selection。`COMPLETED + TRADE route + create_trade_record COMPLETED` 在此只证明分析及候选输出完成，并非正式 intent 或成交。

## 判定语义的另一个风险

W1 的 NEW 根据是 800V DC 报告、Nvidia 2027 计划及客户审批广度；W2 命中根据是 Nebius 取消燃机/发动机订单并选择 Bloom。W1 并没有证明这个 Policy 触发事实本身是新增的。原文在 July 28 earnings call 段落讨论该事实，但不能仅凭段落断言其确切发生日或之前已消费。需使用冻结 Policy/Reference 和事件证据单独核验信号时效、已知性及当前 activation；不能把这次路由结果视为必须成交的业务真值。

## 建议修复边界

- 按 closed_cycle_id 约束候选，避免跨周残留进入下一轮。
- 用本轮冻结的消息/Case 身份及 admission 时间定义候选集合，不用分析产物 created_at 判断原始消息是否赶上截止点。
- 候选分析完成后再冻结 selection；来源/Case 失败采用有明确原因的局部隔离和结束状态。维护有限等待后可用旧 bundle，但不能把未完成候选分析静默当成无候选。
- 迟到产物记录明确未纳入/过期原因，并关联其原 selection；保留证据，防止未来自动继承。维持每周期最多一次正式释放及原有幂等合同。
- 展示区分候选等待、未选择、正式意图、执行、成交，避免 route=TRADE 被理解为交易已发出。
- 历史 Case 不自动重开或补单。修复实现后，用隔离快照验证；任何历史恢复必须保留时效、重复交易和当前 Policy 语义检查。

## 核验与工件

生产身份 `ubuntu@VM-0-15-ubuntu`，仓库 `/home/ubuntu/doxagent`，HEAD `b0f1caf50060b34483f13020a9d173ef57933afa`。Scheduler 镜像 `doxagent-v2:event-maintenance-20261006`。数据库通过 `mode=ro`、`query_only=ON`、读取事务访问。直接读取运行容器确认了上述 selection 函数；本地对应核心条件相同。

隔离临时 SQLite 复现当前 coordinator，结果：

```text
frozen_at_due: ['old-cycle']
unfinished_sweep_blocks_after_4h: False
snapshot_after_late_candidate: ['old-cycle']
new_freeze_still_excludes_late: ['old-cycle']
```

最后一行证明仅重建快照也无效，因为 created_at 截止条件仍会排除迟到候选。本轮未改业务代码或生产账本。

取证文件：`outputs/be-policy-badcase-20261006/evidence.json`，包含 Case、provider 时间、相关 task 收据、selection snapshot、两条候选和 intent/execution 数量。新闻正文未重复导出。已有来源抢占修复记录见 `docs/event-library-maintenance-repair-acceptance-20261006.md`；本轮另核实了实际 Message Bus 容器的运行代码。
