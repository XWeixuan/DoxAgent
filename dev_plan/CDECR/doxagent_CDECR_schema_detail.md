# 跨文档事件共指核心Schema与模型配置

## 一、设计判断标准

Schema是否值得加入，主要看四点：

1. **是否决定事件身份**
   缺少该字段，会不会把两个不同现实事件错误合并。

2. **是否影响投研结论**
   例如财期、实际值与指引、GAAP与Non-GAAP不同，会直接改变事件含义。

3. **是否能稳定抽取和归一**
   无法稳定定义、不同领域含义变化很大的字段，不适合做强Schema。

4. **是否可以延后到下游分析**
   利多利空、重要性、意外程度、可交易性等不决定事件身份，不应放入共指核心。

最终应遵循：

> LLM负责发现和理解事件；Schema负责保存必要结构；规则负责强约束；Embedding负责召回；精排模型负责处理语义歧义。

DiCoRe证明了“开放发现—结构收敛—验证”职责拆分的价值，但它解决的是零样本事件检测，并不意味着必须为每句话、每个候选部署独立Agent。SOEE证明了固定字段与动态属性结合的可行性，但其反思式多Agent流程会明显增加运行复杂度，因此本系统只保留“固定核心＋开放属性”，不在MVP引入多轮反思。

---

# 二、最终保留的Schema

系统需要六类核心对象：

```text
Source Message
Event Mention
Schema Projection
Atomic Event
Event Package
Event Relation / Membership
```

其中Source Message尽量复用现有消息系统，不重新设计复杂数据结构。

---

# 三、Source Message：只保留事件处理需要的输入

```json
{
  "message_id": "MSG-123",
  "source_type": "NEWS",
  "title": "...",
  "text": "...",
  "published_at": "2026-07-15T10:00:00Z",
  "source_name": "Reuters",
  "url": "...",
  "ticker_hints": ["NVDA"],
  "parent_message_id": null,
  "language": "en"
}
```

## 必要字段

* `message_id`
* `title`
* `text`
* `published_at`
* `source_name`
* `source_type`
* `ticker_hints`
* `url`
* 转发、引用或转载关系

来源类型用于：

* 判断是否存在明确Package锚点；
* 识别转载和二次报道；
* 解释信息来源差异；
* 决定抽取上下文。

它不直接决定事件是否共指。

---

# 四、Event Mention Schema

Event Mention是系统最重要的基础对象。Grounder输出、Judge确认后形成。

```json
{
  "mention_id": "",
  "message_id": "",
  "evidence_spans": [],
  "canonical_proposition": "...",
  "event_family": "ANALYST_ACTION",
  "predicate": "raise price target",
  "participants": [],
  "locations": [],
  "time": {},
  "assertion_state": "ACTUAL",
  "quantities": [],
  "open_attributes": [],
  "schema_projection": null,
  "local_package_hint": null,
  "extraction_confidence": 0.92
}
```

## 1. Canonical Proposition

自包含、去语境化的事件描述：

```text
Goldman Sachs raised its price target for Nvidia to USD 1,300.
```

必须保留，但不能替代原文证据。

它主要服务于：

* Embedding候选召回；
* Mention-to-Cluster精排；
* Cluster摘要；
* 人工审查。

---

## 2. Predicate

使用规范化的自然语言谓词：

```text
raise price target
report net income growth
suspend production
announce export restriction
approve acquisition
```

不要求维护完整谓词本体。

MVP只需要：

```json
{
  "raw": "bumped",
  "normalized": "raise price target"
}
```

---

## 3. Event Family

Event Family只用于粗分桶和候选召回，不决定最终Schema。

建议枚举：

```text
FINANCIAL_PERFORMANCE
GUIDANCE_EXPECTATION
ANALYST_ACTION
TRANSACTION_CAPITAL
COMMERCIAL_OPERATION
PRODUCTION_SUPPLY
REGULATORY_LEGAL_POLICY
GOVERNANCE_PERSONNEL
PRODUCT_SCIENCE
INCIDENT_GEOPOLITICAL
MARKET_MOVEMENT
OTHER
```

### 为什么保留粗Event Family

