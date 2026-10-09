# Document2 v2.1｜O0 Prompt/Skill 重写方案 Part 1

## 1. 本 Part 的范围与共同设计原则

本 Part 只重写：

- `skills/candidate-discovery.md`
- `skills/shell-synthesis.md`

跨节点编排、输入方式和输出 Schema 均以当前 v2.1 实现为既定约束，不做工作流重构。

两份 Skill 的共同目标是让 O0 掌握一种新的 Research Topology Construction 心智：

> **Candidate Discovery 发现的是未来仍值得持续维护判断的经济对象；Shell Synthesis 决定这些对象中哪些值得独立维护，以及哪些对象适合共享一个长期研究上下文。**
>
> O0 不提前决定这些对象当前到底 bullish / bearish，不提前建立完整 realization chain，也不规定什么未来事实才足以证明对象的某种结果。

当前实现已经把 O0 目标定义为 Research Topology，Unit 是持续维护的经济预期对象，State / Baseline / Factor / Gap 留给 O1；两份 Skill 必须围绕这一点重写，而不能继续使用旧版的“middle-level proposition”逻辑。

旧版中仍然值得保留的能力主要有：

- Candidate Discovery 的 **recall-first** 偏好；
- 不把已经发生的事实、裸指标、单一机制直接提升为 Unit；
- 不同研究来源是不同观察角度，而不是架构分区；
- Synthesis 中 **Unit 判断与 Shell 判断分离**；
- 因果关系可以跨 Shell；
- context overlap 不具有传递性；
- Shell 应对应一个 O1 能够长期深度维护的研究上下文；
- 所有候选必须可追溯、不能在 Synthesis 中静默消失。

旧 Candidate Skill 已经明确采用“宁可多召回”的错误偏好，这一点应继续保留。 旧 Synthesis 对共享研究上下文、跨 Shell 因果联系以及非传递性边界的分析也仍然有价值。 

真正需要替换的是：

> **question / proposition / validation path**

这套中心概念。

---

# 2. `candidate-discovery.md` 重写方案

## 2.1 整体章节结构

新版建议控制在六个主体章节：

```text
# Candidate Discovery internal skill

## Purpose and mental model
## What counts as a Candidate Unit
## Discover durable expectation objects
## Judge granularity and independent updateability
## Use the assigned research
## Candidate record and completion
```

不要再在本 Skill 中介绍 Shell。

Candidate Discovery 此时不需要知道未来如何分组，也不应该为了“以后好组成 Shell”而调整对象粒度。

---

# 3. Candidate Discovery｜Purpose and mental model

这一段是整份 Skill 最重要的部分。

目标不是先讲 Schema，而是先让 Agent 理解：

> **自己到底在发现什么。**

建议参考表述：

> **Candidate Discovery identifies economic subjects that will remain worth forming and revising expectations about as new information arrives.**
>
> A Candidate Unit is not today's conclusion about that subject. It is the subject whose future state, trajectory, or economic significance will continue to matter after today's evidence becomes stale.
>
> Current facts, forecasts, tensions, and unresolved questions are evidence for discovering that subject. They do not have to become the Unit's permanent definition.

这几句话解决三个问题：

1. Unit 必须面向未来；
2. Unit 不是当前事实；
3. Unit 也不是当前报告对未来提出的那一句问题。

随后立即加入 Materiality：

> The subject must matter economically. A meaningful future change in it should be capable of changing expectations about the company's business, earnings, cash generation, risk, capital needs, or valuation.

但不要把这一句话进一步压成：

> “必须最终影响收入/利润才能成为 Unit”。

Materiality 是判断它是否值得长期维护，不是规定 Unit 的 terminal outcome。

---

# 4. Candidate Discovery｜What counts as a Candidate Unit

这一节应给 Agent 一个**正面定义**，而不以排除清单为主。

推荐核心定义：

> **A Candidate Unit is a durable economic object with a future that remains open, materially matters to the company, and can be meaningfully updated by future evidence without requiring the entire company thesis to move with it.**

然后解释三个关键词。

## 4.1 Durable economic object

它应该是现实经济世界中的稳定研究对象，例如：

