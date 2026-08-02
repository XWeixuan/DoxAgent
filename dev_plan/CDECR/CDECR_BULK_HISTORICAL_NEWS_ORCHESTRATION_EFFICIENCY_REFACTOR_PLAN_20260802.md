# CDECR 批量历史新闻场景的编排与效能重构方案

> 日期：2026-08-02  
> 性质：基于当前真实代码、原第三轮方案和 2026-08-02 真实 30 篇验收的重新设计  
> 本轮范围：审查与方案，不修改运行时代码，不调用真实模型  
> 适用场景：一次性处理一个固定时间窗内几十至数百篇历史新闻  
> 非目标：改变实时增量业务语义、重写领域节点、通过扩大复杂 LLM batch 换吞吐

## 0. 结论先行

当前 `BULK_EPOCH` 不是原方案中的完整 epoch 编排器，而是一个有用但不完整的过渡版本：

1. 已完成单文档 `Map` barrier、exact-duplicate representative 规划、稳定输入顺序、运行模式入口，以及“除最后一篇外暂缓 N13”的骨架；
2. N5.5-N12 仍按文档完整串行，原方案中的阶段 barrier、候选图、connected component 并行、单独的 epoch finalize、版本冲突重排和可恢复 checkpoint 均未完成；
3. 当前 N13 barrier 存在正确性缺口：前面文档触达的 Package 没有被累积成 epoch 范围，最后一次 N13 只从最后一篇返回的 Package 集合发起候选；若最后一篇失败或被旧结果复用，epoch 可能根本没有完整收尾；
4. 当前跨文档 `processing_key` 包含 `execution_mode`，但不包含 epoch manifest、stage、snapshot 或 finalize 角色，因此不能承担 epoch 幂等和重启语义；
5. 最新 30 篇运行的主要效能瓶颈已经变成 N9 与 N12，而不是 N13。新方案不再机械照搬原方案的全节点组件化，也不把当前偏低的 N13 调用量当作已证实的降本成果。

因此在**设计语义**上保留三层边界，方便实现、测试、诊断和回滚归因：

- **P0：把 `BULK_EPOCH` 做成正确、可恢复、独立收尾的 epoch**；
- **P1：只并行已经证明互不影响的 Atomic/N9 组件**，组件内保持当前稳定顺序；
- **P2：在 Atomic barrier 后并行 Package/N12 组件，并让 N13 对全 epoch touched set 做一次真实收尾**；
- **P3 可选：只有 profiling 证明必要时才组件化 N5.5 Field**，不把它作为本轮收益的前置条件。

但在**执行与交付维度**，P0、P1、P2 必须一次性实现、集成、验收和启用，不拆成三轮上线，不交付只有 P0 或只有 P0+P1 的半成品。三层标签不代表发布阶段；它们只用于明确依赖关系和定位回归。正式 `epoch_v1` 必须同时具备正确 epoch、Atomic component 并行、Package component 并行和全量 N13 finalize。

这个设计不是在旧流程旁增加第二套业务实现。实时与批量模式继续复用相同的候选、Prompt、模型 adapter 和 Apply 代码；新增内容只负责 epoch 状态、阶段计划、互斥边界和恢复。

## 1. 审查依据与事实边界

本方案直接核对了以下实际项目内容：

- 原方案：`CDECR_POST_30_ACCEPTANCE_OPTIMIZATION_PLAN_20260731.md` 第 1 节、Batch D、验收与回滚条款；
- 当前入口：`src/cdecr/cli.py` 的 `_events_batch()`、`_evaluation_run_locked()` 和 `--execution-mode`；
- 当前单文档批处理：`src/cdecr/single_document.py:1082`；
- 当前跨文档运行与批处理：`src/cdecr/cross_document.py:867`、`:910`、`:1257`；
- 当前 N9/N10/N12/N13 内部函数边界：`src/cdecr/cross_document.py:1386`、`:1676`、`:2819`、`:3456`、`:3736`、`:4424`、`:5850`；
- 当前 Registry schema：`src/cdecr/registry.py`；
- 当前 bulk 专项测试：`tests/cdecr/test_cross_document.py:60`；
- 最新真实验收：`CDECR_30_REAL_ACCEPTANCE_DEEPSEEK_V4_FLASH_REPORT_20260802.md`、对应 machine report 与只读 SQLite Registry。

以下结论来自当前代码与真实运行，不是对未来实现的推测：

- `SingleDocumentProcessor.process_batch()` 已用 `document_concurrency` 并行 representative，默认并发为 3；重复文档仅等待 exact representative；
- `CrossDocumentEngine.process_batch()` 对 N5.5-N12 使用普通 `for` 循环，跨文档仍串行；
- 30 篇最新运行中单文档 30/30 成功、跨文档 28/30 成功；28 个成功跨文档 run 中，27 个记录 `PACKAGE_N13_DEFERRED_TO_BULK_BARRIER`；
- `package_merge` 仅 4 次调用、全部属于 1 个 run；
- N13 的 `_correct_packages_v13(packages, ...)` 用参数 `packages` 生成 `result_ids`，而最终调用者传入的是当前文档 N12 返回值，不是 epoch 全部 touched Package；
- Registry 目前没有 bulk epoch、epoch item/component、barrier 或 stage snapshot 表；
- 现有专项测试只验证稳定排序、前一篇 defer、最后一篇不 defer、两者 epoch ID 相同，没有覆盖真实 finalize 完整性和恢复；
- 最新运行 N9 占 aggregate model latency 41.07%，N12 占 20.72%；N9/N12 的 scheduler queue wait P50/P95 都为 0，说明串行编排没有持续填满已有模型 lane；
- N12 dictionary payload 的真实 A/B 虽明显降 Token，但业务 P/R 与碎片化变差，当前仍应保持 shadow，不能把它夹带进 bulk 重构。

## 2. 原方案完成情况

