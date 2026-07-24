# CDECR 运行效率、Token 成本与决策留痕优化方案

## 1. 结论

本方案建议采用“**并行计算、串行提交；压缩传输协议、不压缩业务证据；增加引用型留痕、不复制大 Payload**”的路线。

当前流程并非完全串行。Grounder、Judge、N9 Atomic 联合裁决、N12 Package Assignment、
N13 Package Merge 已经存在批次内并发，主要问题集中在：

1. Dreamer 长文 Block、单文档 `process_batch()`、跨文档 `process_batch()` 仍然串行；
2. N5.5 Field Resolution 按字段组逐个执行，LLM 调用次数多且共享 Prompt/Schema/Context
   被重复发送；
3. Atomic 和 Package 阶段中存在可以重叠的 Embedding 计算；
4. N12 应用结果时逐 Event 刷新 Package Embedding，随后 N13 又执行一次全量同步，产生重复
   网络往返；
5. 虽然各 LLM 节点已经广泛使用请求内短 ID，但仍重复发送候选对象、持久化 Schema 字段、
   长 Canonical ID、空字段和可由编排层推导的输出字段；
6. Model Call、领域 Decision 和最终对象之间缺少稳定的批次关联；Mention 最终对象与
   Grounder Draft、Judge Decision 之间尤其缺少显式 Lineage；
7. N5.5 在正式 Cross-document Run 创建前执行，部分 Field Model Call/Audit 的 `run_id`
   可能为空，无法完整归入一次业务运行。

建议的实施优先级为：

- **P0：低风险效率与审计底座**。不改语义判断，只调整并发边界、批量 Embedding、JSON
  Wire DTO、Run Trace 和 Lineage。
- **P1：节点协议压缩**。批量 Field Decision、候选对象去重、删除可推导输出字段、局部
  Repair。
- **P2：Prompt/Reason 协议和动态模型路由**。必须经过冻结语料 A/B，不能直接上线。

以相同语料、相同模型和相同候选集合为前提，合理目标是：

| 指标 | 保守目标 | 说明 |
|---|---:|---|
| 冷启动多文档端到端墙钟时间 | 降低 35%–55% | 主要来自单文档并发、N5.5 批量裁决和 Embedding 合并 |
| 单篇普通文档墙钟时间 | 降低 15%–30% | 不跨越 Grounder/Judge、Atomic/Package 的业务依赖 |
| 单篇长文墙钟时间 | 降低 25%–45% | Dreamer Block 并发收益更明显 |
| 全流程 LLM Input Token | 降低 18%–32% | 不包含 Provider Prompt Cache 的额外收益 |
| 全流程 LLM Output Token | 降低 10%–25% | 主要删除派生字段、压缩 Reason |
| 高价模型成本 | 降低 20%–40% | 主要来自 N9/N12 的按 Item 升级，而非降低模型质量 |

上述比例是基于当前代码结构的工程区间，不应直接相加。正式数值必须由同一冻结语料的
Stage-level A/B 得出。

---

## 2. 当前主流程与真实依赖

当前工作区的主路径可以还原为：

```text
N1 预处理、去重、分块
→ N2 Dreamer
→ N3 Grounder
→ N4 Judge
→ N5 Mention Finalization
→ N5.5 Canonical Field Resolution
→ N6 Identity Compiler
→ N7 Atomic Candidate Recall
→ N8 Hard Cannot-Link Shadow
→ N9 Atomic Joint Assignment
→ N10 Mention-to-Atomic Apply / Duplicate Audit
→ N11 Package Hint / Seed / Candidate Recall
→ N12 Package Joint Assignment
→ N13 Package Coreference / Boundary Correction
```

### 2.1 当前已经存在的并发

| 节点 | 当前并发 |
|---|---|
| Grounder | Candidate Batch 最多 3 个 Worker |
| Judge | Draft Batch 最多 3 个 Worker |
| N9 Atomic | 每批最多 3 个 Mention，Batch 最多 3 个 Worker |
| N12 Package Assignment | 每批最多 12 个 Event，Batch 最多 3 个 Worker |
| N13 Package Merge | 每批最多 12 个 Pair，Batch 最多 3 个 Worker |

因此优化重点不是简单地“给所有函数套线程池”，而是补齐缺失的上层并发、消除重复请求，
并用统一的 Provider 并发预算避免嵌套并发失控。

### 2.2 不能打破的业务依赖

以下边界必须继续串行：

1. Dreamer → Grounder → Judge：后者消费前者的完整结果；
2. N5.5 → N6：Identity 必须消费当前 Canonical Field Link；
3. N9 Decision → N10 Apply：Atomic 更新必须基于已验证的联合裁决；
4. N10 → N11/N12：Package 必须以已经形成的 Atomic Event 为输入；
5. N12 Apply → N13：Package Merge 必须看到 Assignment 后的成员关系；
6. 跨文档派生状态写入：相同 Atomic/Package 的版本和成员更新必须由单一提交者按稳定顺序
   执行。

