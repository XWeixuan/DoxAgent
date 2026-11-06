# W1R1 Known Event Index 筛选与每日冻结

日期：2026-11-06。本轮为本地开发，没有修改生产或发布。

## 行为

Known Event Index继续由独立编译器构造，格式不变：`event_id | occurred_at | known_event_summary`。Reference View及其他消费者默认全量Known Index接口不变，Runtime显式选择新筛选入口。

每日选中ACTIVE Event的并集：

1. 该固定Library版本中属于Reference View；
2. Registry首次入库记录对应发布日位于语义日D-13至D；
3. 至少一个当前Fact的实质内容首次发布日位于D-6至D。

窗口按现有America/New_York语义日计算（当地02:00切日，含DST处理），不是连续336/168小时，也不使用Event occurred_at或Fact发生日期计算保护期。行内occurred_at仍是原格式的发生日期展示。集合按Event ID去重，沿用原确定性排序。

入库时间来自canonical_events.created_version及不可变library_versions的published_at，老记录缺published_at时使用该版本created_at。Fact沿canonical_fact_revisions追溯业务内容首次出现的版本，变更proposition/assertion_state/subject_time及Fact发生时间语义可以形成新内容；空白格式变更不计。Event的Reference标志、维护复审和其他维护字段不会重置Fact窗口。相同Fact内容曾出现后再恢复，也不伪装成首次新增。本轮不做LLM或文本相似度判定。

## 每日冻结与兼容详情

Runtime仓库新增`runtime_v2_w1_daily_indexes`，唯一键为(ticker, semantic_day)，保存原始索引文本、SHA、Library版本、根路径、首次冻结时间及当天W1R1固定指令。SQLite使用INSERT OR IGNORE原子决定唯一赢家，输入编译和大对象编码在写事务外；取回后的大对象解码也在写事务外。并发线程、多个仓库连接、API/Scheduler及进程重启均复用同一份。

W1使用独立的日冻结Library版本和根路径，W1R2按该版本读取被召回的Canonical Event/Fact；后续O2修改、归并、撤销或版本推进不改当日索引或这些旧版本详情。Case全局Event Library、Policy、D1、D2/W3仍沿用当前激活引用，不让W1日冻结导致W3的Reference View回退到日初。provisional在W1中使用冻结版本的ID命名空间，R2详情随Case冻结供下游复用。

Known Index和W1R1固定指令一起冻结；当日提示词资产更新时，W1R1固定部分翌日采用新内容。W1R2/W2等轮次不因此冻结提示词。重试修复说明放到动态尾部，保持系统指令及Known Index引用块逐字相同、base session_id相同；保留原有provisional分批缓存机制。provisional累积引起其完成批次fingerprint变化仍属正常，不等于Known Index前缀变化。

100%保证的是新机制下同ticker、同语义日的编排固定前缀字节一致，不保证provider实际100%缓存命中。旧Case与旧round_inputs不改写，已经发送过的历史请求无法回溯变成相同前缀；上线若在日中进行，旧Case继续原快照，新接入Case按每日表冻结。需要整日统一时应在语义日边界启用。

## 冻结后的事实可见性

- 原Runtime provisional即使O2已整理、日记录已PROCESSED，也保留在该语义日W1可见集合内；不因结算从输入移除。
- 针对直接从Library发布、没有Runtime provisional的新事实，比较当日冻结版本与当前版本，把新入库事实通过现有provisional三列输入及R2详情路径补入。
- 补入项复用已有ProvisionalFactDetail和单调ID分配器，内部使用可追溯Library版本/Fact身份；写入时原子标PROCESSED，不进入下一轮O2待处理Delta，避免循环入库。Agent端不新增字段、工具或能力。
- 所有provisional详情仍逐Case冻结，模型召回后可供W1R2读取。新Library头处理完后用小型进程内有界集合跳过重复扫描；重启重新检查，持久化幂等身份防止重复补入。
- 下一语义日按新Library重新筛选；近期发布Fact进入Canonical Known Index保护窗口，前一日的provisional不会混入新日。

## 修改位置

- event_library/repository.py：可追溯Event/Fact入库记录及并集选择。
- event_library/compiler.py、provider.py：Runtime可选筛选，默认旧消费者不变。
- persistent_runtime_v2/daily_index.py：每日冻结与发布后事实补入。
- persistent_runtime_v2/repository.py：持久化每日表、补入事实原子PROCESSED。
- persistent_runtime_v2/service.py、providers.py：Case根路径/版本、固定指令、详情和provisional接线。
- persistent_runtime_v2/openrouter.py：仅新每日合同的R1重试说明移入动态尾部；旧合同布局保留。

## 验证

新增测试覆盖：14/7天含当天及上下边界、跨DST窗口、旧发生日期的近期补录、ACTIVE筛选、并集去重、Reference/普通维护及空白变更不刷新窗口、已有Fact新增内容、次日过期、多连接24次并发冻结、重启文本一致、当日O2与结算后保留provisional、Library直接新增Fact补入且不再次进O2、真实Service的W1R1→W1R2固定版本与补入详情、同日提示词更新与次日刷新、重试前缀和session_id一致。

当前checkout回写后71项相关回归通过；基于origin/main的独立worktree最终核心链路回归192项全部通过。跨模块扩展回归：219项通过，5项失败在未修改的e101863d基线中逐项同样复现，分别为：

- test_event_library_foundation.py::test_workflow_runner_uses_explicit_ids_and_noops_without_eligible_documents
- test_event_library_incremental_consumers.py::test_document2_injects_full_reference_payload_once_per_shell_thread
- test_event_library_time_reference_repair.py::test_broad_event_same_fact_and_candidate_priority_are_hard_errors
- test_event_library_time_reference_repair.py::test_time_error_codes_are_stable_for_missing_future_and_subject_leak
- test_event_library_time_reference_repair.py::test_supersession_requires_both_sides_and_normalizes_target_reason

这些是既有CDECR/D2/校验预期问题，本轮没有修改对应模块以消除失败。测试为确定性程序与本地SQLite/固定模型回执测试；没有真实LLM质量评估或缓存服务命中率测试，也未自动部署。

## Production compatibility correction (2026-11-06)

Production preflight exposed minimal branch snapshots and native-retained libraries with retired publication metadata. Admission lookup now follows existing branch sidecars, reading only revisions after each base. Missing historical clocks remain `None`; the next retained publication supplies an age upper bound, never a fabricated admission time. An Event is excluded only when the bound proves it is outside the window. Overlapping uncertainty retains the Event, consistent with the legacy full index. This preserves historical research coverage without changing stored Events, Facts, activation pointers, or minimal-branch storage. Nested branch reversion and retired-clock regressions were added.
