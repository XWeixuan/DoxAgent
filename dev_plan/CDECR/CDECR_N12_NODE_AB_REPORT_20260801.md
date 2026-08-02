# CDECR N12 Payload 与 Node-only A/B 报告（2026-08-01）

## 结论

N12 请求内字典化和有界 Package Card 已实现，但**继续保持 `shadow`，不得启用 `on`**。
原因不是降本不足，而是质量门未通过：8-task smoke 的 Input Token 降低 68.67%，但 relation
一致率只有 75%、task 完全一致率只有 25%。全量回放的 canary 又有 9/12 批次因外部 provider
欠费进入编排层保守降级，不能把该结果用于质量比较，也不能把 fallback 后的 93.95% 表观 Token
降幅宣称为有效收益。

## 已实现的表示层修改

- 每个请求只定义一次 `events` 与 `packages`，`tasks` 只引用 request-local `e*`/`p*` ID。
- 每个 Package Card 最多携带 4 个 Anchor、3 个具有不同 identity 的代表 Atomic；保留成员数、
  family/kind、period、entity、冲突和必要表面证据，不发送完整成员 ID 列表。
- 构造候选和计数前解析 Atomic redirect，同一 canonical Atomic 不再以 source/target 两个物理 ID
  重复进入 profile；legacy/shadow 对照计数也已统一为 canonical member count。
- 输出 Schema、relation/action/reason 协议、N12 candidate recall、Apply 和持久化模型均未改变。
- 非法 item 仍按 task/item 局部降级；未增加文档级阻塞。

## Node-only A/B

| 指标 | 8-task legacy | 8-task dictionary | 变化 |
| --- | ---: | ---: | ---: |
| task / candidate refs | 8 / 48 | 8 / 48 | coverage 均为 100% |
| Payload bytes | 176,978 | 53,425 | -69.81% |
| Input Token | 61,139 | 19,156 | -68.67% |
| Output Token | 4,183 | 3,822 | -8.63% |
| aggregate latency | 74,449ms | 67,658ms | -9.12% |
| relation agreement | — | 75.00% | 未过质量门 |
| selected target agreement | — | 100.00% | 通过 |
| exact task agreement | — | 25.00% | 未过质量门 |

全量回放覆盖 138 个去重后的历史 task、785 个 candidate refs。Legacy 12/12 批次成功，消耗
1,019,146 Input / 65,059 Output Token；canary 只有前三批得到真实响应，后 9 批记录
`provider_arrearage` 并保守 fallback。因此以下数据只作故障审计：canary 61,655 Input、15,159
Output、relation agreement 68.03%、target agreement 55.07%、exact task 21.74%；它们不能与
legacy 做业务 A/B。

## 大 Package 放大检查

对固定 Registry 的 68 个当前 Package 离线编译：新 Card 为 647–4,539 bytes，中位数 1,088.5
bytes；51-member 大包为 4,539 bytes。Card 已从“按全体成员线性展开”改成“最多 3 个代表成员
+ 4 个 Anchor”的结构上限，但横截面 member-count/card-size Pearson 仍为 0.775，原因是 1–3
成员阶段代表项数量阶梯增长，且当前只有两个真正大包，不足以用相关系数证明大包区间完全去相关。
因此可确认的是**结构有界**，不能宣称“大包大小与 Token 已经统计独立”。

## Prompt 变更

`package_assignment.md` 只新增一条协议说明：

> Resolve each task using the request-local package definitions.

原 Prompt 已有：

> Return every candidate exactly once, use only supplied request-local IDs, and give concise reasons.

两句合起来覆盖计划要求，没有重复追加 `return every supplied candidate exactly once`，也没有改变
relation/reason 业务定义。

## 决策

保留新 Payload、Card、redirect 解析和 shadow 审计；`canary` 仅供显式 node test，`on` 继续代码级
拒绝。待 provider 恢复后，应在同一 138-task 固定输入上重跑完整 A/B；最低门槛是 candidate
coverage 100%、无 batch fallback、relation/target 不劣于 legacy，经 Gold/人工复核无质量下降后
才允许讨论启用。

## 2026-08-02 补测：Package Gold 与碎片化

### 结论先行