以下内容不建议并行：

- 不建议对多个文档直接并行执行完整 CrossDocumentEngine。不同文档可能召回并更新同一
  Atomic/Package；并行快照会制造 False Split、Lost Update 或重复 Package。
- 不建议并行执行 N10/N12/N13 的 Registry 写入。LLM 判断可以并行，领域状态提交必须串行。
- 不建议为了吞吐跳过 M4 Judge。现有真实评估已经证明 Grounder 仍是候选生成器，不能安全
  替代最终 Mention Gate。

---

## 3. 运行效率优化

## 3.1 P0：建立统一的 Bounded Scheduler

当前多个节点各自创建 `ThreadPoolExecutor(max_workers=3)`。如果未来再并行多个文档，会形成
嵌套并发，既可能触发 Provider Rate Limit，也会让 SQLite 写锁竞争变严重。

建议引入 CDECR 自有的运行级 Scheduler：

```text
M1 Embedding Lane
M2 Structured Lane
M3 Structured Lane
M4 Structured Lane
SQLite Single-writer Lane
```

初始并发上限建议只作为可配置起点：

| Lane | 初始上限 | 说明 |
|---|---:|---|
| M1 | 2 | 优先做大 Batch，不靠高并发堆吞吐 |
| M2 | 4–6 | Field/N9/N12 的主要吞吐通道 |
| M3 | 2–3 | 控制复杂裁决与升级请求 |
| M4 | 2 | Judge 质量优先，避免大规模并发重试 |
| SQLite Writer | 1 | 所有领域状态由单 Writer 提交 |

Scheduler 需要记录 `queued_at/started_at/finished_at/queue_wait_ms`，并在 429、超时和余额异常时
做降并发，而不是盲目重试。

预期收益：

- 消除嵌套线程池过度并发风险；
- 对多文档批处理可稳定获得 2–4 倍 LLM 吞吐；
- 本项本身不改变业务结果，风险低。

## 3.2 P0：单文档批处理改为“先规划、后并行”

当前 `SingleDocumentProcessor.process_batch()` 逐文档串行。不能直接对整个 `process()` 并发，
因为预处理阶段会读取已有文档进行重复检测。

建议拆成：

```text
阶段 Ao：串行或单事务完成所有 Surce 的预处理与重复关系规划
→ 选出唯一代表文档
→ 对唯一代表文档并行执行 N2–N5
→ 重复文档等待代表文档完成后复用结果
```

这样既保留跨文档去重语义，又允许 Dreamer/Grounder/Judge 并行。

建议文档并发初始设为 3，并由全局 Scheduler 控制实际 M2/M3/M4 请求数。

预期收益：

- 4 篇以上冷启动批处理的单文档阶段墙钟时间降低 50%–70%；
- 单篇文档延迟不变；
- 风险低到中，主要风险是重复组规划错误，需要固定代表文档排序和 E2E 验证。

## 3.3 P0：Dreamer Block 并行

当前 `_dream()` 对 `document.document_blocks` 串行调用模型，各 Block 之间只有最后的
Candidate 去重关系，没有前后状态依赖。

建议：

1. 所有 Block 先生成稳定 `block_index/block_id`；
2. 通过 Scheduler 并行调用；
3. 按原始 Block 顺序汇总；
4. 使用现有 Candidate 内容哈希去重；
5. 审计中记录 Block 输入哈希和输出 Candidate ID。

预期收益：

- 普通单 Block 文档无变化；
- 2–4 Block 长文的 Dreamer 延迟降低 35%–65%；
- 长文端到端降低约 15%–30%；
- 风险低，需验证并发完成顺序不影响 Candidate ID 和 Grounder 排序。

## 3.4 P0：Title Embedding 与 Dreamer 重叠

Title Embedding 在预处理后执行，但后续 Dreamer/Grounder/Judge 不消费该向量。

建议：

- 单文档模式：Title Embedding 与 Dreamer 并行；
- 批处理模式：所有 Title 一次批量 Embedding；
- 在最终 Document Run 完成前等待 Title Embedding 成功并持久化，保持现有失败语义。

预期收益为单篇 3%–8%，多文档 Embedding 网络往返减少 60%–90%，风险低。

## 3.5 P0/P1：N5.5 拆成 Read/Decide/Apply 三段

当前 Canonical Field Resolution 逐 Group 执行：

```text
Recall → 必要时 Embedding → 必要时 M2 → Apply → 下一 Group
```

该设计保证了同一运行中新建 Field 可以被后续 Group 看到，但也造成大量串行请求。不能直接
将所有 Group 并行，否则可能为同一对象创建多个 Provisional Field。

