# CDECR Parent Occurrence Package V2：真实 30 篇深度归因、全量非 Singleton 审计与效能对比

> 日期：2026-08-12  
> 审计对象：`parent_v2_final_review_replay.sqlite3` 的最终 75-Package 冻结分区  
> 对照报告：`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`、`CDECR_DETERMINISTIC_RUNTIME_FIELD_FIX_30_REAL_RERUN_REPORT_20260808.md`  
> 本轮操作：只读 SQLite、最终层级、Gold 和报告；未重新调用模型、未修改工作流

## 1. 结论先行

V2 的质量回归由两个相反但同时发生的机制造成：

1. **前段过度聚合**：Parent Induction 已经把 earnings disclosure、股价反应、独立 SCA、估值和宽泛 AI/memory 主题写入同一个 proposal；R1/R2 又把这些 proposal 作为大体不可分的集合继续归一，形成 79-Atomic 的 Micron earnings 超大污染簇。
2. **后段过度拆分**：oversized review 为了清理大簇，使用“一个 default parent + event exceptions”的压缩协议。它确实移除了一些错误 pair，但也拆走了大量真正属于 earnings 的 Atomic；同一 Micron FQ3 Gold 父发生最终被拆为 12 个 Package 组件。

在同一 99 个高置信 Gold 对齐 Atomic 上：

- 上一实际 Package：TP/FP/FN=`614/142/181`，P/R/F1=`81.22%/77.23%/79.17%`；
- V2 最终：TP/FP/FN=`287/163/508`，P/R/F1=`63.78%/36.10%/46.10%`。

即 V2 不只是“为了 Precision 多拆了一点”：它丢失了 327 个正确 pair，同时还新增了 21 个错误 pair。最终 review 相对 V2 67-Package 中间态减少 92 个 FP，却又损失 127 个 TP，说明 review 没有学会稳定的父发生边界，只是在错误连接和正确连接之间做不稳定切割。

本轮逐一审查全部 48 个非 singleton Package 后，主口径结论为：

| 指标 | 结果 |
| --- | ---: |
| 非 singleton Package | 48 |
| 明确存在内部误合并 | **17/48 = 35.42%** |
| 边界敏感、暂不计入误合并 | 4/48 |
| 明确干净 | 27/48 |
| 非 singleton 内 Atomic | 254 |
| 需要移出当前 Package 的误合并 Atomic | **61/254 = 24.02%** |
| 误合并 Atomic 占全部 281 Atomic | 21.71% |

其中 29/61 个错误 Atomic 来自唯一的 79-Atomic Micron earnings 簇，占全部内部误合并的 47.54%。因此用户关于“singleton 小基数波动不是核心，超大簇才是主要风险”的判断成立。

综合质量与成本，本报告建议：**生产候选回滚到 V1 语义；V2 分支和 Registry 保留为离线研究样本，不在当前活跃路径继续叠加补丁。** 如果还想验证 V2，先做冻结 Atomic 的 Package-only 反事实回放，在 proposal purity、candidate coverage、超大簇错误率和 Recall 同时过门槛后，再决定是否重新进入真实全流程。

## 2. 审计口径

### 2.1 业务边界

以 V2 自己的 Prompt 为准：Package 表示同一个 bounded parent occurrence 或 identifiable continuing matter，而不是共同文章、公司、日期、主题或宽泛上下文。特别采用当前 review Prompt 已明确的边界：

- disclosure 与其市场/分析师 reaction 是不同父发生；
- 独立 contract/agreement 是不同父发生；
- 不同分析机构的报告是不同父发生；
- broad industry、product、valuation theme 不能只因出现在同一报道中进入 disclosure Package；
- 同一 market session 的显式 roundup 可以作为父发生，但不能把不同日期/时段的涨跌自动合成一个“市场周期”。

### 2.2 “误合并 Atomic”如何计数

