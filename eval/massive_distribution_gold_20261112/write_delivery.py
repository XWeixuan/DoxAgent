"""Write a concise delivery note from verified frozen datasets, without tuning."""
import collections,json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).parent;ROOT=P.parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p):
 with p.open(encoding='utf-8-sig') as f:return [json.loads(s) for s in f if s.strip()]
m=read(P/'dataset-manifest.json');v=read(P/'verification.json');counts=read(P/'final/label-summary.json');inv=read(P/'inventory.json')
table='|Ticker|去重召回池|调试集 R / I / U|验证集 R / I / U|调试 / 验证相关占比|\n|---|---:|---:|---:|---|\n'
for t in m['tickers']:
 a=next(r for r in counts if r['ticker']==t and r['split']=='development');b=next(r for r in counts if r['ticker']==t and r['split']=='validation')
 table+=f"|{t}|{m['per_ticker_dedup_pool'][t]}|{a['related']} / {a['irrelevant']} / {a['insufficient']}|{b['related']} / {b['irrelevant']} / {b['insufficient']}|{a['related_fraction']:.1%} / {b['related_fraction']:.1%}|\n"
source_table='|来源|完整正文留存文章¹|正则召回并冻结的独立输入|样本对数²|\n|---|---:|---:|---:|\n'
archive=rows(P/'complete-body-archive.jsonl');all_samples=rows(P/'final/development-gold.jsonl')+rows(P/'final/validation-gold.jsonl')
for sid in m['sources']:
 n=sum(r['n'] for r in inv['body_counts'] if r['source_id']==sid and r['outcome'] in ['FULL','SHORT_FULL'])
 source_table+=f"|{sid}|{n}|{sum(a['source_id']==sid for a in archive)}|{sum(r['source_id']==sid for r in all_samples)}|\n"