建议改为：

### 阶段 1：确定性规划

- 先完成文内 Alias Group；
- 一次批量完成 KB Exact/String Recall；
- 将唯一 Exact、Generic Unresolved、Clearly-name-like NEW 等确定性结果分离；
- 为所有需 Embedding 的 Field Registry Entry 一次补齐向量；
- 为所有 Query Field 一次批量生成向量。

### 阶段 2：LLM 决策

- 按 Namespace/Policy 分 Lane；
- 每个请求处理 4–8 个独立 Field Task；
- 同一 Batch 内共享 System Prompt、Policy、Schema 和重复 Local Context；
- `participant.unknown` 单独成 Lane，因为它可以路由到 Typed Namespace；
- 普通 Typed Namespace 可以并行，不能与 `participant.unknown` 的 Apply 并行。

### 阶段 3：单 Writer 应用

- 按稳定键 `(namespace, normalized_value, mention_id, field_path)` 串行 Apply；
- Apply 时重新检查目标是否已由本批前一项创建；
- 若出现同一规范化 Surface 的并发 NEW，执行确定性合并，不再次调用 LLM；
- 每轮最多执行一次有界收敛，不能无限循环。

预期收益：

- N5.5 Model Request 数下降 60%–85%；
- N5.5 Input Token 下降 30%–55%；
- N5.5 墙钟时间下降 40%–70%；
- 风险中等。主要风险是同运行内新建 Field 的顺序语义变化，因此必须使用 Snapshot +
  Single-writer Recheck，而不是直接并行写 Registry。

## 3.6 P0：并行 Atomic Embedding 刷新与 Mention Embedding

N6 完成后：

- 现有 Atomic Event 的 Embedding 刷新；
- 当前文档 Mention 的 Embedding 生成；

两者彼此独立，可以在 M1 Lane 中并行或合并为大 Batch 后一次调用，再按 Owner Kind 拆分写入。

预期收益：

- Atomic Recall 前的 Embedding 阶段降低 25%–45%；
- 端到端降低约 3%–8%；
- 风险低。

## 3.7 P0：N9 保持每批 3 Mention，只提高 Batch 调度效率

现有真实修复已经将 N9 恢复为每个响应最多 3 个 Mention，这一上限与结构化输出可靠性有关，
不建议为了少几次请求直接增大。

建议：

- 保留 `ATOMIC_DECISION_MENTION_BATCH=3`；
- 多个 Batch 继续并行，但交由全局 M2/M3 Lane；
- M2 仅对出现 `UNCERTAIN`、多个 `SAME_EVENT` 或业务校验失败的具体 Mention 升级 M3；
- 不再让一个复杂 Mention 使同 Batch 的其他 Mention 一起重复进入 M3。

预期收益：

- N9 升级请求 Input Token 降低 30%–65%；
- N9 高价模型成本降低 20%–45%；
- 风险低到中，需保证拆出的 M3 Item 仍看到其完整候选集合。

## 3.8 P0：N11 Package Hint 每篇只解析一次

当前 Cross-document 开始时已执行一次 `resolve_package_hints()`，构建每个 Event 的 Package
Seed 时又重复调用。已有 Field Link 时通常不会再次调用模型，但仍会重复做 Group、Registry
读取和哈希计算。

建议：

- 每篇文档只执行一次 Package Hint Resolution；
- 构建 `mention_id → resolved package anchors` 的只读 Snapshot；
- 所有 Event Seed 从 Snapshot 取值；
- Seed Hash 中保留 Snapshot Hash，保证增量失效正确。

预期收益：

- N11 CPU/SQLite 读取降低 40%–80%；
- 对 Package Hint 未命中的文档收益较小；
- 风险低。

## 3.9 P0：N12 按模型 Tier 分组后再 Batch

当前 N12 一个 12-Event Batch 中，只要任一 Event 有多候选、Anchor Conflict 或缺少可靠
Bounded Anchor，整批都会使用 M3。

建议先对每个 Event 独立路由：

```text
Simple Event → M2 Batch
Complex Event → M3 Batch
```

两类 Batch 可以并行执行，结果仍由同一个 Package Apply 阶段串行提交。

预期收益：

- N12 总 Token 变化不大；
- N12 M3 Token/成本降低 25%–60%；
- 简单 Event 不再等待复杂 Batch，墙钟时间降低 10%–25%；
- 风险低，前提是不同 Event 的判断本就独立，不依赖同批其他 Event 的内容。

## 3.10 P0：N12 Apply 后统一批量刷新 Package Embedding

当前 N12 对每个 Event Apply 后都会调用 `_sync_package_embeddings([package])`，N13 开始时又对
Active Package 做同步。

