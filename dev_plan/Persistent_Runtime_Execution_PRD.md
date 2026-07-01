# DoxAgent Persistent Runtime Execution Agents 开发需求文档

## 1. 背景与目标

DoxAgent 已完成 Document 3: Persistent Operation 的开发。Document 3 的核心作用是将 Document 1: Global Research 和 Document 2: Expectation Units 中已经稳定下来的投研结论、可交易预期、已知事件、监测配置与执行规则，转化为后续持久化运行阶段可以被系统持续消费的运行资产。

本阶段需要规划和开发的是 Document 3 之后的运行时执行层，即：

> 当 DoxAgent Monitoring 的监测管线持续捕捉到新的媒体、机构文章或社交媒体消息后，系统如何基于 Known Events 与 Monitoring Execution Policy，完成新旧识别、policy 命中判断、专家研判、交易意图记录、缓存归档、Known Events 更新和 blackboard 修正。

本阶段不是重新生成 Document 3，也不是继续写研究文档，而是开发一套围绕 Document 3 的持久化运行执行编排。

本阶段的核心目标包括：

1. 让监测消息进入执行管线后，能够被低成本 worker 快速初筛。
2. 让高确定性的 Direct Trade Candidate 能够绕过深度 agent，快速进入 Trading Records。
3. 让未被 policy 覆盖、或需要进一步研判的消息进入 O3 值班专家。
4. 让社交媒体消息以 batch 方式进入执行管线，并通过更严格的 irrelevant 过滤降低噪音。
5. 让非实时交易消息进入 ingest_queue 或 archive，为后续 DoxAtlas 联动和投研 agent 消费预留空间。
6. 让 O3 具备受限但足够完整的运行时专家能力，能够在 2 分钟内完成判断。
7. 让 O3 对 Known Events 的直接更新、objection、objection_note 都具备最小审计能力。
8. 当前阶段不接真实交易执行，所有 trade 路径统一记录到 Trading Records。

---

## 2. 本阶段与 Document 3 的关系

Document 3 是本阶段运行时执行层的上游基础资产。

本阶段运行时系统主要消费：

1. Known Events：用于 W1 判断消息是旧消息、旧事件回顾、旧事件新进展，还是全新事件。
2. Monitoring Execution Policy：用于 W2 判断消息是否命中 Direct Trade Candidate 或 Escalate to Background Agent。
3. Document 1 / Document 2 / Document 3 Brief State：用于 O3 在必要时进行深度判断。
4. Monitoring Config：用于辅助判断消息来源是否属于系统主动监测范围，以及消息是否与当前 ticker 的运行目标相关。

需要明确的是：

* Document 3 是静态或低频更新的运行文档。
* Persistent Runtime Execution Agents 是高频运行的执行层。
* Document 3 生成阶段和本阶段运行时执行阶段是两个不同模块。
* 原 Document 3 PRD 中 O3 暂不启用的设计，仅适用于 Document 3 生成阶段；本阶段需要新增运行时 O3 值班专家。

---

## 3. 本阶段开发范围

### 3.1 本阶段需要实现

本阶段需要实现以下内容：

1. 新增 W1 新旧判定 worker。
2. 新增 W2 policy 判定 worker。
3. 新增 O3 值班专家 agent。
4. 复用并扩展 A2 事实核查能力，用于低置信度和 social 真实性判断。
5. 实现 media 消息实时执行流转。
6. 实现 social 消息 batch 执行流转。
7. 实现运行时路由决策模块。
8. 实现 Trading Records 最小记录能力。
9. 实现 ingest_queue / archive 两层缓存归档能力。
10. 实现 O3 对 Known Events 的直接更新能力。
11. 实现 O3 objection / objection_note 发起能力。
12. 实现最低限度的失败、超时、重试和异常记录逻辑。
13. 实现运行时执行日志，支持后续回放、审计和调试。


### 3.2 与 Monitoring Message Bus 的衔接

本阶段 Persistent Runtime Execution Agents 不直接负责外部数据采集、原始消息落库、标准化、基础去重或监测源配置的底层执行，这些能力由 Monitoring Message Bus 提供。

Monitoring Message Bus 是运行时执行层的上游基础设施。它负责：

1. 按 ticker 或参数持续轮询 media / social 数据源；
2. 将原始消息写入 raw persistence；
3. 将消息标准化为 agent 可消费的 standard message；
4. 基于 provider message id、source URL 或 payload hash 做基础幂等；
5. 为每条 accepted standard message 生成持久化 event stream item；
6. 提供 recent events、source status、ticker config 等 agent-readable 工具和观测接口。

因此，本阶段的边界可以概括为：

> Message Bus 负责“把可监测消息稳定、幂等、可回放地送到事件流”；Persistent Runtime Execution Agents 负责“消费事件流，并基于 Document 3 完成新旧判断、policy 命中、专家研判、交易意图记录和后续归档”。


---

## 4. 核心角色定义

## 4.1 W1：新旧判定 worker

W1 是一个低成本、低参数、快速执行的 LLM 节点。

它不是完整 agent，不具备深度研究能力，不做交易判断，不做 policy 判断，不主动搜索。

W1 的核心职责是：

> 根据新进入的消息和 Known Events，判断该消息是否为新消息，是否命中已知事件，以及它属于旧消息、旧事件回顾、旧事件新进展还是全新事件。

W1 的输入包括：

1. ticker
2. source message
3. message source type：media / social
4. message title
5. message  content
6. published_at / collected_at
7. Known Events 列表
8. 必要的 duplicate_detection_keys

W1 的输出字段为：

