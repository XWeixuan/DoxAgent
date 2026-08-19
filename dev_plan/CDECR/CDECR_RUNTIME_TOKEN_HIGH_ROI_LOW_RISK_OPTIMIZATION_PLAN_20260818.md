# CDECR Runtime Token 高收益、低风险优化方案

日期：2026-08-18  
适用基线：当前未提交工作树；不得以 `HEAD` 覆盖现有 Package V3、模型路由、Provider resilience 或 Parent 简化实现。  
范围：降低 CDECR 全流程真实模型 Token 成本，同时不显著增加钟墙、不扩大复杂 LLM 上下文、不大改业务语义。  
状态：可直接实施的工程方案；本文不包含真实模型验收结果。

---

## 0. 总体结论

当前最有性价比的降本路径不是继续删 Prompt、删 Schema 说明、全局降低 thinking 或扩大复杂节点 batch，而是：

1. 消除已经失去业务必要性的 Parent Repartition 请求；
2. 把 Field 当前“有 batch 协议、但大量单条调用”的机会式凑批改成真正的 epoch 计划后批处理；
3. 在 Grounder item repair 前处理能够无损、确定性归一的输出形状错误；
4. 保持 N9 每批 3 条 Mention 不变，只优化 Mention 的装箱顺序，减少同批重复 Atomic card；
5. 为 Parent Induction 使用只包含模型所需字段的 Wire DTO，不改内部对象和持久化模型；
6. 修正 Relevance effective reasoning、continuation 与 processing-key 的一致性，确保已经关闭的 thinking 不被旧 checkpoint 或错误 telemetry 掩盖。

明确不做：

- **不实施 Grounder/Judge 请求内 Evidence 字典。**
- 不删除 Grounder/Judge 当前必要的文档上下文、Evidence 原文或直接证据定位。
- 不把模型改成从一份共享大上下文中自行寻找每条任务所需证据。
- 不扩大 N9、Grounder、Judge 的复杂任务 batch 上限。
- 不全局删除 Schema `description/default/examples`，不删任务边界 Prompt。
- 不启用 JSON Schema strict；继续使用当前 JSON Object + 本地 Pydantic/coverage/业务 validator。
- 不把不同文档的 repair 合并成一个大请求。
- 不恢复跨语义节点的 `previous_response_id` continuation。
- 不以切换便宜模型作为本轮降本手段。

以最近一次完整30篇真实运行的 1,718,846 Token 为可核基线：

- 当前已经落地、但尚未完成同代码口径实测的 Parent/Relevance 改动，机械推算可降至约 1.53M；
- 完成本文 P0 后，预计约 1.35M–1.42M；
- 完成本文 P1 且 Grounder 窄规则通过 shadow 后，预计约 1.30M–1.38M；
- 相对最近完整基线，预计下降约 20%–24%；
- 300篇按线性一阶估算约 13.0M–13.8M，考虑跨文档密度和滚动 Registry 增长，MU300 第一阶段硬门槛设为不超过 15M。

以上均是工程估算，不得替代同一固定语料、同一模型、同一 Prompt/Schema 的真实 A/B。

---

## 1. 可核基线与口径边界

### 1.1 最近一次完整真实运行

基线报告：

- `dev_plan/CDECR/CDECR_SELECTIVE_RECOVERY_30_REAL_ACCEPTANCE_REPORT_20260818.md`
- Registry：`.tmp/cdecr/mu30_selective_recovery_20260817_r1.sqlite3`

主要数据：

| 指标 | 数值 |
| --- | ---: |
| 文档成功率 | 30/30 |
| 模型调用 | 556 |
| Input Token | 1,325,375 |
| Output Token | 393,471 |
| 总 Token | 1,718,846 |
| 首轮钟墙 | 1,063,198 ms / 17.72 min |

按业务节点重新聚合 `model_calls`：

| 节点族 | Token | 占比 | 主要浪费形态 |
| --- | ---: | ---: | --- |
| Atomic/N9 | 451,070 | 26.24% | 每批固定协议重复、Atomic card 跨批重复 |
| Grounder及repair | 386,551 | 22.49% | 34次 item repair 消耗143,807 Token |
| Parent | 310,323 | 18.05% | Repartition及repair消耗154,507 Token |
| Judge及coverage recovery | 233,486 | 13.58% | 主要是必要语义成本，低风险压缩空间有限 |
| Dreamer/Relevance | 152,095 | 8.85% | Relevance hidden reasoning 较高 |
| Field | 148,423 | 8.63% | 140次请求中125次只有1个item |
| Package V3 | 35,857 | 2.09% | Token不高，但单次全局聚类钟墙很长 |

