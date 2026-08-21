# CDECR Package Workflow V3 真实验收报告

日期：2026-08-13  
测试对象：Package Workflow V3 Global LLM Clustering + Rolling Registry  
主要基线：Parent Occurrence Package V2.0R  
结论：**工程实现完成；百炼 JSON Schema Gate 通过；两份冻结 Package-only Gate A 质量失败；fresh 30 因百炼账户欠费在 V3 之前中断，不能宣称完成正式全流程验收。**

## 1. 本轮实际完成范围

V3 已替换 Package 聚合 active path：

```text
Mention / Atomic
  -> 单文档 Parent Occurrence Proposal
  -> 全局 LLM clustering（<=200）或 rolling clustering（>200）
  -> versioned MCP Registry
  -> affected MCP description
  -> deterministic Atomic ownership
  -> atomic Package projection / activation
```

主要实现包括：

- 稳定的 `PO-*` 与 `MCP-*` ID；
- Node 1 初始全局聚类、Node 2 rolling 聚类、Node 3 Registry 描述；
- 百炼 Chat Completions JSON Schema 模式，`strict=true`，reasoning effort=`high`；
- staged batch、Node 3 分项 checkpoint、Registry version/CAS finalize；
- 跨 MCP Atomic 唯一归属与确定性投影；
- bulk 与 incremental 统一 V3 resolver；
- V2/V3 artifact、cache 和版本隔离；
- Package-only runner、Schema probe、201-occurrence rolling/idempotency 测试。

没有切换回 DeepSeek 官方 provider，没有关闭 thinking，没有降级为普通 JSON mode，也没有自动回滚。

## 2. Gate 0：百炼 JSON Schema

