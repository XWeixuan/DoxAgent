# CDECR N7–N10 最终优化交付与在线验收报告

## 交付结论

本轮方案中的工程改造已经落地，但真实语料生产验收未通过，因此不能将
N7–N10 标记为生产完成。

已完成的核心边界包括：N8 Shadow、HOLD 全局删除、N9 多候选联合裁决、
Atomic Identity Embedding 生命周期修复、Canonical Field Identity 信任等级
贯通、增量 Assignment 防错、N10 audit-only duplicate detection，以及可重建
派生状态的 SQLite v7/CLI。

真实网络已恢复，M1/M2/M3 均能正常调用。最新 10 篇两轮在线波次最终为
7/10 成功；按用户要求，本波结束后不再发起新的模型调用或测试。最后针对
3 类失败补入的最小代码修复已写入，但没有在本轮之后再次测试，必须保持
“待验证”状态。

## 主要实现

- `AtomicAction` 仅保留 `MERGE/CREATE_NEW`，Package 不确定性也不再产生
  HOLD；Registry v7 删除旧 `hold_queue`，Assignment 必须具有最终目标。
- `CDECR_ATOMIC_HARD_CANNOT_LINK_MODE` 支持 `enforce/shadow/off`，默认
  `shadow`。Shadow 只写 `ATOMIC_HARD_CANNOT_LINK_OBSERVED`，不排序、
  不过滤、不进入 N9。
- N9 按单 Mention 的完整候选集合联合裁决，输出每候选 Assessment 和唯一
  Action；M2 业务校验失败升级 M3，持续不确定时 `CREATE_NEW`，技术失败仍
  失败，不伪造结果。
- `related_candidate_event_ids` 和 `possible_duplicate_atomic_ids` 由
  Assessment 确定性派生；唯一 `SAME_EVENT` 的长 ID 复制错误可安全恢复，
  不改变模型的语义关系判断。
- Atomic Embedding 的唯一输入改为当前 Atomic Identity Text；新建、更新、
  恢复时同步，召回只使用当前 exact input hash。
- 新增 External/Canonical/Legacy/Surface/Unresolved 身份证据等级和
  `SAME/DIFFERENT/UNKNOWN` 三值比较；Participant 保留角色，Place 和六类
  Named Object 进入 `FIELD_ID` 召回。
- Field Link 或 N6 Identity 变化时不复用旧 Assignment，也不覆盖旧 Atomic
  Profile，而是返回 `DERIVED_STATE_REBUILD_REQUIRED`。
- `_correct_atomic()` 只写重复候选审计，不再自动修改 Atomic 或创建
  redirect。
- 新增 `python -m cdecr registry rebuild-derived`；保留 Source、Mention 和
  Field Resolution，清理并重建 Atomic/Assignment/Embedding/Package/
  Membership/Relation/Cross-document Run。
- 当前版本：SQLite `user_version=7`、Cross-document Engine `v9`、
  Prompt `v5`、Atomic Assignment Policy `v2`。

## 验收证据

### 离线工程验证

在最后一轮在线修复之前已获得：

- CDECR 非真实测试：168 passed，3 skipped；
- N7–N10 focused：20 passed；随后新增的 N9 失败升级与冗余字段规范化用例
  2 passed；
- scoped Ruff：通过；
- strict mypy：34 个 source files 通过；
- 早前已完成 wheel/CLI smoke。

用户要求停止继续测试后新增的以下三处最小修复没有再跑回归，不能引用上述
结果替它们背书：

1. N9 每个 JSON 响应从 3 个 Mention 收缩为 1 个 Mention（仍保留该
   Mention 的全部候选）；
2. `participant.unknown` 的 enum member-name 格式确定性规范化；
3. N10 合并 aware/naive datetime 时统一为 UTC-naive 可表示边界。

### 30 篇先前在线波次

独立 Registry：
`.tmp/cdecr/n7_n10_real_20260724_v3.sqlite3`

- 30 篇、227 Mentions；
- 第二轮最终 14 成功 / 16 失败；
- 失败 Run：`structured_output_invalid` 22、N6 未解析 7、
  Field Coreference 4、供应商欠费 1、派生状态需重建 2；
- 60 个当前 Atomic 均有 Embedding，当前 Identity Text hash 不匹配为 0；
- 活跃 Mention 多 Atomic 成员关系为 0；
- HOLD 表 0、Atomic redirect 0；
- N8 Shadow 审计 4,411 条，Assignment 中实际 hard conflicts 非空为 0。

### 最新 10 篇在线波次

独立 Registry：
`.tmp/cdecr/n7_n10_real_20260724_v4_10.sqlite3`

机器报告：
`.tmp/cdecr/n7_n10_real_20260724_report_v4_10.json`

- 10 篇、72 Mentions、232 次真实模型调用；
- 第一轮 5/10 成功，第二轮 7/10 成功；
- 第二轮 5 篇零调用幂等复用，2 篇失败后成功，3 篇持续失败；
- 72/72 N6 Identity 在波次结束后的当前 Field Links 上可编译；
- 34 个当前 Atomic、19 个 Package；
- 34/34 Atomic 均有当前 exact Identity Text Embedding，hash 不匹配为 0；
- 56 条 Atomic Assignment、46 条 Package Assignment，最终目标缺失均为 0；
- 22 次 MERGE、34 次 CREATE_NEW；42 条 Assignment 保留 Related 候选痕迹，
  但正式 `RELATED_TO` edge 为 0；
