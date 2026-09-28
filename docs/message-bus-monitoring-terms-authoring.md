# Message Bus 监测词编写规则

本文面向人工维护者和未来的监测配置 Agent，描述**当前代码可提交的** ticker 级 `TickerMonitoringTerms`。一份配置只绑定一个 ticker；提交一次后，被该 ticker 订阅的所有 `by_search`、`by_distribution` 入口按各自模式使用同一版本。提交监测词**不会**自动创建消息源订阅；`by_ticker` 入口也不使用这些词筛选。

## 1. 先确定监测边界

编写前明确：ticker 对应公司/业务、需要召回的产业链事件、竞争者事件在什么条件下相关、明确不相关的同名词或消费品内容。L1 追求搜索页的宽召回，L2 追求正文后的可解释筛选，`definition` 供 Jev 作实验性补充判断。不要把 L2 的长词表或复杂正则塞进 L1，也不要把 Jev 当成唯一分发路径。

入口的有效语言来自 `SourceDefinition.content_language`（可由站点默认语言在注册时解析），不是 Browser Profile 的 locale。目前提交校验要求 `en`、`ko`、`zh-Hant`：每个 L1 concept 都要有这三种表达，L2 也要有这三种规则。入口后来新增语言时，以 `terms validate` 当时返回的语言集合为准。

## 2. 可提交的 YAML 结构

下面是**格式示例，不是经审核的 MU 生产监测词**。实际表达和相关性定义必须由维护者按 ticker 审核。

```yaml
ticker: MU
expected_revision: 0
l1_concepts:
  - concept_id: company
    expressions:
      en: Micron
      ko: 마이크론
      zh-Hant: 美光
  - concept_id: memory
    expressions:
      en: memory chips
      ko: 메모리 반도체
      zh-Hant: 記憶體晶片
  - concept_id: hbm
    expressions:
      en: HBM
      ko: HBM
      zh-Hant: HBM
l2:
  en:
    groups:
      - id: company
        any:
          - {literal: Micron, whole_word: true}
      - id: memory_industry
        any: [{literal: DRAM}, {literal: HBM}, {literal: NAND}]
        all:
          - {regex: '(?i)\b(supply|pricing|capacity|capex)\b'}
        none:
          - {literal: smartphone review}
  ko:
    groups:
      - id: company
        any: [{literal: 마이크론}]
      - id: memory_industry
        any: [{literal: 디램}, {literal: HBM}, {literal: 낸드}]
        all: [{regex: '공급|가격|생산능력|설비투자'}]
  zh-Hant:
    groups:
      - id: company
        any: [{literal: 美光}]
      - id: memory_industry
        any: [{literal: DRAM}, {literal: HBM}, {literal: NAND}]
        all: [{regex: '供需|價格|產能|資本支出'}]
definition:
  relevant: >-
    News about Micron or memory-chip supply, pricing, technology, production,
    capital spending, or competitor actions with a material impact on MU.
  irrelevant: >-
    Consumer-device reviews, unrelated products using similar names,
    and stock-price lists without a substantive business event.
```

### L1：搜索页召回

- `l1_concepts` 必须有 **1–3** 个条目，`concept_id` 在同一配置内唯一。每个 concept 的 `expressions` 是语言到**单个搜索表达**的映射，不能用 `OR` 或 `|` 在一个表达里绕过限额；短语可含空格。
- `by_search` 入口按自己的 `search_policy` 将同一组概念渲染为多个独立查询（`separate`），或一个括号内 `A OR B OR C` 的查询（`or`）。程序负责引用含空格的短语、生成查询键和冻结 terms revision。L1 不用于 `by_distribution` 的正文筛选。
- 选择可由搜索引擎实际召回的公司/产品/行业称谓，不堆叠长句。过宽的普通词会增加抓取量；过窄的公司全称可能漏掉产业事件。先用 `terms validate` 返回的 `search_plans` 核对最终查询。

### L2：补全正文后的确定性判定