N12 的所有 LLM Decision 已经基于 Apply 前的冻结候选 Snapshot 产生，因此逐 Event 更新的
Embedding 不会反向影响本轮尚未应用的 Decision。

建议：

1. N12 按稳定 Event 顺序串行 Apply；
2. 只记录 Dirty Package ID；
3. 完成全部 Apply 后一次批量生成 Dirty Package Embedding；
4. N13 复用该结果，只补齐确实缺失或 Hash 变化的 Package；
5. Membership Move 导致 Source Package 重建时同样只标 Dirty。

预期收益：

- N12/N13 Package Embedding 网络往返下降 50%–90%；
- Package 阶段墙钟时间降低 10%–25%；
- 风险低，需以 Profile Hash 验证 N13 开始前所有 Current Package 向量均与当前版本一致。

## 3.11 保持 Cross-document 文档级串行提交

本轮不建议实现多文档 Cross-document 并行写入。若后续确有吞吐压力，可考虑“候选图连通分量
分区”，只对完全不共享 Atomic/Package 候选的分区并行；但该方案复杂度和验证成本明显高于
当前收益，应延后。

---

## 4. Token 与成本优化

下表的“预计节省”均指该项所影响节点的 Token，不是全流程可直接累加比例。

| 优化项 | 影响节点 | 预计节省 | 风险 | 建议 |
|---|---|---:|---|---|
| JSON 使用紧凑分隔符，去无意义空格 | 全部结构化节点 | Input 2%–6% | 低 | P0 |
| Wire Schema 删除 `title/default/examples`，只保留必要 description | N2/N3/N9/N12/N13 | Input 5%–15% | 低到中 | P0 |
| Wire DTO `exclude_none/exclude_defaults` | N3/N9/N12/N13 | Input 5%–15% | 中 | P0，必须恢复默认值后校验 |
| Recall Score/Similarity 保留 3 位小数 | N9/N12/N13 | Input 1%–3% | 低 | P0 |
| 请求内 Canonical ID Interning | N9/N12/N13 | Input 5%–15% | 中 | P1 |
| 同 Batch 候选对象字典化、Task 只引用短 ID | N9/N12 | Input 10%–35% | 中 | P1 |
| N13 Package View 去重，同一 Package 在多个 Pair 中只发送一次 | N13 | Input 25%–55% | 中 | P1，最高收益项 |
| Field Task 批量化并共享 Context/Policy/Schema | N5.5 | Input 30%–55% | 中 | P1 |
| Grounder 自由文本 `issue_flags` 改为有界 `issue_codes`，只在非空时输出 | N3 | Output 1%–4% | 低到中 | P1 |
| N9 删除编排层可推导的 Related/Duplicate ID 列表 | N9 | Output 10%–25% | 低到中 | P1 |
| N12 删除可由 Assessment 推导的完整 Ranked List | N12 | Output 8%–18% | 中 | P1 |
| Repair 只发送失败 Item 与约束胶囊 | 全部 | Repair Input 35%–65% | 中 | P1 |
| Common Reason Code + 异常才输出短 Note | N4/N9/N12/N13 | Output 10%–35% | 中到高 | P2/A-B |
| Provider Prompt Cache（若 Gateway 和供应商支持） | 静态 Prompt/Schema | 计费 Input 10%–30% | 低 | 条件启用，不计入基础目标 |

## 4.1 先统一 Wire DTO，不修改持久化模型

当前多处直接将 `AtomicEvent/EventPackage/IdentityProfile` 的持久化模型
`model_dump(mode="json")` 发送给 LLM。持久化模型服务于恢复、版本和审计，不等于最省 Token
的模型协议。

建议为每个节点定义独立 Wire DTO：

```text
Persistence DTO
→ Node-specific Wire DTO
→ request-local ID / intern table
→ LLM
→ Wire Output DTO
→ strict validation
→ Persistence DTO / Domain Decision
```

这与当前 Judge 已采用的“持久化协议与模型命令协议分离”方向一致，但必须吸取 Judge A/B 的
教训：Payload 变短不代表总成本必然下降，若首轮结构通过率下降，Repair 会吞掉全部收益。

Grounder 的 `issue_flags` 当前没有参与下游业务判断，但它仍有潜在审计价值，因此不建议直接
静默删除。应改成有界 `issue_codes`，只在非空时进入 Grounder Batch Audit，不进入最终
Mention Payload；能够由 Evidence/Schema Validator 确定的 Issue 由编排层生成，不要求模型
重复输出。

## 4.2 全局 JSON 与 Schema 精简

当前 Judge 已删除部分 Schema 的 `title/default`，其他结构化节点没有统一执行。

建议统一：

- JSON 使用 `separators=(",", ":")`；
- 删除 Schema 中生成器附带的 `title/default/examples`；
- `description` 只保留模型确实容易误判的业务字段；
- 不在 Prompt 和 Schema 中重复解释同一规则；
- Output Schema 中不要暴露编排层会覆盖或推导的字段；
- 所有 Wire Schema 保存 Hash，便于 A/B 对比。