| 原方案能力 | 当前状态 | 实际实现 | 判断 |
| --- | --- | --- | --- |
| 固定输入、稳定排序 | 已完成 | `published_at + message_id` 去重排序 | 可保留 |
| 单文档 Map barrier | 已完成 | 全体单文档先跑完，exact duplicate 分两波 | 可保留 |
| `INCREMENTAL` / `BULK_EPOCH` 双模式 | 基本完成 | CLI 与 engine 都有模式参数；batch 默认 bulk | 入口已具备，语义未完整 |
| 稳定 epoch ID | 部分完成 | 根据 message IDs 和 engine version 计算短 ID | 只作为参数/审计，未形成持久状态 |
| N13 延迟到全体 N12 后 | 表面完成 | 前 N-1 篇 defer，最后一篇内联执行 N13 | barrier 存在，但不是独立 finalize |
| epoch 全量 touched Package | 未完成 | 只使用最后一篇 N12 返回的 Package | P0 正确性缺口 |
| 最后一篇失败仍可收尾 | 未完成 | finalize 绑定最后一篇 `process()` | P0 正确性缺口 |
| epoch 幂等/断点续跑 | 未完成 | evaluation 仅写外部 JSON checkpoint | 不能作为运行时恢复协议 |
| Field epoch/component | 未完成 | N5.5 每文档串行 Apply | 本轮不应优先重构 |
| Atomic epoch/component | 未完成 | N6-N10 每文档完整执行 | 当前第一性能优先级 |
| Package assignment epoch/component | 未完成 | N11-N12 每文档完整执行 | 当前第二性能优先级 |
| N13 pair component + pair ledger | 部分完成 | 单次调用内有 pair 去重、profile hash 与缓存 | 缺 epoch touched union、独立 finalize 和 bounded convergence |
| Read/Decide/Apply | 局部具备 | 已有候选、决策、Apply 私有函数 | 尚未被 stage orchestrator 使用 |
| 单 writer + expected version | 未完成 | 当前靠跨文档串行与 SQLite 写事务避免并发 | 需要显式 stage Apply 合同 |
| late-edge 检查 | 未完成 | 没有 component snapshot/replan | 并行启用前必须补 |
| Prompt 对齐 | 当前无需变化 | bulk 元数据未进入模型任务 | 新方案继续保持不改 Prompt |

总体判断：当前完成的是**批量入口和最浅层 barrier 骨架**，不是原方案所说的阶段化效能重构。不能仅因枚举值叫 `BULK_EPOCH` 就视为该方案已经落地。

## 3. 当前真实流程及其问题

```text
全体文档
  └─ SingleDocument.process_batch：representative 并发、exact duplicate 后置
       ↓ 全体单文档 barrier
排序后的 doc 1
  └─ N5.5 → N6/N7/N9/N10 → N11/N12 → defer N13
排序后的 doc 2
  └─ N5.5 → N6/N7/N9/N10 → N11/N12 → defer N13
...
最后一个 eligible doc
  └─ N5.5 → N6/N7/N9/N10 → N11/N12 → N13(仅本 doc touched Package 发起)
```

### 3.1 为什么运行仍然很慢

单文档已经并行，但真正昂贵的跨文档 N9/N12 被包在逐文档串行循环里。节点内部即使有 batch 并发，也只能消费当前一篇文档产生的少量任务；当该文档只有一个或少量 batch 时，M2/M3 lane 大部分时间空闲。最新运行 N9 与 N12 的 queue wait P50/P95 均为 0，结合两者占 61.79% aggregate latency，说明当前主要矛盾不是并发上限太低，而是 orchestrator 没有足够的独立任务同时提交。

### 3.2 当前 N13 barrier 为什么不完整

当前做法把“barrier”表达成 `defer_package_merge=True/False`，最终 N13 仍属于某一篇文档的 run。由此产生四个问题：

1. **覆盖缺口**：最后一篇的 `packages` 不包含前 27 个成功文档独有的 touched Package；
2. **失败耦合**：最后一个 eligible 文档在 N5.5-N12 失败，之前所有 defer 不会被独立 finalize；
3. **复用耦合**：完成结果在进入 N5.5 前可按 processing key 复用，但 key 不含 defer/finalize 角色；一个曾作为 defer 文档完成的结果，不能保证在新 cohort 成为最后一项时补做 N13；
4. **状态失真**：单文档跨文档结果可显示成功，但 epoch 是否完成 N13 没有独立状态，外部无法区分 `N12_DONE` 与 `EPOCH_FINALIZED`。

这也是为什么不能把当前 4 次 N13 调用、2.73% input 占比直接解释为正确的 bulk 降本。真正修复全 epoch 覆盖后，N13 pair 数和 Token 可能先上升；只有按“每个有效 touched Package / 每个 judgeable pair”的覆盖归一后，才能谈效率提升。

### 3.3 为什么不能直接按文档强行并行 N5.5-N12

不同文档可能命中同一 Canonical Field、Atomic 或 Package。若两篇同时读取旧 head、分别让 LLM 决策并直接 Apply，会出现：

- 两边都 CREATE_NEW，造成碎片化；
- 两边都向已经变化的旧 target MERGE，产生陈旧决定；
- N12 读取不到同 epoch 先前形成的 parent anchor/Package；
- N13 对未稳定 profile 做重复或矛盾判断。

因此并行单元必须是候选关系的 connected component，而不是文档；component 内仍保持稳定顺序。若候选图形成巨型 component，应诚实退化为该 component 内串行，而不是为追吞吐硬切真实边。

## 4. 设计目标与非目标

### 4.1 目标

1. 批量 epoch 的 N13 覆盖所有成功通过 N12 的 touched Package，并且不依赖最后一篇文档；
2. epoch 可重启、可继续、可区分 `PARTIAL` 与 `FINALIZED`，重复运行不重复调用已完成任务；
3. 在不改变 Mention、候选业务规则、Prompt 和模型 batch 语义的前提下，提高 N9/N12 lane 利用率；
4. 组件并行不降低 candidate coverage，不引入新的错误合并或碎片化；
5. 单个文档、task、component 失败不扩大为整批失败，成功组件可以继续产出；
6. 对几十至数百篇输入使用有界内存、背压和稳定 checkpoint；
7. 实时增量模式继续使用相同领域实现，不维护第二套 Prompt/Apply 逻辑。