```json
{
  "is_new": true,
  "novelty_label": "old_duplicate | known_event_recap | material_update | new_event",
  "matched_known_event_ids": ["KE_001", "KE_002"],
  "confidence": "high | medium | low",
  "reasoning": "简短说明"
}
```

其中：

* `is_new` 是主路由字段。
* `confidence` 是主路由字段。
* `novelty_label` 是辅助诊断字段，不增加主路由复杂度。
* `matched_known_event_ids` 用于审计、后续 Known Events 更新、O3 判断和运行时 eval。
* `reasoning` 只需要简短说明，不需要展开长推理。

### 4.1.1 novelty_label 与 is_new 的映射

W1 的细分类别不直接增加路由分支，只映射为 true / false。

映射规则如下：

| novelty_label     | is_new | 含义                             |
| ----------------- | ------ | ------------------------------ |
| old_duplicate     | false  | 完全重复旧消息或已知事实                   |
| known_event_recap | false  | 对旧事件的回顾、评论、复述或总结               |
| material_update   | true   | 旧事件出现新的金额、数量、状态、时间节点、官方确认或阶段变化 |
| new_event         | true   | 全新事件，不属于 Known Events 中已有事件    |

这样可以在不扩大路由复杂度的情况下，为后续 O3、cache、Known Events 更新和 eval 提供更细的信息。

### 4.1.2 W1 confidence 的含义

W1 的 confidence 只表示新旧判断的置信度。

它不表示：

* policy 命中置信度
* 消息真实性置信度
* source 可靠性置信度

confidence 枚举为：

```text
high | medium | low
```

---

## 4.2 W2：policy 判定 worker

W2 是一个低成本、低参数、快速执行的 LLM 节点。

它不是完整 agent，不做深度研究，不做搜索，不判断价格是否 price in，不生成最终交易决策。

W2 的核心职责是：

> 根据 Monitoring Execution Policy 判断新进入消息是否命中已有 policy，并输出该消息在运行时属于 DTC、EBA、NULL 还是 Irrelevant。

W2 的输入包括：

1. ticker
2. source message
3. message source type：media / social
4. message title
5. message summary
6. message  content
7. Monitoring Execution Policy 列表

W2 的输出字段为：

```json
{
  "matched_policy_code": "POLICY_ID | NULL",
  "type": "Direct Trade Candidate | Escalate to Background Agent | NULL | Irrelevant",
  "reasoning": "简短说明"
}
```

W2 不输出 confidence。

原因是：W2 加 confidence 可能导致模型倾向于保守输出 medium / low，进而使路由复杂化。本阶段通过严格定义四类 type 来控制 W2 的行为。

### 4.2.1 W2 四类 type 定义

#### 1. Direct Trade Candidate

表示消息明确命中某条可交易 policy。

要求：

* 必须命中具体 policy。
* `matched_policy_code` 不得为 NULL。
* 消息内容必须明确满足该 policy 的 trigger。
* 不能因为“看起来利好 / 利空”就泛化命中。
* 当前阶段输出后不下真实订单，只进入 Trading Records。

#### 2. Escalate to Background Agent

表示消息明确命中某条需要专家研判的 policy。

要求：

* 必须命中具体 policy。
* `matched_policy_code` 不得为 NULL。
* 消息本身重要，但不适合直接转为 trade record。
* 需要 O3 结合原文、Document 1 / 2 / 3、实时价格、新闻检索或事实核查进一步判断。

#### 3. NULL

表示消息与 ticker、expectation 或当前监测目标相关，但没有命中任何已有 policy。

NULL 不是 cache。

NULL 的含义是：

> 相关，但未被 Document 3 的 Monitoring Execution Policy 覆盖。

NULL 通常代表：

* 新事件类型；
* policy 没有预见到的消息；
* 与当前 ticker 相关但没有直接 trigger 的消息；
* 需要 O3 判断是否重大、是否 price in、是否要更新 blackboard 的消息。

#### 4. Irrelevant

表示消息属于监测误召回、低相关、低质量或不服务于当前 ticker 运行目标。

Irrelevant 的含义是：

> 不值得进入本 ticker 的实时运行判断。

Irrelevant 不等于 NULL。

Irrelevant 通常包括：

* 与 ticker 无实质关系；
* 只是同名实体误召回；
* 纯情绪表达；
* 低质量社媒喊单；
* 无来源传言；
* 纯广告、引流或噪音；
* 不服务于任何 expectation unit 或全局运行变量。

---

## 4.3 A2：事实核查与新旧复核 agent

A2 是轻量事实核查 agent。

在本阶段中，A2 不负责生成主文档，不负责交易判断，不负责最终路由。A2 只在必要时进行复核。

A2 的运行时职责包括：

1. 当 W1 对 media 消息的新旧判断 confidence 为 low 时，复核该消息是否为新。
2. 当 social 消息被 W1 判断为新，且 W2 不为 Irrelevant 时，判断该 social 消息是否为新且是否真实。
3. 当 O3 需要事实核实时，可被 O3 调用或由路由系统前置调用。
4. 对明显无法核验或真实性不足的 social 消息进行降级处理。

A2 的输出建议包括：

```json
{
  "is_new": true,
  "verification_status": "verified | likely_true | unverified | likely_false | denied",
  "reasoning": "简短说明",
  "evidence_refs": []
}
```

其中：

* `is_new` 用于复核 W1 的新旧判断。
* `verification_status` 用于 social 真实性判断。
* A2 不需要输出 trade 相关字段。

---

## 4.4 O3：值班专家 agent

O3 是本阶段新增的运行时值班专家 agent。

O3 的定位是：

