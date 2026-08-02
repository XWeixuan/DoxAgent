# CDECR Anchor 离线审计

> 本报告读取固定 Registry，不调用模型、不修改业务数据。`SUPPORTED` 与 `valid_hint` 是保守的确定性复核代理，不是独立人工 Anchor Gold，因此只用于定位，不能作为发布门。

## 汇总

| 指标 | 结果 |
| --- | ---: |
| Mention | 254 |
| 有 hint | 101 (39.76%) |
| 保守 SUPPORTED | 130 |
| 保守 Anchor Recall 代理 | 76.15% |
| Anchor Precision 代理 | 98.02% |
| missing SUPPORTED | 31 |
| invalid hint | 2 |
| Judge 继承 | 72/72 (100.00%) |
| canonical Anchor links / roots | 101 / 15 |
| Package Anchor conflict | 1/68 |

## Missing SUPPORTED（按文档聚合）

| 文档 | 数量 | 代表缺失事实 |
| --- | ---: | --- |
| Wedbush says chip rally has further to run after Micron's blowout quarter | 9 | Micron signed 16 strategic customer agreements. |
| Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility | 7 | Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December 2026. |
| Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit | 3 | Jake Behan stated that Micron’s strategic agreements and HBM production ramp are providing visibility beyond a typical memory-pricing cycle. |
| KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings | 2 | Micron Technology reported fiscal Q3 2026 revenue of $41.46 billion. |
| Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders | 2 | Micron has announced strategic customer agreements that provide long-term demand visibility. |
| Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead | 2 | Micron guided fiscal Q4 revenue to approximately $50 billion with a tolerance of plus or minus $1.0 billion. |
| Why Everyone Is Talking About Micron | 2 | Micron's earnings have risen 1,368% year-over-year. |
| Apple Just Confirmed Micron's Biggest AI Prediction | 1 | Micron disclosed customer commitments of approximately $22 billion through strategic agreements. |
| Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings | 1 | Micron Q3 earnings exceeded expectations |
| Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike | 1 | Sandisk is planned to hold an investor day in August where updates on demand expectations, technology roadmap, and capital return plans are expected. |
| Why Sandisk Stock Soared Today | 1 | Micron and Sandisk are securing long-term prices via Strategic Customer Agreements. |

## Invalid hint

| 文档 | Mention | hint | 原因 |
| --- | --- | --- | --- |
| Why Micron's blowout earnings are a headache for Apple | mention:83da0408e7303bc1b1f4061c9e022ab116dd54883cf04f08232f2d95d74a4bfc | micron_q2_earnings_release | REACTION_INHERITED_DISCLOSURE_PARENT |
| Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure | mention:545259b88f9e6db144b49cc8dff0e0fd5be985a5b54f393d8481da8e6491a991 | micron-anthropic-partnership | MENTION_RESTATEMENT |

## Package Anchor 分布

- Anchor 数量分布：`{0: 59, 1: 8, 12: 1}`
- 最大 Package Anchor 数：12
- 冲突 Package：1 个。冲突集合会保留，但 N12 card 只暴露最多 4 个 Anchor。

完整逐 Mention 明细见同名 JSON。
