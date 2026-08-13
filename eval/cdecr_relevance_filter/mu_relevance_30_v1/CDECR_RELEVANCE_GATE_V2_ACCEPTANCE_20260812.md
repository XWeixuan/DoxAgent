# CDECR Relevance Gate v2 真实分类复验

日期：2026-08-12  
语料：MU relevance 30 v1，30篇 / 409 frozen Dreamer candidates  
模型：DashScope `deepseek-v4-flash-0731`  
主运行：`runs/frozen_gate_v2_20260812_live1_retry1`  

## 结论

v2 对前轮漏召有局部且可确认的改善，但同时引入了更多错召，整体不构成稳定晋级：

- 基于统一上下文合同后的 Gold v2，上轮有效错误为26条，本轮修复7条，19条持续错误，并新增9条错误。
- False negatives 从20降至17，false positives 从6升至11。
- Relevant recall 从90.20%升至91.67%，dropped-candidate precision 从89.95%升至91.10%，Direct FN 从1降至0。
- Relevant precision 从96.84%降至94.44%，valid accuracy 从93.32%降至92.80%。
- hard-indirect 仍是核心风险：recall 89.72%，drop precision 72.50%，valid accuracy 87.41%。

因此 Gate 继续保持 Shadow，不应启用 Enforce。

## 本轮变量边界

本轮不是单一 Prompt A/B，而是以下组合变化：

1. `relevance-filter-v2`：放宽“clearly yes”阈值，加入 complementary-product / derived-demand，并修正 list-membership 边界。
2. 第二轮事件输入新增 `exact_evidence`。
3. 通过 CLI 显式注入业务画像：`Micron Technology (MU); DRAM, NAND and HBM memory semiconductors`。当前 `.env` 本身仍没有 `CDECR_RELEVANCE_TARGET_PROFILES`。
4. 评分改用已冻结局部上下文合同的 Gold v2：204 Relevant / 205 Irrelevant。

因此不能把标签变化全部归因于 Prompt 文案；模型采样波动也没有通过重复试验被隔离。

外发内容仍只包含完整正文与409条 frozen candidates；mentions、candidate Gold、`relevance_gold` 和事后映射没有进入模型请求。

## 总体指标

上轮 baseline 已使用 Gold v2 重新计分，避免把已裁决的9条 Gold 变化误算成模型变化。

| 指标 | 上轮 baseline / Gold v2 | 本轮 v2 | 变化 |
| --- | ---: | ---: | ---: |
| Valid predictions | 389 | 389 | 0 |
| Fail-open | 20 | 20 | 0 |
| Simulated drops | 199 | 191 | -8 |
| Relevant recall | 90.20% | **91.67%** | +1.47pp |
| Relevant precision | **96.84%** | 94.44% | -2.40pp |
| Dropped-candidate precision | 89.95% | **91.10%** | +1.15pp |
| Valid accuracy | **93.32%** | 92.80% | -0.52pp |
| Direct FN | 1 / 80 | **0 / 80** | 修复1条 |

仅对389条有效模型标签的混淆矩阵：

| | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: |
| 上轮 baseline | 184 | 6 | 20 | 179 |
| 本轮 v2 | 187 | 11 | 17 | 174 |

### 切片

| 切片 | Baseline recall | v2 recall | Baseline drop precision | v2 drop precision | Baseline accuracy | v2 accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hard indirect | 87.85% | **89.72%** | 71.74% | **72.50%** | **88.81%** | 87.41% |
| mixed | 84.78% | **86.96%** | 94.40% | **95.12%** | 94.01% | 94.01% |
| mostly high | 100.00% | 100.00% | N/A | N/A | 100.00% | 100.00% |
| very low / none | N/A | N/A | 100.00% | 100.00% | 100.00% | 100.00% |

## 旧错漏召是否改善

### 已修复的6条旧 FN

| 文档 | Candidate | 判断 |
| --- | --- | --- |
| D021 | Micron、Skywater、Photronics 是仅有的低于20倍PE的三家公司 | **修复**。列表直接陈述 Micron 估值，现识别为 Direct；旧 Direct FN 已消除。 |
| D045 | JPMorgan 上调 HPE 目标价并维持 Overweight | **修复**。所在段明确将 call 归因于 memory concerns reversal。 |
| D102 | RTX Spark 提供最高1 petaflop AI性能且保持笔记本功耗 | **修复**。complementary-product / AI-PC derived demand 路径开始生效。 |
| D138 | Broadcom CEO 目标2027年AI销售超过1000亿美元 | **修复**。AI基础设施需求路径被保留。 |
| D138 | Oracle 5530亿美元RPO由 hyperscaler 和 AI-lab prepayments 支撑 | **修复**。云/AI capex 到内存需求路径被保留。 |
| D138 | NVIDIA 指引Q2 non-GAAP毛利率75% | **修复**。局部段落中的共同AI供应链和 Micron 暴露被继承。 |

