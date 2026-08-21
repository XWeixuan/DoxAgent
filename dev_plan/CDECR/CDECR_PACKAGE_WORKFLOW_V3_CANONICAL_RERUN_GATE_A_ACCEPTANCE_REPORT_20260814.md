# CDECR Package Workflow V3 canonical 修复后 Gate A 真实复测报告

日期：2026-08-14  
范围：R3-derived 与 formal-V2-derived 两份冻结 Parent Occurrence Pool 的真实 Package-only 测试  
主基线：各自冻结输入对应的 V2.0R active Package partition  

## 结论

上一轮 R3-derived 的 canonical 输出存在严重合同错误，本报告不沿用该轮结论。修复后使用全新 Registry/Scope 完成两次真实测试；两次均 FINALIZED、覆盖全部 Parent Occurrence 与 Atomic、Apply 单事务完成，幂等二跑为0次新模型调用且 partition hash稳定。

canonical字段修复已经闭环：

- R3-derived 35/35 canonical为非空语义名称，ID型canonical为0；
- formal-V2-derived 44/44 canonical为非空语义名称，ID型canonical为0；
- Response A → staged batch → Registry snapshot/head → EventPackage title逐ID一致；
- Node 3 description → checkpoint → Registry → EventPackage summary一致；
- R3旧测试中37/37 `MCP-001`式错误canonical已降为0/35。

但是，**canonical正确不等于Package聚类正确**。两份新结果的Pair Precision均不足90%，Recall均不足65%，F1均不足75%，因此V3质量Gate仍失败，不应切换生产current Package。

## 1. 测试输入与执行

| 测试 | Frozen Atomic | Parent Occurrence | V3 Registry | Package |
| --- | ---: | ---: | ---: | ---: |
| R3-derived | 192 | 115 | 35 MCP | 34 |
| formal-V2-derived | 281 | 132 | 44 MCP | 44 |

R3有1个MCP在跨MCP Atomic唯一归属后变为空，因此35个Registry MCP投影为34个Package；这不是canonical丢失。两轮均只涉及初始Registry v0→v1，因此真实覆盖Node 1和Node 3，未真实覆盖>200 occurrence才触发的Node 2 rolling路径。

## 2. 运行与成本

| 指标 | R3-derived | formal-V2-derived |
| --- | ---: | ---: |
| Model calls | 2 | 2 |
| Failed/repair calls | 0 | 0 |
| Input tokens | 7,471 | 9,058 |
| Output tokens | 36,370 | 46,652 |
| Total tokens | 43,841 | 55,710 |
| Initial clustering latency | 249.58s | 357.37s |
| Description latency | 57.81s | 50.94s |
| Clean wall | 308.36s | 409.39s |
| Idempotent replay | 0 calls / stable | 0 calls / stable |

相较旧V3，R3 wall增加8.79%、output增加16.22%；formal wall下降10.13%、output下降16.73%。方向相反，不能宣称canonical修复带来稳定效能改善。输入因更明确的Schema/Prompt增加约2.4%–4.8%。

## 3. Gold指标

### 3.1 新结果

| 指标 | R3-derived | formal-V2-derived | Gate |
| --- | ---: | ---: | ---: |
| Gold对齐覆盖 | 73/192, 38.02% | 99/281, 35.23% | 仅局部证据 |
| Pair Precision | 82.48% | 81.99% | ≥90% |
| Pair Recall | 50.37% | 56.10% | ≥65% |
| Pair F1 | 62.55% | 66.62% | ≥75% |
| TP / FP / FN | 339 / 72 / 334 | 446 / 98 / 349 | — |
| Package | 34 | 44 | — |
| Singleton | 15/34, 44.12% | 14/44, 31.82% | ≤45% |
| 最大Package | 71 | 74 | 需人工审计 |

两份Gold覆盖只有35%–38%，因此准召只能用于固定可判子集，不可替代全量逐Package审核。

### 3.2 formal-V2-derived严格共同99项A/B

Evaluator中的 `previous_actual_packages_on_matched_gold` 实际读取2026-07-28 Gold Registry历史partition，并不是V2.0R。独立Agent使用同一99个accepted Atomic、同一Gold label，直接读取真正V2.0R source Registry的active memberships重新计算：

| Partition | P | R | F1 | TP/FP/FN |
| --- | ---: | ---: | ---: | --- |
| V2.0R | 81.85% | 33.46% | 47.50% | 266/59/529 |
| 旧V3 | 69.66% | 41.01% | 51.62% | 326/142/469 |
| 新V3 canonical R2 | 81.99% | 56.10% | 66.62% | 446/98/349 |

新V3相对V2.0R：Precision +0.14pp、Recall +22.64pp、F1 +19.12pp；相对旧V3也实现P/R/F1同时改善。但绝对Gate仍未通过。

Micron earnings从V2.0R的13个组件改善为3个组件，达到≤4的形态门槛；missed links仍为343。

### 3.3 R3-derived严格共同73项A/B

同样使用新V3的73个accepted Atomic、同一Gold label，并直接读取真正的V2.0R R3 source Registry active memberships：

| Partition | P | R | F1 | TP/FP/FN |
| --- | ---: | ---: | ---: | --- |
| V2.0R | 81.23% | 33.43% | 47.37% | 225/52/448 |
| 新V3 canonical R2 | 82.48% | 50.37% | 62.55% | 339/72/334 |