### 1.2 当前代码已经漂移，不能把推算当实测

最近完整运行之后，当前工作树又发生了两项与成本直接相关的变化：

1. `src/cdecr/parent_occurrence.py::build_parent_occurrence_pool()` 已停止运行 Repartition；
2. Relevance 当前真实请求已使用 `reasoning_effort="none"`、`previous_response_id=None`、`session_cache=false`。

最近实测中：

- Parent Repartition + repair = 154,507 Token；
- Relevance reasoning = 33,760 Token。

两项合计188,267 Token，占最近总量10.95%。如果当前代码行为与预期一致，总量机械推算约为1.53M。但必须用新的30篇 fresh Registry 验证，原因包括：

- Parent简化可能改变 Package 输入与质量；
- `SingleDocumentProcessor.model_config()` 仍存在报告旧 Relevance effort 的风险；
- checkpoint/processing key若未纳入有效配置，可能误复用旧输出；
- 当前 Package V3 effort 也与最近真实运行不完全相同。

因此本文将“确认当前已落地改动”列为 P0，而不是把188,267 Token直接写成既成收益。

### 1.3 当前质量不是可无条件牺牲的稳定基线

最近完整30篇虽恢复了性能，但质量仍有已知问题：

- Mention Precision/Recall仍不理想；
- Field total accuracy未达到既定门槛；
- 最终存在30-Mention复合Atomic和407个hard-cannot-link pair violation；
- Package Precision较高，但Recall与碎片化仍未完全通过。

因此本文禁止以下“表面降本”动作：

- 提高 N9 batch 体积；
- 缩短 Atomic identity 边界说明；
- 用更低档模型替换复杂节点；
- 通过减少候选或Evidence来压Token；
- 取消必要的局部 repair/fail-open。

---

## 2. 实施原则

### 2.1 语义分级、一次落地、独立开关

方案保留 P0/P1 的风险语义，但可以在一个实现周期一次性落地。每项必须有独立配置开关、独立 telemetry 和独立回滚能力，禁止用一个总开关把所有行为绑定。

建议新增配置：

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `CDECR_FIELD_EPOCH_PLANNED_BATCHING` | `true` | 启用 Field epoch 计划后批处理 |
| `CDECR_GROUNDER_SAFE_NORMALIZATION` | `true` | 启用确定性Grounder形状归一 |
| `CDECR_GROUNDER_PRIMARY_NORMALIZATION` | `shadow` | PRIMARY quantity窄规则先shadow |
| `CDECR_N9_OVERLAP_BATCH_PACKING` | `true` | 保持batch=3的候选重叠装箱 |
| `CDECR_PARENT_COMPACT_WIRE_DTO` | `true` | 启用Parent模型专用精简输入DTO |

不得增加“发生任何局部异常就整篇失败”的新硬校验。所有新优化失败时都必须回退到原有节点路径或原有单条repair。

### 2.2 不修改业务 Prompt

本轮不修改 Dreamer、Relevance、Grounder、Judge、Field、Atomic、Parent、Package V3 的业务 Prompt 文本。

原因：本轮收益来自减少重复请求和确定性冗余，不需要通过删解释压缩模型注意力。Parent Wire DTO只改变输入序列化，不改变任务定义和输出Schema。

### 2.3 版本与checkpoint必须闭环

下列版本材料必须纳入对应任务的 processing key、checkpoint input hash 或 stage contract：

- Field planned-batching version；
- Grounder normalization policy version；
- N9 batch-packing version；
- Parent compact-wire version；
- Relevance effective reasoning、transport、continuation和session-cache配置。

批次边界变化不应使语义相同的已完成单项任务永久失效；但不得让旧批次输出伪装成新Wire/新策略结果。

---

## 3. P0-0：确认并闭环当前已落地的 Parent/Relevance 降本

### 3.1 Parent Repartition保持关闭

实现要求：