- `l2.<language>.groups` 是规则组列表，**组间 OR**。一个组内：`any` 至少命中一项（如果配置了）、`all` 全部命中、`none` 全部不命中，组才成立。每组至少有 `any` 或 `all` 的正向条件；`none` 只否决本组。
- 每个 `L2Term` 必须二选一：`literal` 或 `regex`。`field` 可为 `all`（默认，标题+摘要+正文）、`title`、`summary`、`body`；`case_sensitive` 默认 `false`，`whole_word` 默认 `false`，后者只应用于 `literal`。复杂正则须避免灾难性回溯；单条匹配有约 20 ms 超时，错误/超时按该条不命中处理。
- 程序对文本做 Unicode NFC 与空白归一。对一个分发入口使用**入口语言规则与英文规则的并集**，任一语言的任一组命中即相关。韩文/繁中不要依赖英文 `\b`；竞争者或宽行业词宜配合 `all` 的业务事件限定，减少误投。
- L2 没有三词上限，但整份配置序列化后不能超过 64 KiB。它只用于 `by_distribution`，不反向改变搜索页召回。

### `definition`：给 Jev 的相关/不相关边界

- 当前模型只接受一组非空字符串 `definition.relevant` 与 `definition.irrelevant`。应分别写清**哪些业务事件算相关**与**哪些相似但不相关**，避免“凡是半导体新闻都相关”之类过宽定义。可用一种能覆盖各入口语言的简洁表述；当前 schema **不支持** `definition.en/ko/zh-Hant` 三套独立版本。
- 这两个字符串直接进入每个 ticker 的 Jev 问题说明，不是 L1 查询词，也不作为正则模式；更新 definition 与 L1/L2 一样生成一个新的 ticker terms revision。
- Jev 是可关闭的实验轨；规则命中照常分发，规则未命中时只有 Jev 返回分数 `>= 0.5` 才补充分发。Jev 禁用、超时或两轮失败不会阻断正则命中。

## 3. 提交、预览与维护

```bash
python -m doxagent.message_bus_v2.cli terms validate --file monitoring/MU.yaml
python -m doxagent.message_bus_v2.cli terms apply --file monitoring/MU.yaml --actor user
python -m doxagent.message_bus_v2.cli terms show --ticker MU
python -m doxagent.message_bus_v2.cli terms history --ticker MU
python -m doxagent.message_bus_v2.cli terms preview --ticker MU
python -m doxagent.message_bus_v2.cli terms test --ticker MU --source ctee_semiconductor --article sample-article.json
```

`expected_revision` 是乐观并发条件：首次为 `0`，后续必须填写当前 revision，避免人工与 Agent 互相覆盖。`validate` 只校验并预览；`apply` 才原子写入不可变 revision 历史。`terms test` 的文章文件须是 `RawMessageInput` JSON，用于检查 L2 命中，不会调用 Jev。Agent 应调用同一提交服务，不直接修改 SQLite，也不要为每个消息源复制一份监测词。

分发仅面向已订阅该入口、处于可运行状态的 ticker；每批任务冻结 roster 和 terms revision，后续改词不改写已开始的批次。新建 `by_search` 订阅需要完整监测词，旧订阅可暂用历史搜索词兼容路径。`by_distribution` 没有有效订阅时不抓取，也不会因为“来源已注册”自动产生 ticker 消息；实际发布时间仍需通过现有实时/闭市 sweep 准入。

## 4. 当前 Jev 上下文的准确边界

调用条件是入口 `distribution_policy.jev_enabled=true`，且全局 `DOXAGENT_MESSAGE_BUS_JEV_ENABLED=true` 并配置 OpenRouter key。请求发送到 OpenRouter Decisions API，模型固定为 `typesafe/jev-1.13`。一篇文章作为 `state`，同批已订阅的 ticker 各有一个独立的 `noul` 问题；单请求最多 16 个 ticker。`state` 只有 `title`、`summary`、`body`、`source`、`language`；不发送 URL、发布时间、HTML、L1/L2 规则、其他文章或历史消息。每个问题的 `instructions` 由 ticker ID、`relevant`、`irrelevant` 三段拼接。

正文不超过总输入预检 64,000 字符时，常规文章只用一个 state；有正文则按段落分成最多四个、每段最多 16,000 字符的 state。任一分段分数达到 `0.5` 即相关。缺失的 ticker/分段答案至多补试一轮，单次请求上限 15 秒、文章总预算 60 秒；过大输入标记 `JEV_INPUT_LIMIT`，不会悄悄截断。当前生产 `DOXAGENT_MESSAGE_BUS_JEV_ENABLED=false`，因此现在**没有实际 Jev 请求**，仅运行 L2 规则轨。