### 已修复的1条旧 FP

- D167 Oracle 创纪录Q4后股价一周下跌22.1%：本轮正确判为 Irrelevant。证券价格变动本身没有 MU 特异路径。

### 仍持续的14条 FN

| 聚类 | 文档 / 数量 | 追因 |
| --- | --- | --- |
| GPU/AI部署到内存派生需求 | D072 ×3 | AMD hyperscaler deals、data-center revenue、6GW GPU deployment 仍全部被拒。仅加入 derived-demand 术语和 MU 产品画像不足以稳定触发 `GPU deployment -> HBM/DRAM`。 |
| AI数据中心互联需求 | D095 ×3 | Ciena earnings、AI data-center optical component demand、DCI market growth 仍被拒；模型没有稳定接受“AI基础设施互补需求”到 MU 的非直接路径。 |
| 成立的内存竞争关系 | D001 ×2、D097 ×4 | SanDisk、Samsung、SK Hynix、Kioxia 的估值/评级仍被拒。业务画像只列 DRAM/NAND/HBM，没有显式提供竞争者关系；“可使用稳定公开知识”并未稳定生效。 |
| AI-PC互补需求 | D102 ×1 | RTX Spark性能事件修复，但“为PC设计的custom SoC”仍被拒，说明模型只在更显著AI性能表述下建立内存需求路径。 |
| 同段明确成本传导 | D293 ×1 | Apple margin pressure 仍被拒，尽管同段把压力归因于 memory prices；说明模型没有可靠回取 exact evidence 所在完整段落。 |

### 仍持续的5条 FP

| 文档 | Candidate | 追因 |
| --- | --- | --- |
| D147 | Supermicro 融资70亿美元购买 hardware components | 泛称组件仍被错误扩展为 Micron 需求；Prompt 缺少“server/chip/hardware components 泛称不足”的明确负例。 |
| D167 | Oracle FY27 EPS/收入指引 | 仍从文章更宽的AI建设背景继承 MU 路径，违反局部段落合同。 |
| D025 ×3 | AMD整体 revenue / EPS / net income | exact evidence 是独立财务项目，但模型继续继承文章其他位置的 data-center 驱动，说明 article-context leakage 未解决。 |

## 新增 badcase

### 新增3条 FN：D190

SK Hynix 的 NAND 18%、HBM 58%、DRAM 29% 市占率三条由正确变为 Irrelevant。它们都是 Micron 在同一明确产品市场中的竞争份额事件；其中 NAND/DRAM 所在段直接写“SK Hynix 与 Samsung、Micron 并列为领先内存厂商”，HBM 段则明确写 AI accelerator demand 和供给短缺。

这不是 Gold 边界争议，而是新的真实漏召。最可能原因是模型波动或“不要假设关系”仍压过 competitor / target-profile 规则；它也证明 MU profile 并不能稳定替代显式、结构化的 exposure 输入。

### 新增6条 FP

| 文档 | Candidate | 局部上下文结论与根因 |
| --- | --- | --- |
| D021 | DeepSeek 更便宜且性能相当 | 所在段只讨论模型价格/性能，没有内存需求路径。阈值放宽后把泛AI事件扩展成 derived demand。 |
| D027 | AMD业绩超预期后盘前涨近20% | 独立新闻列表项，没有数据中心、GPU部署或内存暴露。属于同行/AI-chip证券事件泛化。 |
| D025 | Intel 业绩超预期且股价翻倍 | 同段随后并列 Micron 股价上涨，但没有因果关系。模型把局部共现误当传导路径。 |
| D045 | HPE EPS超指引 | 同段增长来自 Juniper Networking integration，没有内存归因；模型继承了文章首段 memory narrative。 |
| D045 | JPMorgan 上调 Dell 目标价 | 所在段只称 IT hardware synchronized opportunity，没有 memory rationale；模型把上一段 HPE 的 memory rationale 横向继承。 |
| D045 | JPMorgan 看好 Dell/HPE earnings revisions | 所在段只有盈利和估值预期，没有内存驱动；同样属于跨段继承。 |

