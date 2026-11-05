# Event Library 精简能力：现状勘察与开发边界

日期：2026-11-06（Asia/Shanghai）。本报告是新 O2 精简节点开发前的现状基线；生产业务数据来自本轮只读快照，而非历史记录推断。

## 1. 结论

现有系统没有“按容量或距上次精简时间触发、逐一审阅全库、退出低效用历史 Event/Fact”的节点。当前所谓退役包含两种不同机制：

1. Canonical Event 的合并、无效抑制、拆分重定向，是纠正对象身份的机制。
2. 10/30/7 天 Reference Review，是维护 D2/D3 当前研究视图的机制。

二者都不是全库容量治理。Reference Review 即使完全正常执行，也不会减少 W1R1 的 Known Event Index。当前证据不支持将问题简单归因为“定时退役作业坏了”。核心问题是既有语义、审阅范围、消费者筛选与本次目标不一致。

新能力应当区别“有效事实失去当前效用”和“事实本身无效”。把有效旧事件伪装成 `SUPPRESSED_INVALID_OCCURRENCE`，或任意指定一个无关 successor 来满足退役 schema，会污染事实与身份关系。

本轮交付勘察、生产快照、逐项标题筛查、Fact 深读和实现建议；**没有实现、部署或启用新节点，没有执行清理**。触发与目标阈值尚待用户根据业务评估确定；下文均为待定设计输入。

## 2. 本轮证据与仓库状态

- 仓库：`C:/Users/WEIXUANXIE/Desktop/DoxAgent`；分支 `codex/agent-readonly-mcp`，HEAD `26c7a54a`。有较多既有已修改和未跟踪文件，本轮未回滚或重排它们。
- Python 3.11–3.13，pytest、Ruff；生产调度入口 `python -m doxagent.runtime_scheduler.v2`。
- SSH：`doxagent-sg`，主机 `VM-0-15-ubuntu`。读取实际 scheduler 容器，未重启服务。
- 九份关键生产源码 SHA-256 与本地对应文件一致或仅 CRLF/LF 不同：compiler、contracts、repository、reference_review、validator、remote_runner、maintenance、coordinator、event_branch。具体 hash 位于 `eval/event-library-audit-20261106/scheduler-evidence.json`。
- 从 `ticker_active_revision → activation_revisions.artifacts.event_library` 解析真实 root/version。实际激活库在各 `runtime_artifacts/runtime-maintain-*/events` 下；不能拿 `/data/events` 的初始化库代替当前激活库。
- 采集通过 stdin 执行诊断 Python，SQLite 使用 `mode=ro` 和 `query_only`；`EventLibraryRepository(read_only=True)`；未构造会初始化/迁移库的业务服务。
- 事件内容快照时点为 **2026-11-06 00:53:14 北京时间**（UTC 2026-11-05 16:53:14）。十库依次读取、各版本固定，不宣称跨库同一事务快照。

详细业务评估见 [远端业务报告](event-library-compaction-production-review-20261106.md)。原始快照和逐行筛查均保存在 `eval/event-library-audit-20261106/`。

## 3. 对象、版本与消费者

### 3.1 Canonical 模型

`src/doxagent/event_library/contracts.py`：

- Event 是 occurrence，而非无限延伸的主题容器；包含稳定 E#、title、occurred_at/precision、两类 summary、importance/reference 标志、关系和完整 Fact membership。
- Fact 有稳定 F#，区分事实发生时间和 subject_time。事实时间不能与未来的经营/预测适用期混用。
- `CanonicalObjectStatus` 为 ACTIVE / MERGED / SUPPRESSED，没有“有效但已归档”的状态。
- `is_important` 表示长期研究重要性；`include_in_reference_view` 表示 D2/D3 当下必要性。这两个字段都不负责 W1 索引排除。
- Event 的 `price_analysis` 必须保留；精简不能顺手改写交易/价格分析。

### 3.2 存储

`src/doxagent/event_library/repository.py`：

- `library_heads/library_versions` 管 Published 版本；Event/Fact 有独立 identity、revision 和 state。
- `event_fact_memberships`、`event_relations` 有版本有效区间；发布是完整目标 membership 替换，而非逐条 append patch。
- `published_events(ticker, version)` 选择该版本最新 state 为 ACTIVE 的 Event；`get_event` 可以读取已存在对象的版本详情，语义不同。
- Event revision 重写 membership；发布后 `_suppress_orphaned_facts` 将不再属于任何有效 membership 的 ACTIVE Fact 标为 SUPPRESSED。这是完整替换结果的清理，不是 Fact TTL。
- 这意味着“不小心少写一个 Fact”可能有正式退出效果。新精简节点必须给每项变化明确决策和审计，不能把漏写当成默许。