不能删除的内容：

- Event Identity 与 Claim 的区别；
- Actual/Guidance、不同财期、不同机构、不同事件里程碑边界；
- Evidence 必须来自已暴露 Segment 的约束；
- Package 与 Atomic 不同粒度的说明。

## 4.3 请求内 ID Interning

顶层 Mention/Atomic/Package ID 已经使用 `m1/a1/e1/p1/d1/c1/k1`，但嵌套在
`identity_profile`、Canonical Participant、Package Anchor 和 Artifact 中的长 ID 仍会重复。

建议增加每个请求的只读字典：

```json
{
  "refs": {
    "r1": "COMPANY:MU",
    "r2": "FISCAL_PERIOD:MU:FY2026:Q3",
    "r3": "ARTIFACT:EARNINGS_RELEASE:..."
  },
  "tasks": []
}
```

Wire DTO 中只发送 `r1/r2/r3`。Adapter 恢复完整 ID 后再执行现有业务校验和持久化。

风险：

- 嵌套列表恢复遗漏；
- 模型返回不存在的 Ref；
- 同一个短 ID 在不同 Task 中语义漂移。

控制：

- Ref 在整个请求内全局唯一；
- Schema 限制短 ID Pattern；
- 恢复后再次执行 Candidate Coverage 和 Domain Validation；
- 审计保存 Ref Map Hash，不需要复制整份长 ID Map。

## 4.4 候选对象去重

N9 中同一个 Atomic 可能是多个 Mention 的候选；N12 中同一个 Package 可能是多个 Event 的
候选；N13 中同一个 Package 会出现在多个 Pair。当前实现会重复发送完整 Profile。

建议统一改为：

```json
{
  "atoms": {
    "a1": {"p": "...", "i": {}, "t": {}}
  },
  "tasks": [
    {"m": "m1", "c": ["a1"]}
  ]
}
```

以及：

```json
{
  "packages": {
    "p1": {"kind": "...", "anchor": {}, "members": []}
  },
  "pairs": [["p1", "p2"]]
}
```

这是 N13 最有价值的 Token 优化，因为 Pair 图中 Package View 的重复率天然高。

## 4.5 删除模型输出中的派生字段

### N9

当前模型输出：

- 每个 Candidate 的 Relation；
- `related_candidate_event_ids`；
- `possible_duplicate_atomic_ids`。

后两者已经由 Relation 和最终 Merge Target 决定，编排层也会重新推导。建议 Wire Output
只保留：

```text
mention_id
action
merge_target_event_id
candidate_assessments
```

编排层恢复：

- `RELATED_NOT_SAME` → `related_candidate_event_ids`；
- 除选中 Target 外的 `SAME_EVENT` → `possible_duplicate_atomic_ids`。

### N12

`ranked_member_package_ids` 与 `selected_member_package_id` 存在重复。业务真正需要的是：

- 每候选 Relation；
- 如果有多个 MEMBER，选中的唯一 Target；
- 为什么选它。

建议删除完整 Ranked List，或仅在多 MEMBER 时输出其余候选的相对次序。普通单 MEMBER 场景
不输出 Ranking。

## 4.6 Targeted Repair

当前 Repair 会重复发送：

- 原始完整 Request；
- Invalid Payload；
- Validation Error。

对于 N9/N12/N13，一个 Item 失败会导致整个 Batch 重发。

建议按错误类型处理：

1. JSON 表示错误：先做现有安全 Adapter 归一，不改变业务枚举；
2. Candidate Coverage 错误：只重发失败 Item、完整候选和允许 ID；
3. Evidence 错误：只重发失败 Draft、相关 Segment 和 Evidence 约束；
4. Batch 中其他已通过 Item 保留并写入临时 Validated Result；
5. Repair 仍失败时整 Stage 失败，不伪装成 CREATE_NEW。

Repair Capsule 示例：

```json
{
  "task": "m2",
  "allowed_candidates": ["a1", "a2"],
  "invalid": {},
  "errors": [{"path": "candidate_assessments", "code": "coverage"}],
  "context": {}
}
```

## 4.7 Reason 协议

### Mention / Judge

当前每个 Judge Command 都要求最长 240 字符 Reason。审计需要 Reason，但普通 ACCEPT 不需要
长自由文本。

P2 建议：

```text
reason_code: VALID | NON_EVENT | DUPLICATE | COMPOSITE | EVIDENCE_FIX |
             TIME_FIX | CLAIM_STATE_FIX | ATTRIBUTE_ONLY | OTHER
reason_note: 可选，最多 96 字符
```