### 4.2 非目标

- 不在本项目中引入通用 DAG/分布式工作流平台；
- 不同时改模型、thinking effort、Prompt、Wire DTO、候选 top-k 或复杂节点 batch 大小；
- 不以吞吐为理由减少候选、跳过 repair 或放宽 SAME/MERGE 业务边界；
- 不把 epoch/component/version 元数据加入 LLM payload；
- 不要求每个 batch 都能并行。相关性强的历史新闻形成单一大 component 时允许串行；
- 不把 `BULK_EPOCH` 用于要求逐篇即时可见最终 Package 的实时监测入口。

## 5. 目标架构

```text
Epoch manifest（固定语料、配置、模型/Prompt/策略版本）
  ↓
A. Single-document Map
   并发 representative → duplicate follow-up → 成功文档集合
  ↓ barrier
B. Stable Field lane（P0/P1 仍按稳定顺序）
   N5.5 + package hint resolution → Field links checkpoint
  ↓ barrier
C. Atomic stage
   批量 N6 compile / embedding / N7 scheduler graph
   → connected components
   → component 内稳定顺序，component 间有界并行 N9/N10
   → expected-version Apply / 局部 replan
  ↓ barrier
D. Package assignment stage
   全量 N11 anchor materialize / N12 scheduler graph
   → connected components
   → component 内稳定顺序，component 间有界并行 N12 Apply
   → 持久化 epoch touched Package union
  ↓ barrier
E. Dedicated N13 finalize
   resolve touched roots → material profile snapshot → pair dedupe
   → pair components 有界并行 Decide
   → 受限 Apply → 最多一次 material-change convergence
  ↓
Epoch FINALIZED / PARTIAL（与各文档状态分开）
```

### 5.1 一套领域实现，两种编排

建议引入一个很薄的 `CDECRBatchOrchestrator`，但不复制 `CrossDocumentEngine`：

- `INCREMENTAL`：为单个文档建立一个即时 stage context，顺序调用相同的 Read/Decide/Apply facade，并立即 finalize touched Package；
- `BULK_EPOCH`：固定 manifest 后分阶段调用同一 facade，延迟到全体 N12 稳定后统一 finalize；
- `CrossDocumentEngine.process()` 保留为兼容入口，内部逐步改为调用 facade；
- CLI `events.batch` 与 evaluation harness 必须统一调用 orchestrator，删除 evaluation 中手写的第二套 `for + defer` 编排。

这会替换现有重复流程，而不是新增永久旁路。

### 5.2 最小持久化模型

只新增两张编排表，避免把审计表误作运行状态：

#### `bulk_epochs`

最小字段：

- `epoch_id`：manifest 的稳定哈希；
- `manifest_hash`：排序后的输入 IDs、source fingerprints、engine/prompt/policy/model 配置；
- `status`：`PLANNED/RUNNING/PARTIAL/FINALIZING/FINALIZED/FAILED`；
- `current_stage`；
- `successful_message_count/failed_message_count`；
- `created_at/updated_at/finalized_at`。

#### `bulk_epoch_items`

统一承载 document、component 和 finalize checkpoint：

- `epoch_id + stage + item_id` 唯一；
- `status`：`PENDING/RUNNING/SUCCEEDED/DEGRADED/FAILED/REPLAN_REQUIRED`；
- `input_hash/snapshot_hash/expected_versions_hash`；
- `result_ref_json`：只存已有领域记录 ID、touched roots、错误码等短引用；
- `attempt_count/started_at/finished_at`。

不再单建审计、component、touch 三套表。模型调用、领域决策和 reason 继续使用现有表；`bulk_epoch_items` 只回答“该阶段是否完成、能否安全复用、下一步从哪里继续”。

### 5.3 幂等键分层

当前把整篇跨文档结果压在一个 `processing_key` 上，无法表达阶段性复用。新方案拆成：

- **immutable document key**：source fingerprint + single-document Prompt/schema/model 版本；
- **stage task key**：业务输入 hash + candidate/profile snapshot hash + policy/model/Prompt 版本；
- **epoch key**：固定 manifest + orchestrator version；
- **N13 finalize key**：epoch ID + resolved touched roots + 两端 material profile hashes + N13 policy/model/Prompt 版本。

`epoch_id` 不进入 Mention/Atomic/Package 业务 ID。不同 epoch 可以复用 immutable 文档结果、相同 embedding 和完全相同的模型 task；但只要候选 head/material snapshot 改变，mutable stage 必须重规划，不能因同一 message 曾成功就盲目复用。

## 6. 一次性交付中的三层设计语义

P0/P1/P2 在代码实现上仍应按依赖顺序完成，但属于同一个开发任务、同一个 `epoch_v1` 版本和同一次完整验收。实施过程中可以分别运行单元测试和 recorded-response 诊断；在 P0-P2 全部完成前，不将任何不完整组合视为本方案已落地，也不单独发布到正式 bulk 入口。

## 6.1 P0：正确 epoch 与恢复基础

P0 是 P1/P2 的内部前置依赖，不是独立交付批次。它本身不追求显著提速，但必须与 P1/P2 在本轮一次性落地。

### P0-1 统一批量入口

将 `_events_batch()` 与 `_evaluation_run_locked()` 都改为调用同一个 orchestrator：

```python
run_bulk_epoch(message_ids, *, execution_mode="BULK_EPOCH") -> BulkEpochResult
```

`BulkEpochResult` 至少提供：

- epoch 状态与当前 stage；
- 每个 message 的 single-document / cross-document 状态；
- succeeded、degraded、failed component 数；
- complete touched Atomic/Package root IDs；
- N13 是否已 finalize、未覆盖原因；
- 各 stage wall time、model calls、tokens、queue wait。

CLI 输出仍可投影回现有逐文档结果，避免破坏调用方；但不能再由“最后一篇是否成功”代替 epoch 状态。

### P0-2 把 N13 从最后一篇文档中拆出来