最初把严格 Schema 错接到 Responses `text.format`，百炼没有执行预期合同。修复后按百炼 JSON Schema 模式改走 Chat Completions：

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "<stable_schema_name>",
      "strict": true,
      "schema": {}
    }
  }
}
```

真实 Node 级 probe：

| 节点 | Input | Output | Reasoning | Latency | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Node 1 Initial | 222 | 176 | 131 | 3.12s | 通过 |
| Node 2 Rolling | 293 | 2,395 | 2,350 | 23.44s | 通过 |
| Node 3 Description | 253 | 168 | 135 | 2.51s | 通过 |

三个节点均通过服务端 Schema、JSON parse、Pydantic 和业务 coverage 校验。Gate 0 通过。

## 3. 冻结 Package-only Gate A

### 3.1 结果总览

| 指标 | V2.0R R3 基线 | V3 R3-derived | V2.0R formal-V2 基线 | V3 formal-V2-derived |
| --- | ---: | ---: | ---: | ---: |
| Atomic | 192 | 192 | 281 | 281 |
| Proposal | 115 | 115 | 132 | 132 |
| Package | 49 | 36 | 73 | 31 |
| Singleton | 20/49, 40.82% | 18/36, 50.00% | 23/73, 31.51% | 5/31, 16.13% |
| 最大 Package | 50 | 86 | 56 | 63 |
| Pair Precision | 72.93% | 81.47% | 77.55% | 69.66% |
| Pair Recall | 42.36% | 79.05% | 38.78% | 41.01% |
| Pair F1 | 53.59% | 80.24% | 51.70% | 51.62% |
| V3 model calls | — | 2 | — | 2 |
| V3 input/output | — | 7,298 / 31,294 | — | 8,644 / 56,027 |
| V3 total token | — | 38,592 | — | 64,671 |
| V3 clean wall | — | 283.45s | — | 455.52s |
| 幂等二跑 | — | 0 call / hash stable | — | 0 call / hash stable |

说明：两个冻结输入的 Gold 对齐覆盖分别只有 73/192（38.02%）和 99/281（35.23%）。同一列的 V2.0R 与 V3 可作结果级比较，但低覆盖 Gold 不能替代全量人工语义审计。

### 3.2 Gate 判定

门槛为 Pair Precision ≥90%、Recall ≥65%、F1 ≥75%、Package singleton ≤45%、Gold 可判 singleton 漏合 ≤25%。

- R3-derived：Recall/F1通过，Precision 81.47%失败；singleton 50%失败；最大簇86且存在污染；Micron earnings仍为4组件。整体失败。
- formal-V2-derived：Precision/Recall/F1全部失败；singleton比例虽然降至16.13%，但这是明显过度合并伴随的表面改善。整体失败。

因此 V3 不具备替代 V2.0R current Package 的证据，V3 测试 Registry 保持隔离。

## 4. 独立 Agent 逐 Package 审计

### 4.1 R3-derived：36 Package

人工审计结论：

- 7 个明确误合 Package；
- 11 个非 singleton Package可接受；
- 18 个 singleton 内有5个明确漏合，另1个只按旧 Gold 判漏合但业务边界有争议；
- Gold 可判 singleton 漏合为2/11（18.18%），但全量人工结果为5/18（27.78%），说明低覆盖 Gold 低估了漏合。

关键误合簇：

| MCP | Size | 判断 | 主要问题 |
| --- | ---: | --- | --- |
| MCP-001 | 86 | 误合 | Micron earnings 主体之外吸入 supplier reallocation、Tesla/Optimus/physical-AI 等；明确误成员至少8条，人工扩展口径约14条 |
| MCP-005 | 21 | 严重误合 | 把 Micron 即时股价反应与 SK Hynix listing、KOSPI、长期涨幅、估值和 broad theme 混合；约12/21误成员 |
| MCP-021 | 13 | 严重误合 | 把 earnings shortage guidance、Mulberry访谈、ETF文章、Apple成本背景和市场评论聚成泛 memory theme；约8/13误成员 |
| MCP-003 | 3 | 误合 | Anthropic partnership/investment 与 Needham 市场预测混合 |
| MCP-006 | 2 | 误合 | Micron市值变化与Apple跌幅仅同文并列 |
| MCP-007 | 5 | 窄误合 | Apple定价披露与独立收盘反应混合 |
| MCP-037 | 2 | 误合 | Wedbush 与 BofA 两个独立机构行动混合 |

可接受的代表簇包括：Micron SCA、IDC iPhone预测、SK Hynix listing、Citi Sandisk note、Qualcomm data-center strategy、同一次 global AI selloff、UBS供给展望、Wedbush单一报告。

明确漏合 singleton：

- MCP-008：应与 MCP-007 中的 Apple price-hike reaction 另组反应包，而不是吞并整个定价披露包；
- MCP-017：应与 MCP-001 中被错误吸入的 Tesla/Optimus/physical-AI 成员另组父事件；
- MCP-023：应并入 MCP-031，同一 Wedbush 报告；
- MCP-025：应与 MCP-021 中两条 Mulberry 访谈记录另组父事件；
- MCP-027：应吸收 MCP-005 中同一 Micron forward-P/E valuation 成员。

Micron earnings Gold 仍为4个组件 `[33,2,1,1]`，未达到不多于3组件的目标。

### 4.2 formal-V2-derived：31 Package

人工审计结论：

- 16/31 明确误合；
- 14/31 可接受；
- 1/31 不可判但高风险；
- 至少67/281（23.84%）Atomic处于错误 Package 成员关系中，这是保守下界；
- 5个 singleton 均没有可安全指定的合并目标；Recall损失主要来自多个非 singleton Package 之间的碎片化。

关键超大簇：

| Package | Size | 判断 | 主要问题 |
| --- | ---: | --- | --- |
| `8b5e609e` | 63 | 窄误合 | Micron earnings高度纯，但混入外部 forward valuation；同时同一 earnings仍裂为6包 |
| `a3a496b2` | 38 | 严重误合 | Mizuho、Needham、Citi、BofA、UBS、Bernstein、Wedbush及作者观点被当成一个“post-earnings analyst reports”容器；误成员下界30/38 |
| `e6eff005` | 20 | 严重误合 | Tuesday/Wednesday/after-hours/Thursday/YTD/RSI/valuation与Apple走势被聚成一个reaction；误成员下界10/20 |
| `39ea1228` | 18 | 不可判/高风险 | 持续memory供需事项混入AI adoption、价格四倍和hyperscaler capex，父边界不稳定 |
| `664e88a6` | 18 | 严重误合 | 同日market rebound吸入Triller +259%、Apple volume与Apple特异反应 |
| `1ddca375` | 15 | 中度误合 | SCA核心吸入HBM shipment、终端销量、spending discipline等不同发生 |
| `8657aeb1` | 11 | 可接受 | Samsung/SK Hynix/KOSPI与资金流可视为同一次 Korea market episode；旧Gold边界偏窄 |

其他稳定 bad case 包括：Apple announcement + market reaction、Qualcomm strategy + Q2 handset revenue/market cap、IDC预测 + Siri兼容、investor day + later earnings、Defiance + Roundhill 两只独立ETF、跨issuer $1tn theme、HBM sellout披露 + 行业供需背景。

Micron FQ3 Gold 40条被拆在6个Package中，产生464个 missed pair links。形式上的 singleton 下降并没有解决主要 Recall 损失。

### 4.3 共同根因

两份结果出现同一种双向尺度漂移：

1. 过宽：模型把 `earnings/report/market/AI-memory theme` 当成真实父发生，将共同报道、共同主题或共同市场背景提升为 MCP；
2. 过窄：同一真实 earnings、同一 analyst report、同一 reaction 又按语义子主题拆成多个 MCP。

两份运行均达到 occurrence/Atomic exactly-once coverage，Apply也完整；主要问题不在候选缺失或持久化，而在一次性全局分区的语义尺度不稳定。当前极简 Prompt 只定义 MCP，没有足够稳定地区分：

- 真实发生 vs 报道/财报容器；
- bounded market episode vs 同日市场主题；
- 单一机构报告 vs “分析师们” umbrella；
- 披露成员 vs 披露后的市场/分析师反应。

这不是通过增加更多候选或放宽合并即可解决的问题。

## 5. fresh 30 全流程：真实运行但未形成有效验收样本

固定输入：

- corpus：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`；
- manifest：`.tmp/cdecr/parent_v2_fixed_30_manifest.json`；
- Registry：`.tmp/cdecr/package_v3_full30_real_20260813.sqlite3`；
- report：`.tmp/cdecr/package_v3_full30_real_20260813_report.json`。

