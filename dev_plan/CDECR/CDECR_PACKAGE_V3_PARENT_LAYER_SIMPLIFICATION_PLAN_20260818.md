# CDECR Package V3 Parent 层简化修复方案

日期：2026-08-18  
范围：模型路由解耦现状说明；Parent Induction 窄调；停用 Repartition；移除旧 Parent Resolution R1/R2 运行路径。  
不在本方案范围：V3 occurrence DTO、V3 Prompt/Schema、跨 MCP Atomic owner、Package V3 clustering/description 业务逻辑。

## 0. 前置现状：M2/M3/M4 已完成语义档位与调用通道解耦

### 0.1 已完成的修复

当前工作树已将以下三类概念拆开，不再由一个 `M2/M3/M4` 参数同时决定：

1. `model_profile`：只决定模型名称和 thinking effort；
2. `invocation_channel`：决定 Chat JSON Object、Responses JSON Object 或 Package 专用 Responses；
3. `scheduler_lane`：只决定物理并发队列、并发额度和 provider pressure 管理。

主要实现位置：

- `src/cdecr/model_routing.py`：集中定义节点路由；
- `src/cdecr/cli.py`：分别解析语义 profile、调用通道和物理 lane；
- `src/cdecr/cross_document.py`：`tier` 表示语义 profile，`execution_tier` 表示物理 lane；
- `src/cdecr/package_global_clustering.py`：Package V3 保留专用 Responses client，但模型档位独立选择。

当前统一模型与 effort 语义：

| Profile | Model | Effort |
| --- | --- | --- |
| M2 | `deepseek-v4-flash-0731` | `none` |
| M3 | `deepseek-v4-flash-0731` | `low` |
| M4 | `deepseek-v4-flash-0731` | `high` |

当前 provider 均为百炼 DashScope；JSON Schema strict 默认关闭，节点保持各自既有 JSON Object 调用方式、本地 Pydantic/coverage/业务校验及 repair/fail-open 边界。

### 0.2 当前节点路由

| 节点 | 语义 Profile | 调用通道 | 物理 Lane |
| --- | --- | --- | --- |
| Dreamer | M2 | Responses JSON Object | M2 |
| Relevance | M2 | 独立、无 continuation 的 Responses JSON Object | M3 |
| Field | M2 | Chat JSON Object | M2 |
| Atomic initial / ordinary repair | M2 | Chat JSON Object | M2 |
| Atomic escalation / late convergence | M2 | Responses JSON Object | M3 |
| Grounder 及 repair/recovery | M2 | Responses JSON Object | M3 |
| Parent Induction / Repartition | M2 | Responses JSON Object | M3 |
| Judge 及 coverage/item repair | M2 | Responses JSON Object | M4 |
| Package V3 Initial/Rolling clustering | M3 | Package Responses JSON Object | M4 |
| Package V3 Description | M2 | Package Responses JSON Object | M4 |

这意味着本方案停用 Parent Repartition、移除 Parent Resolution 时，不得顺带修改 Parent Induction 的 M2 语义 profile、Responses 通道或 M3 物理 lane，也不得把 `execution_tier` 再解释为 thinking effort。

### 0.3 已完成验证与交接提醒

- CDECR 全量测试：`309 passed, 3 skipped`；
- Ruff、strict mypy、`git diff --check` 已通过；
- 未进行真实模型调用验收；
- 以上解耦修改当前仍位于未提交工作树中，主对话实施本方案时必须基于当前工作树继续，不能用旧版本覆盖 `model_routing.py`、`cli.py`、`cross_document.py`、`config.py` 和 Package V3 路由修改。

## 1. 本轮目标与明确边界

本轮只做一次小范围 Parent 层收敛：

```text
Atomic
  -> Parent Induction（保留，窄幅降低明显误合）
  -> Parent Proposal Pool
  -> Package V3 Initial / Rolling Global Clustering
  -> 现有跨 MCP Atomic owner
```

明确不做：

- 不修改 `PackageV3OccurrenceInput`，V3 继续只接收 `occurrence_id + parent_occurrence`；
- 不增加 scope、role、identity cues 或其他 occurrence DTO 字段；
- 不修改 Package V3 Initial/Rolling Prompt 与 Schema；
- 不修改跨 MCP Atomic owner score、tie-break、审计和最终唯一归属；
- 不新增硬阻塞校验；
- 不增加新的 LLM 节点。

## 2. Parent Induction：保留，只做轻量 Prompt 微调

### 2.1 当前问题

V3 无法拆开一个已经复合的 Parent Occurrence，因此 Induction 对明显独立的报告、市场发生、协议或交易不应过度合并。但本轮不重构 Induction DTO、Schema、上下文组织、batch、checkpoint、repair 或 fallback。

### 2.2 修改方式

仅在 `parent_occurrence_induction.md` 中将现有边界说明压缩调整为下面两句，不新增长示例或复杂规则：

> Keep clearly independent reports, market occurrences, agreements, transactions, reactions, and background context in separate parents. A field difference alone does not require a split unless the evidence supports a distinct real-world boundary.

该修改的尺度：

- 只弱化明显复合 Parent 的误合倾向；
- 不把 issuer、family、metric、object 或任一单字段变成 hard split；
- 不要求模型输出 reasoning/confidence；
- 不修改输出 Schema；
- 不改变失败局部化和文档 fail-open 行为。