新增明确的领域入口：

```python
finalize_bulk_epoch_packages(
    *,
    epoch_id: str,
    touched_package_ids: Sequence[str],
    expected_material_hashes: Mapping[str, str],
) -> PackageFinalizeResult
```

执行规则：

1. 每个成功 N12 Apply 都将其 touched Package IDs 写入 epoch item；
2. 进入 N13 前解析 redirect，形成去重后的完整 root set；
3. N13 不再接受“最后一篇返回的 Package 列表”作为 epoch 范围；
4. 最后一篇 N5.5-N12 失败时，仍对其他成功组件的 touched set 执行 finalize；
5. 若某个 N12 component 失败，epoch 标 `PARTIAL`，N13 对已成功范围继续执行，并明确记录 `uncovered_component_ids`；
6. finalize 成功后独立标记 `FINALIZED`，重复调用以 finalize key 直接复用；
7. 增量模式继续对当前文档的 touched set 立即调用同一个函数。

在开发中的 P0 单测与 recorded-response 诊断里，可以暂时用 N5.5-N12 当前稳定顺序验证 N13 完整性；正式交付的 `epoch_v1` 不保留该半成品运行形态，而是同时接入 P1/P2 component 编排。

### P0-3 修正完成结果复用边界

现有 `CrossDocumentEngine.process()` 的 completed-result 快速返回不应决定 epoch finalize。最低限度改为：

- 已复用的文档 N5.5-N12 结果仍要把既有 touched Package refs 注册到当前 epoch；
- epoch finalize 永远由 orchestrator 检查，不依赖某个 `process()` 是否实际执行；
- 旧 `defer_package_merge` 参数只保留一个兼容周期，内部不再承担业务语义，随后删除；
- 不能简单把 `bulk_epoch_id` 塞进现有 processing key，否则相同文档在每个 cohort 都会重复消耗模型；应使用上一节的 stage key 与 snapshot key。

### P0-4 checkpoint 与恢复

恢复流程固定为：

1. 校验 manifest hash；不一致则创建新 epoch，不覆盖旧 epoch；
2. `RUNNING` item 超过 lease 后可重新认领；
3. `SUCCEEDED` item 的 input/snapshot hash 相同则复用；
4. `DECIDE_SUCCEEDED/APPLY_PENDING` 的 item 从 Apply 恢复，不重新调用模型；
5. Apply 使用现有 append-only/immutable 决策记录，重复提交必须幂等；
6. epoch 在 N12 barrier 后中断，重启直接进入 N13 finalize；
7. 单个 item 重试超过上限后标 `FAILED/DEGRADED`，不把整个 epoch 回滚或伪装成 `CREATE_NEW`。

### P0 验收

- 更早文档独有的 touched Package 必须出现在 N13 candidate generation；
- 最后一个文档 N12 失败，其他成功范围仍完成 N13；
- 在 N12 barrier 与 N13 Apply 之间强制中断，恢复后 N13 模型调用不重复；
- 相同 manifest 重跑新增 Mention/Atomic/Package 与模型调用均为 0；
- overlapping cohort 不会把旧 epoch 的 deferred 状态误当作已 finalize；
- 相同 provider 响应回放下，P0 的 N5.5-N12 业务结果与当前稳定串行路径完全一致；
- N13 成本必须同时报告 raw cost 与 coverage-normalized cost，不要求 raw Token 比当前不完整覆盖更低。

## 6.2 P1：共享只读准备 + Atomic/N9 component 并行

P1 负责最新运行中最大的跨文档瓶颈 N9。它在同一次实现中与 P2 集成，但保留独立指标，防止 N9 与 N12 的收益和回归相互掩盖。

### P1-1 提取现有最小 facade

复用当前已有私有函数，提取而非重写：

```text
read_atomic_plan(snapshot)  -> compiled identities, embeddings, candidates, components
decide_atomic_task(task)    -> 现有 N9 decision/fallback
apply_atomic_decision(...)  -> 现有 N10 Apply + expected-version check
```

`decide` 不写领域状态；`apply` 不调用模型。增量模式也改走相同 facade，从而避免两套业务逻辑漂移。

### P1-2 批量只读准备

在所有 eligible 文档完成 N5.5 后：

1. 一次性编译全部 Mention Identity；
2. 按 input hash 读取或补齐 Mention/Atomic head embeddings；
3. 使用现有 N7 召回规则生成正式模型候选；
4. 另生成只用于调度的 wider recall pool；它不能进入模型 payload，也不能改变正式 top-k；
5. 用以下真实依赖边构建无向图：
   - 两个 incoming Mention 共享同一正式或 wider-pool Atomic root；
   - 一个 incoming Mention 是另一个 incoming 临时事实的可召回近邻；
   - 两者会写入同一 canonical root 或同一 singleton absorption 目标；
   - 已验证 deterministic SAME/DIFFERENT 仍按现有业务规则形成必要依赖。

不能仅因同一 ticker、同一公司或同一宽泛 period 就连边，否则 MU 等批量语料会无意义地形成巨型 component。scheduler graph 只使用“足以进入现有候选边界或近阈值”的信号。

### P1-3 组件执行语义

- Atomic component 最多 12 个 worker 并行提交；实际在途调用仍受对应 provider lane 和启动节流控制；
- component 内严格按 `published_at + message_id + mention_id` 顺序；
- 后一个 task 读取本 component 前一个 Apply 后的本地/持久化 head；
- 模型 request、Prompt、schema、候选 top-k 和 N9 batch=3 均不改变；
- Apply 仍由一个逻辑 writer queue 串行提交，SQLite 事务只负责最终原子性；
- Apply 前检查 candidate root/version。版本不一致时不硬失败、不沿用陈旧决定，只将该 component 标为 `REPLAN_REQUIRED`；
- component 失败不影响其他 component，失败项继续使用现有 assessment 级局部降级，不扩大成整文档 `CREATE_NEW`。

“单 writer”不是要求 LLM Decide 也串行。目标是让耗时的只读 Decide 并行，而短 Apply 串行且可验证。