生产采用 copy-on-write 分支。`persistent_runtime_v2/event_branch.py` 仅复制选定版本必要主数据、最新状态、有效 membership、mapping 和 review schedule；不复制所有旧运行历史。当前分支的 `maintenance_runs` 数量或 `reference_review_history` 行数，不能用于推断系统历史总执行次数。回收逻辑还会压缩分支/工作区，因此历史发布的可追溯需求必须显式设计保留。

### 3.3 W1R1 真正读取什么

`compiler.py:known_event_index` 遍历全部 Published ACTIVE Event，输出三列：

```text
event_id | occurred_at | known_event_summary
```

不包含 title，不直接包含全部 Fact，不按 importance、reference 或年龄过滤，也没有数量上限。

`event_library/provider.py` → `persistent_runtime_v2/service.py` 将其送入 `published_known_event_index`；`openrouter.py`/`transport.py` 将该部分作为可缓存的稳定上下文。缓存可能减少部分计费/延迟，但不消除上下文长度、注意力干扰或更新后的缓存失效问题。

因此：

- Event 数增加会近似增加 W1R1 识别索引；summary 长度也是独立变量。
- Fact 数不是 W1R1 索引大小的直接线性因子；它影响 Event Detail、W1 后续读取和 O2 编辑/Reference 负担，可能间接使 summary 变长。
- 只删 Fact 而保持 Event 和 summary，不能预期显著降低 W1R1 基础索引成本。
- 只清 Reference View，也不能降低 W1R1 基础索引。
- 当前报告验证输入规模和业务噪声，**未测得精简前后真实准召变化或账单节约**。

## 4. 当前 O2 编排

### 4.1 初始化与增量

`workflows/codex_event_library/remote_runner.py`：

| 模式 | 模型任务顺序 | 主要作用 |
| --- | --- | --- |
| INITIALIZE | SURVEY → LOCAL_RECONSTRUCTION（按 wave）→ GLOBAL_RECONCILIATION | 从冻结 Delta 重建完整初始 Canonical |
| INCREMENTAL | BUILD_CANDIDATE_MAP → RECONSTRUCT_AND_EDIT → REFERENCE_REVIEW | 全索引找可能匹配，读取相关详情，编辑受影响对象，再审阅 flags |
| review-only | REFERENCE_REVIEW | 无 Delta、存在 due candidate 时维护 Reference flags |

任务工作区有 frozen manifest、Known Index、Delta、允许访问的 Event Detail、上游 D1、schema、Date Resolution Ledger、Reference View Decision Ledger。模型不直接写 SQLite、Published 或 ID counter。

最终 Bundle 经 wire 规范化、日期与身份校验、局部降级、working import、原子 publish、导出，再供 Runtime 激活。局部错误可被取消并留下 PARTIAL/KEEP_PENDING，不能把 `BUNDLE_READY` 或“任务成功”当成所有业务编辑均被接受。

Prompt 资产：

- `prompts/codex_v2/event_library/agents/o2.md` 明确使命是完整历史事实记忆和精简 Reference View。
- `skills/foundation.md` 要求 broad coverage，避免因不在 Reference View 而漏掉有效 Canonical。
- `skills/incremental-edit.md` 只在当前 Delta 受影响范围发现边界问题时 merge/split。
- `skills/incremental-reference-review.md` 只重新决定 importance/reference flags；review-only 修改其他字段不属于其职责。

仅给现有 Reference Review prompt 加一句“清理旧 Event/Fact”，会与已有职责和 validator 契约冲突。

### 4.2 当前生产运行链路

`runtime_scheduler/v2.py` 构造 Persistent Runtime；`persistent_runtime_v2/coordinator.py` 产生持久化 MAINTENANCE 任务，包括 DAILY、SWEEP 结束后的维护及晚到结果的 SUPPLEMENTAL。

`persistent_runtime_v2/maintenance.py`：

1. 冻结当前 active revision、候选、模型、D1/D2/D3/Event 基线及 case membership。
2. 从 Event 激活版本建立隔离 branch，避免直接覆盖生产当前库。
3. 用 RuntimeDeltaBatchAdapter 冻结 provisional candidate，调用 O2 INCREMENTAL；无候选时 runner 仍有 review-only 路径。
4. 根据真实 Delta resolution 结算候选；未解决的保留 pending 和 gap。
5. 计算 Reference View Delta，执行 O3，发布新的组合 activation，保留 checkpoint/retry/rebase 语义。

O2 在 scheduler 链路执行，不在交易 executor 中执行。新增节点应接入这一持久化/激活所有权，不能另写 cron 直接修改各库 head。