- 普通 ACCEPT 只返回 `VALID`；
- ACCEPT with changes、REJECT、SPLIT、MERGE_AS_ATTRIBUTE 才允许 Note；
- Judge 协议历史上出现过“首轮略省 Token、Repair 总成本反升”的情况，因此该项必须最后做。

### Atomic

N9 当前有 Relation、`identity_differences` 和 `claim_conflict`，但缺少“为什么是
SAME/RELATED/UNRELATED”的稳定依据。

建议每个 Assessment 增加 1–2 个短枚举：

```text
basis_codes:
SAME_ARTIFACT_PERIOD
SAME_OCCURRENCE
DIFFERENT_PERIOD
DIFFERENT_ACTOR
DIFFERENT_MILESTONE
CLAIM_VALUE_ONLY
INSUFFICIENT_EVIDENCE
```

只有 `UNCERTAIN`、多个 SAME 或 M3 升级场景允许最多 80 字符 Note。该设计略增普通 N9 Output，
但能显著提高审计价值；可以同时删除派生 ID 列表抵消 Token。

### Package

N12/N13 已有自由文本 Reason。建议改成：

- 必填 `reason_code`；
- 可选 `reason_note <= 96`；
- Candidate Assessment 保留成员关系和 External Relation；
- Package Merge 的 `UNCERTAIN/DIFFERENT_PACKAGE` 可保留短 Note。

---

## 5. Mention、Atomic Event、Package 决策留痕

## 5.1 当前能力与缺口

### 已有能力

- Dream Candidate、Grounder Batch、Judge Decision 已分别持久化；
- Event Mention 不可变；
- Atomic Assignment 保存最终动作，Decision Audit 额外保存完整 N9 Decision；
- Package Assignment 保存 Candidate Assessments、选择结果和 Reason；
- Package Membership Decision、Package Merge Decision、Redirect、Version 均有持久化；
- Model Call 保存 Stage、Model、Token、Latency、Prompt/Schema/Input Hash。

### 主要缺口

1. Final Mention 没有显式指向 Grounder Draft 和 Judge Decision；
2. Duplicate Evidence、Split Child、Attribute Merge 对最终 Mention 的贡献关系无法直接查询；
3. 并发 Batch 下，领域 Decision 无法稳定定位到具体 `model_call_id`；
4. Atomic/Package Candidate 使用可变 Head 构造，事后只看 ID 不能知道当时比较的是哪个 Version；
5. Model Call 只保存 Input Hash，缺少 Batch Item Count、Candidate Count、Output Hash 和
   Queue Wait；
6. N5.5 在 Cross-document Run 创建前执行，Field Audit/Model Call 可能没有 Run 归属；
7. Package Assignment、Membership Move、Package Merge 之间缺少统一 Cause ID。

## 5.2 P0：在任何派生处理前创建 `trace_id`

`processing_key` 依赖 Field Links，不能在 N5.5 前最终确定；但审计关联不应因此延后。

建议区分：

```text
trace_id：一次尝试开始时立即创建，只用于关联和审计
processing_key：Field Resolution 后计算，用于幂等和结果复用
run_id：领域 Run ID，可与 trace_id 一致或建立唯一映射
```

N5.5、N6、N7–N13 的 Model Call、Decision Audit 和失败记录全部携带同一 `trace_id`。

如果最终发现已有相同 `processing_key` 的成功结果：

- 当前 Trace 标记为 `REUSED`；
- 记录 `reused_from_run_id`；
- 不生成新的领域 Decision。

## 5.3 P0：新增 Mention Lineage

建议新增轻量、追加式 `mention_derivations`，不复制 Mention Payload：

```text
derivation_id
trace_id / document_run_id
mention_id
origin_draft_id
judge_decision_id
derivation_role
contributor_draft_ids_json
source_candidate_ids_json
grounder_batch_key
judge_batch_key
created_at
```

`derivation_role`：

```text
ACCEPTED
REVISED
SPLIT_CHILD
DUPLICATE_EVIDENCE_CONTRIBUTOR
ATTRIBUTE_MERGE_CONTRIBUTOR
```

这样可以直接回答：

- 某 Mention 来自哪些 Dream Candidate；
- Grounder 生成了什么 Draft；
- Judge 对 Draft 做了什么；
- 哪些被丢弃 Draft 的 Evidence/Attribute 最终进入了保留 Mention；
- Finalization 是否又做了确定性变换。

对于 N5 Mention Finalization，现有 `normalization_decisions` 继续保留字段级变化，不需要再复制
完整前后 Payload。

## 5.4 P0：给 Model Call 增加轻量关联元数据

建议为 `model_calls` 增加或规范以下元数据：

```text
trace_id
batch_key
attempt: initial | repair | escalation
request_item_count
candidate_count
request_payload_bytes
output_hash
queue_wait_ms
cache_hit
```

