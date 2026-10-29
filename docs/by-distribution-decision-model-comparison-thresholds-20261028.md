# Decision Model 免费横比与阈值统计

核验时间：2026-10-28 14:10 UTC（北京时间 22:10）。本次不修改提示词、GOLD、正则或生产阈值。

结论：**三个指定模型的 OpenRouter 免费入口均已不可用，无法在“确保免费”的条件下完成 BE 验证集性能横比。** 已各发出一次零价格上限的真实请求确认，全部返回 HTTP 404，成功推理为 0；没有转用付费版本。现有 Bailian 分数的阈值统计已完成：ONDS 降至 45% 有效；BE 的验证问题不能单靠降低阈值解决。

## 1. 对照口径

“当前提示词”取上一轮最终选定的调试候选：BE `decision/attempt-03`，ONDS `decision/attempt-01`。它们是上轮交付的本地配置，不代表已发布的生产版本。

BE 新模型对照计划固定为原 [decision50 验证集](../eval/distribution_relevance_debug_20261028/decision50/validation-gold.jsonl) 的 50 条：4 条 RELATED、46 条 IRRELEVANT。保留同样 title/summary/body/source/language、同一个 `Is this news relevant to BE?` 问题和 relevant/irrelevant 两段 definition；不向模型发送 GOLD 或理由。已保存全部 50 条请求输入，但在免费路由探针失败后没有继续批量请求。

阈值部分复用上一轮两 ticker 各 50 条调试、50 条验证的成功预测分数，不重新调用 Bailian，也不重抽成功回答。`score >= threshold` 判相关。ONDS 的 10/12 条调试/验证 INSUFFICIENT 仍不计入二分类；BE 无 INSUFFICIENT。文章分数沿用正式分发逻辑的结果，未改切段/聚合。

## 2. 官方协议、免费条件和真实调用

