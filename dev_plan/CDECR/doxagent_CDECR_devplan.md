# 金融新闻／社媒跨文档事件共指与 Event Package 构造流程

## 一、系统目标与对象层级

系统处理持续进入的股票新闻、公告、快讯和社媒消息，最终形成两层结构：

```text
Source Message
    ↓
Event Mention
    ↓ 跨文本严格共指
Atomic Event
    ↓ 事项归属与事件关系
Event Package
```

其中：

* **Event Mention**：某条文本中对一个事件的具体表述；
* **Atomic Event**：多个Mention共同指向的同一次现实发生；
* **Event Package**：由多个不同Atomic Event组成的同一披露、交易、事故、政策或持续事项。

例如：

```text
媒体A：Goldman将StockA目标价上调至1300美元
媒体B：GS raises StockA PT to $1,300
```

形成一个Atomic Event。

而：

```text
StockC发布Q2财报
StockC Q2净利润增长29%
StockC上调全年资本支出指引
```

形成三个Atomic Event，但共同归入一个“StockC 2026年Q2财报”Event Package。

系统只做到Event Package层，不继续扩展到更宽泛的Topic或Narrative层。

---

# 二、阶段一：消息输入与基础预处理

每条Source Message首先保留事件处理需要的输入：

* `message_id`；
* `source_type`；
* `title`与`text`；
* `published_at`；
* `source_name`；
* `url`；
* `ticker_hints`；
* `parent_message_id`等转发、引用或转载关系；
* `language`。

预处理只负责准备可靠输入，包括：

* 清除网页模板和无关文本；
* 保留标题、导语和正文结构；
* 标准化发布时间和时区；
* 识别完全重复的消息；
* 处理社媒转发和新闻转载；
* 对过长文本切分为连续段落块。

此时可以附带高置信度实体提示，例如消息源自StockA频道或Feed已经提供ticker，但不在这里执行完整的跨文档实体归一，也不能让预识别结果限制后续开放事件发现。

普通新闻以完整文章为处理单位；过长文章采用：

```text
公共上下文：
标题 + 导语 + 来源信息

局部输入：
若干连续段落块
```

而不是逐句发起模型请求。

---

# 三、阶段二：文本级Dreamer开放发现事件

Dreamer读取完整新闻或段落块，高召回地发现所有可能的原子事件候选。

它只回答：

> 文本中出现了哪些可以独立判断真假的行为、变化、状态或信息发布？

例如：

```text
StockC发布Q2财报，净利润同比增长29%，
资本支出增长24%，并上调全年资本开支计划。
```

Dreamer可以输出：

```text
C1：StockC发布Q2财报
C2：StockC Q2净利润同比增长29%
C3：StockC Q2资本支出增长24%
C4：StockC上调全年资本支出计划
```

Dreamer不负责：

* 最终判断候选是否独立；
* 填满Universal Event Core；
* 跨文本共指；
* 创建Atomic Event；
* 创建全局Event Package。

它的职责是避免因为固定金融Schema覆盖不足而漏掉产业、政策、事故和突发事件。

---

# 四、阶段三：Grounder进行文内收敛与事件表示构造

Grounder同时读取：

* 原文和必要上下文；
* Dreamer发现的全部候选；
* Universal Event Core定义；
* 少量可选金融Schema插件。

它负责把开放候选收敛为可以跨文本比较的Event Mention。

## 1. 文内事件边界处理

Grounder需要：

* 删除明显不是事件的候选；
* 把复合候选拆成多个原子事件；
* 合并同一文本中的重复发现；
* 区分事实、计划、传闻、预测、否认和观点；
* 区分事件本身、原因、结果和市场反应。

例如：

```text
火灾导致Company X大阪工厂停产。
```

应形成：

```text
M1：Company X大阪工厂发生火灾
M2：Company X暂停大阪工厂生产
M1 --causes--> M2
```

而不是把整句话压缩为一个模糊事件。

## 2. 文内局部指代解析

Grounder只解决当前文本中形成完整Event Mention所必要的局部问题：

```text
the company → StockC
the bank → Goldman Sachs
it → price target
the June quarter → StockC FY2026 Q2
```

这一步是Mention-local resolution，不是完整的跨文档Canonicalization。

## 3. 去语境化与Canonical Proposition