当前最终 Package 在高置信 Gold 投影集上达到 Precision 93.47%、Recall 93.10%、F1 93.28%，
已经超过 2026-07-28 全量人工复核基线的 91.54% / 73.54% / 81.56%。但本次不能据此启用
dictionary N12：完整 canary 真实请求被当前运行环境的外发安全策略
阻断，旧的全量 A/B 文件又没有保存可重算准召的完整 decisions。因此，“当前工作流最终 Package 已恢复到
之前水平”成立，“dictionary N12 已证明不降质、可以从 shadow 切到 on”不成立。

### 评估口径

- Gold 继续使用 `.tmp/cdecr/resilience/package_review_all.json`，没有重新标注。
- 两轮 Atomic ID 不同，使用两轮已落库的 1024 维 Atomic embedding 做跨轮映射；只保留 cosine
  `>=0.90` 且第一、第二候选差值 `>=0.01` 的映射。
- 145 个当前 Atomic 中 84 个可判，覆盖率 `57.93%`。以下投影指标只代表该高置信子集，不能冒充
  145 个 Atomic 的全量人工 Gold。
- Pair Precision/Recall 直接比较 Package partition。碎片化同时统计：被拆散的 Gold 组数、超过理想
  单簇的 component 数、multi-event Gold 内 singleton components、以及 missed pair links。
- 机器可读明细位于
  `.tmp/cdecr/n12_node_ab_20260802_full/n12_package_gold_projection.json`；评估脚本为
  `scripts/cdecr_evaluate_n12_package_gold.py`。

### 核心指标

| 口径 | Events | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-07-28 全量人工复核基线 | 95 | 303 | 28 | 109 | 91.54% | 73.54% | 81.56% |
| 旧轮 Package 在本次 84-event 匹配集上的重投影 | 84 | 414 | 89 | 93 | 82.31% | 81.66% | 81.98% |
| 当前 Registry 中原始 N12 decision 的冻结投影 | 84 | 470 | 63 | 37 | 88.18% | 92.70% | 90.38% |
| 当前最终 Package partition | 84 | 472 | 33 | 35 | **93.47%** | **93.10%** | **93.28%** |

旧轮 84-event 重投影会让多个当前 Atomic 映射到同一个旧 Atomic，因而放大旧错误簇的 pair 权重；它适合
做同一当前样本权重下的方向比较，不替代 95-event 全量人工基线。两个口径都显示当前最终 Package F1
明显上升。相对全量人工基线，当前高置信投影为 Precision +1.93pp、Recall +19.56pp、
F1 +11.72pp；相对同权重旧轮投影为 +11.16pp / +11.44pp / +11.30pp。

2026-07-31 报告中的 `85.82% / 98.84%` 是 105/131 的 provisional 投影。与它相比，本次更严格
子集为 `Precision +7.65pp / Recall -5.74pp / F1 +1.38pp`：没有维持那一轮由强聚合带来的极高
Recall，但换回了更高 Precision 和更好的总体 F1。若把“之前水平”机械定义为 Recall 98.84%，本次
未达到；若按可接受的平衡质量和旧人工基线判断，则已达到并超过。

### 碎片化对比

| 口径 | multi-event Gold组 | 碎片化Gold组 | excess components | multi-Gold singleton components | missed pair links |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2026-07-28 全量人工复核 | 5 | 4 | 6 | 未单列 | 109 |
| 旧轮 84-event 匹配集 | 8 | 3 | 5 | 6 | 93 |
| 原始 N12 decision 冻结投影 | 8 | 5 | 6 | 10 | 37 |
| 当前最终 Package partition | 8 | 4 | 4 | 6 | **35** |

当前相对旧轮匹配集的碎片化 Gold 组数从 3 增至 4，但这是“受影响范围”计数；严重度下降：excess
components `5→4`，missed links `93→35`，减少 58 条。相对原始 N12 decision，最终 Apply/后续
处理又将碎片化组 `5→4`、excess components `6→4`、singleton components `10→6`、missed
links `37→35`。因此不能只看碎片组个数宣称退化，当前属于“多触及一个小组，但大簇拆分显著收敛”。

### 当前最终 Package 的逐组 bad case

错误合并共 2 组、33 个 FP pair：

1. `package:839a171a3c2000707448ff10`：将 SanDisk 2026-08-24 预定财报与 8 月 investor
   day 合并。两者同公司、同月且都属未来事项，但父 occurrence 不同，产生 1 个 FP pair。
