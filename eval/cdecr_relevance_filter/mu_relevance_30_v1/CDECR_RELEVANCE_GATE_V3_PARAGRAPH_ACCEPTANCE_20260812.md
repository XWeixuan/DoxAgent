# CDECR Relevance Gate v3 显式 containing_paragraph 真实复验

日期：2026-08-12  
语料：MU relevance 30 v1，30篇 / 409 frozen Dreamer candidates  
模型：DashScope `deepseek-v4-flash-0731`  
主运行：`runs/frozen_gate_v3_paragraph_20260812_live1_retry1`  

## 结论

显式 `containing_paragraph` 成功压制了一批跨段错召，但造成更大的间接相关漏召，整体质量显著退化：

- v2 的11条 FP 中修复9条，但本轮新增3条，最终 FP 为5条。
- v2 的17条 FN 中修复6条、持续11条，同时新增27条，最终 FN 为38条。
- Relevant recall 从91.67%降至81.37%。
- Dropped-candidate precision 从91.10%降至82.57%。
- Valid accuracy 从92.80%降至88.95%。
- D021 的 Micron 低于20倍PE再次被误删，修正评分器后 Direct FN 为1/80，而不是旧报告逻辑显示的0。

该版本不能进入 Enforce，必须继续保持 Shadow。显式局部段字段本身应保留，因为它实现了可审计的上下文边界；但不能直接以当前 Prompt/单批分类行为晋级。

## 实现与预检

生产和冻结评测共用的 `RelevanceEvent` 现在显式包含：

- `statement`
- `exact_evidence`
- `containing_paragraph`

段落由 evidence 的 segment-local offsets 确定，在 segment 内使用最近单换行边界。不能直接发送整个预处理 segment：本语料的 segment 段落中位长度为3,809字符、最大7,900字符，因为源新闻大量使用单换行而非空行分段。

修正后409条均满足 exact evidence 位于显式 paragraph 内：

- paragraph字符中位数：329
- P95 / 最大值：2,430 / 2,430
- 1条候选有两个不同证据段，按证据顺序去重后以双段输入
- segment缺失或 evidence 跨越局部段边界时直接报错，不静默退回全文

Prompt v3 明确禁止从文章其他部分或其他候选继承上下文。模型仍能从前一轮 transcript 看见完整原文，但第二轮的业务合同和显式字段已经机械限定允许使用的上下文。

## 指标对比

两轮均使用同一 Gold v2（204 Relevant / 205 Irrelevant）、MU profile、模型、30篇输入与并发参数。

| 指标 | v2 | v3 explicit paragraph | 变化 |
| --- | ---: | ---: | ---: |
| Valid predictions | 389 | 389 | 0 |
| Fail-open | 20 | 20 | 0 |
| Simulated drops | 191 | 218 | +27 |
| Relevant recall | **91.67%** | 81.37% | -10.30pp |
| Relevant precision | 94.44% | **97.08%** | +2.64pp |
| Dropped-candidate precision | **91.10%** | 82.57% | -8.53pp |
| Valid accuracy | **92.80%** | 88.95% | -3.85pp |
| Direct FN | 0/80 | **1/80** | +1 |

仅389条有效预测的混淆矩阵：

| | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: |
| v2 | 187 | 11 | 17 | 174 |
| v3 | 166 | 5 | 38 | 180 |

### 切片

| 切片 | v2 recall | v3 recall | v2 drop precision | v3 drop precision | v2 accuracy | v3 accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hard indirect | **89.72%** | 69.16% | **72.50%** | 50.75% | **87.41%** | 75.52% |
| mixed | 86.96% | **89.13%** | 95.12% | **95.93%** | 94.01% | **95.21%** |
| mostly high | 100.00% | 100.00% | N/A | N/A | 100.00% | 100.00% |
| very low / none | N/A | N/A | 100.00% | 100.00% | 100.00% | 100.00% |

显式 paragraph 对 mixed 有小幅净改善，但 hard-indirect 大幅恶化，不能接受。

## v2错误的改善

### 修复6条旧FN

- D072 ×2：AMD hyperscaler deals、OpenAI 6GW GPU deployment。
- D190 ×3：SK Hynix NAND/HBM/DRAM市场份额；显式段落中产品市场或Micron竞争关系更加清楚。
- D293 ×1：Apple margin pressure；局部段落现在正确携带 memory-price 因果句。

### 修复9条旧FP

- D021 DeepSeek泛AI事件。
- D027 AMD独立股价/业绩列表项。
- D025 ×4：AMD整体 revenue/EPS/net income，以及Intel业绩与股价。
- D045 ×3：HPE EPS、Dell目标价、Dell/HPE一般盈利预期。

这些修复与设计目标一致：不再从其他段落继承AI、memory或data-center narrative，也不把局部共现当成传导路径。

## 持续错误

持续FN共11条：

- D072 AMD data-center revenue ×1。
- D095 Ciena earnings / AI optical component demand / DCI market ×3。
- D001 SanDisk估值/股价 ×2。
- D097 Samsung、SanDisk、Kioxia、SK Hynix评级/目标价 ×4。
- D102 RTX Spark PC SoC ×1。

这些仍说明 profile 不足以稳定建立GPU/光互联/AI-PC派生需求和内存同行竞争暴露。

持续FP共2条：

- D147 Supermicro泛称采购 hardware components。
- D167 Oracle FY27 EPS/收入指引。