- V3主路径继续只运行 Parent Induction → Parent Proposal Pool；
- 不调用 `parent_induction_repartition`；
- 旧checkpoint只保留历史记录，不读取、不删除；
- telemetry固定记录：
  - `repartition_enabled=false`；
  - `suspect_group_count=0`；
  - `repartition_request_count=0`；
  - `repartition_repair_count=0`；
- 不顺带修改Parent Prompt、输出Schema、failure-localization或Package V3输入合同。

收益：按最近运行机械消除154,507 Token，约8.99%，并减少模型钟墙。  
风险：不增加代码复杂度，但可能改变Package质量；必须看误合并、Recall和碎片化，不能只看Token。

### 3.2 Relevance effective config闭环

当前真实请求应固定为：

- Responses JSON Object；
- `reasoning_effort="none"`；
- `previous_response_id=None`；
- `session_cache=false`；
- 主批失败时仅同批一次独立repair；
- repair仍失败则逐项fail-open，不重跑Dreamer、不错误删除candidate。

修正要求：

1. `SingleDocumentProcessor.model_config()` 必须报告真实effective值，不能继续写`low`；
2. processing key必须包含effective reasoning、transport、continuation与session-cache；
3. model call telemetry必须能证明每次请求无continuation、reasoning=none；
4. 继续执行“每个输入candidate恰好一个输出ID”的coverage validator；
5. 不增加确定性auto-drop或auto-keep规则，本轮不改变Relevance业务语义。

收益：最近运行中最多约33,760 reasoning Token，约1.96%；预计缩短钟墙。  
风险：低到中，主要是模型决策可能随thinking变化；必须做冻结candidate A/B。

---

## 4. P0-1：Field改为真正的 Epoch Planned Batching

### 4.1 当前问题

当前已经有namespace batch Wire协议和以下上限：

- 普通namespace每批最多12项；
- `participant.unknown`每批最多8项；
- 另有payload bytes上限。

但 `_model_decision()` 依赖40ms Timer机会式凑批。`CanonicalFieldResolutionService.resolve_epoch()` 又让各semantic group并发完成候选准备，导致任务零散到达：

- 140次模型调用处理191项；
- 平均batch=1.36；
- 125/140请求为singleton；
- `predicate` 54次请求只处理55项；
- `metric` 53次请求只处理56项。

这不是batch上限过小，而是计划屏障没有真正接通。

### 4.2 目标结构

改为：

```text
Immutable Epoch Snapshot
  -> Prepare：完成全部确定性匹配、query embedding、候选召回
  -> Collect：收集确实需要LLM的FieldDecisionPlanItem
  -> Batch：按namespace、现有item/bytes上限稳定装箱
  -> Decide：并发执行已装满的批次
  -> Validate：逐task coverage与candidate ID校验
  -> Apply：按稳定semantic task order写入link/registry
```

`FieldDecisionPlanItem`建议只包含：

- `semantic_task_id`；
- namespace；
- 当前 `FieldCoreferenceInput`；
- 冻结候选列表；
- run/task审计信息；
- completion future或结果槽位。

不得在plan阶段提前创建canonical field或写link。

### 4.3 保持现有语义的关键约束

当前 `begin_epoch_snapshot()` 已经明确使用immutable epoch inventory，且候选准备应发生在模型调用之前。本次必须延续这一合同：

1. 所有model-bound task基于同一个epoch snapshot；
2. 同semantic key仍只判断一次并fan-out；
3. batch不混namespace；
4. batch item与bytes上限保持现值，不提高；
5. Apply按稳定的`semantic_task_id`排序；
6. 同批多个`CREATE_NEW`在Apply前走现有canonical uniqueness/版本检查；
7. 若Apply发现目标已由同epoch先前结果创建，则复用目标，不重复创建；
8. 不允许批次完成顺序决定Field ID或link结果。

### 4.4 失败语义

- provider/auth/arrearage等整批失败：保持当前batch failure局部化，不递归放大请求；
- root JSON或batch coverage非法：最多按当前规则拆分一次；
- 单个task非法：只fallback/repair该task，不重跑合法task；
- task失败不得扩大成整篇或整个epoch失败；
- resume继续以semantic task为粒度跳过已完成项，不以物理batch ID作为唯一恢复单位。

### 4.5 Telemetry

新增或补齐：

