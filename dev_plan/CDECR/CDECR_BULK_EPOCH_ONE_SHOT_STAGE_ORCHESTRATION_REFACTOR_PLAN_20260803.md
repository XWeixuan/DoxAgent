# CDECR BULK_EPOCH 一次性极致阶段化编排重构方案

> 日期：2026-08-03
> 性质：一次性替换式实施方案，不是审查、试验或渐进上线方案
> 适用场景：固定时间窗内几十至数百篇历史新闻的批量 CDECR 抽取
> 核心 SLO：固定 30 篇真实语料首轮完整运行小于 60 分钟
> 扩展目标：工作量随文档、Mention、Atomic、Package 数量近似线性增长，不再因逐文档扫描、状态交错和重复重算产生非线性放大
> 质量边界：不缩候选、不扩大复杂 LLM batch、不降低结构化输出合同、不以更多 `UNRESOLVED` / `CREATE_NEW` / `DIFFERENT` 换速度

## 0. 不可协商的执行原则

本方案追求一次性完成最终架构，不设置分阶段发布、shadow、canary、双写、旧路径回退或“先做一半观察”的空间。

正式落地必须同时完成：

1. 删除当前“文档 component 外套旧 `process(message_id)`”的 BULK_EPOCH 控制流；
2. 删除 N12 `anchor/window` 长持有 writer locks；
3. 一次接入全 epoch 的 Field、Atomic、Package、N13 `Read → Decide → Reduce → Commit`；
4. 一次接入真实异步模型执行器、固定高并发、批量只读快照和单 writer 批量提交；
5. 一次删除旧 BULK_EPOCH orchestration 代码、旧开关和旧测试假设；
6. 完成全量离线回归后，直接用固定 30 篇进行真实模型验收；
7. 验收失败时修复新架构，不恢复旧 BULK_EPOCH，不让系统自动降回逐文档串行。

这里需要明确区分两类“退避”：

- **禁止的退避**：切回旧工作流、切回逐文档串行、降低候选覆盖、将新协议改成 shadow、以保守 action 掩盖失败、根据质量结果只启用部分 stage、遇到 provider 压力自动永久降低既定并发。
- **必须保留的运行保障**：单次 HTTP 连接失败的有限重试、幂等 task replay、非法单条输出的 item repair、失败 task 局部隔离。这些不改变业务算法，也不构成旧架构退路；若把它们一并删除，偶发网络错误会直接扩大成整批失败，与成功率目标冲突。

新架构只有一条生产路径：

```text
BULK_EPOCH_V3_STAGE_GRAPH
```

不新增 `legacy/shadow/canary/on` 四态开关。原 BULK_EPOCH 在同一提交中被替换，而不是并存。

## 1. 真实问题、目标与事实边界

### 1.1 本轮已证实的关键事实

固定 30 篇正式运行中：

| 指标 | 真实值 |
| --- | ---: |
| 单文档 barrier | 18:28 |
| bulk components（N5.5-N12） | 2:47:05 |
| N13 finalize | 18:47 |
| 首轮总墙钟 | 3:24:22 |
| component 分布 | `20 + 10×1` |
| 20 篇主 component 墙钟 | 10,025 秒 |
| 主 component 模型 latency 合计 | 10,529 秒 |
| 主 component 有效并发系数 | 约 1.05 |
| 主 component M2 最大在途 | 5 / 配置 24 |
| 主 component M3 最大在途 | 2 / 配置 24 |
| 主 component N5.5 最大在途 | 1 |
| 主 component N12 最大在途 | 2 |

当前 component 并行并非完全无收益：11 个 component 若完全串行约需 4:04:00，真实并行耗时 2:47:05，节省约 1:16:54。但这部分收益全部来自 10 个 singleton 与 20 篇主 component 重叠；singleton 在前 13 分钟内结束后，剩余约 154 分钟只有主 component 一条长链。

### 1.2 根因不是 provider 并发上限

本轮没有 429 风暴或 provider pressure 降并发证据。M2/M3 的真实在途请求远低于配置上限。主要问题是编排器没有同时制造足够多的 ready tasks，而不是 provider 不允许并发。

### 1.3 当前复杂度风险

现有逐文档跨文本流程包含以下随历史头数量增长的操作：

- 每篇读取全部 current Atomic；
- 每篇检查或同步全部 Atomic embedding；
- 每篇读取全部 active Package；
- 每个 event 对不断增长的 Package 集合召回候选；
- N12 Apply 改变后续文档可见状态；
- 新碎片 Package 又扩大后续候选池与 payload；
- 组件中的任意一条候选边通过文档级传递闭包把整篇文档的全部节点锁成串行。

这使业务规模上升后不仅是任务数增加，还会出现重复扫描、候选池膨胀、顺序依赖和重算，存在事实上的 `O(N²)` 路径。

### 1.4 目标的严格定义

“1 小时内”定义为固定 30 篇、空白 Registry、完整 Dreamer 至 N13、真实模型、包含局部 repair、包含持久化的首轮墙钟：

```text
P50 < 50 分钟
P95 < 60 分钟
```

“文档增长不造成非线性上升”定义为：

```text
T(N) = 固定阶段开销 + O((D + F + K₁M + K₂A + K₃P) / C) + reducer
```

其中：

- `D`：文档数；
- `F`：去重后的 Field task 数；
- `M`：Mention 数；
- `A`：Atomic 数；
- `P`：Package proposal 数；
- `K₁/K₂/K₃`：固定候选上限；
- `C`：实际 provider 并发。

固定并发下，任意大 N 都不可能永远小于 1 小时；本方案承诺的是 30 篇 SLO 和规模增长时近似线性吞吐，而不是违反物理容量的常数时间。