- 某类需求；
- 某个业务；
- 某类客户或市场采用；
- 某个商业化方向；
- 某类盈利或现金流对象；
- 某种资本结构或资金来源。

但这里不要在 Skill 中固定 taxonomy。

核心判断是：

> **如果今天这篇报告的具体结论反转了，这个名字本身是否仍然值得研究？**

参考表述：

> A good Candidate name survives a reversal in current evidence. If the current outlook turns from positive to negative, the research subject should normally remain the same.

例如：

> `AI基础设施需求`

在需求变强或变弱时都成立。

而：

> `AI基础设施需求能否转化为可重复订单`

已经把当前想验证的 transmission path 写进了身份。

---

## 4.2 Future-facing

用户特别强调这一点，应在 Skill 里单独讲清楚。

推荐表述：

> The Candidate must point beyond the current snapshot. Ask:
>
> **What is it that we will still need to maintain a view on when new evidence arrives next quarter, next product cycle, or after the next major industry change?**
>
> A reported fact may reveal the subject, but the reported fact itself is not the Candidate.

例如：

> “Q2服务器单位增长9%”不是 Candidate。

它可能提示：

> “企业与云服务器需求”值得持续研究。

---

## 4.3 Independently updateable

不能把“独立更新”理解成：

> 必须有一套与其他 Unit 完全不同的新闻。

应改成：

> **未来一条重要新信息可以显著改变我们对这个对象的判断，而不必同时强迫我们对相邻的所有对象作相同程度的修改。**

参考表述：

> Independent updateability is about the economic judgment being maintained, not about having a unique news feed or mutually exclusive evidence.
>
> Two Units may share evidence and causal links while still being independently updateable if the same evidence can change their outlooks differently.

这一点是避免为了“独立”而疯狂细拆的重要约束。

---

# 5. Candidate Discovery｜Discover durable expectation objects

这是 Skill 的核心方法论部分。

不要再像旧版那样：

> For each unresolved issue, ask what future judgment remains open.

旧版正是从“未决问题”直接生成 Candidate question。

新版应教 Agent 做一次**抽象转换**。

建议采用四步方法。

---

## Step 1：找到 source 中真正重要的变化、争议或未来判断

可以从：

- 当前事实；
- 管理层预期；
- 行业驱动；
- 关键外部主体；
- 传导关系；
- 市场定价问题；
- unknowns；
- future nodes；

中获得线索。

但不要要求 Candidate 必须来自某个固定 Section。

C1 本身会同时包含当前经营事实、管理层预期、核心驱动、传导链和未知项。 

C3 又会同时包含外部行业状态、关键主体、传导机制、商业化阶段和未来问题。

因此 Skill 应明确：

> **Do not extract Candidates by section heading. Follow the economic meaning of the research.**

---

## Step 2：追问“这些材料背后，真正需要持续形成判断的对象是什么？”

推荐直接给 Agent 这一问法：

> **What persistent economic subject makes this fact, tension, or forward-looking conclusion worth caring about?**

例如：

```text
Source conclusion:
客户验证已经增加，但是否会形成订单尚不确定。

不要直接变成：
“客户验证能否转成订单？”

继续追问：
我们真正长期维护的对象是什么？

可能得到：
“AI基础设施客户需求与采用”
或更合适的业务对象。
```

这里不是要求一律扩大。

而是要求 Agent 区分：

> 当前研究问题

和

> 这个问题所服务的长期 expectation object。

---

## Step 3：剥离暂时性的方向、解释和证明条件

这是 Candidate Discovery 新版最值得明确教授的分析技能。

推荐参考表述：

> Before naming the Candidate, strip away details that describe only the current thesis:
>
> - today's positive or negative direction;
> - one specific customer, event, product launch, or quarter unless that object itself deserves long-term management;
> - the current suspected realization mechanism;
> - evidence thresholds for proving success or failure;
> - exclusions such as “rather than ASP”, “only if repeated”, or “must convert into orders”.
>
> Keep the economic subject these details are currently helping the research understand.

这里的目的不是机械删词，而是让 Agent 完成：

