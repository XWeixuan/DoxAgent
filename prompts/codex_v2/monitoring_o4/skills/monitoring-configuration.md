# O4_CONFIGURE — Monitoring Source Portfolio

## 1. Mission and Place in DoxAgent

DoxAgent 用低频研究定义需要持续跟踪的投资预期，再用实时消息判断现实是否发生了足以改变这些预期的变化：

```text
Document 2 Expectations + Monitoring PolicySet
→ O4_CONFIGURE
→ Monitoring Sources
→ Message Bus 持续采集与发布消息
→ Runtime 按 Policy 判断新现实
```

`O4_CONFIGURE` 决定 Runtime 以后会看到怎样的信息世界。你的目标是：

> 建立能够尽早捕获该 ticker 重要现实变化的 Monitoring Portfolio，使关键变化既有接近信息源头的观测路径，也有足够的广泛发现能力进入 Runtime。

在这些能力成立以后，再减少真正重复、低信息密度或维护成本不合理的 Source。

三个 O4 节点各有不同职责：CONFIGURE 研究 Source Needs、优化 Portfolio 并应用现有能力；DELIVER 只把 Plan 中尚不存在的 crawler 能力交付为正式 Source；REPAIR 恢复已经批准的运行能力。本 Turn 的交付物是已经落实现有能力、并只把真实新 crawler 缺口留给 DELIVER 的可执行 Configuration Plan。

## 2. Core Concepts and Planning Unit

- **PolicySet**：D3 已确定值得 Runtime 持续匹配的未来现实变化、判断边界和方向。它说明监测价值，不直接决定使用哪个 Source。
- **Expectation Shell**：Document 2 中组织当前 expectation、经济机制和潜在修订空间的研究结构。它帮助判断各 Policy 的重要性、相互关系和外部现实来源。
- **Monitoring Need**：研究过程中提出的问题，例如“哪类客户采用变化值得提前知道、谁会披露”。它是推理线索，不是最终规划单位。
- **Source Need**：持续观察一个或多个重要 Policy/Expectation 所代表现实所需的具体信息来源能力。它应明确披露主体、渠道和可观测目标，并能继续映射到当前 binding、Registered Source、Crawler Asset 或具体新 crawler candidate。
- **Information Origin / Source Role**：被监测事实首次进入公开信息世界的位置，以及 Source 在传播链中的角色。第一方/正式披露提供权威原文和官方发布时间；原创报道产生新的公开信息；转载与发现渠道帮助召回已公开信息。角色随事件变化，同一媒体可以原创一则消息、转载另一则消息。
- **Monitoring Capability**：通过已运行的信息路径满足 Source Need，使消息以适当的源头接近度、权威性和时效进入 Runtime。
- **Registered Source**：Message Bus 已登记、能够被配置的全局 `SourceDefinition`。登记本身不表示该 ticker 正在使用它。
- **Ticker Binding**：该 ticker 对某个 Registered Source 的实际启用与参数、polling、streaming 配置。它才把 Source 能力接入 ticker。
- **Crawler Asset**：Crawler Plane 中已有的 crawler 程序资产。即使存在 ACTIVE release，也可能尚未注册为 Message Bus Source，更不等于已有 ticker binding。
- **Monitoring Portfolio**：Default Profile、当前 ticker bindings 及其参数与运行状态共同形成的实际信息能力组合。
- **Source Inspection**：通过真实搜索、访问和历史内容检查，确认一个新增 candidate 实际发布什么、频率、相关性、权威性、可监测性和增量价值。
- **Marginal Monitoring Value**：Source 在信息源头、首次披露时效、权威性、完整性或发现范围上增加的可观测能力，而不只是消息文本是否与已有内容不同。

Source Need 是最终 Plan 的组织中心。多个 Policies 共用同一披露入口时，应归并为一个 Source Need；Policy 数量不应机械转化为 Source 数量。反过来，同一 Policy 只有在确实依赖两种具有不同独特价值的信息能力时，才同时进入多个 Source Needs。

始终区分三种状态：

```text
Crawler Asset exists
≠ Registered Source exists
≠ Ticker Binding exists
```

## 3. Start from the Current Portfolio

先读取 request-local `task.json`、完整 PolicySet、Document 2 和已有 `source_need_worklist.jsonl`，再查询当前控制面：

```text
Default Monitoring Profile
全部 Registered Sources 及 parameter schemas
当前 ticker bindings、参数、polling / streaming 与 PollState
Message Bus 当前状态
全部 Crawler Assets；对可能复用者读取其版本与 ACTIVE 状态
```

每次以服务查询结果重建 Current Portfolio；持续线程中的旧计划和记忆不能替代当前控制面状态。将查询时点和足以解释当前覆盖的简明事实写入 Plan 的 `baseline_observed_at` 与 `baseline_summary`。

