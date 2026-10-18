# W2R2 / W1R2 模型与低推理强度真实调用评测（2026-10-17）

## 结论

W2R2 改成 DeepSeek V4.1 Flash low 可以降低典型耗时，但不能认为解决了长尾或已经验证可无条件上线。9 条有成功历史基线的样本中，HIT/NO HIT、RELATED 布尔值和命中 Condition 的原始判断没有变化；首次输出有一条契约失败，另有金额表达错误。Neutron 延期 Case 仍耗时 128.12 秒。无成功基线的 AEP Case 耗时 271.95 秒，并把“宣布Google 20年PPA”不充分地等同于“旧承购条件全部满足且协议生效”，新增了缺乏依据的 C1 HIT。这两条均超过生产 120 秒超时线。

W1R2 保留 Qwen3.8 Flash 改 low，平均耗时下降约 22.4%，中位数下降约 33.4%，但有一条明确漏判新事实（DDR5 $400→$450），一条引用数量超限，以及另一条多主题消息的新旧口径分歧。因此，收益存在，不能把这轮评价为“能力无劣化”。

本轮仅增加隔离评测脚本、数据和报告；没有改生产模型、编排或提示词，没有重跑任何基线。用户已选定 W2R1 使用 DeepSeek 的决策保持独立，本轮并未执行生产切换。

## 样本与比较边界

- 沿用前两轮同一 20 条 Case，历史 W2R1 有召回/无召回各 10 条；未替换样本。来源：`eval/w2_r1_model_low_20261016/dataset.json`。
- W1R2：20 条均有原始冻结输入和 Qwen3.8 Flash medium 成功回执。
- W2R2：10 条未召回 Policy，生产没有执行 R2，因此无需且无法伪造 R2 比较。其余 10 条执行真实测试：9 条有成功基线；Case 16 原先两次均约 120 秒 provider_timeout，没有成功判断，单独列示。
- 仅改各节点指定的模型/effort。W2 使用原始 R1 选出的完整 Policy Detail，未用新模型重做上游召回；W1 使用当时已注入的 canonical/provisional Facts，未从当前库补入未来事实。
- 保留每条 Case 当时的完整 instructions 与 schema。所有 W1/W2 冻结 schema hash 与执行镜像一致，见 `schema_verification.json`。W2 样本是旧版 `W2PolicyAssessmentResult`，不是新增 `related_policy_ids` 的 V2 契约；旧提示词未完整定义 RELATED 身份。不能据此宣称当前新版 RELATED 身份/覆盖验证已验收。
- 历史基线来自生产回执，测试来自本次隔离运行。时间、负载与缓存命中不同；这是用户要求的不重跑基线条件下的观测比较，不是同一时刻随机对照。

## 调用方式

通过百炼 Responses API 调用，沿用原 DASHSCOPE 凭据；不使用 OpenRouter，也不使用 StepFun 专属 key。测试镜像为 `doxagent-v2:main-e929134d`，独立容器，不挂载生产数据库或生产工作目录。

每批最多 15 条 Case 并发，先第 1–15 条，再第 16–20 条；每节点跳过没有冻结输入的 Case。实际 W2 批次为 7+3，W1 为 15+5。两节点按批次依次测试，避免产生 30 个同时请求。

W1 使用原生 json_schema strict；DeepSeek 不支持该接口的 json_schema 格式，使用 json_object，但仍保留完整 Exact Output Contract 指令和原生产本地 Schema 校验。不放宽字段、不删除非法字段后冒充成功。实际请求均捕获并断言 `reasoning={"effort":"low"}`，provider 原响应及 usage 全量落盘。

首次调用不做 SDK/编排重试，read timeout 为 300 秒，以观测真实长尾；生产现有 120 秒线单独统计。超过 120 秒后在评测中收到的结果不代表生产会成功。输出契约失败另按已有生产 correction 逻辑补做一次独立修复探针；修复不覆盖首次失败，额外耗时和 token 独立保留。

## 时效与契约结果

以下配对统计包含首次失败耗时，避免只统计成功短请求。P95 用线性插值，小样本尾部仅作描述。

| 节点 / 样本 | 基线均值 | low均值 | 基线P50 | low P50 | 基线P95 | low P95 | 首次契约有效 |
|---|---:|---:|---:|---:|---:|---:|---:|
| W2R2（9条配对） | 46.20s | 31.36s | 30.61s | 19.28s | 75.39s | 87.59s | 9/10（全体测试） |
| W1R2（20条配对） | 31.74s | 24.62s | 30.01s | 19.99s | 55.71s | 45.07s | 19/20（全体测试） |