- `planned_item_count`；
- `physical_batch_count`；
- `batch_size_min/p50/p95/max`；
- `singleton_batch_count`；
- `payload_bytes_min/p50/p95/max`；
- `batch_split_count`；
- `item_fallback_count`；
- `candidate_card_unique_count`与复用率；
- planned、decide、apply三段墙钟。

### 4.6 预期收益与风险

- 预计调用数：140 → 20–30；
- 预计节省：65k–85k Token/30篇；
- 占最近总量：3.8%–5.0%；
- MU300线性节省：0.65M–0.85M；
- 钟墙：预计下降，不应增加；
- 业务复杂度：中等编排重构，业务判断不变；
- 质量风险：低，主要风险来自batch装箱/Apply顺序和resume实现错误。

---

## 5. P0-2：Grounder Repair 前的安全确定性归一

### 5.1 当前问题

最近运行34次 `grounder_item_repair` 消耗143,807 Token。错误分类：

| 类型 | 次数 | 判断 |
| --- | ---: | --- |
| `PRIMARY_QUANTITY_COUNT` | 24 | 含语义边界，不能全量确定性修正 |
| `EVIDENCE_FIELD_SHAPE` | 6 | 多数可无损修正 |
| 其他Schema形状错误 | 4 | 部分可无损修正 |

真实坏形状包括：

- Evidence location携带已弃用的`start_char/end_char`；
- 数值字段输出为无歧义的纯数字字符串；
- 可选字段出现空字符串、空容器或兼容旧别名。

这些错误若能通过lossless normalization恢复，就不应再次发送完整单条Grounder任务。

### 5.2 新增 `normalize_grounder_draft_shape()`

执行位置：Grounder root JSON成功解析后、`GrounderDraft`最终Pydantic/业务validator之前；只处理单个draft，不处理整批。

允许的第一阶段规则：

1. 删除Evidence location中未被当前合同消费的旧`start_char/end_char`；
2. 仅将满足严格正则、无单位、无千分位歧义、无范围表达的数字字符串转换为int/float；
3. 将当前Schema明确允许为null的空字符串规范为null；
4. 对确定的一对一旧字段别名做映射，发生冲突时不覆盖新字段；
5. 不修改Evidence原文、segment/block引用、predicate、participant、metric、period或assertion语义。

返回：

```text
UNCHANGED
NORMALIZED_VALID
NORMALIZED_STILL_INVALID
UNSAFE_TO_NORMALIZE
```

后两类继续走现有单条repair，不允许静默丢draft。

### 5.3 PRIMARY quantity窄规则

第一轮只做shadow，不自动写结果。只有同时满足以下条件才记为`safe_candidate`：

1. 当前draft有且仅有一个quantity与primary metric明确绑定；
2. 其余quantity已经有comparison、bound、supporting或secondary语义；
3. 不需要猜测metric、单位、period或字段对应关系；
4. 不需要删除任何quantity；
5. 规范化后的draft通过全部原有validator。

shadow需记录：

- 若启用会避免多少repair；
- 与真实repair后结果的字段级一致率；
- 不一致集中在哪些metric/quantity形态；
- 是否出现候选错误升级为primary。

启用门槛：至少在固定30篇和额外开放世界样本上，和repair后结果字段级一致率≥99.5%，且0个Evidence/metric/period语义漂移。未达到则永久保持shadow。

### 5.4 审计

只记录短审计，不复制payload：

- draft短ID；
- normalization rule codes；
- before/after shape hash；
- validator result；
- 是否避免LLM repair。

### 5.5 预期收益与风险

第一阶段只启用确定性shape规则：

- 至少避免约10/34 repair；
- 预计节省约42k Token/30篇，约2.4%；
- 钟墙下降；
- 业务复杂度低，质量风险很低。

PRIMARY窄规则通过shadow后：

- 总节省预计70k–95k Token/30篇；
- 约4.1%–5.5%；
- 风险低到中，必须由shadow门槛控制。

---

## 6. P1-1：N9 Candidate-overlap Batch Packing

### 6.1 不扩大batch

`ATOMIC_DECISION_MENTION_BATCH`继续固定为3。本轮绝不改为6或更大，原因是：

- Atomic输入与判断复杂；
- 最近运行已出现30-Mention污染簇；
- 407个hard-cannot-link pair violation说明当前质量没有扩大上下文的余量。

### 6.2 当前浪费