## 2. 目标架构：全 epoch Read / Decide / Reduce / Commit

```text
Stage 0  Manifest + immutable base snapshot
   ↓
Stage 1  Document Map：Dreamer / Grounder / Judge
   ↓ barrier
Stage 2  Field Plan → Field Decide Pool → Field Reduce/Commit
   ↓ barrier
Stage 3  Atomic Plan → N9 Decide Pool → Atomic Constrained Reduce/Commit
   ↓ barrier
Stage 4  Package Plan → N12 Wave A/B Decide Pool → Package Reduce
   ↓
Stage 5  N13 Pair Plan → Decide Pool → Final Package Reduce/Commit
   ↓
Stage 6  Publish epoch heads + final audit summary
```

每个模型节点都拆成四个纯边界：

```text
Read/Plan：只读快照，生成完整有界 task
Decide：无持久化写入，可任意并行
Reduce：确定性聚合模型决定，执行领域约束
Commit：短事务批量写入，不等待模型
```

### 2.1 component 的新语义

component 不再是 worker 串行单元，只承担：

- reducer 的关系闭包边界；
- task 失败隔离范围；
- checkpoint 与审计范围；
- 内存分片范围。

即使所有 Mention 形成一个 giant Atomic component，component 内的 N9 pair/task Decide 仍可并行。只有最终 constrained reducer 对该 component 做一次内存聚类。

### 2.2 快照语义

epoch 启动时记录：

- source manifest 与 fingerprint；
- KB/catalog hash；
- Field registry snapshot version；
- Atomic root/version snapshot；
- Package root/version snapshot；
- Prompt/schema/model config；
-索引版本。

模型任务只能读取该 stage 的 immutable snapshot 和上一个 stage 已提交的 epoch overlay，不读取其他正在执行 task 的中间写入。

### 2.3 overlay 而非边跑边改全局 head

Field、Atomic、Package 的新对象先写入 epoch-local overlay：

```text
base snapshot + epoch overlay = 当前 stage 的完整只读视图
```

只有该 stage reducer 成功后才发布 overlay。这样既允许批内对象互相成为候选，也不会让模型请求看到随调度顺序变化的世界。

## 3. 运行时与持久化合同

### 3.1 Epoch 状态机

只保留一条正向状态机：

```text
CREATED
→ DOCUMENT_MAP
→ FIELD_DECIDE
→ FIELD_COMMITTED
→ ATOMIC_DECIDE
→ ATOMIC_COMMITTED
→ PACKAGE_DECIDE
→ PACKAGE_REDUCED
→ N13_DECIDE
→ PACKAGE_COMMITTED
→ FINALIZED
```

失败状态只记录位置，不触发算法降级：

```text
PARTIAL(stage, failed_task_ids)
```

恢复时重放未完成 task，不能切换到旧串行路径。

### 3.2 Task ledger

新增统一 `bulk_epoch_tasks`，不为每个节点堆一套表：

| 字段 | 含义 |
| --- | --- |
| `epoch_id` | epoch 标识 |
| `stage` | FIELD/N9/N12_A/N12_B/N13 |
| `task_id` | 内容寻址稳定 ID |
| `component_id` | reducer/隔离分组 |
| `input_hash` | Prompt 可见 payload hash |
| `snapshot_hash` | 决策读取的快照 |
| `status` | PENDING/RUNNING/SUCCEEDED/FAILED |
| `attempt_count` | 传输/单条 repair 次数 |
| `decision_ref` | 结构化结果引用 |
| `error_code` | 局部错误 |
| `started_at/finished_at` | 墙钟与恢复 |

模型完整 payload 不重复写三份；使用内容寻址 blob 或现有 model-call input hash。Task ledger 只保留恢复所需元数据。

### 3.3 Stage artifacts

每个 stage 只产生一个版本化 artifact：

- `field_plan_v1` / `field_overlay_v1`；
- `atomic_plan_v1` / `atomic_partition_v1`；
- `package_plan_v1` / `package_partition_v1`；
- `n13_pair_plan_v1` / `final_package_partition_v1`。

artifact 由 `manifest_hash + upstream_artifact_hash + engine_version` 唯一确定，保证断点恢复不重复消耗 Token。

### 3.4 单 writer actor

SQLite 继续使用 WAL，但所有写入通过一个 writer actor：

- LLM worker 不直接持有写事务；
- worker 只提交 decision/result message；
- reducer 汇总后批量写入；
- 每个 stage 最多若干短事务；
- 写入期间没有网络等待；
- 读连接使用独立 read-only connection pool。

这不是全流程串行：串行的是毫秒至秒级 Commit，不是分钟级 LLM Decide。

## 4. Stage 1：单文档 Map 一次性铺满

### 4.1 调度

- document workers 固定为 24；
- 30 篇除 exact duplicate 外全部立即进入 ready queue；
- exact duplicate 只运行 representative，其他文档引用结果；
- Dreamer、Grounder、Judge 保留现有文档内依赖；
- Grounder item repair、missing recovery、Judge coverage repair 进入独立 repair queue；
- repair 不占住原 document worker，不阻塞其他文档继续完成；
- 单文档结果完成即持久化，但跨文档阶段只在全体 representative 到达 barrier 后开始。

### 4.2 失败语义

- 单条 candidate 非法：只 repair 该 candidate；
- 单个 repair 仍非法：保留同文档其他合法 Mention；
- 单篇完全没有合法 Mention：该篇标 `SUCCEEDED_EMPTY`，不阻塞 epoch；
- HTTP/transport 失败：同请求内容寻址重试最多 2 次；
- schema 业务非法不做整篇重试；
- 不切换模型、不切换 Prompt、不回退到旧 processor。