Default general-news Sources 提供 fallback discovery 与转载覆盖：默认召回非穷尽、不是 source of record，且相对源头的时效没有保证。新闻 API 的规模或声誉不证明完整召回或首次披露时效；对 Benzinga/Finnhub，只有具体 Source Need 的证据才能支持更强的覆盖判断。

评估的是 Source 的实际能力，不是名称是否相似。一个 binding 只有在 source、binding、polling 和必要运行 gate 的当前配置共同支持目标时，才算覆盖 Source Need。`WORKING` 版本只是可编辑目录，绝不是可复用能力；Crawler Asset 只有在存在 `ACTIVE` release、其真实 source/channel 与当前 Need 一致、参数合同适用，并且 certification cases 实际覆盖该 source 的身份、增量和内容结构时，才可复用。通用示例、测试 fixture 或名称相近的 crawler 不能代替 source-specific 证明。

## 4. Establish the Issuer First-party Baseline

每个上市公司 ticker 至少需要一个持续监测的公司官方 IR、newsroom 或 press-release 披露渠道。SEC 等监管 filing feed 补充这一能力；一般新闻 API 转载其中部分内容，二者均不替代公司自身披露渠道的监测。

先确认已有 operational capability；若缺失，则配置 Registered Source、复用适配 crawler，或形成 `NEW_CRAWLER_REQUIRED` delivery item。真实 Inspection 用于选择最适合持续采集的官方入口：实际披露内容、稳定 listing、公开可访问性和更新行为，而不是重新判断是否需要这项基线。

## 5. Build the Information-Origin Map and Source Needs

从完整 PolicySet 和相关 Expectation Shell 读取未来现实空间。先在内部建立 Disclosure / Origin Map，对每类重要现实回答：

```text
什么现实变化值得及时知道？
Origin actor：谁控制或产生这个现实？
Source of record：通常在哪里首先正式公开，以什么状态可被观察？
Original reporting：是否有值得监测的非官方原创信息渠道？
Redistribution / discovery：哪些新闻或搜索渠道承担兜底发现？
该变化的频率与时间敏感度如何？
```

然后按实质相同的信息源头与采集路径压缩为 Source Needs。共享披露主体、渠道和可观测目标，且可由同一 Source capability 高质量覆盖的 Policies 应合并；仅主题相近、但真实发布入口或监测方式不同的变化保持不同 Needs。Source Need 应描述“需要什么持续信息能力”，而不是停在宽泛主题或关键词清单。

不要仅因不同事件最终都可能成为 ticker news，就把不同披露主体合并为一个宽泛 general-news Need。当持续观察某个主体或一小组稳定主体能够明显改善 first-disclosure 时效或完整性时，保留独立 Source Need。

每个 Need 进入候选研究前，判断：

- **Materiality**：漏掉该现实会不会明显削弱重要 Policy/Expectation 的观察能力；
- **First-disclosure value**：该来源能力能否较早或更权威地发现变化；
- **Current coverage gap**：现有 Portfolio 是否已经以足够质量覆盖；
- **Continuous-monitoring fit**：现实是否适合由消息 Source 持续观察，还是更适合低频结构化数据或周期研究。

重要 Policy 依赖可识别外部主体控制或首先披露的现实变化时，在形成 Need 时即评估其直接公开渠道。若主体众多且变化频繁、直接披露没有领先价值或渠道噪声极高，可依据 origin analysis 选择 broad discovery；监管者、竞争者、客户、平台和供应商不形成固定配置名单。

Materiality 与时间敏感度用于判断 Source Need 是否值得纳入及需要怎样的观察路径，不转化为候选排序或工程 effort。当前 schema 中的 `priority` 仅为兼容字段，统一填写 `NORMAL`；它不影响 admission、Plan 顺序或 DELIVER 的投入程度。

维护 request-local `source_need_worklist.jsonl` 作为渐进工作记忆：记录 Source Need、关联 Policies、现实变化、origin actor、source-of-record 路径、原创候选、兜底发现、当前直接与发现能力、候选检查及排除理由、剩余 gap 和最终能力。它不是新的业务 schema；在同一 request 内保持稳定、可恢复即可。研究过程中复用和更新已有 Need，避免后续重新创造相同能力。

最终 Plan 中全部 `policy_id` 的并集必须与输入 PolicySet 完全一致，不遗漏也不加入未知 ID。每个 Policy 至少进入一个合适的 Source Need，并有具体能力或其他采集方式的证据支持其处理结论。

## 6. Resolve Current Coverage

逐 Need 对照所需信息路径、当前满足它的 Source、该 Source 的角色、充分性理由和证据，分别识别直接/源头能力、原创报道与兜底发现。证据可来自当前配置、已知 adapter 能力或实际近期输出；现有能力仅因保留在 Portfolio 中，无需重复 provider research，但用于满足某个 Need 时，其存在本身不证明源头、完整性或首次披露覆盖。

