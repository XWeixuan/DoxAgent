# CDECR Grounder 真实工作流 30 篇质量报告

## 1. 运行边界与语料

- 运行日期：2026-07-22。
- 数据：`US / MU / 2026-06-25 UTC`，从 Supabase `public.raw_media` 通过受限 REST GET 读取；实时返回 250 条原始记录，153 条满足基础 SourceMessage 条件。
- 30 篇组成：最近 Dreamer/Grounder 实验 10 篇、旧步骤二语料中重新审核的 10 篇、本次 DB 新抽并全文审核的 10 篇。
- 为解决“新增 10 篇 + 上次 10 篇 = 30 篇”的数量冲突，采用上述 `10 + 10 + 10` 可追溯口径。三组仅使用互不重复的 row id。
- 新抽 10 篇均为完整单篇新闻或单主题分析，长度 1,059–4,170 字符。审核中剔除了 1 篇带时间戳访谈转录、1 篇盘中直播聚合和 1 篇多主题市场综述。
- 工作流严格停在 `Dreamer (M2) -> Grounder (M3)`。没有执行 M4 Judge、Normalization、Atomic Event、Event Package 或跨文档共指。
- 模型：`deepseek-v4-flash`、`qwen3.7-plus`；JSON mode；reasoning effort=`none`。

冻结清单见 `experiments/grounder_quality_30_manifest.json`；全文、Registry 和完整模型输出只保存在 Git 忽略的 `.tmp/cdecr/grounder_quality_v5/`。

## 2. 完成情况

| 项目 | 结果 |
|---|---:|
| 文档完成 | 30 / 30 |
| 最终 Grounder mentions | 227 |
| 每篇 mentions | min 1 / median 7.5 / max 20 |
| mention 数量 > 8 的文档 | 11 / 30 |
| 最终成功路径模型调用 | 60（每篇 Dreamer、Grounder 各一次） |
| 最终成功路径 JSON repair | 0 |
| 输入 / 输出 token | 139,207 / 121,047 |
| 累计模型延迟 | 1,864,294 ms（并发运行，非墙钟时间） |
| EventMention Schema | 227 / 227 有效 |
| EvidenceSpan 原文切片 | 248 / 248 有效 |

首轮有 4 篇在模型成功返回 JSON 后被本地语义校验拒绝：3 个引文在同段重复而无法唯一定位，4 个引文不是原文精确子串。换用全新 Registry、禁止复用失败缓存后，仅重跑这 4 篇并全部成功。该结果说明 JSON 合规不能替代证据语义校验；首轮 Grounder 后可落地完成率是 26/30（86.7%），恢复后为 30/30。

## 3. 人工逐条判定口径与总评

逐条查看了全部 227 条 mention 的原文、证据、命题和所有字段。判定口径：

- `PASS`：事件成立、证据支持、基本原子化，核心字段没有实质错误。
- `PARTIAL`：事件本身受原文支持，但至少存在一个需要下游修订的实质字段问题，例如日期、事件族、Assertion State、主体分离或原子性。
- `FAIL`：非事件噪声、节目出席/免责声明等无业务价值事件，或明显不应进入事件集合。

| 判定 | 数量 | 比例 |
|---|---:|---:|
| PASS | 118 | 52.0% |
| PARTIAL | 99 | 43.6% |
| FAIL | 10 | 4.4% |

因此，Grounder 的“有原文支持的候选保留能力”较好，但“无需 Judge 即可直接进入跨文档共指”的严格可用率只有约 52%。`PASS + PARTIAL` 不能解释为最终精确率：PARTIAL 中包含会污染共指身份的日期、原子性和断言状态错误。

按语料组看：最近实验 10 篇为 `37/33/3`，旧步骤二 10 篇为 `52/35/5`，新抽 10 篇为 `29/31/2`（依次为 PASS/PARTIAL/FAIL）。新抽语料的严格通过率较低，主要由相对日期错误和多指标合并造成，并非证据失配。

## 4. 按字段评估

### EvidenceSpan

- 最终 248 个 Span 全部满足左闭右开切片与原文完全一致，最终有效率 100%。
- 首轮仍暴露 7 个 LLM 引文定位缺陷，涉及 4 篇文档；独立 Registry 重跑才恢复。因此当前实现具备可靠的“拒绝错误证据”能力，但 Grounder 单次成功率仍需提高。