### 4.3 预算

本轮 Grounder P95 约 256 秒、Judge P95 约 133 秒。24 个 document workers 下，阶段墙钟由最慢文档链而不是30篇总和决定：

```text
目标 10–15 分钟
硬上限 15 分钟
```

## 5. Stage 2：全 epoch N5.5 Field Plan / Decide / Reduce

### 5.1 全量 occurrence inventory

一次遍历所有 Mention，提取：

- participant；
- predicate；
- metric；
- fiscal period；
- schema projection field；
- routed open attribute；
- package anchor/artifact hint。

每个 occurrence 记录 `mention_id + field_path`，但模型任务按语义键去重。

### 5.2 全局 Field task 去重

Field task key：

```text
namespace
+ normalized_raw_value
+ issuer_scope
+ participant_role
+ period_context
+ candidate_external_ids
+ applicable_hard_dimensions
```

同键 occurrence 只调用一次模型，结果 fan-out 到所有 `mention_id + field_path`。候选或上下文不同则保持独立，不能只按字符串粗暴合并。

### 5.3 召回与索引

- exact KB lookup 全量批处理；
- participant catalog 使用现有 batch prime 扩展到全 epoch；
- string/vector recall 使用 namespace 分区索引；
- 每 task 最多 8 candidates；
- safe deterministic match 继续直接 Resolve；
- 只有真正需要语义消歧的 task 进入 M2。

### 5.4 LLM payload 与 batch

Field task 结构简单，生产批大小固定 12；同一请求中的 task 独立，输出必须逐 task 恰好一次。

```json
{
  "fields": {"f1": {...}, "f2": {...}},
  "candidates": {"k1": {...}},
  "tasks": [
    {"id": "t1", "field": "f1", "candidates": ["k1"]}
  ]
}
```

Prompt 只需增加一句批处理覆盖约束，不增加 reasoning：

> Return exactly one decision for every task ID. Tasks are independent; never omit, duplicate, or combine tasks.

非法 task 只发送该 task 的 compact repair payload；其他合法结果立即进入 reducer。

### 5.5 Field reducer

Reducer按完整 task key及决定生成：

- LINK existing registry；
- NEW canonical entry；
- UNRESOLVED canonicalized entry；
- occurrence fan-out links。

同一 NEW/UNRESOLVED task 只创建一个稳定 registry ID。Field overlay 一次提交后成为 N9 的唯一 Field视图。

### 5.6 性能目标

当前 Field 模型 latency 合计约 1,098 秒，召回 embedding 约 112 秒。即使不考虑全局去重，24 并发理论容量低于 1 分钟；考虑 launch、尾延迟和 Commit：

```text
目标 1–2 分钟
硬上限 3 分钟
```

## 6. Stage 3：全 epoch Atomic Plan / N9 Decide / Constrained Reduce

### 6.1 Atomic base snapshot

从 Registry 一次读取并冻结：

- canonical root Atomic；
- current version；
- Compact Identity Card；
- Sidecar 三轴与 hard conflict dimensions；
-代表 Mention/Evidence；
- embedding；
- issuer/family/period/metric/anchor 倒排键。

不再每篇 `list_current_atomic_events(limit=10000)`，不再每篇扫描和同步全部 embedding。

### 6.2 Incoming provisional Atomic

每个 eligible Mention 先生成稳定 provisional singleton：

```text
provisional_atomic_id = hash(epoch_id, mention_id, identity_hash)
```

该对象只供候选召回和 N9 Decide 使用，不提前写成正式 Atomic。

### 6.3 双索引候选召回

每个 Mention 同时召回：

1. base snapshot 中的历史 Atomic；
2. 本 epoch 其他 provisional Atomic。

候选来源：

- deterministic identity keys；
- issuer/family/assertion bucket；
- period/date/metric倒排；
- canonical participant/object；
- ANN embedding Top-K；
-已知 parent/source report boundary；
- deterministic hard cannot-link filter。

每个 Mention 对模型可见候选仍保持现有 `ATOMIC_TOP_K=5`。调度前可维护最多 12 个近邻用于 component/coverage 检查，但不全部进入 LLM payload。

### 6.4 N9 task 不再按文档分批

所有 N9 task 进入一个全局 priority queue：

1. deterministic SAME/NOT_SAME 不调用 LLM；
2. 有历史 Atomic candidate 的 task；
3. epoch provisional candidate task；
4. escalation task；
5. item repair。

基础 N9 batch 保持 3 个 Mention task，避免复杂判断 batch 膨胀。并发来自请求数量，不来自扩大单请求。

### 6.5 giant component 仍并行 Decide

candidate graph 的 connected component 只决定 reducer 范围。一个 200-Mention giant component 的几十个 N9请求仍同时提交；不会出现“component内 `for message_id`”。

### 6.6 N9结果协议

保留当前 relation、axis、reason协议。每条 candidate assessment 独立合法化：

- 可确定性规范化的 enum/short ID 直接修正；
- 单个 assessment 非法只 repair 该 assessment；
- repair 仍非法则该 pair 标 `UNJUDGEABLE_FAILED`，不能扩大成整个 Mention `CREATE_NEW`；
- 如果仍有合法 SAME_EVENT，Mention继续进入merge reducer；
- 没有合法 SAME_EVENT才按合法 RELATED/NOT_SAME结果处理；
- 不允许用整 task `CREATE_NEW` 作为并行失败的通用退路。

### 6.7 Atomic constrained reducer

Reducer输入为完整候选边和全部合法决定，处理顺序固定：

1. deterministic SAME；
2. 模型 SAME_EVENT；
3. singleton absorption；
4. possible duplicate审计；
5. hard cannot-link冲突裁决；
6.生成canonical Atomic partition。

