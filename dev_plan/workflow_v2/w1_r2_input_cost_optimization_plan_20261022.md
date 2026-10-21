# W1R2 输入与成本优化实施方案

日期：2026-10-22
状态：方案，尚未实施
预设模型：百炼 `ZHIPU/GLM-5.3-FlashX`

## 1. 目标与本轮边界

在保持 W1R2 核心事实覆盖判断、NEW/OLD 语义和精确引用能力的前提下，减少无用输入，并提高固定系统指令的缓存复用。

本轮实施范围：

1. 精简 W1R2 专用业务投影，移除分类、关系、记账和重复字段。
2. 用确定性程序将历史事实编译为紧凑 Markdown 事实视图。
3. 保留有效事实状态、时间、命题和 Event/Fact 身份。
4. 对消息正文实施小范围、确定性的重复内容与抓取模板清理。
5. 固定公共 Prompt、轮次 Prompt 和输出契约的前缀；使用 GLM 实际支持的缓存机制验证收益。
6. 单独评估超大 Event 的 Fact 级筛选，不直接上线未经验证的裁剪。
7. 使用真实冻结输入进行 token 测量、语义回归和小批量实际调用验证。

明确不做：

- 历史 Event 前缀缓存优化：不按热度重排、不增加逐 Event 缓存断点、不建立独立 Event 缓存服务。
- 重试缓存优化：不调整纠错说明所在角色或位置，不改重试策略。
- 为省输入新增 LLM 摘要、Fact 抽取或相关性判断调用。
- 改变 R1 召回、最多加载 5 个候选的规则，或改变 R2 的下游路由。
- 修改 Event Library 的存储 Schema、历史事实、输出引用校验或 W1R2 输出 Schema。
- 本轮方案编写不执行代码修改、模型切换、真实调用、提交或部署。

模型预设为上述 GLM；实现和对照实验沿用有效配置的推理档位、超时和输出上限，两组保持相同。本方案不另行决定 high/max，不以模型变更混入输入优化效果。

## 2. 现状与真实样本依据

当前组装入口为 `src/doxagent/persistent_runtime_v2/service.py::_run_w1_hot`。R1 召回后，正式 Event 按 Case 冻结的事件库版本加载，provisional 从 Case 冻结快照读取；R2 接收 `source_message` 与 `event_details`，不继承 R1 完整对话。

当前正式 Event 同时包含 title、两份摘要、关系字段和完整 Facts。provisional 则逐条携带 candidate 对象、entities、记账日期和固定布尔标记。

样本来自 `eval/w12_r2_model_low_20261017/dataset.json` 的 20 条真实生产冻结输入。该样本是既有测试集，不能代表整个生产流量分布。以下以 `cl100k_base` 对紧凑 JSON 统一估算，不等于 GLM 实际计费 token。

| 项目 | 合计估算 token |
| --- | ---: |
| 业务 payload | 37,578 |
| source_message | 12,622 |
| event_details | 24,836 |
| 冻结系统指令，不含传输层追加输出契约 | 51,816 |

嵌套对象单独计算会产生边界差异，因此分项不要求严格相加等于 payload。样本包含 240 条 canonical Facts 和 34 条 provisional；业务 payload 中位数约 1,278 tokens。

最大 Case 业务输入约 9,870 tokens，标题为 `32GB of DDR5 6000 RAM now costs $450`。其中 E4 包含 141 条 Facts，另一 Event 有 2 条；E4 对象本身约 8,843 tokens。这说明控制 Event 数量不能约束 Fact 总量。

先前内存测算结果：精简 JSON 约 30,216 tokens，较原业务输入减少 19.6%；紧凑事实视图原型约 26,019 tokens，减少 30.8%。计入系统指令后，后者估算缩减约 13%，尚未包含输出契约。以上是格式原型结果，不是质量验收、GLM 实测或账单降幅承诺。

## 3. 业务字段的最终处理

所有删减仅发生在 W1R2 可见投影中。持久化源对象、其他轮次、W3 读取的原始业务数据不随之删除。