### Canonical Proposition 与 eventhood

- 大多数命题忠实概括证据；没有发现最终 Span 指向原文外内容。
- 主要噪声是把评论、行业观点、节目出席和作者持仓免责声明当作事件。例如“Jim Lebenthal joins CNBC's Halftime Report”、作者无持仓披露、分析师对 AI 周期的泛化评论。
- 部分命题把“说了什么”作为 ACTUAL 事件，绕过了底层命题应为 EXPECTED/PLANNED 的要求。该问题会使不同报道中的同一预期无法稳定共指。

### Event Family 与 Predicate

- Predicate 通常可读且与命题方向一致，但规范化词表不稳定，同类事件出现 `report`、`report_financials`、`report_financial_metric`、`report_financial_results` 等多种形式。
- 明确的事件族错误包括：市场价格状态被归为 `PRODUCTION_SUPPLY`，上市计划被归为 `GOVERNANCE_PERSONNEL`，客户承诺被归为 `FINANCIAL_PERFORMANCE`，合同增长被归为 `FINANCIAL_PERFORMANCE`，CEO 陈述被归为 `GOVERNANCE_PERSONNEL`。
- 这些错误不会破坏证据，但会显著降低后续同族召回与 Cannot-Link 规则的可靠性。

### Participants 与 Source Claim

- 公司、分析机构和市场指数的表面实体大多正确。
- 局部指代仍有失败，例如保留 `The analyst` 而没有回填 Needham；多处把 Sanjay Mehrotra 作为底层经营事件的 ACTOR，而不是把 Micron 作为主体、Mehrotra 放入 `source_claim`。
- 多主体合并仍存在：三个指数、两只股票或 SanDisk/Western Digital/Seagate 被放入同一 mention，违反单一核心主体的原子化目标。
- `source_claim` 43/227 非空，但格式不稳定：既有 `Sanjay Mehrotra`，也有 `TheFly reported`、完整解释句甚至文章标题。该字段需要封闭为“声明者/来源实体”，而不是自由文本出处说明。

### Time / Reference Period

- 这是本轮最严重的字段问题。
- 101 条 mention 填了 `event_start`，其中 46 条明显把相对日期解析到了执行日附近（2026-07-16、07-21、07-22、07-23），而语料统一发表于 2026-06-25。
- 另有 Micron 财季被直接映射为自然季度的问题：例如 fiscal Q3 被映射为 2026-07-01 至 09-30，或 04-01 至 06-30；真实财季不能用自然季度猜测。
- 同一语料中，显式写出 June 25 的文章能正确解析，说明问题不是模型不会抽日期，而是 Grounder 请求缺少稳定的 `published_at`/文档基准时间约束，并且财政期应只保留 `reference_period_id` 直到 Normalizer 解析。

### Assertion State

- 总体分布：ACTUAL 173、EXPECTED 35、PLANNED 9、ONGOING 10。
- 典型错误是把“公司发布了指导”标为 ACTUAL，而非把指导内容本身标为 EXPECTED；把上市计划、回购假设或价格上限猜测编码为 ACTUAL speech act。
- 相比事实/预期，否认、传闻和条件性命题没有在本样本中形成足够覆盖，不能据此证明这些状态已可靠。

### Quantities / Units

- 共抽取 227 个 Quantity；226 个 `raw_text` 可在源文档直接找到。唯一例外是将原文 `50-60%` 拆成 `50%` 和 `60%` 后，单独的 `50%` 不再是原文精确子串。
- 数值方向和量级总体可靠；但单位未归一，存在 `% / percent / PERCENT`、`USD / billion USD / trillion USD` 等并存。这在 Grounder 截止点可以接受，但不得直接用于共指数值比较。
- 多处把共识值、同比值或比较值放进 Open Attributes 而不是 Quantity，字段策略尚不一致。

### Open Attributes / Local Package Hint

- 85/227 条 mention 有 Open Attributes，常见用途是驱动因素、产品范围、评级和比较基准，整体有帮助。
- 一些 Attribute 实际是应独立抽取的事件或数量，造成原子性与字段归属混杂。
- 227 条 mention 的 `local_package_hint` 全部为 null。当前 Grounder 实际没有履行 Local Package Hint 契约，这会把文内 package 线索全部推迟到后续阶段。

## 5. 逐文档人工审阅