对于一个包含多个真实父发生的 Package，先选取其中最大的语义一致父发生作为保留主体，其余成员计为需要移出的误合并 Atomic。因此这不是把混包内所有 Atomic 都算错，而是“使该 Package 恢复单一父边界所需的最少移出数”。

主口径按当前 Prompt 的严格父发生定义计数。为避免伪精确，同时报告一个更保守的敏感性下限：如果沿用上一轮独立审计对最大簇仅认定 22 个明显异类，并暂不把 global selloff/recovery 与 valuation/performance 两个复合 Package 判错，则为 **15 个混包、45/254=17.72% 错误 Atomic**。无论采用主口径还是保守下限，结论都远高于 5% 的超大簇/错误成员目标。

### 2.3 Gold 的作用与限制

现有 Package Gold 只高置信对齐 99/281 Atomic，覆盖率 35.23%。它用于验证准召和定位已知组，但不能代替本轮全量人工业务判断。Gold 将 KOSPI sidecar、同一 market roundup 中不同 instrument、供应变化与价格结果等拆得较细；对于这些仍可能属于同一父发生的情况，本轮单列“边界敏感”，不强行计入主误合并数。

## 3. 为什么 V2 同时降低准召、提升碎片化

### 3.1 污染首先发生在 Parent Induction，而不是最终 Apply

最终 79-Atomic Micron earnings Package 由 27 个 document-local proposal 支撑。按本轮父边界复核，其中 **17/27 个 proposal 在 Induction 阶段就已经包含至少一个异类 Atomic**。真实例子：

- `parent-proposal:c4b0e1ee...` 标为“earnings report and related announcements”，却把 after-hours stock reaction `atomic:e770e8b3...` 标成 `DISCLOSED_IN`；
- `parent-proposal:9a28e94e...` 同时包含 earnings、SCA、market cap、after-hours reaction、valuation 和 contract uncertainty；
- `parent-proposal:43511df0...` 把 earnings、HBM供给主题、分析师周期性判断和市场预期解释放在同组；
- `parent-proposal:f8a736c9...`、`e35c44e8...` 的 label 直接写成“earnings release and investor/market reaction”，与 Prompt 要求 reaction 分离正面冲突。

这些输出 Schema 都合法，所以 coverage validator 无法发现。后续 R1/R2 看到的是已经污染的 proposal 卡片；在正常 resolution 阶段，proposal 基本是不可拆单位，因此错误从文档内阶段被放大为跨文档 supercluster。

### 3.2 相同 issuer/period/family 成为比父边界更强的语义捷径

Micron 语料中大量 proposal 同时具有：

- participant=`COMPANY_MU`；
- period=`FY2026 Q3/Q4`；
- family=`EARNINGS_DISCLOSURE/GUIDANCE`；
- 共同出现 AI memory、supply、revenue、gross margin、guidance 等词。

模型因此把“同一 earnings episode 的上下文”错误提升为“同一个父发生”，并把 SCA、股价反应、估值与行业叙事当作其内容。V2 删除了旧路径中 instrument/session/reaction/analyst 等 Apply 前的窄边界后，最终集合归一主要依赖模型语义判断；当前模型在结构化 identity 不足时偏向上述共同上下文捷径。

### 3.3 R2 没有完成预期的二次补召回

| 阶段 | proposal coverage |
| --- | ---: |
| R1 | 91.59% |
| R2 | 87.80% |
| 门槛 | >=98% |

R2 coverage 不仅没有补齐 R1，反而更低。不同文档对同一 earnings 父发生使用不同 proposal label；若它们没有共同进入候选邻域，resolver 不可能恢复连接。最终 `G_MICRON_FQ3_2026_EARNINGS` 的 40 个 Gold Atomic 被切为 12 个组件 `[24,4,2,2,1,1,1,1,1,1,1,1]`，单这一组就产生 496 个 missed pair links。

### 3.4 Oversized review 是有损切割，不是稳定语义修复

67-Package 中间态到最终 75-Package：