Grounder将依赖上下文的表达改写为可独立理解的事件描述。

例如：

```text
原始表达：
“GS bumped it to 1.3K after the print.”

Canonical Proposition：
“Goldman Sachs raised its price target for StockA
to USD 1,300 after StockA's quarterly earnings release.”
```

去语境化不是单独的后置调用，而是Grounder构造Event Mention的一部分。

每个Mention同时保留：

```text
原始证据Span
Canonical Proposition
结构化Event Core
```

## 4. Universal Event Core

所有事件使用统一的基础容器：

```json
{
  "event_family": "ANALYST_ACTION",
  "predicate": {
    "raw": "bumped",
    "normalized": "raise price target"
  },
  "participants": [
    {
      "surface": "Goldman",
      "entity_id": "INSTITUTION_GOLDMAN_SACHS",
      "role": "ACTOR"
    },
    {
      "surface": "StockA",
      "entity_id": "COMPANY_STOCK_A",
      "role": "TARGET"
    }
  ],
  "locations": [],
  "time": {
    "event_start": "2026-07-15",
    "event_end": null,
    "precision": "DAY",
    "reference_period_id": null
  },
  "assertion_state": "ACTUAL",
  "quantities": [
    {
      "metric_id": "PRICE_TARGET",
      "value": 1300,
      "unit": "USD",
      "raw_text": "$1.3K"
    }
  ],
  "evidence_spans": []
}
```

Universal Event Core保证不同领域的事件至少拥有一套可比较的基础表示。其中`event_family`只用于粗分桶和候选召回，`assertion_state`统一表达事件是已发生、进行中、计划、预期、传闻、否认、假设还是未知。

## 5. Open Attributes

当前文本中无法由固定核心字段覆盖的信息，由Grounder一次性附加到具体Event Mention上：

```json
{
  "open_attributes": [
    {
      "key": "capacity_impact",
      "value": "40%",
      "evidence_span": "accounting for 40% of domestic capacity"
    }
  ]
}
```

Open Attributes首先属于Mention，不能直接覆盖Atomic Event上的聚合字段。

## 6. 可选金融Schema投影

如果事件属于成熟高频类型，可以进一步投影到MVP保留的三个专用Schema：

* `FINANCIAL_METRIC`；
* `GUIDANCE`；
* `ANALYST_ACTION`。

匹配失败时仍以开放Event Mention进入后续流程，不能被丢弃或塞入一个无意义的`OTHER`类型。

## 7. Local Package Hint

Grounder还可以输出文本内的Package提示，但此时不直接创建最终全局Package。

例如：

```json
{
  "local_package_hint": {
    "anchor": "StockC FY2026 Q2 earnings release",
    "package_family": "EARNINGS_DISCLOSURE",
    "relation_to_anchor": "DISCLOSED_IN"
  }
}
```

它表示当前Mention可能属于哪一个披露或事项，供后续Event Package阶段使用。

---

# 五、阶段四：Judge批量验证

Judge一次读取原文和Grounder产生的全部Event Mention，批量检查：

* 原文是否支持该事件；
* 事件是否可以独立判断真假；
* 是否错误混合了多个事件；
* 是否与另一个Mention重复；
* 事实状态是否判断错误；
* Open Attribute是否属于该事件；
* 某个“属性”是否其实应当独立成为另一个事件。

Judge输出可以简化为：

```text
ACCEPT
REJECT
SPLIT
DUPLICATE
MERGE_AS_ATTRIBUTE
```

MVP中Judge采用条件调用：

* 候选过多；
* 存在明显拆分争议；
* Grounder低置信度；
* 事件重要性高；
* 属性与事件边界不清。

普通短消息可以由Grounder完成自检，跳过独立Judge。

经过Judge后，系统得到正式的Event Mention集合。

---

# 六、阶段五：跨文档字段Canonicalization

Event Mention形成后，再对用于跨文本比较的结构化字段进行统一。

这一阶段处理：

## 实体归一

```text
NVDA / Nvidia / NVIDIA Corp. / 英伟达
→ COMPANY_NVIDIA
```

```text
GS / Goldman / Goldman Sachs Research
→ INSTITUTION_GOLDMAN_SACHS
```

## 时间与财期归一

```text
Q2 / June quarter / fiscal second quarter
→ COMPANY_X_FY2026_Q2
```