下表中的 `P/R/F` 分别是 PASS/PARTIAL/FAIL；`M#` 对应该文档完整输出中的 mention 顺序。

| # | row id 前缀 | mentions | P/R/F | 主要结论 |
|---:|---|---:|---:|---|
| 01 | d42584c3 | 8 | 5/3/0 | M1 评论事件化；M3 证据范围不足；M5 family 错；遗漏 Apple ASP +12% 预期。 |
| 02 | b24ea6b3 | 9 | 6/3/0 | M1/M5 对同一上市融资重复分解且 Assertion 不一致；M7 市值 family 错；遗漏 Micron guidance beat。 |
| 03 | 238a879b | 4 | 2/2/0 | M1 误用执行日且产品发布 family 偏差；M4 把 guidance speech act 标为 ACTUAL。 |
| 04 | d07de614 | 5 | 2/2/1 | M1 合并合作、Claude 采用和投资；M4 未解析 `The analyst`；M5 泛化乐观评论应拒绝。 |
| 05 | 4fcc4fb0 | 7 | 5/2/0 | CAPEX 与资本返还字段较好；M4 合并多地扩产；M7 混合当前财务状态和未来预期。 |
| 06 | 181db9d6 | 9 | 3/5/1 | M1/M2 财季日期错误；M3/M4 同一 SCA 过拆；M5 family/Assertion 错；M9 作者持仓免责声明噪声。 |
| 07 | 79fe1f85 | 9 | 3/6/0 | 财季自然季度映射错误；M7 把 FY2027 供应展望错误绑定到 Q4 毛利率；M9 “reiterated”证据偏弱。 |
| 08 | 2b5bba2 | 5 | 1/4/0 | 多条日期落到执行日；M1 合并收入/利润，M4 合并收入/EPS guidance/CAPEX，原子性差。 |
| 09 | 25387f96 | 9 | 4/5/0 | 财报与 supply outlook 抽取完整；M3/M8 日期错误；M4 未把 payable/record 日期写入 Time；M6/M7 多指标合并。 |
| 10 | a424d484 | 8 | 6/1/1 | 财务指标较完整；M6 行业判断事件化；M7 年内涨幅终点误用执行日。 |
| 11 | 1e515897 | 18 | 13/4/1 | 高召回但过密；M5/M6/M11 多指标或多动作合并；M17 “scheduled”推断偏强；M18 CEO 评论噪声。 |
| 12 | 3f25dec4 | 9 | 8/1/0 | 日期和市场事件总体准确；M6 把 SK Hynix 涨幅与 VI 合并后丢失涨幅；遗漏机构净买、2.8m 价位等。 |
| 13 | 005d3637 | 7 | 3/3/1 | M2 机器人内存倍数是一般事实而非事件；M1/M5 执行日污染；M7 “position Optimus”事件价值较弱。 |
| 14 | 1aaec309 | 12 | 0/12/0 | 12 条事件均受原文支持，但所有 Tuesday/Wednesday/Thursday 都按执行周解析；M6/M8/M10 多主体合并；M12 family/Assertion 错。 |
| 15 | 34fc2a61 | 10 | 7/2/1 | 财务指标原子拆分优秀；M8/M9 日期污染；M10 行业转型评论噪声；遗漏供应紧张至 2028 的预期。 |
| 16 | 6b5d042a | 20 | 15/4/1 | 数值覆盖最完整；M16 把假设性回购当 ACTUAL；M18/M19 分析师评论事件化；M20 guidance action 的 Assertion 处理不稳。 |
| 17 | 35a4333 | 7 | 4/3/0 | M1 日期错误；M2 合并 EPS/收入；M6 合并投资行为与经营利润率；其余事件支持良好。 |
| 18 | 6e04e5e6 | 3 | 1/1/1 | M1 买入事件可用但 source_claim 是长解释句；M3 参加电视节目是噪声。 |
| 19 | fce73732 | 5 | 1/4/0 | 4 个市场事件日期误用执行日；Dreamer/Grounder 遗漏文中 Nasdaq 与 S&P 500 两项明确变动。 |
| 20 | 1516d084 | 1 | 0/1/0 | 标题事件被正确识别，但日期为执行日；rating 维持与 target 上调合并为单 predicate。 |
| 21 | 6389d63c | 3 | 0/2/1 | M1 三只股票合并且日期错误；M2 两个 guidance 指标合并并标 ACTUAL；M3 排名信号噪声。 |
| 22 | 5d890b9d | 8 | 4/4/0 | 财务实际与 guidance 分拆较好；M3/M4 未做声明者/主体分离；M7 日期错误；M8 是无值、无具体分析师的模糊动作。 |
| 23 | bf5258ec | 5 | 1/4/0 | M1/M2 把 Q3 实际错误标成 Q4；M3 合并 revenue/EPS guidance 且标 ACTUAL；M4 时间与 source_claim 错。 |
| 24 | 35c3d868 | 3 | 0/3/0 | 严重漏掉 Apple 提价主事件；M2 客户承诺 family 错；M3 时间只记 earnings call 而未表达 beyond 2027。 |
| 25 | 4a7c28e1 | 3 | 2/1/0 | 目标价与 investor-day 预期合理；M2 Thursday 解析到执行周。 |
| 26 | 6ccb4979 | 8 | 3/5/0 | Apple 提价抽取正确；M3 合并 record revenue/毛利率；M3/M5-M8 日期全部落到 7 月 16 日。 |
| 27 | d9404d58 | 9 | 8/1/0 | 显式 June 25 使日期全部正确，市场事件原子化良好；M8 合并收入趋势与毛利率。 |
| 28 | c762d097 | 15 | 8/6/1 | 财务指标覆盖强；M1/M9/M12 日期污染；M2 把季度终点当完整事件日；M6/M8 丢失 ±1 区间；M12 CEO 评论噪声；M13 合并收入与利润率。 |
| 29 | 49fb27c5 | 5 | 1/4/0 | M1 合并 EPS/收入；M2 guidance 标 PLANNED 而非 EXPECTED；M4 日期污染；M5 分析师猜测标 ACTUAL；遗漏 800% 股价和 1,368% 盈利增长。 |
| 30 | f70bfd87 | 3 | 2/1/0 | 客户承诺与供需状态可用；M2 与 M1 有重叠且 family 应为商业合同而非财务表现。 |