- Precision：61.88% -> 63.78%，仅 +1.90pp；
- Recall：52.08% -> 36.10%，-15.97pp；
- F1：56.56% -> 46.10%，-10.45pp；
- FP：255 -> 163，减少 92；
- FN：381 -> 508，增加 127。

review 的“default parent + explicit event exceptions”协议在少数清晰例外时节省 payload，但面对已经包含多个父发生的 96/79-member 大簇时，存在两个问题：

1. 模型需要在大量紧凑 Atomic 卡片中一次找齐所有例外，遗漏项自然留在 default；
2. 模型列出的 exception 同时含错误成员和真正 earnings facts，reducer 只能确定性消除重复，无法替模型恢复语义。

所以最大簇虽从中间态 96 缩到 79，却仍有显著污染；同时真正 earnings facts 被拆到 `ea0d3378...`、`a4bb33ee...`、`c51206d5...`、`bce6e1f8...` 等多个 Package。

### 3.5 上游 Atomic 污染进一步模糊 Package 边界

`atomic:654d1dbc...` 自身有 16 个 Mention，至少 6 个并非同一个“FQ3 revenue $41.5B”最小事实，混入 EPS、data-center run-rate、$11.3B/37% 和泛化 beat。这类 Atomic 对 Package V2 来说是不可再拆的输入单元，会同时污染 proposal summary、metric cues 和代表事实。

这不是本轮 Package 回归的唯一原因，但解释了为什么仅靠 Package Prompt 很难形成稳定边界。

## 4. 全部 48 个非 Singleton Package 逐包审查

判定说明：`MIXED` 计入主误合并统计；`CLEAN` 未见明确跨父发生；`SENSITIVE` 表示 Gold 与父发生业务定义存在粒度争议，本轮不计入主误合并数。