## 5. 旧“自动退役”的完整解释

### 5.1 Canonical retirement

`EventRetirement` 只允许：

| reason | 写入状态 | 业务含义 |
| --- | --- | --- |
| MERGED_DUPLICATE_OCCURRENCE | MERGED | 同一 occurrence 重复对象合并 |
| SUPPRESSED_INVALID_OCCURRENCE | SUPPRESSED | 无效 occurrence 被抑制 |
| SPLIT_TO_SUCCESSOR | MERGED | 原对象拆分并重定向到主要后继 |

所有条目必须有不同于自己的 `redirect_to_event_id`，且目标在发布后可解析为 ACTIVE。`validator.py:_filter_retirements` 拒绝未知 source/target；缺失匹配 retirement 的非 ACTIVE revision、同 Bundle ACTIVE revision 又被 retire 等冲突也会被拦截。`repository.py:_write_retirements` 写 state 并关闭 source membership。

没有“老于 N 天就 retire”的状态转换；`supersedes_event_id` 只是关系，不等于自动将被替代事件移出 ACTIVE。低相关、低价值、已吸收但仍然真实的事件通常没有合法 redirect，因此现有 API 无法正确表达这次需求。

### 5.2 Reference aging/review

`reference_review.py:classify_review` 的实际时钟：

| 条件 | 行为 |
| --- | --- |
| 有 anchor，未到 anchor + 30 天 | IMPLICIT；下一次为当前 +10 天和 30 天边界中的较早者 |
| 已过 30 天且仍 include_in_reference_view | EXPLICIT，每 7 天复查 |
| 已过 30 天且不再 include_in_reference_view | EXPLICIT 判断后 `next_review_at = null` |
| anchor 无法解析 | include 时每 7 天复查，否则不再定期排程 |

anchor 取 Event 发生时间和 Fact 发生时间的最大值；不会用 subject horizon 作事件年龄。但语义复审会考虑 subject horizon 是否仍有效。

10/30/7 天是“何时重新判断”，不是“到时自动退出”。旧但仍有效的 PPA、诉讼、扩产、采购合同可以保留；新但例行的事件也可退出 Reference View。`include=false` 且 age>=30 后停止复查，是设计结果，不是 scheduler 失效证据。

即使模型判为 `COMPLETED_AND_ABSORBED` / `ROUTINE_OR_REDUNDANT` / `NO_CURRENT_DOWNSTREAM_UTILITY`，动作仍是 Reference 排除，不是 Canonical retirement。

### 5.3 生产证据

快照中九个 ticker 没有当前非 ACTIVE Event；仅 RKLB 有 1 个 MERGED Event，另有 167 个 ACTIVE。统计已按每个 Event 的最新 state 去重，未把重复 state 版本计为新 Event。

MU / INTC / BE 的 review schedule 均覆盖当前 ACTIVE Event，快照时 due 数均为 0；其中 `next_review_at=null` 分别为 236 / 245 / 76。它们分别还有 306 / 277 / 147 个 Reference Event。这不是全库失去调度或全部卡住的形态。

本轮读取的 durable task 中，MU / INTC / BE MAINTENANCE 成功行分别为 188 / 433 / 113，历史失败行为 7 / 10 / 5；最近各 ticker 的 11/4 DAILY 均为 SUCCEEDED。失败行不能直接解释现在的膨胀，也不能据此说每个失败都未补偿。另有 IONQ 8 个、RKLB 1 个 due review candidate，仅是快照的待复审量，本轮未扩展恢复。

结论分层：

- **已证实**：退役状态极少，Reference 排除不能减 Known Index；没有容量/全库效用精简节点。
- **机制解释**：完整历史覆盖导向 + 增量只看受影响范围 + 退役必须有后继 + flags 对 W1 不起筛选作用，导致内容持续积累。
- **未证实**：历史所有 run 是否曾提出 retirement 却被 validator 否决。当前 branch 的历史已裁剪，不能拿它的少量 run/history 行证明这一点；若要追责具体拒绝样本，需要单独追原始 Bundle/validation receipts。

## 6. 新节点的实现约束与建议接点

### 6.1 应先定清语义

建议新建“有效但退出当前识别工作集”的归档语义，保留历史身份、原文、版本和退出理由；不复用“无效事实”理由。若采用 `ARCHIVED` 生命周期，则需扩展 Event/Fact 状态与 retirement 契约、允许无 successor 的合法归档，并逐一核对 provider、validator、compiler、branch、API/projector 及历史检索消费者。