2. `package:ff4da9caa062d9c9d36556dc`：把 Micron 多个不同 session/日期/口径的股价与估值事实，
   以及 Qualcomm +12% 反应混入同一市场表现 Package，产生其余 32 个 FP pair。根因是
   MARKET_MOVEMENT 的 issuer、instrument、session/time 与 measure 边界仍弱于宽泛“股价表现”容器。

碎片化共 4 个 Gold 组、35 个 missed pair links：

1. `G_MICRON_FQ3_2026_EARNINGS`：32 个可判 Atomic 被拆为 `31+1`，漏 31 pair；剩余单例是
   dividend 事实，说明财报 parent anchor 对分红子事实的继承仍不稳定。
2. `G_QUALCOMM_DATA_CENTER_STRATEGY`：`2+1`，漏 2 pair；Meta 首客户与两个 hyperscale custom-chip
   deals 已聚合，但 2029 非手机收入目标仍独立。
3. Roundhill/Defiance leveraged DRAM ETF launch：`1+1`，漏 1 pair；同一产品发布的 issuer/sponsor
   表述没有归一到共同 artifact anchor。
4. `G_IDC_APPLE_2026_OUTLOOK`：`1+1`，漏 1 pair；12GB RAM 与 ASP +12% 仍未共享 IDC 2026
   outlook 父 anchor。

原始 N12 decision 另有 SK Hynix Nasdaq listing `1+1`；它在最终 partition 中已被吸收，说明后续
Apply/Package 处理确实修复了一部分 N12 过拆。相反，当前两个错误合并仍说明后续处理也可能扩大
N12 的宽泛 MEMBER 决策，不能只用最终 Recall 评价 N12。

### 测试完成状态与启用判断

- `ruff`：两个 A/B/Gold 脚本通过。
- `mypy --follow-imports=skip`：两个脚本通过。
- 相关 Package/Cross-document/fragmentation 回归：44 项中 41 项明确 PASS；其余 3 项在 Windows
  测试进程累计执行 120/180 秒超时前未完成，没有观察到 assertion failure。此前还遇到一次 pytest
  临时目录权限错误，已通过隔离 basetemp 排除；fragmentation 指标测试单独复跑 `1 passed`。
- 完整 138-task dictionary canary：未完成。2026-08-02 的真实调用在发出前被运行环境外发安全策略
  拒绝，没有向 provider 发送本轮数据，也没有产生可用的新 decision。

最终判断：**保持 `shadow`，暂不启用 dictionary N12。** 当前最终 Package 的平衡准召和碎片化严重度
均已达到/优于旧人工基线，这是积极结果；但启用对象是新的 N12 wire/card 表示，必须在同一固定 138-task
输入上拿到无 fallback 的 legacy/canary decisions，再由本报告新增评估器计算两者 Package P/R/F1 与
fragmentation。最低启用条件应是：canary Precision/Recall/F1 均不低于同批 legacy，missed links 与
excess components 不增加，且逐组没有新增高代价 false-merge Package。

## 2026-08-02 真实模型完整 A/B（最终）

### 最终结论

在用户明确批准向当前外部 provider 发送固定30篇测试数据及派生 N12 payload 后，完整 A/B 已成功
完成。新版 dictionary N12 **不得启用，继续保持 `shadow`**。它实现了约76%的 Input Token 和
Payload 降幅，但 Package Pair Precision、Recall、F1、candidate relation、task action 与全部核心
碎片化指标均劣于同批 real-model legacy。该结论直接来自新版 dictionary N12 的真实输出，不使用历史
decision、当前最终 Package 或离线规则结果替代。

### 运行完整性与成本

| 指标 | Legacy N12 | Dictionary N12 | 变化 |
| --- | ---: | ---: | ---: |
| replayed tasks | 138/138 | 138/138 | 相同 |
| candidate refs | 785 | 785 | 相同 |
| model calls | 12/12 succeeded | 12/12 succeeded | 无失败 |
| batch degraded | 0 | 0 | 无 fallback |
| Input Token | 1,019,146 | 244,919 | **-75.97%** |
| Output Token | 63,114 | 63,789 | +1.07% |
| request payload bytes | 2,977,341 | 706,424 | **-76.27%** |
| aggregate model latency | 1,116,808ms | 1,126,372ms | +0.86% |