## 指标归一

```text
net profit
GAAP net income
adjusted net income
```

不能简单视为同一指标，需要区分具体会计口径。

## 数值、币种和单位归一

```text
$1.3K / USD 1,300 / 1300 dollars
→ 1300 USD
```

同时保存：

```text
raw value
resolved value
canonical ID
normalization confidence
```

这一阶段主要服务于：

* Atomic Event候选召回；
* Hard Cannot-Link；
* Mention-to-Cluster精排；
* Claim差异和冲突识别。

如果Grounder已经获得可靠canonical ID，后续直接复用，不重复执行完整识别。

---

# 七、阶段六：Atomic Event跨文本共指

Atomic Event不是预先存在的对象，而是Event Mention通过跨文本共指形成的事件簇。

单条Mention形成的单例簇也是合法Atomic Event。

系统需要同时支持两种运行状态。

---

## 1. 冷启动状态

事件库为空时：

```text
Event Mention
→ 候选Mention／临时Cluster召回
→ Mention-to-Mention或Mention-to-Temporary-Cluster判定
→ 形成第一批Atomic Event
```

实现上可以采用顺序方式统一处理：

```text
第一个Mention
→ 没有候选
→ 创建单例Atomic Event E1

第二个Mention
→ 查询E1
→ 如果共指则加入E1
→ 否则创建E2
```

批次完成后，再对本批产生的高相似临时Cluster进行一次轻量检查，降低输入顺序带来的影响。

---

## 2. 持续增量状态

已有Atomic Event Registry后：

```text
新增Event Mention
→ 召回候选Atomic Event Cluster
→ Mention-to-Cluster精排
→ 合并、建立关系或新建事件
```

冷启动和持续运行因此可以共享同一套主逻辑，只是候选目标不同：

```text
冷启动：
新Mention ↔ 本批临时Cluster

持续运行：
新Mention ↔ 历史Cluster + 本批临时Cluster
```

---

## 3. Atomic Event候选召回

采用多路召回取并集：

```text
核心实体
时间窗口
Event Family
Schema字段
Canonical Proposition embedding
来源／转载指纹
```

例如目标价事件优先召回：

```text
同一股票
+ 同一机构
+ 时间接近
+ ANALYST_ACTION
```

召回阶段追求高召回，只返回少量Top-K候选，不直接决定是否合并。

---

## 4. Hard Cannot-Link

精排前先排除明确不可能共指的候选。

通用冲突包括：

* 核心主体或交易对手明确冲突；
* 规范化谓词或事件行为明确冲突；
* 事件发生时间或参考期间不相容；
* `assertion_state`明确冲突；
* 生命周期阶段不同；
* Schema身份字段冲突；
* 关键地点、工厂或资产明确不相容。

金融插件冲突包括：

* 财期不同；
* 财务指标不同；
* actual与guidance不同；
* GAAP与non-GAAP不同；
* 分析机构不同；
* 并购交易主体不同；
* 传闻、签约、批准和交割阶段不同。

例如：

```text
Goldman上调StockA目标价至1300
Morgan Stanley上调StockA目标价至1300
```

由于机构是该类事件的身份字段，必须形成两个Atomic Event。

Open Attributes默认只是Claim字段，不自动作为Hard Identity字段。

---

## 5. Mention-to-Cluster精排

新Mention需要同时与候选Cluster的以下信息比较：

* `canonical_proposition`；
* `event_family`；
* `identity_profile`；
* `time`；
* `assertion_state`；
* `representative_mention_ids`对应的代表Mention；
* `consensus_claims`；
* `conflict_flags`。

模型语义输出：

```text
SAME_EVENT
RELATED_NOT_SAME
UNRELATED
UNCERTAIN
```

Claim冲突与身份冲突独立输出：

```json
{
  "claim_conflict": true,
  "identity_conflicts": [],
  "confidence": 0.87
}
```

编排层再根据关系、冲突和阈值产生动作：

```text
MERGE
CREATE_NEW
CREATE_AND_LINK
HOLD
```

高置信度`SAME_EVENT`且无身份冲突时执行`MERGE`；`RELATED_NOT_SAME`执行`CREATE_AND_LINK`；无合适候选时执行`CREATE_NEW`；置信度不足时执行`HOLD`。金融场景中应优先避免错误合并。