约束：

- 没有 SAME edge 不合并；
- hard conflict 永远优先；
- 禁止仅凭transitive闭包跨越明确 NOT_SAME；
- multi-member cluster之间只有显式可判边才能联合；
- reducer不创造模型未判断的新语义关系；
-选定root使用稳定排序，不依赖任务完成顺序。

### 6.8 一次固定 convergence

Reducer后做一次、且只能一次 coverage检查：

- 新cluster representative 是否进入其他task原本Top-K之外但调度Top-12之内；
- 新root是否暴露高置信identity edge；
- 是否存在未判断的deterministic SAME。

只补这些明确边。第二次 reducer 后结束，不允许循环replan，也不允许切回逐文档流程。

### 6.9 性能目标

当前 N9、escalation、repair和embedding latency 合计约 4,965 秒。24并发平均约 207 秒，但 escalation P95 约403秒，故由尾请求主导：

```text
目标 6–8 分钟
硬上限 10 分钟
```

## 7. Stage 4：全 epoch Package Plan / N12 两波 Decide / Package Reduce

### 7.1 先删除旧 N12 状态交错

正式路径删除：

- `_bulk_package_assignment_locks()`；
- `anchor:*` / `window:*` writer lock map；
- 在 `_assign_packages_v13()` 内边读 current Package 边调用模型边 Apply；
- component 之间交错修改 Package head；
-持锁等待 embedding、candidate recall或LLM；
-后文档读取前文档临时 Package 状态。

N12开始前，Atomic partition必须完整且不可变。

### 7.2 N11一次性物化全部 Package Seed

对全部 Atomic 并行编译、统一汇总：

-完整 `anchor_ids`；
- `primary_anchor_id`；
- `anchor_conflict`；
- canonical artifact identity；
- relation_to_anchor；
- issuer；
- Package family/kind；
- fiscal period/date window；
- source fingerprint/evidence block；
- member identity摘要；
-代表 Atomic卡片。

同一 Atomic/Package card 只编译一次并按版本缓存。Package candidate payload使用稳定短ID，但不切换为此前质量未通过的压缩dictionary协议；保留当前正式业务信息量。

### 7.3 Package base snapshot

一次读取历史 active Package，并预构建：

- root/version；
- canonical anchors；
- artifact；
- issuer/family/period倒排；
-成员代表卡；
- embedding；
- hard boundary dimensions。

不再为每个文档重新读取和同步全部 Package。

### 7.4 N12 Wave A：加入历史 Package

每个 Atomic 对同一冻结 base snapshot召回最多6个候选。模型判断：

```text
MEMBER existing package
EXTERNAL_RELATED
NOT_RELATED
```

Wave A只决定是否进入历史Package，不在模型调用期间创建任何新Package。

若多个历史Package均为MEMBER，保留完整ranked member列表，由reducer按：

1. canonical artifact/anchor；
2. incumbent membership；
3. parent evidence强度；
4.模型排序；
5.稳定Package ID；

选择目标。不同worker完成顺序不参与选择。

### 7.5 N12 Wave B：批内新 Package 聚类

Wave A没有加入历史Package的Atomic，不立即逐个 `CREATE_NEW_PACKAGE`。每个Atomic先形成 provisional package seed：

```text
provisional_package_id = hash(epoch_id, atomic_id, package_seed_hash)
```

然后在这些seed之间构造有界候选图：

-相同 canonical artifact；
-共享可信 canonical parent anchor；
-同 source fingerprint + evidence block parent；
-issuer/family/period兼容；
-代表 Atomic embedding近邻；
-reaction/external boundary；
-hard Package conflict预过滤。

只使用相似 raw hint、同ticker、宽泛主题或“latest report”不能单独形成MEMBER边。

Wave B继续使用现有 N12 membership业务合同判断 provisional seed之间的归属关系。所有pair/task并行，之后由Package reducer统一聚类。

### 7.6 两波是固定算法，不是渐进试运行

Wave A/B均属于唯一生产路径：

-不是canary；
-不是失败后才启用；
-不由配置关闭；
-不根据语料规模切回旧逻辑；
-Wave B结束后不再启动第三波或无限收敛。

两波的目的是消除顺序依赖：先处理稳定历史容器，再处理本批新容器。

### 7.7 N12 payload与批大小

- batch固定最多12个Atomic task；
-每task最多6个Package candidate；
- candidate card只出现一次，task引用短ID；
-保留必要代表Atomic、anchor、artifact、family和period；
-不增加新的LLM reasoning字段；
-Prompt说明任务间独立、每task恰好一次；
-单task非法只repair该task。

Prompt需补充的最小批处理约束：

> Decide every task independently from the supplied immutable Package candidates. Return every task ID exactly once. Do not assume that another task has already created or modified a Package.

Wave B补充一句：

> Provisional Package seeds are candidate parent containers for this epoch; judge membership by the same parent-boundary rules as existing Packages.

不向模型解释epoch调度、worker、checkpoint或并发概念。

### 7.8 Package reducer

Reducer生成一个完整Package partition，约束为：

-历史Package只接收明确MEMBER；
-批内seed之间只有明确MEMBER边才聚合；
-共享canonical anchor是强证据但不是无条件自动merge；
-不同可信artifact anchor形成窄边界；
-reaction一般只能EXTERNAL_RELATED，除非现有业务合同明确允许MEMBER；
-hard Package boundary优先于模型MEMBER；
-模型明确NOT_RELATED的边不能被传递闭包跨越；
-root与primary anchor选择使用稳定排序；
-多anchor冲突保留完整集合和conflict标志，不清空证据；
-reducer不读取或修改全局Package head。