> 一个能力全面但受限、轻量、快速的值班专家，用于处理 W1 / W2 无法充分覆盖的少数高价值、高不确定性或 policy 未覆盖消息。

O3 不是开放式研究 agent。它不应长时间循环搜索，也不应承担完整 Document 1 / 2 / 3 重写职责。

O3 的核心职责包括：

1. 阅读原文并判断消息是否包含可交易信息。
2. 判断消息是否为重大利好、重大利空、潜在利好、潜在利空或无实质价值。
3. 判断消息是否已经被市场 price in。
4. 判断消息是否影响 Document 1 的全局研究背景。
5. 判断消息是否影响 Document 2 的 expectation unit。
6. 判断消息是否说明 Document 3 的 Known Events、Monitoring Config 或 Policy 需要更新。
7. 决定消息最终进入 Trading Records、ingest_queue、archive、objection 或 objection_note。
8. 在必要时直接更新 Known Events。
9. 在必要时发起 objection 或 objection_note。
10. 在 O3 超时时记录异常，但不阻断 trade 路径。

---

## 5. O3 的轻量化与时间约束

O3 的设计目标是：

> 尽可能在 2 分钟内完成判断。

为实现这一目标，O3 必须是 bounded expert，而不是开放式 agent loop。

### 5.1 O3 的上下文由 Context Builder 预装

在 O3 启动前，系统应预先准备必要上下文，避免 O3 自行多轮读取。

Context Builder 应尽量提供：

1. 原消息标题、摘要、正文或可用片段；
2. W1 输出；
3. W2 输出；
4. matched_known_event_ids；
5. 相关 Known Events；
6. matched policy 或相邻 policy；
7. Document 1 ；
8. 相关 Document 2 expectation units；
9. Document 3；
10. ticker 实时价格快照；
11. 大盘 / 板块实时价格快照；
12. source metadata；
13. 已有路由上下文。

---

### 5.2 O3 的 tool calling 预算

O3 最多允许两轮模型调用。

推荐执行方式：

#### 第一轮：判断是否需要工具

O3 判断：

* 当前上下文是否足够；
* 是否必须读原文；
* 是否必须检索新闻；
* 是否必须看价格；
* 是否必须调用 A2 或事实核查能力。

#### 第二轮：给出最终动作

如果需要工具，O3 可在第一轮后进行一次并行工具调用，然后第二轮必须输出最终动作。

O3 不允许连续多轮搜索。

推荐 tool budget：

```text
read_article: 最多 1 次
price_snapshot: 最多 1 次
news_search / web_search: 最多 2 次
document_read: 尽量由 Context Builder 预先完成，O3 不默认全量读取
A2 verification: 按需调用，避免默认调用
```

---

### 5.3 O3 超时处理

O3 超时不能将消息打入 ingest_queue，也不能阻断 trade 路径。

O3 超时后的处理原则为：

1. 记录 O3 timeout 异常。
2. 写入 execution exception log。
3. 标记 `o3_timeout=true`。
4. 如果该消息所在路径已经进入 trade 判断链路，则正常推入 Trading Records。
5. Trading Records 中应记录该 trade record 存在 O3 timeout 异常。
6. 不因 O3 超时将消息降级为 ingest_queue。
7. 不因 O3 超时丢弃消息。
8. 后续可通过 Trading Records 或异常记录复盘该次判断。

O3 timeout 对应的 Trading Records 状态可标记为：

```json
{
  "status": "recorded_with_exception",
  "exception_type": "o3_timeout"
}
```

---

## 6. O3 输出结构

O3 最终输出应结构化。

建议字段为：

```json
{
  "primary_action": "trading_record | ingest_queue | archive | objection | objection_note",
  "side_effects": ["known_events_update"],
  "trade_intent": {
    "side": "long | short | exit",
    "conviction": "low | medium | high",
    "size_bucket": "small | normal | aggressive",
    "reasoning": "简短说明"
  },
  "known_events_patch": {
    "event_id": "KE_XXX",
    "event_time_or_window": "...",
    "core_fact": "...",
    "duplicate_detection_keys": []
  },
  "blackboard_target": "document1 | document2 | document3 | known_events | null",
  "objection_type": "objection | objection_note | null",
  "reasoning": "简短说明",
  "evidence_refs": []
}
```

说明：

1. `primary_action` 是主动作。
2. `side_effects` 用于记录与主动作并存的副作用，例如同时进入 Trading Records 并更新 Known Events。
3. `trade_intent` 仅在进入 Trading Records 时需要。
4. `known_events_patch` 仅在需要更新 Known Events 时需要。
5. `blackboard_target` 用于指出需要修正哪个文档或板块。
6. `objection_type` 用于区分即时 objection 与延后 objection_note。
7. `reasoning` 必须简短，不需要长篇研究报告。

---

## 7. Media 消息运行流程

media 消息是单篇文章级实时处理。

当 DoxAgent Monitoring 监测到一篇媒体、新闻、机构文章或类似正式来源内容时，应立即推入监测执行管线。

### 7.1 media 基础流程

1. Message Bus 捕捉到 media 消息。
2. 执行基础清洗、去重、标准化。
3. 创建 source_message 记录。
4. 并行启动 W1 和 W2。
5. 等待 W1 / W2 输出。
6. Route Engine 根据 W1 / W2 输出决定下一步。
7. 根据路由结果进入 Trading Records、A2、O3、ingest_queue 或 archive。

---

### 7.2 media 路由规则

media 场景下，以 W1 的 `is_new`、W1 的 `confidence` 和 W2 的 `type` 为核心路由依据。

#### 1. 新 + Direct Trade Candidate

条件：

```text
W1.is_new = true
W2.type = Direct Trade Candidate
```

