# CDECR Relevance Gate v2.1 真实复验

日期：2026-08-12  
语料：MU relevance 30 v1，30篇 / 409 frozen Dreamer candidates  
模型：DashScope `deepseek-v4-flash-0731`  
主运行：`runs/frozen_gate_v21_20260812_live1_retry1`  
比较基线：`runs/frozen_gate_v2_20260812_live1_retry1`  

## 结论

当前 v2.1 相比 v2 整体退化，不能进入 Enforce：

- Relevant recall 从91.67%降至88.73%。
- Relevant precision 从94.44%降至93.78%。
- Dropped-candidate precision 从91.10%降至88.27%。
- Valid accuracy 从92.80%降至91.00%。
- False negatives 从17增至23，false positives从11增至12。
- Direct FN仍为0/80，但mostly-high切片首次出现1条FN。

v2.1 修复了v2的4条FN和4条FP，但新增10条FN和5条FP，净损失明显。继续保持Shadow。

## 版本变量边界

当前工作树中的v2.1是以下组合，而不是只回退一个字段：

1. 保留v2 Prompt的阈值、complementary-product / derived-demand和列表边界。
2. 第二轮保留 `statement + exact_evidence`。
3. 不显式传入 `containing_paragraph`；段落上下文只能从前一轮完整文章transcript中隐式获得。
4. 评测CLI已删除 `--target-profile`，当前 `.env` 也没有 `CDECR_RELEVANCE_TARGET_PROFILES`，因此target从v2的 `Micron Technology (MU); DRAM, NAND and HBM memory semiconductors` 回退为 `Micron Technology (MU)`。
5. 使用相同Gold v2：204 Relevant / 205 Irrelevant，并保留candidate-level Direct评分修正。

因此本轮差异同时包含“去掉显式段落”和“去掉MU业务画像”，不能把全部变化归因于Prompt或上下文回退。模型仅单次运行，仍含采样波动。

外发内容仍只有30篇正文和409条冻结candidates；mentions、Gold、`relevance_gold`及事后映射未进入请求。

## 指标对比

| 指标 | v2 | v2.1 | 变化 |
| --- | ---: | ---: | ---: |
| Valid predictions | 389 | 389 | 0 |
| Fail-open | 20 | 20 | 0 |
| Simulated drops | 191 | 196 | +5 |
| Relevant recall | **91.67%** | 88.73% | -2.94pp |
| Relevant precision | **94.44%** | 93.78% | -0.66pp |
| Dropped-candidate precision | **91.10%** | 88.27% | -2.83pp |
| Valid accuracy | **92.80%** | 91.00% | -1.80pp |
| Direct FN | 0/80 | 0/80 | 0 |

仅389条有效标签的混淆矩阵：

| | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: |
| v2 | 187 | 11 | 17 | 174 |
| v2.1 | 181 | 12 | 23 | 173 |

### 切片

| 切片 | v2 recall | v2.1 recall | v2 drop precision | v2.1 drop precision | v2 accuracy | v2.1 accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hard indirect | **89.72%** | 85.98% | **72.50%** | 65.12% | **87.41%** | 83.92% |
| mixed | **86.96%** | 84.78% | **95.12%** | 94.35% | **94.01%** | 93.41% |
| mostly high | **100.00%** | 98.04% | N/A | 0% | **100.00%** | 98.04% |
| very low / none | N/A | N/A | 100.00% | 100.00% | 100.00% | 100.00% |

## 相对v2的错误迁移

### 修复4条v2 FN

- D190 ×3：SK Hynix NAND 18%、HBM 58%、DRAM 29%市场份额。
- D293 ×1：Apple margin pressure；恢复宽上下文后重新继承memory-price因果。

前三条说明单次模型在竞争份额识别上存在明显波动；后一条说明全文上下文有助于找回跨句/段成本传导。

### 修复4条v2 FP

- D027 AMD业绩后盘前上涨近20%。
- D025 AMD net income、EPS。
- D025 Intel业绩和股价翻倍。

这些修复无法由“上下文回宽”直接解释，更可能包含采样波动；AMD整体revenue仍持续FP，说明财务事件边界没有稳定解决。

### 持续13条FN

- D072 AMD hyperscaler、data-center revenue、6GW GPU部署 ×3。
- D095 Ciena earnings、AI optical demand、DCI market ×3。
- D001 SanDisk估值/股价 ×2。
- D097 Samsung、SanDisk、Kioxia、SK Hynix评级/目标价 ×4。
- D102 RTX Spark PC SoC ×1。

这是v2.1仍未解决的核心：AMD/Ciena派生需求和内存同行竞争关系无法稳定建立。

### 持续7条FP

- D021 DeepSeek泛AI事件。
- D147 Supermicro泛称hardware components采购。
- D167 Oracle整体EPS/收入指引。
- D025 AMD整体revenue。
- D045 HPE EPS、Dell目标价、Dell/HPE一般盈利预期 ×3。