> **结论 → 对象**

的转换。

---

## Step 4：确认这个对象确实值得持续管理

再问：

> 如果未来一年没有今天这条具体事件，这个对象本身仍值得持续更新吗？

如果不是，通常说明：

- 只是单个事件；
- 单个指标；
- 某个 Factor；
- 一个非常临时的问题。

---

# 6. Candidate Discovery｜Judge granularity and independent updateability

这是防止矫枉过正的关键章节。

不能因为旧版太窄，就把新版全部变成：

> “公司增长”
> “盈利能力”
> “市场需求”

这种巨大对象。

建议明确两个方向的错误。

---

## 6.1 Too narrow

一个 Candidate 太窄的典型特征：

> 主要围绕一个事件、客户、阶段、指标或 realization mechanism。

可以用下面的判断：

> **Can this Candidate support several distinct future drivers, mechanisms, and revision paths while still referring to one coherent economic subject?**

如果答案是否，则可能只是：

- State；
- Factor；
- Future Node；
- Gap；
- 单一事件。

例如：

> “某OEM下一代机型增加Intel采购”

通常太窄。

但：

> “PC与边缘计算需求”

可以被 OEM采用、换机周期、渠道库存、竞争架构、宏观需求等不同信息持续更新。

---

## 6.2 Too broad

过宽的 Candidate 通常：

- 几乎所有公司新闻都能更新；
- 一个新事实只能改变其中很小一角；
- 后续 O1 无法形成一个聚焦的 State / Factor / Gap 模型。

推荐判断：

> **If a material new fact changes only a small corner of the Candidate while most of the subject remains unrelated, the Candidate may be too broad.**

例如：

> “Intel未来业务表现”

没有可维护的独立经济对象边界。

---

## 6.3 合适的中间粒度

不必继续使用旧版模糊的 “middle-level proposition”。

新版更适合直接说：

> A Candidate should usually be broad enough to contain multiple drivers and future revision paths, but narrow enough that a major new fact can meaningfully change the maintained view of the subject as a whole.

这句话比“middle-level”本身更可执行。

---

# 7. Candidate Discovery｜Use the assigned research

这一段保留旧版中有价值的 provenance 纪律，但重写得更简单。

核心原则：

> **Primary source defines the discovery perspective, not the architecture.**

C1、C3、C5、Narrative 各自从自己的研究视角发现 Candidate。

它们不负责跨来源去重，也不应该因为猜测别的 Branch 会覆盖，就少报候选。

推荐表述：

> Work from the assigned primary source as the main research basis. Use Future Nodes, entity relations, horizontal indicators, and available Event Library material only to clarify or support what the source is revealing.
>
> Do not suppress a valid Candidate because another research branch is likely to discover a similar object. Recall is preferred at this stage.

保留旧版的：

> optional input 缺失不是 Candidate failure。

旧版这部分思路是合理的。

---

## 7.1 C5 的特殊说明仍应保留

这是旧版中很有价值、上一轮没有重点提到的内容。

C5 很容易产生：

> “市场是否已经 price in X？”

这种 measurement task。

所以需要保留：

> Use market, valuation, price, and implied-expectation evidence to identify the underlying economic object being priced.
>
> The act of measuring market expectations is not itself a Candidate Unit.

例如：

> “市场是否已经反映某个估值倍数”不是 Unit。

背后的：

> 商业 Foundry 前景 / 服务器需求 / 盈利路径

才可能是 Unit。

---

# 8. Candidate Discovery｜Candidate record

必须严格贴合 v2.1 Schema：

```text
candidates[]
  name
  scope
  why_material
  ref[]

warnings[]
```

当前实现已明确 Candidate Discovery 不再输出 ID、proposition 或 horizon。

## `name`

规则：

> **短、稳定、名词化、对象化。**

它应该告诉下游：

> “这个对象是什么？”

而不是：

> “我现在想证明这个对象什么？”

建议参考表述：

> Name the economic subject, not the current thesis about it.

---

## `scope`

回答：

> 持续对该对象的哪些业务方面形成判断？

要求比 `name` 更具体，但不能偷偷变成 proposition。