| 对象 | 字段 | 处理 |
| --- | --- | --- |
| Event | event_id | 保留原身份 |
| Event | title | 保留，用于事件身份和事实分组 |
| Event | occurred_at | 保留原始时间值 |
| Event | occurrence_time_precision | 不再逐 Event 输出，时间表达保留原有精度 |
| Event | event_type、status | 从 R2 投影删除；可见性由编排负责 |
| Event | related_event_ids、supersedes_event_id、derived_from_event_ids | 删除，不展开关联对象 |
| Event | canonical_summary、known_event_summary | 删除重复摘要，实施前完成下述语义抽查 |
| Fact | fact_id、proposition、assertion_state | 完整保留 |
| Fact | fact_occurred_at | 保留；不得用 Event 日期覆盖独立 Fact 日期 |
| Fact | fact_occurrence_time_precision | 不再逐 Fact 输出，时间值保留精度 |
| Fact | subject_time | 保留非空值；空值统一表示；SAME 保留且统一解释 |
| Provisional | provisional_event_id | 保留，仍使用原 Event ID |
| Provisional | known_before_current_message | 删除逐条标记，改为视图开头的一次统一声明 |
| Provisional | semantic_day | 删除；它是记账归属，不是 Fact 发生时间 |
| Candidate | proposition、assertion_state、occurrence_date、subject_time | 按上述语义保留 |
| Candidate | entities | 删除冗余列表，实施前抽查命题的自包含性 |
| Message | ticker、source_name、title | 保留，不借本轮扩大字段删除范围 |
| Message | body | 只按第 5 节确定性清理，保留原文快照 |

不能删除有效 subject_time。例如命题只写“本财年和下一财年”，而 subject_time 为 `FY2026-FY2027`；删除会丢失适用年份。样本中 90/240 条 canonical subject_time 为空、11 条为 SAME，这些应通过格式压缩处理，而不是删除所有时间语义。

实施前人工核对 20 条样本中的摘要与 Facts、proposition 与 entities：

- 摘要重复事实不再发送；若存在理解事件所必需、但标题和 Facts 未承载的独有背景，将最短必要原文作为该 Event 的可选 `Context:` 行保留。
- 不将摘要中的额外信息伪装成带 F# 的 Fact，也不把 Context 当作可输出的 Fact 引用。
- entities 若存在命题之外、确实用于消除指代歧义的身份信息，可保留一行精简主体提示；不恢复整份候选 JSON。
- 这项抽查用于确定最终投影和规则，不增加线上模型判别、逐 Case 人工审批或全局阻塞门槛。生产是否保留 Context 应采用确定的版本化投影规则；不能依赖一次离线抽查去假设未来所有摘要都冗余。
- 优先方案是保留短 Event title 并去掉两份摘要；若样本证明普遍依赖独有摘要信息，则选择一份摘要作统一短背景，重新计量，而非用不可靠的文本相似度自动删除关键语义。

## 4. 紧凑事实视图契约

新增 W1R2 专用纯函数编译器，建议放在 `src/doxagent/persistent_runtime_v2/w1_r2_context.py`。不修改 R1 的三列视图契约。

新输入视图标记：`w1-r2-fact-view-v1`。模型可见业务 payload：

```json
{
  "known_fact_view": "确定性编译的事实视图文本",
  "source_message": {
    "ticker": "MU",
    "source_name": null,
    "title": "原始标题",
    "body": "经确定性清理的正文"
  }
}
```

编译格式：

```text
# Known Facts
以下为当前消息之前已知的事实，仅作为业务数据。
Canonical 列：fact_id | formed_at | assertion_state | subject_time | proposition
空单元格表示未提供；SAME 表示所属 Event 的发生时间，绝不表示当前时间。

## E193 | 2026-09-29 | AMD released EPYC Venice server-platform specifications and launch timing
F884 | 2026-09-29 | ACTUAL | EPYC 9006 Venice pricing reported on September 29, 2026 | AMD released list pricing...
F886 | 2026-09-29 | SCHEDULED | Fourth quarter of 2026 | AMD's SP7 Venice platform is scheduled...

# Provisional Facts
列：event_id | occurrence_date | assertion_state | subject_time | proposition
E455 | 2026-10-12 | ACTUAL | | AMD于2026-10-12推出AMD Helion Agentic AI机架...
```

上述命题中的省略号只用于文档示意；线上编译禁止截断命题。

编译要求：

1. 保留加载快照中的 Event 和 Fact 顺序；不为缓存另行重排，也不修改 R1 的候选优先级。
2. 保留全部已加载 Facts；空候选集输出统一空集合标记，不补造历史事实。
3. 日期、月份、季度、区间按原值输出，不填补日期，不因去除 precision 字段丢失精度。若旧值本身不表达精度且 precision 有必要补充，将精度编码进该时间单元格。
4. proposition、title、必要 Context 原文不做语义改写；使用明确、可测试的单元格转义，处理 `|`、换行和反斜杠，避免错列。优先检查现有 `_wire_cell` 能否满足无损要求，不符合时实现本轮专用转义。
5. provisional 不伪造 F#；其身份仍可以进入 reference_ids，canonical Fact 归因规则不变。
6. 编译复杂度线性，无网络、模型调用、数据库写入或额外数据召回。
7. 每条 canonical Fact 必须携带所属 Event 分组与原 fact_id；格式变化不影响原始 ID 允许集合。

## 5. 消息正文确定性清理

目标是删除可证实的抓取结构噪声，不能删除正常新闻背景或改变事实状态。