### 7.9 性能与质量预期

当前N12 aggregate latency约6,368秒，37次调用中35次为M3复杂任务；P50约190秒，P95约317秒。

24并发容量：

```text
6,368 / 24 ≈ 265 秒
```

考虑Wave B额外判断、最长请求和Commit：

```text
目标 6–8 分钟
硬上限 10 分钟
```

质量上，所有Atomic看到同一Package世界，直接消除本轮：

```text
交错读取 → 多次CREATE_NEW → Package增多
→ 后续候选膨胀 → 更多复杂M3请求
→ 进一步碎片化
```

的反馈回路。

## 8. Stage 5：N13 全pair并行 Decide / 一次性 Final Reduce

### 8.1 输入

N13只读取：

-历史Package base snapshot；
-N12 reducer生成的完整Package proposals；
-最终N12 memberships；
-anchor/artifact/family/period/profile；
-N12 external relations。

不读取逐task中间Package。

### 8.2 Pair plan

一次生成全epoch候选pair：

-无序pair ID去重；
-相同profile pair只判断一次；
-redirect/root先规范化；
-deterministic SAME/DIFFERENT先执行；
-hard boundary pair不发送模型；
-剩余pair按固定Top-K/route生成有界图；
-pair数必须满足 `E₁₃ ≤ K₃ × P`。

### 8.3 全量并行 Decide

- batch最多12 pairs；
-全部batch一次进入N13 ready queue；
-N13 worker cap为24；
-不再按wave读取前一wave Apply结果；
-单pair非法只repair该pair；
-没有整batch `DIFFERENT_PACKAGE` fallback；
-失败pair记录为`UNJUDGEABLE_FAILED`，其他合法pair继续reduce。

### 8.4 Final Package reducer

集中处理：

1. deterministic SAME；
2.模型 SAME_PACKAGE；
3. hard boundary；
4.弱SAME不Apply审查；
5.稳定root选择；
6. memberships、anchors、external relations并集；
7.重建最终Package profile；
8.生成一次性merge plan。

禁止边判断完成后立即修改Package；所有merge在完整关系图上统一决定。

### 8.5 性能目标

当前33次N13调用aggregate latency约1,932秒，P95约170秒：

```text
目标 3–4 分钟
硬上限 5 分钟
```

完整coverage必须保留，不能恢复上一轮只有4次调用的漏覆盖状态。

## 9. 真正异步的模型执行器

### 9.1 不再用嵌套ThreadPool表达节点并发

新增单一 `AsyncModelExecutor`：

- `asyncio.TaskGroup` 管理task；
- `httpx.AsyncClient` 或provider原生异步client；
-连接池至少100 keep-alive连接；
-per-tier semaphore；
-per-stage active cap；
-priority queue区分normal和item repair；
-所有调度等待显式计入telemetry。

同步模型client不能用无限 `to_thread` 包装长期保留；正式实现必须提供async transport，避免线程、连接池和SQLite线程上下文互相阻塞。

### 9.2 固定激进并发

| 资源 | 固定配置 |
| --- | ---: |
| document workers | 24 |
| M1 physical lane | 32 |
| M2 physical lane | 48 |
| M3 physical lane | 48 |
| M4 physical lane | 16 |
| Field active | 32 |
| N9 base active | 24 |
| N9 escalation active | 16 |
| N12 active | 24 |
| N13 active | 24 |
| item repair active | 8 |

physical lane高于业务stage cap，用于允许stage交界、repair和telemetry请求共存，不代表一个复杂节点会同时发48个请求。

### 9.3 删除全局1秒启动门

删除当前所有structured tier共享的全局1秒start gate。改为per-tier固定token bucket：

| Tier | start rate | burst |
| --- | ---: | ---: |
| M2 | 16 req/s | 24 |
| M3 | 10 req/s | 16 |
| M4 | 6 req/s | 8 |

不做自动减半、动态降为串行或持久退让。若provider明确返回限流：

-该请求按`Retry-After`或固定短间隔最多重试2次；
-并发配置本身不自动修改；
-重试失败只标该task失败；
-epoch继续处理其他task；
-验收中任何持续限流意味着固定配置或provider容量假设不成立，必须修复后重新验收，不能静默降速宣称成功。

### 9.4 公平性与避免头阻塞

-长M3任务不占用M2 semaphore；
-repair使用独立小队列，不能抢光normal capacity；
-同一stage使用shortest-ready-first不是按payload预测模型语义，只用于优先提交已准备task；
-一个giant component不能独占executor；component轮转提交task；
-每个请求timeout仍为600秒；超时后局部失败，不阻塞TaskGroup收尾。

## 10. 索引、缓存与线性复杂度保证

### 10.1 禁止逐task全表扫描

正式代码不得在Field/N9/N12/N13 task循环中调用：

- `list_current_atomic_events(limit=10000)`；
- `list_current_packages(limit=10000)`；
-全量embedding同步；
-对全部历史head逐一Python打分；
-每个文档重新构建相同Package/Atomic card。

这些数据只能在stage Plan阶段读取一次。

### 10.2 倒排索引

Atomic索引至少包括：

```text
issuer
event_family
assertion_state
primary_metric / metric_family
fiscal_period / date_bucket
complete_referent
artifact/report
canonical anchor
```

Package索引至少包括：

```text
issuer
package_family/kind
canonical anchor
artifact
period/date window
source fingerprint
member identity keys
```

索引查询先产生有界调度池，再由embedding/业务ranker选模型Top-K。

### 10.3 ANN与向量缓存

- embedding按 `owner_id + owner_version + model` 内容寻址；
-只计算新增或版本变化对象；
-base snapshot加载现有向量索引，不重复同步；
-epoch overlay使用临时ANN shard；
-查询合并base Top-K和overlay Top-K；
-stage结束后批量持久化新增向量。