已阅读 OpenRouter 的 [Decisions API 文档](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)及[接入示例](https://openrouter.ai/docs/guides/community/jev-tutorial)。这些模型使用 `POST https://openrouter.ai/api/alpha/decisions`，不是 Chat Completions。请求为 `model + state + questions`，相关性使用 `type: noul`、`criteria.true/false`，成功响应应提供 `answers.BE.noul`。免费变体和限制参见[官方免费模型说明](https://openrouter.ai/collections/free-models)。

旧索引仍能搜到免费页面；不能据此认定今天可用。实际查询 `GET /api/v1/models?output_modalities=decisions`，再查每个模型的 `/endpoints`，结果如下：

|模型|请求的明确免费 ID|免费端点数|零价请求结果|50 条性能分数|
|---|---|---:|---|---|
|Baseten Mammoth 1|`baseten/mammoth-1:free`|0|404：No endpoints found|不可得|
|Baseten Elephant 1 9B|`baseten/elephant-1:free`|0|404：No endpoints found|不可得|
|Inception Mercury Decide|`inception/mercury-decide:free`|0|404：unavailable for free；提示使用付费 ID|不可得|

Baseten 两个不带 `:free` 的 ID 同样没有可用端点。Mercury 的不带后缀版本有 Inception 端点，实时输入价格为 `$0.00000002/token`，即 **$0.02 / 百万输入 token**，输出价格为 0；输出免费不代表整次调用免费。该付费版本未调用。

三次探针都使用下面的正式路由参数，禁用兜底，且未提供其他模型：

```json
{
  "provider": {
    "max_price": {"prompt": "0", "completion": "0", "request": "0"},
    "allow_fallbacks": false
  }
}
```

零价约束字段来自上述 API 的 `ProviderPreferences` schema，见留存 [decisions-api.md](../eval/distribution_decision_comparison_20261028/decisions-api.md)。本轮只有 3 次失败的路由探针，没有成功模型推理、没有付费推理；不把 404 当成“不相关”，也不伪造 50 条准确率/召回率。旧网页上的免费说明不能覆盖本次 API 实测。

## 3. 不同阈值下的验证表现

下表均为当前 **Bailian decision-model-preview** 的缓存分数，不能当作三个未跑通模型的结果。P 为准确率，R 为召回率。

|判相关阈值|ONDS P|ONDS R|ONDS TP/FP/FN/TN|BE P|BE R|BE TP/FP/FN/TN|
|---:|---:|---:|---|---:|---:|---|
|50%|100.00%|77.78%|7/0/2/29|100.00%|50.00%|2/0/2/46|
|45%|100.00%|88.89%|8/0/1/29|100.00%|50.00%|2/0/2/46|
|40%|100.00%|88.89%|8/0/1/29|100.00%|50.00%|2/0/2/46|
|35%|100.00%|88.89%|8/0/1/29|66.67%|50.00%|2/1/2/45|
|30%|100.00%|88.89%|8/0/1/29|66.67%|50.00%|2/1/2/45|
|25%|100.00%|88.89%|8/0/1/29|33.33%|50.00%|2/4/2/42|
|20%|88.89%|88.89%|8/1/1/28|37.50%|75.00%|3/5/1/41|
|15%|80.00%|88.89%|8/2/1/27|33.33%|75.00%|3/6/1/40|
|10%|44.44%|88.89%|8/10/1/19|25.00%|75.00%|3/9/1/37|
|5%|31.03%|100.00%|9/20/0/9|22.22%|100.00%|4/14/0/32|

### 调试集对照

|阈值|ONDS 调试 P/R|BE 调试 P/R|
|---:|---|---|
|50%|93.33% / 100.00%|84.62% / 91.67%|
|45%|93.33% / 100.00%|85.71% / 100.00%|
|40%|93.33% / 100.00%|85.71% / 100.00%|
|30%|93.33% / 100.00%|80.00% / 100.00%|
|20%|73.68% / 100.00%|70.59% / 100.00%|

45% / 40% 对 BE 调试集有改善，找回分数为 0.48 的机架交付后缺电消息；验证集却没有收益。这也是不能仅凭调试改善确认阈值有效的直接例子。

## 4. 如何解释这些结果

**准确率 100% 并不能单独证明只要降低阈值就能解决。** 需要看漏报的分数是否高于潜在误报的分数。这里的数值是模型输出，不应理解为在此业务中已经校准好的真实相关概率。

ONDS 验证中，分数 0.48 的反无人机研究项目原被 0.5 阈值拦截；降至 0.45 即找回，未新增可评分误报，F1 从 87.50% 提升到 94.12%。调试集指标保持不变。因此，在本轮冻结数据上，**45% 是值得保留的阈值候选**，继续降到 40% 或 30% 没有额外验证收益。没有把它写入生产。

BE 验证的两个漏报是两条晶圆制造扩产信息，模型分数为 **0.22 和 0.07**。45% / 40% 都无法找回；降到 30% 反而先放进了 0.36 的材料供应链投资负例。为了找回低分真相关，会先收进多个更高分的不相关消息，说明至少在这组输入与 GOLD 上存在分数排序问题，不能只归因于阈值过高。

除表中固定档位，还枚举了 `[0, 0.5]` 内全部实测分数断点，确保没有漏掉 22%、7% 这类中间位置：

- BE 验证中，P 严格大于 80% 时，最高 R 仍为 50%。
- BE 验证只有 4 条正例，R 严格大于 80% 必须找回全部 4 条；此时能取得的最高 P 是 **26.67%**，在阈值 7% 时为 TP4/FP11/FN0/TN35。
- 因而 **不存在只调整阈值便让 BE 此验证集 P、R 都严格大于 80% 的方案**。阈值大于 0.5 只会进一步减少正预测，亦不能补齐召回。

BE 仍需模型/业务定义与 GOLD 边界的后续比较，而非继续无条件降低阈值。上一轮记录的 GOLD 尺度疑点在本轮未修改。此轮按用户要求做阈值敏感性统计，不追加提示词 attempt，也不重新选取旧候选来抬分。

阈值分析使用了已经看过的验证答案，任何由此选择的阈值都不再拥有未见验证的含义；BE 仅 4 条相关、ONDS 仅 9 条相关，不能外推为稳定生产保证。

## 5. 留存与核验

结果位于 [本轮目录](../eval/distribution_decision_comparison_20261028/)：

- [真实免费路由响应](../eval/distribution_decision_comparison_20261028/free-route-probes.json)、[免费端点与模型目录查询](../eval/distribution_decision_comparison_20261028/pricing-probes.json)、[不带免费后缀的端点查询](../eval/distribution_decision_comparison_20261028/unversioned-endpoints.json)。
- [固定请求与候选哈希](../eval/distribution_decision_comparison_20261028/request-manifest.json)、`frozen-be-requests.jsonl`：50 条相同输入已准备，探针后未批量发送。
- [阈值统计 JSON](../eval/distribution_decision_comparison_20261028/threshold-statistics.json)及 [CSV](../eval/distribution_decision_comparison_20261028/threshold-statistics.csv)：两 ticker、两数据集、11 个固定档位，含 TP/FP/FN/TN、P/R/F1。
- [新增放行病例](../eval/distribution_decision_comparison_20261028/threshold-added-cases.jsonl)：逐阈值记录分数、标题、冻结 GOLD 和理由，INSUFFICIENT 明确标识不参与二分类。
- [全部分数断点](../eval/distribution_decision_comparison_20261028/exact-threshold-breakpoints.json)、[原分数及 metadata 哈希](../eval/distribution_decision_comparison_20261028/threshold-provenance.json)。

核验：50% 阈值重新计算的全部混淆矩阵、分母与上轮 metrics 完全一致；本次未改原预测日志、候选、GOLD 或运行时代码，未泄露 API key。阈值统计不产生模型 API 调用。