| # | Package（短 ID） | Size | 判定 | 最少误合并 Atomic | 审查结论 |
| ---: | --- | ---: | --- | ---: | --- |
| 1 | `4723fa8c` Micron FQ3 earnings | 79 | **MIXED** | **29** | earnings 与 market reaction、valuation、SCA、capital return、行业/产品叙事混合 |
| 2 | `710a1841` Global AI selloff/recovery | 14 | **MIXED** | **7** | Tuesday selloff 与 Thursday recovery 被合成一个跨时段父发生 |
| 3 | `6933a2cd` Wedbush/Ives report | 12 | CLEAN | 0 | 可归于同一 Wedbush/Ives 分析发生 |
| 4 | `b39e3274` SK Hynix US listing | 7 | **MIXED** | **4** | listing announcement 与短期/12个月股价、market-cap reaction 混合 |
| 5 | `2210c6af` Citi/Sandisk target note | 6 | CLEAN | 0 | 目标价、判断与同一研究说明一致 |
| 6 | `89acf71e` KOSPI June 25 opening | 6 | SENSITIVE | 0 | Gold 拆 sidecar/指数；业务上可视为同一开盘 session 的操作与资金流 |
| 7 | `cb14483c` Apple trading-range analysis | 6 | **MIXED** | **2** | 技术交易区间中混入 Apple Intelligence/设备支持事实 |
| 8 | `ea0d3378` Micron Q3 release/call | 6 | CLEAN | 0 | 当前成员可归于同一 earnings disclosure/call |
| 9 | `56f1b41a` Apple price increase | 5 | **MIXED** | **1** | 产品涨价 announcement 混入股价下跌 reaction |
| 10 | `7c7ea391` MU valuation/performance | 5 | **MIXED** | **2** | forward valuation、YTD performance、投资建议被合成泛投资主题 |
| 11 | `a4bb33ee` Micron capex guidance | 5 | CLEAN | 0 | 同一资本开支 guidance 事项 |
| 12 | `c51206d5` Micron CEO earnings-call statements | 5 | CLEAN | 0 | 同一 call 中供需/AI 结构判断 |
| 13 | `cea1d64f` Micron earnings + immediate response | 5 | **MIXED** | **3** | earnings、市场信念/股价 reaction、$22B SCA 混合 |
| 14 | `a026c844` Micron SCA announcement | 4 | CLEAN | 0 | 同一 SCA 事项 |
| 15 | `a5657a53` June 25 index roundup | 4 | CLEAN | 0 | 同源同 session 的市场 roundup |
| 16 | `d8dcff58` Needham/Micron target | 4 | CLEAN | 0 | 同一 Needham analyst action |
| 17 | `f1997339` June 25 broad market close | 4 | SENSITIVE | 0 | Gold 按 instrument 拆；业务上可视为显式同源收盘 roundup |
| 18 | `276478b9` Micron forecast + peer reaction | 3 | **MIXED** | **2** | Micron forecast 与 Samsung/KOSPI reaction 混合 |
| 19 | `2f936208` Micron guide + value surge | 3 | **MIXED** | **2** | gross-margin guidance 与 market-cap/stock reaction 混合 |
| 20 | `3659b0bd` Micron AI adoption outlook | 3 | CLEAN | 0 | 同一公司前瞻判断 |
| 21 | `3d86dfc8` Peer sympathy rally | 3 | CLEAN | 0 | 同一 Micron-results-triggered peer reaction episode |
| 22 | `40486194` Sandisk investor day | 3 | CLEAN | 0 | 同一计划中的 investor-day occurrence |
| 23 | `46b0d3bb` Micron CEO AI/supply statements | 3 | CLEAN | 0 | 同一 CEO disclosure context |
| 24 | `5f6f44af` Qualcomm data-center update | 3 | CLEAN | 0 | 同一业务/产品更新 |
| 25 | `64f06a9b` IDC iPhone RAM/ASP forecast | 3 | CLEAN | 0 | 同一 IDC forecast/report |
| 26 | `6b4c6ea8` MU/NVDA valuation comparison | 3 | CLEAN | 0 | 同一比较性估值分析 |
| 27 | `a8f77c00` Apple price-hike commentary | 3 | **MIXED** | **1** | memory-cost commentary 混入 Apple intraday reaction |
| 28 | `b5172546` Mizuho/BofA coverage | 3 | **MIXED** | **2** | 不同机构报告与独立 capital-return 分析被并为一个发生 |
| 29 | `bce6e1f8` Micron earnings + RSI | 3 | **MIXED** | **1** | earnings facts 混入 RSI market reaction |
| 30 | `c12c9fb9` Micron SCA terms | 3 | CLEAN | 0 | 同一 SCA 事项 |
| 31 | `eff53548` MU June 25 surge | 3 | **MIXED** | **1** | 当日 surge/market value 混入过去一年 700% performance |
| 32 | `f4e99724` SCA + cash return | 3 | **MIXED** | **1** | SCA 混入独立 shareholder-return commitment |
| 33 | `03b265eb` AI memory demand/supply | 2 | CLEAN | 0 | 同一持续供需事项 |
| 34 | `0b05d320` Micron quarterly gross margin | 2 | CLEAN | 0 | 同一季度财务披露 |
| 35 | `2028e20f` Citi post-earnings note | 2 | CLEAN | 0 | 同一研究报告及目标价上下文 |
| 36 | `2510232b` HBM/NAND supply reallocation | 2 | CLEAN | 0 | 同一供应重分配过程 |
| 37 | `29913f7b` Magnificent Seven decline | 2 | CLEAN | 0 | 同一 megacap selloff episode |
| 38 | `6974bd92` SK Hynix/Samsung context | 2 | **MIXED** | **1** | KOSPI 权重与 AI trillion-dollar club 只是共同实体上下文 |
| 39 | `71c1bd2d` Micron AI-memory transformation | 2 | CLEAN | 0 | 同一管理层持续判断 |
| 40 | `7e978651` Bernstein/SCA commentary | 2 | CLEAN | 0 | 同一 Bernstein 分析发生 |
| 41 | `a0b7fc6c` HBM shift and price surge | 2 | SENSITIVE | 0 | Gold 拆分；业务上可视为同一 supply-price 因果过程 |
| 42 | `ab8b1c9b` Micron-Anthropic partnership | 2 | CLEAN | 0 | 同一 partnership/investment announcement |
| 43 | `b5d63f23` Micron/Qualcomm reactions | 2 | **MIXED** | **1** | 两家公司不同 announcement 的 reaction 仅因同文出现而合并 |
| 44 | `b800dba4` AI-memory supply tightening | 2 | CLEAN | 0 | 同一持续供需事项 |
| 45 | `c3aa2ffb` Sandisk upcoming earnings | 2 | SENSITIVE | 0 | Gold 拆 schedule/expectation；业务上均指向同一未来 earnings occurrence |
| 46 | `cbdb9fd8` Micron cash-return commitment | 2 | CLEAN | 0 | 同一 capital-return policy |
| 47 | `d38d6c32` Defiance 2X DRAM ETF | 2 | **MIXED** | **1** | ETF launch 混入 Micron-results/memory-trade 主题背景 |
| 48 | `e58869cf` UBS DRAM/NAND outlook | 2 | CLEAN | 0 | 同一 UBS supply outlook |