---

## 6. Atomic Event Cluster更新

Mention合并后，Atomic Event不能简单使用最新来源覆盖旧信息。

应维护：

```text
event_id
canonical_proposition
event_family
identity_profile
time
assertion_state
mention_ids
representative_mention_ids
consensus_claims
conflict_flags
cluster_confidence
version
```

例如：

```text
Atomic Event E1：
Goldman于7月15日上调StockA目标价

来源A：新目标价1300
来源B：新目标价1300
来源C：旧目标价1100
来源D：同时维持Buy评级
```

Atomic Event保存稳定事件身份；Mention保存各自来源的具体陈述。

---

# 八、阶段七：Atomic Event局部校正

纯顺序归并可能受到输入顺序和早期信息不完整的影响。

因此每个批次结束后，对有限邻域执行轻量校正：

```text
相同核心实体
+ 相近时间
+ 相同Event Family
+ 高语义相似
```

检查：

* 是否存在明显重复Atomic Event；
* 是否出现一个Mention强匹配多个Cluster；
* 是否存在身份冲突却被合并；
* 是否存在可能需要合并的单例事件。

MVP只需支持：

* 明显重复簇合并；
* 冲突标记；
* 暂停不确定的自动合并。

暂不实现复杂的全局图聚类或自动拆簇。

完成这一步后，系统获得相对稳定的Atomic Event节点。

---

# 九、阶段八：Event Package构造

Event Package的构造必须在Atomic Event跨文本共指之后执行。

原因是，如果直接使用Event Mention构造Package，同一事实的多篇重复报道会变成多个重复节点。

正确顺序是：

```text
Event Mention
→ 跨文本共指
→ Atomic Event
→ Package归属与事件关系
→ Event Package
```

Grounder此前生成的Local Package Hint可以辅助这一阶段，但不能直接决定最终归属。

---

## 1. Event Package的两种类型

### BOUNDED Package

存在明确的信息载体或父锚点：

* 财报发布；
* 投资者日；
* 公司公告；
* 监管文件；
* 某份分析师报告。

例如：

```text
StockC FY2026 Q2 Earnings Package
```

其身份通常由以下字段决定：

```text
锚点实体
Package Family
财期或报告期
披露日期
锚点文档／公告ID
```

### EPISODE Package

没有单一文档可以完整代表，而是持续演化的事项：

* 并购事项；
* 监管调查；
* 工厂事故与复产；
* 政策制定和实施；
* 产品危机。

例如：

```text
Company A收购Company B事项
├── 收购传闻
├── 正式要约
├── 签署协议
├── 监管审批
└── 完成交割
```

内部节点都是不同Atomic Event。

---

## 2. Package候选生成

每个新Atomic Event形成或更新后，尝试召回可能归属的Event Package。

BOUNDED Package优先使用：

```text
锚点实体
Package Family
财期
披露日期
锚点文档／公告ID
Local Package Hint
```

EPISODE Package使用：

```text
核心参与者组合
交易／政策／事故对象
产品、资产、工厂或地点
时间窗口
事项类型
Package摘要embedding
已有成员事件和生命周期阶段
```

如果没有可用Package，当前Atomic Event可以创建一个新的单事件Package；后续其他事件再加入。

---

## 3. Atomic-to-Package精排

系统判断：

> 当前Atomic Event是否属于候选Package所代表的同一披露或事项。

输入包括：

* 新Atomic Event完整Profile；
* 候选Package Profile；
* Package已有成员；
* 已有事件关系；
* Local Package Hint；
* 关键原文证据。

模型语义输出：

```text
MEMBER
EXTERNAL_RELATED
NOT_RELATED
UNCERTAIN
```

编排层根据结果产生：

```text
ADD_TO_PACKAGE
CREATE_NEW_PACKAGE
LINK_EXTERNALLY
HOLD
```

加入Package时使用统一成员关系：

```text
DISCLOSED_IN
COMPONENT_OF
STAGE_OF
UPDATE_OF
CORRECTION_OF
IMPLEMENTATION_OF
```

---

## 4. 什么事件允许进入同一个Package

强成员关系包括：

```text
DISCLOSED_IN
COMPONENT_OF
STAGE_OF
UPDATE_OF
CORRECTION_OF
IMPLEMENTATION_OF
```