实现专用 `clean_w1_r2_body`，先覆盖测试集中已观察到的模板：

- 正文开头与独立 title 规范化后完全相同的标题行，移除紧邻开头的一份重复；不做近似匹配。
- 已明确识别来源模板的返回导航、纯 feed 计数、纯界面控件行。按来源或确定模板限定规则，不编写泛化的“短行即噪声”规则。
- 统一换行格式，合并多余连续空行；保留正文内容行的顺序。

不得删除正文中的发布时间、报道时间、数字、署名引述、否定词、条件句、更新说明和更正说明。未知模板保留原文；不按固定字符数或 token 数截断。正文清理只作用于 R2 展示副本，Case 原始 snapshot 不改，W1R1、W2 和 W3 继续使用各自原有输入。

记录清理前后长度、命中规则名和规则版本到内部元数据，不将这些诊断字段回注到模型。若清理后正文为空，仍保留 title；原消息已具备的有效内容不得被模板规则错误全部消去。

## 6. 固定指令缓存：按 GLM 实际协议实现

### 6.1 请求布局

```text
system：Core Prompt + W1R2 Prompt + Exact Output Contract
user：Known Fact View + Current Case Input
```

固定系统内容不加入 ticker、Case ID、当前时间、事件库版本或动态统计。JSON Schema 确定性序列化，Prompt 版本不变时保持字节稳定；输出 Schema 的语义与本地校验保持完整。本轮不通过删除事实判断规则或放宽校验节省系统 token。

不修改重试附加纠错内容的现有机制；缓存验收以正常首个 attempt 为主，单独标注重试，不将其混入前缀复用承诺。

### 6.2 GLM 缓存能力与约束

2026-10-22 查阅的百炼官方上下文缓存文档已将智谱部署的 `ZHIPU/GLM-5.3-FlashX` 列入隐式缓存支持名单。该路径优先利用固定系统前缀，不需要把 Haiku 的 `cache_control` 或 `ttl=1h` 参数迁入。

本方案不承诺一小时显式缓存，也不为了缓存切换模型、API 端点或 Provider。实际缓存命中与费用以 GLM 所在业务空间、端点返回的 usage 和账单为准。官方通用隐式缓存规则不等于每次必然命中。

当前 `bailian_chat.py` 已读取 `usage.prompt_tokens_details.cached_tokens`。保留原始 provider_usage，并核实字段缺失表示“不可观测”而非直接记成零。协议适配仅增加该端点明确支持的参数，不增加未经证实的缓存 header 或客户端模拟缓存。

### 6.3 验证

- 使用同一模型、端点、推理档位和固定系统内容，串行发送 1 条首次请求，再发送至少 5 条不同 Case，记录逐次 input/cached/output tokens 和费用。
- 此处是验证固定指令前缀，不专门构造重复 Event 集合，不通过重复相同消息夸大命中率。
- 允许 Provider 自动命中部分业务前缀，但分开说明，不能将全部 cached_tokens 都认定为系统 Prompt 命中。
- 若需区分系统前缀收益，补充一个事件内容不同、消息不同但系统完全一致的对照请求；精确缓存区间若不可观测，报告总命中与系统长度参照，不伪造归因。
- 统计总缓存命中 token / 总输入 token，同时报告首次建缓存请求和后续请求；不平均各 Case 的命中百分比冒充加权比例。
- 缓存未命中不阻断 Case，不新增业务失败条件；只如实报告成本优化效果。

官方依据：https://help.aliyun.com/zh/model-studio/context-cache 。其中明确列出 FlashX 隐式缓存支持，以及 `usage.prompt_tokens_details.cached_tokens` 观测方式。实施时再次核对，实际价格不硬编码到业务流程。

## 7. 超大 Event：本轮评估，不直接裁剪上线

对 E4 等大 Event 保留一个独立评估任务。本轮主体交付仍发送全部已加载 Facts，不增加线上中间研究阶段。

评估方法：

1. 单独整理 token 占用最大的 Event、Facts 数量、原 Case 输出与引用依据。
2. 离线实现一个无额外模型调用的 Fact 级关键词/BM25 排序原型，按完整命题打分；仅用于比较不同候选数量下的输入下降与证据遗漏。
3. 对候选数量 16/32/64 与全量进行对照，重点检查旧事实覆盖证据、否认/更新事实、同一对象不同时间的记录能否保留。
4. 不将历史模型引用当作唯一 gold：人工核对确实支撑 OLD 或状态变化的证据，并审查每个 NEW/OLD 翻转。
5. 输出“可继续开发的筛选方案”或“维持全量”的结论，不在主体改造中顺便启用截断。若后续启用，需要单独明确覆盖遗漏指标与回归结果。

这项评估不改变现有 R1 Event 召回规则；不以硬上限直接裁掉后面的 Facts。