## 5. 误合并 Atomic 分布

### 5.1 总量与集中度

- 17 个明确 MIXED Package 共含 148 个 Atomic；其中 61 个需要移出。
- 61/254=24.02% 的非 singleton Atomic 处于错误父发生中。
- 最大 `4723fa8c...` 一簇贡献 29 个，占全部错误 Atomic 的 47.54%。
- 前两大错误簇 `4723fa8c...` 与 `710a1841...` 合计 36 个，占 59.02%。
- 因 Pair Precision 按 pair 计数，大簇错误不是线性影响：一个错误成员会与大量正确成员形成 FP pair。这解释了为什么少数污染簇足以把整体 Precision 拉低。

### 5.2 主要 MIXED Package 的错误成员

| Package | 错误 Atomic（短 ID） | 业务类型 |
| --- | --- | --- |
| `4723fa8c` | `0970ca1a,11423b95,21a7f6ce,23938b44,366c1cc3,83d92f7b,89d7adce,93899f49,b6db2b8e,b8eb4ea5,c8fa3ec2,e770e8b3` | market/analyst reaction、valuation |
|  | `05867329,2e81f026,6ce959de,b3573175,b923f3fe,eb2299ce` | 独立 SCA/contract matter |
|  | `015e9cfc,0999cce6,312bf41a,3179b0aa,58cb7dcf,622dbddd,748e5467,9af7c1ae,d2509e8d,e4482448` | broad industry/product narrative |
|  | `1bd4bccd` | 独立 shareholder-return matter |
| `710a1841` | `32a056e0,36b9f67f,7675ecc0,99590c6c,d3d4ea43,e41e8d29,f7d9fbfa` | Thursday recovery，相对 Tuesday selloff 是另一个 occurrence |
| `b39e3274` | `426ffe08,6411a5af,8bfbdd6a,e5ae76e5` | listing 后 reaction 与过去一年 performance |
| `cb14483c` | `7c686525,97e2bda6` | Apple Intelligence/product compatibility |
| `cea1d64f` | `3e64bd9a,64b5f354,c158b302` | stock reaction、AI-market sentiment、SCA |
| `276478b9` | `93185c2f,9512ec48` | Samsung/KOSPI reaction |
| `2f936208` | `35f193a4,56fe5231` | market-cap/stock reaction |
| `b5172546` | `25fe94f6,c1c1f05e` | 独立 capital-return 分析与 BofA valuation，不属于 Mizuho report |
| 其余 9 个 MIXED | 每个 1-2 个，详见逐包表 | reaction、跨机构报告、跨时间 performance、共同主题 |

### 5.3 最典型与最严重案例

#### Case A：`4723fa8c...`，79 Atomic，29 个错误成员

这是数量、pair 影响和业务风险都最严重的 case。Package 标题是 Micron FQ3 earnings，但内部同时包含：