它们继续表现出全文背景继承过宽：泛AI、泛硬件、整体财务和上一段memory narrative被错误扩展成MU路径。

## 新增badcase

### 新增10条FN

| 聚类 | 数量 | 事件 | 判断 |
| --- | ---: | --- | --- |
| RTX Spark / AI-PC内存需求 | 5 | D102：agentic AI PC、120B local model、1M context、1 petaflop、128GB unified memory、OEM采用 | 最明显退化。尤其“up to 128GB unified memory”仍被判不相关，强烈指向移除MU DRAM业务画像后模型未建立产品互补关系。 |
| SanDisk竞争经营 | 2 | D001：Q3 revenue +171%、gross margin 65.9% | 明确NAND竞争者的经营预期被漏掉，同样符合缺少MU NAND profile和竞争知识的影响。 |
| Oracle AI基础设施 | 1 | D167 Multicloud AI Database增长404% | 相关性本就较间接，缺少memory业务画像后被拒。 |
| Broadcom AI销售 | 1 | D138 2027 AI sales目标1000亿美元 | v2正确、v2.1漏召，说明AI基础设施到内存需求路径不稳定。 |
| Apple消费者成本 | 1 | D292 Mac/iPad涨价被用于证明消费者承担成本 | 这是mostly-high切片唯一FN。局部内存价格/监管传导需要更明确的业务画像或段落锚定。 |

### 新增5条FP

- D167 Oracle创纪录Q4后股价下跌22.1%。
- D045 HPE data-center networking增长383%，由Juniper收购驱动。
- D045 HPE Networking增长152%，由Juniper整合驱动。
- D061 Kioxia ADR上涨460%。
- D061 Kioxia股价年内翻逾四倍。

HPE两条是典型“data center”词面触发，但实际增长来自网络业务整合，没有具体内存路径。Oracle/Kioxia三条是其他公司证券表现，按Gold v2没有局部具体传导路径。它们说明回到全文隐式上下文后，错召边界再次扩大。

## 根因判断

1. **移除MU业务画像伤害互补产品与竞争者召回。** D102新增5条、D001新增2条FN是最强证据；仅公司名称不足以稳定让模型调用DRAM/NAND/HBM知识。
2. **未显式提供paragraph恢复了上下文歧义。** D045网络整合、D167/Kioxia证券事件新增FP，持续的D025/D045/D167 FP也未改善。
3. **exact evidence单独不足以冻结上下文。** Prompt口头要求使用containing paragraph，但请求没有该字段，模型只能自行从完整transcript定位或继承。
4. **单次采样波动明显。** D190三条竞争份额从v2错误变正确，而若干无关AMD财务事件也随机修复；不能将每条迁移视为版本因果。
5. **没有原因码。** 仍无法从输出区分知识缺失、路径过长、段落定位失败或阈值选择，追因仅是基于候选、证据与版本差异的推断。

## Provider与成本观察

- 28/30文档成功；D017 14条、D089 6条在原始调用和一次定向重试中均为 `provider_arrearage`，随后熔断。
- 20条全部KEEP_FAIL_OPEN，不计作模型正确分类。
- 首轮墙钟约89.2秒。
- v2.1成功调用：input 114,292、cached input 256、output 31,005、reasoning 25,178 tokens。
- 相比v2：input -0.62%、output -16.92%、reasoning -19.90%、provider latency -6.80%。这是单次困难集观察，不代表300篇净成本。

## 验收判定

| 项目 | 判定 |
| --- | --- |
| 生产/评测协议一致 | PASS |
| 输入隔离 | PASS |
| Direct保护 | PASS，0/80 FN |
| 相对v2质量 | **FAIL** |
| hard-indirect质量 | **FAIL** |
| Enforce | **禁止，继续Shadow** |

当前v2仍是三轮中质量最好的主基线。若下一步希望判断“去掉显式paragraph、保留MU profile”的纯效果，需要恢复一个可控的profile注入方式并单独跑v2.1-profile；当前v2.1无法回答这个单变量问题。

## 可复核产物

- `runs/frozen_gate_v21_20260812_live1_retry1/report.json`
- `runs/frozen_gate_v21_20260812_live1_retry1/predictions.jsonl`
- `runs/frozen_gate_v21_20260812_live1_retry1/candidate_mention_mapping.jsonl`
- `cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl`

SHA256：

- Prompt v2.1：`002cfbda20da9ae9cbc4380f05689c69cef522fe660fd598d940bfc2b3d61ab3`
- Report：`0f3fe8e39b82033aa4be57011dc32c55466dfe3bb27841992977bfc91db919b9`
- Predictions：`a68cd52aeb4390d63d36649d97ea8a27fc1c6a825d580784e6b49b9e905b97a7`