而以下关系一般只作为Package外部连接：

```text
CAUSES
MARKET_REACTION_TO
ANALYST_REACTION_TO
CONFIRMS
CONTRADICTS
RELATED_TO
```

例如：

```text
StockC发布Q2财报
StockC盘后上涨8%
```

建议处理为：

```text
财报Atomic Event → 财报Package成员
股价上涨Atomic Event → Package外部事件
股价上涨 --MARKET_REACTION_TO--> 财报Package
```

这样可以防止Package无限扩张。

---

## 5. Package-to-Package共指

不同文章或批次可能先创建两个局部Package：

```text
P1：StockC Q2 earnings
P2：StockC June-quarter results
```

需要进一步判断它们是否代表同一个全局Event Package。判定输出为：

```text
SAME_PACKAGE
DIFFERENT_PACKAGE
UNCERTAIN
```

编排层据此执行`MERGE_PACKAGES`或`HOLD`，不同Package则保持分离。

主要依据：

* 锚点实体是否一致；
* 财期或时间范围是否一致；
* Package Family是否一致；
* 文档或公告ID是否一致；
* 是否包含已经共指的Atomic Event；
* 成员事件是否兼容；
* 是否存在Package级身份冲突。

其中最强的证据通常是：

> 两个Package内部是否已经包含一个或多个相同Atomic Event。

如果判断为同一Package，则合并成员和关系图，同时保留Package版本。

---

## 6. Package Profile更新

每个Event Package保存：

```text
package_id
package_kind
package_family
canonical_title
anchor_entities
anchor_artifact_id
anchor_period_id
time_range
lifecycle_state
member_event_ids
canonical_summary
status
confidence
version
```

其中`package_kind`取`BOUNDED`或`EPISODE`，`status`取`OPEN`、`CLOSED`或`UNKNOWN`。EPISODE Package的阶段与进展通过成员Atomic Event、`lifecycle_state`、`time_range`和`canonical_summary`表达，不另增独立Schema字段。

Package的Canonical Summary由成员Atomic Event生成，但不能代替成员事件本身。

---

# 十、阶段九：Package局部校正

批次结束后，对新建或更新的Package做轻量检查：

* 是否存在相同锚点的重复Package；
* 两个Package是否包含相同Atomic Event；
* 是否因弱语义关系发生过度扩张；
* 是否把市场反应、分析师反应错误纳入内部；
* EPISODE Package的生命周期阶段是否合理；
* 是否存在同一事项被错误拆成多个Package。

MVP中只需支持：

```text
明显重复Package合并
过度扩张Package标记
不确定关系保留为外部边
```

暂不需要复杂图社区发现或全局Package重聚类。

---

# 十一、完整端到端运行方式

## 冷启动批处理

```text
一批Source Message
→ 各文本独立Dreamer / Grounder / Judge
→ 形成Event Mention
→ 跨文本Canonicalization
→ Mention逐步形成Atomic Event
→ 批次级Atomic Event轻量校正
→ Atomic Event逐步归入Event Package
→ Package-to-Package合并
→ 批次级Package轻量校正
```

## 持续增量处理

```text
新Source Message
→ 提取Event Mention
→ Canonicalization
→ 召回历史Atomic Event
→ Mention-to-Cluster归并
→ 更新或新建Atomic Event
→ 召回历史Event Package
→ Atomic-to-Package归属判断
→ 更新或新建Event Package
→ 必要时局部校正
```

因此，不需要定期对全部历史文本一次性重新处理。

系统持续维护两个注册表：

```text
Atomic Event Registry
Event Package Registry
```

新增消息只需要访问与当前事件相关的局部候选。

---

# 十二、MVP实现范围

第一版保留完整架构，但只实现必要能力。

## 单文档处理

* 文本／段落级Dreamer；
* Grounder完成原子化、局部指代解析、Canonical Proposition；
* Universal Event Core；
* Open Attributes；
* Local Package Hint；
* 条件式批量Judge。

## Atomic Event层

* 基础实体、时间、财期、指标和数值Canonicalization；
* 规则＋embedding候选召回；
* Hard Cannot-Link；
* M2批量Mention-to-Cluster判定，复杂或高影响候选升级至M3；
* `SAME_EVENT`／`RELATED_NOT_SAME`／`UNRELATED`／`UNCERTAIN`，并由编排层执行`MERGE`／`CREATE_NEW`／`CREATE_AND_LINK`／`HOLD`；
* Atomic Event Registry；
* 批次级重复簇检查。