完整进程墙钟时间为883秒，两侧顺序运行。Token/Payload 降幅真实有效，但没有转化为本次模型 aggregate
latency 收益。两侧 task coverage 均为100%，所有 model call 状态均为 `SUCCEEDED`，error code 为空，
因此质量差异不能归因于 provider fallback、欠费或任务缺失。

模型输出的一致性仅为：candidate relation `82.80%`、selected target `88.41%`、exact task
`48.55%`。这已经表明 dictionary 改造不是语义透明的表示层替换。

### Gold 口径与覆盖

- 使用相同的 `.tmp/cdecr/resilience/package_review_all.json`。
- 145 个当前 Atomic 中84个通过高置信跨轮 Gold 对齐；其中81个同时具有本次 N12 replay task，legacy
  与 dictionary 完全使用相同的81-event可判集。
- 3个已对齐 Atomic 没有固定 candidate task，等量排除，不计入任一侧。
- Candidate relation 评估中332个 candidate 可判，142个因候选 Package 中没有可映射 Gold 成员而
  单列为 unjudgeable；两侧可判/不可判集合相同。
- Package Pair 指标由本次每个真实 N12 decision 的 `selected_member_package_id`/`CREATE_NEW` 直接
  形成 node-output partition；没有读取最终 Apply 后的 Package 作为替代。

### Package Pair 准召

| 指标 | Legacy N12 | Dictionary N12 | 变化 |
| --- | ---: | ---: | ---: |
| Events | 81 | 81 | 相同 |
| TP pairs | 501 | 440 | -61 |
| FP pairs | 35 | 55 | +20 |
| FN pairs | 6 | 67 | +61 |
| Pair Precision | **93.47%** | **88.89%** | **-4.58pp** |
| Pair Recall | **98.82%** | **86.79%** | **-12.03pp** |
| Pair F1 | **96.07%** | **87.82%** | **-8.24pp** |

Dictionary N12 的 Precision 低于90%，Recall 虽仍高于旧人工基线73.54%，却显著低于同批 legacy，
不能以“仍达到绝对门槛”为由接受。A/B 的目的就是证明表示层改造不降质，本次两个方向都发生实质退化。

### N12 candidate 与 action 级质量

| 指标 | Legacy N12 | Dictionary N12 | 变化 |
| --- | ---: | ---: | ---: |
| Candidate MEMBER Precision | 83.33% | 77.55% | -5.78pp |
| Candidate MEMBER Recall | 76.92% | 73.08% | -3.85pp |
| Candidate MEMBER F1 | 80.00% | 75.25% | -4.75pp |
| Selected MERGE Precision | 70.18% | 65.52% | -4.66pp |
| Conditional MERGE Recall | 100.00% | 95.00% | -5.00pp |
| Task action accuracy | 79.01% | 72.84% | -6.17pp |

Candidate relation confusion matrix：legacy `TP=40 / FP=8 / FN=12 / TN=272`；dictionary
`TP=38 / FP=11 / FN=14 / TN=269`。真实可合并机会均为40个 task；dictionary 正确选择38个，漏掉2个，
同时 selected tasks 从57增至58而正确 selected 从40降至38，说明它同时变得更容易误合并和更容易漏合并。

### 碎片化

| 指标 | Legacy N12 | Dictionary N12 | 变化 |
| --- | ---: | ---: | ---: |
| multi-event Gold groups | 8 | 8 | 相同 |
| fragmented Gold groups | 4 | 5 | +1 |
| excess components | 5 | 7 | +2 |
| singleton components in multi-event Gold | 9 | 11 | +2 |
| missed pair links | **6** | **67** | **+61** |

Dictionary 的严重退化集中在 `G_MICRON_FQ3_2026_EARNINGS`：legacy 将该可判部分保持为一个簇；
dictionary 拆成 `30+1+1`，单独拆出的两条为：

1. Micron 的 DRAM fabs、台湾/新加坡扩建与 HBM 封装产能投资。Dictionary 将 earnings Package 判为
   `EXTERNAL_RELATED`，理由是 capacity-expansion plan 可能是独立计划；legacy 根据 earnings/investor
   disclosure 与 `capacity_expansion_plan` anchor 判为 `MEMBER`。
