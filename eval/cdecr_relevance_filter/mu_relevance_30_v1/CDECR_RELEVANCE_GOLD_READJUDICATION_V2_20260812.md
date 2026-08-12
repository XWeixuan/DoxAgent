# CDECR Relevance Gold 争议复审与调整后准召口径

日期：2026-08-12  
范围：上轮真实 Gate 与 final Gold 的全部 35 条有效分歧  
原则：不使用 Gate 预测作为标注证据；旧 Gold 保留不覆盖  

## 一、历史覆写来源

用户没有改动 Gold。原始 Terra 29篇标注为 202 Relevant / 193 Irrelevant；主对话 Codex 在验收阶段另外发起了15条人工裁决指令，生成 `cdecr_mu_relevance_30_candidates_gold_adjudicated_29.jsonl`：

- 12条 `IRRELEVANT -> RELEVANT`：D095 7条、D025 1条、D045 2条、D138 2条。
- 3条 `RELEVANT -> IRRELEVANT`：D045 3条。

这些动作被明确记录为 `MANUAL_OVERRIDE`，随后与独立补标的D017机械合并进 `cdecr_mu_relevance_30_candidates_gold_final.jsonl`。因此，“Terra原始标签”和“验收使用的final Gold”是两个不同层级；问题来自主对话二次裁决采用了未冻结的宽上下文边界，不是用户手工改动，也不是 Terra 文件被原地覆盖。

## 二、冻结的上下文合同

每个 candidate 只允许使用：

1. candidate statement；
2. exact evidence span；
3. evidence所在段落。

允许使用稳定的公开业务知识识别已经成立的竞争或互补产品暴露，例如 SanDisk/Kioxia 与 Micron 的 NAND 竞争关系、GPU 与 HBM 的互补关系；但不得从文章其他段落继承未出现在局部上下文中的因果解释。

附加边界：

- 如果列表或比较本身陈述了 Micron 的估值、评级、价格或经营事实，仍是 Direct，不因“list membership”被排除。
- 其他公司的整体收入、EPS、利润、股价或分析师动作，只有当同段明确建立了一个具有实质性的 MU 暴露驱动时才相关。
- 同段仅仅并列两个事实不等于因果归因；但可量化且占比重大的业务构成可以建立局部传导路径。
- 不允许为了让 Gold 接近 Gate 预测而修改标签。

## 三、35条复审结果

全部35条均在 `gold_dispute_adjudication_v2.json` 中保存：candidate statement、exact evidence、containing paragraph、Terra标签、旧final标签、新标签、relevance type、传导路径和独立理由。

复审结果：

- 26条维持旧final标签。
- 8条 `RELEVANT -> IRRELEVANT`。
- 1条 `IRRELEVANT -> RELEVANT`。
- 新Gold：204 Relevant / 205 Irrelevant。

### 改为 IRRELEVANT 的8条

| 文档 | 候选摘要 | 结论依据 |
| --- | --- | --- |
| D021 | DeepSeek更便宜且性能相当 | 局部段落没有内存需求、价格或Micron路径 |
| D095 | Ciena backlog $7B | 局部段落未说明AI data center来源 |
| D095 | Ciena全年收入指引 | `This explains`的因果前件位于其他段落，不得继承 |
| D095 | Ciena季度收入增长 | 公司整体收入，局部段落无AI数据中心归因 |
| D095 | Ciena订单 $2B | 局部段落无AI数据中心或MU暴露 |
| D045 | Dell目标价上调 | memory rationale位于另一段，不得继承 |
| D045 | HPE/Dell一般盈利预期 | 局部段落没有内存归因 |
| D102 | 历史数字助手采用率不佳 | 软件采用事件，没有具体硬件内存后果 |

### 改为 RELEVANT 的1条

| 文档 | 候选摘要 | 类型 | 传导路径 |
| --- | --- | --- | --- |
| D025 | AMD收入增长38%，同段data-center sales增长57%至$5.8B | Indirect Economic | AMD收入增长中数据中心业务占比重大 -> accelerator/server deployment -> HBM/DRAM需求 -> MU预期 |

### 关键维持项