## Event Package层

* `BOUNDED`和`EPISODE`两类Package；
* 基于锚点、实体、时间和成员事件的候选召回；
* BOUNDED Package优先由M0分配，字段不足或冲突时使用M2；EPISODE Package默认使用M2；
* Atomic-to-Package输出`MEMBER`／`EXTERNAL_RELATED`／`NOT_RELATED`／`UNCERTAIN`，由编排层执行`ADD_TO_PACKAGE`／`CREATE_NEW_PACKAGE`／`LINK_EXTERNALLY`／`HOLD`；
* Package-to-Package输出`SAME_PACKAGE`／`DIFFERENT_PACKAGE`／`UNCERTAIN`并执行合并或保留；
* Event Package Registry；
* 基础事件关系图。

第一版暂不实现：

* 专用Cross-Encoder训练；
* 时间GNN；
* 自动Schema归纳；
* 全局约束聚类；
* 自动复杂拆簇；
* 完整事件知识图谱推理。

---

# 十三、最终主流程

```text
Source Message
      ↓
文本清洗、元数据和时间处理
      ↓
全文／段落级Dreamer
      ↓
全文级Grounder
- 文内原子化
- 局部指代解析
- 去语境化
- Universal Event Core
- Open Attributes
- Schema投影
- Local Package Hint
      ↓
条件式批量Judge
      ↓
Event Mention
      ↓
跨文本字段Canonicalization
      ↓
Atomic Event候选召回
      ↓
Hard Cannot-Link
      ↓
Mention-to-Cluster精排
      ↓
Atomic Event创建／合并／关联
      ↓
Atomic Event局部校正
      ↓
Event Package候选召回
      ↓
Atomic-to-Package精排
      ↓
Package创建／归入／外部关联
      ↓
Package-to-Package共指合并
      ↓
Event Package局部校正
      ↓
Atomic Event Registry
+
Event Package Registry
```

这套流程的核心原则是：

> **单文本阶段负责发现并完整表达事件；Atomic Event层负责严格去重和现实身份统一；Event Package层负责将不同原子事件组织为同一披露或事项。**

三层职责分离后，系统既能覆盖固定金融Schema以外的长尾事件，又能避免把同一财报中的不同指标、不同机构的目标价调整以及事件与市场反应错误合并。

---

# 十四、验收标准

开发完成必须同时满足以下条件：

1. **端到端可运行**：冷启动批处理和持续增量两条链路均能从 Source Message 稳定产出 Event Mention、Atomic Event 和 Event Package，并可在进程重启后继续使用既有 Registry。
2. **Schema严格一致**：字段、枚举、关系和模型分工必须与本文及《跨文档事件共指核心Schema与模型配置》一致；不得私自增加同义字段、旧枚举或隐藏的 fallback 语义。所有模型输出必须经过结构校验，非法输出不得写入正式 Registry。
3. **关键边界全部通过**：至少覆盖并通过以下固定测试：同一机构同一目标价的多来源报道合并；不同机构的目标价调整不合并；不同财期、不同指标、Actual 与 Guidance、GAAP 与 Non-GAAP 不合并；同一财报的不同 Atomic Event 进入同一 Package；不同财期财报不进入同一 Package；市场反应和分析师反应仅建立外部关系。上述高风险用例不得出现 False Merge。
4. **幂等与可追溯**：同一消息重复处理不得重复创建 Mention、Atomic Event 或 Package；任何合并、关联和冲突均可追溯到原始 message、evidence span 和模型／规则判定结果；更新不得覆盖或丢失来源特定 Claim。
5. **调用与性能受控**：候选召回和 Hard Cannot-Link 不得调用 LLM；普通文章按批量方式调用模型，不得退化为逐 Mention、逐候选请求；应记录每个节点的模型、耗时、token 和候选数量，并验证单条失败不会中断整批处理。
6. **测试完整**：为 Schema 校验、Canonicalization、Hard Cannot-Link、Atomic 归并、Package 归属、幂等和恢复分别提供单元测试，并提供覆盖完整冷启动与增量流程的 E2E 测试。所有自动化测试必须通过，关键路径不得以 mock 结果替代真实编排验证。
7. **交付报告清楚**：说明最终实现范围、数据表／模型变更、模型路由、阈值配置、测试样例及结果，并列出所有未实现项和已知风险。未达到以上任一项，不视为开发完成。