stamp=datetime.fromisoformat(m['snapshot_at']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
date_range=(min(r['first_seen_at'] for r in archive),max(r['first_seen_at'] for r in archive))
text=f'''# Massive Distribution：五家 Newswire 调试集与验证集 GOLD 交付

日期：2026-11-12。生产只读快照：北京时间 **{stamp}**。本轮只完成抽样、隔离和逐条 GOLD，**未开发或应用 Massive 专属正则，没有 Decision Model 调用、线上 BODY 重跑、正式消息发布或生产配置修改**。

共 **{v['pairs']} 条文章×ticker**，覆盖十个ticker。调试与验证各 **{v['per_split']['development']} 条**；九个ticker各50/50，IONQ因去重召回池仅94条，全部纳入为47/47。两集共{v['unique_families']}个不同新闻族，跨集新闻族重叠0。

## 文件入口

- [完整调试集 JSONL](../eval/massive_distribution_gold_20261112/final/development-gold.jsonl) / [调试逐条审阅表](../eval/massive_distribution_gold_20261112/final/development-review.md)
- [完整验证集 JSONL](../eval/massive_distribution_gold_20261112/final/validation-gold.jsonl) / [验证逐条审阅表](../eval/massive_distribution_gold_20261112/final/validation-review.md)
- 每个ticker的两集分别位于 `eval/massive_distribution_gold_20261112/final/<TICKER>/`，包含完整原文和标注理由。
- [抽样 manifest](../eval/massive_distribution_gold_20261112/dataset-manifest.json)、[标注合同](../eval/massive_distribution_gold_20261112/annotation-rules.md)、[完整性验证](../eval/massive_distribution_gold_20261112/verification.json)。原始标注分包位于同目录 `annotation/labels-NN.jsonl`。

R=RELATED，I=IRRELEVANT，U=INSUFFICIENT；下表比例仅是**特意选样后的标签构成**。

{table}

相关比例30%–50%是抽样努力方向，不能据此反改GOLD。本轮用标题业务分层尽量增加可能相关的候选；标题并不是真值，部分ticker仍可能偏离该范围。IONQ稀缺池全部保留，不通过复制文章、跨集复用或从正则阴性中补齐名额。

## 抽样范围及池子的定义

五个源作为一个全市场press-release总体合并处理，**不纳入 `bloomberg_massive`**，没有按五源各自再为每个ticker抽50条。生产是隔离测试发布，虚拟目标为 INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ。

使用已取得的完整正文，不新抓网页。只纳入 FULL/SHORT_FULL 且 `result_ref` 能原生解码、正文完整标记有效的记录。其正则命中收据须与快照的当前词库revision及正文digest一致；不把TEST_DUPLICATE、ADMISSION_REJECTED或是否最终落测试发布表当作GOLD真值，也不只抽已发布消息。

快照原正则阳性收据{m['raw_positive_pairs']}对，其中{m['stale_revision_pairs_excluded']}对词库revision过旧而未混入；有效当前版本收据{m['eligible_current_positive_article_ticker_rows']}对，共冻结{m['frozen_complete_articles']}篇不同完整输入。冻结阳性输入首次发现范围 `{date_range[0]}` 至 `{date_range[1]}`。这是保留库截至快照的候选范围，**不是指定两天的完整市场档案**。

{source_table}

¹ 包括重新检查中的既有完整正文，和尚未对任何ticker命中的文章；不等于全部可访问上游新闻。² 文章×ticker按选用代表稿的主来源归类，同篇新闻可以对多个ticker出现。Business Wire全文受限明显，不能以另外四源的充足数量掩盖该覆盖缺口。

## 去重、两集隔离及标题选样

- 先按全局文章身份、规范URL、规范标题、正文fingerprint/全文hash、发布者同稿ID合并，之后再形成 ticker 池。用八词shingle近重复检查合并高度相似的转载；对标题浏览已确认的产品译稿与同公司同季度财报译稿也作同族处理。
- 同一新闻族在所有ticker中固定属于同一集合，不能在A ticker调试、B ticker验证。稀缺池先平衡两侧，其余族按固定seed分配。去重族代表稿优先英文及更完整的留存输入；仅有其他语言的稿件仍保留，未机器翻译或补写正文。跨语言未能识别的语义重复仍可能存在，不声称做到所有同事件语义去重。
- 在各池、各侧按标题分成业务对象较明确的 `business_title` 与其他标题。大多数按约40%/60%随机无放回抽样；某层不足时取尽并用另一层补足。没有根据GOLD或模型结果反复重抽，seed **{m['seed']}**，各层总体/样本数保存在manifest。标题筛选表达式只是抽样辅助，**不是本轮产出的新监测词**。
- 先冻结所有证据、ID、两集、分包及标注合同的hash，再发起指定模型标注。随后核对各文件hash未变。旧1800集及11月3日INTC/IONQ集的精确URL、规范标题和全文hash重叠检查均为0，见[旧集重叠检查](../eval/massive_distribution_gold_20261112/prior-dataset-overlap.json)。

## GOLD 尺度和标注执行

由三个独立sub agent逐条标注，创建参数均为 **`gpt-6.1-terra` / high**，无fallback；同篇相同输入的多ticker目标合并分包以保持尺度一致。标注者读取完整冻结标题、摘要和正文，不接触生产词表、命中片段、标题选样层或既有GOLD。没有让程序或Decision Model生成标签。原始标签保留，脚本只核对ID、字段、类别映射及数据hash。

主标签为 RELATED / IRRELEVANT / INSUFFICIENT，并细分：

|类别|主标签|含义|
|---|---|---|
|DIRECT|RELATED|目标公司/产品的实质事件|
|INDIRECT|RELATED|有原文事实支持的竞争、客户、供应、技术依赖、政策等业务近邻|
|INDUSTRY_BACKGROUND|IRRELEVANT|仅大行业/题材背景，没有可说明的目标业务联系|
|IRRELEVANT_TEMPLATE|IRRELEVANT|无关主题、同名歧义、名单/模板/导航等非实质提及|
|INSUFFICIENT|INSUFFICIENT|有效输入不足以作业务判断|

每条含中文理由、`mixed_topics`、置信度和模型/标注者信息。Massive强调核对“实质事实”，不新增必须拿单、显著影响、已商业化或有交易价值的条件。市场报告、融资、诉讼、活动通知可以相关，不按体裁一刀切；报告中的覆盖公司名单及公司介绍不能替代相关新闻事实。间接正例需要说清楚业务联系，不能把一个AI、云、能源或量子词扩展为整条产业链需求。

## 完整性与后续使用边界

- {v['pairs']}条GOLD的ID完整且唯一，目标ticker/字段/标签类别一致；{len(m['packets'])}个分包全部齐全。每条证据hash及冻结输入hash核验通过，跨两集新闻族重叠0。
- 按快照生产 `regex_relevant`、`_match`、`_normalize` 函数hash核验一致，并依**原消息实际language，随后英文回退**做原规则回放：{v['baseline_integrity']['replay_positive']}/{v['pairs']}命中，0条池成员不一致。见[池归属核验](../eval/massive_distribution_gold_20261112/baseline-integrity.json)。这只是完整性检查，没有训练或择优新的规则。普通旧回放工具按来源声明语言重建；后续使用本集应保留每条的实际 `language`，避免多语消息被错误改成仅英文入口。
- 固定词库在 `baseline-terms/<TICKER>.json`，版本指针、五源及虚拟测试绑定在 `frozen-context.json`。未来Massive候选应单独保存；当前生产仍共用统一L2，直接改统一L2会影响普通By Distribution，本轮未改机制或词库。
- **本集只覆盖原规则已召回的文章。** 新规则的recall只能解释为对本次已标注相关样本/原召回范围的保留率，不能宣称全市场召回率；本轮没有GOLD标注原规则未召回的新闻。后续需要测漏召时应另建原规则阴性/独立发现样本。
- 标题选样提高了某些题材的入样概率，未加权的相关比例或precision不能直接代表生产自然流量准确率。分层总体和配额均保留，不能仅凭本表推算生产降量。
- 调试只使用 development。validation的具体文章、GOLD理由和坏例应在候选冻结后按SOP阶段开放；本轮只构建标注，没有拿验证GOLD调参或跑新候选。
'''
(ROOT/'docs/massive-distribution-gold-dataset-delivery-20261112.md').write_text(text,encoding='utf-8')
readme='''# Massive Distribution GOLD 2026-11-12

交付说明：`docs/massive-distribution-gold-dataset-delivery-20261112.md`。

- `final/development-gold.jsonl`：调试集。
- `final/validation-gold.jsonl`：验证集，调试候选冻结前不要读取具体标签/理由。
- `final/<TICKER>/`：逐ticker两集，完整输入及GOLD。
- `final/*-review.md`：逐条审阅表；不是调参时必须加载的上下文。
- `dataset-manifest.json` / `annotation-freeze.json` / `verification.json`：配额、来源、去重、hash与标注完整性。
- `baseline-terms/` / `frozen-context.json`：冻结当前统一词表和来源上下文。
- `complete-body-archive.jsonl` / `inventory.json` / `deduplicated-positive-pool.jsonl`：私有复现依据，含完整召回输入及原收据，标注者未读取。
- `annotation/packet-NN.jsonl` / `labels-NN.jsonl`：盲标原包和原始输出；只使用gpt-6.1-terra high。

只读取样与离线数据加工，没有生产配置写入、Decision Model调用或新规则调试。本数据仅测当前规则召回范围，不能估全市场recall；标题分层选样比例不是自然precision。
'''
(P/'README.md').write_text(readme,encoding='utf-8')
print('Delivery documentation written.')
