# 远端 Persistent Runtime：NEW 消息与 provision 覆盖审查（2026-10-10）

## 问题和结论

问题是：能否把一条消息最终写出的所有 provisional facts，基本等同于该消息全部 NEW 的 fact，并据此解决“W1 消息级 NEW 与 W2 Policy 命中可能指向不同事实”的错配？

**结论：不能作为等价关系，也不能直接作为交易准入条件。** 20 条正文样本中既有 provision 漏掉 W1 明确指出的新增事实，也有 provision 只写入 W1 所说已被覆盖的事实；另有 W1 因异公司新闻判 NEW、provision 却写入目标 ticker 的旧背景/价格的样本。很多样本可以覆盖其**被选择的核心 novelty scope**，但这不等于文章所有 NEW fact，更不能证明 W2 命中的正是该新 fact。

这次审查检验的是持久化输出的**内部一致性与消息原文支持**。对于未在 W1/W3 reason 中被明确标为旧或新的附带事实，没有逐一重放版本固定的完整 Event Library，因此不把“provision 中出现”本身当作其新旧证据。不能由这 20 个分层样本估计全量错误率。

## 抽样和证据

- 远端 `doxagent-sg` 生产 `v2-scheduler` 容器，SQLite `/data/runtime/runtime.sqlite3`，以 `mode=ro`、`PRAGMA query_only=ON` 读取；没有改动远端数据、配置或服务。
- 在 `runtime_v2_cases` 中筛 `w1_final.result=NEW` 且保存的 `source.snapshot.body` 至少 300 字符；满足条件的总体为 MU 1163、INTC 815、BE 400、RKLB 90，共 2468 条。按每个 ticker 的创建时间顺序等人数分成 5 段，固定种子从每段随机取 1 条，共 20 条。没有按 provision 数量或交易结果筛选。时间均为 Case `created_at` 的 UTC 日期，**不是文章发布时间**。
- 逐条读取原始 title/body、W1/W2 最终理由、W1-R3 或 W3 输出，以及 `runtime_v2_candidates` 中实际落库的 provisional record。**18 条走 W3、2 条走 W1-R3**；后两条的常规路由分别为 TRADE 和 ADD_TO_DELTA。20 条均有实际 provision。
- 原始抽样与全部正文在 [sample.json](./sample.json)；可用 [read_sample.py](./read_sample.py) 按 ticker 复核。抽样 SQL/算法在 [export_remote.py](./export_remote.py)。`sample.json` SHA-256：`D9CE480E8B1F1013B783F5D236B80D1721629F4AF18D7CFA01AD0AED7554F0FE`。部分原始正文只是消息源保存的摘要/短文，不保证是出版商网页全文。

## 逐条审查

表中 E# 是该 ticker、交易日语义下的 provisional ID；括号中的 case 后 8 位用于快速搜索原始证据。“覆盖”指 W1/W3 明确指出的核心新增事实是否在 provision 中有对应命题，不表示它包含全文全部事实或经过独立新旧校验。