路由：

* 当 W1 confidence 为 high / medium：

  * 直接进入 Trading Records。
  * 不经过 O3。
  * 当前阶段只记录 trade intent，不执行真实交易。

* 当 W1 confidence 为 low：

  * 流转 A2 判断是否为新消息。
  * 若 A2 判断为新，则进入 Trading Records。
  * 若 A2 判断为旧，则按旧 + DTC 处置。

设计理由：

* DTC 的核心价值是速度。
* 如果 DTC 仍默认进入 O3，则 Document 3 policy 的运行意义会被削弱。
* 只要 policy 已明确覆盖，且 W1 对新旧判断不低置信度，即可绕过 O3。

---

#### 2. 新 + Escalate to Background Agent

条件：

```text
W1.is_new = true
W2.type = Escalate to Background Agent
```

路由：

* 当 W1 confidence 为 high / medium：

  * 流转 O3 研判。

* 当 W1 confidence 为 low：

  * 先流转 A2 判断是否为新消息。
  * 若 A2 判断为新，则进入 O3。
  * 若 A2 判断为旧，则按旧 + EBA 处置。

---

#### 3. 新 + NULL

条件：

```text
W1.is_new = true
W2.type = NULL
```

路由：

* 当 W1 confidence 为 high / medium：

  * 流转 O3 研判。

* 当 W1 confidence 为 low：

  * 仍流转 O3。
  * O3 需要在一开始先完成一次新旧判断。
  * 若 O3 判断为旧，则进入 archive 或 ingest_queue。
  * 若 O3 判断为新，则继续判断是否 trade、ingest_queue、archive、objection 或 objection_note。

设计理由：

* NULL 表示消息相关但 policy 未覆盖。
* NULL 是 O3 最重要的工作来源之一。
* NULL 不等于 cache。

---

#### 4. 新 + Irrelevant

条件：

```text
W1.is_new = true
W2.type = Irrelevant
```

路由：

* 一律进入 archive。
* 不进入 O3。
* 不进入 Trading Records。
* 不进入 A2。
* 不进入 ingest_queue，除非后续规则认为该来源仍具有批量舆情价值。

---

#### 5. 旧 + Direct Trade Candidate

条件：

```text
W1.is_new = false
W2.type = Direct Trade Candidate
```

路由：

* 当 W1 confidence 为 high：

  * 进入 archive。
  * 理由是旧消息即使命中 DTC，也不应触发实时 trade。

* 当 W1 confidence 为 medium / low：

  * 流转 A2 判断是否为新消息。
  * 若 A2 判断为新，则进入 Trading Records。
  * 若 A2 判断为旧，则进入 archive。

---

#### 6. 旧 + Escalate to Background Agent

条件：

```text
W1.is_new = false
W2.type = Escalate to Background Agent
```

路由：

* 当 W1 confidence 为 high：

  * 进入 archive。

* 当 W1 confidence 为 medium / low：

  * 流转 A2 判断是否为新消息。
  * 若 A2 判断为新，则进入 O3。
  * 若 A2 判断为旧，则进入 archive。

---

#### 7. 旧 + NULL / Irrelevant

条件：

```text
W1.is_new = false
W2.type = NULL 或 Irrelevant
```

路由：

* 一律进入 archive。
* 不进入 O3。
* 不进入 Trading Records。

---

## 8. O3 对 media 核心场景的处理

## 8.1 新 + EBA 场景

新 + EBA 表示消息是新的，并且命中某条需要专家研判的 policy。

O3 需要：

1. 阅读原文或使用 Context Builder 提供的正文片段。
2. 二次判断该消息是否包含值得交易的信息。
3. 结合 Document 1、Document 2、Document 3、实时价格、大盘 / 板块状态判断是否有交易价值。
4. 若有交易价值，推入 Trading Records。
5. 若无交易价值，但消息足以影响 ticker 基本面、叙事状态、expectation unit 或后续监测执行，则发起 objection 或 objection_note。
6. 若消息仅有记录价值，则进入 ingest_queue。
7. 若消息无实质价值，则进入 archive。
8. 必要时更新 Known Events。

---

## 8.2 新 + NULL 场景

新 + NULL 表示消息是新的，但未被现有 Monitoring Execution Policy 覆盖。

O3 需要：

1. 阅读原文。
2. 判断该消息是否为重大利好、重大利空、潜在利好、潜在利空或无实质价值。
3. 判断消息是否已经被市场 price in。
4. 判断该消息是否对应既有 expectation unit。
5. 判断该消息是否推翻或强化 Document 2 中的某个 expectation。
6. 判断该消息是否需要新增 Known Events。
7. 判断该消息是否说明 Monitoring Execution Policy 存在遗漏。
8. 若有交易价值，推入 Trading Records。
9. 若无即时交易价值但重要，发起 objection 或 objection_note。
10. 若只是弱信号但有后续分析价值，进入 ingest_queue。
11. 若无价值，进入 archive。

当 W1 confidence 为 low 时，O3 需要在最开始先判断消息到底是否为新。

若 O3 判断为旧，则不应进入 Trading Records，除非该消息在 O3 超时或异常情况下已被上游 trade 路径要求记录。

---

## 9. Social 消息运行流程

social 消息不按单条即时触发深度处理，而是按 batch 处理。

### 9.1 social 基础流程

1. Message Bus 持续轮询 social 数据源。
2. 每 10 分钟聚合一批 social 消息。
3. Message Bus 在推入执行队列前，先做基础清洗。
4. 如果 10 分钟内消息量过大，则拆分成多个 batch。
5. 每个 batch 推入执行管线。
6. W1 / W2 对 batch 内每条消息逐条判断。
7. Route Engine 根据每条消息输出决定是否 archive、A2 或 O3。
8. 同一 polling time window 内需要继续流转的 social 消息合并提交到下一环节。