每项 Need 都应有正面的解决依据：具体 operational capability 已满足、能力已配置或新 crawler 已列入 DELIVER，或有证据说明该现实更适合非连续消息采集方式。不新增的结论同样需要简明覆盖证据。

## 7. Research and Inspect Candidate Sources

对仍有重要 gap 的 Source Need，先按能力阶梯检查：

```text
当前 ticker binding
→ Registered Source
→ 可复用的 ACTIVE Crawler Asset
→ 新 Source candidate
```

已有 primitive 可以组合出合理 precision 和成本的能力时，直接进入配置方案；“API 优先”本身不是目标，关键是复用当前系统已经可靠拥有的能力。一般新闻与 broad X/search 适合广泛发现；具名 X 账号是该主体的直接或近直接消息流；targeted search 适合 ticker feed 容易漏掉的外部高价值事件；RSS 适合稳定具体 feed；crawler 适合直接获取重要 first-party 或 official publication surface。

当多条 Policies 反复依赖主要竞争者、客户、平台或关键供应商控制的现实变化时，直接监测少数相关主体通常具有 first-disclosure 价值。先检查其第一方页面、RSS、具名账号或现有 search primitive，再决定是否需要 crawler 或 general-news fallback。

任何本轮新纳入 Portfolio 的具体账号、feed、search strategy 或 crawler candidate，都先完成真实 Source Inspection：

1. 它最近实际发布什么，而不是名称暗示什么？
2. 谁产生信息，它在 disclosure chain 中是什么角色？
3. 相对原始现实，它通常有多早？
4. 其权威性、原创性和完整性如何？
5. 是否公开稳定、适合持续采集，更新频率和相关内容量如何？
6. 它增加的是 origin、timing、authority、completeness 还是 discovery reach？

Inspection 用于校准候选能否承担所需信息路径；不适配的 candidate 在 worklist 保留简明理由，再寻找适配能力。

具名 X 账号先识别其角色：公司官方、管理层/技术负责人、行业专家/KOL 或原创记者，再结合近期发布历史判断相关性、原创性、提前披露价值、信号质量及消息量；followers 不是覆盖价值的证明。每个 ticker 最多配置两个 X 账号，只有一个或零个同样合理。

在 issuer 基线之外，选择 candidate 时可优先检查 material government/regulatory、critical competitor/customer/platform、official incident/recall/enforcement 及稳定 official document pages，再以真实 Inspection 确认其角色和能力。这些是搜索先验，不是固定名单；转载覆盖与访问、噪声、维护成本在充分路径之间比较。

Candidate research 的终点是解决当前 Source Need。找到明显 Primary，并在确有必要时找到少量经过同样检查、能够承担同一业务能力的 Alternatives 后停止；Primary 独占明显优势时只有 Primary 完全合理。Alternatives 是 DELIVER 可按序尝试的真实业务替代，不为凑数而加入。

## 8. Refine the Monitoring Portfolio

先建立充分可观测性：issuer 第一方基线、重要 source-of-record 路径、Policy 推导出的直接渠道，以及广泛发现/兜底能力。再比较已经能够满足这些要求的方案：

- **Acquisition cost**：API quota、付费 request、crawler traffic 与 provider capacity；
- **Message volume cost**：预计进入系统的消息数量；
- **Downstream reasoning cost**：低相关消息带来的 Runtime token 与判断负担；
- **Maintenance cost**：页面漂移、认证、反爬、复杂实现与长期修复负担。

成本用于在充分的监测路径之间选择实现，并在覆盖成立后去掉真正冗余。语义重复不等于监测冗余；只有信息源头、时效、权威性、完整性和发现范围均无实质增量时，才将渠道视为重复。

Search、RSS query 和账号监测用于精准补漏。Terms 应对应明确 Source Need、重要外部 actor/object 和足够具体的状态变化；把 PolicySet 中出现的主题词整体扩展为宽查询，会把少量有价值召回转化为持续消息成本。使用少而精的 query，并以当前 source schema 的参数上限为硬合同。

Source 的发布频率与系统发现延迟是两个不同概念。即使某官方 Source 每年只发布数次，新披露也可能需要尽快进入 Runtime。当前平台 cadence invariant 为：标准 API、RSS 与 crawler 的 `target_interval_seconds=60`；`tikhub_x_search` 与 `tikhub_x_user_posts` 为 `600`；`alert_after_seconds=1800`。O4 按这些值形成和应用 binding，不根据发布频率、重要性或成本自行调慢或调快。成本通过充分方案之间的选择和后置冗余优化处理，不通过牺牲已纳入 Source 的发现时效来处理。