不建议默认保存完整 Prompt/Response：

- Payload 大；
- 包含原文，增加数据治理负担；
- 领域表已经保存了可审计结果。

只在隔离 Debug 模式按显式配置保存脱敏后的完整 Payload，并设置 TTL。

## 5.5 P0：Atomic Decision 引用 Candidate Version

保留现有 `atomic_assignment_decisions` 和完整 N9 Decision Audit，只补充：

```text
decision_batch_id
model_call_id
incoming_mention_hash
candidate_refs: [{event_id, version, identity_hash}]
resulting_event_version
decision_output_hash
reason_code / basis_codes
```

M0 `NO_ELIGIBLE_CANDIDATE` 场景的 `model_call_id` 为 null，但仍保存 Candidate Snapshot 与
确定性 Reason Code。

N10 Apply 后记录：

```text
parent_event_version
applied_assignment_id
result_event_version
profile_hash_before
profile_hash_after
```

这比在 Audit 中再保存完整 Atomic Profile 更轻，也足以从不可变 Version 表恢复当时状态。

## 5.6 P0：Package Cause Chain

补充以下引用关系：

### Package Assignment

```text
decision_batch_id
model_call_id
event_ref: {event_id, version}
candidate_refs: [{package_id, version, profile_hash}]
package_seed_hash
resulting_package_version
```

### Membership Decision

增加：

```text
caused_by_assignment_id
caused_by_merge_plan_id
```

二者只允许一个非空。

### Package Merge

```text
model_call_id
source_package_version
target_package_version
pair_snapshot_hash
merge_plan_id
resulting_package_version
```

### Boundary Repair

每次 Remove/Move/Quarantine 写入：

```text
finding_id
policy_version
input_package_version
actions
caused_membership_decision_ids
result_package_versions
```

这样可以从 Final Package 反向追到：

```text
Final Package Version
← Merge Plan / Boundary Repair
← Package Membership Decision
← Package Assignment
← Atomic Event Version
← Atomic Assignment
← Event Mention
← Judge / Grounder / Dreamer
← Source Message / Evidence Span
```

## 5.7 不增加大 Payload 的原则

审计默认只新增：

- ID；
- Version；
- Hash；
- Reason Code；
- 极短 Note；
- Parent/Candidate Ref。

以下大对象继续复用现有事实表：

- Source Message；
- Preprocessed Document；
- Dream Candidate；
- Grounder Batch Result；
- Judge Decision；
- Event Mention；
- Atomic/Event Package Version。

---

## 6. 目标编排

```text
Batch Intake
→ Preprocess / Duplicate Plan                         [single writer]
→ Unique Documents
   ├─ Bulk Title Embedding                           [M1 lane]
   └─ Per-document bounded execution
      → Dreamer Blocks                               [parallel M2/M3]
      → Grounder Batches                             [parallel M3]
      → Judge Batches                                [parallel M4]
      → Mention Finalization                         [CPU + single writer]
→ Documents enter Cross-document stage in stable order
   → Create Trace
   → N5.5 Recall Plan                                [parallel read/CPU]
   → N5.5 Bulk Embedding                             [M1 lane]
   → N5.5 Namespace Decision Batches                 [parallel M2]
   → N5.5 Apply                                      [single writer]
   → N6 Identity Compiler                            [CPU]
   → Atomic Head Embedding + Mention Embedding       [parallel/bulk M1]
   → N7/N8                                           [CPU/read]
   → N9 Decision Batches                             [parallel M2; item-level M3]
   → N10 Apply                                       [single writer]
   → N11 Hint/Seed once                              [CPU/read]
   → N12 M2 and M3 Batches                           [parallel by tier]
   → N12 Apply                                       [single writer]
   → Dirty Package Bulk Embedding                    [M1 lane]
   → N13 Pair Decision Batches                       [parallel M3]
   → N13 Graph Reconcile / Merge / Boundary Apply    [single writer]
   → Complete Trace and Run
```

---

## 7. 成本预算与失败语义

建议增加 Run-level Budget Manager，至少跟踪：

```text
input_tokens
output_tokens
estimated_cost_units
model_call_count
repair_count
m3_escalation_count
wall_clock_ms
```

预算分为：

- **Soft Budget**：触发日志、缩小后续 Batch、降低并发或停止预取；
- **Hard Budget**：在下一个可恢复 Checkpoint 前停止，不继续发新请求。

必须保持：

1. 技术失败或预算耗尽不能伪装成 `CREATE_NEW Atomic`；
2. 技术失败或预算耗尽不能伪装成 `CREATE_NEW_PACKAGE`；
3. Mandatory Stage 未完成时 Run 标记 FAILED/INTERRUPTED，并允许从 Stage Checkpoint 恢复；
4. 已验证 Batch 可以复用，不能整篇从头重跑；
5. 预算策略和阈值进入 Processing Key/Run Config，避免错误复用。