---

### 9.2 social 与 media 的区别

social 场景与 media 场景的主要区别包括：

1. social 是 batch 提交，不是单条即时提交。
2. W1 / W2 需要逐条输出判断结果。
3. W1 判断为旧的 social，一律进入 archive。
4. W1 判断为新，且 W2 不为 Irrelevant 的 social，一律进入 A2。
5. A2 同时判断消息是否为新、是否真实。
6. A2 通过后再进入 O3。
7. social 的 system prompt 与 media 不同。
8. social 的 Irrelevant 判定必须更严格。
9. social 不做复杂 semantic clustering。
10. social 的合并依据是同一轮 polling time window。
11. Message Bus 在推入队列前已经做一次清洗，因此执行层不重复做重清洗。

---

### 9.3 social 路由规则

#### 1. W1 判断为旧

条件：

```text
W1.is_new = false
```

路由：

* 一律进入 archive。
* 不进入 A2。
* 不进入 O3。
* 不进入 Trading Records。

---

#### 2. W1 判断为新 + W2 为 Irrelevant

条件：

```text
W1.is_new = true
W2.type = Irrelevant
```

路由：

* 进入 archive。
* 不进入 A2。
* 不进入 O3。
* 不进入 Trading Records。

---

#### 3. W1 判断为新 + W2 不为 Irrelevant

条件：

```text
W1.is_new = true
W2.type in [Direct Trade Candidate, Escalate to Background Agent, NULL]
```

路由：

* 一律进入 A2。
* A2 判断是否为新且是否真实。
* 若 A2 判断为旧、likely_false 或 denied，则进入 archive。
* 若 A2 判断为新且 verified / likely_true / unverified，则进入 O3。
* O3 决定是否进入 Trading Records、ingest_queue、archive、objection 或 objection_note。

说明：

* social 即使命中 DTC，也不直接进入 Trading Records。
* social 必须经过 A2 和 O3。
* 这是为了过滤传言、喊单、重复转发和低质量消息。

---

### 9.4 social batch 合并规则

social batch 的合并依据为同一轮 polling time window。

执行层不开发复杂语义聚类。

对于同一 batch 内需要进入 O3 的多条消息，系统应合并为一个 O3 输入包。

输入包应包含：

```json
{
  "batch_window_id": "...",
  "window_start": "...",
  "window_end": "...",
  "ticker": "...",
  "items": [],
  "summary_stats": {
    "total_items": 0,
    "new_items": 0,
    "non_irrelevant_items": 0,
    "a2_passed_items": 0
  }
}
```

如果通过 A2 的消息数量过多，则可只携带 Top K 条代表性消息和聚合统计。

Top K 的规则第一版不需要复杂化，可按以下优先级选择：

1. source 质量更高；
2. 文本信息密度更高；
3. 更早出现；
4. 被更多相似消息重复提及；
5. 与 policy 或 known event 更相关。

---

## 10. Cache 分层设计

本阶段不使用复杂 cache 概念。

cache 只拆成两层：

1. ingest_queue
2. archive

### 10.1 ingest_queue

ingest_queue 表示：

> 暂不触发实时交易，但后续仍值得被 DoxAtlas 或投研 agent 消费的消息池。

进入 ingest_queue 的消息包括：

1. O3 判断无实时交易价值但仍重要的消息。
2. O3 判断需要每日收盘后统一分析的消息。
3. objection_note 对应的来源消息。
4. 相关但未命中 policy 的弱信号。
5. 有舆情价值的 social batch。
6. 可能影响后续 blackboard 但不需要实时处理的消息。
7. 某些节点失败但消息仍相关的异常消息。

ingest_queue 暂时不接 DoxAtlas，后续再实现联动。

---

### 10.2 archive

archive 表示：

> 只留痕，不进入后续分析消费的消息池。

进入 archive 的消息包括：

1. Irrelevant 消息。
2. 明确旧消息。
3. 已知事件回顾。
4. 纯重复内容。
5. 低质量 social。
6. 明显误召回。
7. 无信息量内容。
8. 已处理过的相同 URL / content_hash。
9. A2 判断 likely_false 或 denied 的 social 消息。

---

## 11. Trading Records 最小设计

虽然完整 Trading Records 是后置模块，但本阶段必须实现最小记录能力。

原因是：

> 当前阶段所有进入交易执行阶段的请求都不真实下单，而是统一记录到 Trading Records。

### 11.1 Trading Records 当前阶段定位

当前阶段 Trading Records 不是完整交易复盘账本，而是 trade intent 记录表。

它用于记录：

1. 哪条消息触发了 trade 路径；
2. W1 / W2 的判断结果；
3. 命中的 policy；
4. 进入 trade 的 route；
5. trade intent；
6. 是否存在 O3 timeout 或其他异常；
7. 当前状态为 recorded_only 或 recorded_with_exception。

### 11.2 最小字段

建议最小字段包括：

```json
{
  "record_id": "...",
  "ticker": "...",
  "source_message_id": "...",
  "source_type": "media | social",
  "route": "new_dtc | a2_confirmed_dtc | o3_trade | o3_timeout_trade",
  "matched_policy_code": "POLICY_ID | NULL",
  "w1_result_ref": "...",
  "w2_result_ref": "...",
  "a2_result_ref": "...",
  "o3_result_ref": "...",
  "trade_intent": {
    "side": "long | short | exit",
    "conviction": "low | medium | high",
    "size_bucket": "small | normal | aggressive",
    "reasoning": "简短说明"
  },
  "status": "recorded_only | recorded_with_exception",
  "exception_type": "o3_timeout | worker_error | null",
  "created_at": "..."
}
```

