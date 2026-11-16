# W3生产上下文注入全貌与真实输入

2026-11-15。只读核验生产容器、Runtime冻结记录及原生thread；本轮没有修改生产配置、模型、Prompt或代码。

## 先看整体

W3不是每条消息都接收一份相互独立的“大Prompt”。当前是公共模板线程先初始化AGENTS.md与唯一Skill；Case继承模板，先执行Trade，再在同一Case线程上续接Operations。Agent实际可见上下文由四层构成：

1. 模板继承的公共行为指令与SDK工具说明。
2. Trade本轮user JSON：消息、W1/W2、相关Policy、状态、近期交易历史和时间语义。
3. Operations追加user JSON：已提交方向、执行处置、刷新后的WATCH/PIN、合法Event引用范围；同时继承Trade已有消息、工具结果和研究过程。
4. 持有context_ref后按需检索的冻结D1/D2/Policy/Event等资料，以及另行获取的行情/实时Web结果。

因此，“未在Operations新增input中出现”不等于Agent看不到；“已经绑定到研究工具”也不等于全文已经进入模型输入。物化到工作目录的文件、后端冻结manifest、真正发送给模型的user prompt要分开理解。

## 核验版本与证据边界

生产Scheduler镜像：`sha256:090c503631399d46c49ae7604fe47f48390109417f6ef8b68689fddb138f6963`，启动UTC11/12 21:59:30。

生产Worker镜像：`sha256:e8dcf289ec9d965f27a82844b720496c84318fe811945fb7d8034b3693a63224`。

本地当前HEAD为741043666，三个上下文相关文件与生产SHA256均不一致；本文优先采用真实原生输入与生产冻结资产，而不是把本地HEAD当作已上线版本。生产文件指纹：

| 文件 | SHA256 |
| --- | --- |
| w3.py | a3e9cb0c28da611fefeb6c6fa4fa2e8e18db5dcfbc3ddeceee7703aabd1775c2 |
| w3_context.py | 8ecae9e247c783a4b6d5624f11605e4642a498ca10de05fe7c76b28de535da02 |
| w3_execution.py | f5bf1bfcd042bfdec8b1b3e3b6eb794c30109fdce907c3c32957d46bef911f22 |

样本：NBIS `case_562cbe83867e4d6a9c2593788f63bb06`，原thread `01a1aea0-5319-78e2-b2b1-8055980a1c95`；标题为“NBIS Q3 Earnings Call Highlights Expansion Plans and Funding - The Globe and Mail”。W2明确判定policy_hit=false、policy_related=true，关联3条Policy。消息总线时点为2026-11-14T04:20:14.625556+00:00。原线程可能包含其他Case；本次用两阶段各自Worker Job起止区间定位原始user消息，避免混入其他Case。所载新闻、历史消息和资产观点是业务输入，不代表本报告独立核实其事实真伪。


本次按要求替换为真实RELATED Case；资产与生产版本证据沿用已核验快照，未发布改动。以下长度均为该样本，不能代表所有Case的典型值或上限：字符串计原文字符，复合对象计紧凑JSON字符（不含缩进，包含字段名/标点），均不是token。原始user全文长度与逐字段紧凑JSON不是同一序列化口径，不能直接逐项相加。示意例会明确标注，完整真实输入仍见附录。

## 第一层：模板、线程继承与SDK环境

| 内容 | 如何给Agent | 含义/边界 |
| --- | --- | --- |
| w3/AGENTS.md | 模板公共指令，Case继承 | W3身份、研究信任、业务判断、价格与能力边界、阶段交付 |
| w3/skills/skill.md | 模板初始化时加载唯一Skill，Case继承 | Trade研究/方向与Operations的Novelty、Fact、PIN/WATCH方法；不是每Case再贴全文 |
| 模板ready任务 | 模板初始化user消息 | 只初始化公共资产并返回ready，不分析具体消息 |
| Case Trade过程 | Case thread原生对话历史 | Operations继承Trade输入、Agent输出和已调用工具的结果，不是只收到Trade四个结论字段 |
| 输出JSON Schema | Worker/SDK原生结构化输出约束 | 与user中的output_fields不同：后者只有字段名，完整约束由SDK和本地校验实施 |
| 工具声明与能力约束 | SDK配置/MCP描述 | w3_research研究、行情等工具及Web能力；工具说明本身也是上下文 |
| 时间提醒/SDK事件 | 如启用，相应运行层注入 | 不应与业务input.cutoff_at混同；本次两份user JSON未含单独时间提醒字段，不声称导出了供应商隐藏系统Prompt |

样本冻结AGENTS为2606字符，SHA256 `cca5cfe5d58ef63819a39ff29dfd91ab4262469f986a0165ce4f598f4fc5dcec`；Skill为14105字符，SHA256 `82fff436598a6f63d841a6b4e12344fd17541fc4c643bc72191c991e050c0a23`。冻结布局agents-skill.v1，入口w3-entry-v5，业务契约persistent-runtime.w3.v5。周末sweep附加指令属于特定sweep任务，不是普通Case默认第三份Skill。

### 看起来是什么样：公共指令与Schema

AGENTS不是一个JSON标签，而是一份约2.6千字符的中文Markdown；本例章节包括“身份与业务贡献”“核心心智：市场正在重估什么”“研究信任与持续运行”“时效、阶段与交付”。Skill约1.41万字符，更像完整执行手册：分Trade研究、方向、机会核验、立即提交以及Operations事实归档/PIN/WATCH等章节。两者合计16,711字符，但不是每Case都重新内联这么一段。

模板ready任务的**结构示意**如下，省略版本和其他外壳；不是从本例Case反推模板原始全文：

```json
{"phase":"w3_template","instruction":"只初始化共享AGENTS和唯一Skill，不研判消息；返回ready。"}
```

输出Schema是带properties、required、类型/枚举及嵌套约束的JSON文档；Trade user只给4个字段名，不会在此重贴整份Schema。示意：`{"type":"object","properties":{"decision":{"enum":["LONG","SHORT","NO_TRADE"]}}}`，此示意省略其他必需字段与约束，不能直接作为完整Schema使用。完整Schema长度未在本次样本中单独测量。

Operations继承的历史并非一句“已完成Trade”，可能包括本例约11.43万字符的Trade输入、Trade结论和之前的工具结果；有效送入模型的历史还会受SDK上下文管理/压缩影响，不能把磁盘原thread大小当作每次请求token量。工具声明同样是多行参数说明；例如read接受context_ref、objects、cursor、max_bytes，Agent需理解这些参数后才能读取资料。隐藏系统指令和时间提醒的实际总长本轮未测量。

## 第二层：两个阶段共同的user外壳

| 字段 | 解释 | 本例形状、内容与长度 |
| --- | --- | --- |
| phase | trade_decision或post_decision_operations，规定本轮职责 | `trade_decision`；字符串14字符 |
| input | 本阶段业务载荷，具体如下 | 对象：`ticker, cutoff_at, trading_context, case_type, additional_tags, message, w1, w2, policies, watch, pins, recent_trade_intents, contexts, alignment`；JSON 112536字符 |
| entry_version | 输入入口布局版本，本例w3-entry-v5 | `w3-entry-v5`；字符串11字符 |
| contract_version | W3业务输出契约版本，本例persistent-runtime.w3.v5 | `persistent-runtime.w3.v5`；字符串24字符 |
| output_fields | 本轮顶层输出字段列表；不替代完整Schema | `["decision","expectation_delta","prior_expectation","reason"]`；4项，JSON 61字符 |
| instruction | 继承AGENTS/Skill、只处理当前阶段、用context_ref读取研究、正确处理分页等固定说明 | `Inherited AGENTS and unique Skill apply. This is the current phase input; do not reread contrac…（摘录）`；字符串243字符 |
| context_ref | 本Case本阶段冻结研究绑定的工具访问引用；不是研究正文 | `w3ctx_d8f739cd5025e5d8d57fe0bc8a3fe0736e782c452746fb3d316779fe57db5518`；字符串70字符 |
| context_manifest_sha | 冻结manifest的内容指纹，用于确认访问的是同一资料版本 | `9e221c6f7daaeb6d894892f0dab8929adecc6c545bc8d32cb5d1ea747df53883`；字符串64字符 |
| direct_objects | 后端绑定返回的直接对象提示；本例两轮均为空数组，不能误读为没有研究资料 | `[{"kind":"policy","id":"pol_90d7c24fb5d048b8be7bed29ec918066","provided_inline":true},{"kind":"…`；3项，JSON 256字符 |

Trade输出字段为decision、expectation_delta、prior_expectation、reason；Operations为delta_candidates、novelty、pin_actions、watch_actions。Operations不重新提交交易方向，不改变W2 HIT结论。

## 第三层：Trade input逐字段

| 字段/路径 | 简要解释 | 本例形状、内容与长度 |
| --- | --- | --- |
| ticker | 当前研究标的 | `NBIS`；字符串4字符 |
| cutoff_at | 本Case消息总线事件时点，界定当前判断时点；不同于新闻发布时间 | `2026-11-14T04:20:14.625556+00:00`；字符串32字符 |
| trading_context.runtime_mode | REALTIME等运行模式 | `REALTIME`；字符串8字符 |
| trading_context.semantic_day | Runtime语义日，不直接按UTC日期推断 | `2026-11-13`；字符串10字符 |
| trading_context.intent_expires_at | 本Case交易意图的有效期终点 | `2026-11-14T07:00:00+00:00`；字符串25字符 |
| trading_context.output_kind | 实时TRADE_INTENT或闭市路径CANDIDATE | `TRADE_INTENT`；字符串12字符 |
| trading_context.holding_exit_at | 按交易策略和日历得到的计划退出时点，可空 | `2026-11-16T20:30:00+00:00`；字符串25字符 |
| trading_context.exit_before_regular_close_minutes | 相对正常收盘提前退出的分钟数，可空 | `30` |
| case_type | POLICY_HIT_REVIEW、POLICY_RELATED_EVALUATION或OPEN_EVENT_EVALUATION；HIT优先 | `POLICY_RELATED_EVALUATION`；字符串25字符 |
| additional_tags | 纯信息标签数组：W1/W2_LOW_CONFIDENCE、WATCH_FOLLOWUP、BODY_MISSING、RECENT_TRADE；不独立授予交易资格或改变路由 | `[]`；0项，JSON 2字符 |
| message.title | 当前标题；与body完全相同时可置空以免重复 | `NBIS Q3 Earnings Call Highlights Expansion Plans and Funding - The Globe and Mail`；字符串81字符 |
| message.body | 当前正文；可能为空、很长，亦可能含采集噪音 | `NBIS Q3 Earnings Call Highlights Expansion Plans and Funding\nNebius Group N.V.NBIS emphasized a…（摘录）`；字符串6047字符 |
| message.source_name | 消息来源名 | `news.google.com`；字符串15字符 |
| message.published_at | 业务认可的发布时间；不要当作入站/模型启动时间 | `2026-11-14T04:18:49Z`；字符串20字符 |
| message.url | 原消息URL，不保证正文完整或可直接访问 | `https://www.theglobeandmail.com/investing/markets/stocks/NBIS-Q/pressreleases/8824173/nbis-q3-e…（摘录）`；字符串147字符 |
| w1.result | W1的NEW/OLD判断 | `NEW`；字符串3字符 |
| w1.confidence | W1置信度 | `normal`；字符串6字符 |
| w1.reason | W1判断理由 | `季度收入增长及2027年收入、资本开支和融资比例已有覆盖；但FY2026收入指引进一步收窄至30亿至32亿美元，且本消息新增了2027年盈亏平衡预期、客户预付款和合同经济性细节等核心进展。`；字符串94字符 |
| w1.events | 召回/引用的Event对象详情；顶层reference_ids及fact_attributions在此输入构造中移除，以events承载证据；空列表正常 | `[{"candidate":{"assertion_state":"GUIDANCE","entities":["Nebius Group"],"occurrence_date":"2026…`；3项，JSON 1141字符 |
| w2.policy_hit / policy_related | W2确认的命中与相关状态，两者可同时成立 | `false`；`true` |
| w2.policy_ids | 实际HIT Policy身份集合 | `[]`；0项，JSON 2字符 |
| w2.hit_condition_ids | 实际命中的Condition身份/分组 | `[]`；0项，JSON 2字符 |
| w2.related_policy_ids | 新版W2的RELATED Policy身份；老冻结结构可能没有，本例实际包含3个related_policy_ids；历史冻结结构可能缺键，不能统一补造 | `["pol_351a03ffc69e45ddaf14f78487992854","pol_c653295f02044bb29ff7f81a682a0930","pol_90d7c24fb5d…`；3项，JSON 118字符 |
| w2.matched_condition_ids | 历史兼容字段；不能把它当新版RELATED的必需Condition证据 | 本例父对象为null或此键未出现；不补造false |
| w2.confidence / reason | W2置信度及事实依据 | `normal`；字符串6字符；`pol_351a03ffc69e45ddaf14f78487992854：消息提及Vineland临时供电安排变化导致延误及共址项目收入递延，属于站点交付进度范围，但未明确具体阶段及其服务影…（摘录）`；字符串268字符 |
| policies | 与HIT/RELATED及入口所需WATCH关联的Policy对象，不默认把全PolicySet全文内联；本例3项。对象以冻结Policy自身契约序列化，包含身份、范围、Condition及其业务配置；本文不伪造空样本里没有的子字段 | `[{"activation_conditions":[{"calibration":{"reference_state":"Q2基线是四份大型核心云新约平均TCV超过10亿美元、ACV/MW…`；3项，JSON 28684字符 |
| watch | 有效WATCH状态，见下表 | 对象：`limit, used, items`；JSON 540字符 |
| pins | 有效PIN状态，见下表 | 对象：`limit, used, items`；JSON 3166字符 |
| recent_trade_intents | 同ticker此前72小时已产生交易意图/相应分析决策的冻结历史，不要求成交；不是最近所有消息 | `[{"message":{"title":"CoreWeave results lift AI infrastructure stocks as demand for computing p…`；7项，JSON 71158字符 |
| contexts[].name / cutoff_at | D1/D2/PolicySet/Reference View等资料及其记录的截止时间；null表示未记录，不等于实时最新 | `D1`；字符串2字符；`null` |
| alignment.status / error | W2事实对齐可用性与错误说明 | `COMPLETE`；字符串8字符；`null` |
| alignment.hit_has_new_fact / related_has_new_fact | 有有效对齐时的条件/Policy新增事实标志；缺失不能自行补false | `false`；`true` |

### WATCH和PIN

两者公共limit为容量上限，used为已用数量，items为本次快照；本例均limit=2、used=2。

| 路径 | 含义 | 本例形状、内容与长度 |
| --- | --- | --- |
| watch.items[].policy_id | 跟进关联Policy身份 | `pol_3f969b5c37a44d2cb9bd01510ae5a9c7`；字符串36字符 |
| watch.items[].reason | 为什么等待、后续重点验证什么 | `当前不交易，因规则仍属拟议阶段且对云端远程使用的实际限制及适用范围未明；报道指规则最快本月定案，后续披露可能补足这一关键问题。跟进最终规则是否具约束力、是否限制远程云服务及其覆盖用户范围。`；字符串94字符 |
| watch.items[].start_semantic_day | 跟进开始语义日 | `2026-11-12`；字符串10字符 |
| watch.items[].expires_at | 跟进失效时间 | `2026-11-14T07:00:00+00:00`；字符串25字符 |
| pins.items[].pin_id | 短期置顶消息身份 | `pin_6833a0179f29d04f636fc4d4`；字符串28字符 |
| pins.items[].message | 被保留消息的title/body/source_name/published_at，可能全文很长 | 对象：`body, published_at, source_name, title`；JSON 1448字符 |
| pins.items[].reason | 保留它对后续判断有什么价值 | `保留贷款方据报道考虑退出英伟达5000亿美元AI融资联盟这一未确认状态，供后续融资进展解读，避免把相关确认或变化误判为首次出现。`；字符串64字符 |
| pins.items[].expires_at | PIN失效时间 | `2026-11-14T18:01:51.926289+00:00`；字符串32字符 |

### 近期交易历史每条的字段

| 路径 | 含义 | 本例形状、内容与长度 |
| --- | --- | --- |
| message | 历史消息标题、正文、来源、发布时间；本例含完整长正文及部分广告/播放器文字 | 对象：`title, body, source_name, published_at`；JSON 6493字符 |
| w1.result / reference_ids | 历史NEW/OLD及事件覆盖引用 | `NEW`；字符串3字符；`["E567","E589"]`；2项，JSON 15字符 |
| w2.policy_hit / policy_related | 历史Policy判断 | `true`；`true` |
| w2.policy_ids / related_policy_ids / hit_condition_ids / matched_condition_ids | 历史实际HIT/RELATED身份及兼容字段 | `["pol_9313ed863ee04004b67d1d8185a951d5"]`；1项，JSON 40字符；`["pol_ac34e25398f8445380ccf57f4c4d9571"]`；1项，JSON 40字符；`[{"condition_ids":["C3"],"policy_id":"pol_9313ed863ee04004b67d1d8185a951d5"}]`；1项，JSON 77字符；`null` |
| w3_decision.decision | 历史LONG/SHORT/NO_TRADE等原契约方向值 | `LONG`；字符串4字符 |
| w3_decision.prior_expectation / expectation_delta / reason | 历史预期、预期差与决策理由 | `NBIS自身旧代新签价格上升、IREN重签提价及CoreWeave需求增长已知，但不能据单家公司表现认定行业资产回收普遍改善。`；字符串63字符；`BofA与Wedbush将同业增长进一步连接到长期合同提价、旧代GPU涨价及较小云商回本期缩短，增加了资产型AI云持续变现和资本回收改善的证据，而非仅重复收入超预期。`；字符串83字符；`本次研究提供跨供应商的定价与回收佐证，正向修订NBIS所处市场的成熟GPU变现前景；无需先出现NBIS新订单，但不将同业涨幅套用全机群，也不认为融资风险已解除。保留W2归因。已检查行情，仅用…（摘录）`；字符串139字符 |
| direction | 这条历史意图的方向 | `LONG`；字符串4字符 |
| initiated_at | 历史意图发起/记录时间 | `2026-11-11T17:30:10.384841+00:00`；字符串32字符 |
| execution.output_status | 输出/交易处置状态 | `EXECUTION_ACCEPTED`；字符串18字符 |
| execution.delivery_status | 交付执行模块的回执状态 | `EXECUTION_ACCEPTED`；字符串18字符 |
| execution.intent_released | 是否实际释放了交易意图，而非只有分析候选 | `true` |
| execution.entry_result | 入场执行结果，可空或含原执行回执结构 | `FAILED`；字符串6字符 |
| execution.filled_qty | 成交数量，可空 | `0` |
| execution.error | 执行或交付错误，可空 | `null` |

### policies对象子字段（已核对生产模型）

以下说明来自生产容器实际Policy契约；本例有3条Policy，表中展示第一条的真实字段值。

| 路径 | 解释 | 本例形状、内容与长度 |
| --- | --- | --- |
| policy_id / title | 稳定身份、业务标题 | `pol_90d7c24fb5d048b8be7bed29ec918066`；字符串36字符；`AI云专属容量合同的净新增承诺与采购质量`；字符串20字符 |
| decision | Policy预设方向，不是W3本轮最终方向 | 本例父对象为null或此键未出现；不补造false |
| match_scope | RELATED相关性判断的业务范围 | `Nebius核心AI云大客户与AI原生客户的新专属容量合同、增购、续约、固定采购量或收费条件变更、购买选择权收缩及合同取消；覆盖Nebius或买方披露的新模型/推理容量交易、Meta特定集群…（摘录）`；字符串356字符 |
| source_refs[].shell_id / expectation_id / gap_id | 来源研究Shell、对应预期和预期差身份 | 本例父对象为null或此键未出现；不补造false；本例父对象为null或此键未出现；不补造false；本例父对象为null或此键未出现；不补造false |
| activation_conditions[].condition_id | 触发Condition身份 | `C1`；字符串2字符 |
| activation_conditions[].criterion | 被监测的稳定业务变化描述；多个Condition按固定OR语义 | `Nebius与具名买方（或可靠报道足以识别买方及其付款保障）披露一项已签的新建、扩容或续约核心AI云专属容量承诺；公开的固定合同价值达到至少10亿美元TCV，或明确固定最低购买义务达到一份Q…（摘录）`；字符串230字符 |
| activation_conditions[].calibration.reference_state | Policy截止时已确认的现实锚点 | `Q2基线是四份大型核心云新约平均TCV超过10亿美元、ACV/MW为2000万—2500万美元；同季AI云收入5.749亿美元。约70% Q2新交易含预付款，但这是交易笔数比例，相关cape…（摘录）`；字符串2193字符 |
| activation_conditions[].calibration.trigger_boundary | 超过预期基线的最小可观察触发边界 | `一项至少10亿美元TCV的实际固定采购，达到一份已披露Q2大型新约的量级，且买方/用途、收费责任与服务窗口可识别，已足以新增一个大型付费需求承诺并改变订单组合与未来服务机会；无需再等收入确认…（摘录）`；字符串220字符 |

### 非空Event和Policy：本例的真实内容

本例W1提供3个Event对象，3条Policy分别关联站点交付、项目融资和客户采购。W2只判RELATED，没有编造HIT Condition。以下Event是完整真实值：

```json
{
  "candidate": {
    "assertion_state": "GUIDANCE",
    "entities": [
      "Nebius Group"
    ],
    "occurrence_date": "2026-11-12",
    "proposition": "Nebius预计2026年收入位于原30亿至34亿美元指引区间低端，全年资本开支约270亿美元；公司对2027年的初步展望为收入120亿至130亿美元、资本开支400亿至450亿美元。",
    "subject_time": "FY2026及FY2027"
  },
  "known_before_current_message": true,
  "provisional_event_id": "E383",
  "semantic_day": "2026-11-13"
}
```

Event外壳中的`provisional_event_id`是可定位身份；`known_before_current_message`表示此前已知；`semantic_day`表示语义日；`candidate`承载具体事实。candidate的proposition为事实句，assertion_state区分GUIDANCE/ACTUAL等，entities为主体名单，occurrence_date为发生日期，subject_time为事实指向的期间。此形状是本例provisional来源对象，不强行推广为所有Event的统一Schema。

下面展示第一条真实Policy，仅长calibration段落摘录，其余形状不伪造：