另一可行方案是版本化识别工作集，与完整 Canonical 库分离；此时“目标 Event 数”必须明确定义为识别工作集数，不能让 UI 的全历史总数冒充仍未精简。两种方案不要混成一个含义不明的 `is_important` 开关。

有效历史退役还会减少 W1 的去重记忆。应保留可检索的冷历史，在宣告 NEW 前，对有合理历史匹配线索的候选补查，区别旧事实重报与新披露；受影响的 runtime_atomic mapping/redirect 不能悬空。否则省下输入成本可能增加错误“新事实”和后续研究/交易判断。

### 6.2 建议任务形态

建议独立持久化 `LIBRARY_COMPACTION` 任务/维护 scope，复用 scheduler 所有权、ReceiptWorker、frozen assets、lease、checkpoint、copy-on-write 和激活 fence；不把逐库全审塞进每次 DAILY 的 Reference Review。

阶段建议：

```text
满足可配置 trigger
→ 冻结 active revision、Event/Fact 清单、hash、as_of 和清理政策
→ O2 全库 survey（全 title/date/summary + D1 与当前研究基线）
→ O2 分批读详情，对每个 Event、每个 Fact 作出决定
→ 全局 reconciliation（跨批重复、关系、仍有效事项、目标差额）
→ 严格校验逐项覆盖与改动白名单
→ 隔离分支试发布、产出前后统计与审计
→ 必要的 Reference/O3 衔接
→ 当前 activation 仍匹配时原子激活
→ 记录成功维护时间/结果与未达目标原因
```

模型可选择 KEEP / ARCHIVE / DEDUPLICATE / NEEDS_ADJUDICATION；拆分、改写事实应有独立授权范围，不能靠“精简”隐式扩大。KEEP 与退出均要对每项留简短理由，Fact 引用 Event 内稳定 F#；程序只校验，不按关键词代替 O2 作语义退役。

数量触发和时间触发为 OR；高阈值触发后目标低于高阈值形成滞回。应限制单 ticker 同时一个精简任务，防 DAILY/SUPPLEMENTAL 并发发布；base 失效后 rebase/review 变化范围，不能对新 head 强套旧计划。

“last_compacted_at”只能在完成全库覆盖、验证和实际激活后更新；初始化成功时间作为首次基线。普通 O2 增量、任务入队、失败、半途退出均不能刷新精简时钟。对于全库审阅成功但无安全退出项、无法达到数量目标，应记 `TARGET_UNMET` 与冷却/下次复审安排，避免每次 tick 无限重跑同一份库。

### 6.3 Fact 与模型输入预算

Event threshold 不能治理一个 141-Fact 大主题；Fact threshold 也不能治理几百个一条 Fact 的边缘新闻。应同时记录索引 token、Fact 总量和大事件尾部，但把“详情审阅触发”与“强制删除配额”分开。

批次应按 token/Fact 预算切分，保持所有 Event/Fact 的覆盖清单。大财报单独读，不因为超过 50 条就自动移除指标。跨批事实的日期、来源口径、主体、单位、条件和阶段必须保留；不同分析师给出不同预测不等于重复事实。

### 6.4 需覆盖的验收

1. 数量/时间双触发、首次初始化基线、滞回、成功/失败/无变更/未达目标的时钟。
2. 全库逐项覆盖；漏项、未知 ID、重复决定、越过 frozen scope、审阅不完整均不发布。
3. 低价值有效事件可归档，无伪造 successor；有效旧长期事项不因年龄被强退。
4. Fact 去重、孤儿处理与历史可读性；price_analysis、Delta 状态、交易记录不随精简被改写。
5. 生产 head 漂移、并发维护、lease lost、进程重启、重复交付的幂等性和 checkpoint 恢复。
6. 当前 W1 识别索引下降，并验证 archived old-news 重报不被当成全新事实；用同一冻结样本比较 W1/W2 准召，至少区分旧闻重报、仍有效旧事件、真正新事件和相关续报。
7. Reference Delta/O3、API 计数和 active revision 一致；仅作模型 dry-run 不算发布验收。

## 7. 本轮验证与交付边界

本轮未改动运行时代码或生产配置。新增两个只读采集脚本与可复算业务筛查脚本；Ruff 通过。运行现有的 reference boundary、temporary retirement atomicity、review-only idempotence 三个定向测试：**3 passed / 17 deselected**。这些测试验证本报告涉及的既有机制，不代表尚未开发的新精简节点通过验收。

下一步实现所需的业务输入：目标是完整 ACTIVE Canonical 数还是独立识别工作集数；初始数量/时间阈值；是否接受达到安全保留边界时目标未达；历史补查策略。远端业务报告给出了可供决定的规模依据和候选范围。