当前阶段不需要：

* broker_order_type
* limit_price
* stop_loss
* take_profit
* exact_position_size
* account_id
* order_id
* realized pnl
* exit reason

这些字段留给未来真实交易执行模块。

---

## 12. Known Events 运行时更新

Known Events 是运行时新旧判断的核心记忆表。

本阶段允许 O3 直接更新 Known Events。

需要特别说明的是：

在「新 + Direct Trade Candidate」路径下，消息不会进入 O3，而是直接进入 Trading Records。但为了保证 Known Events 的持续更新能力，该类消息仍需要触发一个并行的 O3 轻量更新流程，仅用于 Known Events 更新与必要的运行时修正，不参与交易判断。

### 12.1 更新原则

O3 可以在以下情况下直接更新 Known Events：

1. 新消息是全新事件。
2. 旧事件出现 material_update。
3. 旧事件进入新阶段。
4. 消息提供新的金额、数量、时间、状态、官方确认。
5. 消息会导致后续 W1 重复误判。
6. 消息虽然不触发 trade，但后续很可能被反复报道或引用。
7. 新 + Direct Trade Candidate 场景下，即使消息已直接进入 Trading Records，仍需通过并行 O3 更新 Known Events，以避免后续重复误判或信息缺失。

Known Events 更新无需走初始化阶段的审查流程。

持久化运行阶段的修改应尽快生效，以服务后续监测执行。

---

### 12.2 最小审计字段

Known Events 直接更新必须留痕，但字段要保持轻量。

最小审计字段为：

```json
{
  "known_event_id": "KE_001",
  "source_ref": "source_message_id or url",
  "change_reason": "简短说明",
  "changed_at": "..."
}
```

不需要在第一版引入复杂 version、changed_by、supersedes_event_id 等字段。

---

## 13. Objection 与 objection_note

O3 可以对 Document 1、Document 2、Document 3 或其中的具体板块发起 objection 或 objection_note。

### 13.1 objection

objection 是即时修正请求。

适用于：

> 不立刻改，会影响后续持久化运行正确性的情况。

触发条件包括：

1. Known Events 缺失会导致后续持续误判新旧。
2. Monitoring Execution Policy 明显漏掉一个高频或高风险事件类型。
3. 某条 policy 可能把错误消息推成 Direct Trade Candidate。
4. Monitoring Config 需要立即新增或删除，否则后续监测会持续失效。
5. Document 2 的某个 expectation unit 被新事实明显推翻。
6. Document 1 的核心基本面、行业判断或价格反映判断被重大事实改变。
7. 不改 Document 1 / 2 / 3 会影响后续 W1 / W2 / O3 的运行判断。

objection 应尽量少发。

预期频率是几天才会出现一次。

---

### 13.2 objection_note

objection_note 是延后处理的非阻塞记录。

适用于：

> 消息有研究价值，但不影响当前实时运行正确性，可以等每日收盘后统一处理。

触发条件包括：

1. 对叙事有边际影响。
2. 对 expectation 有弱影响。
3. 对后续研究有帮助。
4. 对市场关注度或舆情状态有参考价值。
5. 属于重要但不紧急的信息。
6. 不影响当前 W1 / W2 / O3 的运行判断。
7. 不影响当前 trade 路径。

objection_note 应进入后续每日收盘处理队列。

---

### 13.3 objection 频率控制

为避免 blackboard 被频繁修改，需要限制 O3 发起 blocking objection 的频率。

建议第一版规则：

```text
同一 ticker 每个交易日 blocking objection 默认最多 1 条。
同一 document target 的相似 objection 必须合并。
O3 低置信度时，只能生成 objection_note，不能生成 objection。
Known Events 直接更新不受该限制。
```

---

## 14. 运行时消息状态

每条进入执行管线的消息应有清晰状态。

建议状态包括：

```text
received
cleaned
deduplicated
w1_running
w2_running
workers_completed
a2_running
o3_running
routed_to_trading_records
routed_to_ingest_queue
routed_to_archive
objection_created
objection_note_created
known_events_updated
failed_with_exception
```

消息状态用于：

1. 观测执行管线；
2. debug；
3. 后续 replay；
4. 失败恢复；
5. 运行审计。

---

## 15. Route Engine 设计原则

Route Engine 是执行编排中的核心决策模块。

它不做 LLM 判断，而是根据 W1、W2、A2、O3 的结构化输出执行确定性路由。

Route Engine 的职责包括：

1. 等待 W1 / W2 并行结果。
2. 校验 W1 / W2 schema。
3. 根据 media / social 类型进入不同路由。
4. 根据 W1 is_new、confidence、W2 type 决定下一节点。
5. 处理 A2 结果。
6. 处理 O3 结果。
7. 写 Trading Records。
8. 写 ingest_queue / archive。
9. 写异常记录。
10. 触发 Known Events patch。
11. 创建 objection / objection_note。
12. 保证同一 source_message 不重复处理。

---

## 16. 去重与幂等

执行层需要最低限度的去重与幂等能力。

### 16.1 去重依据

建议使用：

1. source_message_id
2. url_hash
3. content_hash
4. source_type + source_id + published_at
5. batch_window_id + item_id

### 16.2 幂等原则

1. 同一 source_message 不应重复进入 Trading Records。
2. 同一 source_message 不应重复触发 O3。
3. 同一 source_message 不应重复创建 objection。
4. 同一 URL 或 content_hash 重复出现时，应合并或进入 archive。
5. social batch 拆分后仍需保留 batch_window_id，避免重复消费。