- D021 Micron低于20倍PE：Direct Relevant。列表本身直接陈述MU估值事实。
- D095 Ciena earnings、DCI市场和光网络组件需求三条：同段明确写AI data-center驱动，维持Indirect Relevant。
- D167 Oracle整体指引和股价下跌：局部上下文未建立Micron特异路径，维持Irrelevant。
- D147 Supermicro泛称采购hardware components：未点明内存或Micron采购关系，维持Irrelevant。
- D001/D097的SanDisk、Samsung、SK Hynix、Kioxia证券事件：基于已成立的NAND/DRAM/HBM竞争关系，维持Indirect Relevant。
- D102 RTX Spark高性能/本地AI平台：高容量AI-PC内存是具体互补需求路径，维持Indirect Relevant。
- D138三条：局部段落明确把hyperscaler capex、共同AI供应商定价/利润率和Micron订单或Cloud Memory margin连接起来，维持Indirect Relevant。
- D293 Apple margin pressure：同段明确说明memory prices上涨并点名Micron受益，维持Indirect Relevant。

## 四、调整后的准召口径

### 计分定义

- Relevant recall = Gold Relevant中没有被DROP的比例；fail-open在运行语义上属于KEEP。
- Relevant precision = 有效预测为RELEVANT的候选中Gold Relevant的比例。
- Dropped-candidate precision = 被DROP的候选中Gold Irrelevant的比例；这是前置过滤器最重要的“删除精度”。
- Valid accuracy仅在389条有模型标签的候选上计算。
- 20条fail-open来自D017/D089文档级provider欠费失败；这20条在新版Gold中仍全部是Irrelevant，但不视为模型正确分类。

### 总体混淆矩阵（仅389条有效预测）

| | Gold Relevant | Gold Irrelevant |
| --- | ---: | ---: |
| Predicted Relevant | TP 184 | FP 6 |
| Predicted Irrelevant / DROP | FN 20 | TN 179 |

### 旧final Gold与复审v2对照

| 指标 | 旧final Gold | 复审v2 Gold | 变化 |
| --- | ---: | ---: | ---: |
| Gold Relevant / Irrelevant | 211 / 198 | 204 / 205 | -7 / +7 |
| Relevant recall | 86.73% | **90.20%** | +3.47pp |
| Relevant precision | 96.32% | **96.84%** | +0.52pp |
| Dropped-candidate precision | 85.93% | **89.95%** | +4.02pp |
| Valid accuracy | 91.00% | **93.32%** | +2.31pp |
| Direct-relevant FN | 0 / 79 | **1 / 80** | 暴露原指标漏标 |

Relevant F1为93.40%。Direct FN是D021的“Micron属于低于20倍PE的三家公司之一”。

### 切片

| 切片 | Relevant recall | Drop precision | Valid accuracy | 结论 |
| --- | ---: | ---: | ---: | --- |
| hard indirect | 87.85% | 71.74% | 88.81% | 仍是主要质量短板 |
| mixed | 84.78% | 94.40% | 94.01% | D095宽上下文噪声修正后明显改善 |
| mostly high / very high | 100.00% | N/A | 100.00% | 无变化 |
| very low / none | N/A | 100.00% | 100.00% | 20条provider fail-open不计模型正确 |

## 五、验收判断

Gold复审后，Gate没有先前报告显示得那么差，但仍不能进入Enforce：

1. Relevant recall只有90.20%，仍有20条真实FN。
2. hard-indirect drop precision只有71.74%，说明该切片误删风险仍高。
3. 新识别出1条Direct FN，否定了旧的“Direct保护完全通过”结论。
4. 20条provider fail-open仍未获得真实模型分类。

因此调整后的结论是：Gold边界问题解释了9/35条分歧，但其余26条仍是有效Gate/Gold分歧；Gate确实偏严，Shadow应继续保持。

## 六、产物

- `cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl`
- `gold_dispute_adjudication_v2.json`
- `gold_readjudication_v2_summary.json`
- `scripts/cdecr_relevance_readjudicate_gold.py`

旧的final Gold及原始Terra产物均未覆盖，以便完整追踪两轮裁决差异。