即使显式局部段已经存在，模型仍可能依赖稳定知识或前轮全文，把泛硬件采购和Oracle整体指引扩展成MU路径。Prompt中的禁止继承不是强制的输入隔离。

## 新增错误

### 新增27条FN

| 聚类 | 数量 | 典型文档 | 追因 |
| --- | ---: | --- | --- |
| AMD AI/data-center派生需求 | 12 | D025 | Helios出货、Meta 6GW GPU、data-center sales、server demand、OpenAI/Meta部署等被集中误删。局部段提供事件，但模型不稳定地完成 `GPU/server -> HBM/DRAM -> MU`。 |
| Kioxia竞争/供需暴露 | 7 | D061 | 收入、利润率、现金流、Nanya供给协议等从正确变为不相关；窄上下文移除了文章中更宽的内存市场定位，而profile没有显式竞争图谱。 |
| SanDisk竞争事件 | 2 | D001 | SanDisk revenue/gross-margin guidance被拒，反映同行经营结果到MU预期的竞争传导不稳定。 |
| AI基础设施派生需求 | 2 | D138 | Broadcom AI sales、Oracle hyperscaler RPO再次漏召。 |
| 其他局部事件 | 4 | D021、D045、D102、D180 | 包括Micron直接估值、HPE memory-linked call、RTX Spark性能和产品里程碑；存在明显单次模型波动与过严判断。 |

D021 是明确的新 Direct FN：candidate statement 直接点名 Micron 是低于20倍PE的三家公司之一。它证明即使字段边界正确，当前模型单次判定仍不能保证Direct保护。

### 新增3条FP

- D167 Oracle创纪录Q4后股价下跌22.1%。
- D061 Kioxia ADR上涨460%。
- D061 Kioxia股价年内翻逾四倍。

这三条都是其他公司证券表现。Kioxia属于Micron的明确NAND竞争者，因此是否把其股价本身视为可更新MU相对估值/行业预期仍有边界敏感性；按已冻结Gold v2，它们没有局部具体传导路径，计为FP。

## 根因判断

1. **显式段落解决了上下文可审计性，但暴露了模型的间接路径能力不足。** 它成功修复多数宽上下文FP，却使hard-indirect recall下降20.56pp。
2. **稳定业务知识没有以结构化 exposure 输入提供。** `MU; DRAM, NAND and HBM`不足以稳定表达 AMD GPU/Ciena optical/AI-PC互补需求和SanDisk/Kioxia/SK Hynix竞争关系。
3. **批量输入重复局部段落增加了上下文负担。** 相比v2，成功调用input tokens从115,008增至161,821（约+40.7%）；重复paragraph可能加重候选间干扰和保守偏差。
4. **Prompt限制不等于物理隔离。** 冻结评测为了模拟两轮会话仍包含第一轮完整文章，模型理论上仍能违反指令使用全文；持续FP证明这种泄漏没有被彻底消除。
5. **单次模型波动仍存在。** 同一Prompt附近，D190三条从漏召变正确，而D021 Direct从正确变错误；需要重复运行或更强确定性保护，不能把所有迁移都解释为字段因果。

## Provider与成本观察

- 28/30文档成功；D017（14条）和D089（6条）两轮均为 `provider_arrearage`，随后熔断。
- 20条按生产语义 KEEP_FAIL_OPEN，不计作模型分类正确。
- 首轮墙钟约108.6秒。
- 成功调用：input 161,821、cached input 25,600、output 40,451、reasoning 34,395 tokens。
- 包含定向重试的实际调用32次、失败4次、累计provider latency 369,376ms。
- 30篇困难集不能用于净成本推断。

## 验收判定与建议

| 项目 | 结果 |
| --- | --- |
| 显式 paragraph字段进入生产/评测共用request | PASS |
| 409条证据到paragraph机械映射 | PASS |
| 跨段FP抑制 | PASS，修复9条v2 FP |
| Relevant recall | FAIL，81.37% |
| hard-indirect质量 | FAIL，recall 69.16%、drop precision 50.75% |
| Direct保护 | FAIL，1条Direct FN |
| Enforce | **禁止，继续Shadow** |

下一步不应简单恢复全文继承。更合理的是保留显式 paragraph，同时：

1. 为candidate增加短、结构化的 target exposure hints，而不是更宽文章上下文，例如 `MEMORY_COMPETITOR`、`GPU_HBM_COMPLEMENT`、`AI_SERVER_DRAM_DEMAND`。
2. 增加原因码和强制三段式内部判断：event、exposure、MU consequence；Direct候选可加程序性保留保护。
3. 避免为同段多个candidate重复发送全文段落，可改为paragraph table + paragraph ID引用，降低输入冗余。
4. 在完整30篇前先对本轮43条有效错误做至少多次重复验证，区分稳定错误与采样波动。

## 可复核产物

- `runs/frozen_gate_v3_paragraph_20260812_live1_retry1/report.json`
- `runs/frozen_gate_v3_paragraph_20260812_live1_retry1/predictions.jsonl`
- `runs/frozen_gate_v3_paragraph_20260812_live1_retry1/candidate_mention_mapping.jsonl`
- `cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl`

SHA256：

- Prompt v3：`3ccc73c231904764615f7d75e34b1cd8a108ebb4b2c40b6c064a6d5af41bbb28`
- Predictions：`137e852072813278c6aa339cba997deb97f4a9a89f22bf3cf89b78ef298bb277`
- Report：`1aaf800ebd2dc6d35598c9a5ddd688a744d6e9c6cbe328ba0235e6182e5d878e`