---

## 17. 失败、超时与重试逻辑

第一版只做最低限度定义，后续根据真实运行调优。

### 17.1 W1 失败

包括：

* schema invalid
* timeout
* LLM error

处理：

1. 重试 1 次。
2. 若仍失败：

   * media：进入 O3。
   * social：进入 ingest_queue。
3. 记录 `w1_failed=true`。

---

### 17.2 W2 失败

包括：

* schema invalid
* timeout
* LLM error

处理：

1. 重试 1 次。
2. 若仍失败：

   * 若 W1 判断为新：进入 O3。
   * 若 W1 判断为旧：进入 archive。
3. 记录 `w2_failed=true`。

---

### 17.3 A2 失败

处理：

1. 不直接进入 Trading Records。
2. 对 social：进入 ingest_queue 或 archive，具体根据 W2 type 与 source 质量判断。
3. 对 media：若原路径是 DTC 且只是 A2 复核失败，可进入 ingest_queue 并记录异常；后续可人工或每日复盘。
4. 记录 `a2_failed=true`。

---

### 17.4 O3 超时

处理：

1. 不打入 ingest_queue。
2. 不阻断 trade 路径。
3. 正常推入 Trading Records。
4. Trading Records 标记 `status=recorded_with_exception`。
5. Trading Records 标记 `exception_type=o3_timeout`。
6. execution exception log 记录 `o3_timeout=true`。
7. 后续通过异常记录和 Trading Records 复盘。

---

### 17.5 O3 普通失败

如果 O3 不是超时，而是 schema invalid、tool error、不可恢复错误：

1. 自动修复或重试 1 次。
2. 若仍失败，记录异常。
3. 若该消息已有明确 trade 上游路径，则进入 Trading Records 并标记异常。
4. 若没有明确 trade 上游路径，则进入 ingest_queue。
5. 不直接丢弃。

---

### 17.6 原文读取失败

处理：

1. O3 可基于摘要、标题、source metadata 和必要搜索判断。
2. 若判断足够，则继续输出。
3. 若判断不足，则进入 ingest_queue 或 Trading Records with exception，取决于上游路径。
4. 记录 `article_read_failed=true`。

---

### 17.7 LLM 输出字段缺失

处理：

1. 自动修复 1 次。
2. 修复失败按对应节点失败处理。
3. 所有 invalid output 写入 execution log。

---

## 18. Prompt 与判定规则要求

### 18.1 W1 prompt 要求

W1 prompt 必须强调：

1. 只判断新旧，不判断交易价值。
2. 必须基于 Known Events 判断。
3. 必须输出 is_new、novelty_label、matched_known_event_ids、confidence、reasoning。
4. material_update 应视为 is_new=true。
5. new_event 应视为 is_new=true。
6. old_duplicate 和 known_event_recap 应视为 is_new=false。
7. 如果出现新的金额、数量、状态、时间节点、官方确认、主体变化，应倾向 material_update。
8. 如果只是重复旧事实或评论旧事件，应倾向 old_duplicate 或 known_event_recap。
9. reasoning 保持简短。

---

### 18.2 W2 prompt 要求

W2 prompt 必须强调：

1. 只判断是否命中 policy，不判断新旧。
2. 不输出 confidence。
3. 只有明确满足 policy trigger 才能输出 DTC 或 EBA。
4. 相关但未命中 policy，输出 NULL。
5. 无关或低质量，输出 Irrelevant。
6. NULL 不等于 cache。
7. Irrelevant 不等于 NULL。
8. 不允许因为消息看起来利好 / 利空就泛化命中 policy。
9. matched_policy_code 必须与输出 type 一致。
10. DTC 和 EBA 必须有具体 matched_policy_code。
11. NULL 和 Irrelevant 的 matched_policy_code 应为 NULL。

---

### 18.3 social prompt 要求

social 的 W1 / W2 prompt 需要与 media 区分。

social prompt 必须更严格过滤：

1. 纯喊单；
2. 纯情绪表达；
3. 纯价格口号；
4. 无来源传言；
5. 重复转发；
6. 图片空帖；
7. 带单、老师、群聊引流；
8. 与 ticker 无关的泛市场情绪；
9. 无新增事实的短句；
10. 疑似机器人或灌水内容。

social 中的 Irrelevant 判定应比 media 更严格。

---

### 18.4 O3 prompt 要求

O3 prompt 必须强调：

1. 目标是在 2 分钟内输出可执行判断。
2. 不进行开放式研究。
3. 不写长篇研究报告。
4. 必须在有限上下文和有限工具调用内完成判断。
5. 需要明确输出 primary_action。
6. 如果 trade，必须输出 trade_intent。
7. 如果更新 Known Events，必须输出 known_events_patch。
8. 如果发起 objection / objection_note，必须说明 target 和原因。
9. 必须区分 objection 和 objection_note。
10. 必须尽量少发 blocking objection。
11. O3 可以直接更新 Known Events。
12. O3 超时不阻断 trade 路径，但必须记录异常。

---

## 19. 数据记录与审计要求

本阶段所有关键动作都应留痕。

至少需要记录：

1. source_message
2. W1 result
3. W2 result
4. A2 result
5. O3 result
6. route decision
7. Trading Records
8. ingest_queue item
9. archive item
10. Known Events patch log
11. objection record
12. objection_note record
13. exception log

---

## 20. 观测与调试需求

第一版需要支持研发和用户观察运行状态。

至少应能够查看：