推荐 Skill 直接警告：

> Scope is not a hidden proposition. Do not use it to encode success criteria, a preferred causal path, or what must be excluded.

例如：

```text
name:
AI基础设施需求

scope:
AI基础设施需求及公司相关产品在客户、平台和工作负载中的需求与业务暴露。
```

而不是：

> AI需求如何通过客户资格转成重复出货，并排除 ASP 的影响。

---

## `why_material`

这是 Synthesis 非常重要的信息交接。

应该写：

> 为什么这个对象的未来变化值得公司层面持续维护判断；
> 为什么它不是另一个 Candidate 中的普通低层变量。

不能写成：

> “因为它影响收入、利润和估值。”

这种泛话。

推荐 reference wording：

> Explain the economic consequence that makes this object worth maintaining separately. Be concrete enough that Synthesis can understand why the object matters without reconstructing the entire source report.

---

## `ref`

只保留真正支持 Candidate 识别与 materiality 的主要来源。

不追求 exhaustive citation dump。

---

# 9. Candidate Discovery｜Completion

这一部分要继续坚持宽召回。

建议新版参考表述：

> Recall is the preferred error direction. Preserve plausible overlapping Candidates when each represents a defensible durable economic subject supported by the source.
>
> Discovery is complete when all materially distinct future-facing economic subjects revealed by the source have been considered, and additional Candidates would mainly:
>
> - duplicate an existing subject;
> - restate current facts;
> - isolate a lower-level variable or mechanism;
> - or expand into a company-wide theme too broad for independent maintenance.

这里比旧版的：

> “every distinct material future question”

更符合新 ontology。旧版 Completion 的召回原则可以保留，但对象必须从 question 改成 economic subject。

---

# 10. `shell-synthesis.md` 重写方案

## 10.1 整体章节结构

建议新版分为七个主体章节：

```text
# Shell Synthesis internal skill

## Purpose and mental model
## Use the available evidence
## Interpret Candidate objects
## Decide provisional Unit status
## Build shared research contexts
## Express provisional Shells
## Output discipline and completion
```

新版架构应明显区别于旧版。

不再以：

> Same question / future validation event / core_question

作为主要组织方式。

---

# 11. Shell Synthesis｜Purpose and mental model

推荐开头：

> **Shell Synthesis turns independently discovered economic subjects into a provisional research topology.**
>
> It makes two different decisions:
>
> 1. which Candidate subjects are strong enough to deserve independent Unit-level maintenance;
> 2. which retained subjects should share one bounded O1 research context.
>
> Do not turn Candidate subjects into more complete investment propositions. O1 later determines their current State, Expectation Baseline, realization mechanisms, materiality, and revision space.

这里保留旧版最重要的正确认识：

> Unit decision ≠ Shell decision。

旧版这一点本来就是正确的。

但把：

> “material middle-level proposition”

换成：

> “durable economic subject”。

---

# 12. Shell Synthesis｜Use the available evidence

这是本轮根据用户最新编排调整必须新增、并写清楚的一节。

## 12.1 默认工作材料

默认优先使用：

- `candidate_sets`
- 公共 context：
  - Future Nodes
  - entity relations
  - horizontal indicators
  - Event Library
  - `as_of`

Candidate Sets 应当是结构判断的**第一入口**。

因为它们已经把来源研究压缩成：

> name / scope / why_material / ref。

---

## 12.2 C1/C3/C5 原报告是可选深化材料

新版 Synthesis 可以读取原报告，但不应该默认完整重读全部报告。

应让 Agent 自行判断是否需要。

建议直接加入参考表述：

> Candidate Sets are the default structural input. Consult the original C1, C3, or C5 reports selectively when the Candidate summaries are not sufficient to resolve an important structural question.
>
> Typical reasons to open the original report include:
>
> - two Candidates appear similar but their underlying economic objects may differ;
> - a Candidate's `scope` or `why_material` is too compressed to judge its proper granularity;
> - Shell placement depends on a broader business system or actor network not captured in the Candidate summary;
> - a Candidate appears to be an over-abstraction or under-abstraction of what the source actually researched;
> - conflicting Candidate framings from different sources need to be reconciled against the underlying evidence.