## 根因判断

以下追因是根据标签迁移与局部证据作出的推断，因为当前输出合同仍只有二元标签，没有模型原因码：

1. **上下文合同仍未机械冻结。** 第二轮显式传了 statement 和 exact evidence，但没有传 `containing_paragraph` 字段；模型只能从包含整篇正文的第一轮 transcript 自行定位段落。D025、D045、D167 的持续/新增 FP 与 D293 的持续 FN 同时证明它既会过度继承，也会漏用紧邻上下文。
2. **放宽阈值产生了预期的 recall/FP trade-off。** D138、RTX Spark、HPE memory-rationale 得到改善，但 DeepSeek、AMD证券事件及一般IT硬件事件开始被泛化。
3. **业务画像不等于 exposure graph。** profile 告诉模型 MU 做 DRAM/NAND/HBM，却没有稳定编码 AMD GPU/Ciena interconnect/Apple成本传导和 SanDisk/Samsung/SK Hynix/Kioxia 的竞争关系。
4. **缺少关键负边界。** Prompt 仍未明确：泛称 hardware components 不足、同行证券表现不足、同段并列不等于因果、公司整体财务只有局部段明确归因于暴露业务时才相关。
5. **没有原因码，无法区分知识缺失、路径过长、段落定位失败和阈值决策。** 本轮追因只能依靠受控证据对比，不能视为模型自陈原因。

## Provider 与 fail-open

- 主运行28/30文档成功，D017（14条）和D089（6条）返回 `provider_arrearage`。
- 仅重试这两篇一次后得到相同错误，累计实际调用32次、失败4次，随后熔断。
- 20条全部按生产语义 KEEP_FAIL_OPEN；它们的100%保留不计作模型分类正确。
- 主运行墙钟约92.2秒。成功调用汇总：input 115,008、cached input 768、output 37,320、reasoning 31,433 tokens。
- 相比上轮相同28个成功文档，input约增加17.3%，output约增加35.8%，reasoning约增加45.2%。30篇困难集不用于推断净成本收益。

沙箱内第一次运行因 `APIConnectionError` 得到409条 fail-open，保留在 `runs/frozen_gate_v2_20260812` 作为环境诊断，不纳入本报告模型指标。

## 后续建议

在下一轮 Prompt 调整前，优先完成：

1. 第二轮为每条 candidate 显式传入有长度上限的 `containing_paragraph`，并使模型不得使用全文其他段落；仅写在 Prompt 中不足以执行该边界。
2. 增加短原因码，至少覆盖 `DIRECT_TARGET`、`COMPLEMENT_DEMAND`、`CLOSE_COMPETITOR`、`LOCAL_CONTEXT_PATH`、`GENERIC_COMPONENT`、`COOCCURRENCE_ONLY`、`ARTICLE_CONTEXT_ONLY`、`NO_SPECIFIC_PATH`。
3. 加入正负对照：AMD 6GW GPU部署、SK Hynix明确内存份额、Ciena AI DCI为 Relevant；Supermicro泛硬件采购、独立AMD/Oracle股价、无归因整体EPS、同段并列为 Irrelevant。
4. 先固定复验本轮28个有效 badcases，再跑完整30篇；至少做多次重复或固定采样参数，避免把单次波动误判为 Prompt 因果效果。

## 可复核产物

- `runs/frozen_gate_v2_20260812_live1_retry1/report.json`
- `runs/frozen_gate_v2_20260812_live1_retry1/predictions.jsonl`
- `runs/frozen_gate_v2_20260812_live1_retry1/candidate_mention_mapping.jsonl`
- `cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl`
- `gold_dispute_adjudication_v2.json`

SHA256：

- Prompt v2：`3d560276af057b541718d12a2fb94a757a3261f33144f093e3717bf30bae940e`
- Gold v2：`fbd6a0e8bd81c8d47e4cbf2d77f695583ef9133c72fef873c21a49fc4ad0dae9`
- Final report：`5249f06d4735f080eb0fea85b0387b169db72640dd04b767346948781abdb9e6`
- Final predictions：`4ba0723280f019ccc2de301eb62ed62b48fec91c5c431e48363d21b221b7f6f4`
