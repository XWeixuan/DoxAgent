# CDECR 历史版本性能与质量选择性恢复方案

日期：2026-08-17  
性质：历史代码与真实验收结果审计后的修复方案；本文件不执行代码回滚或真实模型验收。

## 1. 结论先说

当前版本不适合继续做 MU300。问题不是近期改坏了某一个 Prompt 或 Schema，也不存在一个可以无脑回退、同时恢复全部性能和质量的历史 commit。

本轮排查得到四个主要结论：

1. **钟墙翻倍的主因是并发控制退化，不是模型计算量翻倍。** 当前累计 provider latency 比上一轮下降约 9.3%，钟墙却上升 93.6%。共享 provider gate 在单文档阶段受到压力后，从高并发逐级收缩到最终并发 4，并把这个低并发状态带入 Field、N9、Parent 与 Package 阶段；同时 tier limiter 又进行第二次收缩。当前还把 M1 embedding 纳入了 structured request gate，进一步扩大排队范围。
2. **Judge 变成最高成本节点的直接原因是 M4 `high` 从“配置存在但没有实际发送”变成真正传给 Responses API。** Judge Prompt 和 Schema 没有同期变化；请求数和输入量也没有增加，但输出 Token 从历史正常版本的 16,382 上升到 496,741，累计 latency 从 201.8 秒上升到 5,420.2 秒。Judge 当前主要消耗的是模型内部 reasoning/output，不是业务 Payload。
3. **Mention/Atomic 回归并非“毫无缘由”。** 虽然核心 Dreamer/Grounder/Judge/N9 Schema 与近期 Prompt 大体未变，但实际模型、API 端点、thinking 参数、Relevance Enforce 行为和失败语义都变了。Mention 主要漏在 Dreamer missing/partial，不应归因给 Judge；Atomic 则直接受到 N9 Responses JSON Object 大量 `invalid_json` 和 27 条降级 singleton 的影响。
4. **除 Package V3 外的 `json_invalid` 也不是 Schema 变化导致。** Judge、N9、Dreamer 的 Schema hash 与正常历史运行一致；真正变化是 M2 从 Chat Completions JSON Object、thinking disabled 切到 Responses JSON Object。当前 N9 主请求 59 次 `invalid_json`，而 V2.1 R3 没有 N9 主请求 `invalid_json`。继续使用相同 Responses 路径做 batch/item repair，只会放大失败与成本。

因此，最优恢复路径是：

- 保留当前确定性批处理、失败局部化、Package V3 Registry/CAS 等有价值的新实现；
- 恢复各模型层最后一次已验证的“节点级传输和 thinking 行为”，取消全局统一 Responses 的做法；
- 恢复阶段隔离的并发控制，取消跨阶段共享的自适应收缩；
- 先做冻结节点 A/B，再做新的 30 篇全流程；
- 只有恢复版 30 篇同时通过性能、JSON 稳定性和业务质量门槛后，才重新进入 MU300。

## 2. 审计边界与版本可追溯性

### 2.1 当前 Git 状态

当前分支：`codex/cdecr-parent-occurrence-package-v2`  
当前 HEAD：`8942f8e`，2026-08-13，`进行package V3重构前`

与本次问题相关的历史节点：

| Git 节点 | 日期 | 主要意义 |
| --- | --- | --- |
| `f169c05` / `218ef45` | 2026-08-04 | Thinking downshift R5 及收尾式 Package 收敛版本 |
| `de185c8` | 2026-08-09 | 8 月 8 日确定性运行时优化附近的代码边界 |
| `238e3f9` | 2026-08-12 | Relevance 与 Parent Occurrence 重构 |
| `8942f8e` | 2026-08-13 | Package V3 重构前的最后一次提交 |

重要限制：8 月 13 日以后 Package V3、strict、JSON Object failure resilience、provider gate 等修改目前仍是同一份未提交工作树改动，无法通过普通 `git bisect` 精确定位到单一提交。因此本方案采用三种证据交叉恢复：

1. 历史 commit 的代码行为；
2. 每轮真实 SQLite/model_calls/telemetry；
3. 当前工作树相对 `8942f8e` 的逐文件差异。