1. 最近进入执行管线的消息。
2. 每条消息的 source type。
3. W1 判断结果。
4. W2 判断结果。
5. A2 判断结果。
6. O3 判断结果。
7. 最终路由去向。
8. 是否进入 Trading Records。
9. 是否进入 ingest_queue。
10. 是否进入 archive。
11. 是否触发 Known Events 更新。
12. 是否触发 objection / objection_note。
13. 是否出现 timeout / schema invalid / tool error。
14. 每个节点耗时。

---

## 21. 总体运行流程

### 21.1 media 总流程

```text
Message Bus captures media
→ clean / deduplicate / normalize
→ create source_message
→ W1 and W2 run in parallel
→ Route Engine joins W1/W2
→ route by W1.is_new + W1.confidence + W2.type
→ possible targets:
   - Trading Records
   - A2
   - O3
   - ingest_queue
   - archive
→ record all outputs and route decision
```

---

### 21.2 social 总流程

```text
Message Bus polls social source
→ aggregate every 10 minutes
→ clean before enqueue
→ split batch if too large
→ W1/W2 classify each item
→ old items archive
→ irrelevant items archive
→ new and non-irrelevant items go to A2
→ A2 verifies novelty and truthfulness
→ A2-passed items merge by polling window
→ O3 judges batch
→ possible targets:
   - Trading Records
   - ingest_queue
   - archive
   - Known Events update
   - objection
   - objection_note
→ record all outputs and route decision
```

---

## 22. 验收标准

本阶段完成后，应满足以下标准：

1. 系统能够消费 Message Bus 推入的 media 消息。
2. 系统能够消费 Message Bus 推入的 social batch。
3. media 消息进入后，W1 / W2 能够并行启动。
4. social batch 进入后，W1 / W2 能够逐条判断。
5. W1 能输出 is_new、novelty_label、matched_known_event_ids、confidence、reasoning。
6. W1 的主路由只依赖 is_new 和 confidence，不因 novelty_label 增加复杂分支。
7. W2 能输出 matched_policy_code、type、reasoning。
8. W2 不输出 confidence。
9. W2 type 仅包含 Direct Trade Candidate、Escalate to Background Agent、NULL、Irrelevant。
10. NULL 被定义为相关但未命中 policy。
11. Irrelevant 被定义为误召回、低相关或低质量。
12. NULL 不等于 cache。
13. Irrelevant 不等于 NULL。
14. 新 + DTC + W1 high / medium confidence 能直接进入 Trading Records。
15. 新 + DTC 不默认进入 O3。
16. 新 + EBA 能进入 O3。
17. 新 + NULL 能进入 O3。
18. 新 + Irrelevant 能进入 archive。
19. 旧 + DTC / EBA 在 W1 high confidence 时进入 archive。
20. 旧 + DTC / EBA 在 W1 medium / low confidence 时进入 A2 复核。
21. 旧 + NULL / Irrelevant 一律进入 archive。
22. social 中 W1 判断为旧的消息一律进入 archive。
23. social 中 W1 判断为新且 W2 不为 Irrelevant 的消息一律进入 A2。
24. social 通过 A2 后进入 O3。
25. social 的 Irrelevant 判定比 media 更严格。
26. social batch 按 polling time window 合并，不做复杂语义聚类。
27. O3 能在有限上下文和有限工具调用内完成判断。
28. O3 目标运行时间为 2 分钟内。
29. O3 超时不阻断 trade 路径。
30. O3 超时应正常推入 Trading Records，并记录异常。
31. O3 能输出 trading_record、ingest_queue、archive、objection、objection_note 等动作。
32. O3 能直接更新 Known Events。
33. Known Events 直接更新无需审查即可生效。
34. Known Events 更新具备最小审计字段。
35. O3 能区分 objection 与 objection_note。
36. blocking objection 有频率限制。
37. objection_note 能进入每日收盘后统一处理队列。
38. cache 仅拆成 ingest_queue 和 archive。
39. ingest_queue 暂时不接 DoxAtlas，但保留后续联动能力。
40. archive 只留审计，不进入后续分析消费。
41. 当前阶段不接 broker。
42. 当前阶段不真实下单。
43. 所有 trade 路径统一写入 Trading Records。
44. Trading Records 当前只做最小 trade intent 记录。
45. 所有节点输出、路由决策和异常都可追踪。
46. W1 / W2 / A2 / O3 失败有最低限度重试与 fallback。
47. 同一 source_message 不应重复进入 Trading Records。
48. 同一 URL / content_hash 重复消息应合并或 archive。
49. 系统可观测每条消息的执行状态、节点耗时和最终去向。
50. 本阶段运行时系统与 Document 3 生成系统边界清晰。

---

## 23. 核心设计原则总结

本阶段的核心设计原则是：

1. 低成本 worker 做初筛。
2. O3 只处理少数高价值或高不确定性消息。
3. DTC 绕过 O3，以保留 policy 的速度价值。
4. NULL 进入 O3，以覆盖 Document 3 未预见的新事件。
5. Irrelevant 进入 archive，以降低噪音。
6. social 比 media 更严格。
7. O3 必须轻量、受限、快速。
8. 失败时保留记录，不轻易丢弃相关消息。
9. O3 超时不能阻断 trade 路径。
10. 所有副作用必须可审计。
11. 当前阶段只记录 trade intent，不真实交易。
12. Known Events 可以被 O3 直接更新，以支持持久化运行中的新旧识别。
13. objection 必须少而准，objection_note 承担大部分非紧急更新压力。
14. 系统的核心不是让 agent 自由发挥，而是让 workers、Route Engine、A2、O3 在明确边界下协同运行。