---

## 8. 分阶段实施

## Phase A：P0 低风险底座

1. 创建统一 Scheduler 和 Provider/Tier 并发 Lane；
2. Dreamer Block 并发、Title Embedding 重叠；
3. 单文档 Batch 改为 Duplicate Plan 后并发；
4. Atomic/Mention Embedding 并行；
5. Package Hint 每篇一次、N12 Tier 分组；
6. N12 Dirty Package Embedding 批量刷新；
7. 全局紧凑 JSON、Wire Schema Presentation Metadata 清理；
8. 在 N5.5 前创建 Trace；
9. 新增 Mention Lineage、Model Call Batch Metadata、Atomic/Package Version Ref；
10. Stage Metric 看板或导出。

这一阶段原则上不改变 LLM 看到的业务字段和输出动作。

## Phase B：P1 协议与调用次数优化

1. N5.5 Read/Decide/Apply 和 Field Batch Decision；
2. N9/N12/N13 候选对象字典化；
3. 嵌套 Canonical ID Interning；
4. 删除 N9/N12 可推导输出字段；
5. Targeted Repair；
6. `exclude_none/exclude_defaults` Wire DTO；
7. Grounder `issue_flags` 改为有界 `issue_codes`，并保留 Batch Audit。

## Phase C：P2 需要语义 A/B 的优化

1. Judge Reason Code + Optional Note；
2. Atomic `basis_codes`；
3. Package Reason Code；
4. Prompt 去重和缩短；
5. Provider Prompt Cache；
6. 动态 M2/M3 路由阈值校准。

---

## 9. 验收与回滚门槛

## 9.1 性能与 Token

对同一冻结语料同时记录：

- 每 Stage P50/P95 Queue Wait、Model Latency、Wall Time；
- 每 Stage Request Count；
- Initial/Repair/Escalation 次数；
- Input/Output Token；
- 每文档、每 Mention、每 Atomic、每 Package 的 Token；
- M2/M3/M4 Token 占比；
- Cache Hit 和 Reused Batch 数。

## 9.2 结构可靠性

- First-pass Schema Valid Rate 不下降；
- Repair Final Success Rate 不下降；
- Candidate Coverage 100%；
- Short ID/Intern Ref 恢复 100%；
- 重跑零新增 Model Call；
- 任一失败 Stage 可从最近 Checkpoint 恢复；
- 并发顺序扰动下最终 ID、Assignment 和 Membership 稳定。

## 9.3 业务质量

必须保持或改善：

- Mention Precision/Recall；
- Judge 错误 REJECT/MERGE_AS_ATTRIBUTE；
- Atomic False Merge 为 0 的高风险固定集；
- Atomic Recall@K；
- Package False Merge；
- Package Fragmentation；
- 相同冻结输入的跨波次 Package Pair Jaccard；
- External Candidate Accuracy。

当前 Package 语义仍存在 False Merge、Fragmentation 和跨波次不稳定，效率优化不能把
N11–N13 的工程成功误报为生产语义通过。

## 9.4 发布策略

每项 Wire/Prompt 变更使用：

```text
off
shadow
canary
on
```

- `shadow` 只生成新 Wire Payload/Decision，不写正式派生状态；
- `canary` 仅处理固定比例文档；
- 任一结构通过率、Repair Token 或 False Merge 指标恶化即回退；
- 回退只切换编排/协议版本，不删除历史审计。

---

## 10. 明确不做

本轮方案不建议：

1. 跳过或降级 M4 Judge；
2. 删除原文 Evidence、Source Claim、Identity Profile 或 Package Representative Member；
3. 将所有跨文档处理直接并行；
4. 用更大的 N9 Batch 冒险换取少量 Prompt Token；
5. 让 Budget/Provider Failure 生成假的 CREATE_NEW；
6. 默认保存所有完整 Prompt 和 Response；
7. 建设复杂的分布式任务系统、消息队列或全局图重聚类；
8. 在当前 Package 语义门槛未通过前扩大 N13 自动合并范围；
9. 仅根据单次首轮 Payload 下降宣布成本优化成功。

---

## 11. 推荐落地顺序

如果下一轮进入实现，建议严格按以下顺序：

1. **先补 Trace/Lineage/Stage Metric**，保证优化前后可测；
2. **再做低风险并发与 Embedding 批量化**；
3. **再做 N5.5 Batch、N9/N12/N13 候选去重和 Targeted Repair**；
4. **最后做 Judge/Prompt/Reason 协议 A/B**。

原因是：没有可靠 Stage Metric 和决策 Lineage 时，即使墙钟时间或 Token 下降，也无法判断
是否以更多 Repair、错误 Merge、Package Fragmentation 或不可复现的决策为代价。