### P1-4 late-edge convergence

并行 Apply 后只做一次低成本候选复查：

- 若 material identity/profile 没有产生跨 component 新边，直接结束；
- 若出现新边，只把相关 component 合并后放入串行 convergence lane；
- 已经完成且 input/snapshot 未变化的 task 不重调模型；
- 每个 component 最多一次自动 replan。仍持续变化则标 `DEGRADED_REPLAN_EXHAUSTED` 并按稳定串行路径处理，不循环重试。

### P1-5 巨型 component 与背压

- component 大小本身不是错误；大 component 内串行；
- component plan、task payload 按需从 Registry 读取，不一次把几百篇的所有 prompt 保存在内存；
- 同时在途的 M2/M3 task 不超过 scheduler lane + 1 个预取窗口；
- embedding 按 provider 已有上限合批，失败按 item 降级，不重跑整批；
- 记录最大 component、P50/P95 component size、并行覆盖率、串行退化率。只有存在足够多独立 component 才宣称吞吐收益。

### P1 验收

- 固定 recorded responses 下，Atomic memberships、redirects、candidate coverage 与稳定串行 reference 等价；
- 真实模型 A/B 固定模型、effort、Prompt、schema 和 corpus，Atomic Pair P/R、N9 MERGE P/R 不得越过质量回滚线；
- known hard-conflict violations 不增加，candidate recall@8 保持既有门槛；
- 新增 singleton/fragmentation 不增加；
- N9 scheduler active concurrency 实际大于 1，且 queue wait/429/timeout 没有抵消收益；
- component version conflict 均被局部 replan，没有陈旧 Apply；
- 若候选图只有一个 component，输出应等价并诚实报告“无可并行空间”。

## 6.3 P2：Package/N12 component 并行 + 完整 N13 finalize

P2 在内部实现依赖上位于 P1 之后，但不另行发布。开发时先证明 P1 的 recorded-response 等价，再接入 P2；最终只验收和启用 P0+P1+P2 完整组合。

### P2-1 N11 barrier

在所有 Atomic component 稳定后一次性完成：

- Mention anchor 到 Atomic anchor 集合聚合；
- canonical parent anchor materialize；
- Atomic current root 与 Package seed/profile 准备；
- existing Package embedding 的 input-hash 复用。

N11 不新增模型 reasoning，也不把 epoch/component ID 写进模型可见字段。

### P2-2 Package scheduler graph

使用现有 N12 正式候选与较宽调度池构图，依赖边至少包括：

- 多个 Atomic event 共享同一候选 Package root；
- 共享可信 canonical parent anchor，且 relation/family 允许成为同一父 Package 候选；
- 同一 source occurrence 下会相互竞争 MEMBER/EXTERNAL_RELATED 位置；
- Apply 会修改同一 Package profile 或 membership root。

仅有相似 raw hint、同 issuer 或宽泛 `latest report` 不能单独制造跨文档组件边，也不能自动合并。共享 canonical anchor 只是调度依赖和重要候选信号，业务决定仍由现有 N12 合同完成。

### P2-3 N12 执行

- component 内保持当前稳定 Atomic 顺序；Package component 最多 10 个 worker 并行提交；
- 保留已经验收较好的 legacy N12 payload；dictionary payload 继续 shadow，不能借 bulk 开启；
- 保持 N12 batch=12，不因积累大量任务就扩大复杂 batch；
- Apply 前检查 Package root/profile version；冲突只重排相关 component；
- 每个成功 Apply 把原始 target、最终 resolved root 与 material hash 写入 epoch item；
- 同一 Package 被多个 item touched 时做集合并集，不采用“第一个/最后一个获胜”。

### P2-4 N13 全 epoch 收尾

N13 finalize 使用 P0 的完整 touched root set：

1. 解析所有 redirect 并冻结一次 material profile snapshot；
2. 按 unordered root pair + 两端 material hash + policy version 精确去重；
3. 复用当前 deterministic guard 和安全缓存，不新增未经 Gold/回放支持的宽规则；
4. pair graph 的独立 component 可并行 Decide，仍保持 N13 batch=12；
5. Apply SAME 后，仅当 member roots、anchor set、family、period、issuer 等 material identity 改变时，重算受影响 pair；
6. 最多一次 bounded convergence，不因 summary 文案、时间戳、审计字段或 embedding rebuild time 重评；
7. `DIFFERENT`/`SAME` 缓存都绑定两端 material hashes，redirect 后重新解析 root，防止复用陈旧 pair。

N13 的核心指标不应再是“比当前 4 次调用更少”，而应是：

- touched Package coverage=100%；
- 相同 material profile pair 不重复调用；
- 每 100 个 covered pair 的 input/output Token；
- 每个 Gold-correct 新增 join 的 Token；
- N13 对 Package P/R 与 fragmentation 的净影响。

### P2 验收

- Package Pair Precision/Recall 不低于同模型稳定串行 reference 的允许波动线；
- Gold fragmentation、singleton excess、false-merge groups 不恶化；
- 所有成功 N12 touched roots 均进入 N13 coverage audit；
- 一个 Package 被多个文档触达，只生成一次相同 material pair 请求；
- N13 Apply 后 material 未变化的 pair 不重评；
- N12/N13 任一 component 失败不会抹掉其他已成功 Package；
- N12 与 N13 的实际并发、wall time、tokens、provider errors 分开报告。

## 6.4 P3 可选：Field/N5.5 component 化

原方案把 Field epoch 与 Atomic/Package 同列，但根据最新运行，Field 只占 aggregate model latency 8.58%，而 Field root 是后续 Identity 的基础。当前优先组件化它，收益小于 N9/N12，回归面却更大。

因此本轮默认保持 Field 稳定串行 Apply，仅做安全的 embedding/input-hash 复用。只有满足以下条件才另立实施项：