- 正确主体：Q3 revenue/net income/margin、Q4 guidance、capex disclosure、earnings-call facts；
- 六类 SCA/contract 事实；
- 八类直接或假设性股价/market-cap reaction；
- forward P/E valuation；
- 外部分析师周期性/market-expectation 判断；
- humanoid memory、physical AI、行业供需等宽泛叙事；
- 独立 shareholder-return commitment。

主口径错误率为 29/79=36.71%；即使采用上一轮最保守的 22 个明确异类，仍为 27.85%。它同时造成 Precision 和 Recall 问题：错误事实留在大簇中形成 FP，正确 earnings facts又散落在另外 11 个组件中形成 FN。

#### Case B：`710a1841...`，14 Atomic，最少 7 个错误成员

模型把 Tuesday global AI selloff 与 Thursday cross-market rebound 合成“selloff and recovery”。共同 AI 主题和相邻日期不能自动创建一个离散父发生。该簇还跨 KOSPI/Nikkei/Stoxx/Dow/Nasdaq 以及不同 session；若没有一个明确 artifact 定义整个回顾性 episode，至少应按 selloff/recovery 两个 occurrence 拆分。

#### Case C：`b39e3274...`，7 Atomic，4 个错误成员

三个 listing announcement 事实与 Thursday stock reaction、market-cap milestone、过去 12 个月涨幅混在一起。listing 是父发生，短期反应应为 external relation，12个月表现更不是同一 occurrence。

#### Case D：earnings + reaction 的重复错误模板

`cea1d64f...`、`276478b9...`、`2f936208...`、`bce6e1f8...` 均重复同一模式：模型或 proposal label 直接把“disclosure and reaction”当作合法父发生。说明这不是单一偶发 bad case，而是 Prompt 边界在 Induction/Resolution 中没有成为实际约束。

#### Case E：不同 analyst/report 被共同“coverage”吞并

`b5172546...` 将 Mizuho target、Bank of America valuation 与 share-repurchase capacity 分析合成一个 Package。共同 issuer=Micron 不能替代 report/institution identity。

## 6. 本次运行总时长与 Token，对比 2026-08-08 R2

### 6.1 可审计的总口径

V2 Registry 的首次单文档 run 从 `2026-08-11T08:12:29.565635Z` 开始，最终 epoch 在 `2026-08-11T10:14:46.306360Z` 完成，因此从发起到最终 75-Package 产物的实际历时为：

```text
7,336,741 ms = 2小时02分16.741秒
```

该历时包括断点恢复、8 个 FAILED cross-document run、4 个被中断后遗留为 RUNNING 的 run、两次成功 cross-document completion 以及最终 review replay。它是“为了得到当前最终产物真实花掉的端到端时间”，不是一次无故障稳态性能。

模型调用累计值是 Registry 中实际留下的完整消费记录：

| 指标 | 2026-08-08 R2 | Parent V2 最终产物过程 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 820 | **1,131** | +311 / +37.93% |
| Input Token | 1,321,345 | **2,557,172** | +1,235,827 / **+93.53%** |
| Output Token | 1,061,669 | **565,104** | -496,565 / -46.77% |
| Total Token | 2,383,014 | **3,122,276** | +739,262 / **+31.02%** |
| 累计模型延迟 | 7,391,834 ms | **8,183,638 ms** | +791,804 / +10.71% |
| 端到端墙钟 | 1,116,711 ms（18分36.711秒） | **7,336,741 ms（2时02分16.741秒）** | +6,220,030 ms / **+557.00%** |

### 6.2 Parent V2 新路径的成本贡献

所有 `parent_*` stage 累计：

- 120 calls；
- input 1,151,404；output 98,013；
- total 1,249,417 Token，占本轮全部 Token 的 **40.02%**；
- 累计模型延迟 1,691,083 ms，占 20.66%。

其中主要成本：

