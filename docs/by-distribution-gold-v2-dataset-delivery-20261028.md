# By Distribution GOLD v2：规则与 1,800 条分层样本交付

按用户缩量后的要求，调试集和验证集各 900 个“文章×实际目标 ticker”对象，每 ticker 各 100。9,539 对全量池的剩余标注已停止；全量池仅用于抽样溯源，不作为已全部标注的交付物。

## 交付入口

- [七条标注规则及 12 个权威改判](C:/Users/WEIXUANXIE/Desktop/DoxAgent/docs/by-distribution-gold-annotation-rules-v2-20261028.md)。
- [业务尺度与校准启发](C:/Users/WEIXUANXIE/Desktop/DoxAgent/docs/by-distribution-gold-v2-calibration-insights-20261028.md)。
- [调试集 900 条浏览表](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/development-gold-review.md)；[完整冻结输入与 GOLD JSONL](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/development-gold.jsonl)。
- [验证集 900 条浏览表](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/validation-gold-review.md)；[完整冻结输入与 GOLD JSONL](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/validation-gold.jsonl)。
- [随机抽样清单](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/sample-manifest.json)；[覆盖与一致性校验回执](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/distribution_relevance_gold_v2_20261028/sample1800/validation-receipt.json)。

## 抽样与日期

随机种子 `2026102802`。按 ticker 分层，每层在合格池中均匀无放回抽 100 条；抽样不读取 GOLD，也不按正文是否为空、是否容易判断挑选。先抽验证集，再从与验证集文章/事件组不重叠的调试池中抽取。等额 ticker 样本适合调试和对照，不直接代表按生产消息量加权的总体准确率。

|集合|基础日期（纽约 02:00 语义日）|晚启用例外|数量|
|---|---|---|---:|
|调试|2026-09-29、2026-10-26|后期 ticker 仅有后一个日期的真实消息|900|
|验证|2026-10-16、2026-10-23|CEG、SNOW 原日期无实际目标，各从 10 月 26 日补抽 100 条|900|

CEG/SNOW 的验证样本是同日不同文章/事件的留出，不能据此宣称做过跨日期泛化验证。没有为未启用 ticker 伪造历史订阅或负例。基础日期来自先前冻结的约束随机选择；晚期两日为可用高覆盖完整日，较早两日从相隔至少七天的 66 个组合中用固定种子选择。

## 标签分布

|Ticker|调试 RELATED / IRRELEVANT / INSUFFICIENT|验证 RELATED / IRRELEVANT / INSUFFICIENT|
|---|---|---|
|BE|23 / 77 / 0|9 / 91 / 0|
|CEG|76 / 18 / 6|81 / 16 / 3|
|COHR|19 / 80 / 1|12 / 88 / 0|
|INTC|38 / 62 / 0|48 / 52 / 0|
|MU|32 / 68 / 0|30 / 70 / 0|
|NBIS|29 / 71 / 0|22 / 78 / 0|
|ONDS|22 / 60 / 18|17 / 63 / 20|
|RKLB|45 / 45 / 10|30 / 50 / 20|
|SNOW|8 / 92 / 0|17 / 82 / 1|

|集合|RELATED|IRRELEVANT|INSUFFICIENT|二分类可评分|
|---|---:|---:|---:|---:|
|调试|292|573|35|865|
|验证|266|590|44|856|

## 标注与质检

全部对象均由指定的 **GPT-6.1 Terra / high** 逐条标注：176 对复用同一模型已经完整阅读、且文章/ticker/输入哈希完全相同的标签；其余 1624 对完成新标注。不是把正则、Decision Model 或旧 GOLD 当作真值。每对保留 RELATED/IRRELEVANT/INSUFFICIENT、原文子串、业务理由、规则编号和混题标记。

仲裁记录 6 对；未解决仲裁 0。用户确认的 12 个改判另存独立账本，旧 80 案和旧审计原件未覆写。

核验包括：两集各 900、每 ticker 各 100；目标无遗漏/重复；ticker 和输入哈希匹配；全部引文确为输入原文；标签/规则编号有效；输入冻结后未变；两集文章/事件组交集为 0；验证集与 80 案校准组交集为 0。

对精确 URL、规范化标题、实质正文哈希先归组，并对候选报道逐条比较后合并 7 类跨源/跨语言同事件组。抽样时同一组只能进入一侧；没有把同公司、同产品或同数字自动当作同事件。该隔离降低泄漏风险，不能证明没有任何措辞不同、尚未识别的同事件消息。

抽样后的原文复查又确认 2 条跨体裁/跨语言泄漏：验证集的一篇 Anthropic IPO 报道和一篇中文 Fab62 报道。以固定种子 `2026102803` 在同 ticker 的原合格池补抽，排除已知同事件、已选文章与校准重叠；没有读取 GOLD 选择替代对象。原抽样、换样理由、替代输入哈希均保留，最终仍各 900 条、每 ticker 各 100。

## 输入证据与使用边界

样本池来自真实 by_distribution 留存召回、实际目标及任一判定器实际判相关对象的并集，按 URL 家族去重。抽样后不再声称包含池中每一条正判消息。历史实际判定记录和真实分发链只作为池覆盖证据，不作为标注员的答案。

10 月 23/26 日按 capture 真实观察时间，并合并早先冻结快照补回已清理记录；较早日期依赖旧 observation、run、原始文章留存恢复。采集、入库、判定记录创建与实际分发不是同一时钟。历史 compact membership 的逐项时间以及入库提交前失败的返回内容无法全部恢复，因此这里的“全量池”仅指可追溯留存池，不声称历史提供商全部返回已被证明无缺。

有实际判定且内容版本一致时使用该冻结补全输入，否则使用不可变原始输入，不联网补正文。一个 URL 多版本取最丰富的一份完整连贯输入，原始版本留在底稿中，不跨文章拼接正文。模型分数是冻结快照时可见的状态，created_at 不能当模型完成时刻。

调试集 758/900、验证集 813/900 的原始输入没有正文；有足够标题/摘要时照常判断，不把空正文自动标信息不足，也不把信息不足强制改负例。

后续只用调试集修改正则和 Decision Model 提示词。冻结方案后再运行验证集；不得把验证集具体错例反复用于改提示词后继续把同一集称为独立验收。二分类评分排除 INSUFFICIENT 并单报数量；未运行、超限或模型失败应另报，不能当作不相关。

重放时只向判定器提供 evidence 中的 title/summary/body、目标 ticker 及待测定义。gold、理由、cohort_reasons、历史判定和集合身份仅作评估侧数据，不能放进模型输入。GOLD 只判断业务相关，不替代发布时间准入、正文质量、去重或后续研究价值判定。

本轮没有修改生产正则、模型提示词、消息分发逻辑或部署。