执行修复前应先把当前工作树做一个**取证快照 commit**，之后按“模型传输”“并发门控”“质量语义”分别提交，恢复后才重新具备可二分性。这个快照不是认定当前实现正确，也不是发布 commit。

### 2.2 不能选择单一历史版本整体回退

- 2026-08-04 R5：Mention/N9 质量相对较好，但钟墙 55.26 分钟，不适合作为性能恢复目标。
- 2026-08-08 R2：钟墙 18.60 分钟，确定性运行时优化有效；但后段 provider 402 污染了 Package Recall，不能把 Package 结果当正常质量基线。
- 2026-08-13 V2.1 R3：钟墙 21.34 分钟，Judge 成本正常；但 Parent/Package 过度碎片化，不能回滚整个 Package 业务逻辑。
- 2026-08-17 当前版：Package V3 在低覆盖 Gold 上的准召较好，但上游稳定性、钟墙和 JSON 有严重回归。

恢复目标应组合为：

```text
运行时/并发：参考 2026-08-08 与 2026-08-13
非 Package 节点传输：参考 8942f8e 以前的节点级行为
Mention/N9 质量：参考 2026-08-04 R5 与 2026-08-13 R3
Package：保留 V3，单独验收，不回退到 V2.1/V2.0R
```

## 3. 历史真实运行对照

| 运行 | 30篇成功 | 钟墙 | Calls | Input | Output | 累计模型 latency | 关键结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-08-04 Thinking Downshift R5 | 30/30 | 55.26m | 792 | 1,363,869 | 1,064,405 | 7,856.95s | 上游质量较好，但旧编排慢 |
| 2026-08-08 Deterministic Runtime R2 | 30/30 | 18.60m | 820 | 1,321,345 | 1,061,669 | — | 性能基准；Package 被 402 污染 |
| 2026-08-13 Parent V2.1 R3 | 30/30 | 21.34m | 981 | 1,734,241 | 433,442 | 5,280.87s | 非 Package 运行时与 Judge 成本基准 |
| 2026-08-16 Strict 版 | 30/30 | 45.18m | 547 | 958,857 | 1,453,567 | 16,003.47s | effective thinking/strict 引发输出与延迟膨胀 |
| 2026-08-17 JSON Object 版 | 28/30 | 87.51m | 686 | 1,517,458 | 1,287,834 | 14,515.43s | 共享 gate 塌缩、N9 JSON invalid、2篇失败 |

不能只看 Calls 或累计 latency 判断效率。当前版累计模型 latency 比 strict 版低约 9.3%，但钟墙几乎翻倍，证明主要损失在队列、重试、退避和阶段间等待，而不是模型真正多计算了一倍。

## 4. 问题一：钟墙为什么翻倍

### 4.1 已确认的代码变化

`8942f8e` 以前：

- structured provider gate 主要是固定 hard limit；
- 默认 target/hard/start-rate/burst 约为 `100/160/50/80`；
- Bulk executor 有自己的 phase-local semaphore/token bucket；
- M1 embedding 不经过 structured LLM gate；
- 单文档阶段的压力状态不会持续污染后续 Field/N9/Parent。

当前工作树：

- hard limit 被压到最多 100，burst 降到 12；
- provider gate 新增全局 `_dynamic_limit`；
- 每次压力信号按约 0.7 倍收缩，100 次成功才缓慢恢复 5；
- tier adaptive limiter 还会再次按 0.6/0.8 收缩；
- Bulk executor 复用 scheduler 的同一个 provider gate；
- scheduler 的 gate 包裹范围扩到 M1 embedding；
- `provider_error`、欠费、数据审查失败等性质不同的错误被统一识别成“并发压力”；
- scheduler 和 executor 都可能重试，形成物理调用放大。

### 4.2 当前运行证据

- configured hard limit：100；
- final dynamic limit：4；
- observed max active：31；
- queue wait：846,902ms；
- provider completed physical attempts：808，而业务 model_calls 只有 686；
- 单文档阶段 27.64m，Field 7.29m，N9 12.86m，Parent+Package 39.73m；
- Package V3 本身只有 5.88m，Parent/隐藏等待占了余下的大部分时间。

这说明不是“并发参数略保守”，而是**全局状态塌缩后跨阶段继承**。三个 provider data-inspection 类错误不应把后续全部工作压成并发 4。