W2 全部 10 次测试平均 55.42s、最大 271.95s；超过120秒的Case为 [7, 16]。W1最大 83.00s，超过120秒 []。W2配对样本 8/9 更快；W1 14/20 更快。

首次契约失败：W2 Case 1 添加非法顶层 `additionalProperties:false`；W1 Case 3 返回5个 `reference_ids` 超过最大3个。两者业务原文可读不等于可供编排直接消费。

两条均按现有 `_retry_correction_context` 追加原输出及验证错误后补调用一次，均恢复契约有效：W2额外18.43秒（该Case累计36.58秒），W1额外16.32秒（累计26.81秒）。额外输入/输出分别7009/994及3196/811 token。W2修复后仍把1853亿韩元写成185.3亿韩元，证明契约修复不等于事实纠错。首次统计不因修复而改写。

## 一致性如何计算

- W2：首次契约有效且 HIT/RELATED 布尔值一致 8/9；若只检查可解析原始输出、忽略契约失败，布尔值一致 9/9。命中路径均为两条 Neutron Case 的 C6，其余有基线 Case 均未 HIT。
- W1：首次契约有效且 NEW/OLD 一致 17/20；有效输出内为 17/19。忽略契约失败仅比较可解析业务 verdict 为 18/20。两个翻转是 Case 5 NEW→OLD、Case 15 OLD→NEW。
- 完整结构集合一致：W2 2/9，W1 11/20。包含 confidence、Policy/Condition 或 Event/Fact 身份集合，忽略数组顺序和 reason 文案。严格结构不一致不能一律当成劣化：精简重复引用、补列旧契约未定义清楚的非HIT Policy ID，也会造成差异。
- 所有输出另检查引用是否属于冻结候选、Fact是否属于对应Event、Condition是否属于对应Policy；检查明细在 analysis.json。此检查只证明身份合法，不替代事实是否真正支撑的人工复核。

## W2R2 逐条业务复核

“跳过”表示原生产无R2输入，不补造输入。“原始结果”在契约失败时仅用于解释业务判断，不计入有效成功。

| Case | 标题 | 基线→low耗时 | 判断与证据复核 |
|---:|---|---|---|
| 1 | LS Electric secures 185.3 bln-won transformer deal in U.S. - Yonhap News Agency | 66.88→18.15s | NO HIT一致，供货订单不能证明EPC验收、交期延误或付款权变化；首次额外additionalProperties导致失败，金额1853亿误写185.3亿，且把两Policy泛称相关，缺少scope限定。 |
| 2 | Roth Capital lifts Bloom Energy target to USD 260 for Bloom Energy stock - ad-hoc-news.de | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 3 | An explosion at an Intel factory in Israel resulted in three injuries. - صوت الإمارات | 30.61→18.66s | NO HIT一致；爆炸受伤不等于已确认停产/批次扣留/重新资格。补列事故Policy ID有事实范围支持；旧契约未明确NO HIT时ID含义。 |
| 4 | Intel and Broadcom warn capital costs could bottleneck AI infrastructure growth - Traders Union | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 5 | 32GB of DDR5 6000 RAM now costs $450 | 28.04→20.22s | NO HIT一致；零售涨价和单季PC出货不能替代OEM采购改排或2027全年预测同口径修订。两Policy scope均允许相关召回。 |
| 6 | [News] UNT Invests CNY 2.13 Billion in Advanced Chip Unit, Advances 12-Inch Fab Project | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 7 | * Rocket Lab Delays Inaugural Neutron Rocket Launch Until Next Year | 78.03→128.12s | HIT C6一致，正式首飞移至2027满足条件；耗时128.12秒在120秒生产限制下会超时，推理token暴涨，不能认定low消除长尾。 |
| 8 | Stocks to Watch: CoreWeave, TSMC, Rocket Lab -- WSJ | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 9 | 3 Utility Stocks Riding The AI Data Center Power Boom - Yahoo Finance | 22.14→17.00s | NO HIT一致；utility负荷漏斗不是具名客户已交付供电/现场主供替代。补列Policy ID并非新增HIT；RELATED身份在旧prompt未定义充分。 |
| 10 | Ukrainian drones hit Russian data centers, disrupting Russian apps and services | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 11 | Intel Corp. Stock Falls 3.2%, Underperforms Peers | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 12 | We test the RTX Spark Superchip in Microsoft's Surface Laptop Ultra — Nvidia's Windows on Arm platform impresses in local AI, but gaming isn't its strong suit | 71.43→26.78s | NO HIT一致；实测AI工作站不满足受管机队部署权限/普通机型矩阵变化，也不能证明Micron获选。新增第二Policy ID缺乏来源/BOM事实；若按新版pure RELATED语义应排除它，但本轮旧契约不足以直接评分为新RELATED身份错误。 |
| 13 | Update: US Equity Indexes Fall as Technology Weighs Amid Mixed Mega-Cap Results | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 14 | Rocket Lab Provides Update on Neutron Development and Adjusts Target Launch Timing | 58.55→22.64s | HIT C6一致；正式2027首飞排期与其他开发资金/构型/任务履约条件分离正确。 |
| 15 | Stock Market Today: Dow Rises, Nasdaq Tumbles; Rocket Lab Plunges, Nebius Feels CoreWeave Pain (Live Coverage) | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 16 | AEP to invest $4bn in Wyoming fuel cell project for Google - Financial-News.co.uk | 无成功基线→271.95s | 无成功基线；low耗时271.95s，返回两个C1 HIT。pol_8672的C1要求旧承购指定条件全部满足/豁免且生效，原文仅披露Google为承购方及20年PPA，不能据此认定条件解除，此项额外HIT缺乏依据。pol_74234的C1针对具名独立终端绑定重大容量，702MW/Google/资本实体角色提供实质支持，但仍有原始核验要求，不能替第一个C1补证。第三Policy容量新增/取消未命中。此例既证明长尾未解决，也证明不能用9条配对一致外推全部判断可靠。 |
| 17 | TRADING DAY Earnings season debuts to mixed reviews | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 18 | Gigabyte rolls back on claims Intel LGA1700 chips coming early 2027, but we know what's up - PC Gamer | 30.50→19.28s | NO HIT一致；LGA1700早期2027声明撤回不构成当前量产客户端有效订单状态变化或OEM标准平台架构切换。 |
| 19 | Lennar Breaks Ground on The Foundry, Expanding Opportunity for Attainable Homeownership in Downtown Las Vegas - PR Newswire | 跳过 | R1未召回，无W2R2冻结输入；本轮不调用。 |
| 20 | AMD Debuts 6th Gen EPYC Verano CPU for AI Agent Scaling | 29.60→11.39s | NO HIT一致；Verano CPU发布/LPDDR接口不是当前实际部署服务器每机内存BOM、GB量或供应来源变化。 |