- HOLD 表 0、redirect 0、活跃 Mention 多 Atomic 成员关系 0；
- N8 Shadow 审计 770 条，实际传入 Assignment 的 hard conflicts 非空为 0；
- EventMention 前后 hash 一致。

## 高失败率根因

### 1. N9 输出矩阵可靠性是主因

30 篇旧波次中有 22 个终态 `structured_output_invalid`。审计显示主要不是
JSON 语法错误，而是：

- 模型漏掉一个或多个候选 Assessment；
- 复制长 `atomic:*` ID 时改变字符；
- Assessment 与冗余 Related/Duplicate ID 列表不一致；
- MERGE target 与 SAME_EVENT Assessment 不一致。

原实现一次响应包含 3 个 Mention，每个 Mention 又包含完整多候选集合，
JSON Mode 只保证 JSON 对象，不保证业务覆盖约束，输出矩阵越大越容易漏项。
同时，旧修复链仍由同一模型修复，未真正落实“M2 业务失败升级 M3”。

本轮已把可确定字段交给程序派生、允许唯一 SAME target 的纯 ID 复制修复，
并实现 M2→M3 升级。最新波次仍有 1 篇因 M3 连续两次漏候选 Assessment
而失败；随后又把每个响应收缩为单 Mention，但按用户要求未再次测试。

### 2. 文档级全有或全无放大失败率

最新波次 232 次真实调用中，仅 3 次被模型调用审计记录为失败；但文档最终
仍有 3/10 失败。原因是每篇平均 7.2 个 Mention，并包含多个 Field 与候选
Assessment，任意一个严格约束失败都会使整篇 Cross-document Run 失败。

因此“30% 文档失败”不等于“30% 请求失败”。当前隔离策略保证不会提交
半篇污染状态，但缺少更细粒度的可恢复 checkpoint，造成单点失败对文档状态
的放大。

### 3. Field Coreference 的 unknown 类型输出不稳定

最新持续失败之一来自 `participant.unknown`：M2 两次返回不属于
`FieldNamespace` enum 的 `target_namespace`。这是 API 成功、JSON 合法，
但严格 Schema 失败。30 篇旧波次也累计有 4 个 Field Coreference 终态失败。

现已只对 FieldNamespace 自身的 member name/value 做大小写和连字符格式
规范化，不把 `organization` 等开放语义词强行映射到某种类型；此修复尚未
在线复验。

### 4. N6 未解析既包含正确门禁，也暴露 Field Resolution 非收敛

旧波次有 7 次、最新波次首轮有 1 次
`IDENTITY_FIELDS_UNRESOLVED`。最新实例缺少
`predicate.normalized` 的 CanonicalFieldLink；第二轮后 72/72 Identity
最终可编译，说明它是 Field Resolution 调用波动/增量链接变化，而非 Mention
本身不可表示。

N6 拒绝伪造 typed identity 是正确边界；问题在于上游 Field Resolution
尚未做到单轮稳定收敛。不能通过放宽 N6 必需字段来掩盖。

### 5. N10 混合时区时间合并存在确定性缺陷

最新持续失败之一在 `merge_event_times()` 构造 `EventTime` 时触发
Pydantic `ValidationError`。失败 Mention 含 `-04:00` aware timestamp，
候选 Atomic 可含 timezone-unspecified datetime；堆栈和字段形态共同指向
aware/naive 边界不兼容。这一判断属于有证据的根因推断，因为旧审计没有保存
Pydantic 的具体 message。

已增加只在 aware/naive 不兼容时将 aware bound 转为 UTC-naive 的最小修复，
不改变普通日期、同类 datetime 或 reference period 合并；尚未回归。

### 6. `DERIVED_STATE_REBUILD_REQUIRED` 是保护机制，不应算模型失败

30 篇旧波次有 2 篇首轮成功、第二轮失败：Field Links 在重试中变化后，
旧 Assignment 的 Identity Processing Key 已过期。系统拒绝把新身份覆盖进
旧 Atomic Profile，按设计返回需重建。

这证明增量保护生效，也说明“在同一可变 Field Registry 上直接跑两轮”不是
正确的收敛验收方式。Field Links 变化后应通过 Derived State Rebuild 重算；
不能为追求表面幂等而复用旧 Assignment。

## 尚未通过的门槛

- 最新在线文档成功率仅 70%，不是 100%；
- 最后一组 N9/Field/time 修复尚未回归；
- 本轮没有对 30 篇做修复后的完整重放；
- 尚未完成人工 Atomic MERGE 精度、Recall@K、多个 SAME/Related 语义质量
  复核；
- Derived State Rebuild 的真实全量重建和连续重建幂等尚未完成在线验证；
- N10 仍沿用集合并集 Profile 更新，链式扩张风险按方案明确保留。

因此当前状态应表述为：

> N7–N10 优化工程实现完成，核心持久化/Embedding/Shadow/HOLD 不变量在已产出
> Registry 上成立；真实语料可靠性验收未通过，最后三处修复待下一轮回归。