### 4.3 仍不能武断归因的部分

Parent 阶段存在约 21 分钟的请求记录空档，而已记录的请求 latency 无法覆盖该空档。当前 telemetry 没有记录每次物理 attempt 的开始、结束、退避、key rotation 和 gate wait，因此不能把全部 21 分钟都声称为 queue wait。

修复时必须补物理 attempt 级遥测；在获得该数据前，准确说法是：

- 共享 gate 塌缩是已确认主因；
- Parent 还有一段未被业务 model_calls 解释的等待，可能是 retry/backoff/provider operation，必须在恢复版中验证。

## 5. 问题二：Judge 为什么突然占用最高 Token 和时间

### 5.1 不是 Prompt 或 Schema 变化

Judge 在 R5、V2.1 R3 和当前运行使用相同 Schema hash。当前与 `8942f8e` 相比也没有 Judge 业务 Prompt 的实质改动。

| Judge 运行 | Calls | Input | Output | 总 Token 占比 | 累计 latency | latency 占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V2.1 R3 | 30 | 181,912 | 16,382 | 9.15% | 201.75s | 3.82% |
| 当前 JSON Object | 28 | 172,107 | 496,741 | 23.84% | 5,420.21s | 37.34% |

输入量和调用数基本相同，输出增加约 30.3 倍，latency 增加约 26.9 倍。因此根因不在 batch 数、Prompt 长度或业务 Schema。

### 5.2 真正变化

`8942f8e` 以前 DashScope adapter 对 M3/M4 使用 Responses，但实际固定发送 `reasoning effort = none`。配置中虽然写了 M3 low、M4 high，却没有真正透传。

当前 CLI 和 adapter 开始透传 tier reasoning：M4 Judge 的 `high` 真正生效。百炼本轮 usage 又没有单独提供 reasoning token 字段，隐藏 thinking 被计入 output，表现为 Judge 输出 Token 暴涨。

因此恢复动作不是删 Judge 规则，而是先恢复：

```text
Judge: Responses JSON Object + reasoning none
```

之后若需要提高 Judge 质量，只能做 Judge 节点的独立 `none vs low` A/B，不能再用全局 M4 high。

## 6. 问题三：Mention/Atomic 为什么回归

### 6.1 Mention 回归的真实分布

同一 268 条 Gold：

| 运行 | Output Mention | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| 2026-08-04 R5 | 229 | 79.91% | 68.28% | 73.64% |
| 2026-08-17 当前 | 248 | 60.08% | 55.60% | 57.75% |

当前 FN 根因：

- Dreamer missing：76；
- 输出 partial：42；
- Judge：1。

所以 Judge 是成本异常，但不是 Mention Recall 的主因。为了降低 Judge Token 去改 Judge 业务语义，反而会错修问题。

### 6.2 “近期没改业务逻辑”的前提为什么不成立

核心 Prompt/Schema 近期确实基本没动，但模型行为边界发生了以下变化：

1. 模型从 `deepseek-v4-flash` 变成 `deepseek-v4-flash-0731`；
2. M2/M3/M4 transport 与 effective reasoning 改变；
3. Relevance 已是 Enforce，本轮从 430 个候选删除 88 个；
4. Relevance 改为独立请求，不再继承 Dreamer response；
5. 两篇文档 Grounder provider data-inspection 失败，导致整篇无最终结果；
6. 更早的 `238e3f9` 附近，Dreamer 删除了“优先 target ticker material claims”的指导。

其中 Relevance 独立请求是合理的失败隔离，但 Enforce 是否误删 Mention Gold 仍未做固定候选逐条对齐。因此应保留协议、重新验证语义，不应把独立请求本身回滚。

### 6.3 Atomic 回归的直接原因

当前：

- 248 个 N9 task；
- 27 个退化为 singleton；
- 189 个 Atomic，其中 160 个 singleton，占 84.66%；
- N9 主请求、batch repair、item repair 合计形成高额 Token；
- hard-cannot-link violations 仍有 98。

N9 主 Schema 与历史版本一致，但主请求有 59 次 `invalid_json`；同一路径 repair 又出现 12 次 batch invalid 和 25 次 item invalid。相同错误 transport 被重复使用，是 Atomic 碎片化和成本回归的直接原因。