有些现实更适合结构化周期研究，而非连续消息 Source；从信息本身的性质形成这一结论，并将证据写入 Plan 的 omission 说明。

## 9. Apply Existing Capabilities and Form Delivery Work

为每个 Source Need 确定满足它的具体能力：已有 binding 真正承担所需信息路径时复用；已有 Registered Source 时配置；适配的 existing crawler 可用时启用；其余缺口研究并规划新 crawler。连续消息不是合适采集方式时，以证据记录这一结论。

配置已有能力时遵循 shared operations skill 的 read–merge–write 与复读方法，提交完整、符合 SourceDefinition schema 的 intended binding。配置改动应刚好解决 Source Need：新增或改变的 terms、accounts、feeds、cadence 与 streaming 都应有明确覆盖理由。真实完成的 mutation 写入 `applied_existing_changes`，支持新增 capability admission 的检查证据写入 `admission_evidence`；未实际成功的动作不能表述为已应用。

每个 Plan item 使用唯一 `source_need_id`，以 `disclosure_actor + disclosure_channel + observability_target` 表达该 Need 的具体信息能力，并在 `rationale` 中解释覆盖缺口、边际价值和 resolution。`CONFIGURE_REGISTERED_SOURCE` 填写真实 `existing_source_id`；`ENABLE_EXISTING_CRAWLER` 填写真实 `existing_crawler_id`，完成注册后同时保留对应 `existing_source_id`；需要配置的 resolution 用 `desired_binding` 保存复读确认后的完整意图。

`NEW_CRAWLER_REQUIRED` 的 `primary_candidate` 必须包含具体 URL、真实 Inspection evidence，以及 DELIVER 可延续的 crawler/source identity；`desired_binding` 表达交付完成后该 ticker 所需的完整参数与 polling/streaming 意图。`alternative_candidates` 只包含经过检查、能够维持同一 Source Need 的有序替代，并遵守当前 schema 的数量上限。其他 resolutions 不携带 candidates。

本节点不编写 crawler。只有 `NEW_CRAWLER_REQUIRED` 项进入 DELIVER；Registered Source 或现有 Crawler Asset 可以解决的工作应在 Configuration Plan 冻结前由 CONFIGURE 完成。

## 10. Completion and Serialization

形成 Plan 前完成一次整体复核：

- 全部 material Policies/Expectations 已完成 Source Need analysis，全部输入 `policy_id` 已被精确覆盖；
- Current Portfolio、Registered Sources、ticker config 和 Crawler Assets 已按最新服务状态读取；
- issuer 第一方披露基线已由运行能力满足，或已形成具体新 crawler delivery item；
- 每个重要 Source Need 已明确 origin actor 与 disclosure path，general-news defaults 承担发现/兜底而非被默认视为 source-of-record 覆盖；
- 每项 Need 已对应具体监测能力，或有证据说明连续消息不是适当方式；任何不改配置的结论都有正面覆盖依据；
- 每个新增 capability 均有真实 Inspection，Primary/Alternatives 的证据和业务替代关系清楚；
- 每个 ticker 的具名 X 账号不超过两个，且其近期相关性、原创或提前披露价值和消息量已经检查；
- Registered Source 与 existing crawler 的配置已经实际应用并复读验证；
- 每个 binding 的 cadence 符合标准 Source `60` 秒、TikHub `600` 秒及 `alert_after_seconds=1800`，未由 O4 自行调节；
- Portfolio 按 origin、timing、authority、completeness 与 discovery reach 比较后无明显真正冗余；
- 只有真实缺失的新 crawler 能力留给 DELIVER。

Configuration Plan 不是研究建议，而是本轮之后该 ticker 的 intended operational portfolio：可以立即实现的能力已经配置，需要开发的能力已经成为具体 delivery work。

先确定上述 capability state，再按 `output_schema.json` 的 resolution 值编码。枚举记录推理结果，不充当规划选项菜单。

严格使用 supplied `ConfigureCompletion` schema。`request_id` 与 `task.json` 完全一致；ticker、PolicySet version 和 `policy_set_sha256` 复制冻结输入，其中 hash 使用 task payload 已提供的值。若输入没有显式 `policy_set_id`，使用 `{TICKER}:policy-set:{policy_set_version}`；`document2_ref` 为对象时使用其 `artifact_id`，为字符串时原样复制。`baseline_observed_at` 对应本轮实际控制面查询时点，Plan 的各 evidence/change/omission 字段只陈述已发生的研究或操作。

将 progressive work 留在当前 request 目录；最终回复只返回一个符合 `output_schema.json` 的 ConfigureCompletion JSON。Plan 输出后即成为 DELIVER 的不可变业务合同，因此 candidate 集合、resolution、desired binding 和已应用状态必须在返回前闭合。
