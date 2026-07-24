# CDECR 结构化可靠性修复与真实复验

## 结论

本轮按指定方案完成结构化模型边界修复，并对隔离的 10 篇真实语料执行两次
pass。离线测试、Ruff 和 mypy 通过；真实结果由首轮 7/10 收敛为第二轮
8/10。长 ID、混合时区、非法 namespace 和 Provider 错误均未再成为最终失败
原因，但仍有两个确定性阻塞，因此不能标记为生产验收完成。

## 已实施修复

- N9 恢复为每个响应最多 3 个 Mention，保留每个 Mention 的完整候选集。
- 所有 `StructuredModelRequest` 强制使用 JSON object 模式；M2 Chat
  Completions 使用 `response_format=json_object`，M3/M4 Responses 使用
  `text.format=json_object`，thinking 继续关闭。
- N9、Field Coreference、Normalization、Package Assignment/Merge 和 M4 审查
  均使用请求内短 ID；长 ID 只在适配器内恢复。
- 所有结构化节点经统一模型边界输入时间：带时区时间先换算为美东本地表示，再
  移除时区标签；模型只处理 `YYYY-MM-DD` 或无时区的本地日期时间，输出由
  适配器归一。
- 仅放宽表示层约束：移除模型 Schema 中的 date/time `format`，并允许适配器
  清理额外字段、枚举大小写及可确定的派生字段。候选覆盖、证据一致性、MERGE
  目标、Package 排序和成员选择等业务约束仍保持严格。
- Canonical Field 的追加式审计 ID 纳入 run ID，避免跨运行的不可变审计冲突。
- 保留 SQLite v8 与 N11-N13 新联合 Package 契约，并补齐兼容 DTO/测试桩，
  不回退新 Package 语义。

## 验证证据

离线结果：

- `tests/cdecr`：186 passed，3 skipped。
- scoped Ruff：通过。
- strict mypy：35 个 source files 通过。

真实复验：

- 输入 Registry：`.tmp/cdecr/n7_n10_real_20260724_source_v2.sqlite3`
- 输出 Registry：`.tmp/cdecr/n7_n10_real_20260724_v6_10.sqlite3`
- 机器报告：`.tmp/cdecr/n7_n10_real_20260724_report_v6_10.json`
- 10 篇、72 Mentions、2 pass；首轮 7/10，第二轮 8/10。
- 第二轮 7 篇稳定结果全部 `reused=true`，没有新增调用；Mention 保持不可变。
- 最终 71 个完整 Identity、1 个不完整 Identity、28 个 Atomic Events、
  15 个 Event Packages、169 个外部 KB links、64 个 provisional links。
- 首轮 242 次模型调用，第二轮新增 18 次；没有 Provider 级失败。

## 剩余失败根因

1. 文档 4 首轮 N11 Package Assignment 返回了一个业务校验错误；构造 repair
   请求时，Pydantic `errors()` 中的非 JSON 对象触发 `TypeError`。该次失败前
   已追加 Atomic 派生状态，第二轮因此被正确的
   `DERIVED_STATE_REBUILD_REQUIRED` 防护拦截。问题是 repair 诊断序列化和文档
   事务/checkpoint 边界，不是 JSON Mode 或长 ID。
2. 文档 10 的谓词 `estimate` 在候选 `meet/miss/exceed estimate` 间无法安全
   判定，Field Coreference 两次均返回 `UNRESOLVED`；N6 因缺少
   `predicate.normalized` 拒绝伪造 Identity。此处是开放世界谓词覆盖/消歧
   缺口，不应通过放宽 N6 必需字段掩盖。
3. 文档 8 首轮同样因谓词未解析失败，但第二轮由 Field Coreference 创建
   provisional predicate 后恢复，属于模型决策波动。应在 N5.5 增加同运行内的
   有界收敛或稳定 unresolved 策略，而不是整篇失败后重跑。

按用户要求，本轮结束后不再追加测试。