## 7. 问题四：`json_invalid` 到底哪里变了

### 7.1 Schema 不是主要变量

以下节点的历史/当前 Schema hash 保持一致：

- Judge；
- N9 Atomic Coreference；
- Dreamer；
- Grounder 在 V2.1 R3 与当前一致。

当前 112 次 `invalid_json` 分布：

| 节点 | 次数 |
| --- | ---: |
| N9 main | 59 |
| N9 item repair | 25 |
| N9 batch repair | 12 |
| Grounder | 5 |
| Field | 4 |
| Judge | 2 |
| Parent induction | 2 |
| 其他 | 3 |

这与“所有 Schema 同时坏掉”不相符，却与“共同 adapter/endpoint 行为变化”高度一致。

### 7.2 历史与当前 transport 差异

历史稳定行为：

```text
M2 Field/N9: Chat Completions + JSON Object + enable_thinking=false
M3/M4: Responses + JSON Object + effective reasoning=none
```

当前行为：

```text
M2/M3/M4: 全部 Responses + JSON Object
reasoning: M2 none / M3 low / M4 high
```

V2.1 R3 没有 N9 main `invalid_json`，当前则 59 次。因变量隔离已经足够强：优先恢复 M2 的 Chat JSON Object，而不是重写 N9 Schema 或叠加更多 repair。

目前 DB 没有持久化失败原文或受限解析诊断，尚无法证明具体是 Markdown fence、截断、root shape、thinking 污染还是 provider 响应拼接。方案不编造该细节，修复时补充受限诊断后再定。

## 8. 选择性恢复设计

## 8.1 P0-A：恢复节点级 Model/Transport/Thinking Matrix

禁止继续使用“所有 LLM 节点统一 Responses JSON Object”的全局开关。传输模式必须是节点契约的一部分。

| 节点/层 | 恢复后的默认行为 | 理由 |
| --- | --- | --- |
| M1 embedding | 原 embedding endpoint；绕过 structured gate | 不是结构化生成请求 |
| Dreamer | Responses JSON Object，reasoning none | 历史行为稳定；先不改 Prompt |
| Relevance | 独立 Responses JSON Object，reasoning low；同批一次无 continuation repair | 保留已确认的隔离/fail-open设计 |
| M2 Field | Chat Completions JSON Object，thinking disabled | 恢复最后稳定 transport |
| M2 N9 | Chat Completions JSON Object，thinking disabled | 直接解决主 `invalid_json` 来源 |
| M3 Grounder | Responses JSON Object，reasoning none | 先恢复基线，再独立验证 low |
| M3 Parent | Responses JSON Object，reasoning none | 避免低价值长 thinking 与等待 |
| M4 Judge | Responses JSON Object，reasoning none | 消除已确认的输出/latency膨胀 |
| Package V3 Initial | 保留当前独立配置，high | 不将 Package V3 与上游恢复绑在一起 |
| Package V3 Description | 保留当前独立配置，low | Description 已独立，且不是钟墙主因 |

模型 alias 也应分开：

- 非 Package 节点优先恢复最后稳定的 `deepseek-v4-flash` alias；
- Package V3 可继续固定 `deepseek-v4-flash-0731`；
- 在百炼当前是否仍解析旧 alias 不确定时，先做一个节点级 probe。若旧 alias 已不可用，保持 `-0731`，但仍恢复 transport/thinking，从而只隔离模型版本变量。

涉及文件：

- `src/cdecr/models.py`
- `src/cdecr/config.py`
- `src/cdecr/cli.py`
- `src/cdecr/ports.py`
- `.env.example`

实现要求：

- 用 stage/tier routing table 实现，不复制多套 adapter；
- strict/JSON Schema 实现保留但默认关闭；
- repair 必须继承原节点 transport，不得悄悄回到全局 Responses；
- model_calls metadata 必须记录 endpoint、output mode、effective reasoning、model alias。

## 8.2 P0-B：恢复阶段隔离的并发与压力控制