金融新闻中，同一公司同一天可能同时出现：

* 财报指标；
* 指引变化；
* 分析师调整；
* 股价变化；
* 监管消息。

只按ticker和时间召回会产生大量无效候选。

### 为什么不做更细的全局事件类型表

细分类型数量会快速膨胀，而且产业、政策和突发事件无法穷举。精确类型由可选Schema或开放谓词表达。

---

## 4. Participants

```json
[
  {
    "surface": "Goldman",
    "entity_id": "INSTITUTION_GOLDMAN_SACHS",
    "role": "ACTOR"
  },
  {
    "surface": "Nvidia",
    "entity_id": "COMPANY_NVIDIA",
    "role": "TARGET"
  }
]
```

通用角色枚举保持精简：

```text
ACTOR
SUBJECT
TARGET
COUNTERPARTY
AFFECTED
AUTHORITY
OTHER
```

具体角色如`issuer`、`acquirer`、`analyst_institution`放入Schema Projection，不扩大全局枚举。

---

## 5. Time

金融事件必须区分：

* 事件发生时间；
* 新闻发布时间；
* 财务或业务参考期间。

```json
{
  "event_start": "2026-07-15",
  "event_end": null,
  "precision": "DAY",
  "reference_period_id": null
}
```

时间精度枚举：

```text
TIMESTAMP
DAY
MONTH
QUARTER
YEAR
INTERVAL
UNKNOWN
```

`reference_period_id`用于：

```text
COMPANY_X_FY2026_Q2
CALENDAR_2026_Q2
FY2026
```

这是必要字段，因为：

> “公司今天发布Q2净利润”中的事件发布时间和指标所属期间不是同一个时间概念。

---

## 6. Assertion State

上一版同时使用`factuality`、`modality`、`polarity`，存在重叠。MVP合并为一个字段：

```text
ACTUAL
ONGOING
PLANNED
EXPECTED
RUMORED
DENIED
HYPOTHETICAL
UNKNOWN
```

它能够覆盖交易场景最重要的事实差异：

* 已经发生；
* 正在发生；
* 公司计划；
* 市场预期；
* 未确认传闻；
* 被否认；
* 假设性讨论。

不再单独维护：

* realis；
* modality；
* polarity；
* certainty。

模型置信度与事件事实状态必须分开：

```text
assertion_state = RUMORED
extraction_confidence = 0.98
```

表示模型高度确定原文表达的是一个传闻，而不是模型不确定。

---

## 7. Quantities

```json
[
  {
    "metric_id": "PRICE_TARGET",
    "value": 1300,
    "unit": "USD",
    "raw_text": "$1.3K"
  }
]
```

Universal Core只保留通用数值：

* metric_id可为空；
* value；
* unit；
* raw_text。

同比、环比、GAAP等细节放入金融Schema，而不是塞进Universal Core。

---

## 8. Open Attributes

```json
[
  {
    "key": "capacity_impact",
    "value": "40%",
    "evidence_span": "accounting for 40% of domestic capacity",
  }
]
```

Open Attributes属于具体Event Mention。

MVP只保留：

```text
key
value
evidence_span
```

暂不增加：

* key_raw与key_normalized双层结构；
* 复杂值类型系统；
* 自动Schema升级状态；
* 属性级版本历史。

这些可以在出现真实归一需求后再增加。

Open Attributes默认是**软匹配字段**，不能自动成为Hard Cannot-Link依据。

---

# 五、金融Schema Projection

Schema Projection是Event Mention上的可选增强，不是事件进入系统的前提。

```json
{
  "schema_type": "ANALYST_ACTION",
  "fields": {}
}
```

## MVP只建议实现三个Schema

### 1. Financial Metric

```json
{
  "schema_type": "FINANCIAL_METRIC",
  "fields": {
    "issuer_id": "COMPANY_C",
    "period_id": "COMPANY_C_FY2026_Q2",
    "metric_id": "NET_INCOME_GAAP",
    "value": 1000000000,
    "unit": "USD",
    "comparison_basis": "YOY",
    "change_value": 29,
    "accounting_basis": "GAAP"
  }
}
```

必要枚举：

`comparison_basis`：