- P1/P2 后 N5.5 已成为墙钟前两位瓶颈；
- 可以按 namespace + candidate-root 构成明确互斥 component；
- 同一 provisional/canonical root 的 redirect 与 runtime alias 写入能用 expected version 保护；
- recorded-response 等价测试证明不同 component 执行顺序不改变 Field links；
- 真实 Gold 的 total/conditional P/R 与 runtime alias 风险均不下降。

即便启用，也不做 Snapshot 批量 LLM Decide，不扩大 top-8，不把 namespace 之间的未知项混在一起。

## 7. 并发与 batch 策略

### 7.1 初始配置

历史批量模式应主动利用 provider 的高并发能力。以下是 `epoch_v1` 的建议初始值，约为当前 lane 的 3-4 倍，但仍远低于 provider 声称支持的数百并发，给本地内存、长 thinking 请求和 strict 输出稳定性留出余量：

| 项目 | 当前/初始值 | 本轮策略 |
| --- | ---: | --- |
| single-document concurrency | 3 | bulk 提高到 8；实时模式仍保持 3 |
| M1 lane | 2 | bulk 提高到 8，继续优先 embedding 复用/合批 |
| M2 lane | 6 | bulk 提高到 24 |
| M3 lane | 3 | bulk 提高到 24 |
| M4 lane | 2 | bulk 提高到 12 |
| Atomic component workers | 无 | 12 |
| Package component workers | 无 | 10 |
| N13 pair-component workers | 无 | 12 |
| N9 batch | 3 mentions | 保持 |
| N12 batch | 12 events | 保持 |
| N13 batch | 12 pairs | 保持 |

这些是应用侧上限，不要求每个 epoch 都达到。connected component 数不足或只有一个巨型 component 时仍会自然串行；不能为了填满 24/24 个 M2/M3 lane 切断真实候选边。

### 7.2 启动节流与自动回落

provider 支持数百并发，不代表同一毫秒提交数十个长请求一定稳定。采用 provider 级 token-bucket/admission gate：

- bulk 新请求默认每 **1 秒**放行一个，带 0-250ms jitter；这是启动间隔，不是请求完成后的固定 sleep；
- 请求一旦启动即可与前序请求并行，因此长请求会逐步填满 24/24 的 M2/M3 lane，而不是被串行化；
- 若连续窗口出现 429、连接重置或 provider timeout，启动间隔临时提高到 2 秒，并将对应 lane 减半；
- 连续 30 次成功后按每次 +2 恢复，最高回到配置上限；
- strict/schema/业务校验错误不视为限流信号，避免模型内容错误错误压低系统并发；
- M2/M3 若使用同一个 provider，启动 gate 共享，但 lane 仍按 tier 隔离；不同 provider 分别节流。

固定在每个 request 之后 sleep 1-2 秒会占用 worker、放大长尾且不能控制已经在途的请求，因此不采用。上述启动 gate 只平滑突发，真正的并发边界仍由 semaphore 控制。

如果完整 30 篇验收同时满足以下条件，可继续将 M2/M3 lane 提高到 32/32、component workers 提高到 16；否则保留 24/24：

- 该 stage 存在排队 component 且 provider lane 长时间满载；
- provider 429/timeout/invalid response 比例不升；
- P95 model latency 增幅不超过 15%；
- 业务结果与 recorded-response reference 等价，真实 P/R 不触发回滚；
- 内存峰值与 SQLite writer wait 在预算内。

这里刻意不通过增加复杂 LLM batch 追求吞吐。大 batch 会同时增加上下文干扰、单次失败爆炸半径、尾延迟与 strict 输出难度；component 并发可以复用当前已验收的任务粒度，风险更低。

### 7.3 公平与背压

bulk 与实时增量共用服务时，不能让历史任务占满所有 lane。建议 scheduler 增加轻量 workload class：

- `REALTIME` 保留每个 tier 至少 1 个 permit；
- `BULK` 只使用剩余 permit；
- 没有实时任务时，bulk 可以借用空闲 permit；
- 不新增独立 scheduler，实现上只在现有 semaphore 前加一个有界 admission policy。

若当前部署不会同时跑实时与 bulk，可暂不启用该策略，但接口应预留 workload class，避免以后重写。

## 8. Token、成本与墙钟预期

### 8.1 当前基线

最新 30 篇 DeepSeek V4 Flash thinking 运行：

| 指标 | 当前值 |
| --- | ---: |
| 首轮墙钟 | 15,707,654 ms（约 4:21:48） |
| 调用数 | 973 |
| Input / Output / Total Token | 3,495,773 / 3,066,910 / 6,562,683 |
| N9 input / aggregate latency 占比 | 22.97% / 41.07% |
| N12 input / aggregate latency 占比 | 46.47% / 20.72% |
| N13 input / aggregate latency 占比 | 2.73% / 2.71% |

该运行同时更换了模型和 thinking effort，因此只用于定位当前瓶颈，不能直接用来证明编排优化收益。正式 bulk A/B 必须固定模型配置。

### 8.2 合理预期

编排并行本身不减少每个判断所需的输入/输出 Token，不能把墙钟收益虚报为 Token 收益：

- **P0**：墙钟与 Token 目标为不显著回归；修复 N13 覆盖后 raw N13 Token 可能上升，这是正确性成本；
- **完整 P0-P2**：主要收益来自 N9/N12 独立 component 并行。按 M2/M3 24/24 provider lane 与 12/10 component workers，30 篇墙钟设 **下降 30%-50% 的观察目标**，不是硬承诺；
- 数百篇、多 issuer/多时间段语料若能形成多个独立 component，吞吐目标为 **2x-4x documents/hour**；同 issuer、同一事件密集语料可能明显低于此范围；
- embedding 合批、相同 task/profile 复用和 N13 pair 去重预计使全流程 input Token **净变化 -5% 至 +5%**。只有在完整 N13 coverage 下仍下降，才能宣称降本；
- 当前 3,066,910 output Token 的主要问题与 thinking/model 输出行为有关，orchestrator 无法单独解决，应在独立模型配置 A/B 中处理，不能夹带到本重构。

### 8.3 必须新增的归一化指标