可使用内存HNSW/FAISS或已有外部向量后端，但不能继续在Python中对不断增长的全集逐项比较。

### 10.4 Card缓存

缓存键：

```text
AtomicCard = atomic_id + version + card_schema_version
PackageCard = package_id + version + card_schema_version
```

同一card在一个epoch payload中使用短ID引用，序列化一次。缓存只减少重复工作，不改变模型可见内容。

### 10.5 固定边数

必须在代码中设置并审计：

```text
Atomic scheduler pool ≤ 12 / Mention
Atomic model candidates ≤ 5 / Mention
Package model candidates ≤ 6 / Atomic
N13 scheduler pairs ≤ K₃ × Package
Convergence waves = 1
N12 waves = 2
```

任何边数超界应在Plan阶段确定性截断到rank最高的边，而不是生成后再靠timeout结束。

### 10.6 复杂度门槛

对合成30/60/120/240文档数据运行无模型planner benchmark，必须证明：

-Plan CPU增长近似线性；
-candidate边数不出现平方增长；
-payload总字节数与task数近似线性；
-内存峰值可按component流式加载；
-不存在随文档数增长的重复全表读取。

门槛：

```text
T_plan(2N) / T_plan(N) ≤ 2.3
E(2N) / E(N) ≤ 2.2
PayloadBytes(2N) / PayloadBytes(N) ≤ 2.2
```

## 11. Prompt、Schema与业务合同对齐

### 11.1 不重新设计业务Prompt

本重构改变的是task来源和调度时序，不改变：

-Mention定义；
-Field LINK/NEW/UNRESOLVED语义；
-Atomic SAME/RELATED/NOT_SAME语义；
-Sidecar与hard conflict；
-Package MEMBER/EXTERNAL/NOT_RELATED语义；
-N13 SAME/DIFFERENT/UNCERTAIN语义；
-reason协议。

### 11.2 只补不可变快照与覆盖约束

新Prompt增量只能表达模型确实需要知道的两件事：

1.任务彼此独立且必须逐项恰好返回一次；
2.候选是当前请求的完整可选集合，不能假设其他task已经修改持久状态。

不得把以下内容写进Prompt：

-epoch ID；
-component ID；
-worker数量；
-并发策略；
-checkpoint；
-reducer算法；
-snapshot version。

### 11.3 Schema

-批处理root必须有明确tasks/decisions覆盖关系；
-ID使用请求内短ID；
-每个decision保持现有reason字段；
-不增加长审计reasoning；
-validator按item处理非法项；
-root完全不可解析时才对该请求所有task逐项标记失败并进入item repair；
-不得以整请求默认CREATE_NEW/DIFFERENT替代合法判断。

## 12. 失败、恢复与“不回退”语义

### 12.1 允许的局部恢复

| 故障 | 新路径行为 |
| --- | --- |
| HTTP连接中断 | 相同task ID有限重试 |
| 单task Schema非法 | compact item repair |
| 单pair非法 | 只repair该pair |
| 单task最终失败 | 标FAILED，其他task继续 |
| writer进程中断 | 从已持久decision重新执行Reducer/Commit |
|进程重启 | 读取task ledger，只补未完成task |
|版本冲突 | epoch最终Commit前重新校验一次并仅重算受影响外部边 |

### 12.2 明确禁止的fallback

-整文档重新Grounder；
-整N9 task无条件CREATE_NEW；
-整N12 batch无条件CREATE_NEW_PACKAGE；
-整N13 batch无条件DIFFERENT_PACKAGE；
-切换到旧逐文档process；
-降低候选Top-K；
-关闭Wave B；
-关闭N13全coverage；
-自动降低固定并发后继续把慢运行判为达标；
-provider失败时切换另一个模型并混入正式A/B。

### 12.3 Commit冲突

批量历史epoch运行期间，实时增量新闻可继续写live heads。最终Commit执行：

1.校验base root/version；
2.没有变化则直接发布overlay；
3.若存在实时增量变化，只为受影响root重读最新head；
4.使用已经完成的pair/identity结果做一次确定性rebase；
5.只有出现新增、此前未判断的候选边时，对这些边发起一次补充Decide；
6.完成后Commit；
7.不切回旧工作流，不重跑整个epoch。

该rebase最多一次。仍冲突则相关root标`COMMIT_CONFLICT`并隔离，其他结果照常发布。

## 13. 代码重构边界

### 13.1 新模块

建议新增：

```text
src/cdecr/bulk_epoch/
├─ engine.py              # 唯一BULK_EPOCH入口与状态机
├─ artifacts.py           # stage artifacts与hash
├─ task_ledger.py         # task状态与恢复
├─ executor.py            # async provider执行器
├─ snapshots.py           # base/overlay immutable view
├─ field_stage.py          # Field Plan/Reduce
├─ atomic_stage.py         # Atomic Plan/Reduce
├─ package_stage.py        # N12 Wave A/B Plan/Reduce
├─ n13_stage.py            # pair Plan/Reduce
├─ indexes.py              # 倒排/ANN adapter
└─ writer.py               # 单writer批量Commit
```

### 13.2 从原CrossDocumentEngine抽出的无状态业务函数

必须把以下能力从“读取Registry并立即写回”的方法拆开：

- Field occurrence extraction；
- Field candidate recall；
- Identity compile；
- Atomic candidate rank；
- N9 request build/validate；
- Atomic hard conflict/reducer；
- Package seed compile；
- Package candidate rank；
- N12 request build/validate；
- N13 pair candidate/build/validate；
-Package profile rebuild。