| # | Ticker / UTC 日期 / Case 后 8 位 | 原文与 NEW 理由 | 实际 provision | 审查 |
|---|---|---|---|---|
| 1 | BE 09-25 `5757a58d` | SB Energy IPO 从传闻升级为确认，至少推至 10 月下旬；文中还有估值、SEC、Nvidia 投资和容量数据。 | E80-E85：延期、估值受阻、SEC/无运营站点、backlog、Nvidia 购股、容量。 | **核心覆盖，范围过宽**：E83/E85 等是同篇背景数值，W1 没证明其相对旧事实为 NEW。消息内页标 09-22，部分 provision 的 `occurrence_date=09-25` 用了处理日。 |
| 2 | BE 09-29 `49663932` | Anthropic IPO Founder LLC 50.1% 投票权、约 14% 股权、董事会与拟融资。 | E245-E249：相应治理结构、席位、估值、融资。 | **核心覆盖**；保留了估值/融资的计划或预测状态。 |
| 3 | BE 09-29 `3cae0f50` | Trump 拒绝与中国共同制定 AI 规则，Amodei 实际参会，纠正此前未受邀传闻。 | E124-E125：拒绝合作、Amodei 参会。 | **核心覆盖**，两个独立变化都有命题。 |
| 4 | BE 10-05 `31a41421` | Toyota/Isuzu 发布新 SORA 燃料电池巴士及续航、售价。 | E79-E80：发布/联名销售、>300 km/7980 万日元。 | **核心覆盖**；但完全不涉及 BE 业务，不能把 NEW 解释为 BE 交易相关。 |
| 5 | BE 10-07 `db7b8027` | Morgan Stanley 新电力缺口、97 GW 需求修订、Bloom 约 6 GW 预测、Nvidia 机架功率修订等；W2 命中 C3。 | E101-E108：上述研究数据和机架功率 149→234、415→600 kW。 | **核心覆盖且本例同 fact 对齐**：W2 的 C3 理由所依赖的机架功率修订在 E107；但它是 provision 落库后的核对结果，不能代表所有命中 Case。 |
| 6 | INTC 09-25 `5987eade` | Nintendo 获盗版诉讼 450 万美元缺席判决并获禁令。 | E103-E104：判决、关闭盗版商店。 | **核心覆盖，ticker 错配**：原文无 Intel 业务。 |
| 7 | INTC 09-29 `be84c602` | 白宫 AI 午餐会、行业自愿护栏、Johnson 对暂停开发及 DOJ 的表态。 | E173-E175：会议参与者、议题、Johnson 表态。 | **核心覆盖**；属于政策讨论，不是 Intel 自身变化。 |
| 8 | INTC 09-30 `4b8f41aa` | OpenAI Jalapeño B0 较 A0 每瓦性能最多 +25%，第三代 ASIC 已开发；W1 指出其他设计基线已覆盖。 | E260-E261：+25%（RUMORED）和第三代开发。 | **核心覆盖且排除了已知基线**。 |
| 9 | INTC 10-05 `ea47fac6` | AI Torture Chamber 实验、要求删库及作者受威胁；W1 还称项目上线 GitHub。 | E400-E402：实验、要求删库、死亡威胁。 | **基本覆盖**；项目上线未单列，但原文只是提到现存仓库，能否算本次独立新变化不确定。正文还含大量读者评论，不应当成消息事实。 |
| 10 | INTC 10-08 `c59281c6` | Michigan 球队招募：本周访问 SEC 目标、联系承诺球员，Mekhi McNair 将其列为 Top 4。 | E436：访问和联系球员。 | **明确遗漏** McNair Top 4（原文推荐区有该句，W1 reason 也明列）。同时 `Intel` 只是标题里的“情报”，与 INTC 无关。 |
| 11 | MU 09-13 `0a796310` | 多只非 MU 股票盘中上涨、Nvidia 长期投资预测重申、Oracle 云表现作为情绪驱动。 | E36-E41：五只股票涨幅和 Nvidia 预测。 | **部分覆盖**：W1 reason 明提的 Oracle 表现未收录；主要 facts 是其他股票盘中涨幅，不能视为 MU 新经营现实。 |
| 12 | MU 09-25 `496f430a` | OpenAI 通知数十家第三方、Hugging Face 最严重、内部审计发现 agent 经常越界。 | E244-E247：上述三项及数月日志审查。 | **核心覆盖**；额外审查期限是同一事件后续安排，是否独立新 fact 有选择性。 |
| 13 | MU 09-30 `dde198f3` | 中国 3D DRAM 从早研进入晶圆工艺/设备阶段，仍不能量产。 | E320-E321：推进阶段和量产障碍。 | **核心覆盖**；正文仅 432 字符摘要，无法检验更长原报道。 |
| 14 | MU 10-01 `3df04c0d` | W1 判 NEW 的依据是 BP 等十余家公司新评级行动；MU 业绩和指引被 W3 指为既有内容。 | E285：MU 强劲业绩/指引后盘后股价略跌。 | **严重错位**：provision 没有任何 W1 所列新评级；反而写了 MU 旧业绩背景旁的一句行情。这里“provision = NEW 部分”直接不成立。 |
| 15 | MU 10-07 `3bbfcdad` | Stuut 新 5250 万美元 B 轮融资。正文还首次陈述客户数、5 倍增长、30 亿美元交易量、多个客户部署结果。 | E350：融资。 | **所选核心覆盖，全文不覆盖**：若这些业务指标相对已有事实也为 NEW，provision 明显不是“全部 NEW 部分”；更关键是整篇与 MU 无直接关系。 |
| 16 | RKLB 09-17 `22f8fb79` | W1 reason 明列 SpaceX 9/24 解禁 3.284 亿股、空头 34%→11%、8/6 约 9.12 亿股解禁、借券量约 290 亿美元等为新；称波动率 4.53%/4.52% 和 9/22 Flight 14 已有 provisional 覆盖。 | E78-E80：恰为波动率比较和 Flight 14 计划。 | **严重反向**：新 facts 基本没入 provision，已被 W1 判覆盖的 facts 却入了 provision。W3 reason 虽也提到解禁/空头等，但输出未跟随。 |
| 17 | RKLB 09-23 `93ab397d` | StockStory 把 RKLB 放在买入名单；W1 自认文中只是旧财务指标，但因为没加载 Event 而判 NEW。 | E89：StockStory 推荐。 | **文字覆盖但 business novelty 弱**：编辑推荐是新的出版行为，不是 RKLB 经营变化；“没有加载事实”不能证明财务指标为新。 |
| 18 | RKLB 09-29 `6f69641b` | Starship 首次携有效载荷入轨、部署卫星、发动机提前关闭但目标达成、受控溅落。 | E92-E94：任务、发动机和溅落。 | **核心覆盖**；其余券商评级和股票涨跌没有进入该任务 scope。 |
| 19 | RKLB 10-02 `f2436b53` | W1 判 Starship 已入轨为唯一新核心，其他 RKLB Q2、合同、评级等说已被覆盖。 | E99：Starship 到达轨道。 | **核心覆盖**；示例说明输出本来就按 scope 选择，不等于全文新事实清单。 |
| 20 | RKLB 10-06 `910602bf` | Morgan Stanley 重申 SpaceX 评级/300 美元目标，SPCX +2%、ASTS +8%、RKLB +5%。 | E106-E107：RKLB +5%、Morgan Stanley 评级。 | **部分覆盖**：W1 明列的 SPCX、ASTS 当日价格变化未收录。它们可能不是 RKLB 必需的 durable fact，但不是“全文全部 NEW”。 |