1. scheduler provider gate 不再跨 document、Field、N9、Parent、Package 共用可变 `_dynamic_limit`。
2. 每个 stage/lane 使用独立 limiter；stage 结束后不得把临时降容带到下一阶段。
3. M1 embedding 使用自己的并发/批量限制，不进入 structured LLM gate。
4. 恢复可配置的 target/hard/burst；初始建议沿用已验证值 `100/160/80`，而不是代码硬夹到 100/12。
5. 全链路只保留一个自适应控制器，删除 scheduler gate 与 tier limiter 的双重乘法收缩。
6. 只有明确的 429、provider overload、连接池饱和才触发降容。
7. 以下错误不得触发全局降容：
   - `invalid_json`；
   - data-inspection/review；
   - arrearage；
   - schema/business validation；
   - 单条 payload error。
8. data-inspection 对同一 payload 视为不可通过重试治愈：最多换 key/endpoint 做一次受控尝试，仍失败则只标该文档/条目 retryable，不进行三次相同请求，也不降低其他 lane 并发。
9. recovery 采用时间冷却或连续少量成功后的快速回升，禁止“100 成功才 +5”导致整轮停留在并发 4。

涉及文件：

- `src/cdecr/scheduler.py`
- `src/cdecr/bulk_epoch/executor.py`
- `src/cdecr/bulk_epoch/engine.py`
- `src/cdecr/cli.py`
- `src/cdecr/bulk_epoch/embedding.py`
- `src/cdecr/provider_resilience.py`

必须新增物理 attempt telemetry：

```text
business_call_id
attempt_index
stage/lane
queued_at / started_at / ended_at
gate_wait_ms / backoff_ms
active_limit_before / active_limit_after
key_fingerprint（不可记录 key）
provider_error_class
retry_decision
```

Parent stage 增加合理的阶段 deadline。超时后保留当前合法 proposal/group，局部标记 retryable，不得让整个 30 篇在不可见等待中挂 20 分钟。

## 8.3 P0-C：修复 JSON Object 失败放大

1. N9 main/batch repair/item repair 全部走恢复后的 M2 Chat JSON Object。
2. Field main/repair 同样走 M2 Chat JSON Object。
3. 解析器只允许两类低风险确定性规范化：
   - 删除唯一、完整的 Markdown code fence；
   - 在全文恰好只有一个平衡 JSON object 时提取该 object。
4. 规范化后仍必须经过 Pydantic、ID coverage 和业务 validator。
5. 多 object、缺失 ID、歧义修复不得被“尽量解析”吞掉，继续走现有局部 repair/fallback。
6. 持久化受限诊断，不保存整份敏感原文：
   - response length；
   - finish reason；
   - first/last 128 chars 的哈希与字符类别；
   - JSON parse offset/type；
   - 是否 fence、是否截断、是否多 root；
   - provider response id。
7. repair 只重试非法 assessment/item，不重跑整批或整文档。

验收门槛：

- N9 main structured parse success ≥95%；
- 最终 N9 fallback singleton ≤1%；
- coverage=100%；
- repair 不能再次使用与主请求不同的隐藏 endpoint；
- 一次非法输出最多触发一次 batch-local repair，再按 item 局部化。

## 8.4 P1：Mention 质量恢复，不把成本问题错修成业务逻辑问题

第一阶段不修改 Grounder/Judge 业务 Prompt。先只恢复 model/transport/thinking，再对固定输入重放。若 Mention 仍然回归，再做以下最小语义调整：

1. 对当前 430 条 Dreamer candidates 冻结回放 Relevance shadow/enforce，逐条与 Mention Gold 对齐。
2. 任何 Gold 正例或 judgeable 相关候选被误删，都先留在下游；Relevance Enforce 只有在 candidate recall 不下降时恢复。
3. Dreamer 恢复 target material 的**排序优先级**，但不设为硬过滤：

   > When the candidate cap is binding, prioritize concrete claims materially relevant to the target entity, while retaining concrete indirect events with a supported economic or market link.

   这避免恢复旧 Prompt 时把开放世界的间接相关事件全部压掉。
4. 两篇 provider data-inspection 文档必须保留为 `FAILED_RETRYABLE`，不得以空输出或部分输出计成功；恢复时只重跑失败文档，不重跑已完成 28 篇。
5. 用冻结 Dreamer candidates 分别评估 Grounder、Judge，区分 missing candidate、Grounder reject、Judge split/reject，不再用最终 Mention P/R 反推单个节点。