```json
{
  "activation_conditions": [
    {
      "calibration": {
        "reference_state": "Q2基线是四份大型核心云新约平均TCV超过10亿美元、ACV/MW为2000万—2500万美元；同季AI云收入5.749亿美元。约70% Q2新交易含预付款，但这是交易笔数比例，相关capex覆盖50%—60%并非每单合同价值或全公司现金比例。多季RPO为374.906亿美元，不能代替合同收入时序；未来2027年爬坡已由Meta专属协议和Q2晚2026容量计划支持。微软原约全批已经交付；Meta另有的最多150亿美元仅是第三方售后剩余容量安排。\n\n2026-10-31，S&P将Meta展望调至负面但维持AA−，并提出基础设施支出、自由现金流、现金消耗、杠杆及未来借款的预测；Meta对其AI变现…（摘录，完整见附录）",
        "trigger_boundary": "一项至少10亿美元TCV的实际固定采购，达到一份已披露Q2大型新约的量级，且买方/用途、收费责任与服务窗口可识别，已足以新增一个大型付费需求承诺并改变订单组合与未来服务机会；无需再等收入确认或另一个客户重复签约。若金额只是最大可购上限、买方无明确付款责任、相同容量已计入现有合同，或只称将采购/扩大用户名单，则未跨边界。10亿美元是总合同价值比较锚，不年化；只有同代、同口径容量确实披露时才用2000万—2500万美元/MW/年作辅助比较。"
      },
      "condition_id": "C1",
      "condition_revision": "1986671abff4749b6ff7b989cb24c6566bd4ca24bb2a4d13378787ae3ad99fa5",
      "criterion": "Nebius与具名买方（或可靠报道足以识别买方及其付款保障）披露一项已签的新建、扩容或续约核心AI云专属容量承诺；公开的固定合同价值达到至少10亿美元TCV，或明确固定最低购买义务达到一份Q2大型新约的可比规模。公告说明买方用于训练或生产推理等实际算力需求、服务开始窗口以及在容量验收后应付的服务费/最低付款或预付款。承诺须是相对已有固定合同的净新增，不得只是意向书、最高购买上限、可随意取消的期权，或Meta同一集群先向第三方销售再由Meta承接的重复计量。",
      "decision": "LONG",
      "trigger_layer": "MIDDLE"
    },
    {
      "calibration": {
        "reference_state": "新签大单的当前可比单位是Q2四份平均TCV超过10亿美元；现有集中度仍显著，但匿名客户C/D/E不得映射到具名买方。Meta最高150亿美元的剩余容量机制并非同额固定订单；微软原协议全部批次已交付，不是未履约储备。客户及Nebius可多源采购，竞争者赢得订单不等于Nebius丢失订单。\n\n2026-10-31，S&P将Meta展望从稳定改为负面、维持AA−，并预测未来数年较高基础设施投资/现金消耗及可能增加借款；Meta不同意其AI变现相对同业的判断。该信用展望是已知的前瞻风险背景，不代表评级已下调、发生违约、已改变对Nebius的付款责任或已撤回合同。当前短向条件仍需公开的固定承诺实质取消/…（摘录，完整见附录）",
        "trigger_boundary": "移除一个至少10亿美元TCV或一份同等规模主要固定批次的已承诺采购，直接否定一项大型订单，而非一般行业需求减速；该量级与Q2单份大型新约平均相当，因此无需叠加多个小客户流失、下一季使用下滑或全公司RPO重算。只削减未承诺的最高额度、项目改址后同等收费继续、期限变化但固定总对价/最低购买未下修，或竞争者取得新客户而没有Nebius合同退出，都不构成足够的SHORT。"
      },
      "condition_id": "C2",
      "condition_revision": "f788d285b3160e11671bca58cd02f3ad724c585581905db21332f4a28efcc0f6",
      "criterion": "Nebius或具名大买方披露，一份原先固定的核心AI云专属容量合同被取消、实质缩减、提前终止，或在续约/改约中撤掉了有约束力的购买义务；被移除的承诺达到至少10亿美元TCV，或相当于一份已披露Q2大型新约的主要固定服务批次。消息须显示对Nebius的固定付费容量、服务收费权或合同总对价确实减少；单纯换址/改期而保留等量可收费替代容量、未行使的最高容量选项回落、或客户在其他云新增采购而未撤回Nebius合同均不符合。",
      "decision": "SHORT",
      "trigger_layer": "MIDDLE"
    }
  ],
  "match_scope": "Nebius核心AI云大客户与AI原生客户的新专属容量合同、增购、续约、固定采购量或收费条件变更、购买选择权收缩及合同取消；覆盖Nebius或买方披露的新模型/推理容量交易、Meta特定集群剩余容量的实际分配、以及Reflection、Cohere或其他客户扩容与续约。留意合同是否超出现有固定承诺、服务何时开始、买方用途与付款支持、预付款、最低购买义务及退出/补偿权。例如，买方签下另一个大型多年期集群，或大客户把已固定的购买量改成可选额度/退出一部分，都是相关消息；公布最大容量、合作名单、预算意向、设备到场或新客户姓名本身不等于合同增量。同时留意可归属至具名大买方的大额基础设施承诺的信用评级/融资能力变化，作为付款支持的背景；单独的评级展望、风险预测或投资计划不等于Nebius合同变更、违约或现金损失。",
  "policy_id": "pol_90d7c24fb5d048b8be7bed29ec918066",
  "ref": [
    "output/work/v21/discovery/S0001.jsonl#L1",
    "context/document3/v21/d2/shell-0001.json｜AI云算力需求与商业化",
    "context/document3/v21/shared/d1/c1.md｜新签价格及转收入；合同回收与预付款",
    "context/document3/v21/shared/d1/c3.md｜驱动甲：客户采购与期限结构",
    "context/document3/v21/shared/d1/c4e_network_build.md｜大买方怎样分配容量、锁定交付窗口",
    "context/document3/v21/shared/d1/c5.md｜主题甲：2027年交付跃迁必须转为收入",
    "https://nebius.com/newsroom/nebius-signs-new-ai-infrastructure-agreement-with-meta",
    "https://assets.nebius.com/assets/a6ecfd85-a6cb-4967-8ef7-9a25bd261f9c/SHLQ226.pdf",
    "https://www.sec.gov/Archives/edgar/data/1513845/000110465926094844/nbis-20260812xex99d2.htm",
    "https://www.sec.gov/Archives/edgar/data/1513845/000110465925088312/tm2525580d1_6k.htm",
    "context/document3/v21/shared/event_library.md#E109",
    "context/document3/v21/shared/event_library.md#E120",
    "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/86/source/snapshot",
    "context/document3/v21/shared/event_library.md#E158",
    "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/5/source/snapshot",
    "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/76/result",
    "context/document3/v21/shared/delta.json#/reference_view_delta",
    "context/document3/v21/shared/event_library.md#E202",
    "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/67/source/snapshot",
    "https://huggingnews.com/global-markets/metas-debut-euro-bond-sale-attracts-record-euro53b-in-investor-demand-0f97832d",
    "context/document3/v21/shared/event_library.md#E260",
    "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/0/source/snapshot",
    "https://www.sec.gov/Archives/edgar/data/1326801/000162828026066773/meta-20260930.htm",
    "context/document3/v21/shared/event_library.md#E336",
    "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126008/tm2629571d1_ex99-1.htm",
    "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-20261112x6k.htm"
  ],
  "title": "AI云专属容量合同的净新增承诺与采购质量",
  "transmission": "Nebius核心AI云2026-Q2收入为5.749亿美元、6月末年化运行率30亿美元；四份Q2大型新约平均TCV超过10亿美元，新签TCV接近Q1四倍，且大部分容量对应2026年末、主要贡献2027年。合同承诺通过买方用途、付款能力、价格、期限和可退出权影响未来可售容量与收费确定性：Q2新约约70%的交易含预付款，相关预付覆盖预计capex约50%—60%，能降低建设融资缺口，但不是该比例合同价值已收现。Q2 RPO为374.906亿美元，其中36%预计在2026-07至2028-06确认，且不含原始期限一年或以下合同、包含可变对价，不能把TCV或RPO直接年化为当期收入。Q2匿名C/D/E占集团收入24%/21%/14%，客户集中仍高，但匿名编号不能对号入座，也不能用新增客户名单单独证明集中度已下降。微软原五年协议已披露全批交付，不能继续算作待交付订单；Meta新五年专属容量120亿美元计划2027年初交付，另最多150亿美元只对应特定集群向第三方销售后的剩余可用容量，不能把上限当成无条件新增订单或与第三方销售重复相加。大买方同时使用多家GPU云及自有/专用路线，故新签必须是真正的新增合同购买，而不是行业需求、最高容量上限或同池改名。\n\n2026-10-31，S&P Global Ratings将Meta信用展望由稳定调为负面、同时维持AA−评级；其预测Meta年度基础设施支出到2028年可达2520亿美元、未来三年出现自由现金流赤字、2027年现金消耗峰值为710亿美元、杠杆到2027年可能超过1.0倍并可能于2028年借款弥补现金流缺口。Meta表示不同意S&P关于其AI投资变现能力弱于同业的判断。此为评级机构的信用/投资预测更新，不是实际降级、违约/逾期或NBIS合同及付款条件变化；本次材料没有报告120亿美元固定专属容量协议被修改，也没有把最高150亿美元第三方售后剩余容量变成固定订单。该事实纳入买方付款背景，但不单独证明订单流失或Meta无法履约。\n\n2026-11-03，Meta的150亿美元美元债发行已完成定价（五档，订单约580亿美元，30年期一档融资成本7.50%；穆迪对新债评级Aa3、展望稳定），但截至本截止点没有交割/资金到账确认，拟议欧元部分也不视为完成。该进展改变买方融资执行背景而非Nebius合同事实；新增长期债务与资金来源的可用性须分别看待，不能据定价推定Meta对Nebius的固定付款责任或容量采购已经改善。\n\n截至2026-11-12，Nebius披露Q3 AI Cloud收入9.738亿美元；自Q3开始客户已承诺超过120亿美元合同价值。Q3同口径加权ACV/MW超过2,500万美元（Q2超过2,000万美元），可比1—3年交易预计回收期由22个月缩短至19个月；Q3超过90%的合同价值含预付款，超过一年协议的预付款覆盖相关Q3 capex超过100%。公司还披露Databricks、Datadog和Snowflake多年度AI Cloud协议，但未披露条款/金额；五年期交易为既有客户扩容、新增容量。以上是Q3组合与公司披露的现时基线，不把组合TCV、RPO、预付覆盖或未具名客户拆成单一买方固定采购义务。\n\n截至2026-09-30，Q3 RPO为387亿美元，高于6月30日的375亿美元；它不同于新签合同TCV，不能证明任何单一买方的固定订单、服务费或现金收入。Q3可比1—3年交易的19个月回收期不计客户预付款效益；不得将其再计作现金融资证据。"
}
```

三个Policy的长度分布，均为紧凑JSON字符：

| Policy | Condition数 | 整个Policy字符 | reference_state合计字符 | trigger_boundary合计字符 |
| --- | ---: | ---: | ---: | ---: |
| pol_90d7c24fb5d048b8be7bed29ec918066 | 2 | 9,366 | 4,151 | 403 |
| pol_c653295f02044bb29ff7f81a682a0930 | 4 | 7,823 | 1,687 | 1,227 |
| pol_351a03ffc69e45ddaf14f78487992854 | 4 | 11,491 | 3,743 | 1,079 |

这说明Policy并不只是几句触发规则：本例calibration.reference_state包含累计研究背景，是主要阅读负担之一。

### 用一个历史条目看“展开后长什么样”

以下取新样本第一条真实历史，长正文与理由只作明确标注的摘录；其余完整内容见附录。

```json
{
  "message": {
    "title": "CoreWeave results lift AI infrastructure stocks as demand for computing power surges",
    "body": "Published: 12:12 11 Nov 2026 EST\n\nAI infrastructure stocks rallied on Wednesday after better-than-expected results from CoreWeave reinforced expectations that demand for computing power is continuing to outpace available supply.\n\n…（摘录）",
    "source_name": "yahoo",
    "published_at": "2026-11-11T17:12:00Z"
  },
  "w1": {
    "result": "NEW",
    "reference_ids": [
      "E567",
      "E589"
    ]
  },
  "w2": {
    "policy_hit": true,
    "policy_related": true,
    "related_policy_ids": [
      "pol_ac34e25398f8445380ccf57f4c4d9571"
    ],
    "policy_ids": [
      "pol_9313ed863ee04004b67d1d8185a951d5"
    ],
    "hit_condition_ids": [
      {
        "condition_ids": [
          "C3"
        ],
        "policy_id": "pol_9313ed863ee04004b67d1d8185a951d5"
      }
    ],
    "matched_condition_ids": null
  },
  "w3_decision": {
    "decision": "LONG",
    "expectation_delta": "BofA与Wedbush将同业增长进一步连接到长期合同提价、旧代GPU涨价及较小云商回本期缩短，增加了资产型AI云持续变现和资本回收改善的证据，而非仅重复收入超预期。",
    "prior_expectation": "NBIS自身旧代新签价格上升、IREN重签提价及CoreWeave需求增长已知，但不能据单家公司表现认定行业资产回收普遍改善。",
    "reason": "本次研究提供跨供应商的定价与回收佐证，正向修订NBIS所处市场的成熟GPU变现前景；无需先出现NBIS新订单，但不将同业涨幅套用全机群，也不认为融资风险已解除。保留W2归因。已检查行情，仅用于时效与完…（摘录）"
  },
  "direction": "LONG",
  "initiated_at": "2026-11-11T17:30:10.384841+00:00",
  "execution": {
    "output_status": "EXECUTION_ACCEPTED",
    "delivery_status": "EXECUTION_ACCEPTED",
    "intent_released": true,
    "entry_result": "FAILED",
    "filled_qty": 0,
    "error": null
  }
}
```

| 条目 | 对象JSON字符 | 正文字符 |
| --- | ---: | ---: |
| 1 | 7,460 | 6,143 |
| 2 | 1,399 | 361 |
| 3 | 2,003 | 836 |
| 4 | 9,345 | 8,191 |
| 5 | 37,543 | 35,880 |
| 6 | 1,124 | 62 |
| 7 | 12,276 | 10,873 |

## 第四层：Operations新增input

| 字段 | 解释 | 本例形状、内容与长度 |
| --- | --- | --- |
| submitted_decision | 已提交Trade的decision/prior_expectation/expectation_delta/reason，作为不可重新交易的前提 | 对象：`decision, expectation_delta, prior_expectation, reason`；JSON 398字符 |
| output_disposition | 编排对该方向实际形成的处置，例如NO_TRADE；不要仅由LONG/SHORT推断一定已发交易 | `NO_TRADE`；字符串8字符 |
| candidate_eligibility.origin_trade_eligible | 存在Candidate时记录来源是否可交易；无Candidate为null | 本例父对象为null或此键未出现；不补造false |
| candidate_eligibility.trade_disposition | Candidate实际交易处置；与上一字段同一可选对象 | 本例父对象为null或此键未出现；不补造false |
| watch / pins | 进入运营时冻结的有效全局状态，可能与Trade快照不同；不是无条件沿用旧状态 | 对象：`limit, used, items`；JSON 540字符；对象：`limit, used, items`；JSON 3166字符 |
| allowed_reference_event_ids | 本轮novelty.reference_ids可使用的固定Event ID白名单；本例671个ID | `["E102","E104","E105","E106","E107","E109","E111","E116","E117","E118","E12","E120","E121","E12…`；671项，JSON 4653字符 |
| reference_rule | 只允许白名单E#；OLD必须有实际事实覆盖，PIN、URL、消息ID、F#不能替代Event | `novelty.reference_ids accepts only the listed E# Event IDs, not F#, PIN IDs, news IDs or URLs. …（摘录）`；字符串223字符 |

运营新增input不再完整重复ticker/message/W1/W2/policies/history；Agent依靠同一Case线程继承。研究绑定为本阶段独立context_ref/manifest；这也不是跨Case共享原消息历史。

### 本例运营新增输入的直观样子

`candidate_eligibility:null`表示整个对象为空，不是内部布尔字段为false。合法Event白名单共671项：`["E102","E104","E105","E106","E107","E109"]`只是前6项，完整数组见附录。已提交决策的完整真实值：

```json
{
  "decision": "NO_TRADE",
  "expectation_delta": "摘要补充2027年收入指引不含额外轻资产安排贡献，并提示不能年化Q3利润率，细化了收入质量与利润判断，但未上调指引、确认新增可用资金或披露新的交付变化。",
  "prior_expectation": "FY2026收入30亿—32亿美元已见于此前研报；FY2027收入120亿—130亿美元、Q4税前盈亏平衡目标、预付款覆盖及资本开支展望均已披露。共址延期和Q3约3.25亿美元一次性收入也已知。",
  "reason": "维持W2相关但未命中：没有新增具名大客户固定采购、重要项目阶段的可用资金或现金责任变化，也没有相对既有排程的新延期。轻资产收入排除说明提供正面解释，但不足以独立形成实质经营预期差；利润率提示也未给出新的下修幅度。电话会复盘不重置11月12日披露的催化时点。本次不交易不依赖价格完成度否决。"
}
```

## 第五层：按需读取的研究与行情

| 类别 | 给法与作用 |
| --- | --- |
| D1 | 冻结研究章节/原文对象，提供ticker的研究主线和先验 |
| D2 | 冻结Shell/Unit、状态、baseline/factors/gaps及来源；用于具体传导和预期核验 |
| D3 / PolicySet | 冻结Policy及相关研究；内联policies只是已挑选的一部分，不等于整个可查研究范围 |
| Reference View / reference_event | 固定参考视图及可读取的Event；与当前合法引用范围配套 |
| provisional facts | 通过冻结Event/证据路径提供；W1R1的today_provisional_facts输入格式不能直接当作W3内联字段，真实Trade没有这个同名字段 |
| WATCH/PIN/submitted_decision运行状态 | 可以作为runtime_state对象进入研究绑定；部分已同时内联，不代表要再查一遍 |
| trade_intent / historical_message | 近期历史意图及原消息的可检索对象；内联历史与按需对象可能有重叠 |
| raw_document / source | 可访问的冻结原文与证据来源；不是允许读取任意服务器文件 |
| market_view | 预备的多时间尺度行情视图，经工具按需取得；不是user中直接塞入整套K线，PREPARING不应忙等 |
| Web Search / IBKR只读能力 | SDK/MCP按生产能力设置提供；实际调用后的结果进入线程，不能把初始冻结context理解为完全杜绝时点之后的信息 |

w3_research通常经catalog发现对象，search定位，read按对象读取；通过context_ref绑定允许的资料范围。body可能是分页文本片段，next_cursor与page.complete指示是否完整；不能对每个未完成片段直接当完整JSON解析。

后端manifest还保存case_id、phase、ticker、mode、version_pin、components、cutoff、deadline、state、history、events等。这些主要是服务端冻结/工具运行数据；不应一律算成首轮user里已全文注入。旧入口才出现的task_path/output_schema/skill_path/reference_path/research_path、ibkr_history client_id、policy_usage等不能冒充当前v5常规字段；本例中均无这些字段。回测execution_environment/evidence是离线分支，不属于生产Case默认输入。

### 工具对象、请求和返回实际上是什么形状

这里补的是**接口形状示意，不是本例实际调用回执**；此前导出的是两轮user输入，没有逐条导出该Case所有研究工具正文，因此不能为D1/D2/Web等填造“实测平均长度”。它们也不预先整体计入114,296字符的Trade新增输入。

| 类别 | 可读到的内容形状/典型内容 | 长度如何理解 |
| --- | --- | --- |
| D1 | 类似章节目录与Markdown研究正文，章节可讨论市场主线、公司经营对象及预期 | 请求读哪一节/哪一页才返回相应正文；本例未量取 |
| D2 | 以业务Unit名称定位，再读state、baseline、factors、gaps等部分；可能是多段文字或表格 | 一个Unit可多页，不能把一次read当完整D2 |
| D3/PolicySet | 结构化Policy数组；字段形状见上文Policy示意 | 取决于Policy/Condition数、自然语言段落长度 |
| Reference View/Event | 目录/视图用于定位E#，单Event读取可展开现实描述、Fact和证据 | `E102`四个字符只是ID，读取详情后另有正文 |
| provisional | 可包含provisional_event_id、source_message_id、candidate_index及candidate事实的对象 | 与简单Markdown目录不是同一种体积；这些字段是身份/事实载荷，不全是首轮内联字段 |
| runtime_state | 如`{"id":"submitted_decision","kind":"runtime_state"}`定位到已提交方向；WATCH/PIN同类 | 本例内联状态已有实测长度，再读结果会增加线程内容 |
| trade_intent/historical_message | 与前述历史对象/消息近似的结构化内容，工具按需返回 | 可能含长正文，不保证只有摘要 |
| raw_document/source | 原始Markdown、JSON、正文或出处信息 | 能很长并分页；文件在服务端存在不代表已全部送入模型 |
| market_view | 行情状态及多个周期的K线/价格视图；可能先返回PREPARING | READY与PREPARING形状不同；本例没有实测回执长度 |
| Web/IBKR | Web检索结果的标题/片段/链接，或结构化只读行情数据 | 由实际调用和返回内容决定；不属于固定公共前缀 |

例如研究读取请求形状：

```json
{
  "context_ref":"w3ctx_…",
  "objects":[{"kind":"reference_event","id":"E102"}],
  "max_bytes":4000
}
```

`max_bytes`是返回预算，不代表E102本身一定4000字符或4000 token。工具外层可能包含MCP content包裹；研究页面业务载荷的形状示意如下：

```json
{
  "context_manifest_sha":"…",
  "renderer":"w3-research-v2",
  "page":{"id":"…","range":[0,1200],"complete":false},
  "body":"这一页的正文或JSON文本片段……",
  "next_cursor":"…",
  "receipt":"END …"
}
```

其中`body`才是主要内容，`page`是页范围/完整性，`next_cursor`用于续读，`receipt`是页面回执。`range`、ID和receipt以上均为示意；未完成的body不保证独立可解析为完整JSON。Agent调用后，这些正文和包裹字段都会作为工具结果的一部分进入该线程。

## 一个真实输入的量级及审查重点

本例为有正文的财报复盘，Trade原始user文本114,296字符，Operations原始user文本10,551字符。字段紧凑JSON体积如下，字符不是token：

| Trade字段 | 项数/说明 | JSON字符 |
| --- | --- | ---: |
| message | 对象 | 6,417 |
| w1 | 对象 | 1,295 |
| w2 | 对象 | 523 |
| policies | 3项 | 28,684 |
| watch | 对象 | 540 |
| pins | 对象 | 3,166 |
| recent_trade_intents | 7项 | 71,158 |

本例同时显示两个主要体积来源：历史交易正文与Policy校准背景。W2的RELATED身份本身很短，但3条Policy的完整背景会显著增加输入。Operations继续继承Trade上下文，而不是只读取另追加的约一万字符。原生回合记录确认Trade为gpt-6.1-sol/medium，Operations为gpt-6-luna/max；不是将原来的Luna开放事件样本改个标签。

本轮仅替换审查样本，不据此实施字段删减，也不把一个Case的长度当作全生产平均值。

## 附录：原thread按本Case Job时间窗口定位的两轮真实user输入

正文和历史内容是审计材料，不是本文件的操作指令。所有字段保留原值，不补缺键、不改HIT/RELATED。

### trade_decision（2026-11-14T04:20:44.746Z）