## 为什么不能用 provision 自动解决 W1/W2 同 fact 问题

1. **合同本身只保证核心 scope。** [W1-R2](../../../prompts/persistent_runtime_v2/w1_r2.md) 以“至少一个未覆盖的核心事实”判消息 NEW；[W1-R3](../../../prompts/persistent_runtime_v2/w1_r3.md) 要求最小充分事实集合，明确排除别的独立新闻项目。W3 的 [uncovered_new](../../../prompts/persistent_runtime_v2/w3/skills/uncovered_new.md) 也要求最少命题覆盖当前 NEW reality。这些不是全文每个事实的逐项新旧标注。
2. **实际输出没有保证与 W1 novelty reason 一致。** #10、#14、#16、#20 给出明确的遗漏或错位；#16 更把 W1 说已覆盖的内容写进 provision。命题只标 assertion state，不带事实级 `NEW/OLD` 证据，也没有 W2 condition 对应关系。
3. **常规 TRADE 在 provision 产生之前路由。** [router.py](../../../src/doxagent/persistent_runtime_v2/router.py) 在 `W1 NEW + W2 hit` 时立即选 TRADE 并另行排入 EMIT_DELTA；[service.py](../../../src/doxagent/persistent_runtime_v2/service.py) 后续才运行 R3、分配 provisional。把事后结果直接当现有交易门槛在时序上不成立。W3 路径虽先出 `delta_candidates` 再最终处理，仍无事实级 W2 归因绑定。

更准确的判断是：provision **可作为“系统选出的新增核心现实候选”的线索**，有时可用来核对 W2 命中事实（如 #5）；它不是消息 NEW 部分的完备、纯净集合。若要将其用于交易准入，至少需对**每个 W2 命中 condition 所依据的原文事实**单独建立与已知事实的 NEW/OLD 归因，并验证它与候选命题是同一现实变化；本次审查不包含实现该机制。