N9已在单次请求内使用短ID和Atomic字典，但Mention仍按原始顺序每3条切分。相邻Mention的candidate集合未必重叠，导致同一Atomic card在多个请求重复发送。

离线检查当前候选引用：

- 顺序分组unique Atomic card累计815；
- candidate-overlap贪心装箱后为548；
- 重复card理论下降32.8%。

### 6.3 确定性装箱算法

前提：必须先完成全部Mention的N7候选规划并冻结candidate snapshot，之后才能装箱。若当前路径仍存在“前一个N9 Apply改变后一个Mention候选”的实时依赖，则该模式不得启用，必须先保持原顺序。

算法：

1. 按稳定Mention ID排序未分配任务；
2. 取最小Mention ID为seed；
3. 在剩余任务中按以下score选最多2条：
   - shared candidate count优先；
   - 再按Jaccard overlap；
   - 再按候选总长度较短；
   - 最后按Mention ID稳定tie-break；
4. batch仍恰好最多3条；
5. 每条Mention的candidate集合、顺序、hard boundary和payload内容不变；
6. 所有模型结果先验证，再按原始稳定Mention顺序Apply；
7. 模型请求完成顺序不得影响最终Atomic版本和redirect。

装箱只改变“谁与谁共享一个请求”，不改变任何候选或决策规则。

### 6.4 Telemetry

- sequential baseline unique-card count；
- packed unique-card count；
- card reuse reduction ratio；
- batch overlap p50/p95；
- Mention exact coverage；
- candidate exact coverage；
- batch size必须继续≤3；
- Apply顺序hash。

### 6.5 预期收益与风险

- 预计节省12k–30k Token/30篇；
- 占最近总量0.7%–1.7%；
- Output Token基本不变；
- 钟墙不变或略降；
- 代码复杂度低到中；
- 质量风险低，但必须验证请求分组变化不影响模型裁决分布和Apply结果。

---

## 7. P1-3：Parent Induction 模型专用 Compact Wire DTO

> 编号说明：原候选方案P1-2“Grounder/Judge Evidence字典”已明确取消并保留为空缺编号；本节继续使用原编号P1-3，不包含任何Evidence字典化。

### 7.1 当前问题

内部 `AtomicDocumentSlice` 同时服务编排、审计和模型输入，携带模型不需要的程序字段：

- 长 `event_id`；
- 重复 `document_ref`；
- `document_fingerprint`；
- 空cue数组/空可选字段；
- 可由请求内映射恢复的持久化标识。

这些字段每个Atomic slice重复出现，但模型只需要理解当前文档中的Atomic事实、family/time、已知cue、role和直接Evidence引用。

### 7.2 新Wire DTO

内部/持久化DTO保持不动，新增仅用于模型输入的结构，例如：

```json
{
  "atomic_ref": "a1",
  "proposition": "...",
  "family": "...",
  "time": {...},
  "cues": {...},
  "role": "...",
  "evidence_refs": ["e1"]
}
```

规则：

- 完整Atomic ID只保留在编排层`a1 -> atomic:*`映射；
- document task本身已确定文档时，不在每个slice重复fingerprint；
- `exclude_none=true`；
- 空列表和空cue对象不发送；
- proposition、time、所有已知identity cues、role和现有Evidence引用全部保留；
- 不把多个Atomic的Evidence合并成共享大字典；
- 模型输出继续使用request-local短ID；
- validator恢复完整ID后继续执行exact coverage和业务校验。

### 7.3 版本与checkpoint

- bump Parent input wire/compiler version；
- checkpoint input hash纳入Wire版本；
- 旧checkpoint不得被新Wire误复用；
- Parent输出合同、Prompt和Schema不变；
- Package V3 occurrence DTO不变。

### 7.4 预期收益与风险

- Parent Induction input预计减少10k–20k Token/30篇；
- 占最近总量0.6%–1.2%；
- 钟墙略降；
- 业务逻辑不变；
- 实现复杂度中等、质量风险低。

---

## 8. 明确取消的 Evidence 字典方案

本轮不得实现以下结构：

```text
Grounder/Judge共享segments/evidence字典
Candidate或Draft只携带Evidence ref
模型再去共享上下文中查找具体证据
```

即使该方式可能节省30k–50k Token，也不纳入收益预测。原因：