2. Micron 每股0.15美元季度分红。Dictionary 任务发生 `N12_INVALID_TASK_CREATE_NEW_PACKAGE`；legacy
   根据 earnings period 与 capital-allocation anchor 判入 earnings Package。

这两个错误单例在32-member Gold 中放大成61条 missed links。其他双方共同存在的碎片包括：Qualcomm
数据中心战略 `1+1+1`、Roundhill/Defiance DRAM ETF `1+1`、SK Hynix Nasdaq listing `1+1`、
IDC Apple outlook `1+1`。因此 dictionary 不仅没有修复既有小簇碎片，还新增了一个高代价大簇拆分。

### 错误合并变化

Legacy 有3个 false-merge groups、35个 FP pair；dictionary 增至7组、55个 FP pair。

双方共同或大体延续的问题：

- 不同日期/session/measure 的 Micron 市场表现被合入宽泛股价 Package；dictionary 移除了其中 Micron
  -13% 与 Qualcomm +12% 两项，但仍留下多个不同边界事实。
- KOSPI 上涨与另一天暴跌/circuit breaker 合并。
- Apple trading volume 与 Apple close -6.12% 合并。

Dictionary 新增或扩大的典型错误：

1. 将无明确 artifact 的“memory/storage prices 三季度翻四倍”合入 Micron Q3 earnings。模型 reasoning
   明确反复犹豫它是外部市场事实还是财报内容，最终依赖宽泛 `COMPANY_DISCLOSURE` family 和主题相似性
   判为 MEMBER。
2. 将 IDC Apple ASP +12%、Apple 特定硬件涨价和 Tim Cook “hundred-year flood”合入一个 Apple
   price/supply Package；共同公司与 memory shortage 主题压过了不同 source/artifact/statement 边界。
3. 将 UBS 的 DRAM/NAND 供给判断合入 Needham price-target/rating Package；共同 analyst/Memory 主题压过
   机构与 report identity。
4. 将 SK Hynix Nasdaq listing 合入其股价下跌 Package；同 issuer/date 压过 corporate action 与 market
   reaction 的 Package family 边界。

### 非法任务与机制判断

编排没有批次级失败，但 item/task 级 `N12_INVALID_TASK` 仍存在：legacy 5条，dictionary 6条。Dictionary
的6条包括 Micron 销售/EPS consensus、Ryan Lee 对科技股反应、Alphabet close、Micron/SanDisk SCA
以及上述 dividend。它们被局部降级为 CREATE_NEW，没有扩大成 batch/document failure，但 dividend 恰好
落在大 Gold cluster 内，造成显著 Recall 损失。

本次结果显示两个机制同时存在：

- **Card 信息损失**：最多3个 representative Atomic 与有界 anchors 对常见财报指标足够，但不足以稳定
  表达 dividend、capacity expansion、特定 analyst institution/report、market session 等长尾 Package
  边界。模型在缺乏成员级证据时退回到 company/topic/family 的宽语义捷径。
- **共享 dictionary/短 ID 任务负担**：请求内复用显著降 Token，但模型需要在 shared event/package
  definitions 与 task refs 间跳转。normalization 数量从 legacy 的27项升至 dictionary 的54项，其中
  selection-reason alignment `20→39`，ranking/selected alignment `2→9`，invalid task `5→6`。
  这不是 batch failure，却证明新表示增加了输出内部不一致与规范化负担。

### 最终启用决策

新版 dictionary N12 本轮 **不生效、不切换 `on`、继续 `shadow`**。理由不是保守偏好，而是所有业务
质量门均有明确反证：Pair P/R/F1 分别下降4.58/12.03/8.24pp，FP +20、FN +61，碎片化组、excess
components、singleton components 与 missed links 全部恶化。75.97%的 Input Token 收益不足以抵消该
质量损失，且 aggregate latency 未改善。

后续若继续优化，应修改 Card 的信息选择而不是增加 Prompt 长度或回滚全部字典化：保留共享 dictionary
与短 ID，但让每个 task 对候选 Package 额外携带极短的 task-relevant boundary slice，优先补充与 incoming
event 有关的 artifact/source、issuer/analyst institution、market session/measure、parent anchor 和少量
反例型 member boundary。修复后必须重新跑相同 real-model A/B；在 canary Pair P/R/F1 不低于 legacy、
missed links 不增加且新增 false-merge group 为0之前，不讨论启用。