## 3. Parent Repartition：在 V3 主路径中停用

### 3.1 修改方式

`build_parent_occurrence_pool()` 在 Induction 和缺失任务 fallback 后直接编译 Parent Proposal，不再调用 `_repartition_suspect_groups()`。

要求：

- 不发起 `parent_induction_repartition` 模型请求；
- telemetry 固定记录：
  - `repartition_enabled=false`；
  - `suspect_group_count=0`；
  - `repartition_request_count=0`；
- checkpoint/resume 不再等待 Repartition；
- 旧 Repartition checkpoint只保留为历史记录，不读取、不删除；
- 暂时保留底层 helper 一轮，待本次回归验证稳定后再做物理删除，避免本轮同时扩大代码改动面。

预期收益：最近一次30篇运行中 Repartition 为16 calls、约占总 Token 6.81%；停用后原则上消除该节点全部请求、Token和累计模型延迟。质量风险由第2节的轻量 Induction Prompt微调承担，但不宣称一定无回归，需由后续30篇测试确认。

## 4. 移除 Parent Resolution R1/R2 可执行路径

### 4.1 当前事实

现役 Bulk 和 Incremental V3 路径均调用：

```text
ParentOccurrenceService.build_parent_occurrence_pool()
  -> PackageWorkflowV3Service.run()
```

旧 `ParentOccurrenceService.run()` 内的 prototype embedding、candidate graph、R1、R2、merge admission 和 frozen V2 partition 已不参与 V3 正式路径，与 Package V3 Global Clustering 职责重复。

### 4.2 删除范围

从现役代码中移除：

- `ParentOccurrenceService.run()` 的旧 V2 Resolution 执行入口；
- `_resolve_wave()` 及 R1/R2专用调用链；
- prototype embedding、candidate routing、bridge ledger、merge admission、R1/R2 reduce/reconcile等仅服务旧Resolution的 helper；
- `ParentOccurrenceService.__init__()` 中仅服务 Resolution 的并发、route quota、candidate K、resolution token budget参数；
- 生产 engine/CLI 中仅为 Parent Resolution保留的配置透传；
- 旧 Resolution runner 和对应主动执行测试，或将其明确移入历史离线兼容目录，确保生产代码无法误调用。

### 4.3 保留范围

为避免破坏历史 Registry和报告读取，本轮保留：

- 旧 `FrozenParentPartition` DTO的反序列化能力；
- 历史 V2 partition表和数据库 migration；
- `project_frozen_partition()` 对旧冻结产物的只读投影兼容；
- 历史审计、checkpoint和artifact读取能力。

不得删除或迁移既有 SQLite 历史数据。

## 5. 明确保留不变的 V3 行为

以下内容不得在实施中顺手调整：

1. `PackageV3OccurrenceInput` 仍为：

   ```json
   {"occurrence_id":"PO-000001","parent_occurrence":"..."}
   ```

2. Initial/Rolling clustering仍使用现有 Prompt、Schema、batch size、token estimator和顺序 Registry更新；
3. Initial/Rolling clustering仍为 M3=`low`，Description仍为M2=`none`；
4. Package V3仍使用专用 Responses JSON Object通道和M4物理 lane；
5. 跨 MCP Atomic冲突仍按现有支持数、membership支持数、MCP ID顺序选唯一 owner；
6. Description fallback、CAS finalize、checkpoint、幂等和审计逻辑不变。

## 6. 测试与验收

本轮实施后的最低验证：

1. 单元测试：
   - V3主路径不会调用 `_repartition_suspect_groups()`；
   - `parent_induction_repartition` 模型调用数为0；
   - Induction失败仍保持原有局部fallback；
   - V3 occurrence payload字段集合完全不变；
   - 跨MCP Atomic owner测试结果完全不变；
   - 生产代码不存在 Parent Resolution R1/R2可执行入口；
   - 历史 `FrozenParentPartition` 仍可读取和投影。

2. 静态检查：Ruff、strict mypy、`git diff --check`。

3. 全量 CDECR测试不得低于当前 `309 passed, 3 skipped` 基线。

4. 后续真实30篇验收重点只比较：
   - Parent Induction调用数、Token和失败数；
   - Repartition calls必须为0；
   - Parent复合误合是否增加；
   - Package Pair Precision/Recall/F1、singleton漏合和大簇误成员；
   - Package V3 input DTO hash/字段集合保持不变；
   - 全流程钟墙和总Token。

验收阶段若质量下降，只评估“Induction Prompt微调”或“停用Repartition”的恢复必要性；不得借机恢复 R1/R2、修改 V3 DTO或改写跨MCP owner。

## 7. 最终实施顺序

1. 先保护并复核当前 M2/M3/M4解耦工作树；
2. 微调 Parent Induction Prompt；
3. 在 `build_parent_occurrence_pool()` 跳过 Repartition并补齐零调用telemetry；
4. 删除 Parent Resolution R1/R2现役执行链和无效配置，保留历史只读兼容；
5. 更新测试和 `changelog`；
6. 完成静态检查及全量 CDECR测试；
7. 后续由独立任务决定是否启动真实30篇验收。