- Grounder/Judge是高注意力密度节点；
- 让模型跨payload位置恢复每条任务的直接证据可能增加检索负担；
- 容易造成Evidence错位、participant/metric/period字段错配；
- 当前Mention质量尚不稳定，不应在此时增加注意力分散风险。

现有Grounder/Judge Evidence原文、直接上下文和Schema解释保持不变。

---

## 9. 代码修改清单

### 9.1 必改文件

| 文件 | 修改 |
| --- | --- |
| `src/cdecr/config.py` | 新增独立优化开关及默认值 |
| `src/cdecr/canonical_field_resolution.py` | Field Prepare/Collect/Apply计划屏障、稳定任务顺序、telemetry |
| `src/cdecr/field_coreference.py` | 接收显式planned batches；保留legacy timer作为关闭开关后的fallback |
| `src/cdecr/single_document.py` | Grounder安全shape normalizer、shadow PRIMARY统计、Relevance model_config修正 |
| `src/cdecr/cross_document.py` | N9 overlap packing、稳定Apply、WIRE/POLICY版本材料 |
| `src/cdecr/parent_occurrence.py` | Parent compact model Wire DTO及版本hash |
| `src/cdecr/relevance_filter.py` | 确认所有实际入口none/no-continuation/no-session-cache |
| `src/cdecr/cli.py` | 配置解析、effective routing/telemetry透传 |
| `src/cdecr/bulk_epoch/engine.py` | stage telemetry、processing key和resume版本材料 |
| `tests/cdecr/test_field_coreference.py` | Field批处理与失败局部化测试 |
| `tests/cdecr/test_bulk_deterministic_runtime.py` | epoch resume/idempotency、stable Apply测试 |
| `tests/cdecr/test_single_document.py` | Grounder normalization及repair fallback测试 |
| `tests/cdecr/test_relevance_filter.py` | effective reasoning、coverage与无continuation测试 |
| `tests/cdecr/test_bulk_epoch_v3.py` | N9 packing、阶段telemetry与完整覆盖测试 |
| `tests/cdecr/test_parent_occurrence_induction.py` | Compact Wire等价性与checkpoint版本测试 |
| `.env.example` | 新增开关说明，不改变provider/model/key |

### 9.2 不应修改

- Grounder/Judge业务Prompt；
- Grounder/Judge Evidence组织；
- Mention/Atomic/Package持久化模型；
- Field/N9/Parent输出Schema；
- Package V3 Initial/Rolling/Description Prompt和Schema；
- 当前模型名称、provider、key、并发总配置；
- N9 batch=3；
- 当前局部repair/fail-open边界。

### 9.3 Changelog

完成代码修改后，按仓库规则在`changelog`追加一条简要记录，至少包含：

- Field planned batching；
- Grounder safe normalization；
- N9 overlap packing；
- Parent compact wire；
- Relevance effective-config闭环；
- 对应测试结果。

不得覆盖或整理用户当前已有的changelog修改。

---

## 10. 测试方案

### 10.1 Field

必须覆盖：

1. 相同namespace按现有12/8上限装箱；
2. 不同namespace绝不混批；
3. item/bytes双上限同时生效；
4. 输入顺序变化不改变semantic task结果和Field link hash；
5. 同semantic key只调用一次并fan-out；
6. batch root非法只拆分一次；
7. 单item非法不重跑合法item；
8. provider失败不持久化虚假UNRESOLVED；
9. resume只重跑未完成semantic task；
10. Apply顺序不依赖模型完成顺序；
11. 开关关闭时完整回到legacy路径。

### 10.2 Grounder

必须覆盖：

1. 旧offset字段可被剥离且Evidence不变；
2. 无歧义数字字符串可无损转换；
3. 范围、百分号、单位、千分位歧义不自动转换；
4. normalization后仍非法则只repair单draft；
5. 不能安全判断PRIMARY时继续repair；
6. shadow规则不改变业务结果；
7. repair失败保留现有合法draft并且不使文档失败；
8. normalization审计不保存完整payload。

### 10.3 N9

必须覆盖：

1. 每个eligible Mention恰好进入一个batch；
2. batch始终≤3；
3. 每条Mention候选ID、顺序和数量完全不变；
4. candidate-overlap装箱稳定、输入顺序不敏感；
5. overlap card数不高于sequential baseline；
6. 模型结果按稳定Mention顺序Apply；
7. redirect/version不随异步完成顺序变化；
8. 开关关闭时恢复原顺序切批。