```text
ABSOLUTE
YOY
QOQ
YTD
VS_CONSENSUS
VS_GUIDANCE
UNKNOWN
```

`accounting_basis`：

```text
GAAP
NON_GAAP
STATUTORY
UNKNOWN
```

### 为什么必要

以下事件不能错误合并：

```text
Q2净利润增长29%
Q2调整后净利润增长29%
Q2净利润环比增长29%
Q3净利润增长29%
```

---

### 2. Guidance

```json
{
  "schema_type": "GUIDANCE",
  "fields": {
    "issuer_id": "COMPANY_C",
    "period_id": "COMPANY_C_FY2026",
    "metric_id": "CAPEX",
    "action": "RAISE",
    "value_low": 12,
    "value_high": 14,
    "unit": "USD_BILLION"
  }
}
```

`action`枚举：

```text
INITIATE
RAISE
LOWER
REITERATE
NARROW
WIDEN
WITHDRAW
UNKNOWN
```

### 为什么与Financial Metric分开

```text
Q2资本支出增长24%
公司上调全年资本支出指引24%
```

一个是已经发生的Actual，一个是面向未来的Guidance，不能依靠单一`actual_or_guidance`字段混在同一个Schema里。

---

### 3. Analyst Action

```json
{
  "schema_type": "ANALYST_ACTION",
  "fields": {
    "institution_id": "GOLDMAN_SACHS",
    "analyst_id": null,
    "company_id": "COMPANY_A",
    "action": "RAISE_TARGET",
    "rating_from": null,
    "rating_to": "BUY",
    "target_from": 1100,
    "target_to": 1300,
    "currency": "USD",
    "report_date": "2026-07-15"
  }
}
```

`action`枚举：

```text
UPGRADE
DOWNGRADE
INITIATE
REITERATE
MAINTAIN
RAISE_TARGET
LOWER_TARGET
SUSPEND_COVERAGE
RESUME_COVERAGE
UNKNOWN
```

### 为什么必要

分析师消息语言高度模板化，只使用Embedding非常容易把不同机构、不同报告或不同操作合并。

---

# 六、Atomic Event Schema

```json
{
  "event_id": "E-123",
  "canonical_proposition": "...",
  "event_family": "ANALYST_ACTION",
  "identity_profile": {},
  "time": {},
  "assertion_state": "ACTUAL",
  "mention_ids": [],
  "representative_mention_ids": [],
  "consensus_claims": {},
  "conflict_flags": [],
  "cluster_confidence": 0.94,
  "version": 3
}
```

## 1. Identity Profile

这是Atomic Event最关键的字段。

对于有Schema的事件，由确定性规则生成身份字段。

### Financial Metric

```text
issuer
period
metric
comparison_basis
accounting_basis
```

### Guidance

```text
issuer
period
metric
guidance action
```

### Analyst Action

```text
institution
company
action category
report date或report ID
```

目标价具体数值不直接作为绝对Hard Identity字段，因为来源可能：

* 抄错；
* 使用不同币种；
* 未做拆股调整；
* 引用旧版本；
* 省略旧目标价。

它属于高权重Claim Conflict，而不是一看到差异就强制拆分。

## 2. 开放事件的Identity Profile

MVP不让LLM自由决定任意Hard Identity字段。

默认使用：

```text
normalized predicate
principal participants
event time
reference period
location/facility/asset
assertion state
```

Open Attributes只参与软匹配。

这样可以避免模型把`capacity_impact`、`reason`等偶然字段错误提升为事件身份。

当某种开放事件高频出现后，再为它开发Schema Adapter。

---

# 七、Atomic Event核心判断维度

## Hard Dimensions

出现明确冲突时禁止合并：

1. 核心主体或交易对手；
2. 核心谓词或事件行为；
3. 发生时间或参考期间；
4. Assertion State；
5. 明确生命周期阶段；
6. Schema身份字段；
7. 关键地点、工厂或资产。

## Soft Dimensions

用于精排打分，但不单独决定共指：

1. Canonical Proposition语义相似；
2. Open Attributes重合；
3. 非核心参与者；
4. 数值和Claim相似度；
5. 来源转载或引用关系；
6. Local Package Hint；
7. 标题与上下文相似度。