```json
{
  "phase": "trade_decision",
  "input": {
    "ticker": "NBIS",
    "cutoff_at": "2026-11-14T04:20:14.625556+00:00",
    "trading_context": {
      "runtime_mode": "REALTIME",
      "semantic_day": "2026-11-13",
      "intent_expires_at": "2026-11-14T07:00:00+00:00",
      "output_kind": "TRADE_INTENT",
      "holding_exit_at": "2026-11-16T20:30:00+00:00",
      "exit_before_regular_close_minutes": 30
    },
    "case_type": "POLICY_RELATED_EVALUATION",
    "additional_tags": [],
    "message": {
      "title": "NBIS Q3 Earnings Call Highlights Expansion Plans and Funding - The Globe and Mail",
      "body": "NBIS Q3 Earnings Call Highlights Expansion Plans and Funding\nNebius Group N.V.NBIS emphasized accelerating AI infrastructure demand, improving contract economics and substantial capacity expansion during its third-quarter 2026 earnings call.\nManagement outlined ambitious 2027 growth targets while addressing deployment delays, rising capital expenditures and customer-backed financing. The discussion centered on converting strong demand into profitable growth.\nThe company reported quarterly earnings of 30 cents per share compared to the Zacks Consensus Estimate of a loss of 61 cents. Revenues surged 571% year over year to $980.8 million, surpassing the Zacks Consensus Estimate of $916.8 million.\nNebius Group N.V. Price, Consensus and EPS Surprise\nNebius Group N.V. price-consensus-eps-surprise-chart | Nebius Group N.V. Quote\nNebius Targets Fourfold Revenue Growth in 2027\nChief Financial Officer Dado Alonso projected 2027 revenues of $12 billion to $13 billion, representing approximately fourfold growth. Management expects pretax breakeven in the fourth quarter of 2027.\nFor 2026, revenues are now projected at $3 billion to $3.2 billion, narrowing the previous $3 billion to $3.4 billion range.\nAlonso attributed the revision to delays at third-party facilities. The company maintained its approximately 40% adjusted EBITDA margin target for 2026.\nNBIS Faces Capacity Deployment Challenges\nFounder and Chief Executive Officer Arkady Volozh acknowledged construction delays at colocation facilities, including New Jersey, which have shifted some revenue recognition into 2027.\nDuring questioning from BNP Paribas, Chief Product and Infrastructure Officer Andrey Korolenko identified changes in temporary power arrangements as the primary cause of delays at Vineland.\nKorolenko emphasized that company-owned facilities should provide greater scheduling control. Most new capacity scheduled for 2027 will come from Nebius-operated sites.\nNebius Sees Improving Contract Economics\nVolozh highlighted strengthening customer commitments, with third-quarter agreements exceeding $25 million per megawatt, compared with $20 million to $25 million previously.\nCustomer prepayments now cover more than 100% of capital expenditures associated with newly contracted capacity, compared with 50% to 60% in the preceding quarter.\nChief Revenue Officer Marc Boroditsky told Goldman Sachs that flexible contract structures allow Nebius to adjust its financing requirements. More than 90% of third-quarter contracted value included prepayments.\nNBIS Outlines Substantial Funding Requirements\nAlonso raised the 2026 capital expenditure outlook to approximately $27 billion from the previous $20 billion to $25 billion range.\nFor 2027, management anticipates spending $40 billion to $45 billion, with 75% to 85% expected to be funded through customer prepayments and asset-backed financing.\nThe company projects $17 billion to $20 billion in customer prepayments during 2027. Alonso emphasized that investment-grade customer contracts and financing flexibility underpin the expansion strategy.\nNebius Expands Its AI Software Platform\nChief Business Officer Roman Chernin highlighted growing adoption of Token Factory, the company's managed inference platform, which supports model deployment and optimization.\nVolozh expects the platform to reach several hundred million dollars in annual recurring revenues, supported by infrastructure ownership and favorable operating costs.\nBoroditsky also highlighted multiyear enterprise agreements involving Databricks, Datadog, Snowflake and Palantir. These relationships broaden the company's reach beyond AI-native customers.\nNBIS Addresses Revenue Quality and Margin Questions\nDuring questioning from Wolfe Research, Chief Operating Officer Ophir Nave explained that approximately $325 million in nonrecurring quarterly revenues came from initial asset-light infrastructure partnerships.\nNave clarified that the company's 2027 revenue forecast excludes potential contributions from additional asset-light arrangements.\nResponding to Compass Point Research, Alonso cautioned against annualizing third-quarter margins. He explained that infrastructure commissioning creates timing differences between operating expenses and recognized revenues.\nNebius Maintains Focus on Execution\nManagement's commentary reflected confidence in customer demand and pricing, alongside caution regarding infrastructure delivery schedules.\nThe company's priorities remain expanding owned capacity, strengthening prepayment arrangements and improving profitability while managing substantial investment requirements.\nZacks Rank and Style Scores Signal Prospects\nNBIS currently carries a Zacks Rank #2 (Buy), indicating favorable near-term earnings estimate revision trends. Its Growth Score of B highlights relatively attractive growth characteristics, while Value, Momentum and VGM Scores of C indicate more moderate positioning across those factors. You can see the complete list of today’s Zacks #1 Rank (Strong Buy) stocks here.\nThe combination of a favorable Zacks Rank and Growth Score supports its growth-oriented profile, although the broader Style Scores present a mixed picture. The Zacks Rank remains subject to change as analysts revise their earnings estimates following the reported quarterly results.\nZacks' Research Chief Names \"Stock Most Likely to Double\"\nOur team of experts has just released the 5 stocks with the greatest probability of gaining +100% or more in the coming months. Of those 5, Director of Research Sheraz Mian highlights the one stock set to climb highest.\nThis innovative software-as-a-service firm upped its user base +22% year over year. That rate is increasing and margins are expanding as AI efficiencies take effect. Of course, all our elite picks aren't winners, but this one could far surpass earlier Zacks' Stocks Set to Double like D-Wave Quantum, which shot up +680.1%.\nFree: See Our Top Stock And 4 Runners UpThis article originally published on Zacks Investment Research (zacks.com).",
      "source_name": "news.google.com",
      "published_at": "2026-11-14T04:18:49Z",
      "url": "https://www.theglobeandmail.com/investing/markets/stocks/NBIS-Q/pressreleases/8824173/nbis-q3-earnings-call-highlights-expansion-plans-and-funding/"
    },
    "w1": {
      "result": "NEW",
      "confidence": "normal",
      "reason": "季度收入增长及2027年收入、资本开支和融资比例已有覆盖；但FY2026收入指引进一步收窄至30亿至32亿美元，且本消息新增了2027年盈亏平衡预期、客户预付款和合同经济性细节等核心进展。",
      "events": [
        {
          "candidate": {
            "assertion_state": "GUIDANCE",
            "entities": [
              "Nebius Group"
            ],
            "occurrence_date": "2026-11-12",
            "proposition": "Nebius预计2026年收入位于原30亿至34亿美元指引区间低端，全年资本开支约270亿美元；公司对2027年的初步展望为收入120亿至130亿美元、资本开支400亿至450亿美元。",
            "subject_time": "FY2026及FY2027"
          },
          "known_before_current_message": true,
          "provisional_event_id": "E383",
          "semantic_day": "2026-11-13"
        },
        {
          "candidate": {
            "assertion_state": "ACTUAL",
            "entities": [],
            "occurrence_date": "2026-11-12",
            "proposition": "Nebius reported unaudited Q3 revenue of $980.8 million, up 571% year over year and above approximately $907.5 million consensus; nine-month revenue was $1.9621 billion.",
            "subject_time": "FY2026-Q3"
          },
          "known_before_current_message": true,
          "provisional_event_id": "E417",
          "semantic_day": "2026-11-13"
        },
        {
          "candidate": {
            "assertion_state": "GUIDANCE",
            "entities": [],
            "occurrence_date": "2026-11-12",
            "proposition": "Nebius preliminarily forecast FY2027 capital expenditure of $40–$45 billion and expected customer prepayments and investment-grade client asset-backed financing to fund 75%–85% of it.",
            "subject_time": "FY2027"
          },
          "known_before_current_message": true,
          "provisional_event_id": "E455",
          "semantic_day": "2026-11-13"
        }
      ]
    },
    "w2": {
      "policy_hit": false,
      "policy_related": true,
      "policy_ids": [],
      "hit_condition_ids": [],
      "confidence": "normal",
      "reason": "pol_351a03ffc69e45ddaf14f78487992854：消息提及Vineland临时供电安排变化导致延误及共址项目收入递延，属于站点交付进度范围，但未明确具体阶段及其服务影响。pol_c653295f02044bb29ff7f81a682a0930：消息涉及客户预付款覆盖建设支出及资本开支融资，属于项目资金相关范围，但未指明重要阶段的可用资金或现金责任变化。pol_90d7c24fb5d048b8be7bed29ec918066：消息涉及客户承诺、预付款和多年度协议，但未披露具名买方的新增固定采购规模或合同变更。",
      "related_policy_ids": [
        "pol_351a03ffc69e45ddaf14f78487992854",
        "pol_c653295f02044bb29ff7f81a682a0930",
        "pol_90d7c24fb5d048b8be7bed29ec918066"
      ]
    },
    "policies": [
      {
        "activation_conditions": [
          {
            "calibration": {
              "reference_state": "Q2基线是四份大型核心云新约平均TCV超过10亿美元、ACV/MW为2000万—2500万美元；同季AI云收入5.749亿美元。约70% Q2新交易含预付款，但这是交易笔数比例，相关capex覆盖50%—60%并非每单合同价值或全公司现金比例。多季RPO为374.906亿美元，不能代替合同收入时序；未来2027年爬坡已由Meta专属协议和Q2晚2026容量计划支持。微软原约全批已经交付；Meta另有的最多150亿美元仅是第三方售后剩余容量安排。\n\n2026-10-31，S&P将Meta展望调至负面但维持AA−，并提出基础设施支出、自由现金流、现金消耗、杠杆及未来借款的预测；Meta对其AI变现比较有异议。该信用信号进入买方付款支持基线，但不是降级、实际付款失败或合同修订；Meta 120亿美元固定协议及最高150亿美元条件性剩余容量不可混为一谈。\n\n2026-10-27，Meta将Class A发行规模提高并按每股625美元定价至250亿美元，另将2.5亿份强制可转换优先股存托凭证按每份50美元定价、规模125亿美元。与Class A相关的远期出售协议预计于2027-11-19前结算、届时约249亿美元才会收到；Meta还计划不早于2027年第一季度启动600亿美元无投票权Class C ATM。故此轮融资是未来AI建设的资金支持背景，但不是全部款项已在本截止点可用的证明。Meta固定120亿美元Nebius专属容量协议和最高150亿美元的条件性第三方售后剩余容量安排在本材料中均未报告改动。结合10月31日S&P下调展望至负面但维持AA−、并预测高基础设施开支与未来借款需求，当前付款支持基线同时包含股权融资路径与较高投资/现金消耗风险；二者均不等同实际逾期或合同付款责任变化。\n\n2026-11-02，Meta安排固定收益投资者电话会，筹备至少200亿美元的多档美元/欧元债券发行以支持AI基础设施；报道称最多五档美元、六档欧元，Morgan Stanley与Citi安排沟通，发行尚未定价或完成。该计划为潜在融资支持，同时也是拟新增债务，不代表现金已到账或Meta履约能力已净改善；本次材料未披露Meta对Nebius的固定120亿美元专属容量安排或最高150亿美元剩余容量安排有任何改动，也未新增Nebius采购。\n\n2026-11-03，Meta为此前筹备的美元债完成五档合计150亿美元定价；报道所述认购订单约580亿美元，30年期一档融资成本为7.50%，穆迪给予本次新债Aa3、稳定展望。此次是定价进展而非已交割或现金到账的证明，拟议欧元部分亦未因此视为完成。它更新了AI建设融资的执行背景，同时新增长期债务及利息负担；没有报告Meta修改Nebius固定120亿美元专属容量安排或最高150亿美元条件性第三方售后剩余容量安排，也未新增Nebius采购。\n\n2026-11-04报道Meta首次欧元债销售约€82.5亿、六档且认购订单约€530亿；报道未单独确认交割或资金到账。它增加多币种融资执行的可见度，但不是截至cutoff可用现金的证明，也未显示Nebius固定120亿美元专属容量安排或条件性最高150亿美元剩余容量安排发生变化。\n\n2026-11-02，Event Library E260记录了一则归因于J.P. Morgan的跨公司估算：七家AI公司未来数据中心租赁及硬件承诺合计约4.5万亿美元、其中约15%被报告为表内债务，并称Meta约8100亿美元；这是报告中的行业汇总，不是Meta单体债务比率或Nebius新增采购。Meta于2026-10-22提交的2026-09-30 Form 10-Q分别披露约3557.3亿美元未起租租赁义务（预计于2026至2036年间开始）和4545.2亿美元不可取消合同承诺（主要涉及第三方云容量、服务器/网络、数据中心能源及Reality Labs硬件；其中约458.5亿美元和1214.1亿美元分别于2026、2027年到期）；另有最高147.2亿美元、可因云服务商转售而减少的五年云容量或有采购义务。分类及期限不可机械合并为现有债务或近期现金缺口。Meta同时报告期末现金、现金等价物和有价证券合计824.5亿美元，并预计可用资金及经营/融资现金流足以满足未来12个月及其后可预见期间的现金需求。故大额承诺强化买方资金负担这一风险背景，但与融资能力支持并存；该信息没有显示固定120亿美元Nebius专属容量协议变化、实际付款违约，或最高150亿美元第三方售后剩余容量已变成固定采购。\n\n2026-11-12 Q3披露：自Q3开始合计客户承诺TCV超过120亿美元，Q3 ACV/MW超过2,500万美元；公司说明五年协议均为既有客户扩容、Databricks/Datadog/Snowflake多年度协议的金额和条款未披露。该组合基线不能证明任一具名买方单独固定TCV达到10亿美元，也不能把多年度关系或新客户名称当作固定最低购买义务。\n\n截至2026-09-30，Q3 RPO為387億美元，高於6月30日的375億美元；它不同於新簽合同TCV，不能證明任何單一買方的固定訂單/費用或現金收入。Q3可比1—3年交易19個月的回收期不計客戶預付款效益；不得將其再次計作現金融資證據。",
              "trigger_boundary": "一项至少10亿美元TCV的实际固定采购，达到一份已披露Q2大型新约的量级，且买方/用途、收费责任与服务窗口可识别，已足以新增一个大型付费需求承诺并改变订单组合与未来服务机会；无需再等收入确认或另一个客户重复签约。若金额只是最大可购上限、买方无明确付款责任、相同容量已计入现有合同，或只称将采购/扩大用户名单，则未跨边界。10亿美元是总合同价值比较锚，不年化；只有同代、同口径容量确实披露时才用2000万—2500万美元/MW/年作辅助比较。"
            },
            "condition_id": "C1",
            "condition_revision": "1986671abff4749b6ff7b989cb24c6566bd4ca24bb2a4d13378787ae3ad99fa5",
            "criterion": "Nebius与具名买方（或可靠报道足以识别买方及其付款保障）披露一项已签的新建、扩容或续约核心AI云专属容量承诺；公开的固定合同价值达到至少10亿美元TCV，或明确固定最低购买义务达到一份Q2大型新约的可比规模。公告说明买方用于训练或生产推理等实际算力需求、服务开始窗口以及在容量验收后应付的服务费/最低付款或预付款。承诺须是相对已有固定合同的净新增，不得只是意向书、最高购买上限、可随意取消的期权，或Meta同一集群先向第三方销售再由Meta承接的重复计量。",
            "decision": "LONG",
            "trigger_layer": "MIDDLE"
          },
          {
            "calibration": {
              "reference_state": "新签大单的当前可比单位是Q2四份平均TCV超过10亿美元；现有集中度仍显著，但匿名客户C/D/E不得映射到具名买方。Meta最高150亿美元的剩余容量机制并非同额固定订单；微软原协议全部批次已交付，不是未履约储备。客户及Nebius可多源采购，竞争者赢得订单不等于Nebius丢失订单。\n\n2026-10-31，S&P将Meta展望从稳定改为负面、维持AA−，并预测未来数年较高基础设施投资/现金消耗及可能增加借款；Meta不同意其AI变现相对同业的判断。该信用展望是已知的前瞻风险背景，不代表评级已下调、发生违约、已改变对Nebius的付款责任或已撤回合同。当前短向条件仍需公开的固定承诺实质取消/缩减或撤除；最高150亿美元的条件性剩余容量不作固定承诺。\n\n2026-10-27，Meta将Class A发行规模提高并按每股625美元定价至250亿美元，另将2.5亿份强制可转换优先股存托凭证按每份50美元定价、规模125亿美元。与Class A相关的远期出售协议预计于2027-11-19前结算、届时约249亿美元才会收到；Meta还计划不早于2027年第一季度启动600亿美元无投票权Class C ATM。故此轮融资是未来AI建设的资金支持背景，但不是全部款项已在本截止点可用的证明。Meta固定120亿美元Nebius专属容量协议和最高150亿美元的条件性第三方售后剩余容量安排在本材料中均未报告改动。结合10月31日S&P下调展望至负面但维持AA−、并预测高基础设施开支与未来借款需求，当前付款支持基线同时包含股权融资路径与较高投资/现金消耗风险；二者均不等同实际逾期或合同付款责任变化。\n\n2026-11-02，Meta安排固定收益投资者电话会，筹备至少200亿美元的多档美元/欧元债券发行以支持AI基础设施；报道称最多五档美元、六档欧元，Morgan Stanley与Citi安排沟通，发行尚未定价或完成。该计划为潜在融资支持，同时也是拟新增债务，不代表现金已到账或Meta履约能力已净改善；本次材料未披露Meta对Nebius的固定120亿美元专属容量安排或最高150亿美元剩余容量安排有任何改动，也未新增Nebius采购。\n\n2026-11-03，Meta为此前筹备的美元债完成五档合计150亿美元定价；报道所述认购订单约580亿美元，30年期一档融资成本为7.50%，穆迪给予本次新债Aa3、稳定展望。此次是定价进展而非已交割或现金到账的证明，拟议欧元部分亦未因此视为完成。它更新了AI建设融资的执行背景，同时新增长期债务及利息负担；没有报告Meta修改Nebius固定120亿美元专属容量安排或最高150亿美元条件性第三方售后剩余容量安排，也未新增Nebius采购。\n\n2026-11-04报道Meta首次欧元债销售约€82.5亿、六档且认购订单约€530亿；报道未单独确认交割或资金到账。它增加多币种融资执行的可见度，但不是截至cutoff可用现金的证明，也未显示Nebius固定120亿美元专属容量安排或条件性最高150亿美元剩余容量安排发生变化。\n\n2026-11-02，Event Library E260记录了一则归因于J.P. Morgan的跨公司估算：七家AI公司未来数据中心租赁及硬件承诺合计约4.5万亿美元、其中约15%被报告为表内债务，并称Meta约8100亿美元；这是报告中的行业汇总，不是Meta单体债务比率或Nebius新增采购。Meta于2026-10-22提交的2026-09-30 Form 10-Q分别披露约3557.3亿美元未起租租赁义务（预计于2026至2036年间开始）和4545.2亿美元不可取消合同承诺（主要涉及第三方云容量、服务器/网络、数据中心能源及Reality Labs硬件；其中约458.5亿美元和1214.1亿美元分别于2026、2027年到期）；另有最高147.2亿美元、可因云服务商转售而减少的五年云容量或有采购义务。分类及期限不可机械合并为现有债务或近期现金缺口。Meta同时报告期末现金、现金等价物和有价证券合计824.5亿美元，并预计可用资金及经营/融资现金流足以满足未来12个月及其后可预见期间的现金需求。故大额承诺强化买方资金负担这一风险背景，但与融资能力支持并存；该信息没有显示固定120亿美元Nebius专属容量协议变化、实际付款违约，或最高150亿美元第三方售后剩余容量已变成固定采购。\n\n截至E336披露，Nebius报告新增合同、Q3扩张与服务批次延迟，但没有披露固定采购义务取消、实质缩减或合同对价撤回。累计TCV、RPO变化及新买方名单不是合同取消的替代证据；同一Q3信息的后续报道不构成新的独立合同变化。",
              "trigger_boundary": "移除一个至少10亿美元TCV或一份同等规模主要固定批次的已承诺采购，直接否定一项大型订单，而非一般行业需求减速；该量级与Q2单份大型新约平均相当，因此无需叠加多个小客户流失、下一季使用下滑或全公司RPO重算。只削减未承诺的最高额度、项目改址后同等收费继续、期限变化但固定总对价/最低购买未下修，或竞争者取得新客户而没有Nebius合同退出，都不构成足够的SHORT。"
            },
            "condition_id": "C2",
            "condition_revision": "f788d285b3160e11671bca58cd02f3ad724c585581905db21332f4a28efcc0f6",
            "criterion": "Nebius或具名大买方披露，一份原先固定的核心AI云专属容量合同被取消、实质缩减、提前终止，或在续约/改约中撤掉了有约束力的购买义务；被移除的承诺达到至少10亿美元TCV，或相当于一份已披露Q2大型新约的主要固定服务批次。消息须显示对Nebius的固定付费容量、服务收费权或合同总对价确实减少；单纯换址/改期而保留等量可收费替代容量、未行使的最高容量选项回落、或客户在其他云新增采购而未撤回Nebius合同均不符合。",
            "decision": "SHORT",
            "trigger_layer": "MIDDLE"
          }
        ],
        "match_scope": "Nebius核心AI云大客户与AI原生客户的新专属容量合同、增购、续约、固定采购量或收费条件变更、购买选择权收缩及合同取消；覆盖Nebius或买方披露的新模型/推理容量交易、Meta特定集群剩余容量的实际分配、以及Reflection、Cohere或其他客户扩容与续约。留意合同是否超出现有固定承诺、服务何时开始、买方用途与付款支持、预付款、最低购买义务及退出/补偿权。例如，买方签下另一个大型多年期集群，或大客户把已固定的购买量改成可选额度/退出一部分，都是相关消息；公布最大容量、合作名单、预算意向、设备到场或新客户姓名本身不等于合同增量。同时留意可归属至具名大买方的大额基础设施承诺的信用评级/融资能力变化，作为付款支持的背景；单独的评级展望、风险预测或投资计划不等于Nebius合同变更、违约或现金损失。",
        "policy_id": "pol_90d7c24fb5d048b8be7bed29ec918066",
        "ref": [
          "output/work/v21/discovery/S0001.jsonl#L1",
          "context/document3/v21/d2/shell-0001.json｜AI云算力需求与商业化",
          "context/document3/v21/shared/d1/c1.md｜新签价格及转收入；合同回收与预付款",
          "context/document3/v21/shared/d1/c3.md｜驱动甲：客户采购与期限结构",
          "context/document3/v21/shared/d1/c4e_network_build.md｜大买方怎样分配容量、锁定交付窗口",
          "context/document3/v21/shared/d1/c5.md｜主题甲：2027年交付跃迁必须转为收入",
          "https://nebius.com/newsroom/nebius-signs-new-ai-infrastructure-agreement-with-meta",
          "https://assets.nebius.com/assets/a6ecfd85-a6cb-4967-8ef7-9a25bd261f9c/SHLQ226.pdf",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926094844/nbis-20260812xex99d2.htm",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465925088312/tm2525580d1_6k.htm",
          "context/document3/v21/shared/event_library.md#E109",
          "context/document3/v21/shared/event_library.md#E120",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/86/source/snapshot",
          "context/document3/v21/shared/event_library.md#E158",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/5/source/snapshot",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/76/result",
          "context/document3/v21/shared/delta.json#/reference_view_delta",
          "context/document3/v21/shared/event_library.md#E202",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/67/source/snapshot",
          "https://huggingnews.com/global-markets/metas-debut-euro-bond-sale-attracts-record-euro53b-in-investor-demand-0f97832d",
          "context/document3/v21/shared/event_library.md#E260",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/0/source/snapshot",
          "https://www.sec.gov/Archives/edgar/data/1326801/000162828026066773/meta-20260930.htm",
          "context/document3/v21/shared/event_library.md#E336",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126008/tm2629571d1_ex99-1.htm",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-20261112x6k.htm"
        ],
        "title": "AI云专属容量合同的净新增承诺与采购质量",
        "transmission": "Nebius核心AI云2026-Q2收入为5.749亿美元、6月末年化运行率30亿美元；四份Q2大型新约平均TCV超过10亿美元，新签TCV接近Q1四倍，且大部分容量对应2026年末、主要贡献2027年。合同承诺通过买方用途、付款能力、价格、期限和可退出权影响未来可售容量与收费确定性：Q2新约约70%的交易含预付款，相关预付覆盖预计capex约50%—60%，能降低建设融资缺口，但不是该比例合同价值已收现。Q2 RPO为374.906亿美元，其中36%预计在2026-07至2028-06确认，且不含原始期限一年或以下合同、包含可变对价，不能把TCV或RPO直接年化为当期收入。Q2匿名C/D/E占集团收入24%/21%/14%，客户集中仍高，但匿名编号不能对号入座，也不能用新增客户名单单独证明集中度已下降。微软原五年协议已披露全批交付，不能继续算作待交付订单；Meta新五年专属容量120亿美元计划2027年初交付，另最多150亿美元只对应特定集群向第三方销售后的剩余可用容量，不能把上限当成无条件新增订单或与第三方销售重复相加。大买方同时使用多家GPU云及自有/专用路线，故新签必须是真正的新增合同购买，而不是行业需求、最高容量上限或同池改名。\n\n2026-10-31，S&P Global Ratings将Meta信用展望由稳定调为负面、同时维持AA−评级；其预测Meta年度基础设施支出到2028年可达2520亿美元、未来三年出现自由现金流赤字、2027年现金消耗峰值为710亿美元、杠杆到2027年可能超过1.0倍并可能于2028年借款弥补现金流缺口。Meta表示不同意S&P关于其AI投资变现能力弱于同业的判断。此为评级机构的信用/投资预测更新，不是实际降级、违约/逾期或NBIS合同及付款条件变化；本次材料没有报告120亿美元固定专属容量协议被修改，也没有把最高150亿美元第三方售后剩余容量变成固定订单。该事实纳入买方付款背景，但不单独证明订单流失或Meta无法履约。\n\n2026-11-03，Meta的150亿美元美元债发行已完成定价（五档，订单约580亿美元，30年期一档融资成本7.50%；穆迪对新债评级Aa3、展望稳定），但截至本截止点没有交割/资金到账确认，拟议欧元部分也不视为完成。该进展改变买方融资执行背景而非Nebius合同事实；新增长期债务与资金来源的可用性须分别看待，不能据定价推定Meta对Nebius的固定付款责任或容量采购已经改善。\n\n截至2026-11-12，Nebius披露Q3 AI Cloud收入9.738亿美元；自Q3开始客户已承诺超过120亿美元合同价值。Q3同口径加权ACV/MW超过2,500万美元（Q2超过2,000万美元），可比1—3年交易预计回收期由22个月缩短至19个月；Q3超过90%的合同价值含预付款，超过一年协议的预付款覆盖相关Q3 capex超过100%。公司还披露Databricks、Datadog和Snowflake多年度AI Cloud协议，但未披露条款/金额；五年期交易为既有客户扩容、新增容量。以上是Q3组合与公司披露的现时基线，不把组合TCV、RPO、预付覆盖或未具名客户拆成单一买方固定采购义务。\n\n截至2026-09-30，Q3 RPO为387亿美元，高于6月30日的375亿美元；它不同于新签合同TCV，不能证明任何单一买方的固定订单、服务费或现金收入。Q3可比1—3年交易的19个月回收期不计客户预付款效益；不得将其再计作现金融资证据。"
      },
      {
        "activation_conditions": [
          {
            "calibration": {
              "reference_state": "截至2026年8月，公司FY2026 capex指引200—250亿美元；H1集团投资后现金差额-36.262亿美元，但不是核心云单项目现金缺口。Q2新单约70%含预付款及50%—60%相关capex覆盖、22个月预计回收为总体/管理层口径，不能移作任一未来项目的既有比例。常态扩张包含客户款先收、未来服务与设备/场址支出后发生。\n\n2026-11-12更新后的公司/组合基线：FY2026 capex约270亿美元；FY2027 capex初步指引400亿—450亿美元。Q3超过一年协议的预付款覆盖相关Q3 capex超过100%，Q3超过90%合同价值含预付款，FY2026预付款承诺约100亿美元；管理层预计FY2027预付款及基于投资级合同的融资覆盖75%—85% capex（FY2027预付款预测170亿—200亿美元）。这些都不映射到单个建设阶段，未公开单笔已收款、释放日期、账单与可回收责任；不把公司的比例覆盖表述成所有capex已获资金。\n\nQ3合併經營現金流與固定資產/設備/無形資產現金購置分別為負2.73億美元和73.004億美元；九個月數據為正42.311億美元和154.307億美元。這些期間合計數不能指出任一項目的資金來源或到期日，也不能替代階段特定的資金/責任比較。\n\nE340所载基线：截至2026-09-30，VIE相关账面价值/未扣回收保护的最大损失敞口为14.624亿美元；10月1日另垫付2.306亿美元，不并入9月30日数值。最高6.622亿美元后续融资仅属酌情、非合同义务。VIE贷款是对外应收资产而非NBIS新增债务；未证明有现金已返还或项目级可用资金。",
              "trigger_boundary": "比较该阶段支出发生前实际可用资金及不可撤回现金责任。普通服务期最低付款可以保障未来回款，却只有已收到/可在建设节点释放的预付、可融资确定安排或伙伴直接承担支出时，才降低建设前现金峰值，不能将两者混算。正式内部排序也不因项目有客户就充分，须识别重要可回收保护、可用资金或可交付阶段相对原路径的独立改善。无需100MW/十亿美元/半数capex或提前整季，亦不等实际投运；但阶段标题或小额保护不代替经济分量。只取消未承诺扩张而没有上述变化，或只增加未来收入，不通过本入口。 对VIE相关回款，只有实际已收、未被抵销/托管/用途限制占用、且相对现有敞口有实质净改善时才计为正向；最大损失敞口、应收票据账面值、或未提款的酌情融资意向都不是可用建设资金。"
            },
            "condition_id": "C1",
            "condition_revision": "408a48eee2168f3043a996fd32b9823ea848f4101e6f3a9542a4ac7cf0038aff",
            "criterion": "Nebius披露一项针对重要、可识别商业建设/服务阶段的有约束力资本或付款安排，实际改变其建设时点自有现金暴露、可回收性或交付可执行性：例如客户预付款已收/按建设节点可用，确定可融资款能在所需支出前动用，伙伴直接支付相关建设款，或不可撤回支出取得实质退款/抵租保障；也包括集团专用数据中心VIE的票据实际偿还、租赁预付款/押金释放，且净回款在重要阶段支出前已可用并确实减少Nebius的资金暴露。须保留可执行服务路径；普通把未承诺预算转投客户项目、未来服务期take-or-pay或总capex调整本身不足。",
            "decision": "LONG",
            "trigger_layer": "INNER"
          },
          {
            "calibration": {
              "reference_state": "Nebius当前扩张路径资本密集，Q2集团资产现金购置和FY2026 capex指引显示释放可用资金可能有实际时点价值；但公司没有公开成熟AI站点售后租回的项目现金明细。服务权转给租赁结构不会自动消除能源、维护、更新或Nebius仍负的客户SLA义务。当前核心客户期限多为1—3年，较短合同也有3—6个月，资产可在合同后再服务其他客户，故租期较长本身不代表负回报。\n\nQ3新的组合级预付覆盖与FY2027融资预测已进入当前资金背景，但未披露成熟设施售后租回净所得、租金/维护更新责任、具体阶段项目现金桥或资产出售。较大的集团capex、合同预付或融资预测均不替代一项已签sale-leaseback的实际净交换。",
              "trigger_boundary": "出售把近端自有资本换成后续固定责任。足够的正向须能用公开净所得与对应服务范围、原/新固定及更新义务作有意义比较，说明重要资金需求得到释放/保护且主要经济交换改善，例如保留原主要服务现金和使用权的同时，新增租金/更新承担相对所释放资本及原已承担责任并未构成主导不利。金额有上限只是可测，不等有利；已知高租金、低净所得或不利控制权不能忽略。无需每期客户款全覆盖、完整私人全寿命IRR或所有未来买家，但只有净所得、有限租金而无法比较后续代价时，方向仍未建立，不以近端现金流入自动LONG。"
            },
            "condition_id": "C2",
            "condition_revision": "236a35db71831a1c91106af9836e0b65e1d4481b6608a2257510c2e299089509",
            "criterion": "Nebius完成具有约束力的成熟AI/HPC设施/服务阶段出售并租回，披露扣除债务、留置与费用后的可用净所得、保留服务权，以及租金/调价、维护更新和退出责任；这些可比安排显示重要资本现金得到释放，同时保留的服务现金/使用价值未被同包新增固定或更新责任主导抵销。不能仅凭净现金为正、义务有上限或服务连续就判有利；账面收益和gross proceeds不足。",
            "decision": "LONG",
            "trigger_layer": "INNER"
          },
          {
            "calibration": {
              "reference_state": "公司整体capex仍处快速扩张，H1集团现金流和资本购置有较大时间差；Q2约70%新单预付和相关项目50%—60%capex覆盖并不证明每个项目预付款可退或已收。已知合同、预付、未承诺扩建的取消可以改变客户采购价值或减少未来支出，这两者不可误认为同一笔现金损失。\n\nQ3披露显示组合预付款覆盖提高，但没有识别某个已取消或缩减阶段的退款、抵租、设备采购款没收或未补偿固定补款；组合基线不证明单项目款项可退或不可回收。\n\nVIE回收的当前基线：截至2026-09-30最大损失敞口14.624亿美元（未扣担保、抵押、抵销等保护），未计提票据信用损失准备且票据公允价值约等于账面；其中部分应收有设施/股权抵押和VIE母实体保证、部分租赁余额可抵销票据，另有余额无担保/无保证。10月1日再垫的2.306亿美元为后续单独发生的有担保票据；最多6.622亿美元计划为可酌情、非必需出资。披露没有显示已发生逾期或减值。",
              "trigger_boundary": "当一个已具体识别的商业阶段被取消/缩小后，正式条款或公司/伙伴披露显示一项已承诺现金不能退回、不能抵租/转用于其他部署，或出现新的固定补款/终止责任，且金额/范围对应一个实际建设/设备阶段，而非小额行政费用时，新增现金下行已成立；无需等财报正式冲销或知道完整项目IRR。单纯客户TCV撤回由通用合同Policy处理，不因同一合同额再SHORT；若取消未承诺投资反而释放现金、无费用或沉没金额可回收，也不命中。 对VIE分支，以已披露的敞口/保护结构作为逐项比较锚：当一项可识别的实质余额进入逾期/实际信用损失，或其原可执行抵销、抵押、保证被实质削弱且无替代回收来源时，即可在正式核销前判断新增损失风险；不要求先证明整个14.624亿美元全损。现有无担保余额本身、总敞口面额、10月1日已披露且有担保的垫款，或尚未承诺的6.622亿美元，不自动SHORT；若未来新增垫款成为合同义务，仍须比较其可执行租赁抵销/回收保护、建成后服务价值与现金占用，不以融资金额单项定方向。"
            },
            "condition_id": "C3",
            "condition_revision": "aedd41364e3b09c5a176241257e8fa8813c309a2d32881548fefb0f5f531f66b",
            "criterion": "Nebius或具名项目的合同/融资披露首次确认，已取消、缩小或退出的一个特定建设/服务阶段留下新的、不可收回或不可重新配置的现金责任，例如已付预付款失去抵租/退款、GPU或设施采购款被没收、不可取消租金/终止款/工程押金继续到期，或必须补付项目成本；若同一消息也取消客户固定采购，本条件只观察超出该采购/TCV消失本身的项目现金责任。 对上述集团专用数据中心VIE的项目应收资产，若公开披露现存票据/租赁预付款的实质金额逾期、减值或核销，或可执行的租赁抵销、抵押、保证/追索等主要回收保护被撤销，致重要余额不再具有原有可回收性，也符合本SHORT；如后续资金支持成为新的合同义务，只有它造成重要、无充分抵销/回收保护且无相称服务价值的净暴露增加时才按该SHORT判断；单纯酌情融资计划或垫款金额本身不计。",
            "decision": "SHORT",
            "trigger_layer": "INNER"
          },
          {
            "calibration": {
              "reference_state": "售后租回可能降低当期自有资本占用，也可能将设施所有权换成多年固定付款；现有材料没有Nebius成熟AI设施交易的租金、处置净额、剩余设备更新或项目现金桥。核心客户合同通常1—3年，短约3—6个月；但供给资产可以服务续约/后续客户，故仅有长租期、没有每期客户最低款覆盖或未披露完整IRR都不自动代表亏损。\n\n截至2026-11-12的新披露更新了capex及预付款资金背景，没有披露Nebius出售并租回成熟AI设施的交易、该类设施租金或持续维护/更新责任。继续按具体交易条款比较净所得、服务权与责任，不从融资比例推断sale-leaseback经济性。",
              "trigger_boundary": "最早充分的SHORT是交易条款本身已经使一个被出售的已服务/已承诺阶段出现可执行的现金错配或控制缺口：例如净出售款被债务/托管等占用而该阶段仍要支付较高且不可取消的租金、更新/修复/保证责任，或NBIS继续承诺客户SLA却失去在服务窗内控制/使用所需设施与电力的权利；已公开的租金/阶段现金安排足以显示这类负担并非短期收入时点移动。无须等待资产经营失败或重建私有全周期IRR，但单纯租期超过一份1—3年客户合同、未逐笔匹配客户最低款、或正常设备维护责任均不足。"
            },
            "condition_id": "C4",
            "condition_revision": "4d812095a3e0d9abf148d3cd9d6514464dcbb40b01c5c0b8cb605bb6077d3284",
            "criterion": "Nebius出售仍用于客户AI/HPC服务的成熟设施或容量并租回，或签署相似长期使用安排；具有约束力的条款显示其出售资产后仍须承担新增/持续的固定租金、能源、维护更新、恢复、保证或退出付款，并且这些义务已经实质脱离可用服务权或被保留的项目现金。",
            "decision": "SHORT",
            "trigger_layer": "INNER"
          }
        ],
        "match_scope": "匹配Nebius对自建/共址项目、未承诺扩建、GPU/设施采购、客户预付款与托管释放、资本重排、阶段取消/延迟、已收款退款/抵租、沉没支出、售后租回及相关租金/维护/刷新/终止责任的正式决定和合同披露。代表情景包括重要客户阶段取得可用建设资金或实质可回收保护、首笔建设款或预付款改为按验收释放、取消项目后首次披露预付或GPU订单不可回收、以及成熟场址出售并租回以换取可用现金。纯季度FCF/EBITDA变化、仅新增客户TCV、融资证券发行/保证法律追索、一般capex指引或单纯账面收益可召回，但不自动判断项目经济性。 也匹配Nebius为集团专用数据中心VIE提供的应收票据、租赁预付款/押金、建设资金支持及其收回安排；观察实际垫付或偿还、租赁抵销权、抵押/保证保护、逾期/信用损失和新增不可撤销资金义务。",
        "policy_id": "pol_c653295f02044bb29ff7f81a682a0930",
        "ref": [
          "output/work/v21/research/S0002/0029/policy_project_capital_ordering.json",
          "output/work/v21/research/S0002/0029/policy_existing_powered_site_transfer.json",
          "output/work/v21/research/S0002/0027/policy_external_capacity_contract_economics.json",
          "output/work/v21/research/S0001/0009/policy-capacity-contract-quality.json",
          "context/document3/v21/shared/document2.json#/shells/1/units/2",
          "context/document3/v21/shared/document2.json#/shells/1/units/3",
          "context/document3/v21/research_context/main_results.json",
          "https://www.sec.gov/Archives/edgar/data/2070542/000121390026105865/ea030737801ex99-1.htm",
          "https://www.sec.gov/Archives/edgar/data/2070542/000121390026107421/ea030792301ex99-1.htm",
          "context/document3/v21/shared/event_library.md#E336",
          "context/document3/v21/shared/delta.json#/reference_view_delta",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126008/tm2629571d1_ex99-1.htm",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-20261112x6k.htm",
          "context/document3/v21/shared/event_library.md#E340",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-ex99d2.htm"
        ],
        "title": "项目资本排序、预付款保障与售后租回现金责任",
        "transmission": "项目资本排序决定资金先被锁在哪条容量路径、客户付款和融资保护能否抵消建设前的现金暴露，以及公司是否仍能在承诺窗口交付。当前FY2026 capex指引为200—250亿美元；H1集团CFO为45.041亿美元、固定及无形资产现金购置为81.303亿美元，投资后差额约-36.262亿美元，其中递延收入现金流贡献43.950亿美元。Q2新单约70%含预付款，管理层称预付覆盖相关项目capex约50%—60%，22个月是尚含未建容量的预计回收，历史预计区间为24—36个月。它们说明现金时序和合同保护在扩张中有实际分量，但都是公司/集团汇总及管理层估计，不能直接当成每个项目的旧条款或实际回报。\n\n公司核心容量服务合同通常约1—3年，短约约3—6个月；设备/场址租赁和售后租回可能更长，但长于一份客户合同本身不代表亏损，因为资产可能服务后续客户。明确的付款、托管、取消权、供应商预付款、租赁起算、维护更新和可转移服务权，能在完整项目利润尚未知时改变近期现金峰值或现金下行。售后租回将当期资产变现换成租金和保留服务责任；评估对象是实际净现金释放、持续服务权以及已知阶段内新增固定/更新义务的组合，而非毛收入、会计收益或假设的完整项目内部收益率。此Policy只覆盖NBIS项目资金和资产责任，不重复客户固定合同总额变化、集团证券融资条款或项目能源价格。\n\n2026-11-12的Q3股东信将FY2026 capex展望更新为约270亿美元（早前为200亿—250亿美元），初步FY2027 capex为400亿—450亿美元。公司称Q3超过一年协议的预付款覆盖其相关capex超过100%（Q2为50%—60%），超过90%的Q3合同价值含预付款；管理层预计2027年预付款及基于现有投资级客户合同的融资覆盖当年capex的75%—85%，并预计FY2027客户预付款为170亿—200亿美元。这些公司/组合级历史值和前瞻指引改变融资基线，但不是每个未来项目的已收、可释放现金或项目级成本/退回条款。\n\nQ3持续经营现金流为负2.73亿美元，固定资产、设备及无形资产现金购置为73.004亿美元；前九个月分别为正42.311亿美元及154.307亿美元。这些是公司合并层面的期间流量，不是项目阶段的净现金需求、付款日程或发票到期前可用资金。\n\n2026-11-12的Form 6-K附注（Event Library E340）补充了集团专用数据中心VIE的资本回收基线：截至2026-09-30，Nebius对两家VIE的相关资产账面价值及未扣除抵押、担保、抵销权的最大损失敞口均为14.624亿美元；截至该日票据未计提信用损失准备，票据公允价值约等于账面价值。2026-10-01，公司在修订后的担保票据协议下另向其中一家VIE垫付2.306亿美元；另称有意向最多提供6.622亿美元用于完成建设，但未来垫款由公司单方酌情决定、无合同义务，未提款额不属于现有最大损失敞口。部分票据和租赁预付款有VIE子公司股权质押、设施抵押及VIE母实体保证，且其中一家VIE的剩余租赁义务足以抵销其票据；另一家VIE的2.494亿美元租赁预付款有抵押/保证，而仍有其他余额无担保、无保证。VIE并非由Nebius控制。故将这些项目视为分层的项目资本/应收款风险：最大敞口不是已发生损失，已垫款不是Nebius新增借款，未提款意向不是承诺支出；只有净可回收性、可用现金或有约束力的资金责任实际改变时才重判。"
      },
      {
        "activation_conditions": [
          {
            "calibration": {
              "reference_state": "截至2026-10-17，Lappeenranta 310MW园区首期70MW变电站目标为2027年春；Caverion合同和项目页说明2026年7月实施、2027年春调试。Mäntsälä新建70MW数据中心及70MW配套站目标为2027年秋。公司年末connected目标为800–1,000MW；5GW contracted不是建成或可服务容量。\n\nBéthune首期60MW的最新可读报道目标为2026年12月开放；DCD在2026年2月曾转述Nebius CCO称首期于夏末上线、年底约半园区运营。报道没有提供可比的阶段验收、实际接电或可服务时间，不能把12月目标当作已确认的交付约束解除或已发生的前移。\n\nCLT1是另一条具名路径：AIB/Nebius公布50MW 关键IT托管协议（两座各25MW机房），AIB于2026-11-07估计两座机房在2027-Q4交付，但这不是正式指导。独立的One Blockchain LLC—Lockhart Power电力服务协议自2026-10-01生效，目标在2026-12-15前以按商业上合理的努力首次供电，仍受既有协议终止及延误条款约束。Spartanburg County于11月3日批准开发协议第二读，11月6日议程将第三读列为暂定11月16日；截至cutoff仍非最终批准。以上是合同、供电目标和审批进度，不能当作实际首供、已集成IT、在运服务容量或收费。\n\n2026-11-12 Q3报告新增的是分站点营收/active进展（四个新站已产生收入，Q3环比active增幅最大，且新泽西已有更多批次交付）与2027上半年共址交期后移的并存状态；公司>4GW contracted power及年末5GW目标仍不是connected或active MW。Q3未披露各站的MW、送电/IT/客户验收分量，不能用组合表述为具体新园区关键障碍已解除。",
              "trigger_boundary": "Lappeenranta首期70MW目标2027年春、新Mäntsälä70MW目标2027年秋，是来源实际给出的精度，不自行补日期。可信更新若使重要供电部分实质较早可用，或落实原先仍未定的关键并网安排而改变主要交付概率，可以在原宽窗口内有信息。跨到另一年/半年/季节是比较线索，不是完整提前时长或充分分量证明；相邻窗口一两天不自动重要。应定位同一阶段、关键约束和有效送电时间的真实改变，不要求编出精确旧日程或把额定MW换算成active IT/收入。只有原目标照常推进而无关键增量，不自动LONG。"
            },
            "condition_id": "C1",
            "condition_revision": "da0d802b16200592949d156c11c83da626d4fbfd42a319b62dbdc4000533a333",
            "criterion": "Nebius、施工/供电方或具名场址伙伴，以有依据的正式安排或进度更新，确认一项已公布、对扩容有分量的变电站/并网阶段实质提前，或此前未解决的主要接电关键路径已被可执行安排解除，使该阶段按公开时点可用的概率实质提高。可覆盖70MW电站中的有分量可用部分，不要求整个法律/建设阶段全部完成；普通开工、目标重述、按计划机械验收或季节标签边缘变化不满足。",
            "decision": "LONG",
            "trigger_layer": "MIDDLE"
          },
          {
            "calibration": {
              "reference_state": "Lappeenranta首期70MW变电站原目标2027年春；Mäntsälä新70MW楼配套70MW变电站原目标2027年秋。对应公司connected目标为2026年末800–1,000MW，随后逐步active于2027年上半年；已知排期未给具名客户验收日期或IT负荷。\n\nBéthune的DCD 2026-02报道（归于Nebius CCO）称首期夏末上线、年底约半园区；2026-10-26报道则称首期60MW拟于12月开放。两项报道未证明相同阶段/容量口径，也未给出该阶段的可计费服务时间；将其保留为未核清的公开排程差异，不能只凭日期不同直接认定新的实质延期。若可靠来源确认同一重要阶段的当前计划被移出其适用窗口，仍可在实际失约前判断，不需等容量失败或财报。\n\n2026-11-04，Aterio估计DataOne/Vineland约140MW规划容量可能延至2027，并称原发电机许可受阻、改用燃料电池后遇审批延误。此为第三方分析而非项目方对同一重要阶段的正式改期；未给出具体阶段、有效送电/生产可用状态，亦未连到Microsoft已交付批次。将其作为风险线索而非已确认延期，不据此认定C2已触发。\n\nCLT1是另一条具名路径：AIB/Nebius公布50MW 关键IT托管协议（两座各25MW机房），AIB于2026-11-07估计两座机房在2027-Q4交付，但这不是正式指导。独立的One Blockchain LLC—Lockhart Power电力服务协议自2026-10-01生效，目标在2026-12-15前以按商业上合理的努力首次供电，仍受既有协议终止及延误条款约束。Spartanburg County于11月3日批准开发协议第二读，11月6日议程将第三读列为暂定11月16日；截至cutoff仍非最终批准。以上是合同、供电目标和审批进度，不能当作实际首供、已集成IT、在运服务容量或收费。\n\n2026-11-05 E274：Jim Cramer分享的公开资料梳理识别了NBIS相关地点的潜在许可问题，报道点名Highridge存在许可缺口，并重述Vineland房东的发电机许可执法。其依据是公司/房东披露、SEC文件及地方记录，但不是对所有项目许可不足的正式认定，且明确未列出或未知的许可不等于缺失。Highridge的可用计划锚仍为Data Center Dynamics 2026-07-27报道的首期260MW、预计2027年10月投入运营；目前材料未把任何待办/未提交许可映射到首期必要路径，也没有该目标被修改的确认。Vineland发电机许可状态已由独立的Vineland Policy覆盖，E274对其公开提及不构成新的执法或供电状态变化。\n\n2026-11-03地方选举后，Event Library reference-view delta记录Ballotpedia称40/41项本地数据中心措施偏向限制，并记录AP/Redbud对完成公投的另一计数为44/45。不同跟踪口径与司法辖区不能合并为NBIS设施结果；本轮delta未将这些措施匹配到Highridge或另一具名NBIS站点，也未显示其成为适用规则。该结果提高对当地开发许可环境的关注，但仍只是广泛政治背景，不是公司项目延期或许可撤回。\n\nE336已披露部分共址项目延至2027上半年、包括新泽西余下批次；公司未取消容量建设并称客户计划获妥善调整。没有给出任何特定新园区的connected/active MW、同批设施状态或与公司原窗口对齐的具体负荷，不能从集团收入/交付指引或日期推定具名电力节点发生失约。",
              "trigger_boundary": "按原春/秋等真实精度比较重要供电阶段的新旧路径。推迟到2027年秋或2028年可能表明显著后移，但必须有同阶段实际时间/供应约束变化，不因窗口不重叠就假定延满一季。原窗口内已识别主要关键路径失败、重要供电部分不能按所需阶段使用，也可先降低兑现概率，无需等原窗口全部过去、客户验收或财报。普通日程微调不足，不把一站变化外推所有容量。\n\n对Béthune，仅有较早夏末目标与较新12月首期60MW报道尚不足以证明可比阶段已延误；也不要求等到12月结束，若后续可信公开更新明确同一阶段的最新排程变化及其容量/服务影响，即可按该新事实判断。\n\nHighridge首期按已报道的2027年10月/260MW目标单独观察。若有权许可机关拒绝该首期必需许可、对该阶段下达有效限制，或可靠项目方更新显示必需审批已实质改变其原时间窗口，可据此判断C2，不必等容量原定投运后再确认。单纯从Cramer梳理或许可清单看到未办/待办项目，不知道其是否为首期必要审批、是否影响该窗口时，不越过边界。"
            },
            "condition_id": "C2",
            "condition_revision": "3cbda1b6daf8ef3303b62c4c5c91564b9737e0ec27c46268f82b2c9fb2effc04",
            "criterion": "Nebius、具名客户/设施方或有权限主管机关以可信公开信息确认，一项已公布且有分量的容量阶段因变电站/并网、必要土地开发或环境许可、施工主路径等约束而实质延后、取消、缩减，或确认此前未解决的关键约束已显著削弱原可用路径。必须定位受影响阶段及其时间/服务影响；仅有待审、未提交或清单不完整的许可状态、泛称审批风险或一般政治/行业讨论，不足以单独认定阶段路径已转弱。",
            "decision": "SHORT",
            "trigger_layer": "MIDDLE"
          },
          {
            "calibration": {
              "reference_state": "截至2026-08-12，公司维持2026年末800–1,000MW connected并把其active窗口放在2027年上半年；Meta第二协议相关容量计划从2027年初上线。connected与active是不同阶段。芬兰新站目标分别指向2027年春/秋，不能从70MW电站额定值推算客户可用容量。\n\nBéthune首期60MW被报道计划于2026年12月开放，但报道没有证实设施已完成集成、供电或可供常态客户生产使用。开放计划不等同于connected、active、客户验收或计费。\n\nCLT1是另一条具名路径：AIB/Nebius公布50MW 关键IT托管协议（两座各25MW机房），AIB于2026-11-07估计两座机房在2027-Q4交付，但这不是正式指导。独立的One Blockchain LLC—Lockhart Power电力服务协议自2026-10-01生效，目标在2026-12-15前以按商业上合理的努力首次供电，仍受既有协议终止及延误条款约束。Spartanburg County于11月3日批准开发协议第二读，11月6日议程将第三读列为暂定11月16日；截至cutoff仍非最终批准。以上是合同、供电目标和审批进度，不能当作实际首供、已集成IT、在运服务容量或收费。\n\nQ3公布四个新站已开始产生收入、active容量环比增加创高，但没有披露新泽西批次/其他站点的connected到active MW映射；既有connected、active年度总量和客户可服务窗口仍依原口径逐批核验。",
              "trigger_boundary": "800—1000MW年末connected随后2027年H1逐步active、Meta早2027是宽基线，不能当每一客户精确验收日。重要生产阶段相对其自身路径的实质提前，或可信生产资格验证解决了此前影响该阶段的主要集成/可用性不确定性，可在原H1/早2027窗口内改变判断；普通按窗首次开放未必有此增量。只需有业务分量的可用服务部分，不需全网目标、精确IT MW或客户订单齐套；不得从70MW变电站容量推出客户服务量。"
            },
            "condition_id": "C3",
            "condition_revision": "2502913dfd40f3ee1a6be3c65bf1c015f939837d7d953afcb02baa9c44fd7228",
            "criterion": "Nebius或具名客户确认，一个与已公布扩容或主要客户部署有关的有分量容量阶段，已完成集成并取得可供常态生产使用的资格；其实际可用时点实质提前，或这项结果解除此前尚未解决的重要生产约束、显著提高相关服务路径兑现概率。不能把接电、样机/单机上线或按原安排的普通首次开放本身当充分，也不要求全批/全网同时上线。",
            "decision": "LONG",
            "trigger_layer": "INNER"
          },
          {
            "calibration": {
              "reference_state": "已知全公司目标是2026年末800–1,000MW connected，随后在2027年上半年逐步active；Meta首批相关容量为早2027；5GW签约不是active基数。现有公开材料没有逐站IT量、内部阶段排程或客户验收清单。\n\nBéthune首期60MW的12月开放仅为2026-10-26报道中的计划；未提供实际延期、缩减或某项设施/集成约束已影响可服务窗口的证据。不能从初始夏末目标与该较新报道直接推定相同阶段延误或损失。\n\n2026-11-04 Aterio估计DataOne/Vineland约140MW规划容量可能延至2027，并称燃料电池路线后仍遇审批延误；该预测未识别对应的Nebius可服务阶段、客户批次或可收费窗口，且尚非项目方正式确认。不得将规划容量预测视为已发生的服务改期或损失。\n\nCLT1是另一条具名路径：AIB/Nebius公布50MW 关键IT托管协议（两座各25MW机房），AIB于2026-11-07估计两座机房在2027-Q4交付，但这不是正式指导。独立的One Blockchain LLC—Lockhart Power电力服务协议自2026-10-01生效，目标在2026-12-15前以按商业上合理的努力首次供电，仍受既有协议终止及延误条款约束。Spartanburg County于11月3日批准开发协议第二读，11月6日议程将第三读列为暂定11月16日；截至cutoff仍非最终批准。以上是合同、供电目标和审批进度，不能当作实际首供、已集成IT、在运服务容量或收费。\n\n截至2026-11-12管理层将部分共址交付安排移至2027上半年，且新泽西剩余批次在内；该状态已进入基线。没有公布站点级active服务量或同批可用时点，因此重复报道不构成新的服务窗口后移或容量损失。",
              "trigger_boundary": "重要阶段移出原公开服务窗口可以说明后移，但半年/季度边界不是经济分量或固定时长。原窗口内关键可用条件失败且已实质影响重要服务时段/范围，也可降低active兑现概率；应以具体阶段和来源实际日程精度比较，不能用季节交界一两天或公司H1口径补任一客户旧日期。无需收入/索赔、全站验收或净GPU清单。同一供电改期与由它直接导致的服务后移仅算同一事实，不再创造第二份容量损失。"
            },
            "condition_id": "C4",
            "condition_revision": "aab4e57c5aa2bec4cb26c7894ee7236dfcf1e2871e65a23a4083ca496b7fcb12",
            "criterion": "Nebius、具名客户或可信设施方确认，已公开关联至扩容/主要客户交付的一项重要生产容量阶段，因机电、冷却、网络/编排或系统集成问题实质推迟可用时点、缩减服务范围或显著削弱已识别交付路径。受影响可为有分量的服务部分，不限完整母阶段；仅接电状态、单台返工或无法定位阶段/用途的泛化说法不满足。",
            "decision": "SHORT",
            "trigger_layer": "INNER"
          }
        ],
        "match_scope": "匹配Nebius、Caverion等施工方、供电方、设施伙伴或具名客户关于新园区变电站、电网连接、数据中心完整阶段建造，以及从connected到生产可用的排程、送电、集成、验收、改期或取消。代表情景包括Lappeenranta首期70MW变电站和310MW园区，以及Mäntsälä新70MW楼及其独立70MW配套变电站；不能把后者与既有75MW楼混为一批。也匹配800–1,000MW年末connected容量的具名阶段转active及客户可用时点更新。一般园区上限、土地/电力合同、开工、设备到场、单次测试、5GW contracted或未说明项目/容量的新闻只是背景；电热控制、Bloom发电、GPU上游获配和产品资格由各自观察范围处理。\n\n另涵盖Béthune既有240MW园区的阶段排程；对2026-10-26报道的首期60MW/12月开放目标，区分设施开放、接电、IT/GPU集成、客户可用与active计费状态，不把阶段数字互相换算。\n\n对具名新园区也匹配当地土地开发、建筑及环境许可/批准的最终决定或可核验状态变化，但须能说明其与特定已公布容量阶段的建设或交付关键路径关系。地方投票、一般政策或社区立场只有在辖区、法律效力及NBIS项目匹配后才作为该阶段事实；异地结果或非约束性态度仅为背景。Highridge应将首期目标与该阶段所需审批、接电及现场建设分别对齐。",
        "policy_id": "pol_351a03ffc69e45ddaf14f78487992854",
        "ref": [
          "output/work/v21/research/S0002/0021/policy_site_capacity_ramp.json",
          "context/document3/v21/shared/document2.json#/shells/1/units/0",
          "output/work/v21/discovery/S0002.jsonl#L1",
          "https://www.caverion.com/newsroom/releases/2026/caverion-to-deliver-eur-15-million-substation-for-nebius-data-center-in-finland-creating-jobs-for-more-than-80-professionals/",
          "https://www.caverion.com/newsroom/releases/2026/caverion-finland-strengthens-its-position-in-the-data-centre-market-and-will-deliver-a-15-million-substation-for-ai-cloud-company-nebius/",
          "https://nebius.com/newsroom/nebius-to-construct-310-mw-ai-factory-in-finland",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926094568/tm2622968d1_ex99-2.htm",
          "context/document3/v21/shared/event_library.md#E116",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/71/source/snapshot",
          "https://www.linforme.com/tech-telecom/article/nebius-leve-encore-250-millions-d-euros-pour-son-data-center-pres-de-lille_12440.html",
          "https://www.datacenterdynamics.com/en/news/nebius-plans-240mw-data-center-in-bethune-france/",
          "context/document3/v21/shared/event_library.md#E198",
          "context/document3/v21/shared/maintenance_feed.json#/w3_coverage_gaps/0/source/snapshot",
          "https://technews.tw/2026/11/05/satellite-images-show-what-may-be-behind-microsoft-ai-buildout-delays/",
          "context/document3/v21/shared/event_library.md#E267",
          "context/document3/v21/shared/event_library.md#E268",
          "context/document3/v21/shared/event_library.md#E269",
          "context/document3/v21/shared/event_library.md#E270",
          "context/document3/v21/shared/maintenance_feed.json#/reference_view_delta",
          "https://www.datacenterdynamics.com/en/news/nebius-details-plans-for-12gw-data-center-campus-in-pennsylvania/",
          "https://www.tradingview.com/news/benzinga:7d717f85f094b:0-jim-cramer-raises-red-flags-over-coreweave-iren-and-nebius-data-center-expansion-these-are-what-i-am-concerned-about/",
          "https://news.ballotpedia.org/2026/11/06/voters-favored-limiting-data-center-development-in-40-of-41-local-ballot-measures-on-nov-3/",
          "context/document3/v21/shared/event_library.md#E336",
          "context/document3/v21/shared/delta.json#/reference_view_delta",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126008/tm2629571d1_ex99-1.htm",
          "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-20261112x6k.htm"
        ],
        "title": "新园区接电与有效服务容量爬坡的关键路径",
        "transmission": "Nebius扩容不是把签约电力直接计为云容量：2026年末5GW contracted是场址/电力管线，800–1,000MW connected仍需IT安装、网络、冷却、编排和客户上线；管理层把转为active放在2027年上半年，并计划自2027年起每年部署超过1GW。Caverion公布的Lappeenranta首期是70MW变电站，服务的园区规划总容量310MW，目标2027年春；Mäntsälä则是既有75MW场址旁新建70MW楼，另配独立70MW变电站，目标2027年秋。单个70MW电力节点相当于800–1,000MW connected目标的约7%–9%同口径接入量，但不是IT负荷或客户可用MW。重要电力阶段有效可用时点或关键约束的可信变化可先改变近期接电概率；已接电仍不等生产可用，客户服务阶段也可独立改变active时点。常规开工、按期完成、个别设备测试或仅跨过季节标签边界一天，不足以重估整段爬坡。\n\nBéthune是Nebius与Azur的240MW旧Bridgestone场址。DCD于2026-02-12援引Nebius CCO称首期预计于2026年夏末上线、年底约半个场址运营；2026-10-26 L’Informé报道的转述称首期60MW计划于12月开放。现有材料未将两个说法的同一阶段/范围、供电MW、IT负载、GPU集成或客户可计费active状态对齐，也未确认12月计划已兑现；后者是待兑现的报道目标，不能单独证明同阶段新延误或服务容量已可用。\n\n2026-11-04，Aterio估计DataOne为Nebius开发的Vineland项目约140MW规划容量可能延至2027年，并称原发电机许可路径受阻、改用燃料电池后又有审批延误。此为第三方预测/报道中的分析师说法，不是DataOne或Nebius确认的同阶段排程，也未映射至Microsoft已交付批次、connected/active时点或可收费容量；作为风险线索纳入比较，不确认服务损失或合同违约。\n\nCLT1是另一条具名路径：AIB/Nebius公布50MW 关键IT托管协议（两座各25MW机房），AIB于2026-11-07估计两座机房在2027-Q4交付，但这不是正式指导。独立的One Blockchain LLC—Lockhart Power电力服务协议自2026-10-01生效，目标在2026-12-15前以按商业上合理的努力首次供电，仍受既有协议终止及延误条款约束。Spartanburg County于11月3日批准开发协议第二读，11月6日议程将第三读列为暂定11月16日；截至cutoff仍非最终批准。以上是合同、供电目标和审批进度，不能当作实际首供、已集成IT、在运服务容量或收费。\n\n许可与地方审批是容量建设的前置路径，但必须与具体已公布阶段和原排程建立联系。2026-11-03美国地方数据中心投票总体明显偏向限制发展（Event Library reference-view delta列出Ballotpedia的40/41项及AP/Redbud的44/45项计数）；这些是不同司法辖区、法律效力和项目范围的措施，本轮材料没有把其中任何一项匹配到Nebius已识别站点。因此它们更新行业政治背景，不证明NBIS项目许可、接电或建设状态已改变。\n\nPennsylvania Highridge Business Park是Nebius拟自有运营的新园区。Data Center Dynamics于2026-07-27报道，完整园区计划为1.2GW、首期260MW预计在2027年10月投入运营；该报道目标不是已接电、已安装IT或可计费容量。2026-11-05 Cramer分享的公开记录梳理把Highridge列为存在许可缺口，并提到Vineland房东的发电机许可执法；这提示Highridge许可完整性需纳入阶段基线。它没有说明哪些审批对应首期260MW，也没有确认该阶段被否决、停工或改期；未列出或未完成的许可不能一概视作缺失，更不能直接折算为全园区延期。\n\n2026-11-12公司称Q3实现迄今最大环比active容量增幅，四个美欧新站开始产生收入，并在新泽西于Q3及截至11月交付额外批次；Q4还将继续部署其他共址阶段。公司披露已有超过4GW contracted power、仍以2026年底5GW为目标，计划每年新增超过1GW容量。与此同时，若干共址交付时间移至2027年上半年（包括剩余New Jersey批次），客户计划正在调整且管理层称未取消容量建设。签约电力、设施阶段、active可服务/计费容量和客户收入时点仍是不同口径；没有新公布的全公司connected或active MW总量。"
      }
    ],
    "watch": {
      "limit": 2,
      "used": 2,
      "items": [
        {
          "policy_id": "pol_3f969b5c37a44d2cb9bd01510ae5a9c7",
          "reason": "当前不交易，因规则仍属拟议阶段且对云端远程使用的实际限制及适用范围未明；报道指规则最快本月定案，后续披露可能补足这一关键问题。跟进最终规则是否具约束力、是否限制远程云服务及其覆盖用户范围。",
          "start_semantic_day": "2026-11-12",
          "expires_at": "2026-11-14T07:00:00+00:00"
        },
        {
          "policy_id": "pol_ac34e25398f8445380ccf57f4c4d9571",
          "reason": "跟进Nscale资金缺口规模与来源，以及此前磋商中的Nvidia担保是否转为已生效安排，或融资问题是否影响重要容量履约；若出现可执行且对应重要GPU容量的支持，或服务停止并触发客户重新采购，再按C1/C2评估。目前披露未给出缺口规模或付款期限，也未证明融资失败或服务退出。",
          "start_semantic_day": "2026-11-13",
          "expires_at": "2026-11-15T07:00:00+00:00"
        }
      ]
    },
    "pins": {
      "limit": 2,
      "used": 2,
      "items": [
        {
          "pin_id": "pin_6833a0179f29d04f636fc4d4",
          "message": {
            "body": "← Back to live feed · 1 stories across 1 day\nFriday, Nov 13, 2026\n1 story1 Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change\n AI Nov 12, 10:56 PM EST 51/38 \n Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change\nPartners in a $500 billion credit vehicle designed to finance GPU purchases for AI startups are weighing their participation in the program following the removal of founder and CEO Jensen Huang. At least one lender is reportedly considering an exit from the consortium, according to the New York Times, sparking concerns that the company's strategy of providing financial backstops to insecure customers could leave a massive hole in AI infrastructure funding.\nThe potential collapse of the lending facility follows a period of internal friction over Nvidia's growing exposure to circular financing, in which the company guarantees loans for customers to buy its own chips. These off balance sheet obligations surged to $530 billion in the quarter ending July 26. Interim CEO Colette Kress has already begun reducing the firm's risk profile, including scaling back a guarantee for an OpenAI data center in Ohio to $105 billion from an original proposed $250 billion.",
            "published_at": "2026-11-13T17:56:27.005000Z",
            "source_name": "huggingnews.com",
            "title": "Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change"
          },
          "reason": "保留贷款方据报道考虑退出英伟达5000亿美元AI融资联盟这一未确认状态，供后续融资进展解读，避免把相关确认或变化误判为首次出现。",
          "expires_at": "2026-11-14T18:01:51.926289+00:00"
        },
        {
          "pin_id": "pin_68df4fc4b665148d2fe75a94",
          "message": {
            "body": "← Back to live feed · 1 stories across 1 day\nFriday, Nov 13, 2026\n1 story1 NEWMarket Concerns Over AI Stability Jump to 87% in Latest Fed Report\n AI Nov 13, 4:08 PM EST 8/7 \n NEWMarket Concerns Over AI Stability Jump to 87% in Latest Fed Report\nThe Federal Reserve has flagged potential vulnerabilities in the financial system stemming from artificial intelligence and high asset valuations in its latest semi-annual report on financial stability. A survey of market contacts found that 87% now view AI as a salient risk to stability, a sharp increase from 50% in the spring, citing concerns over complex financing, cyberattacks and potential earnings disappointments.\nThe central bank warned that the accelerating pace of investment in AI computing could soon exceed that of the 1990s telecom cycle. Debt issuance for advanced computing reached $680B through October, and outstanding debt in the sector now exceeds $1.1T. Other obligations include $1T in future lease commitments and more than $2T in take-or-pay contracts. The report also noted that hedge fund leverage has reached its highest level since 2013, with data current as of Oct. 28.",
            "published_at": "2026-11-13T21:39:01.151000Z",
            "source_name": "huggingnews.com",
            "title": "Market Concerns Over AI Stability Jump to 87% in Latest Fed Report"
          },
          "reason": "保留报告中的AI风险关注度、行业债务与承诺规模及杠杆背景，供后续判断AI基础设施融资消息是否带来行业层面的新增压力；这些汇总数据不代表NBIS自身义务或融资收紧。",
          "expires_at": "2026-11-14T21:42:06.572616+00:00"
        }
      ]
    },
    "recent_trade_intents": [
      {
        "message": {
          "title": "CoreWeave results lift AI infrastructure stocks as demand for computing power surges",
          "body": "Published: 12:12 11 Nov 2026 EST\n\nAI infrastructure stocks rallied on Wednesday after better-than-expected results from CoreWeave reinforced expectations that demand for computing power is continuing to outpace available supply.\n\nCoreWeave (NASDAQ:CRWV)) shares jumped 26%, while fellow AI cloud provider Nebius Group (NASDAQ:NBIS)) gained 18%. Semiconductor stocks also moved higher, with Micron Technology Inc (NASDAQ:MU) up 14%, Advanced Micro Devices Inc (NASDAQ:AMD, XETRA:AMD) rising 8% and Broadcom Inc (NASDAQ:AVGO, XETRA:1YD) adding 7%.\n\nVideo 5\n\nPlayback speed\n\n1x Normal\n\nQuality\n\nAuto\n\nBack\n\n1080p\n\n720p\n\n360p\n\n240p\n\n144p\n\nAuto\n\nBack\n\n0.25x\n\n0.5x\n\n1x Normal\n\n1.5x\n\n2x\n\n/\n\nVideo 7\n\nSkip\n\nAds by\n\nThe gains followed a strong third-quarter performance from CoreWeave, which reported revenue of US$3.66 billion, up 168% year over year and ahead of analyst expectations. The company also raised its full-year 2026 revenue outlook to between US$13 billion and US$13.3 billion, from a previous range of US$12.4 billion to US$13.2 billion.\n\nIts remaining performance obligations, a measure of contracted future revenue, climbed 138% year over year to US$132.5 billion, up from US$104 billion at the end of June.\n\nManagement said qualified demand was running at roughly 10 times the company's available capacity through the end of 2027, underscoring the scale of the opportunity for providers of AI computing infrastructure.\n\nBank of America said the results demonstrated improving capacity economics, with revenue growth and operating margins both exceeding expectations. Active power capacity increased by 200 megawatts in the quarter to 1.7 gigawatts, while more than half of CoreWeave's contracted backlog is expected to begin generating revenue by year-end.\n\nThe broker also highlighted rising pricing power. CoreWeave is now signing longer-term contracts of four to five years at between US$20 million and US$25 million per megawatt, compared with around US$14 million earlier this year. Shorter-term contracts are fetching approximately US$40 million per megawatt.\n\nBank of America reiterated its Buy rating and US$140 price target, arguing that improving returns on AI inference deployments should support demand and further margin expansion.\n\nWedbush also pointed to pricing as a particularly encouraging feature of the results. CoreWeave disclosed a roughly 25% price increase in July, followed by another 10% increase later in the quarter, with further gains this quarter. Pricing for older Nvidia Ampere and Hopper graphics processing unit offerings rose between 30% and 50% in the third quarter.\n\nThe broker said the developments were consistent with conversations with smaller AI cloud providers, which suggested that payback periods on Nvidia server deployments were shortening as demand increased.\n\nHowever, the strong results have not entirely removed concerns about the cost of expansion.\n\nCoreWeave expects capital expenditure of between US$37 billion and US$39 billion this year and had almost US$47 billion of debt at the end of September.\n\nZaheer Anwari, founder of The Revacy Fund, said the results provided investors with evidence that demand for AI computing remains strong, particularly after CoreWeave shares had fallen more than 40% over the preceding month.\n\nHe cautioned, however, that securing sufficient financing to fund expansion profitably remains a separate challenge.\n\nJefferies described CoreWeave's execution as strong, highlighting improved revenue guidance, a larger backlog and financing options including customer prepayments and contract-backed debt facilities. It maintained a Buy rating but lowered its price target to US$90 from US$150, citing a lower valuation multiple.\n\nThe broader market reaction suggests investors are once again willing to back the AI infrastructure trade, with CoreWeave's results offering fresh evidence that demand for computing capacity remains strong even as questions about financing and the pace of investment persist.\n\nby Taboolaby Taboola\n\nSponsored LinksSponsored Links\n\nPromoted LinksPromoted Links\n\nYou May Like\n\nNew 2026 V6 SUV Prices Are Turning Heads This Year (Take A Look)Compare 2026 V6 SUV Models And Prices (See Options).FAQInsider.com | SUVs Learn More\")\n\nUndo\n\nCompare Nearby Pickup Truck Prices And Models (Discover Now)Compare Local Pickup Truck Listings, Prices And Driver Comfort Features (Search Now)Elitesearches.net | Pickup Trucks Learn More\")\n\nUndo\n\nIf You're Collecting Under $3950 In Ss a Month: You're Eligible for These 10 Discounts 7 Government Changes Impacting Seniors on Social Security HealthyWallet Learn More\n\nUndo\n\nSign up to receive alerts and news direct to your inbox\n\nSign up for Newsletter\n\nMost read\n\nTech\n\nSeeing Machines tipped for upside as latest...\n\n2 days, 4 hours ago\n\nHealth\n\nIambic Therapeutics wins Bank of America...\n\n1 day, 23 hours ago\n\nTech\n\nSeeing Machines sees 10m milestone as driver...\n\n2 days, 10 hours ago\n\nTech\n\nSoftBank and Grab plan US$1.85 billion...\n\n1 day, 1 hour ago\n\nWatch\n\nMeren CEO outlines Nigerian drilling push after strong quarter\n\nMeren Energy Inc (TSX:MER, STO:MER, OTCQX:MRNFF) CEO Dr Oliver Quinn spoke with Proactive's Stephen Gunnion about strong third-quarter financial results, renewed drilling activity in Nigeria and plans to accelerate operations during 2027. Meren reported quarterly EBITDA of more than $110...\n\n3 hours, 28 minutes ago\n\nExecutive Interviews\n\nHIVE Digital reports $84.9M in Q2 revenue as HPC business grows 71%\n\n21 hours, 17 minutes ago\n\nExecutive Interviews\n\nMillennial Potash intersects 132 metres of Potash mineralization...\n\n1 day ago\n\nExecutive Interviews\n\nMedicus Pharma shares rationale as CD228V becomes lead development...\n\n1 day ago\n\nExecutive Interviews\n\nAmerican Rare Earths starts Halleck Creek pilot program for NdPr...\n\n1 day ago\n\nAbout\n\nMeet the team\n\nContact\n\nT&C\n\nCookies\n\nPrivacy\n\nEditorial Standards\n\nWork for us\n\nFollow us on:\n\n© Proactive Group Holdings, Inc. All Rights Reserved.\n\nMarket Indices, Commodities and Regulatory News Headlines copyright © Morningstar. Data delayed 15 minutes unless otherwise indicated. Terms of use.\n\nlink",
          "source_name": "yahoo",
          "published_at": "2026-11-11T17:12:00Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E567",
            "E589"
          ]
        },
        "w2": {
          "policy_hit": true,
          "policy_related": true,
          "related_policy_ids": [
            "pol_ac34e25398f8445380ccf57f4c4d9571"
          ],
          "policy_ids": [
            "pol_9313ed863ee04004b67d1d8185a951d5"
          ],
          "hit_condition_ids": [
            {
              "condition_ids": [
                "C3"
              ],
              "policy_id": "pol_9313ed863ee04004b67d1d8185a951d5"
            }
          ],
          "matched_condition_ids": null
        },
        "w3_decision": {
          "decision": "LONG",
          "expectation_delta": "BofA与Wedbush将同业增长进一步连接到长期合同提价、旧代GPU涨价及较小云商回本期缩短，增加了资产型AI云持续变现和资本回收改善的证据，而非仅重复收入超预期。",
          "prior_expectation": "NBIS自身旧代新签价格上升、IREN重签提价及CoreWeave需求增长已知，但不能据单家公司表现认定行业资产回收普遍改善。",
          "reason": "本次研究提供跨供应商的定价与回收佐证，正向修订NBIS所处市场的成熟GPU变现前景；无需先出现NBIS新订单，但不将同业涨幅套用全机群，也不认为融资风险已解除。保留W2归因。已检查行情，仅用于时效与完成度：截至截止点的完整K线尚未形成明确稳定的反应完成形态，不据证券涨幅定方向。"
        },
        "direction": "LONG",
        "initiated_at": "2026-11-11T17:30:10.384841+00:00",
        "execution": {
          "output_status": "EXECUTION_ACCEPTED",
          "delivery_status": "EXECUTION_ACCEPTED",
          "intent_released": true,
          "entry_result": "FAILED",
          "filled_qty": 0,
          "error": null
        }
      },
      {
        "message": {
          "title": "Nebius 3Q Revenue Beats Estimates",
          "body": "Nov. 12 (Financial News Systems) - Nebius reports 3Q revenue of $980.8 million, which beats FactSet consensus of $907.2 million. -- 3Q adj EBITDA of $465.8 million -- 3Q operating income of $50.4 million Source: FNS - Financial News Systems: https://www.financialnewssystems.com/ This article was automatically generated for Dow Jones by Financial News Systems.",
          "source_name": "financialnewssystems.com",
          "published_at": "2026-11-12T12:11:00Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E242"
          ]
        },
        "w2": {
          "policy_hit": false,
          "policy_related": false,
          "related_policy_ids": null,
          "policy_ids": [],
          "hit_condition_ids": [],
          "matched_condition_ids": []
        },
        "w3_decision": {
          "decision": "LONG",
          "expectation_delta": "Q3营收实际为9.808亿美元，比9.072亿美元共识高约8.1%，也高于已知的9.345亿美元作者预测；调整后EBITDA为4.658亿美元，营业利润为5040万美元。这将超预期增长与实际盈利表现相连，增强了Nebius扩张转化为收入及收益的可信度。",
          "prior_expectation": "市场预期Q3营收约9.072亿美元（同比增幅已很高），但在2026年资本开支预计达200亿至250亿美元的背景下，扩容能否及时转化为持续收入和盈利仍是关键不确定性。",
          "reason": "公司自身业绩显著超过已有收入预期，并报告正营业利润，净影响偏正；尽管营业利润远低于调整后EBITDA，且这条简讯未提供现金流、指引或融资细节，尚不能消除资本强度风险。消息标示于12:11 UTC发布，距本轮cutoff仅约13秒，仍处首轮披露阶段。提交前行情核验中，消息后的5分钟柱仍在形成，未见可确认的已完成稳定重定价形态，故无价格否决；方向依据是业绩意外而非行情。"
        },
        "direction": "LONG",
        "initiated_at": "2026-11-12T12:13:07.007406+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      },
      {
        "message": {
          "title": "“Our focus remains on building a healthy, profitable business for the long term. We will continue to invest across the business — in our capacity and infrastructure build-out, in our platform and products, and in the team that takes this to",
          "body": "“Our focus remains on building a healthy, profitable business for the long term. We will continue to invest across the business — in our capacity and infrastructure build-out, in our platform and products, and in the team that takes this to market.”\n\n– Arkady Volozh, founder and CEO\n\nHighlights from our Q3 results:\n\n- Customers are paying more up front. Prepayments on Q3 agreements covered more than 100% of the related capex, including data center construction and networking, up from 50–60% in Q2.\n- Pricing continues to rise. Annual contract value on Q3 deals topped $25 million per MW, up from more than $20 million in Q2, and GPU-hour prices rose on average by more than 20% from Q2.\n- Our customer base is growing. We added more AI-native companies and signed our first agreements with leading enterprise data and AI platforms.",
          "source_name": "x.com",
          "published_at": "2026-11-12T12:12:43Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E273"
          ]
        },
        "w2": {
          "policy_hit": false,
          "policy_related": true,
          "related_policy_ids": [
            "pol_90d7c24fb5d048b8be7bed29ec918066"
          ],
          "policy_ids": [],
          "hit_condition_ids": [],
          "matched_condition_ids": null
        },
        "w3_decision": {
          "decision": "LONG",
          "expectation_delta": "Q3协议预付款覆盖相关资本开支超过100%，较Q2的50%—60%显著提高，且包括数据中心建设和网络；GPU小时均价环比上涨超过20%。这些新增信息改善了扩容融资缺口及单位变现的判断。",
          "prior_expectation": "Q2客户预付款仅覆盖相关资本开支50%—60%；此前营收超预期已单独评估，但新协议能否进一步减少外部融资依赖仍不确定。",
          "reason": "公司披露了可比的付款条件和定价改善，足以形成新增正向预期差。保留W2相关但未命中的归因：尚无具名、达到规模门槛的新固定采购。覆盖比例仅适用于相关Q3协议，不能推为全公司资本开支已获资金或预付款全部到账。已检查行情；截止点后尚无完整消息后K线可确认稳定重定价，未形成价格否决。"
        },
        "direction": "LONG",
        "initiated_at": "2026-11-12T12:14:01.734516+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      },
      {
        "message": {
          "title": "Nebius reports third quarter 2026 financial results",
          "body": "AMSTERDAM--(BUSINESS WIRE)--November 12, 2026-- Nebius Group N.V. (NASDAQ: NBIS), the AI cloud company, today announced its unaudited financial results for the three and nine months ended September 30, 2026. This press release features multimedia. View the full release here: https://www.businesswire.com/news/home/20261112208769/en/ The Company today also published founder and CEO Arkady Volozh's quarterly letter to shareholders, available on its investor relations website at https://nebius.com/investor-hub. Management will hold an earnings webcast today at 8:00 a.m. Eastern Time (5:00 a.m. Pacific Time / 2:00 p.m. Central European Time). To register, or to listen to the live audio webcast, please visit https://nebius.com/investor-hub. Q3 2026 Financial Highlights Consolidated results (1), (2) Three months ended Nine months ended September In USD $ millions September 30 30 -------------------------- --------------------------- 2025 2026 Change 2025 2026 Change --------------------- ------- ------ --------- ------- ------- --------- Revenues 146.1 980.8 571% 302.1 1,962.1 549% --------------------- ------- ------ --------- ------- ------- --------- Adjusted EBITDA / (loss) (5.2) 465.8 n/m (79.9) 831.5 n/m --------------------- ------- ------ --------- ------- ------- --------- Net income / (loss) from continuing operations (119.6) (37.5) -69% 278.6 393.3 41% --------------------- ------- ------ --------- ------- ------- --------- Adjusted net income / (loss) (100.4) 90.9 n/m (273.7) (42.6) -84% (1) The following measures presented in this release are \"non-GAAP financial measures\": Adjusted EBITDA / (loss) and Adjusted net income / (loss). Please see the section \"Use of Non-GAAP Financial Measures\" below for a discussion of how we define these measures, as well as reconciliations at the end of this release of each of these measures to the most directly comparable U.S. GAAP measures. (2) Results include consolidated financial results of: Nebius AI cloud, the core AI cloud business; Avride, an autonomous vehicle platform; and TripleTen, an edtech service. In Q2 2025 following the completion of a third-party investment transaction in Toloka, an AI development platform, Nebius ceased to hold majority voting power in Toloka and no longer includes Toloka's results in Nebius' consolidated financial statements and reports its stake as an equity method investment. Toloka's results for prior periods have been reclassified to discontinued operations. Operating costs and expenses Three months ended Nine months ended September In USD $ millions September 30 30 -------------------------- --------------------------- 2025 2026 Change 2025 2026 Change --------------------- ------- ------ --------- ------- ------- --------- Cost of revenues 42.9 219.7 412% 97.7 457.1 368% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of revenues 29% 22% 32% 23% --------------------- ------- ------ --------- ------- ------- --------- Product development 44.9 118.6 164% 124.2 377.0 204% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of revenues 31% 12% 41% 19% --------------------- ------- ------ --------- ------- ------- --------- Sales, general and administrative 89.5 237.4 165% 218.6 555.1 154% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of revenues 61% 24% 72% 28% --------------------- ------- ------ --------- ------- ------- --------- Depreciation and amortization 99.0 354.7 258% 223.3 826.4 270% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of revenues 68% 36% 74% 42% --------------------- ------- ------ --------- ------- ------- --------- Total operating costs and expenses 276.3 930.4 237% 663.8 2,215.6 234% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of revenues 189% 95% 220% 113% --------------------- ------- ------ --------- ------- ------- --------- Total share-based compensation expense 26.1 58.7 125% 58.3 196.5 237% --------------------- ------- ------ --------- ------- ------- --------- as a percentage of operating costs and expenses 9% 6% 9% 9% --------------------- ------- ------ --------- ------- ------- --------- Selected consolidated cash flow data In USD $ Three months ended September millions 30 Nine months ended September 30 ----------------------------- ------------------------------ 2025 2026 Change 2025 2026 Change --------------- -------- --------- -------- --------- ---------- ------- Net cash provided by / (used in) operating activities -- continuing operations (80.6) (273.0) 239% (432.4) 4,231.1 n/m --------------- -------- --------- -------- --------- ---------- ------- Purchases of property and equipment and intangible assets (955.5) (7,300.4) 664% (2,010.0) (15,430.7) 668% Outstanding Shares The total number of shares issued and outstanding as of September 30, 2026 was 292,902,405, including 259,597,352 Class A shares and 33,305,053 Class B shares, and excluding 29,138,539 Class A shares held in treasury. Earnings webcast Nebius Group will host a conference call and earnings webcast at 8:00 a.m. Eastern Time (5:00 a.m. Pacific Time / 2:00 p.m. Central European Time) on November 12, 2026 to discuss these financial results. To register to participate in the conference call, or to listen to the live audio webcast, please visit Nebius's Investor Relations website at https://nebius.com/investor-hub. A replay will be available on the same website following the call. About Nebius Nebius, the AI cloud company, is building the full-stack platform for developers and companies to take charge of their AI future -- from data and model training to production deployment. Founded on deep in-house technological expertise and operating at scale with a rapidly expanding global footprint, Nebius serves startups and enterprises building AI products, agents, and services worldwide. Nebius Group also includes Avride (a leading developer of autonomous vehicles and delivery robots) and TripleTen (a leading edtech platform reskilling people for careers in tech), and owns equity stakes in other companies including ClickHouse and Toloka. Nebius is listed on Nasdaq (NASDAQ: NBIS) and headquartered in Amsterdam. For more information, please visit www.nebius.com FORWARD-LOOKING STATEMENTS This document contains forward-looking statements that involve risks and uncertainties. All statements contained or implied other than statements of historical facts, including, without limitation, statements regarding our business plans, market opportunities, capacity buildout plans, capital expenditure requirements, financing requirements and projected financial performance, are forward-looking statements. In some cases, these forward-looking statements can be identified by words or phrases such as \"may,\" \"will,\" \"expect,\" \"anticipate,\" \"aim,\" \"estimate,\" \"intend,\" \"plan,\" \"believe,\" \"potential,\" \"continue,\" \"is/are likely to\" or other similar expressions. In addition, these forward-looking statements reflect our current views with respect to future events and are not a guarantee of future performance. Actual results may differ materially from the results predicted or implied by such statements, and our reported results should not be considered as an indication of future performance. The potential risks and uncertainties that could cause actual results to differ from the results predicted or implied by such statements include our ability to: obtain sufficient financing and manage our liquidity and capital resources to support our operations and growth; successfully identify, develop and bring online additional data center capacity on a timely and cost-effective basis, including securing suitable sites and access to power; implement and maintain effective internal control over financial reporting; manage supply chain risks and secure required equipment, hardware, materials and services on acceptable terms; compete effectively in a dynamic and competitive market while generating sustained customer demand; and manage dependence on key vendors and adapt to technological change.",
          "source_name": "businesswire",
          "published_at": "2026-11-12T12:11:00Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E101"
          ]
        },
        "w2": {
          "policy_hit": false,
          "policy_related": false,
          "related_policy_ids": [],
          "policy_ids": [],
          "hit_condition_ids": [],
          "matched_condition_ids": null
        },
        "w3_decision": {
          "decision": "LONG",
          "expectation_delta": "Q3收入为9.808亿美元，较此前约9.072亿美元的预期高约8.1%，环比增长约68%；调整后EBITDA增至4.658亿美元，较Q2的2.362亿美元接近翻倍，调整后净利润为正。这实质增强了AI云扩张正在转化为收入与经营贡献的证据。仍需注意本季经营现金流为负2.73亿美元、资本购置为73亿美元，且本次未提供ARR或新的全年指引。",
          "prior_expectation": "此前市场预期Q3收入约9.072亿美元、每股亏损约0.63美元；已知公司FY2026收入指引为30亿至34亿美元、年末ARR目标为70亿至90亿美元。投资者关注收入增长能否转化为持续经营盈利，以及大额扩张支出的资金需求。",
          "reason": "LONG：这是首次披露Q3实际业绩，发布于12:11:00Z，距12:11:12Z截止点仅12秒；营收超预期并伴随显著的调整后经营表现改善，对NBIS核心增长叙事具有实质增量。高资本开支及负季度经营现金流限制了对现金转化和融资风险的判断，但尚不足以抵消本次经营数据带来的正向预期修订。行情核验未发现可确认的公告后显著完成态；截止时公告后尚无足够的已完成行情形成稳定化判断。该核验不作为方向依据。"
        },
        "direction": "LONG",
        "initiated_at": "2026-11-12T12:14:04.750222+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      },
      {
        "message": {
          "title": "NEBIUS Group Letter to Shareholders | Q3 2026 - 富途牛牛",
          "body": "Source:Nebius Group Letter to Shareholders – Third Quarter 2026\n\nKey Metrics Overview\n\nExcluding contracts with terms of one year or less: the proportion of capital expenditures covered by upfront payments exceeds 100% (50–60% in the second quarter).\n\nComparable contract return period: 1 year and 7 months (Q2: 1 year and 10 months)\n\nGross Contract Value (TCV) signed since the beginning of Q3: USD 12 billion\n\nAnnual Contract Value (ACV) per MW: > $25 million (Q2: $20–$25 million)\n\nQ3 Group Revenue: $981 million, up 571% year over year; Nebius AI Cloud Adjusted EBITDA Margin 53%\n\n2027 Preliminary Guidance: Revenue of $12.0–$13.0 billion; capital expenditures of $4.0–$4.5 billion, 75–85% covered by customer prepayments and investment-grade asset-backed financing.\n\nCustomer expected prepayments for 2027: $17–20 billion; committed prepayments locked in for 2026: $10 billion\n\nNotes: (1) Capital expenditure coverage statistics include contracts of 1 year or longer, encompassing GPU, data center construction, and leasehold improvement costs. (2) ACV: Annual Contract Value. (3) Weighted average is calculated based on contracts with terms of up to 5 years.\n\nCEO Open Letter\n\nArkady Volozh, Founder and CEO\n\nDear Shareholders:\n\nThe third quarter of 2026 once again demonstrated the strong performance of the business we are building. Market demand remains exceptionally robust, and we have established comprehensive mechanisms to capitalize on growth opportunities.\n\nUnlike typical cloud providers, we do not merely build and operate data centers and computing resources; nor are we like inference service providers that simply offer cloud services on top of leased compute.\n\nNebius strikes a balance between the two: we independently design, build, and operate our underlying infrastructure, develop cloud-platform software, and deliver managed AI services that run on it. This is a hyperscale cloud‑provider model; as an integrated system, it supports all compute‑intensive AI workloads, covering the entire end‑to‑end pipeline—from model training and post‑training to inference, agent search, and execution. This enables us to fully capitalize on the current industry growth opportunities.\n\nTurning to our third-quarter results, we secured the most favorable commercial contract to date this quarter:\n\n1. Customers are signing contracts with longer durations, higher unit prices, and a greater proportion of upfront payments. In Q3, the upfront payments on signed contracts already exceeded the total capital expenditures (including data center construction and network investments), whereas in Q2 this ratio was only 50–60%. To date, this upward trend has continued into Q4 for newly signed contracts.\n\n2. Driven by higher contracted prices under new agreements, the average investment payback period for comparable 1‑ to 3‑year contracts improved from 1 year and 10 months in the second quarter to 1 year and 7 months; this return covers both operating costs and capital expenditures¹.\n\n3. Our hosted inference platform, Token Factory, is on track to generate annualized revenue in the hundreds of millions of dollars; Q3 contract value surged 17-fold quarter-over-quarter, and this is just the early stage of our business.\n\n4. The customer base continues to expand: Native AI companies are steadily increasing the scale of their collaborations, and we have completed initial signings with several leading enterprise data and AI platforms. Our partnership models include direct customer engagements as well as joint sales initiatives with channel partners.\n\n5. Through innovative pricing mechanisms such as reserve auctions and real-time spot computing power pricing, the true value of computing power is determined by the market. This approach not only provides customers with more ways to access the Nebius platform but also enables us to gain real-time insights into actual market demand.\n\nThe foundation of our business model lies in computing power supply, supported by a flexible and diversified global footprint of data centers, partners, and local access to electricity resources:\n\nWe prioritize building our own data centers to better control project implementation and achieve superior economic returns. All self-built sites have been delivered on schedule or even ahead of time, and in the future, the vast majority of new gigawatt-scale computing capacity will come from our proprietary facilities.\n\nThird-party co-hosted data centers have experienced delivery delays; for example, at the New Jersey site, we are switching the power supply to Bloom Energy's sustainable fuel cells. The first batch of generator sets has been installed, and we are currently expanding computing capacity.\n\n¹ Based on comparable 1–3 year contracts, server equipment is depreciated over a 5-year period.\n\nIn recent weeks, the industry has been closely watching the funding sources for the expansion of AI infrastructure.\n\nWe remain focused on building a long-term, healthy, and profitable business. We will continue to invest in computing infrastructure, platform product development, and our go-to-market team.\n\nOur self-developed, self-built model helps us secure more favorable contract pricing and commercial terms while reducing infrastructure costs, ultimately driving higher profit margins. This approach has already begun to deliver results, and we are pursuing prudent financing backed by customer prepayments and a strong balance sheet:\n\nWe expect that 75%–85% of our capital expenditures in 2027 will be financed through customer prepayments and asset‑backed financing secured by contracts signed with investment‑grade customers; we also have a wide range of alternative financing channels available.\n\nThe company has low debt costs, a healthy balance sheet, and holds cash along with several strategic equity investments.\n\nWhen external financing costs are favorable, we will opportunistically pursue additional external financing.\n\nUpdated Fiscal 2026 Earnings Guidance\n\nAffected by the deferral of some revenue for this year to early 2027:\n\nFor the full year 2026, revenue is expected to be at the lower end of the original guidance range of $3.0–$3.4 billion; adjusted EBITDA margin is approximately 40%.\n\nThe annualized recurring revenue (ARR), originally expected to be achieved in December, is now projected for February 2027—earlier than the New Jersey site's full‑capacity commissioning.\n\nPreliminary Business Outlook for 2027\n\nLeveraging the aforementioned structural advantages, we expect to achieve breakeven on a pre-tax basis by the end of 2027.\n\nRevenue target: USD 12–13 billion; this projection is based on conservative assumptions regarding the ramp-up of computing capacity. We continue to plan for the addition of more than 1 GW of new computing capacity each year.\n\nCapital expenditures of $40–45 billion, with a prudent and diversified capital allocation strategy.\n\nAs our business matures, we are transitioning to performance metrics that more accurately reflect our underlying operating performance: beginning in 2027, we will disclose guidance for revenue and profit before tax.\n\nOur confidence in the fundamentals of our business remains unwavering, and the market opportunities ahead remain vast. We will continue to move forward.\n\nCustomer: Deeper engagement, broader customer base\n\nMarket demand is undeniable. Since the start of Q3, customers have secured over $12 billion in contract value for Nebius under historically favorable terms, and we still have substantial mining capacity available for sale.\n\nWe independently determine our computing power sales approach and commercial terms. This quarter, we simultaneously opened additional procurement channels for customers; even with more options available, they continue to accept the contract conditions we set: larger contract sizes, longer durations, higher unit prices, and greater upfront payments. The Q3 contracting activity, as well as the situation in Q4 to date, fully confirms this trend.\n\nPrepayments: Customer prepayments now cover or even exceed capital expenditures.\n\n1. In Q3, the proportion of capital expenditures covered by advance payments on signed contracts exceeded 100%, compared with only 50–60% in Q2; this trend continued into Q4.\n\n2. In Q3, more than 90% of the total contract value signed included advance payment terms; the weighted-average proportion of advance payments to contract value increased by approximately 20 percentage points quarter over quarter.\n\n3. Renewals of existing computing power also generate upfront payments, boosting the return on existing assets. Capital expenditures for existing computing capacity have already been recovered, and the additional upfront payments can fund the next phase of infrastructure development.\n\nReturn Cycle: Accelerated Returns, Enhanced Operational Visibility\n\n1. Compared with the 1‑ to 3‑year contract return period, which shortened from 1 year and 10 months in Q2 to 1 year and 7 months in Q3, the primary driver was an increase in new contract signing prices. The returns cover both operating costs and capital expenditures, and the calculations do not include gains from upfront payments.\n\n2. Nebius AI Cloud has secured five-year customer contracts, with an annual contract value per megawatt exceeding previous quarterly levels. These long-term agreements combine duration, scale, and upfront payments: customers lock in computing power at current prices for the long term.\n\n3. The new five-year AI cloud contract optimizes the customer contract term structure, delivering predictable high-margin revenue and a project payback period of less than two years.\n\nNote: Weighted average based on 1–5 year contracts\n\nRequirement: Price increase, expansion of customer segments\n\n1. In Q3, the annual contract value per megawatt exceeded $25 million, while Q2 contracts ranged from $20 million to $25 million; the contracted price per GPU-hour rose by more than 20% on average in Q3 compared with Q2.\n\n2. We have launched a new market-based pricing tool: reserve auction winning prices exceed the floor price by more than 15%; real-time spot computing power prices sometimes surpass on-demand pricing; and short-term cluster pricing for 3–6 months is more than twice the reserved computing power price.\n\n3. We continue to develop emerging markets. Since its launch in the first quarter of 2026, the Asia-Pacific region has secured multiple customer contracts in Japan, South Korea, India, Australia, and Singapore; we have appointed country heads for South Korea and Japan to lead local market expansion.\n\nDiversified Procurement Model\n\nShort-term contracts (premium pricing model): reserve auctions, preemptible spot instances, pay-as-you-go billing, and premium short-term clusters. Both winning bidders in the reserve auction were new Nebius customers, with one of them subsequently increasing its contract size tenfold.\n\nCore model (1–3 year contracts): A large number of customers upgrade from preemptible compute to reserved compute, iterate to the next-generation GPU, and migrate from Token Factory to dedicated infrastructure.\n\nLong-term contracts (five-year term): All stem from capacity expansions by existing customers, who are procuring additional computing power; optimizing the contract tenor structure supports financial planning. In Q4, the early-stage AI cloud contract value has already reached the multi-billion-dollar level, with only a few months having passed since the first billion-dollar contract.\n\nCustomer Onboarding Status: Cross-Industry, Multi-Channel Expansion\n\nLeading enterprise data and AI platforms sign multi-year agreements for the first time\n\nDatabricks: After rigorous evaluation, we selected Nebius AI Cloud, confirming its ability to support demanding AI research and development workloads.\n\nDatadog: By choosing Nebius to deploy its AI cloud, Datadog recognizes our AI-specific infrastructure, deep technical collaboration, and responsive technical support, resulting in an infrastructure partnership.\n\nPalantir: Designates Nebius as its preferred AI cloud partner, enabling Palantir customers to leverage Nebius infrastructure for fine-tuning and inference of sovereign AI models.\n\nSnowflake: Through partnership agreements, Snowflake's operations run on Nebius AI Cloud, demonstrating our ongoing expansion into top-tier technology enterprise customer segments.\n\nMeanwhile, numerous cutting-edge native AI companies are joining at the same time:\n\nCognition (the team behind Devin, an autonomous AI software engineer): Leverages Nebius's NVIDIA Vera Rubin large-scale cluster to meet computational demands, accelerate product iteration, and serve enterprise customers.\n\nOrtet (Medical Frontier Lab): Deploys the Blackwell cluster and will subsequently be given priority access to Vera Rubin computing resources, enabling expansion of its computational capacity.\n\nCursive: a London-based AI lab founded by former DeepMind researchers, described Nebius as an \"ideal computing partner.\"\n\nDistil Labs (Berlin post-training platform): Leveraging Nebius AI Cloud, it builds specialized models for AI agents using synthetic data.\n\nEcosystem Partner Development\n\nWe have built an open ecosystem around Nebius, with partner offerings that address our customers' core needs:\n\n1. Light-asset model partners: Leverage external capital and facilities to deliver computing power based on the Nebius architecture and cloud platform, expanding global supply of value-added AI computing resources.\n\n2. Channel market partners: TD SYNNEX, Palantir, CDW, etc., leveraging a light-asset cooperation model to reach and serve more customers.\n\n3. Full-stack technology partner: In collaboration with NVIDIA, Hugging Face, Reflection, OpenHands, and LangChain, our tools span models, agent frameworks, observability, and post-training, supporting the entire AI development lifecycle and backed by a developer support program.\n\nExpansion of customers' businesses within the platform\n\nThe inference service Token Factory has been live for less than a year yet is already approaching hundreds of millions of dollars in annualized revenue; many customers run multiple workloads simultaneously on the Nebius platform. Most initially came solely for compute power, but ultimately expanded their partnerships as they leveraged the platform's full suite of offerings.\n\nWe have integrated our proprietary infrastructure with hosting services into a vertically integrated platform, generating higher revenue per megawatt than pure infrastructure providers, and serving customers across diverse workloads, multiple GPU generations, and various contract structures.\n\nApplied Compute & Cursive: Leveraging Token Factory to extend model workflows from post-training on the platform to inference, providing scalable solutions for enterprise models and enhancing customer engagement across the entire AI lifecycle.\n\nHark: Leveraging Token Factory to deliver low-latency inference for Hark Pro AI agents, continuously expanding use cases; several leading personal AI agent companies have adopted similar solutions.\n\nProsus: Token Factory runs open-source models at scale to support its global e-commerce and local services businesses, which together serve one billion users.\n\nAgent Infrastructure (Tavily): Over 3 million developers have built on Tavily, processing 350 million AI agent search requests each month.\n\nmonday.com: AI products use Tavily's real-time web search to conduct corporate research, complete contact information, and monitor news.\n\nBell Cyber: Automated threat analysis in the Security Operations Center processes 100,000 alerts per month, reducing the time to investigate each alert from 30 minutes to under 5 minutes, without requiring additional analysts.\n\nOcean AI: The email security agent uses Tavily to verify senders and detect identity spoofing, completing one billion verifications per month and reducing investigation time from minutes to seconds.\n\nIBM Research: Selected Tavily to complete the post-training of the open-source Granite model family, with a second expansion of the collaboration scope in Q3.\n\nComputing Power Infrastructure: Focus Shifts to Proprietary Sites, with Capacity Steadily Scaling Up\n\nProgress on Computing Power Deployment\n\n1. Q3 achieved the largest quarter-over-quarter increase in effective computing power in its history.\n\n2. A total of four brand-new sites in the United States and Europe have commenced operations and begun generating revenue; at the New Jersey site, multiple batches of computing power were delivered continuously from Q3 through November.\n\n3. Q4: Continuously launching new computing power; phased deployment and expansion of hosted data centers in the United States, Europe, and Israel.\n\nNext-generation chip\n\nNVIDIA Vera Rubin has been deeply integrated into the business prior to large-scale delivery; the first batch of systems has been deployed and connected to Nebius AI Cloud, awaiting customer production launch.\n\nWe have also deployed NVIDIA Vera CPUs early on, in conjunction with GPU clusters, to support agent tasks and data processing; demand for high-performance CPUs remains strong, and we are fully prepared to meet it.\n\nNebius has become the first AI cloud provider to deploy NVIDIA Groq 3 LPX, boosting Vera Rubin NVL72's inference token-generation speed in Token Factory.\n\nVera Rubin's hardware configuration achieved industry-leading single-GPU results in the DeepSeek R1 MLPerf inference benchmark, bolstering customer confidence.\n\nOutlook: We are maintaining our target of adding more than 1 GW of computing capacity annually, while adopting a cautious outlook on the timing of commissioning new capacity. The light-asset partnership model completed its first phase of implementation in Q3 and is expected to contribute a small amount of available computing power starting in 2027.\n\nThe delay in the colocation data center does not signify a change in fundamentals.\n\n1. The delivery of multiple batches of hosted data centers has been postponed to the first half of 2027, including the remaining batches of computing power in New Jersey.\n\n2. We are confident that we will complete delivery in the near term and are working with our partners to advance the project.\n\n3. All affected customers have been updated with the latest information, and their needs have been properly addressed.\n\nNew Jersey project details: More than half of the computing power contracted with Microsoft is expected to be delivered by the end of 2026; the remaining batches will be deferred to the second quarter of 2027. After Q3, DataOne shut down temporary generator sets at its sites while awaiting the activation of permanent power supply, directly resetting the delivery schedule for the remaining computing power. Microsoft was involved in the planning throughout. Bloom's fuel cell power equipment has been brought online and is expanding production capacity, with computing power continuing to ramp up over the coming weeks.\n\nBuilding Your Own AI Computing Power Factory: Greater Controllability, Better Profit Margins\n\nStarting in 2027, the vast majority of new computing power will come from proprietary sites.\n\n1. By 2027, all the electricity and hardware components required for the planned computing power will be secured, and construction progress will be synchronized with the timeline for grid connection at each site.\n\n2. Own sites coming online in 2027: Missouri, Lappeenranta (Finland), Mäntsälä Phase II, and Alabama. Once fully operational, they will collectively deliver over 3 GW of computing capacity over the next 3 to 3.5 years.\n\nAdvantages of the self-built model:\n\n1. We manage projects end-to-end, from groundbreaking to customer system go-live; we oversee approvals and the early construction phase, and can adjust the pace of development—speeding it up or slowing it down—to align with market demand.\n\n2. Buildings and power infrastructure are constructed in multiple phases over several years; once completed, only compute hardware such as GPUs needs to be replaced (depreciated over five years); when new‑generation GPUs are introduced, the existing infrastructure can continue to generate revenue.\n\nCommunity Collaboration: U.S.-based projects adhere to responsible design principles, proactively engaging with local communities and regulatory authorities; they maintain transparent communication, assume electricity costs, reduce water consumption, and implement community benefit programs. We expect the upcoming election will not adversely affect new or hosted sites in Pennsylvania and other states.\n\nThe footprint of signed computing power agreements continues to expand.\n\nThe goal is to reach a total contracted power capacity of 5 GW by the end of 2026; currently, it has already exceeded 4 GW.\n\n1. Missouri adds 300 MW, bringing the total contracted capacity to 1.1 GW;\n\n2. Alberta, Canada has secured an exclusive site for a 100 MW AI data center, scheduled for delivery in 2027 with the potential to expand to several hundred megawatts.\n\n3. South Carolina adds a 50 MW colocation facility, the eighth site in the United States, expected to come online in 2027;\n\n4. The first batch of Asia-Pacific sites have been established, with two colocation data centers in India expected to begin operations in 2027.\n\nGlobal site selection comprehensively considers customer needs, power supply, economic costs, and the policy environment.\n\nThe light-asset model helps us expand capacity more cost-effectively beyond our own contracted power. In Q3, we recognized the initial revenue from light-asset partnerships, and additional computing power will continue to be brought online going forward.\n\nExplanation of the Global Site Distribution Map\n\nDark blue dots: existing stations\n\nYellow dots: New sites added in 2026\n\nLarge-Scale: Sites >100 MW\n\nRough marking: >1 GW site coverage: United States (Alberta, Minnesota, Kansas City, Oklahoma, Missouri, Pennsylvania, New Jersey, Alabama, South Carolina); Europe: Iceland, Finland, Estonia, United Kingdom, France, Spain; Middle East: Israel; Asia: India.\n\nAI Cloud Platform: One platform, spanning from computing power to production-grade AI.\n\nQ3 We upgraded our platform across three key dimensions: inference, new types of workloads (post-training and AI agents), and enterprise-grade deployment. Each new capability provides customers with more options while reducing our service costs, enabling us to generate higher revenue per megawatt of computing power. In September 2026, we received the Platinum rating for SemiAnalysis ClusterMAX™ 3.0—the highest accolade awarded to an AI cloud provider.\n\nReasoning Services: New Models Take Priority, Driving Ongoing Cost Reductions\n\nThe hosted inference service Token Factory has been live for less than a year, with annualized revenue approaching several hundred million dollars. In Q3, the two core objectives were: to deploy new models as quickly as possible and to reduce the cost per inference request.\n\n1. Models such as Kimi K3, GLM5.3, Reflection Beam, and Meta Muse Spark Open were launched on Token Factory on their release day; they feature deep compatibility with the latest versions of DeepSeek, Kimi, and GLM. In the Artificial Analysis benchmark tests, Token Factory ranks among the top performers in terms of throughput, latency, and output quality. Customers can run these models directly on Nebius on the day of their release.\n\n2. Q3: Completed integration with Eigen AI and Clarifai; in October, we integrated Inferize to shorten the time-to-market for new models and accelerate production scaling.\n\nPost-Training Models and Agents: The Same Platform Powers Next-Generation Business\n\n1. Post-training of models: Customers adapt models using their own data and deploy inference directly on Nebius. Applied Compute and Cursive are typical examples, eliminating the need for cross-platform migration. This workflow reuses existing training clusters and the computational resources of Token Factory.\n\n2. Agent execution: Sandboxing, the agent runtime, and model routing generate a large number of CPU-intensive tasks. We have deployed GPU resources in parallel to provide CPU compute power, initially using NVIDIA Vera CPUs.\n\n3. Tavily: As an agent-based data search layer, it attracts a group of customers who would not have otherwise purchased computing power.\n\nThe same customer can procure multi-layer services, which is why our revenue per megawatt exceeds that of pure infrastructure vendors while also achieving diversification of our business portfolio.\n\nEnterprise-grade capabilities: network interconnection, security, and cost management\n\nEnterprise customers have many demands that AI-native Venture companies lack: connecting via dedicated lines to on-premises private systems, ensuring compatibility with existing security tools, and enabling unified cost and compute management across multiple teams. All core capabilities were delivered in Q3.\n\n1. Network Interconnection: Cloud Interconnect dedicated lines connect Nebius with customers' private networks, enabling on-premises data centers and services on other cloud platforms to operate in coordination with production workloads running on Nebius.\n\n2. Security: Supports mainstream cloud security posture management tools such as Wiz, meeting enterprise security access review requirements.\n\n3. Computing power and cost management: cost tagging, monthly usage billing, and reserved computing power quotas; multiple teams within an enterprise share a single contract, with clear data visualization.\n\n4. Rapid Deployment: All services provide REST API and OpenAPI-standard interfaces; Serverless AI has added a persistent Devlabs workspace that supports direct execution of Python tasks, and CI pipelines and AI agents can directly schedule Nebius resources.\n\nDriving Traffic to the Developer Ecosystem\n\nIn September, Nebius launched its AI Developer Support Program; co-developed free agent-based AI courses with NVIDIA; and hosted the Nebius×NVIDIA Global AI Hackathon, attracting over 29,500 participants. Through compute‑power grants, sample code, and direct access to engineers, Nebius is reaching next‑generation AI developers ahead of time.\n\nFinancial Situation\n\nKey Income Statement Figures (Unit: Million USD)\n\nIn Q3, the Group's total revenue reached US$980.8 million, up 571% year over year and 68% from Q2. This growth was driven by the launch of new computing power and revenue from a light-asset partnership model.\n\n1. Q3 saw approximately $325 million in one-time revenue from the light-asset model, marking the initial rollout of this business. Under this model, partners fund the construction of physical infrastructure while we operate the Nebius architecture and cloud platform. We are currently securing a substantial pipeline of partnership projects, and light-asset revenue is expected to become recurring going forward. In this collaborative framework, revenue can be generated both when the platform is deployed and when computing capacity enters full production.\n\n2. Nebius AI Cloud reported Q3 revenue of $973.8 million, up 643% year over year, accounting for approximately 99% of the group's total revenue.\n\n3. As of the end of September, annualized recurring revenue (ARR) stood at $6.4 billion, up 1,062% year over year and up 113% from $3.0 billion at the end of June.\n\nManagement's View: The ARR metric is no longer suitable for evaluating the company's business: it only reflects monthly performance; an asset-light model can cause significant ARR volatility; the business has entered a new phase focused on revenue and profitability. Starting in 2027, the company will prioritize disclosing revenue and pre-tax profit.\n\n1. Remaining performance obligations (RPO) as of the end of September: $38.7 billion, compared to $37.5 billion at the end of June, up 3% sequentially.\n\nOperating Expenses (in millions of U.S. dollars)\n\nCost of revenue: $219.7 million—growth driven by AI cloud scale expansion, data center leasing, and headcount increases; the cost of revenue as a percentage of revenue decreased from 29% to 22%, reflecting economies of scale.\n\nR&D expenses: $118.6 million—driven by an expanded engineering team, including stock-based compensation; as a percentage of revenue, this decreased from 31% to 12%.\n\nSales and General & Administrative (SG&A) expenses totaled $237.4 million, driven by increased headcount, outsourced services, and higher legal advisory fees related to financing; as a result, SG&A as a percentage of revenue decreased from 61% to 24%.\n\nDepreciation and amortization: $354.7 million; increased capital expenditures on GPUs and data center hardware; starting in 2026, the depreciation period for servers and networking equipment will be adjusted to 5 years (previously 4 years); share of revenue decreased from 68% to 36%.\n\nAdjusted EBITDA (in million U.S. dollars)\n\nThe Group's adjusted EBITDA for Q3 was $465.8 million, an improvement of $471 million year over year. This was primarily driven by Nebius AI Cloud, which contributed $518.2 million in adjusted EBITDA at a 53.2% margin, up from $236.2 million in Q2—a sequential increase of $229.6 million.\n\nCapital Expenditures and Sources of Funds\n\nQ3 capital expenditures were approximately $7.3 billion, primarily for GPU hardware purchases and data center expansion.\n\nFunding Strategy: Diversify financing instruments based on cost of capital, equity dilution, and balance sheet health.\n\n1. Of the 40–45 billion in capital expenditures projected for 2027, 75–85% is expected to be financed through customer prepayments and asset‑backed financing tied to investment‑grade contracts. We can independently select our customers, contract durations, and the proportion of prepayments, thereby managing the scale of external financing and timing our funding rounds rather than being passively reliant on external capital.\n\n2. Prepayments have become the standard contractual arrangement. In 2026, customers are committed to prepaying $10 billion; in 2027, the figure is expected to be $17–20 billion, which is not a cap and depends on how computing power is allocated across different contract types. Early receipt of prepayments provides financial predictability. In July, we completed our first asset-backed financing: $775 million at a rate of SOFR + 2.50%, collateralized by long-term contracts with investment-grade clients. Microsoft and Meta's contracts can leverage this same model; with over $40 billion in similar contracted revenue on hand, this financing structure is highly replicable.\n\n3. Strong balance sheet: $7.4 billion in cash and equivalents at quarter-end, complemented by strategic equity investments, with operating cash flow continuing to grow as profit margins improve.\n\n4. External financing will be pursued opportunistically: prioritizing assessment of funding costs while balancing equity and debt. In August 2026, $5.75 billion in convertible notes were issued; in Q3, approximately $1 billion was raised net through the ATM market placement facility; in October, a $3.5 billion senior secured credit facility was agreed with Citi. Future instruments under consideration include customer prepayments, asset-backed financing, internal cash, corporate bonds, and equity financing, with selection based on optimal terms to serve the company's long-term interests.\n\n2026 Guidance Update\n\nThe commissioning of some third-party data centers has been postponed to the first half of 2027, but the contracts remain in effect:\n\nRevenue: At the low end of the $3.0–$3.4 billion guidance range\n\nAdjusted EBITDA margin remains unchanged at approximately 40%\n\nCapital expenditures are approximately $27 billion, exceeding the original $20–25 billion range, with primary investments in owned sites to build up computing power reserves by 2027.\n\nARR: Originally projected to reach $7–9 billion by December, the company now expects monthly ARR to surpass $7 billion in February 2027, ahead of full production at the New Jersey facility.\n\nSigned Power Capacity: Targeting 5 GW by year-end; currently exceeds 4 GW.\n\nPreliminary Outlook for 2027\n\nRevenue: $12–13 billion, roughly four times the 2026 level\n\nPre-tax break-even target for Q4 2027 (including financing costs, with servers depreciated over 5 years)\n\nCapital expenditures of $40–45 billion, matching over 1 GW of new computing capacity per year; 75–85% of funding from advance payments and asset-backed financing.\n\nMore than 1 GW of computing power comes online each year.\n\nStarting in 2027, we will provide guidance on revenue, pre-tax profit, and computing power, and will no longer place a primary emphasis on the ARR metric.\n\nOther Businesses and Strategic Equity Investments\n\nAVRIDE Autonomous Driving Business\n\nAutonomous driving operations continue to expand.\n\n1. Autonomous delivery has launched on 25 U.S. campuses and in over 30 cities. The fleet has expanded to 250 vehicles, with a target of 350 by the end of 2026; total autonomous driving mileage has surpassed 2 million miles (3.2 million kilometers). Robotaxi services have completed more than 100,000 passenger trips; operating coverage in Dallas has doubled to 31 square miles (80 square kilometers), with plans for further expansion.\n\n2. AVRIDE plans to launch fully driverless Robotaxi services in the first half of 2027.\n\n3. In the fall, we added more than 20 new campus partnerships, bringing the total to 25. We will soon be launching initiatives at Ohio State University, the University of Tennessee, and the University of Kentucky.\n\n4. In Q3, the daily average of delivery orders was 6,500, more than tripling year over year; cumulative deliveries exceeded 800,000.\n\n5. Expanded partnerships with Wonder and Grubhub to include over 100 merchants across New Jersey, Virginia, and Philadelphia; automated delivery now operates in more than 30 U.S. markets and Tokyo, Japan; and the total fleet of delivery robots has reached 1,000 units.\n\n6. Efforts to improve operational efficiency on the consumer side and stringent cost control led to a 44% quarter-over-quarter decline in cash burn, achieving break-even on a cash basis for B2C operations. B2B enterprise bookings remain robust, with courses focused on practical AI implementation. In Latin America, adjusted EBITDA and operating cash flow turned positive each month in Q3; in the U.S., TripleTen is concentrating on high-margin core courses tailored to experienced professionals seeking to transition into AI-related roles.\n\nToloka\n\nToloka has evolved from a mere data provider into a full‑cycle partner for AI labs and enterprises.\n\n1. Q3 revenue doubled quarter-over-quarter, with healthy contribution margins and continued leverage in the core business.\n\n2. Arena's agent evaluation capabilities have been upgraded, enabling private, leak-free agent task evaluations across 35+ models. Model defects identified during these evaluations are compiled into standardized datasets that can be licensed for repeated sale, generating highly scalable revenue.\n\n3. The self-service platform has launched, covering manual and synthetic data collection, annotation, and evaluation, and is now part of the Nebius Developer Support Program.\n\n4. In Q3, we launched the fine-tuning service, making the model iteration capabilities validated by the Frontier Lab available to enterprise customers such as Shopify. Ablation experiments demonstrate that single-round post-training based on Toloka's off-the-shelf datasets can improve the model's performance on external unseen benchmark sets.\n\nClickHouse\n\nClickHouse is an open-source database designed for real-time data processing and analytics. In January 2026, it raised $400 million in a Series D funding round, achieving a post-money valuation of approximately $15 billion. Following the financing, Nebius continues to hold a significant minority stake.",
          "source_name": "news.google.com",
          "published_at": "2026-11-12T12:34:23Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E586",
            "E587",
            "E590"
          ]
        },
        "w2": {
          "policy_hit": false,
          "policy_related": true,
          "related_policy_ids": [
            "pol_90d7c24fb5d048b8be7bed29ec918066",
            "pol_70d5e32be9c04fca97b31b622e28c6db",
            "pol_7782abaab5a64491803b9d2d975b1b66"
          ],
          "policy_ids": [],
          "hit_condition_ids": [],
          "matched_condition_ids": null
        },
        "w3_decision": {
          "decision": "SHORT",
          "expectation_delta": "Q3收入中约3.25亿美元来自一次性伙伴模式启动收入，削弱了将营收超预期外推为持续服务增长的依据；公司将FY2026收入收窄至原指引低端，原定12月的ARR目标改为2027年2月超过70亿美元，部分共址容量延至2027年上半年，同时FY2026资本开支升至约270亿美元。近期收入兑现与资金占用的组合较原预期恶化。",
          "prior_expectation": "此前公司维持FY2026收入30亿—34亿美元、12月ARR为70亿—90亿美元及资本开支200亿—250亿美元；本日Q3营收9.808亿美元超预期的初步解读，支持扩容正在转化为持续收入与盈利。",
          "reason": "完整股东信新增的收入构成和指引调整，实质下修近期增长质量及交付可靠性，区别于此前已处理的业绩、提价和预付款利好。新增签约、预计回收期缩短及2027年增长指引提供正面抵消，但不足以消除本次兑现延期与更高投入的负向修订；不推断合同取消或永久需求损失。股东信随12:11财报公开，当前约26分钟，仍处复杂业绩的首轮解读窗口，12:34转载不重置起点。已实际核验统一行情：公告后虽有明显重新定位，但完整5分钟数据尚不足以确认持续稳定的显著完成态，故未施加价格否决。"
        },
        "direction": "SHORT",
        "initiated_at": "2026-11-12T12:38:09.499866+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      },
      {
        "message": {
          "title": null,
          "body": "Nebius Group Sees FY26 Revenue at Low End of $3B - $3.4B Range",
          "source_name": "interactivebrokers.com",
          "published_at": "2026-11-12T12:36:00Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": [
            "E610"
          ]
        },
        "w2": {
          "policy_hit": false,
          "policy_related": false,
          "related_policy_ids": null,
          "policy_ids": [],
          "hit_condition_ids": [],
          "matched_condition_ids": []
        },
        "w3_decision": {
          "decision": "SHORT",
          "expectation_delta": "管理层现预计FY26收入落在既有区间低端（约30亿美元），较此前约33.4亿美元的公开聚合预测低约10%；按前九个月收入19.621亿美元计，Q4约需10.38亿美元，环比Q3约增长6%。这是对近期变现预期的实质下修；已公布的Q3超预期及FY27指引限制了长期负面推论，但未消除FY26下修。",
          "prior_expectation": "FY26公司指引为30亿至34亿美元；截至10月的公开聚合预测约33.4亿美元，Q3前BofA模型为34.9亿美元，预期偏向区间中高端。[BofA预估报道](https://blockspace.media/insight/nebius-buy-rating-310-target/)",
          "reason": "SHORT：低端展望是相对先前预期的负向增量，尽管今日Q3超预期及FY27指引为120亿至130亿美元构成缓冲。该消息12:36Z发布，距cutoff约40秒，仍在首轮传播。行情核验中最新完整5分钟柱止于12:30Z，12:35柱仍在形成，均早于本消息；无法判定本催化后的完成态，故无价格否决，方向依据是业务预期下修。"
        },
        "direction": "SHORT",
        "initiated_at": "2026-11-12T12:40:54.489047+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      },
      {
        "message": {
          "title": "Strong AI computing demand fails to mask risks of data center delivery delays; Morgan Stanley maintains \"Equal-Weight\" rating and $144 price target for Nebius (NBIS.US) - Moomoo",
          "body": "Morgan Stanley maintains a \"Hold\" rating on Nebius Group with a price target of $144, implying about 12% upside from its November 12 closing price of $128.64.\nThe Zhitong Finance APP learned that Morgan Stanley released a research report after Nebius Group (NBIS.US), an artificial intelligence (AI) cloud computing service provider, announced its third-quarter earnings. Although the company's revenue and profitability both exceeded market expectations, AI computing demand remained robust, with further increases in contract sizes and computing rental prices. However, delays in third-party data center deliveries led the firm to lower its 2026 revenue guidance and push back some revenue growth to the first half of 2027. Considering execution risks in data center construction and computing deployment, as well as financing pressures from future large-scale capital expenditures, Morgan Stanley maintained a \"Hold\" rating on the stock and a price target of $144, representing approximately 12% upside from its November 12 closing price of $128.64.\nAccording to the financial report, Nebius' third-quarter revenue increased 571.3% year over year to $980.8 million, exceeding market expectations. Specifically, the company's core AI cloud computing segment saw revenue rise 642.8% year over year to approximately $974 million, accounting for more than 99% of the group's total revenue.\nIn terms of profitability, Nebius' adjusted earnings before interest, taxes, depreciation, and amortization (EBITDA) for the third quarter reached $465.8 million, with an adjusted EBITDA margin of 47.5%, up from 40.6% in the previous quarter and exceeding market expectations. According to Morgan Stanley's financial model, the company's adjusted earnings per share for the third quarter were $0.31.\nHowever, Morgan Stanley noted that Nebius' third-quarter revenue included $325 million in non-recurring income related to its light-asset operating model, accounting for about one-third of the quarter's total revenue. Therefore, despite the company's strong financial performance in the third quarter, investors should still pay attention to the growth of its recurring cloud computing revenue.\nMorgan Stanley analysts Ryan Lountzis and Adam Wood noted that the positive signals from Nebius's latest earnings report were mainly focused on three areas: AI computing demand, pricing power, and financing arrangements.\nFirst, the tight supply-and-demand dynamics in the AI computing power market continue to drive Nebius to raise its service prices. In the third quarter, the annual contract value per megawatt (MW) of computing capacity exceeded $25 million, up from $20 million to $25 million in the second quarter. Meanwhile, the average hourly GPU rental price increased by more than 20% quarter over quarter.\nSince the beginning of the third quarter, Nebius has secured customer contracts totaling more than $12 billion. Management stated that the size of customer contracts is steadily increasing, prices for computing capacity through 2027 remain on the rise, and some customers have already begun seeking to lock in computing resources for 2028.\nMorgan Stanley believes that the aforementioned indicators show that, despite growing market concerns about the scale of AI infrastructure investment and its return rate, Nebius currently still faces strong customer demand and can secure more favorable commercial terms through higher pricing.\nHowever, delays in data center deliveries are becoming the primary factor constraining Nebius's short-term revenue growth.\nNebius stated that, due to the postponement of delivery for several third-party hosted data center projects to the first half of 2027, some computing capacity and related revenue originally scheduled to come online this year will also be delayed accordingly. Among these, the data center project located in Vineland, New Jersey, primarily serving Microsoft (MSFT.US), has become the focus of market attention.\nReportedly, the project's third-party data center vendor is gradually decommissioning some temporary power facilities while advancing the deployment of Bloom Energy's (BE.US) fuel cell power system. This adjustment has resulted in a delay to the computing capacity originally scheduled for delivery to Microsoft this year.\nNebius currently expects to complete the delivery of more than 50% of the capacity under its computing power contract with Microsoft by the end of 2026, with the remaining portion scheduled for delivery in the second quarter of 2027.\nAffected by the aforementioned factors, Nebius has revised its full-year 2026 revenue guidance from a previous range of $3.0 billion to $3.4 billion to $3.0 billion to $3.2 billion, lowering the upper end of the range by $200 million.\nMeanwhile, the company now expects to achieve annual recurring revenue (ARR) of over $7 billion by February 2027, whereas its previous target was to reach $7 billion to $9 billion in ARR by the end of 2026.\nMorgan Stanley believes that although the related revenue was mainly delayed due to changes in the timing of computing power deployment rather than a weakening of customer demand, the recent delays in project delivery highlight the uncertainty surrounding Nebius's ability to convert its contracted computing power capacity into actual revenue in a timely manner amid constrained supplies of data centers, electricity, and other infrastructure.\nDespite short-term delivery schedules being affected, Nebius management is maintaining its long-term computing power expansion targets. Currently, Nebius has contracted for over 4 gigawatts (GW) of electricity capacity and still expects to increase this figure to 5 GW by the end of 2026, with plans to bring more than 1 GW of additional operational capacity online in 2027.\nTo achieve this goal, Nebius recently announced several new data center projects and expansion plans, including adding 300 MW of capacity in Missouri, bringing the total contracted capacity there to 1.1 GW; advancing a multi-campus AI data center project in Alberta, Canada, with a total capacity of 100 MW; and launching a 50-MW third-party colocation data center project in South Carolina, USA.\nManagement noted that as more computing capacity in the future comes from the company's own data centers, Nebius will be better able to control project construction and delivery timelines, thereby reducing its reliance on third-party data center providers.\nIn addition to the progress of computing power deployment, Nebius's financing plan designed to support large-scale business expansion is another major highlight of this earnings report.\nNebius expects capital expenditures to reach $40 billion to $45 billion by 2027. Approximately 75% to 85% of these funding needs are anticipated to be met through customer prepayments and asset-backed financing linked to contracts with existing investment-grade rated customers. The company projects that customer prepayments will total $17 billion to $20 billion in 2027, noting that this figure is not a cap.\nNotably, in the third quarter, customer prepayments received on newly signed contracts at Nebius were sufficient to cover more than 100% of the capital expenditure requirements for the related projects, compared with only 50% to 60% in the second quarter. Management stated that this trend continued into the fourth quarter.\nMorgan Stanley believes that clients' willingness to provide a higher proportion of upfront payments reflects, on the one hand, the urgent need of large tech companies to secure future AI computing resources, and on the other hand, helps Nebius reduce its reliance on external financing during business expansion.\nHowever, even if existing financing arrangements cover most capital expenditures, Nebius will still need to raise additional funds for future data center construction. Given recent changes in the credit markets and the company's substantial investment plans over the next several years, financing costs and the stability of funding channels will remain key factors that investors must closely monitor.\nBased on the latest financial report and computing power deployment plans, Morgan Stanley has adjusted its financial forecasts for Nebius over the next several years. The firm lowered its 2026 revenue forecast for Nebius from $3.389 billion to $3.050 billion, a decrease of 10%; raised its 2027 revenue forecast from $10.926 billion to $12.025 billion, an increase of 10.1%; and raised its 2028 revenue forecast from $19.059 billion to $21.018 billion.\nOn the profitability front, Morgan Stanley expects Nebius's adjusted EBITDA to increase from $1.22 billion in 2026 to $6.92 billion in 2027, and further to $13.35 billion in 2028. Over the same period, the adjusted EBITDA margin is expected to reach 40%, 57.6%, and 63.5%, respectively.\nHowever, as capital expenditures and financing costs rise, Morgan Stanley expects Nebius to continue facing significant cash flow pressure over the next several years. The firm projects that the company's free cash flow will be negative $21.141 billion, negative $25.113 billion, and negative $27.718 billion in 2026, 2027, and 2028, respectively.\nValuation-wise, Morgan Stanley maintains a $144 price target for Nebius, based on the company's 2028 EBITDA forecast of approximately $13.3 billion, applying a 6x enterprise value to EBITDA (EV/EBITDA) multiple and factoring in net debt levels at the end of 2027.\nThe bank noted that although Nebius's vertically integrated AI cloud computing platform has been recognized by major customers, and its lower total cost of ownership along with the monetization potential of its software business are expected to drive improvements in long-term profit margins and free cash flow, given the capital-intensive nature of the company's operations, its valuation multiples should be below the software industry average.\nIn an optimistic scenario, if Nebius can successfully bring more than 5 GW of power capacity online by the end of 2030, further expand its customer base, and enhance profitability through cost advantages and monetization of its software business, Morgan Stanley believes the stock could reach $400.\nConversely, if AI computing power supply and demand gradually balance out, competition among major cloud service providers intensifies, and data center delivery proves more challenging than expected, the stock's valuation under a pessimistic scenario could fall to $70.\nMorgan Stanley believes that Nebius's current customer demand and pricing trends remain encouraging, but in an environment where computing infrastructure supply is constrained, whether the company can complete large-scale data center construction on schedule, convert contracted capacity into actual revenue, and secure ongoing financing at reasonable costs will be key factors determining its future performance and valuation.",
          "source_name": "news.google.com",
          "published_at": "2026-11-13T16:28:25Z"
        },
        "w1": {
          "result": "NEW",
          "reference_ids": []
        },
        "w2": {
          "policy_hit": true,
          "policy_related": true,
          "related_policy_ids": [
            "pol_70d5e32be9c04fca97b31b622e28c6db",
            "pol_90d7c24fb5d048b8be7bed29ec918066"
          ],
          "policy_ids": [
            "pol_9313ed863ee04004b67d1d8185a951d5"
          ],
          "hit_condition_ids": [
            {
              "condition_ids": [
                "C1"
              ],
              "policy_id": "pol_9313ed863ee04004b67d1d8185a951d5"
            }
          ],
          "matched_condition_ids": null
        },
        "w3_decision": {
          "decision": "LONG",
          "expectation_delta": "摩根士丹利将FY2027收入预测由109.26亿美元上调至120.25亿美元（+10.1%），并将FY2028预测由190.59亿美元上调至210.18亿美元。报告结合更高合同定价和持续需求，形成该机构规模化收入路径的正向修订；FY2026预测下调及交付延期构成抵消。",
          "prior_expectation": "该机构此前FY2027收入预测为109.26亿美元。公司已公布120亿—130亿美元的FY2027初步指引，Q3提价、预付款改善及部分交付延期也已知，但不等同于摩根士丹利自身模型已经上修。",
          "reason": "保留W2的C1命中：新增依据是同机构可比模型上修，而非维持Equal-Weight/$144或重复Q3利好。融资压力和负自由现金流限制正面推论，但未提供同机构资本回报进一步恶化的可比证据。原报告首次公开时刻未核实，不将转载时间视为财报新起点。已检查统一行情，仅使用截止前数据；尚无完整的纯消息后5分钟柱可确认该模型修订已稳定重定价，故未施加价格否决。"
        },
        "direction": "LONG",
        "initiated_at": "2026-11-13T16:32:01.368066+00:00",
        "execution": {
          "output_status": "BANNED_TRADE_DAY",
          "delivery_status": "UNKNOWN",
          "intent_released": true,
          "entry_result": null,
          "filled_qty": null,
          "error": null
        }
      }
    ],
    "contexts": [
      {
        "name": "D1",
        "cutoff_at": null
      },
      {
        "name": "D2",
        "cutoff_at": "2026-10-17T11:05:48.453869+00:00"
      },
      {
        "name": "PolicySet",
        "cutoff_at": "2026-11-13T09:11:48.487586+00:00"
      },
      {
        "name": "Reference View",
        "cutoff_at": null
      }
    ],
    "alignment": {
      "status": "COMPLETE",
      "error": null,
      "hit_has_new_fact": false,
      "related_has_new_fact": true
    }
  },
  "entry_version": "w3-entry-v5",
  "contract_version": "persistent-runtime.w3.v5",
  "output_fields": [
    "decision",
    "expectation_delta",
    "prior_expectation",
    "reason"
  ],
  "instruction": "Inherited AGENTS and unique Skill apply. This is the current phase input; do not reread contracts/task/schema routinely. Research uses w3_research tools with context_ref. Print one text body; follow next_cursor for incomplete relevant content.",
  "context_ref": "w3ctx_d8f739cd5025e5d8d57fe0bc8a3fe0736e782c452746fb3d316779fe57db5518",
  "context_manifest_sha": "9e221c6f7daaeb6d894892f0dab8929adecc6c545bc8d32cb5d1ea747df53883",
  "direct_objects": [
    {
      "kind": "policy",
      "id": "pol_90d7c24fb5d048b8be7bed29ec918066",
      "provided_inline": true
    },
    {
      "kind": "policy",
      "id": "pol_c653295f02044bb29ff7f81a682a0930",
      "provided_inline": true
    },
    {
      "kind": "policy",
      "id": "pol_351a03ffc69e45ddaf14f78487992854",
      "provided_inline": true
    }
  ]
}
```