### 10.4 Parent

必须覆盖：

1. Compact Wire不包含完整event ID/document fingerprint；
2. 所有必要proposition/time/cue/role/evidence仍在；
3. 空字段不发送；
4. request-local短ID可无损恢复；
5. Compact/legacy喂入同一模型输出时形成相同Parent Proposal；
6. Wire版本变化使旧checkpoint不可复用；
7. Repartition调用数保持0。

### 10.5 Relevance

必须覆盖：

1. 每个入口均为JSON Object；
2. reasoning=none；
3. `previous_response_id=None`；
4. `session_cache=false`；
5. 输出ID恰好覆盖输入candidate一次；
6. 主批失败只对同批一次独立repair；
7. repair仍失败逐项fail-open；
8. effective config进入processing key和model-call telemetry。

### 10.6 静态与全量测试

至少执行：

```powershell
uv run pytest tests/cdecr/test_field_coreference.py tests/cdecr/test_bulk_deterministic_runtime.py -q
uv run pytest tests/cdecr/test_single_document.py tests/cdecr/test_relevance_filter.py -q
uv run pytest tests/cdecr/test_bulk_epoch_v3.py tests/cdecr/test_parent_occurrence_induction.py -q
uv run pytest tests/cdecr -q
uv run ruff check src/cdecr tests/cdecr
uv run mypy --strict src/cdecr
git diff --check
```

若仓库当前存在与本方案无关的历史失败，必须区分“本方案触发的失败”和“原有失败”，不得通过修改无关模块清理测试结果。

---

## 11. 真实验收顺序

### 11.1 Gate 0：静态与假模型

全部聚焦测试、CDECR全量测试、Ruff、strict mypy、diff check通过后才能调用真实模型。

### 11.2 Gate 1：节点级冻结A/B

#### Field

使用最近30篇冻结Field plan：

- A：legacy timer batching；
- B：planned batching；
- 模型、Prompt、Schema、candidate snapshot完全相同；
- 比较Field decision、link hash、call count、Token和wall。

门槛：

- Field link语义结果无非预期差异；
- call≤30；
- singleton physical batch≤10%；
- Field Token降低≥40%；
- wall不增加。

#### Grounder

对历史34个invalid draft离线回放normalizer：

- 第一阶段只验证shape normalizer；
- PRIMARY规则只做shadow；
- 检查normalization结果与真实repair后draft的字段级差异。

门槛：

- Evidence、predicate、participant、metric、period、assertion 0语义漂移；
- 至少安全消除8次历史repair；
- 不新增discarded draft。

#### N9

使用同一冻结candidate snapshot构造sequential和overlap两套物理batch：

- 先验证candidate exact equality；
- 再做有限真实请求A/B；
- Apply前逐Mention比较decision分布。

门槛：

- candidate coverage=100%；
- batch≤3；
- unique Atomic card下降≥20%；
- N9 merge precision/conditional recall无显著下降。

#### Parent

使用同一冻结Atomic/document slice：

- legacy wire与compact wire各跑一次；
- 比较Parent proposal partition、输入Token和wall。

门槛：

- proposal exact coverage=100%；
- 输入Token下降≥8%；
- Parent/Package质量无显著下降。

### 11.3 Gate 2：固定30篇完整真实验收

必须使用：

- 相同固定30篇snapshot与manifest；
- 当前百炼provider与既有key；
- 相同模型；
- 相同Prompt、输出Schema、并发、batch上限；
- 全新Registry，不复用历史业务结果；
- 完成后再做幂等二跑，要求0新模型调用。

效能门槛：

| 指标 | 硬门槛 | 目标 |
| --- | ---: | ---: |
| 成功文档 | 30/30 | 30/30 |
| 总Token | ≤1.42M | ≤1.35M |
| 首轮钟墙 | ≤18.6 min | ≤17.0 min |
| Parent Repartition调用 | 0 | 0 |
| Relevance reasoning Token | 0 | 0 |
| Field模型调用 | ≤30 | ≤25 |
| Field平均batch | ≥5 | ≥7 |
| Grounder item repair Token | ≤110k | ≤80k |
| 失败请求Token占比 | ≤1% | 0 |