## 8.5 P1：Atomic 质量恢复

1. 在同一冻结 Mention/Field snapshot 上做 N9 transport A/B，确保只改变 Chat/Responses 变量。
2. 对 27 条本轮 degraded singleton 逐条回放，统计恢复 transport 后：
   - 可正常解析比例；
   - MERGE/CREATE_NEW 变化；
   - Pair Precision/Recall；
   - singleton 变化；
   - hard-cannot-link violation。
3. 只有 transport 恢复后仍存在的语义误判，才允许进入 N9 Prompt/sidecar 修复；不得用 Prompt 修复 transport error。
4. 继续保留 assessment/item 级局部降级，不恢复整 task/整文档失败。

## 8.6 明确保留与明确撤销

保留：

- Package Workflow V3 Global Clustering、Rolling Registry、CAS、idempotency；
- Package V3 Initial/Description 参数独立；
- Relevance 独立请求、同批一次 repair、逐项 fail-open；
- strict/JSON Schema 的实现和节点开关，但默认关闭；
- Field 批处理、Bulk stage graph、确定性 Apply 等性能优化；
- 单条/单 assessment 失败不扩大为整文档失败；
- 当前 Pydantic、coverage、业务 validator；
- 审计与 token/latency telemetry。

撤销或替换：

- “所有结构化节点默认 Responses JSON Object”的全局策略；
- 通用 M3 low / M4 high 的 effective thinking；
- document→bulk 共享的自适应 dynamic gate；
- structured gate 包裹 M1 embedding；
- 广义 `provider_error` 触发全局并发收缩；
- scheduler 与 tier limiter 双重收缩；
- 用同一失效 transport 重复 batch/item repair。

暂不处理：

- Package V3 Prompt/Schema 业务重写；
- Judge reasoning 字段协议；
- Mention/Atomic 大规模 Prompt 改造；
- MU300；
- 为追求召回而放松 hard boundary。

## 9. 一步到位实施顺序

虽然按 P0/P1 区分风险，执行时一次性落地，但每一组必须独立 commit，便于快速定位回归。

### Commit 0：取证快照

- 保存当前未提交 CDECR 状态；
- 明确排除无关 D1/Pilot 用户改动；
- 记录当前报告、SQLite SHA256 和固定 30 篇 manifest。

### Commit 1：节点级 transport/thinking 恢复

- 实现 routing matrix；
- M2 Chat，M3/M4 Responses none；
- Package V3 保持独立；
- 补 endpoint/effective reasoning 审计。

### Commit 2：并发与 provider resilience 恢复

- phase-local gate；
- M1 bypass；
- 单 adaptive controller；
- 精确 pressure classifier；
- physical-attempt telemetry；
- Parent deadline/局部 fallback。

### Commit 3：JSON diagnostics 与局部 repair 收口

- 有限规范化；
- 主/repair transport 一致；
- parse diagnostics；
- N9/Field 局部 repair 测试。

### Commit 4：Relevance/Mention 恢复控制

- frozen replay 工具；
- Enforce release gate；
- Dreamer cap tie-breaker；
- failed document retry semantics。

## 10. 验收设计

### Gate 0：无真实模型回归

必须证明：

- 每个节点走预期 endpoint/output mode/effective reasoning；
- strict 默认关闭；
- M1 不进入 structured gate；
- stage 切换时 dynamic limit 不继承；
- data-inspection/invalid_json 不降低全局并发；
- retry 次数和局部化边界正确；
- Judge/N9/Dreamer Schema hash 未被无意改变；
- Relevance 输出 ID 恰好覆盖输入 candidate 一次；
- Package V3 initial/description 路由不受非 Package 恢复影响。

### Gate 1：冻结节点真实模型 A/B

使用历史落库真实 payload，不重跑全文：

1. N9：至少 10 个正常 batch + 本轮 27 个 degraded task；对比 Chat vs Responses。
2. Field：覆盖长 ID、多 field type、batch repair。
3. Judge：同一输入对比 none/low，不再测试 high 作为默认候选。
4. Grounder：同一输入对比 none/low。
5. Relevance：固定 430 candidates 做 shadow/enforce Gold replay。

节点门槛：