- 每个成功文档 wall time / Token；
- 每个 N9 judgeable assessment Token；
- 每个 N12 event-task Token；
- 每个 N13 covered pair 与 Gold-correct join Token；
- provider aggregate latency 与真实 wall time；
- scheduler active、queue wait、writer wait；
- component 数、最大/P50/P95 size、可并行任务比例、replan 比例；
- cache reuse 按 immutable embedding、task decision、pair decision 分开计数；
- N13 touched coverage 与 uncovered component 数。

## 9. 质量保护与非阻塞错误处理

### 9.1 业务语义保护

- 候选图只决定调度，不新增或删除模型正式候选；
- component 内顺序与当前串行顺序一致；
- Prompt、schema、reason 协议和业务校验保持不变；
- deterministic rule 只复用已经正式启用的规则；
- N9/N12/N13 现有 assessment/item/pair 级 repair/fallback 继续生效；
- 不用更多 `UNRESOLVED`、`CREATE_NEW` 或 `DIFFERENT` 掩盖并行冲突；
- Apply version conflict 触发局部重规划，不触发整 epoch 失败。

### 9.2 故障隔离

| 故障 | 处理 | 不允许的扩大行为 |
| --- | --- | --- |
| 单文档失败 | 排除该文档跨文档输入，epoch 继续 | 整批失败 |
| 单个 N9/N12 task 非法 | 现有 item/assessment 级规范化或降级 | 整 component CREATE_NEW |
| 一个 component provider 失败 | checkpoint + 有界重试；其他 component 继续 | 重跑已成功 component |
| expected version 冲突 | 只重读并重判相关 component | 全 epoch 回滚 |
| N13 个别 pair 非法 | pair 级降级并留痕 | 跳过整个 finalize |
| 最后一篇失败 | 对其他成功 touched roots 独立 finalize | 让此前 defer 永久悬空 |
| late-edge 持续变化 | 一次 convergence 后串行处理相关 component | 无限重规划 |

只有 manifest 损坏、Registry 不可写或关键 schema 迁移不兼容等“继续执行会使所有结果失去业务意义”的错误，才允许 epoch 级阻断。业务质量不足应表现为 `PARTIAL/DEGRADED` 和可见产物，不应抹掉合法结果。

## 10. Prompt 与 Schema 对齐审查

本方案 **不修改任何 Prompt 语句，也不修改模型 JSON schema**，理由不是忽略对齐，而是新的概念均不应成为模型任务：

- `epoch_id/component_id/snapshot_hash/expected_version` 是编排元数据；
- 模型仍然看到与当前相同的 Mention、candidate、Sidecar、Package 与 anchor 业务字段；
- connected component 不表达业务类别，只表达任务之间的读写依赖；
- stable order、checkpoint、writer queue、late-edge 都由代码执行，不应让模型推理。

从“完全不了解项目、只看 Prompt 和 schema 的模型”视角，P0-P2 的单次任务目标没有改变，因此没有必要增加解释。相反，把 epoch 或 component 概念加入 Prompt 会占用注意力并可能让模型误以为要跨 task 协调。

若实施中发现必须改变模型可见候选范围、dictionary wire、task grouping 或批内相互引用，则应停止该阶段，把它作为独立 Prompt/Schema 变更做小样 A/B；不得以“只是编排”为名静默改变合同。

## 11. 测试与验收矩阵

### 11.1 静态与单元测试

必须补齐当前唯一 bulk 测试未覆盖的场景：

1. epoch manifest 对输入顺序稳定、对 source/config 变化敏感；
2. 前序文档独有 touched Package 进入 N13；
3. final document 失败仍 finalize；
4. 所有 eligible 文档失败时产生空但合法的 PARTIAL/FAILED epoch，不调用 N13；
5. N12 barrier 后崩溃，恢复时只补 N13；
6. N13 Decide 后 Apply 前崩溃，不重复调用模型；
7. overlapping epochs 复用 immutable 结果但重算变化的 mutable snapshot；
8. duplicate representative 与 deferred exact duplicates 的结果、anchor lineage 正确；
9. 两个 disjoint Atomic/Package component 交换执行顺序，最终 memberships 相同；
10. 共享候选 root 的任务必在同 component；
11. giant component 自动串行，不按 size 硬切；
12. late edge 只重排相关 component 且最多一次；
13. component 失败不阻断其他 component 与 N13 成功范围；
14. writer expected-version mismatch 不执行陈旧 Apply；
15. 同 material N13 pair 在一个 epoch 内只请求一次；
16. 相同 epoch 重跑新增调用与领域实体为 0。

### 11.2 Recorded-response 等价测试

先用固定模型输出验证编排语义，避免 provider 随机性掩盖竞态：

- reference：全新 Registry 的稳定 `INCREMENTAL`；
- candidate：全新 Registry 的 `BULK_EPOCH epoch_v1`；
- 对比 Mention 集合、Field links、N7/N12 candidate IDs、N9/N12/N13 task input hashes、Atomic/Package memberships、redirect、external relation 与 failure codes；
- 允许 run/epoch/audit ID、时间戳、模型调用顺序不同；
- 不允许 candidate coverage、业务 membership 或 fallback 数因调度不同而变化；
- P0、P1、P2 的 recorded-response 投影分别核对，便于定位差异；但发布候选必须再以 P0+P1+P2 全部开启的 `epoch_v1` 做完整等价测试，不能只凭分层测试通过就交付半成品。

### 11.3 真实 30 篇 A/B

控制变量固定：同一 30 篇、同一 Gold、干净 Registry、同一 provider/model/effort/strict 配置、同一 Prompt/schema/policy。至少保留：

- 技术成功率与失败根因；
- Mention P/R（应与 reference 相同或只受模型随机波动影响）；
- Field total/conditional P/R、candidate recall@8、runtime alias；
- Atomic Pair P/R/F1、N9 MERGE P/R、hard-conflict violation、最大 cluster、singleton；
- Package Pair P/R/F1、fragmentation、false-merge groups、N12/N13 各自增量贡献；
- 总与逐节点 input/output Token、aggregate latency、wall time；
- scheduler/component/coverage/replan 指标。