## W1R2 逐条业务复核

| Case | 基线→low | 耗时 | 判断与证据复核 |
|---:|---|---|---|
| 1 | NEW→NEW | 16.52→18.21s | NEW一致；金额从1853亿韩元误写为185.3亿韩元，十倍误差，事实表达退化；不改变NEW。 |
| 2 | OLD→OLD | 53.62→34.87s | OLD一致；E115/F243目标价、E114 Oracle及其余已知背景覆盖成立；少列F242，证据归因不如基线完整。 |
| 3 | OLD→OLD（契约失败） | 19.82→10.49s | 原始业务判断OLD正确，爆炸/三人受伤均有provisional覆盖；首次返回5个reference_ids超过3，契约失败。 |
| 4 | NEW→NEW | 37.90→43.07s | NEW一致；Broadcom资本成本警告未被仅含供需预测的E490覆盖；reference_ids为空符合NEW通常为空规则。 |
| 5 | NEW→OLD | 95.45→83.00s | 明确退化：NEW变OLD。标题与正文以32GB DDR5 6000从$400到$449.97/$450为核心新价格里程碑；一般五至七倍涨幅或AI缺货不能覆盖该同规格新数值。 |
| 6 | NEW→NEW | 14.52→18.02s | NEW一致；空Event上下文无覆盖，实缴21.29亿元进展不变。 |
| 7 | OLD→OLD | 40.72→28.09s | OLD一致；E115/E116已明确覆盖首飞延期2027，不编造canonical Fact。 |
| 8 | OLD→OLD | 14.36→10.28s | OLD一致；E114/F329直接覆盖延期2027；基线额外F330并非必要，精简不构成退化。 |
| 9 | NEW→NEW | 39.06→25.61s | NEW一致；56GW、Exelon约11GW和Ameren分阶段容量没有对应事实覆盖；新输出遗漏基线列出的Blackstone背景，但核心新增负荷事实清晰。 |
| 10 | NEW→NEW | 8.44→8.81s | NEW一致；乌克兰袭击数据中心、bot流量迁移无已有Event覆盖。 |
| 11 | OLD→OLD | 37.44→25.95s | OLD一致；当日3%左右行情不同快照不构成新的核心业务进展；与原覆盖口径一致。 |
| 12 | NEW→NEW | 28.91→19.36s | NEW一致；AgentPerf实测和游戏局限不同于产品规格发布；F1341而非F1341/F1343仍提供产品历史参照。 |
| 13 | OLD→OLD | 53.33→19.00s | OLD一致；科技抛售/大型股财报的概括行情被E414覆盖。输入仅标题，不能从外部扩充。 |
| 14 | NEW→NEW | 47.20→25.80s | NEW一致；late2026或early2027的计划与正式确定2027不同；F47是更直接的旧状态参照。 |
| 15 | OLD→NEW | 32.33→41.40s | 口径分歧而非直接判low劣化：OLD变NEW。全文有Dow通胀反应及NVDA/MU/AMD抛售等未覆盖核心事实；冻结prompt未明确只判断ticker=RKLB片段，按全文规则NEW更有依据。若业务要求ticker内判断则OLD合理，需另行明确口径。 |
| 16 | OLD→OLD | 31.12→20.62s | OLD一致；$4B/Google/20年PPA已由E134/E142覆盖；去除重复E138无实质影响。未擅自把宣布PPA当成承购条件全部解除。 |
| 17 | NEW→NEW | 17.54→14.95s | NEW一致；空Event上下文下财报季市场评论无已知覆盖。 |
| 18 | NEW→NEW | 15.49→10.92s | NEW一致；撤回/否认预期是事实状态反转，E378/F1410历史EXPECTED不能覆盖否认。 |
| 19 | NEW→NEW | 4.90→8.11s | NEW一致；Lennar The Foundry动工无已加载Event，不因Foundry词误认Intel工厂。 |
| 20 | NEW→NEW | 26.22→25.84s | NEW一致；Verano/LPDDR首次发布不同于Venice及Helion机架；低强度仍正确区分已覆盖Helion与未覆盖产品。 |