### post_decision_operations（2026-11-14T04:21:31.522Z）

```json
{
  "phase": "post_decision_operations",
  "input": {
    "submitted_decision": {
      "decision": "NO_TRADE",
      "expectation_delta": "摘要补充2027年收入指引不含额外轻资产安排贡献，并提示不能年化Q3利润率，细化了收入质量与利润判断，但未上调指引、确认新增可用资金或披露新的交付变化。",
      "prior_expectation": "FY2026收入30亿—32亿美元已见于此前研报；FY2027收入120亿—130亿美元、Q4税前盈亏平衡目标、预付款覆盖及资本开支展望均已披露。共址延期和Q3约3.25亿美元一次性收入也已知。",
      "reason": "维持W2相关但未命中：没有新增具名大客户固定采购、重要项目阶段的可用资金或现金责任变化，也没有相对既有排程的新延期。轻资产收入排除说明提供正面解释，但不足以独立形成实质经营预期差；利润率提示也未给出新的下修幅度。电话会复盘不重置11月12日披露的催化时点。本次不交易不依赖价格完成度否决。"
    },
    "output_disposition": "NO_TRADE",
    "candidate_eligibility": null,
    "watch": {
      "limit": 2,
      "used": 2,
      "items": [
        {
          "policy_id": "pol_3f969b5c37a44d2cb9bd01510ae5a9c7",
          "reason": "当前不交易，因规则仍属拟议阶段且对云端远程使用的实际限制及适用范围未明；报道指规则最快本月定案，后续披露可能补足这一关键问题。跟进最终规则是否具约束力、是否限制远程云服务及其覆盖用户范围。",
          "start_semantic_day": "2026-11-12",
          "expires_at": "2026-11-14T07:00:00+00:00"
        },
        {
          "policy_id": "pol_ac34e25398f8445380ccf57f4c4d9571",
          "reason": "跟进Nscale资金缺口规模与来源，以及此前磋商中的Nvidia担保是否转为已生效安排，或融资问题是否影响重要容量履约；若出现可执行且对应重要GPU容量的支持，或服务停止并触发客户重新采购，再按C1/C2评估。目前披露未给出缺口规模或付款期限，也未证明融资失败或服务退出。",
          "start_semantic_day": "2026-11-13",
          "expires_at": "2026-11-15T07:00:00+00:00"
        }
      ]
    },
    "pins": {
      "limit": 2,
      "used": 2,
      "items": [
        {
          "pin_id": "pin_6833a0179f29d04f636fc4d4",
          "message": {
            "body": "← Back to live feed · 1 stories across 1 day\nFriday, Nov 13, 2026\n1 story1 Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change\n AI Nov 12, 10:56 PM EST 51/38 \n Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change\nPartners in a $500 billion credit vehicle designed to finance GPU purchases for AI startups are weighing their participation in the program following the removal of founder and CEO Jensen Huang. At least one lender is reportedly considering an exit from the consortium, according to the New York Times, sparking concerns that the company's strategy of providing financial backstops to insecure customers could leave a massive hole in AI infrastructure funding.\nThe potential collapse of the lending facility follows a period of internal friction over Nvidia's growing exposure to circular financing, in which the company guarantees loans for customers to buy its own chips. These off balance sheet obligations surged to $530 billion in the quarter ending July 26. Interim CEO Colette Kress has already begun reducing the firm's risk profile, including scaling back a guarantee for an OpenAI data center in Ohio to $105 billion from an original proposed $250 billion.",
            "published_at": "2026-11-13T17:56:27.005000Z",
            "source_name": "huggingnews.com",
            "title": "Lenders Consider Withdrawal From Nvidia's $500B AI Credit Project After Leadership Change"
          },
          "reason": "保留贷款方据报道考虑退出英伟达5000亿美元AI融资联盟这一未确认状态，供后续融资进展解读，避免把相关确认或变化误判为首次出现。",
          "expires_at": "2026-11-14T18:01:51.926289+00:00"
        },
        {
          "pin_id": "pin_68df4fc4b665148d2fe75a94",
          "message": {
            "body": "← Back to live feed · 1 stories across 1 day\nFriday, Nov 13, 2026\n1 story1 NEWMarket Concerns Over AI Stability Jump to 87% in Latest Fed Report\n AI Nov 13, 4:08 PM EST 8/7 \n NEWMarket Concerns Over AI Stability Jump to 87% in Latest Fed Report\nThe Federal Reserve has flagged potential vulnerabilities in the financial system stemming from artificial intelligence and high asset valuations in its latest semi-annual report on financial stability. A survey of market contacts found that 87% now view AI as a salient risk to stability, a sharp increase from 50% in the spring, citing concerns over complex financing, cyberattacks and potential earnings disappointments.\nThe central bank warned that the accelerating pace of investment in AI computing could soon exceed that of the 1990s telecom cycle. Debt issuance for advanced computing reached $680B through October, and outstanding debt in the sector now exceeds $1.1T. Other obligations include $1T in future lease commitments and more than $2T in take-or-pay contracts. The report also noted that hedge fund leverage has reached its highest level since 2013, with data current as of Oct. 28.",
            "published_at": "2026-11-13T21:39:01.151000Z",
            "source_name": "huggingnews.com",
            "title": "Market Concerns Over AI Stability Jump to 87% in Latest Fed Report"
          },
          "reason": "保留报告中的AI风险关注度、行业债务与承诺规模及杠杆背景，供后续判断AI基础设施融资消息是否带来行业层面的新增压力；这些汇总数据不代表NBIS自身义务或融资收紧。",
          "expires_at": "2026-11-14T21:42:06.572616+00:00"
        }
      ]
    },
    "allowed_reference_event_ids": [
      "E102",
      "E104",
      "E105",
      "E106",
      "E107",
      "E109",
      "E111",
      "E116",
      "E117",
      "E118",
      "E12",
      "E120",
      "E121",
      "E122",
      "E123",
      "E129",
      "E13",
      "E137",
      "E138",
      "E14",
      "E146",
      "E148",
      "E15",
      "E150",
      "E152",
      "E155",
      "E156",
      "E159",
      "E16",
      "E161",
      "E162",
      "E163",
      "E165",
      "E166",
      "E167",
      "E168",
      "E169",
      "E17",
      "E170",
      "E172",
      "E173",
      "E174",
      "E175",
      "E176",
      "E179",
      "E180",
      "E181",
      "E183",
      "E184",
      "E186",
      "E187",
      "E188",
      "E189",
      "E190",
      "E193",
      "E197",
      "E198",
      "E199",
      "E2",
      "E201",
      "E202",
      "E203",
      "E208",
      "E209",
      "E21",
      "E212",
      "E213",
      "E214",
      "E215",
      "E219",
      "E224",
      "E226",
      "E228",
      "E229",
      "E230",
      "E232",
      "E234",
      "E236",
      "E238",
      "E240",
      "E243",
      "E244",
      "E247",
      "E248",
      "E249",
      "E25",
      "E250",
      "E251",
      "E252",
      "E253",
      "E254",
      "E258",
      "E26",
      "E260",
      "E262",
      "E264",
      "E265",
      "E266",
      "E267",
      "E268",
      "E269",
      "E270",
      "E273",
      "E274",
      "E275",
      "E279",
      "E28",
      "E280",
      "E284",
      "E285",
      "E286",
      "E287",
      "E288",
      "E289",
      "E290",
      "E292",
      "E294",
      "E295",
      "E298",
      "E299",
      "E3",
      "E302",
      "E303",
      "E304",
      "E305",
      "E310",
      "E311",
      "E314",
      "E315",
      "E316",
      "E317",
      "E318",
      "E319",
      "E322",
      "E323",
      "E325",
      "E326",
      "E327",
      "E330",
      "E331",
      "E334",
      "E335",
      "E336",
      "E337",
      "E338",
      "E339",
      "E34",
      "E340",
      "E341",
      "E342",
      "E343",
      "E344",
      "E345",
      "E346",
      "E347",
      "E348",
      "E349",
      "E350",
      "E351",
      "E352",
      "E353",
      "E354",
      "E355",
      "E356",
      "E357",
      "E358",
      "E359",
      "E360",
      "E361",
      "E362",
      "E363",
      "E364",
      "E365",
      "E366",
      "E367",
      "E368",
      "E369",
      "E370",
      "E371",
      "E372",
      "E373",
      "E374",
      "E375",
      "E376",
      "E377",
      "E378",
      "E379",
      "E38",
      "E380",
      "E381",
      "E382",
      "E383",
      "E384",
      "E385",
      "E386",
      "E387",
      "E388",
      "E389",
      "E390",
      "E391",
      "E392",
      "E393",
      "E394",
      "E395",
      "E396",
      "E397",
      "E398",
      "E399",
      "E400",
      "E401",
      "E402",
      "E403",
      "E404",
      "E405",
      "E406",
      "E407",
      "E408",
      "E409",
      "E410",
      "E411",
      "E412",
      "E413",
      "E414",
      "E415",
      "E416",
      "E417",
      "E418",
      "E419",
      "E42",
      "E420",
      "E421",
      "E422",
      "E423",
      "E424",
      "E425",
      "E426",
      "E427",
      "E428",
      "E429",
      "E43",
      "E430",
      "E431",
      "E432",
      "E433",
      "E434",
      "E435",
      "E436",
      "E437",
      "E438",
      "E439",
      "E440",
      "E441",
      "E442",
      "E443",
      "E444",
      "E445",
      "E446",
      "E447",
      "E448",
      "E449",
      "E45",
      "E450",
      "E451",
      "E452",
      "E453",
      "E454",
      "E455",
      "E456",
      "E457",
      "E458",
      "E459",
      "E460",
      "E461",
      "E462",
      "E463",
      "E464",
      "E465",
      "E466",
      "E467",
      "E468",
      "E469",
      "E47",
      "E470",
      "E471",
      "E472",
      "E473",
      "E474",
      "E475",
      "E476",
      "E477",
      "E478",
      "E479",
      "E48",
      "E480",
      "E481",
      "E482",
      "E483",
      "E484",
      "E485",
      "E486",
      "E487",
      "E488",
      "E489",
      "E490",
      "E491",
      "E492",
      "E493",
      "E494",
      "E495",
      "E496",
      "E497",
      "E498",
      "E499",
      "E5",
      "E500",
      "E501",
      "E502",
      "E503",
      "E504",
      "E505",
      "E506",
      "E507",
      "E508",
      "E509",
      "E51",
      "E510",
      "E511",
      "E512",
      "E513",
      "E514",
      "E515",
      "E516",
      "E517",
      "E518",
      "E519",
      "E520",
      "E521",
      "E522",
      "E523",
      "E524",
      "E525",
      "E526",
      "E527",
      "E528",
      "E529",
      "E530",
      "E531",
      "E532",
      "E533",
      "E534",
      "E535",
      "E536",
      "E537",
      "E538",
      "E539",
      "E54",
      "E540",
      "E541",
      "E542",
      "E543",
      "E544",
      "E545",
      "E546",
      "E547",
      "E548",
      "E549",
      "E550",
      "E551",
      "E552",
      "E553",
      "E554",
      "E555",
      "E556",
      "E557",
      "E558",
      "E559",
      "E560",
      "E561",
      "E562",
      "E563",
      "E564",
      "E565",
      "E566",
      "E567",
      "E568",
      "E569",
      "E570",
      "E571",
      "E572",
      "E573",
      "E574",
      "E575",
      "E576",
      "E577",
      "E578",
      "E579",
      "E58",
      "E580",
      "E581",
      "E582",
      "E583",
      "E584",
      "E585",
      "E586",
      "E587",
      "E588",
      "E589",
      "E59",
      "E590",
      "E591",
      "E592",
      "E593",
      "E594",
      "E595",
      "E596",
      "E597",
      "E598",
      "E599",
      "E6",
      "E600",
      "E601",
      "E602",
      "E603",
      "E604",
      "E605",
      "E606",
      "E607",
      "E608",
      "E609",
      "E61",
      "E610",
      "E611",
      "E612",
      "E613",
      "E614",
      "E615",
      "E616",
      "E617",
      "E618",
      "E619",
      "E620",
      "E621",
      "E622",
      "E623",
      "E624",
      "E625",
      "E626",
      "E627",
      "E628",
      "E629",
      "E63",
      "E630",
      "E631",
      "E632",
      "E633",
      "E634",
      "E635",
      "E636",
      "E637",
      "E638",
      "E639",
      "E64",
      "E640",
      "E641",
      "E642",
      "E643",
      "E644",
      "E645",
      "E646",
      "E647",
      "E648",
      "E649",
      "E650",
      "E651",
      "E652",
      "E653",
      "E654",
      "E655",
      "E656",
      "E657",
      "E658",
      "E659",
      "E660",
      "E661",
      "E662",
      "E663",
      "E664",
      "E665",
      "E666",
      "E667",
      "E668",
      "E669",
      "E670",
      "E671",
      "E672",
      "E673",
      "E674",
      "E675",
      "E676",
      "E677",
      "E678",
      "E679",
      "E68",
      "E680",
      "E681",
      "E682",
      "E683",
      "E684",
      "E685",
      "E686",
      "E687",
      "E688",
      "E689",
      "E690",
      "E691",
      "E692",
      "E693",
      "E694",
      "E695",
      "E696",
      "E697",
      "E698",
      "E699",
      "E7",
      "E70",
      "E700",
      "E701",
      "E702",
      "E703",
      "E704",
      "E705",
      "E706",
      "E707",
      "E708",
      "E709",
      "E710",
      "E711",
      "E712",
      "E713",
      "E714",
      "E715",
      "E716",
      "E717",
      "E718",
      "E719",
      "E720",
      "E721",
      "E722",
      "E723",
      "E724",
      "E725",
      "E726",
      "E727",
      "E728",
      "E729",
      "E730",
      "E731",
      "E732",
      "E733",
      "E734",
      "E735",
      "E736",
      "E737",
      "E738",
      "E739",
      "E74",
      "E740",
      "E741",
      "E742",
      "E743",
      "E744",
      "E745",
      "E746",
      "E747",
      "E748",
      "E749",
      "E75",
      "E750",
      "E751",
      "E752",
      "E753",
      "E754",
      "E755",
      "E756",
      "E757",
      "E758",
      "E759",
      "E76",
      "E760",
      "E761",
      "E762",
      "E763",
      "E764",
      "E765",
      "E766",
      "E767",
      "E768",
      "E769",
      "E770",
      "E771",
      "E772",
      "E773",
      "E774",
      "E775",
      "E776",
      "E777",
      "E778",
      "E779",
      "E78",
      "E780",
      "E781",
      "E782",
      "E783",
      "E784",
      "E785",
      "E786",
      "E787",
      "E788",
      "E789",
      "E790",
      "E791",
      "E792",
      "E793",
      "E794",
      "E795",
      "E796",
      "E797",
      "E798",
      "E799",
      "E80",
      "E800",
      "E801",
      "E802",
      "E803",
      "E804",
      "E805",
      "E806",
      "E807",
      "E808",
      "E809",
      "E81",
      "E810",
      "E811",
      "E812",
      "E813",
      "E814",
      "E815",
      "E816",
      "E817",
      "E818",
      "E819",
      "E820",
      "E821",
      "E822",
      "E823",
      "E824",
      "E825",
      "E826",
      "E827",
      "E828",
      "E829",
      "E830",
      "E831",
      "E832",
      "E833",
      "E834",
      "E835",
      "E836",
      "E84",
      "E88",
      "E97"
    ],
    "reference_rule": "novelty.reference_ids accepts only the listed E# Event IDs, not F#, PIN IDs, news IDs or URLs. OLD requires actual fact coverage by one of these Events. Previously seen articles and insufficient body are not Event coverage."
  },
  "entry_version": "w3-entry-v5",
  "contract_version": "persistent-runtime.w3.v5",
  "output_fields": [
    "delta_candidates",
    "novelty",
    "pin_actions",
    "watch_actions"
  ],
  "instruction": "Inherited AGENTS and unique Skill apply. This is the current phase input; do not reread contracts/task/schema routinely. Research uses w3_research tools with context_ref. Print one text body; follow next_cursor for incomplete relevant content.",
  "context_ref": "w3ctx_31c4d020271878292012c97e6ab6d3276106aac304d2959f68f4a1fc35a02818",
  "context_manifest_sha": "a5f7dd6e62aedf1f9ba4e11192d3cb97280eb6748f81253fced04fd6a64c2e22",
  "direct_objects": []
}
```