## 不进入Atomic共指判断

* 股票利多或利空；
* 预期差；
* 事件重要性；
* 交易机会；
* 情绪；
* 来源可靠度；
* 股价反应。

这些字段即使高度一致，也不能证明是同一现实事件。

---

# 八、Atomic共指判定枚举

模型语义输出：

```text
SAME_EVENT
RELATED_NOT_SAME
UNRELATED
UNCERTAIN
```

另外独立输出：

```json
{
  "claim_conflict": true,
  "identity_conflicts": [],
  "confidence": 0.87
}
```

不把`CONFLICTING_CLAIM`作为独立共指关系，因为：

> 同一个事件也可能包含不同来源的冲突Claim。

编排层根据关系和阈值产生动作：

```text
MERGE
CREATE_NEW
CREATE_AND_LINK
HOLD
```

关系与动作分开，可以避免模型同时承担业务编排决策。

顺序式跨文档共指研究支持使用持续更新的Cluster Representation进行Mention-to-Cluster判断，而不是每次对全部Mention重新聚类。

---

# 九、Event Package Schema

```json
{
  "package_id": "P-123",
  "package_kind": "BOUNDED",
  "package_family": "EARNINGS_DISCLOSURE",
  "canonical_title": "...",
  "anchor_entities": [],
  "anchor_artifact_id": null,
  "anchor_period_id": null,
  "time_range": {},
  "lifecycle_state": null,
  "member_event_ids": [],
  "canonical_summary": "",
  "status": "OPEN",
  "confidence": 0.93,
  "version": 2
}
```

## 1. Package Kind

```text
BOUNDED
EPISODE
```

### Bounded

由明确文件或披露行为限定：

* 财报；
* 监管文件；
* 公司公告；
* 投资者日；
* 分析师报告。

### Episode

持续演化的事项：

* 并购；
* 监管调查；
* 政策制定和实施；
* 工厂事故及复产；
* 产品危机。

这项区分必须保留，因为二者候选召回和身份判断完全不同。

---

## 2. Package Family

建议保持粗粒度：

```text
EARNINGS_DISCLOSURE
COMPANY_DISCLOSURE
ANALYST_REPORT
TRANSACTION
REGULATORY_LEGAL
POLICY
OPERATIONAL_INCIDENT
PRODUCT_SCIENCE
OTHER
```

不要为每种政策、事故、产品事件继续细分Package枚举。

---

## 3. Package Status

```text
OPEN
CLOSED
UNKNOWN
```

对Episode Package较重要：

* 正在审查的并购为OPEN；
* 已完成或终止后为CLOSED；
* 状态不明为UNKNOWN。

Bounded Package形成后通常直接为CLOSED，但仍可添加更正或补充事件。

---

# 十、Package成员关系

Atomic Event与Package之间只保留六种成员角色：

```text
DISCLOSED_IN
COMPONENT_OF
STAGE_OF
UPDATE_OF
CORRECTION_OF
IMPLEMENTATION_OF
```

解释：

* `DISCLOSED_IN`：某财务指标或指引在财报中披露；
* `COMPONENT_OF`：某子事件构成更大事项；
* `STAGE_OF`：并购签约、审批、交割等阶段；
* `UPDATE_OF`：同一事项的新进展；
* `CORRECTION_OF`：更正此前披露；
* `IMPLEMENTATION_OF`：政策或决定的落地执行。

无需增加：

```text
reported_metric
announced_guidance
regulatory_stage
restart_stage
```

这些细节可以放在自由文本`role_detail`中，不扩大枚举。

---

# 十一、Package外部关系