然后必须加一道边界：

> Do not re-run broad Candidate Discovery from the full reports during Synthesis. Use them to resolve topology questions, not to silently replace the Candidate stage.

否则 Synthesis 会变成第二轮 Candidate Discovery。

如果原报告明显暴露重要漏项，而 Schema 又没有正常新增 Candidate 的接口，则：

> 记录 warning / 留给 Domain Review 或 Finalization 处理。

不能偷偷制造没有 `candidate_ref` 的 Candidate。

---

# 13. Shell Synthesis｜Interpret Candidate objects

这一节是替代旧版 “Analyze candidate relationships” 的核心。

不要再用：

> Same question = core judgment + future validation event 相同。

而改为：

> **这些 Candidate 到底是不是同一个持续经济对象？**

建议参考表述：

> Compare Candidates by the economic subject they ask the system to maintain, not by wording, current direction, or today's preferred validation path.
>
> Two Candidates may describe the same durable object even when one source frames it through demand, another through profitability, and another through market pricing.
>
> Conversely, two Candidates may share a causal chain or evidence but still represent distinct economic subjects whose outlooks can change independently.

这句话对跨 C1/C3/C5 去重非常关键。

C1/C3 本来就是从不同研究角度看同一公司：C1 更强调公司经营状态和财务驱动，C3 更强调外部系统、参与者和行业传导。 

所以：

> source difference ≠ Unit difference。

---

# 14. Shell Synthesis｜Decide provisional Unit status

这里要定义何时保留 Candidate 为 provisional Unit。

建议用四个判断。

## 14.1 Durability

> 这个名字是否代表一个持续经济对象，而不是某个当期问题或事件？

---

## 14.2 Materiality

> 未来重要变化是否足以改变公司相关预期？

---

## 14.3 Independent maintainability

> 是否值得独立维护判断，而不是邻近 Unit 内的一项 State / Factor / Gap？

这里不要用：

> 是否有完全独立的 future event path

作为必要条件。

---

## 14.4 Granularity

> 是否既不是一个过细机制，也不是一个涵盖大部分公司 thesis 的超大对象？

推荐 reference wording：

> Retain a Candidate provisionally when it represents one coherent economic subject whose future matters materially and whose outlook can be maintained separately from adjacent subjects.
>
> Demote it when it is better understood as:
>
> - a reported event;
> - a State-like variable;
> - a realization mechanism;
> - a narrow customer/product occurrence;
> - a measurement task;
> - or an umbrella theme too broad for one maintained expectation object.

这里“demote”只是语义判断，具体 Schema 中进入 `unassigned_candidates`。

---

# 15. Synthesis 阶段如何处理重复 Candidate

当前程序要求：

> 每个 `candidate_ref` 恰好出现一次。

因此新版 Skill 必须明确区分：

> **语义判断可以认为两个 Candidate 最终应该合并；过程记录仍必须完整保留。**

建议 reference wording：

> You may conclude that several Candidate records describe the same eventual Unit, but do not erase their provenance or fabricate a merged `candidate_ref`.
>
> Preserve every input Candidate exactly once. Retain the strongest representative Candidate in the provisional topology and place overlapping records in `unassigned_candidates` with a concrete reason that identifies the corresponding retained object.

如果当前实现更希望多个同义 Candidate 暂时放同一 Shell 也可以，但最重要的是：

> 不允许在 Synthesis 输出中创造一个没有输入 ref 的“合并 Candidate”。

真正的最终 Unit 名称和合并由 Finalization 决定。

---

# 16. Shell Synthesis｜Build shared research contexts

这是整个 Synthesis 最重要的第二层判断。

Shell 的定义：

> **一组 Unit 为什么值得由同一个 O1 长期共同研究？**

这里要保留旧版“共享上下文不是整条价值链”的好思路。

但要把“future-event space / realization logic”等旧式概念弱化，防止 Agent 在 O0 阶段提前想 Factor / Gap。

建议把共享上下文定义得更业务化：