增量模式也调用这些纯函数，但保留自己的单文档控制流。

### 13.3 删除项

同一实施提交中删除：

-当前 `process_batch()` 中的 component worker包装；
-component内 `for message_id: process()`；
- `_bulk_package_assignment_locks()`；
- `_bulk_package_lock_guard` / `_bulk_package_locks`；
-旧 `bulk_atomic_component_workers` / `bulk_package_component_workers` 配置；
-旧 `BULK_ORCHESTRATOR_VERSION`；
-旧BULK_EPOCH feature flag分支；
-将最后一篇文档当finalizer的任何残留逻辑；
-仅验证defer顺序的旧测试；
-对新编排无意义的shadow/canary orchestration配置。

保留：

-epoch manifest/checkpoint理念；
-最终touched Package coverage；
-现有Prompt与领域DTO；
-item级repair；
-decision audit；
-incremental模式业务入口。

### 13.4 CLI

`--execution-mode BULK_EPOCH`直接实例化新 `BulkEpochEngine`。不能根据异常或配置再进入旧 `CrossDocumentEngine.process_batch()`。

正式配置只保留容量参数，不保留算法开关：

```text
CDECR_DOCUMENT_CONCURRENCY=24
CDECR_SCHEDULER_M1_CONCURRENCY=32
CDECR_SCHEDULER_M2_CONCURRENCY=48
CDECR_SCHEDULER_M3_CONCURRENCY=48
CDECR_SCHEDULER_M4_CONCURRENCY=16
CDECR_FIELD_ACTIVE_REQUESTS=32
CDECR_N9_ACTIVE_REQUESTS=24
CDECR_N12_ACTIVE_REQUESTS=24
CDECR_N13_ACTIVE_REQUESTS=24
```

不提供 `CDECR_BULK_V2_ENABLED=false` 一类逃生开关。

## 14. 一次性交付的实施顺序

以下是开发依赖顺序，不是发布阶段。所有工作必须在同一个交付任务内完成，最终只启用完整新路径。

### 工作包 A：纯业务函数与artifact合同

1.定义immutable snapshot、Field/Atomic/Package/N13 plan DTO；
2.从现有engine抽出无状态candidate/request/validator/reducer函数；
3.保证incremental调用这些函数后现有行为不变；
4.定义内容寻址task/artifact ID；
5.新增task ledger和overlay schema。

### 工作包 B：异步执行器与writer

1.实现provider async transport；
2.实现per-tier semaphore/token bucket；
3.实现normal/repair priority queue；
4.实现显式queue/start/model/parse/commit telemetry；
5.实现单writer actor与批量事务；
6.实现任务幂等恢复。

### 工作包 C：Field stage

1.全epoch occurrence inventory；
2.语义键去重；
3.批量召回；
4.Field Decide pool；
5.item repair；
6.Field reducer/overlay commit。

### 工作包 D：Atomic stage

1.base snapshot/index；
2.provisional Atomic；
3.双索引召回；
4.全局N9 task queue；
5.constrained reducer；
6.一次convergence；
7.Atomic overlay commit。

### 工作包 E：Package/N13 stage

1.N11全量seed；
2.Package base snapshot/index；
3.N12 Wave A；
4.N12 Wave B；
5.Package reducer；
6.N13全pair plan/Decide；
7.final reducer和一次性Package commit。

### 工作包 F：入口切换与旧代码删除

1.CLI直接接新engine；
2.删除旧BULK_EPOCH分支与locks；
3.删除无效配置和测试；
4.升级engine/schema/orchestrator版本；
5.迁移changelog和运维文档；
6.保证仓库中不存在可被启用的旧bulk代码路径。

所有工作包完成后才允许运行真实30篇。不得在A-D完成但E/F未完成时把半成品称为落地。

## 15. 测试体系

### 15.1 纯函数与Reducer测试

-相同输入不同task完成顺序，Field links完全一致；
-相同N9 decisions乱序输入，Atomic partition完全一致；
-相同N12 decisions乱序输入，Package partition完全一致；
-相同N13 pairs乱序输入，最终redirect/membership完全一致；
-hard conflict不会被transitive SAME跨越；
-模型没有给SAME/MEMBER的边，reducer不会自行合并；
-provisional ID稳定；
-Wave A/B root选择稳定；
-多anchor conflict不丢失anchor集合。

### 15.2 并发正确性测试

-30个task随机sleep后结果与固定顺序一致；
-一个task失败不取消TaskGroup中其他task；
-repair queue不阻塞normal queue；
-writer事务期间不包含网络await；
-giant component内至少12个N9/N12任务可同时in-flight；
-同一task重复提交只产生一次业务decision；
-进程在每个barrier前后崩溃，恢复不重复模型调用；
-实时增量写入与bulk Commit冲突时只rebase受影响root。

### 15.3 复杂度测试

构造30/60/120/240篇、单issuer giant component与多issuer分散component两套语料：

-记录planner CPU、edge数、payload bytes、Registry read count、内存峰值；
-禁止每个task全表扫描；
-证明倍增比满足第10.6节门槛；
-证明giant component不会将LLM并发降为1。

### 15.4 Recorded-response等价测试

固定旧Prompt响应，比较：

-Field candidate/decision集合；
-N9可见候选和assessment；
-N12可见历史候选；
-N13 pair coverage；
-最终Atomic/Package memberships。

因新架构新增batch内provisional候选，最终partition不要求机械等于旧顺序结果；但所有差异必须能归因为新增批内候选或消除陈旧快照，不能来自task遗漏、ID错位或默认fallback。

### 15.5 无旧路径证明

测试和静态检查必须证明：