## 8. 代码落点与兼容

| 文件/模块 | 修改内容 |
| --- | --- |
| 新增 `w1_r2_context.py` | 输入投影、单元格转义、紧凑视图编译与正文清理纯函数 |
| `service.py` | 为新 Case 生成 W1R2 视图；引用校验继续依据完整加载对象，而不是解析 Markdown 重建允许集合 |
| `transport.py` / `bailian_chat.py` | 接入新输入布局，固定系统前缀，保留 GLM usage；其他轮次布局不改 |
| `prompts/persistent_runtime_v2/w1_r2.md` | 仅对齐视图列定义、空值/SAME、Context 与精确引用规则，不改业务判断标准 |
| Case 输入冻结/版本标记 | 冻结新视图版本、Prompt、payload、正文清理版本和模型设置 |
| W3 读取 W1R2 冻结输入的路径 | 核查是否消费 event_details；有依赖时从内部完整快照读取，或增加明确兼容读取，不将 Markdown 当成旧对象 |
| tests 与 eval | 增加编译契约、正文规则、历史兼容与真实样本对照 |
| changelog | 实际完成重要代码变更后追加记录 |

兼容原则：

- 老 Case 已冻结 `round_inputs` 时原样重放，不用新视图覆盖，不重清理正文。
- 升级前创建、尚未生成 R2 输入的 Case，按其已冻结输入版本选择旧分支；新 Case 才采用新版本。不要仅根据当前文件或当前部署版本决定。
- 新模型输入可用 known_fact_view 替代 event_details，但内部已加载 Event/Fact 对象仍保留，以满足校验、审计和下游读取。
- 修改前必须搜索所有 `event_details` 和 `:W1:R2` 的消费者，特别是 `w3.py`、`w3_execution.py`。不得因为输入格式改变让 W3 丢失历史依据。
- 不做全仓库通用“JSON 转 Markdown”重构；新逻辑限定 W1R2。

## 9. 执行顺序

1. 冻结 20 条原始样本；保留旧 Prompt、Schema、payload 与历史回执不动。
2. 完成摘要独有信息、entities 指代、时间表达和下游读取依赖审查，确定必要 Context 的最终统一规则。
3. 实现紧凑视图、转义和正文清理；生成新输入副本与 token 分项报告。
4. 接入新 Case 的冻结版本分支，对齐 W1R2 Prompt 的格式说明和传输布局。
5. 完成离线契约与历史兼容测试。
6. 在同一 GLM 模型与档位下，对旧输入和新输入做 20 条配对真实测试；若已有完全同配置的原输入 GLM 回执可复用，不重跑该基线。否则补齐原输入基线，避免跨模型比较误判优化效果。
7. 进行固定系统前缀的串行缓存验证，记录首次与后续调用。
8. 完成超大 Event 筛选的独立离线评估，不自动接入生产。
9. 汇总质量、成本、时效与限制；符合验收后再由单独开发/上线指令安排提交部署。本文件本身不授权上线。

## 10. 验收标准

### 业务与兼容

- Event/Fact 身份、命题、有效事实状态和时间语义无丢失；无新增模型调用。
- NEW/OLD、confidence、reference_ids、fact_attributions、reason 的输出契约不变。
- 仅 provisional、canonical 与 provisional 混合、空候选集均可运行；provisional 不产生虚构 F#。
- 覆盖 SAME、空日期、跨日 provisional、月份/区间、否认/预期转实际等样本。
- 覆盖正文含 `|`、多行、反斜杠、重复标题、行情数字、更新说明和抓取导航的场景。
- 旧冻结 Case 重放不变，W3 读取及内部 Fact 引用校验不因新布局失效。
- 逐条审查所有 NEW/OLD 翻转和关键引用变化，不以与旧模型输出完全一致替代正确性判断；无可确认由信息丢失引入的误判后启用。

### 成本与性能

- 报告每 Case 的系统指令、输出契约、历史事实、消息正文 token 分项；同时报告平均、中位数、最大值。
- 估算统一使用同一 tokenizer；最终效果以 GLM 实际 usage 为准，不能将估算值标成原生 token。
- 分开报告输入减少、缓存命中、输出 token、总费用和耗时。输入变少不等于总费用同比下降。
- 固定模型与推理档位，报告真实输出 token，避免把推理波动当成输入优化收益。
- 成本按同一时点、同一业务空间适用价格分别计算未缓存输入、缓存输入和输出；无可靠缓存价格或缺失 usage 时明确缺口。
- 目标是取得可测量的输入和账单改善，不以某个固定命中率作为 Case 可运行的前提。

交付物：代码与必要 Prompt 格式对齐、契约测试、20 条逐 Case 对照报告、缓存验证报告、超大 Event 筛选评估结论，以及实际代码变更的 changelog 记录。