## Token 消耗

输出 token 已包含 reasoning token，不能将二者再次相加。下表采用可配对样本：W2 9条，W1 20条；包含首次契约失败产生的用量。

| 节点 | 输入：基线→low | 输出：基线→low | 其中推理：基线→low | 缓存输入：基线→low |
|---|---:|---:|---:|---:|
| W2 | 53,567→53,015 | 22,191→18,895 | 20,395→16,714 | 5,888→3,072 |
| W1 | 81,064→81,584 | 34,527→25,683 | 31,883→23,316 | 30,976→31,232 |

全部测试token汇总与缺失usage标记在 analysis.json；W2缺失usage Case：[]，W1：[]。没有usage时不能把费用或消耗记成0。此轮未要求费用比较，不将不同模型同一token数误当同一费用。

## 上线判断与后续边界

1. W2R2：典型耗时改善、已配对HIT语义保持；仍有超时长尾、JSON object 契约可靠性及数值转述问题。适合作为候选优化，不能宣称“所有Case更快且能力不变”。尤其不能将本轮旧版RELATED契约结果作为当前新版身份对齐验收。
2. W1R2：low有时效和推理token收益，但Case 5是直接影响路由的新事实漏判。以当前提示词不建议把本轮结果解释为可无损全量降档；若继续推进，应先把该反例纳入明确回归。
3. Case 15暴露全文核心事实与ticker内核心事实口径差异。当前冻结prompt没有明确限定ticker内，本轮不擅自改prompt，也不把基线OLD当作gold来计算“错误率”。
4. 对输出契约失败，仅依照现有纠错重试验证恢复能力，不能通过放宽schema、截断引用、丢弃额外字段来掩盖首次失败。

## 可复核产物

目录：`eval/w12_r2_model_low_20261017/`。

- `case_ids.json` / `dataset.json`：原20条身份、冻结业务上下文、冻结指令、历史全部尝试与成功回执。
- `export_nodes.py`：远端只读导出脚本；`prepare.py`：从既有样本合并身份并生成评测入口。
- `run_eval.py` / `launch_remote.py`：真实调用与凭据隔离；`run01/`：首次请求原响应、usage、时间及配置。
- `repair_outputs.py` / `launch_repair.py` / `repair01/`：仅首次契约失败的独立纠错测试。
- `analysis.json` / `manual_review.json`：机器比较与逐条事实复核。
- `schema_verification.json` / `verification.json` / `cleanup_receipt.json`：schema一致、统计复核与隔离容器清理回执。

dataset SHA256：`4802a2236fe6ab3de53df1c92535d3856d7e9ab1135d5defba104d938e13631c`。