> Consider whether the Units repeatedly benefit from the same body of persistent research:
>
> - the same business or market domain;
> - overlapping core actors and counterparties;
> - common operating or industry background;
> - recurring datasets and evidence sources;
> - related competitive, regulatory, or supply environments;
> - business relationships that must be understood together for either Unit to be interpreted correctly.

重点是：

> **research reuse**。

不是：

> 共同因果终点。

---

# 17. Shell Grouping 的核心判断法

推荐给 Agent 一个很实用的 mental simulation：

> **Imagine O1 researching these Units over several months.**
>
> If they repeatedly need the same background, actors, data, and business context, keeping them together should improve depth and reduce duplicated reconstruction.
>
> If one group mostly introduces a new business system, evidence base, and actor network, while its link to the others is mainly upstream/downstream value transmission, it likely belongs in another Shell.

这其实继承了旧版的 Research Owner Test，但表达更简单、更贴近运行。

旧版这部分对 O1 persistent context 的想象是值得保留的。

---

# 18. 三个 Shell Boundary 原则

## 18.1 Shared context is not causal closure

> 因果链相连，不代表必须同 Shell。

例如：

> 产品需求 → 盈利 → 现金流

经济上连接，但可以属于不同 Research Context。

---

## 18.2 Context overlap is not transitive

保留旧版优秀原则：

> A 与 B 高度共享，B 与 C 高度共享，不代表 A/B/C 必须一个 Shell。



---

## 18.3 Avoid artificial fragmentation

也不能因为 Unit 能独立更新，就每个 Unit 一个 Shell。

建议：

> A single-Unit Shell is valid when the Unit genuinely requires a distinct persistent research context. It should not be created merely because the Unit is independently updateable or because the taxonomy looks cleaner.

不必像旧版那样强调 “exceptional”，避免模型为了躲避 single-unit shell 而强行合并。

---

# 19. Shell Synthesis｜Express provisional Shells

这是旧 `core_question` 被彻底替换的地方。

每个 provisional Shell 输出：

```text
shell_temp_id
name
scope
boundary
ref[]
candidate_units[]
```

## `name`

稳定、名词化的业务研究域。

不要试图总结 Unit 的共同 outcome。

参考：

> `计算产品市场`

而不是：

> `计算产品需求与供给转化`

---

## `scope`

回答：

> 这个长期研究上下文覆盖哪些经济对象、业务活动和市场环境？

建议 reference wording：

> Scope describes what this research domain covers. It should identify the common business or market system, not formulate a question the included Units must jointly answer.

这是对旧 `core_question` 最大的修正。

旧版曾明确要求 `core_question` 通常必须由多个 Units 联合回答，这是产生冗长兑现链的重要结构性诱因。

新版应彻底取消这种要求。

---

## `boundary`

只回答：

> 与相邻 Research Domain 的主责边界在哪里？

例如：

> 本 Shell 深入维护产品和终端市场；外部 Foundry 制造和公司融资由相邻 Shell 深入维护。

不要写：

- 每个 Unit 不得用什么证据；
- 哪些 Factor 属于谁；
- 哪种 Gap 应被排除；
- 谁必须验证什么。

建议直接规定：

> Boundary is a division of research ownership, not a prohibition on cross-Shell economic influence.

这句话非常重要。

---

# 20. Shell Synthesis｜Candidate wording preservation

当前 v2.1 Schema 要求 Synthesis 中 Candidate record 保持：

```text
candidate_ref
name
scope
why_material
ref
```

Skill 应明确：

> **不要为了 provisional Shell 更整齐而重写 Candidate。**

因为 Domain Review 需要看到来源 Branch 实际产出了什么。

允许结构判断：

> “C1:X 和 C3:Y 最终可能是同一个 Unit”。

但不要把 C3:Y 改写成 C1:X 的文本。

程序本身目前只校验 `candidate_ref` 覆盖，不校验逐字一致，所以这条必须由 prompt 纪律保证。

---

# 21. Synthesis 中原始 C1/C3/C5 的使用边界

考虑到当前编排已允许 Synthesis 按业务需要查看完整报告，Skill 应明确三个层次：

### 第一层：不需要查看

