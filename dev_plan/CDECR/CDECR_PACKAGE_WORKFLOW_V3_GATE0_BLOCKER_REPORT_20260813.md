# CDECR Package Workflow V3 Gate 0 与当前验收状态

日期：2026-08-13

## 结论

最初的 Gate 0 阻塞已解决。百炼的严格结构化输出应使用 Chat Completions 的 JSON Schema 模式，而不是 Responses `text.format=json_schema`。V3 Node 1、Node 2、Node 3 已分别完成一次真实调用，均通过服务端 JSON Schema 约束和本地业务校验。

V3 主链、持久化、投影、bulk/incremental 接入和本地回归已经完成。两份冻结 Package-only Gate A 也已真实运行并完成幂等复验，但质量门槛未通过，因此不能据此启用 V3 作为生产 Package 聚合实现。

fresh 30 篇全流程已经启动，但在进入 V3 之前，百炼账户开始返回 `Arrearage`。该错误同时影响 Field、N9/Atomic 和 Parent Induction，因此当前部分结果不能作为正式质量验收样本。现有 Registry 和失败 checkpoint 均保留，未删除、未伪造成功。

## Gate 0：已通过

传输合同：

- API：百炼 Chat Completions；
- `response_format.type=json_schema`；
- `json_schema.strict=true`；
- 模型：`deepseek-v4-flash-0731`；
- reasoning effort：`high`；
- Node 1/2/3 均使用各自的严格 JSON Schema。

真实探针结果保存在 `.tmp/cdecr/package_v3_provider_schema_probe_20260813.json`：

| 节点 | Schema | Input | Output | Reasoning | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| Initial clustering | strict JSON Schema | 222 | 176 | 131 | 通过 |
| Rolling clustering | strict JSON Schema | 293 | 2,395 | 2,350 | 通过 |
| Registry description | strict JSON Schema | 253 | 168 | 135 | 通过 |

## 冻结 Gate A：已运行但质量未通过

| 冻结输入 | Atomic | Proposal | Package | Singleton | 最大簇 | Pair P/R/F1 | V3 Token |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| R3-derived | 192 | 115 | 36 | 18（50.00%） | 86 | 81.47% / 79.05% / 80.24% | 38,592 |
| formal-V2-derived | 281 | 132 | 31 | 5（16.13%） | 63 | 69.66% / 41.01% / 51.62% | 64,671 |

两份冻结运行的二次幂等复验均为 0 次新模型调用且 partition hash 稳定。失败主因是超大簇和错误合并，而不是 Schema、断点或 Apply 完整性。

## fresh 30：当前真实阻塞

首次运行产物：

- Registry：`.tmp/cdecr/package_v3_full30_real_20260813.sqlite3`；
- Report：`.tmp/cdecr/package_v3_full30_real_20260813_report.json`；
- 30/30 单文档处理完成；
- 257 个 Atomic 已形成；
- 30 个 `PARENT_INDUCE` checkpoint 均为 `FAILED_RETRYABLE`；
- V3 Registry 尚未进入 clustering/finalize。

本轮百炼欠费不是单一 Package 请求失败：Field、N9/Atomic 和 Parent 均出现 `provider_arrearage`。因此只补跑 Package 会保留上游降级污染，不能当成正式验收。恢复策略必须根据任务账本判断是否可在同一 Registry 安全重试失败阶段；若旧的 degraded Atomic 已不可逆影响快照，则需要新的干净 Registry，且必须在报告中说明这是验收重建而非 V3 逻辑失败。

一次最小恢复探针仍得到 HTTP 400 `Arrearage`，因此没有继续高频重试或消耗无效请求。

## 本地验证

- Ruff：通过；
- strict mypy：通过；
- CDECR 全量回归：286 passed，3 skipped；
- 两份冻结 Gate A：真实运行完成；
- 幂等复验：0 次新调用、hash 稳定。

当前结论是：代码与严格 JSON Schema 合同已通过，V3 冻结质量 Gate 未通过，fresh 30 又被外部账户状态中断。账户恢复后应完成正式 fresh 30 与独立逐 Package 审核，但无论结果如何都不自动回滚。