质量护栏：

- Mention Precision/Recall相对可比基线各下降不超过1pp；
- Field total accuracy下降不超过1pp，各关键字段不得显著回归；
- N9 Merge Precision和conditional Merge Recall下降不超过1.5pp；
- Atomic hard-cannot-link violation不得超过当前可比基线，更不能形成新的超大污染簇；
- Package Pair Precision/Recall各下降不超过1pp；
- Package singleton漏合率、Micron earnings组件数和大簇误成员率不得恶化；
- Grounder/Judge candidate/draft exact coverage保持100%；
- 不得通过显著增加UNRESOLVED、rejected或fail-open来换取Token下降。

### 11.4 Gate 3：MU300

只有固定30篇通过后才进入MU300。MU300第一阶段门槛：

- 总Token≤15M，目标≤13.5M；
- 平均≤50k/文档，目标≤45k；
- 300/300文档完成或失败均能局部恢复；
- Field batch填充率不能随规模扩大而恶化；
- Grounder repair Token占比不得高于30篇；
- Package V3滚动Registry不能出现非线性payload膨胀；
- 每30分钟检查一次运行状态即可，不高频轮询。

MU300若因provider欠费、auth或外部中断停止，必须从持久化task/checkpoint恢复，不得全局从头重跑。

---

## 12. 回滚判定

### 12.1 Field planned batching

满足任一条件则仅关闭`CDECR_FIELD_EPOCH_PLANNED_BATCHING`：

- Field link hash出现无法由模型波动解释的系统性差异；
- 重复canonical field增加；
- provider batch拒绝导致拆分后Token反而增加；
- wall增加超过5%；
- resume出现重复Apply或漏任务。

### 12.2 Grounder normalization

- 任一Evidence/metric/period语义被改变：关闭对应rule，不整体关闭shape normalizer；
- PRIMARY shadow一致率低于99.5%：保持shadow，不启用；
- normalization造成draft丢失：整体关闭normalizer并保留原repair。

### 12.3 N9 overlap packing

- Merge Precision或conditional Recall下降超过1.5pp；
- 新增hard conflict或超大污染簇；
- Apply结果随任务完成顺序变化；
- unique card下降不足10%。

仅关闭packing，保留batch=3和现有Wire DTO。

### 12.4 Parent compact wire

- Parent proposal exact coverage不足100%；
- Package质量显著下降；
- Token下降不足5%；
- checkpoint出现误复用。

只回退模型输入序列化，不回滚Parent简化或Package V3。

---

## 13. 收益总表

| 优化项 | 30篇预计节省 | 相对最近总量 | 钟墙 | 复杂度 | 质量风险 | 性价比 |
| --- | ---: | ---: | --- | --- | --- | --- |
| 已落地Parent Repartition关闭 | 154.5k | 8.99% | 明显下降 | 低 | 中，需看Package | 很高 |
| 已落地Relevance no-thinking闭环 | 最多33.8k | 1.96% | 下降 | 低 | 低到中 | 高 |
| Field planned batching | 65k–85k | 3.8%–5.0% | 下降 | 中 | 低 | 很高 |
| Grounder安全shape normalization | 约42k | 2.4% | 下降 | 低 | 很低 | 很高 |
| Grounder PRIMARY窄规则 | 追加28k–53k | 1.6%–3.1% | 下降 | 中 | 低到中 | 需shadow后判断 |
| N9 overlap packing | 12k–30k | 0.7%–1.7% | 不变或略降 | 低到中 | 低 | 高 |
| Parent compact Wire DTO | 10k–20k | 0.6%–1.2% | 略降 | 中 | 低 | 中高 |

不计入收益：Grounder/Judge Evidence字典、Prompt删减、Schema说明删减、复杂节点扩大batch、模型降档、Package V3进一步降thinking。

最终合理目标：

```text
最近完整实测：1.7188M / 30篇
当前已落地改动推算：约1.5306M
P0完成：约1.35M–1.42M
P0+P1成熟：约1.30M–1.38M
MU300审慎目标：≤15M，目标≤13.5M
```

本方案的核心不是让模型承担更多“从上下文自行找信息”的工作，而是让每一份必要信息更少被重复发送、让能由程序无损修复的错误不再调用模型，并保持复杂业务判断的上下文和解释完整。