质量回滚线采用相对稳定 reference，而不是拿不同模型配置的 2026-08-02 运行硬比：

- candidate coverage 不能下降；
- Mention 结果原则上应等价；
- Field/N9/Package 任一 Precision 或 Recall 下降超过 1pp，停止对应并行阶段；
- 新增 known bad cluster、hard conflict violation 或 false-merge group，立即关闭对应 stage component；
- fragmentation 指标不得恶化；
- 不能靠 UNRESOLVED/CREATE_NEW 显著增加达标。

### 11.4 数百篇压力测试

30 篇通过后再做不发送真实模型的 recorded-response/fixture 压测，覆盖：

- 100、300、500 篇；
- 多 issuer/多事件的高并行图；
- 单 issuer/单 episode 的巨型 component；
- 10% 重复文档；
- 5% task failure 与随机中断恢复；
- 实时任务插入时的 scheduler 公平性；
- 峰值内存、Registry 大小、writer wait、documents/hour 与 P95 epoch finalize time。

不得把 fixture 压测的语义质量当作真实模型质量，只用于证明调度、恢复和资源边界。

## 12. 发布、回滚与复杂度控制

### 12.1 开关策略

只保留一个版本开关：

```text
CDECR_BULK_ORCHESTRATOR=legacy_defer | epoch_v1
```

`epoch_v1` 同时代表 P0+P1+P2 全部能力，不提供只启用 P0 或 P0+P1 的正式配置。开发测试可通过内部 fixture 直接调用各 layer，但不增加长期环境开关，避免形成组合状态。若完整验收失败，正式入口整体回到 `legacy_defer`，修复后再一次性启用完整 `epoch_v1`。

### 12.2 一次性交付的内部实现顺序

以下顺序用于降低开发时的定位难度，不代表分轮上线：

1. epoch schema、shared orchestrator、恢复与独立 N13 finalize；
2. Atomic Read/Decide/Apply facade、component planner、late-edge/version replan；
3. Package facade/component、完整 touched union 与 N13 pair finalize；
4. bulk 高并发 lane、provider 启动 gate、背压和指标；
5. P0/P1/P2 分层回归、完整 `epoch_v1` recorded-response 等价测试；
6. 一次完整 30 篇真实 A/B、数百篇压力测试、报告与最终交付。

实现可以使用若干便于审查的内部 commit，但本轮任务必须连续完成全部 P0-P2；不在 P0 或 P1 完成后停下来等待下一轮授权，也不把中间状态切到正式 bulk 默认值。代码、迁移和对应回归应同步落地，每个重要修改继续追加 changelog。

### 12.3 独立回滚

| 现象 | 动作 |
| --- | --- |
| P0 N13 coverage 增加但质量下降 | 不回到最后一篇 finalize；排查新增 pair 的候选/Apply，正确性修复不可用漏处理掩盖 |
| Atomic component 输出偏离 reference | 关闭 Atomic component，保留 epoch/checkpoint/dedicated finalize |
| Package component fragmentation/误并增加 | 关闭 Package component，保留 Atomic 并行和 touched union |
| late-edge/replan 比例过高 | 对该类 component 串行，不删除版本保护 |
| 墙钟无改善但质量等价 | 保持默认 incremental 或 epoch-serial；根据 component 分布决定是否继续，不硬切图 |
| provider P95/错误率上升 | 降 component worker，不缩候选、不放宽 fallback |
| Registry writer wait 成为瓶颈 | 合并短 Apply transaction/优化索引，不让 Decide 持锁 |

## 13. 预计代码改动面

优先修改既有模块，不建立平行领域引擎：

- `src/cdecr/cli.py`：统一 batch/evaluation 调用；
- `src/cdecr/cross_document.py`：拆出 stage facade、独立 N13 finalize，保留现有业务函数；
- `src/cdecr/registry.py`：两张 epoch 状态表、原子 claim/checkpoint API；
- `src/cdecr/scheduler.py`：component task admission，必要时增加 workload class；
- `src/cdecr/config.py`：一个长期 orchestrator 版本开关和临时 canary 参数；
- `tests/cdecr/test_cross_document.py`：完整性、等价、component、恢复；
- 可新增 `src/cdecr/bulk_orchestrator.py`：仅放 manifest/stage/component 调度，不放 Field/Atomic/Package 业务规则。

明确不改：

- `src/cdecr/prompts/v1/*`；
- N9/N12/N13 model-facing DTO 与 reason 协议；
- N12 dictionary 的生产开关；
- Mention、Atomic、Package 持久化身份模型；
- 当前复杂节点 batch 大小。

## 14. 最终决策

原第三轮方案的方向“按候选依赖组件并行，而非按文档强行并行”是正确的；真正落地不足不在并发参数，而在缺少 epoch 级状态和阶段边界。当前实现把 N13 defer 绑定到最后一篇文档，是最先需要修正的正确性问题。

本轮应一次完成 epoch、Atomic 和 Package 三层所需的 P0-P2，但仍不把风险收益比较弱的 Field component 化夹带进来。最平衡的执行路线是：

1. 在同一实现任务内先完成独立 epoch finalize、完整 touched union 和 checkpoint；
2. 紧接着接入 N9 component 并行；
3. 随后接入 N12 component 并行和完整 N13，三者共同组成唯一可交付的 `epoch_v1`；
4. Field 仅在成为新瓶颈且能证明 component 互斥时再做；
5. bulk 默认使用 8 个文档 worker、M1/M2/M3/M4 8/24/24/12 lane、N9/N12/N13 12/10/12 component workers，并以约 1 秒 provider 启动间隔平滑突发；
6. 全程不改 Prompt、不扩大复杂 batch、不降低候选覆盖，也不将单项错误扩大为整批失败。

这条路线引入的长期新复杂度仅是一个薄 orchestrator、两张 checkpoint 表和通用 expected-version Apply 合同；它们替代当前 CLI/evaluation 重复编排与隐式 defer 语义，而不是在旧流程旁堆叠另一套业务系统。