| Parent stage | Total Token |
| --- | ---: |
| Induction（含失败记录） | 581,686 |
| Induction repair | 127,873 |
| Reconcile | 285,229 |
| Reconcile repair | 87,732 |
| R1 | 47,115 |
| R2 | 81,665 |
| Parent embedding | 38,117 |

与 2026-08-08 R2 的 N12 assignment 270,466 Token 相比，V2 Parent 累计成本高 978,951 Token、约为 4.62 倍。但这不是干净的节点 A/B：

- R2 当时有 119 次 Package LLM 请求因 provider 402 被立即拒绝，Wave C/N13 后段 Token 被人为压低；
- V2 累计值包含失败、repair、断点恢复和多次 review 实验；
- 最终幂等 replay 只花 1.489 秒且 0 model call，但它复用了全部 checkpoint，不能冒充完整 V2 稳态成本。

因此可以可靠说：**为了得到当前 V2 最终结果，实际 Token 比 R2 多31.02%、实际 elapsed wall 多557%；新增输入成本主要由 Parent cards、Induction 和 Reconcile 驱动。** 不能可靠说“如果一次无故障跑完，V2 必然需要2小时”——当前没有一份从空 Registry 到最终分区、无中断无调试的纯净 V2 时长记录。

### 6.3 为什么 Input 几乎翻倍而 Output 下降

- V2 把完整文章/Atomic evidence 送入 30 个 Induction task，并在 R1/R2/Reconcile 重复呈现 proposal/prototype cards，导致 Input +93.53%。
- 当前 M2/M3/M4 thinking 为 none/low/high，且 Parent 输出主要是短 ID partition，Output 比旧 R2 下降46.77%。
- 总量仍上升31.02%，说明 dictionary/短 ID 节省的输出不足以抵消多轮集合归一的输入重复。

## 7. 是否继续优化还是回滚

### 7.1 不建议在当前活跃 V2 上继续窄补丁

当前问题横跨三个层级：

1. Induction proposal purity 不足；
2. R1/R2 candidate coverage 不足且不单调；
3. oversized review 不能稳定 event-level 拆分。

这不是加一句 Prompt 或一个 hard rule 就能闭环。若直接增加 reaction/SCA/analyst 等硬边界，会减少 supercluster，但当前 Recall 已只有36.10%，很容易继续制造碎片；若再放宽 recall，又会让污染 proposal 更容易扩散。继续叠加规则会形成新的复杂度和指标摆动。

### 7.2 推荐动作

1. **发布/生产语义回滚到 V1 基线**，并同步恢复 Package 数据快照；不要只回滚 Git。
2. 保留 V2 分支、最终 Registry、proposal/checkpoint 和本报告，作为离线实验材料。
3. 若还要救 V2，只在冻结 Atomic Package-only harness 中进行，不先跑完整30篇或MU300。
4. 下一次进入真实全流程前，至少同时满足：
   - document-local proposal purity >=95%，reaction/SCA/不同 analyst report 不进入 disclosure proposal；
   - R2 candidate coverage >=98%，且不得低于 R1；
   - 最大/`size>=8` Package 错误成员 <=5%；
   - Package Pair Precision >=90%、Recall >=82%；
   - Parent 完整单次 Token 不高于356k，且使用无恢复污染的全新 Registry 计时。

如果上述 Package-only 门槛不能在不增加复杂规则堆叠的情况下达到，应停止 Parent V2 路线，而不是继续依靠后置 review 修补前置 proposal 污染。

## 8. 证据文件

- `dev_plan/CDECR/CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`
- `dev_plan/CDECR/CDECR_DETERMINISTIC_RUNTIME_FIELD_FIX_30_REAL_RERUN_REPORT_20260808.md`
- `.tmp/cdecr/parent_v2_final_review_replay.sqlite3`
- `.tmp/cdecr/parent_v2_final_clusters.json`
- `.tmp/cdecr/parent_v2_final_package_gold_eval.json`
- `.tmp/cdecr/parent_v2_final_hierarchy.md`