- JSON parse ≥99%（小样本要求 0 失败）；
- ID coverage=100%；
- Judge output Token 不超过 V2.1 同规模基线的 2 倍，且决策质量不下降；
- N9 main invalid ≤5%，最终 fallback ≤1%；
- Relevance candidate recall 不下降。

### Gate 2：冻结 30 篇上游回放

- 固定 Dreamer candidates 验证 Relevance→Grounder→Judge；
- 固定 Mention/Field 验证 N9；
- 不启动 Package V3 全局重聚类，以避免模型波动掩盖上游因果；
- 逐条复核本轮 76 个 Dreamer missing、42 个 partial 和 27 个 N9 degraded case。

### Gate 3：新的 30 篇全流程

性能门槛：

- 30/30 成功；
- 钟墙目标 ≤25m，硬门槛 ≤30m；
- 无真实 429 时，各 stage final limit 不低于配置 target 的 80%；
- queue wait < 总钟墙 10%；
- 任一阶段无无法解释的 >2m 静默空档；
- total token ≤2.3M；
- Judge total-token 占比 ≤12%，latency 占比 ≤10%；
- failed calls 消耗 Token 占比 ≤3%。

质量门槛：

- Mention 至少恢复到 R5 的 P/R/F1 水平，且任何单项不得再下降超过 1.5pp；
- candidate coverage=100%；
- Field 总体和 predicate/participant/metric 不低于 R5 可比口径；
- N9 Pair Precision/Recall 不低于最后正常可比运行；
- N9 降级 singleton ≤1%；
- Atomic singleton 与 hard-conflict violation 不劣于 V2.1 R3；
- Package V3 Pair Precision ≥90%、Recall ≥80%、F1 ≥85%，同时报告 Gold coverage；
- Package singleton 漏合率和大簇误成员率必须由独立逐包审查给出，不能只用低覆盖 Gold 宣称通过。

若 Gate 3 失败，只回滚对应的独立 commit，不整体回到某个历史版本。

### Gate 4：MU300 准入

仅在 Gate 3 全部通过后启动。300 篇首先验证的是扩展性与失败韧性，不应继续承担“顺便发现 30 篇已经存在的 transport/gate 回归”的职责。

## 11. 风险与平衡判断

### 恢复 M2 Chat 的风险

风险：与“全节点统一 Responses”架构不一致。  
判断：统一接口不是业务目标；当前 N9 59 次 invalid 已证明统一带来真实损失。routing table 比堆叠 repair 更简单、更稳定。

### 下调 Judge thinking 的风险

风险：可能降低少数复合 Mention 的判断质量。  
控制：先做同 payload none/low A/B；本轮 FN 只有 1 条归因 Judge，而 Judge 成本增长 30 倍，因此默认 none 的收益明显大于风险。

### Relevance 暂时 shadow 的风险

风险：候选量和 Grounder 成本增加。  
控制：仅在固定 replay 发现误删时 shadow；协议与一次局部 repair 保留。不能为节省少量 token 牺牲 Mention Recall。

### 提高并发的风险

风险：真实 429 或 provider overload。  
控制：不是取消限流，而是阶段隔离、精确分类和单控制器；真实 overload 仍可降容，但单条 JSON/data-inspection 错误不能拖垮全局。

## 12. 最终恢复判定

本轮不建议执行任何整 commit 回滚。应恢复的是以下四类**历史已验证行为**：

1. M2 Chat JSON Object + thinking disabled；
2. M3/M4 Responses JSON Object + effective reasoning none；
3. phase-local、M1 隔离、不会跨阶段塌缩的并发控制；
4. 非法输出只局部 repair/fallback，不用同一失效 transport 反复放大。

同时保留 Package V3、Relevance 独立请求、确定性批处理和失败局部化。这样既能恢复 18–25 分钟级钟墙与正常 Judge 成本，也不会丢掉近期真正有价值的业务与韧性改造。

当前最严重、必须先修复后才能继续压测的三项是：

1. shared provider gate 收缩到并发 4；
2. M4 Judge `high` 导致约 30 倍输出膨胀；
3. M2 N9 Responses JSON Object 导致主请求和 repair 连续 `invalid_json`。

这三项完成并通过冻结 A/B 前，不应启动新的 MU300。