---

# 十五、后续开发步骤

1. **建立独立骨架、契约和数据基线**：新建不导入 `doxagent.*`、不注册现有工作流的 `src/cdecr` 包与独立 CLI，先按两份设计文档落地严格 Schema、枚举、配置和端口契约。真实语料只读接入 DoxAtlas Supabase `public.raw_media`，映射为 Source Message 并固化可复现样本；CDECR 的 Registry、版本和审计写入专用本地 SQLite。编码前先验证全部模型 ID、接口和结构化输出能力，尤其确认 `text-emmedding-v4` 是否为 `text-embedding-v4` 的笔误。
2. **完成单文档事件发现与标准化**：实现清洗／去重／长文分块、M2 Dreamer、M3 Grounder、条件式批量 M2 Judge，以及实体、时间、财期、指标和数值的 M0/M1 归一与 M2 兜底；一次性产出带原文证据的 Event Mention、三类金融投影和 Local Package Hint。所有模型调用使用 CDECR 自有适配器，强制 Schema 校验并记录模型、耗时、token 和错误，不复用 DoxAgent 运行时能力。
3. **完成增量 Atomic Event 与 Event Package 引擎**：依次实现多路候选召回、M0 Hard Cannot-Link、批量 Mention-to-Cluster 判定、确定性 Cluster 更新与局部校正，再实现 BOUNDED／EPISODE Package 召回、归属、外部关系、Package 合并和局部校正；补齐幂等键、版本化、冲突／HOLD 队列、原始证据与判定审计，保证冷启动、增量处理和进程重启共享同一主逻辑。
4. **用真实语料验收并固化交付**：从 `raw_media` 选择跨来源密集的股票与时间窗建立人工／M4 审查集，覆盖验收标准中的 False Merge 高风险边界；分层完成 Schema、规则、归并、Package、恢复与幂等单测，以及不以 mock 替代编排的冷启动／增量 E2E。最后校准阈值与调用预算，验证批处理、单条失败隔离和可追溯性，并输出实现范围、测试结果、已知风险和延后项。

---

# 十六、2026-07-21 v5 契约修订（优先于上文旧口径）

本节覆盖上文中“条件式 M2 Judge”、业务 confidence 字段及 Grounder 生成 Evidence
字符位置的旧设计；不新增 CandidateDisposition、QuantityRole、Evidence Catalog 等结构。

1. Grounder 输入将完整 Dreamer Candidate ID 映射为请求内 `c1/c2/...`，且不发送
   DreamCandidate confidence；Grounder 输出只引用短 ID，程序恢复完整 ID 后持久化。
2. Grounder/Judge 的 Evidence 只包含 `segment_id + text`。程序要求指定 Segment 内
   唯一精确匹配，再计算局部及原文全局左闭右开位置；M4 修订后仍不唯一则文档失败并审计。
3. Grounder Mention 新增 `source_claim`；Participant 仅输出 `surface + role`，实体 ID
   在归一阶段补充；Quantity 的 `metric_id` 必填；Local Package Hint 仅保留
   `anchor + relation_to_anchor`，Package Family 由 `event_family` 确定性推导。
4. 删除 Grounder 的 `extraction_confidence/needs_judge/judge_reasons`，全部 Grounder
   drafts 进入 M4 `qwen3.7-max` JSON Mode Judge，`reasoning.effort=none`；Judge 决定也
   不包含 confidence。
5. `source_claim` 进入最终 EventMention、SQLite payload、Atomic `source_claims` 汇总及
   JSON/Markdown 导出。单文档、Normalization、Atomic、Package、Membership、Relation
   的业务 confidence 字段全部删除；技术召回及归一候选的相似度 `score` 继续保留。
6. 原先依赖 confidence 阈值的下游动作改为明确分类与硬规则：只有唯一明确候选才执行
   MERGE/MEMBER/EXTERNAL_RELATED，多候选或 UNCERTAIN 进入 HOLD。Registry 升级到 v5，
   处理版本、Prompt 版本和幂等键同步升级，旧结果不得被新链路误复用。