-BULK_EPOCH入口只指向新engine；
-没有运行时配置能选择旧bulk；
-没有shadow/canary双调用；
-没有异常后调用旧`process_batch()`；
-旧writer lock符号不存在；
-旧最后一篇finalizer语义不存在。

## 16. 真实性能预算与达标证明

### 16.1 使用本轮真实工作量的容量估算

下表不假设Prompt删减、Token下降或模型变快，只把已经发生的aggregate model work放入可用并发：

| Stage | 本轮aggregate/尾延迟 | 新并发 | 容量下界 | 墙钟硬预算 |
| --- | ---: | ---: | ---: | ---: |
| Document Map | 当前18:28，受8 workers限制 | 24 docs | 最慢文档链约8–12m | 15m |
| N5.5 | 模型约1,098s | 24–32 | 35–46s + P95 | 3m |
| N9 | 约4,965s；escalation P95≈403s | 24 | 平均207s，尾部约403s | 10m |
| N12 | 约6,368s；P95≈317s | 24 | 平均265s，尾部约317s | 10m |
| N13 | 约1,932s；P95≈170s | 24 | 平均81s，尾部约170s | 5m |
| Reduce/Commit/rebase reserve | 当前未独立 | 单writer+CPU | — | 7m |

总硬预算：

```text
15 + 3 + 10 + 10 + 5 + 7 = 50 分钟
```

剩余10分钟作为provider波动、item repair和最长尾请求缓冲。因此30篇小于60分钟具有当前实测容量依据，不依赖乐观Token估算。

### 16.2 关键前提

该证明成立必须同时满足：

1. N9/N12 ready queue可跨文档提交；
2. giant component不再串行Decide；
3. M2/M3在ready queue非空时实际active达到至少12，目标18以上；
4. N12不再持锁等待模型；
5.全局1秒start gate删除；
6.不存在逐task全表扫描；
7.没有provider持续限流。

任一前提未实现，就不能以“代码已经异步”宣称达标。

### 16.3 线性扩展证明

设平均每篇Mention数稳定，则：

```text
F = O(D)
M = O(D)
A ≤ M
P ≤ A
N9 edges ≤ K₁M
N12 edges ≤ K₂A
N13 edges ≤ K₃P
```

K固定、wave固定、全表读取固定一次，因此总任务和payload均为O(D)。Provider并发固定后墙钟为O(D/C)，不会出现逐文档扫描与候选池反馈造成的O(D²)。

### 16.4 规模验收

除固定30篇质量验收外，复制/扰动构造60与120篇性能语料，只测吞吐与复杂度，不作为质量Gold：

| 规模 | 墙钟增长门槛 |
| --- | ---: |
| 30 → 60 | ≤2.2× |
| 30 → 120 | ≤4.5× |

多issuer和单issuer giant component都必须满足；不能只用天然易并行语料证明扩展性。

## 17. 真实30篇最终验收

### 17.1 成功率

- document 30/30完成；
-跨文档30/30完成或明确局部task失败但文档产物保留；
-不存在整文档因单条Field/N9/N12/N13非法而失败；
-幂等重跑新增模型调用、Token、Mention、Atomic、Package均为0。

### 17.2 性能

| 指标 | 门槛 |
| --- | ---: |
| 首轮完整墙钟 | `<60m` |
| Document Map | `<15m` |
| N5.5 | `<3m` |
| N9 | `<10m` |
| N12 | `<10m` |
| N13 | `<5m` |
| ready时M2 active P50 | `≥12` |
| ready时M3 active P50 | `≥12` |
| giant component有效model并发系数 | `≥8` |
|隐藏未计时start-gate wait | `0` |

### 17.3 业务质量

继续使用现有Gold与同一评估模型、Prompt、Schema和评分公式：

-Mention、Evidence、Field不能因本重构下降超过1pp；
-candidate coverage必须100%；
-N9 MERGE Precision不低于当前100%评估值的统计容忍边界；
-N9 conditional MERGE Recall至少恢复并超过85%；
-Package Pair Precision `>90%`；
-Package Pair Recall `>80%`；
-Package碎片化至少恢复到上一轮：fragmented groups≤2、excess components≤3；
-Micron财报组不能再从3个Package恶化为13个；
-N13完整coverage，不以少调用掩盖漏判。

### 17.4 Token

编排重构的硬目标是墙钟和复杂度，不虚报必然Token收益。但全局去重、card缓存和消除重算应满足：

-总Input Token不高于本轮3,772,039；
-Field重复task Input至少下降50%；
-N9维持当前优化后的量级；
-N12不因provisional Wave B使总Input增加超过10%；
-N13相同profile pair不重复调用。

若质量达标但Token略有增长，仍以1小时SLO和线性复杂度优先；不得通过删除业务信息强行压Token。

## 18. 最终判定与执行要求

本方案不是在当前BULK_EPOCH上继续加锁、加component或调高worker，而是更换调度原子：

```text
旧：文档/component是调度原子，整条N5.5-N12顺序执行
新：Field/N9/N12/N13 task是调度原子，Decide并行，Reducer集中，Commit短串行
```

一次性落地的最终含义：

-没有旧BULK_EPOCH可回退；
-没有shadow或canary协议；
-没有只启用Field/N9而暂缓N12的半成品；
-没有遇到giant component就降成逐文档串行；
-没有provider压力后永久降低并发并继续宣称性能达标；
-没有以CREATE_NEW/DIFFERENT/UNRESOLVED掩盖并行失败；
-所有失败都必须在新stage graph内局部修复、恢复或明确隔离。

只有完整代码、完整回归、真实30篇质量与性能、60/120篇线性扩展测试全部通过，才算本方案完成。未达到1小时或Package质量门槛时，继续修复新架构；不保留旧路径作为产品退路。