以下事件通常不进入Package，只建立外部边：

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
StockC财报Package
← MARKET_REACTION_TO —
StockC盘后上涨8%
```

事件关系研究通常分别建模共指、时间、因果和子事件关系，而不是把所有相关事件都视为共指或同一簇。

MVP不需要完整实现Allen时间关系：

```text
BEFORE
AFTER
OVERLAP
CONTAINS
BEGINS_ON
ENDS_ON
```

只需在成员事件中保存时间，按时间排序即可。完整时间关系图会明显增加抽取、校验和一致性维护成本。

---

# 十二、Package核心判断维度

## Bounded Package

按优先级判断：

1. 锚点文档或公告ID；
2. 主体；
3. Package Family；
4. 财期或报告期；
5. 披露日期；
6. 是否共享已共指Atomic Event；
7. 成员事件兼容性；
8. Package摘要语义。

例如财报Package身份主要由：

```text
issuer + fiscal period + earnings disclosure
```

决定。

## Episode Package

按优先级判断：

1. 核心实体组合；
2. 事项对象；
3. 事件类型；
4. 时间连续性；
5. 地点、工厂、产品或资产；
6. 生命周期阶段兼容性；
7. 是否共享Atomic Event；
8. 已有成员事件关系；
9. Package摘要语义。

## Package Hard Conflict

* 财报主体或财期不同；
* 并购双方或目标资产不同；
* 政策发布机关、政策工具和适用对象均不相同；
* 事故地点、设施和时间明确不相容；
* 产品或临床项目不同；
* 两个Package的核心事项明确排他。

---

# 十三、Package判断枚举

Atomic-to-Package：

```text
MEMBER
EXTERNAL_RELATED
NOT_RELATED
UNCERTAIN
```

Package-to-Package：

```text
SAME_PACKAGE
DIFFERENT_PACKAGE
UNCERTAIN
```

编排层根据结果决定：

```text
ADD_TO_PACKAGE
CREATE_NEW_PACKAGE
LINK_EXTERNALLY
MERGE_PACKAGES
HOLD
```

不需要在模型输出中增加`SUBPACKAGE_OF`。当前系统只做到单层Event Package，引入Package嵌套会显著增加结构复杂度。

---

# 十四、明确删除或延后的内容

## 从Universal Core删除

* 单独的factuality；
* 单独的modality；
* polarity；
* sentiment；
* direction；
* importance；
* tradability；
* source reliability。

## MVP不做

* 自动Schema归纳；
* SOEE多轮反思；
* LLM自由生成Hard Identity维度；
* 完整时间关系图；
* Event Package嵌套；
* 全局图社区发现；
* 自动复杂拆簇；
* 对全部Open Attribute进行统一本体化；
* 每次更新都重新生成全部Cluster摘要。

这样可以把核心复杂度集中在：

```text
事件是否抽对
Atomic Event是否错误合并
Package是否过度扩张
```

---

# 十五、模型层级定义

建议把系统使用的能力分成四层。

## M0：确定性规则与普通程序

包括：

* 正则；
* 时间解析；
* ticker映射；
* 单位换算；
* 字典；
* URL和转载指纹；
* Hard Cannot-Link；
* 阈值和编排规则。

成本最低、结果最稳定。

## M1：Embedding与小型判别模型

模型使用：text-emmedding-v4

用于大规模候选召回和初步打分。

## M2：低成本、低延迟LLM

模型使用：deepseek-v4-flash

要求：

* 中英文金融文本理解；
* 稳定JSON输出；
* 足够长上下文；
* 能进行基本事件拆分与关系判断；
* 支持批量输入。

承担系统绝大多数在线LLM工作。

## M3：强推理LLM

模型使用：qwen3.7-plus

只用于：

* 复杂长新闻；
* 低置信度事件；
* Atomic Event疑难共指；
* Episode Package判断；
* 高价值消息；
* Cluster合并或拆分审查。

不进入所有消息的默认路径。

## M4：离线高质量审查

模型使用：qwen3.7-max

可以是最强模型结合人工，用于：

* 构建评估集；
* 检查系统错误；
* 归纳新Schema；
* 生成训练数据；
* 优化Prompt；
* 训练M1判别模型。

不承担实时消息处理。

---

# 十六、所有模型使用场景

## 1. 文本清洗和重复消息识别

**模型：M0＋M1**

使用：

* HTML清洗；
* 哈希；
* MinHash；
* 标题Embedding；
* URL和转载链。

不使用LLM。

原因：LLM成本高且没有明显价值。

---

## 2. Dreamer开放事件发现

**默认：M2**

输入：

```text
标题
正文或段落块
来源元数据
ticker提示
```

输出全部候选事件和证据Span。

要求偏高召回，允许产生少量多余候选。

复杂长文或产业政策文档可以升级至M3。

Dreamer和Grounder在逻辑上应当分开，因为开放发现与Schema约束同时放在一个Prompt中容易降低长尾事件召回。DiCoRe的核心贡献正是把发散发现与收敛对齐解耦。

---

## 3. Grounder文内收敛

**默认：M3**

这是单文档阶段能力要求最高的节点，负责：

* 原子化；
* 文内去重；
* 局部指代；
* Canonical Proposition；
* Universal Core；
* Open Attributes；
* Schema Projection；
* Local Package Hint。

LLM生成完整事件表示、再由专用小模型处理大规模共指，是近年跨文档事件共指的重要组合方向。

---

## 4. Judge

**默认：条件式M2**

Judge不逐事件单独调用，而是批量审核整篇文章的Grounder结果。

触发条件：

* 候选事件过多；
* Grounder低置信度；
* 存在SPLIT或MERGE_AS_ATTRIBUTE争议；
* 数值或状态冲突；
* 高价值新闻；
* 复杂政策、事故或交易进程。

Judge最好使用不同Prompt；预算允许时可使用不同模型，以降低Grounder和Judge产生相同系统性错误的概率。

---

## 5. 实体Canonicalization

**默认：M0＋M1，M2兜底**

公司和机构：

```text
ticker字典
别名表
知识库链接
Embedding检索
```

时间、财期、数值和单位：

```text
规则解析
公司财年日历
单位转换
```

只有以下情况调用M2：

* 同名实体；
* 母子公司歧义；
* 代称跨段落解析；
* 指标口径难以判断；
* 财期表达模糊。

不应对每个实体都调用LLM。

---

## 6. 指标Canonicalization

**默认：M0字典＋M1检索，M2兜底**

例如：

```text
net income
adjusted net income
net income attributable to shareholders
```

先通过金融指标词典和Embedding匹配。

无法确定时交给M2，并允许输出：

```text
UNKNOWN_METRIC
```

不能为了提高归一率强制映射到最接近指标。

---

## 7. Atomic Event候选召回

**模型：M0＋M1**

采用：

* 实体倒排；
* 时间窗口；
* Event Family；
* Schema字段；
* Canonical Proposition Embedding；
* 来源指纹。

不使用LLM。

跨文档共指候选数会快速增长，研究中也普遍需要检索、文档分组或顺序式聚类来避免全量平方级比较。

---

## 8. Hard Cannot-Link

**模型：M0**

全部由规则执行：

```text
财期冲突
机构冲突
明确主体冲突
actual与guidance冲突
事件阶段冲突
```

LLM可以提供字段，但不能推翻明确Hard Constraint。

---

## 9. Mention-to-Cluster精排

### MVP

**默认：M2批量判断**

每篇文章将全部Mention及各自Top 3—5候选Atomic Event组成一次请求。

输出：

```text
SAME_EVENT
RELATED_NOT_SAME
UNRELATED
UNCERTAIN
```

复杂或高影响候选升级至M3。

### 后续

积累足够LLM判定和人工修正数据后：

```text
M1 Cross-Encoder初判
M2处理灰区
M3处理极少数疑难案例
```

顺序式模型使用历史Cluster Representation判断新Mention归属，可以避免把每次处理退化成全量Mention Pair比较。

---

## 10. Atomic Event Profile更新

**默认：M0**

确定性合并：

* mention_ids；
* source claims；
* 新增属性；
* 冲突记录；
* 时间范围。

只有在以下情况调用M2：

* Canonical Proposition已明显过时；
* 新Mention补充了关键主体或时间；
* Cluster摘要需要重新生成。

不能每加入一个Mention都调用LLM重写Cluster。

---

## 11. Atomic Event局部校正

**默认：M0＋M1，疑难使用M3**

先通过规则和Embedding发现：

* 高相似重复簇；
* 一个Mention强匹配多个簇；
* 簇内Hard Conflict；
* 可能的链式误合并。

明显重复直接规则合并。

复杂merge/split候选交给M3批量审查。

---

## 12. Local Package Hint

**由Grounder顺带输出**

不增加独立模型调用。

只生成：

```text
候选Package锚点
候选Package Family
与锚点的局部关系
```

它是召回提示，不是最终Package归属。

---

## 13. Bounded Package Assignment

**优先使用M0**

例如财报Package：

```text
issuer + fiscal period + earnings disclosure
```

分析师报告Package：

```text
institution + company + report date/report ID
```

监管文件：

```text
authority/issuer + filing ID
```

字段充分时不需要LLM。

字段缺失或冲突时使用M2。

---

## 14. Episode Package Assignment

**默认：M2**

例如：

* 并购事项；
* 监管调查；
* 事故与复产；
* 政策制定与落地。

输入：

```text
新Atomic Event
候选Package Profile
成员事件
生命周期信息
代表原文
```

输出：

```text
MEMBER
EXTERNAL_RELATED
NOT_RELATED
UNCERTAIN
```

Episode Package比Atomic共指更依赖多事件整体理解，因此强模型使用比例可以高于Bounded Package。

---

## 15. Package-to-Package合并

### Bounded Package

**M0为主**

锚点一致即可高置信合并。

### Episode Package

**M2或M3**

比较：

* 核心参与者；
* 事项对象；
* 时间范围；
* 生命周期；
* 共享Atomic Event；
* 成员事件兼容性。

不能只比较Package摘要Embedding。

---

## 16. Package Summary更新

**默认：M2，按变化触发**

只有以下情况重新生成：

* 新增关键阶段；
* Package状态发生变化；
* 出现更正或反转；
* 成员事件数量达到阈值；
* 用户请求读取。

普通成员增加不触发摘要重写。

---

## 17. 新Schema归纳

**M4离线执行，不进入MVP在线链路**

定期分析：

* 高频Open Attribute；
* 高频OTHER事件；
* 共指错误集；
* 大量需要LLM判断的事件族。

只有当某类事件：

```text
高频
高价值
错误率高
身份字段稳定
```

才升级为正式Schema Adapter。

多文档事件抽取的前沿工作也倾向于让LLM承担复杂组合推理，让较小模型生成稳定结构，而不是让一个大模型独立完成所有步骤。

---

# 十七、MVP实际模型调用路径

## 普通新闻文章

```text
1次M2 Dreamer
1次M2/M3 Grounder
0或1次M2 Judge
1次M2批量Atomic共指
0或1次M2 Package判断
```

通常为：

```text
3—4次LLM调用／文章
```

不是每个事件、每个候选分别调用。

---

# 18. 2026-07-21 v5 Schema 覆盖说明

本节为当前实现契约，优先于前文含 confidence、Grounder 字符位置和条件式 Judge 的示例。

- Grounder `EvidenceText = {segment_id, text}`；`start_char/end_char` 仅存在于程序计算后的
  `EvidenceSpan`，不属于 Grounder/Judge 模型输出。
- Grounder `ParticipantDraft = {surface, role}`；正式 Participant 在归一后增加
  `entity_id`。Grounder Quantity 的 `metric_id` 为必填小写 snake_case。
- Grounder Mention 增加 `source_claim: string | null`；`predicate.normalized` 为小写
  snake_case；Local Package Hint 仅为 `{anchor, relation_to_anchor}`。
- Grounder 输出为 `{drafts, issue_flags}`，每个 draft 只有
  `{source_candidate_ids, mention}`；短 ID 必须匹配 `^c[1-9][0-9]*$`。
- DreamCandidate、Grounder Mention、JudgeDecision、EventMention、NormalizationDecision、
  AtomicEvent、EventPackage、Membership 和两类 Relation 均不再包含 confidence。
- 每个 Grounder draft 必须由 M4 Judge 返回一次明确 action；下游以枚举分类、硬规则和
  `UNCERTAIN/HOLD` 处理歧义，不以业务 confidence 阈值放行。
- 技术性的 MinHash、Embedding 相似度、召回排序和 NormalizationCandidate `score` 不属于
  业务 confidence，仍用于候选生成，不能越过硬冲突或替代明确决策。