新V3相对真正V2.0R：Precision +1.25pp、Recall +16.94pp、F1 +15.18pp，missed links减少114。三项均改善，但绝对值仍未达到发布Gate。

R3-derived Micron earnings被拆为5个组件 `[26,3,3,3,2]`，产生331个missed links，未过组件门槛。

## 4. 独立Agent逐Package审核

### 4.1 R3-derived

- 34个Package全部完成审核；
- 8个明确误合Package；
- 另有4个Package的父边界可接受，但其内部Atomic已经复合污染；
- 15个singleton的审计存在口径差异：宽人工相关性口径识别5个潜在重组目标；严格Gold/同父发生口径仅1个明确漏合、1个需review、12个应保持singleton，另1个属于错误owner而不是漏合；
- 最大Package 71条，Micron earnings主体之外至少13条明确错误，保守误成员率18.3%；
- 25条market reaction包至少16条错误，误成员率64%；
- 18条generic memory conditions包至少15条应拆，误成员率83.3%。

主要错误形态：

1. earnings包吸入Optimus/physical-AI、估值、行业比较与泛AI背景；
2. market reaction包混入KOSPI/SK Hynix、估值、长期涨幅和不同session；
3. generic memory conditions将Mulberry访谈、供应商产能转换、价格上涨、Micron earnings供给判断和Sandisk SCA混为主题容器；
4. Apple产品涨价包混入Counterpoint预测、独立股价reaction和季度背景；
5. Wedbush与BofA被合成analyst umbrella；
6. 若干Atomic本身已经把不同事实合并，V3无法在Package层拆开。

宽人工口径下的潜在重组目标：

- `MCP-000025 → MCP-000011`：同一Wedbush报告；
- `MCP-000031 → MCP-000001`：同一Micron earnings报道中的commentary；
- `MCP-000027`应与MCP-000006中的Mulberry访谈成员另组包；
- `MCP-000019`应吸收MCP-000002内同一forward-P/E估值Atomic；
- `MCP-000026`应吸收MCP-000001中被错误聚入的Optimus/physical-AI成员。

严格口径只确认 `MCP-000019` 的估值singleton存在明确同Gold目标，但必须先将目标Atomic从MCP-000002错误市场反应包中拆出，不能直接整包合并。`MCP-000031 → MCP-000001`仅列人工review。`MCP-000017`则是同一Mizuho Atomic被错误owner到Needham MCP，应纠正为MCP-000030，而不是执行singleton merge。

### 4.2 formal-V2-derived

- 44个Package全部完成审核；
- 14个明确含误成员，29个可接受，1个边界不确定；
- 确定错误Atomic保守下界50/281（17.79%），另有1个不确定Atomic；
- 14个singleton中3个明确漏合，人工漏合率21.43%；
- 7个size≥10簇中5个存在成员问题。

主要大簇：

- 74条earnings包：2条明确外来，约97.3%纯，但仍与earnings-call/SCA等包碎片化；
- 25条post-earnings analyst commentary包：至少16条来自不同机构、不同报告或作者荐股，误成员率64%；
- 21条broad market rebound包：至少7条独立个股/session错误，误成员率33.3%；
- 19条Micron reaction包：至少6条长期表现、估值或不同session错误，误成员率31.6%；
- 16条SCA包：至少4条HBM shipment、成本、支出纪律或终端销量错误，误成员率25%。

明确singleton漏合目标：

- `MCP-000011 → MCP-000015`：同一Apple技术分析；
- `MCP-000034 → MCP-000036`：同一HBM tight-supply持续事项；
- `MCP-000041 → MCP-000040`：同一AI/HBM demand surge。

6个跨MCP Atomic唯一归属冲突中有2个owner明确错误，说明local unique-owner reducer不是完全质量中性的。

## 5. 人类可读结果文档

两份完整结果分别输出为：

- `CDECR_PACKAGE_WORKFLOW_V3_GATE_A_R3_DERIVED_HUMAN_READABLE_20260814.md`；
- `CDECR_PACKAGE_WORKFLOW_V3_GATE_A_FORMAL_V2_DERIVED_HUMAN_READABLE_20260814.md`。

每份文档均按 `MCP → Parent occurrence → Atomic` 展开，包含：

- MCP canonical标题；
- 最终Package ID；
- 压缩后的Registry描述；
- Source/文档编号；
- Parent occurrence；
- Relation；
- Atomic数量与完整Atomic proposition；
- 该Parent occurrence中错误聚合到当前Package的具体Atomic。

错误Atomic列使用独立Agent的逐条业务判断；未发现明确错误或证据不足时保留`—`，不因追求填满而伪造结论。

## 6. 最终判断

canonical修复本身通过：字段内容、Schema约束、持久化与投影均正确。两份新V3在各自严格共同Atomic子集上都相对真正V2.0R改善了P/R/F1；但两份测试的绝对P/R/F1仍未达到90%/65%/75% Gate。

业务上仍存在三层问题：

1. 上游Parent occurrence或Atomic已经复合，Package聚类无法逆向拆分；
2. 极简全局模型仍会把共同报告背景、analyst umbrella或同日market theme当作MCP；
3. 确定性Atomic唯一归属在少数冲突中选择错误owner。

因此本轮结论为：**canonical严重错误已修复，V3的有效测试结论可以重新建立；但Package质量仍未达到发布标准，暂不切换生产、不回滚代码。**