## 6. 结论与修复优先级

Grounder 的强项是证据约束、数值复制和高召回；弱项是让结果稳定满足“可共指的原子事件身份”。当前结果不应跳过 Judge 直接进入 Atomic Event：时间污染会制造不同事件，合并的多指标会制造错误共指桥，speech-act Assertion 会把实际与预期混在一起。

建议按以下顺序修复：

1. 在 Grounder 请求中显式传入并强调 `published_at`，禁止使用运行日期；相对日期必须以 SourceMessage 为基准。Fiscal period 不得自行映射自然季度，只输出 reference period，交给财期 Normalizer。
2. 把“每个金融指标、每个核心主体、actual 与 guidance 分开”变成 Schema 后校验，而不仅是 prompt 文字；多主体/多 metric draft 应触发失败或拆分。
3. 收紧 source separation：底层预期保留 EXPECTED/PLANNED，声明者只进入 `source_claim`；`source_claim` 改为结构化实体或至少限制为短来源名。
4. 加入 eventhood 负例和确定性过滤，拒绝免责声明、节目出席、泛化投资观点和无独立可验证命题的评论。
5. 固定 Event Family 与 normalized predicate 目录映射；恢复并实际生成 Local Package Hint，或从 Grounder 契约中删除该空壳字段。

本轮总体结论：Grounder 已经是一个可靠的“有证据候选生成器”，但尚不是一个可绕过 Judge 的“最终 Mention 生成器”。

## 7. 交付与验证

- 完整 JSON：`.tmp/cdecr/grounder_quality_v5/grounder_30.json`，SHA-256 `5fd6c5f74e352da763b6767bfb6b39b359cfdca4dc5a5f5d1d4c94b3388bb467`。
- 展开的逐条 Markdown：`.tmp/cdecr/grounder_quality_v5/grounder_30_full_mentions.md`，SHA-256 `fcd5a4996c851d2d847934b3d8172da885914204e2787b92aaff1c7e0223d639`。
- 运行阶段审计只出现 `dreamer`、`grounder`，确认没有调用 Judge 或其他下游阶段。
- 全部 CDECR 非真实测试：`139 passed, 3 skipped`。
- `ruff check src/cdecr tests/cdecr`：通过。
- `mypy src/cdecr`：通过（24 个 source files）。
- `git diff --check`：通过。