如果 Candidate Sets 已经能够清楚判断：

- 对象 identity；
- materiality；
- granularity；
- Shell context；

直接工作。

### 第二层：选择性查看

当结构判断本身存在重要歧义时，再读取相关 Source Report 的对应部分。

### 第三层：不要做

不得为了“确保没漏”而完整重新执行 Candidate Discovery。

Synthesis 的任务是：

> **converge topology**

而不是：

> **重新扫描所有 D1 研究。**

这一规则既利用了新增原文访问能力，也不会把 Synthesis 变成第二套 Discovery。

---

# 22. Shell Synthesis｜Warnings

`warnings` 只记录真正影响结构可靠性的材料限制，例如：

- 某 Candidate 缺少足够上下文，且原报告也无法解决；
- 某来源 Candidate Set 明显缺失或运行失败；
- 原报告揭示一个可能的重要对象，但当前 Candidate contract 无法正常容纳，需 Domain Review / Finalization 关注。

不要记录普通：

- 可选 Event Library 缺失；
- 某个引用未解析；
- “C3与C1有重叠”；
- Agent 自己的分析过程。

---

# 23. Shell Synthesis｜Completion

建议新版 Completion 只做四项最终检查：

### Candidate accounting

每个输入 `candidate_ref` 恰好出现一次。

### Unit quality

保留 Candidate 是一个 durable、material、independently maintainable economic subject，而不是 State / Factor / event / mega-theme。

### Shell coherence

同一 Shell 的 Units 共享足够重要的长期研究上下文。

### Boundary quality

Shell 之间可以经济相关，但研究责任清楚；既没有因价值链关系过度合并，也没有因为可独立更新而过度碎片化。

参考结束语：

> The provisional topology is complete when every Candidate is accounted for, retained Candidate subjects are structurally defensible as potential Units, and every Shell represents a coherent body of persistent research that one O1 can maintain deeply without absorbing unrelated business systems.
>
> The draft should remain open enough for Domain Review to challenge and should not pre-empt O1's later expectation research.

---

# 24. 两份 Skill 之间必须保持一致的核心术语

Work 重写时，两份文件不要各自发明不同定义。

建议固定使用：

### Candidate Unit / Unit

> **durable economic subject that remains worth forming and revising expectations about**

### independently updateable / independently maintainable

> **future evidence can materially change the maintained view of this object without mechanically requiring all adjacent objects to change together**

### Shell

> **bounded shared research context**

### shared context

> **persistent business, market, actor, data and evidence background that multiple Units repeatedly reuse**

### Candidate Discovery

> **discover what is worth maintaining expectations about**

### Shell Synthesis

> **decide which subjects deserve provisional Unit status and which should share persistent research context**

不要重新使用：

> middle-level proposition

作为核心 ontology。

可以偶尔出现 “judgment” 或 “expectation”，但不能再把 Unit 定义成完整可证伪 proposition。

---

# 25. Part 1 重写验收标准

完成两份 Skill 后，应该至少通过以下人工 sanity check。

拿同一段材料：

> “AI 数据中心需求很强，但公司是否能把客户验证转化成重复订单仍不确定。”

新版 Candidate Discovery 应更倾向于抽出：

> **AI基础设施需求 / 公司相关业务暴露**

这类长期经济对象。

而不是自动写成：

> **客户验证能否转成重复订单并形成可持续收入**

后者应由后续 O1 的 Factor / Gap / Baseline 去研究。

到了 Synthesis：

如果 C1 写：

> AI基础设施需求

C3 写：

> 数据中心现场电力市场

C5 写：

> AI数据中心增长持续期

Synthesis 应先判断：

> 这三者到底是不是同一个持续经济对象，还是存在值得分别维护的经济边界。

而不是：

> 看三个 Candidate 的 future validation events 是否一致。

同时，Shell 应写成：

> **数据中心电力市场**

这样的研究域，

而不是：

> **AI需求、客户资格、融资与交付能否转化为可持续验收MW**

这样的总命题。

只要这两个行为真的发生变化，Part 1 的重写就算抓住了 O0 v2.1 最核心的业务目标。