运行到中断点的状态：

- 30/30 单文档 run 标记完成；
- 257 Mention/Atomic assignment进入跨文档阶段；
- completed event：0/30；
- Package：0；
- epoch：`PARTIAL_PARENT_RESOLUTION`；
- 30/30 `PARENT_INDUCE` checkpoint：`FAILED_RETRYABLE`；
- V3 clustering、Registry finalize 与 Package projection 尚未执行。

### 5.1 错误分布

本轮不是单一 Parent/Package 请求失败。主要失败为：

| Stage | `provider_arrearage` 次数 |
| --- | ---: |
| Field coreference | 273 |
| Atomic coreference item repair | 208 |
| Atomic coreference | 70 |
| Parent induction | 38 |
| Grounder missing recovery | 1 |

另有既有的 JSON/embedding局部失败，但它们不是本次全局中断的主因。恢复 probe 的第一个 V3 Node 仍收到百炼 HTTP 400 `Arrearage`，因此停止无效重试。

### 5.2 影响范围

欠费错误已被局部 fallback 吸收，但这些 fallback 会污染正式质量验收：

- Field ledger已被写成417/417 `SUCCEEDED`，其中258项走 `FIELD_BATCH_ITEM_FALLBACK`；900条 field links 中374条为 `UNRESOLVED_CANONICALIZED`；
- N9有208个任务失败，但5个 Atomic Apply chunk已完成；257/257 assignment均为 `CREATE_NEW`，其中208条是 `N9_UNJUDGEABLE_FAILED_SINGLETON`；
- `atomic_partition_v1` 已冻结，当前257个 Atomic基本是全 singleton；
- Parent尚未产生 proposal/partition，V3 Registry尚未开始。

因此这轮结果只能证明“失败局部化，没有整篇文档崩溃”，不能用于评估正式 Package准召、碎片化或成本。

### 5.3 为什么不能在同一 Registry 直接续跑

同库重跑 evaluation 的真实行为是：

- 复用已完成的单文档结果；
- 因 `field_overlay_v1` 已存在而跳过 Field，不会重试273个欠费请求；
- 因 `atomic_partition_v1` 已存在而跳过 N9，不会纠正208个失败任务及其 CREATE_NEW；
- 只重试30个 Parent Induction，然后用257个退化 Atomic 生成 V3 Package。

这会把 provider failure 造成的上游碎片化固化进 V3 Registry，属于伪验收。当前 `registry rebuild-derived` 也保留 field links，无法安全清除这374条 unresolved links；新增 V3表也不属于该旧工具的完整 reset范围。

所以原 Registry必须保留为失败证据。百炼恢复后，正式测试需要使用新的 Registry 跑相同固定30篇。这不是“因为 V3 失败从头重跑”，而是因为当前断点边界早于可恢复的 Field/N9 committed artifact，且旧 fallback 已经不可逆写入派生状态。

## 6. 性能判断

冻结 Gate A 证明了 V3 的确定性编排成本显著收缩：每份≤200 proposal输入只需 Node 1 + Node 3 各一次调用，二跑0调用。

但输出 token 与模型 latency 偏高：

- R3：input 7.3k，output 31.3k，wall 4.72min；
- formal-V2：input 8.6k，output 56.0k，wall 7.59min；
- 两轮 wall 的99%以上来自模型，不是 SQLite/Apply。

相对 V2.0R Package-only 的几十万 token，V3 input显著下降；但 high-thinking reasoning被计入output，formal-V2的总token仍有64.7k。效能方向成立，质量方向没有成立。由于 fresh 30没有到达 V3，不能提供全流程 V3 节点占比或与正式 V2.0 clean run 的有效墙钟对比。

## 7. 验证与交付判定

本地验证：

- Ruff：通过；
- strict mypy：通过；
- focused Package V3/Bulk tests：6 passed；
- CDECR全量回归：286 passed，3 skipped；
- `git diff --check`：无错误；
- 两份冻结 Gate A 幂等复验：0次新模型调用、partition hash稳定。

最终判定：

1. **实现完成**：V3代码、Schema、百炼传输、Registry、CAS、checkpoint、projection和active path均已落地；
2. **Gate 0通过**：Node 1/2/3真实 JSON Schema调用通过；
3. **Gate A失败**：两份冻结测试均未达到质量门槛，且逐包审计确认系统性supercluster；
4. **Gate B未完成**：fresh 30被账户欠费污染并在V3前中断，不能冒充正式验收；
5. **不切生产、不自动回滚**：保留V3实现与隔离测试产物，当前生产Package不应切至V3；
6. **恢复后的唯一有效下一步**：确认百炼账户恢复后，以全新 Registry完成同一固定30篇，再运行同一Gold evaluator与逐Package/singleton独立审计。

从业务上看，V3证明了“极简全局聚类”能大幅压缩请求和缓解部分碎片化，但没有证明它能稳定把握父发生边界。现阶段更接近一次有价值的架构实验，而不是可发布的 Package 聚合替代方案。
