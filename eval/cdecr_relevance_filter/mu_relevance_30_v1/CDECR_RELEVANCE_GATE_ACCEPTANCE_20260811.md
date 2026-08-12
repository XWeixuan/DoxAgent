# CDECR Relevance Gate 单节点真实分类验收

日期：2026-08-11  
语料：MU relevance 30 v1  
Provider / Model：DashScope / `deepseek-v4-flash-0731`  
运行模式：冻结 Dreamer candidates，单节点 Gate 分类  

## 结论

Gate 的两轮 Responses 协议、候选级分类边界、逐项 fail-open 和审计链路通过；本轮分类质量不支持从 Shadow 切换到 Enforce。生产默认必须继续保持 Shadow。

主要原因是 211 条 Relevant Gold 中有 28 条被误删，Relevant recall 为 86.73%；hard-indirect 切片的 dropped-candidate precision 只有 65.22%。Direct 保护表现良好：79 条 Direct candidates 的 false negative 为 0。30 篇是刻意平衡的质量困难集，不能据此声称净成本收益；成本验收仍需完整 300 篇。

## 数据与隔离

- 输入：30 篇完整新闻正文和 409 条冻结 Dreamer candidates。
- 未向 Gate 请求发送：生产 mentions、`relevance_gold`、候选 Gold、评测映射。
- 最终 Gold：211 Relevant / 198 Irrelevant。
- candidate-to-mention 映射仅在请求完成后用于评分：310 exact、28 overlap-only、71 unmapped、1 mention-label conflict。

## 核心指标

| 指标 | 结果 |
| --- | ---: |
| Candidates | 409 |
| Valid predictions | 389 |
| Fail-open predictions | 20 |
| Simulated dropped candidates | 199 |
| Relevant recall | 86.73% (183 / 211) |
| Dropped-candidate precision | 85.93% (171 / 199) |
| Valid-prediction accuracy | 91.00% (354 / 389) |
| Direct-relevant false negatives | 0 / 79 |

### 切片

| 切片 | Candidates | Relevant recall | Drop precision | Valid accuracy | Fail-open |
| --- | ---: | ---: | ---: | ---: | ---: |
| hard indirect | 143 | 85.32% | 65.22% | 86.01% | 0 |
| mixed | 167 | 76.47% | 90.40% | 91.02% | 0 |
| mostly high / very high | 51 | 100.00% | N/A | 100.00% | 0 |
| very low / none | 48 | N/A | 100.00% | 100.00% | 20 |

## Provider 执行

- 汇总逻辑调用：30；成功 28，失败 2。
- 首轮加定向重试的实际调用：32；实际失败 4。
- 两轮都失败的文档：D017（14 candidates）、D089（6 candidates）。
- 错误：DashScope `provider_arrearage`；20 candidates 均按生产语义保留为 fail-open。
- 成功调用 tokens：input 98,013；cached input 2,048；output 27,487；reasoning 21,646。
- 包含重试的 provider latency 累计值：310,975 ms；该值不是 4-worker 运行的墙钟时间。
- 首轮 30 篇墙钟时间：约 87.6 秒。

连续重试得到相同 provider 错误后已熔断。评分没有用 Gold 替代失败预测，也没有把 fail-open 计为模型正确分类。

## Badcase 审核

389 条有效预测中有 35 条分歧：28 false negatives、7 false positives。

False-negative 主要集中在两类：

1. AI 基础设施、客户需求和资本开支传导被判得过窄，例如 AMD hyperscaler/deployment、Ciena AI data-center interconnect 订单、RTX Spark/PC memory、Broadcom/Nvidia/Oracle capex signals。
2. 竞争、客户和供需暴露被拒绝，例如 Sandisk 估值/历史、HPE/Dell price targets 与 memory reversal、memory peers ratings、Apple 因 memory prices 引起的 margin pressure。

False-positive 主要是把段落上下文过度传播到公司的一般性财务事件：Supermicro component financing、Oracle general guidance/stock movement，以及 AMD consolidated revenue/EPS/net income。AMD 的 4 条属于边界敏感案例，因为同段明确写到 data center 是主要驱动；本轮不因看到 Gate 预测而反向修改 Gold。

## 架构与协议验收

- Dreamer 第一轮使用 M2 / reasoning `none`；Gate 第二轮复用同一 provider/model/client，通过 `previous_response_id` 进入 M3 / reasoning `low`。
- Gate 位于每个 Dreamer block 内，先于跨 block aggregation、dedupe、Grounder 和 Judge。
- 生产与评测共用 prompt、DTO 和 request builder；评测没有复制独立协议。
- Shadow 记录标签和模拟删除；Enforce 才会实际删除。
- 缺项、非法批次和 provider 失败均逐项 fail-open，不进入 repair loop。
- `LocalPackageHint` 与 `EventMention.local_package_hint` 的永久删除保持不变，没有恢复旧碎片化接口。

独立缓存探针验证第二轮返回 3,072 cached input tokens，协议与缓存链路通过。

## 验收判定

| Gate | 判定 |
| --- | --- |
| Responses 两轮会话与缓存 | PASS |
| 候选级隔离与 Gold 不外发 | PASS |
| Fail-open 语义 | PASS |
| Direct 保护 | PASS |
| Gate 分类质量可进入 Enforce | **FAIL** |
| 30 篇净成本验收 | 不适用 |

因此本轮完整落地后的安全状态是：功能保留、默认 Shadow、禁止启用 Enforce。后续若要晋级，应先修复 hard-indirect 与 mixed 的召回边界，再在同一冻结 Gold 上复验，最后使用完整 300 篇进行净成本和端到端影响验收。

## 可复核产物

- `runs/frozen_gate_20260811_retry1/report.json`
- `runs/frozen_gate_20260811_retry1/predictions.jsonl`
- `runs/frozen_gate_20260811_retry1/candidate_mention_mapping.jsonl`
- `cdecr_mu_relevance_30_candidates_gold_final.jsonl`
- `gold_final_summary.json`
- `cache_probe_20260811.json`

关键 SHA256：

- sanitized input: `1bd92c36bf3ec72e790b277ccfa88150098353cb7ca3b73c9733344068fa3b6e`
- final Gold: `7889d481e3432a19145a03604356fc263ee022edf1e8b211fe21089ebaa571e2`
- final predictions: `81d8b91112f94d2b49ef5fcb57a6b5cf10b2958297a6fd6f8bb244a52a1127d9`
- retry report before this narrative wrapper: `82c1fc2c9a6a8972409cff4bb5c4086b72071645cd76532459ca5b80cdddbb87`
