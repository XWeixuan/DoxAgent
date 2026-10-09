# Document2 v2.1｜O0 Prompt/Skill 重写方案 Part 2

## 1. 本 Part 的范围与共同原则

本 Part 只覆盖：

- `skills/domain-review.md`
- `skills/shell-finalization.md`

延续 Part 1 已确定的核心 ontology：

> **Unit = 值得持续形成和修订预期的经济对象。**  
> **Shell = 一组 Unit 共享的、边界清楚的长期研究上下文。**  
> **O0 = Research Topology Construction，而不是 Expectation Thesis Construction。**

Domain Review 和 Finalization 都不能重新把 Unit 变回“middle-level proposition”，也不能因为进入了“review / finalization”阶段，就自然采用更窄、更保守、更强调证据完备性的标准。

当前 v2.1 的实际链路是 Synthesis 后由 C1/C3/C5 分别审阅，再由 O0 Finalization 统一决策；每次 Worker 都是新会话，因此两份 Skill 都必须只依赖本轮显式注入材料。

两份 Skill 的角色应有明确区别：

```text
Domain Review
= Is this topology a good research architecture
  when viewed through this domain's real business knowledge?

Finalization
= Given all provisional objects and domain perspectives,
  what is the best final research topology for O1?
```

旧版中值得保留的逻辑包括：

- Reviewer 不拥有架构，只提供一个领域视角；
- Reviewer 的意见不是投票；
- 一个有充分业务依据的单一意见也可能很重要；
- 当前不确定性不是 Unit 无效的理由；
- 一个局部 split / merge / move 可能要求重新检查相邻结构；
- 因果联系可以跨 Shell；
- Shell 应能被一个长期 Research Owner 深度维护；
- Finalization 不是重新做一遍广泛 Candidate Discovery。

旧 Finalization 对“reviews neither vote”及“局部修改应作为一个整体结构重新处理”的认识是正确的，应保留。

但需要彻底移除的是旧版围绕：

> proposition integrity / validation path / complete shared event space / final declarative proposition

构造出来的分析框架。

---

# 2. `domain-review.md` 重写方案

## 2.1 推荐整体章节结构

新版建议使用七个主体章节：

```text
# Unified Domain Review internal skill

## Purpose and review stance
## Reconstruct the domain view
## Judge the provisional Unit structure
## Judge the Shell research topology
## Project the structure into downstream research
## Form domain feedback
## Output discipline and completion
```

它应当明显区别于旧版：

> “逐 Candidate 找结构错误 → 判断下游 consequence → 提 correction”

的 QA 风格。

新版真正的主线应是：

```text
先理解这个领域认为公司真正需要持续研究什么
↓
再判断当前 topology 是否合理表达这些研究对象
↓
再考虑这样的结构交给 O1 后会产生什么研究后果
↓
仅在有实质业务价值时给出 feedback
```

---

# 3. Domain Review｜Purpose and review stance

这是这份 Skill 最关键的开头。

必须主动抵消 “review = 找问题 = 更保守” 的模型倾向。

建议给出接近下面的参考表述：

> **Domain Review is an independent business-structure judgment, not a defect hunt.**
>
> Your job is to ask whether the provisional Shell/Unit topology is a good way to organize continuing expectation research when viewed through the business reality captured by your domain.
>
> Do not assume the draft is wrong, and do not assume a narrower, safer, or more evidence-complete structure is better. A good review may confirm the draft, broaden an object, separate two objects, merge artificial distinctions, recover a missing subject, or make no recommendation at all.

然后明确 Review 的目标函数：

> **Prefer the topology that best preserves material economic coverage, meaningful independent expectation objects, and useful shared research context.**
>
> Review quality is not measured by the number of issues found.

这一句非常重要。

旧版虽然允许 empty feedback，但整篇仍是以“检查错误”为中心。

新版应从一开始把 Agent 放到：

> **business architecture evaluator**

而不是：

> **risk-control reviewer**

的位置。

可以借鉴新版 Gap Skill 的写法风格：它会直接声明保守偏好会破坏该阶段的目标。

但 Domain Review 不需要复制其“召回优先”业务语义。这里更适合表述为：

> There is no general preference for narrower scope, fewer Units, more proof, or less ambiguity. Structural uncertainty should be resolved by business reasoning, not by conservative contraction.

---

# 4. Domain Review｜简短重新定义 Shell 与 Unit

Reviewer 不运行 `o0.md`，因此 Domain Skill 必须自包含最基本定义。

但不要重写一遍完整 O0 ontology。

建议只用三段：

> **An Expectation Unit is a durable economic subject that remains worth forming and revising expectations about as future evidence arrives.** It is not today's conclusion, one realization mechanism, or one future event.
>
> **A Shell is a bounded shared research context.** Its Units remain independently maintainable, but benefit materially from repeatedly reusing the same business, market, actor, and evidence background.
>
> The topology determines what O1 will treat as a maintained expectation object and which research it will carry together. It should therefore reflect the economic structure of the business without freezing today's thesis into permanent boundaries.

这三段足够。

不要再使用旧定义：

> “material middle-level proposition that the market can discuss, revise and trade”

旧 Review 正是以这一 ontology 为基础。

---

# 5. Domain Review｜Reconstruct the domain view

这是新版最核心的分析技能之一。

Reviewer 不应该从 provisional draft 开始逐项挑错。

应先回到：

```text
original_domain_report
+
本领域 reviewer role
+
必要公共上下文
```

独立回答：

> **从本领域已经建立的业务理解来看，哪些经济对象真正值得系统长期保持判断？**

建议参考表述：

> Before evaluating the draft, briefly reconstruct the domain's own research map.
>
> Ask:
>
> - What parts of the company's future does this domain show to be economically material?
> - Which of those are durable expectation subjects rather than current facts, mechanisms, or individual future events?
> - Which subjects need meaningfully separate maintained views?
> - Which relationships are important background connections but do not require separate Unit identities?
>
> Build this view from the economic substance of the report, not from its section headings or internal taxonomy.

这里必须强调：

> **Domain Report 是业务知识，不是 topology template。**

C1/C3/C5 的报告结构不应该直接变成 Unit 结构。

例如：

- C1 的“核心驱动因子”不自动等于 Units；
- C3 的一个产业链主题不自动等于 Unit；
- C5 的一个 Market Pricing Theme 也不自动等于 Unit。

Reviewer 要抽象到：

> 它们共同告诉我们“什么对象值得持续形成判断”。

---

# 6. Domain Review｜如何使用原领域报告

当前 Review 实际直接拿到 `original_domain_report`，因此它应当是 Reviewer 的主要业务依据。

新版 Skill 应明确：

> Use the original domain report to recover business meaning, materiality, and distinctions that may have been compressed during Candidate Discovery and Synthesis.
>
> The report is evidence for judging the topology; it does not have to be mirrored in the topology.

可以继续允许 targeted external research，但不要鼓励为每个反馈再调查一遍。

推荐：

> Use additional research only when a specific structural judgment depends on a factual ambiguity that the supplied domain report cannot resolve. Stop when the business-structure decision is adequately grounded.

这是旧版中值得保留的好约束，只需要去掉“restored thread”之类与实际执行不符的表述。旧 Review 本身也已经要求研究 effort 在结构判断清楚后停止。

---

# 7. Domain Review｜Judge the provisional Unit structure

这一节不要命名为 `Unit integrity audit`。

更适合：

> **Does the Unit map make business sense from this domain?**

建议教 Agent 从四个角度理解，而不是机械逐项打分。

## 7.1 Coverage

先问：

> **本领域认为重要的持续经济对象，在 topology 中有没有合理的研究位置？**

这里既包括：

- 已保留 Candidate；
- `unassigned_candidates`；
- 完全遗漏的对象。

但“遗漏”必须是：

> 真正值得 Unit-level 持续研究的经济对象，

不能因为 Domain Report 有一个重要变量，就自动要求新增 Unit。

---

## 7.2 Object identity

问：

> 当前 Unit 的 name / scope 到底是在表达一个经济对象，还是偷偷表达了一个当前结论、实现路径或证明标准？

例如：

```text
合理：
AI基础设施需求

有问题：
AI客户采用能否形成可重复订单
```

但 Review 不能只做文字检查。

真正的问题是：

> 后一种定义会让 O1 把“资格→订单”当成这个 Unit 的永久身份，因此对其他可能改变 AI 需求的因素缺少自然入口。

推荐参考表述：

> Judge the economic object behind the wording. A wording problem matters only when it changes what future research would naturally belong inside the Unit.

---

## 7.3 Granularity

Reviewer 应结合本领域理解问：

> **这个对象是不是被切得比实际经济问题更细，或者把本应分别维护的不同对象包得过大？**

需要明确：

> 不因为信息可以分别披露，就拆 Unit。

也不因为存在共同驱动，就强行合并。

参考表述：

> Granularity should follow independent research value, not the number of observable milestones or the report's internal subheadings.

---

## 7.4 Independent maintainability

从本领域判断：

> 两个对象的预期是否值得分别维护？

不是：

> 它们有没有独立新闻源。

一个新事实可以同时影响多个 Unit。

真正需要避免的是：

> 为了追求“independent updateability”，把同一个经济对象的不同步骤拆成多个 Unit。

---

# 8. Domain Review｜Judge the Shell research topology

Reviewer 随后再判断这些 Unit 的 Shell placement。

这里要保留旧版大量正确逻辑，但换一个更简单的中心问题：

> **如果这些对象由同一个 O1 长期研究，它们是否真的会持续复用一套重要的研究背景？**

建议参考表述：

> Evaluate Shells as research environments, not as causal containers.
>
> Units belong together when maintaining them jointly gives O1 durable research leverage: repeated use of the same business system, major actors, industry context, datasets, competitive environment, or evidence base.
>
> Economic transmission can cross Shell boundaries. A Shell does not need to absorb every upstream cause or downstream consequence of its Units.

这保留了旧版：

> causal link can cross Shell  
> context overlap non-transitive

的有价值部分。旧 Finalization 对这些边界其实已有较成熟表达。

但要去掉：

> 必须共享 realization mechanisms / future-event spaces / full research context

这种容易过度收缩的要求。

---

# 9. Domain Review｜不要把 Shell boundary 理解成“禁止影响”

建议明确加入：

> **Boundary is a depth-of-ownership boundary, not an economic firewall.**

例如：

> “商业 Foundry”由另一个 Shell 深度维护，

不代表：

> 计算产品 Shell 不能研究 Foundry 良率变化对产品供给的影响。

Reviewer 要判断的是：

> 深度研究主体放在哪里最合理，

而不是：

> 一个变量只能出现在哪一个 Shell。

这一点对于防止 Shell 变成孤岛很重要。

---

# 10. Domain Review｜Project the structure into downstream research

这是用户特别强调的核心板块之一，但必须建立在前面的业务判断上。

不能只检查历史 bug：

- 会不会排斥外部 catalyst；
- 会不会泄漏 thesis；
- 会不会丢上下文。

而应该完整地想象：

> **如果按这个 topology 启动 O1，它将怎样理解和研究这个业务？**

推荐核心 reference wording：

> After judging the business structure itself, project the draft forward into O1.
>
> Imagine that this topology becomes the durable frame for State, Expectation Baseline, Realization Factors, Materiality Context, and open Revision Space.
>
> Ask whether that frame would help O1 build the right model of the business, or whether the topology would systematically distort what O1 sees as the object, what it treats as a mechanism, and what kinds of future change it can naturally absorb.

然后从几个真正有业务价值的角度思考。

### 10.1 研究对象是否稳定

未来当前 thesis 反转后：

> Unit 是否仍然是正确的研究对象？

---

### 10.2 是否能承载多种机制

O1 是否可以在 Unit 内研究：

> 多个不同 Factor，而不是被 seed 暗示只能研究一条兑现链？

---

### 10.3 是否能接住意外的重要变化

出现当前研究没预见的新外部变化时：

> 它是否有自然的 Unit home？

这里不是要求 Reviewer 枚举未来 catalyst。

而是测试 topology 的开放性。

---

### 10.4 是否会复制或割裂研究

两个 Unit 是否会让 O1：

> 重复维护同一套 State / mechanism；

或者相反：

> 在一个过大的 Unit 内混合几套本质不同的模型？

---

### 10.5 Shell context 是否真正提高后续研究质量

放在一起是否会：

> 减少重复背景重建、提高跨 Unit 解释一致性，

还是会：

> 让 O1 在一个 Shell 中维护几套几乎不相干的研究系统？

这才是 downstream consequence 的完整含义。

---

# 11. Domain Review｜Review stance：不要为发现问题而发现问题

建议单独放一个短板块，直接校正 GPT-6 的审查倾向。

参考版本：

> **Do not manufacture feedback to demonstrate diligence.**
>
> A review is successful when it produces the best domain judgment, not when it finds the most defects.
>
> Do not:
>
> - narrow a Unit because its future is uncertain;
> - remove a Candidate because evidence is incomplete;
> - prefer a smaller topology merely because it appears cleaner;
> - demand stronger proof for a Unit than was needed to establish its economic materiality;
> - split objects merely to make update paths look cleaner;
> - merge objects merely to reduce overlap.
>
> If the provisional topology represents this domain well, an empty `targeted_feedback` list is the correct result.

旧版最后已经允许 empty feedback，但新版必须把这件事提升成 Reviewer 心智，而不是尾部一句免责。

---

# 12. Domain Review｜Form domain feedback

只有在 Reviewer 形成完整业务判断后，才进入 feedback。

建议告诉 Agent：

> Feedback should identify a material structural opportunity or problem, not every imperfection in wording.

一个 feedback 应形成：

```text
what the topology currently does
→ why this is or is not a good representation of the business
→ what downstream research consequence follows
→ what structural change would improve it
```

`recommendation` 可以：

- broaden；
- narrow；
- merge；
- separate；
- move；
- rename/re-scope；
- restore an unassigned Candidate；
- propose a missing economic object。

但不要要求 Reviewer 输出完整修订 Shell。

---

# 13. Domain Review｜Output contract

严格贴合当前 Schema：

```text
reviewer_role
overall_assessment
targeted_feedback[]
  feedback_id
  target
  issue
  reasoning
  recommendation
  ref[]
warnings[]
```

当前实现就是这一结构。

## `overall_assessment`

不应该变成：

> “发现了 3 个问题。”

更适合回答：

> **从本领域角度，这个 topology 是否是一个合理的长期研究架构？最重要的结构判断是什么？**

---

## `target`

明确：

- Shell：`S#`
- Candidate：完整 `candidate_ref`
- 缺失对象：明确描述 Missing Unit / Missing Subject

不依赖旧 branch-local handle。

---

## `issue`

描述结构本身。

不要写：

> “证据不足”

除非证据不足导致：

> 根本无法支持这个对象作为独立研究结构。

---

## `reasoning`

这是最重要字段。

应该同时包含：

> 本领域业务依据 + 为什么改变 topology 判断 + 交给 O1 后的实际影响。

---

## `recommendation`

给具体结构方向。

不要只说：

> “建议进一步澄清。”

应说清：

> 扩大到什么对象；  
> 哪两个对象其实应合并；  
> 哪个 Candidate 更适合作为 Factor 而不是 Unit；  
> 某 Unit 应移动到哪类 shared context。

---

## `ref`

仅保留支撑该结构判断的主要领域证据。

---

# 14. Domain Review｜Completion

建议最后用一段原则性 completion，而不是十项审计 checklist：

> Domain Review is complete when you have formed an independent view of what this domain needs the expectation system to maintain, compared that view with the full provisional topology, and raised every material structural recommendation that would meaningfully improve future research.
>
> Do not continue searching for smaller objections once the topology is well understood. Do not withhold a justified recommendation merely because the future outcome remains uncertain.

---

# 15. `shell-finalization.md` 重写方案

## 15.1 推荐整体章节结构

建议七个主体章节：

```text
# O0 Shell Finalization internal skill

## Purpose and finalization stance
## Reconstruct the structural decision space
## Integrate domain perspectives
## Resolve Units and Shells globally
## Write stable final seeds
## Run the final topology pass
## Output discipline and completion
```

Finalization 应比旧版更加明确：

> **它拥有最终架构判断权。**

不是：

> 在 provisional draft 上应用 reviewer patches。

---

# 16. Shell Finalization｜Purpose and finalization stance

建议参考表述：

> **Finalization chooses the best final research topology from the provisional structure, unassigned Candidates, and the domain perspectives actually available in this run.**
>
> The provisional draft is evidence of earlier structural reasoning, not a default that should be preserved. Domain Reviews are informed perspectives, not instructions and not votes.
>
> Your task is to produce the topology that will give O1 the most useful durable expectation objects and shared research contexts.

这一部分保留旧版非常好的两点：

1. provisional 不是正确性的 presumption；
2. review 不投票。

旧版已有这两个思路。

但新版要进一步说明：

> Finalization 不具有“越少改越安全”的默认偏好。

推荐：

> There is no preference to preserve, split, merge, broaden, or narrow the provisional structure. Choose among these actions according to research usefulness and business meaning.

---

# 17. Finalization｜输入边界必须符合实际运行

当前 Finalization 实际得到：

```text
provisional_shells
domain_reviews
公共 context
```

但不直接得到完整 C1/C3/C5 原报告。

因此新版严禁：

> “重新读取全部 D1 报告再做最终判断。”

也不能再写：

> Resume the O0 Synthesis thread。

当前每次 Worker 是新会话。

建议表述：

> Work from the complete provisional result, including `unassigned_candidates`, the Domain Reviews actually present, and the common context supplied in this run.
>
> Do not assume a missing review means the domain found no problem. Do not claim to have incorporated a review that is absent.

---

# 18. Finalization｜Reconstruct the structural decision space

不要一上来按反馈逐条修改。

第一步应该完整理解：

> Synthesis 到底构造了怎样的 topology，各 review 对它提出了哪些不同视角。

建议 reference wording：

> Before applying any recommendation, reconstruct the structural decision space:
>
> - what economic subjects the provisional draft is trying to maintain;
> - which Candidate records appear to refer to the same eventual object;
> - which important subjects were left unassigned;
> - what research context each provisional Shell is trying to create;
> - where Domain Reviews agree, disagree, or reveal different business interpretations.

这样 Finalization 会先形成自己的全局模型。

---

# 19. Finalization｜Domain Reviews 应怎样使用

旧版“organize feedback by structural issue rather than reviewer order”应保留。

新版可以进一步明确：

> **A Domain Review is evidence about the architecture, not an edit command.**

推荐表述：

> Group related feedback by the underlying economic object or research-context question.
>
> Evaluate the reasoning and evidence behind each recommendation. Agreement across reviewers increases confidence but does not create a vote. A single reviewer may identify a decisive domain distinction that the others were not positioned to see.
>
> When reviews conflict, resolve the conflict by asking which topology better represents durable economic objects and produces a better research environment for O1.

这样既保留 reviewer 专业性，也不给它“各管一摊”的 ownership。

---

# 20. Finalization｜不要因为 Review 天然保守而让最终结构单向收缩

这一段应明确写。

> Reviews may identify over-promotion, but they may equally identify missing subjects, artificial fragmentation, overly narrow scope, or Shell boundaries that prevent useful shared research.
>
> Finalization must consider expansion and contraction symmetrically.

不要默认：

```text
review
→ remove
→ narrow
→ merge
```

同样可能是：

```text
review
→ restore
→ broaden
→ split
→ move
```

真正标准仍是：

> 哪种结构更有研究价值。

---

# 21. Finalization｜Resolve final Units

建议把 Unit 决策教成三个连续问题。

## 21.1 What is the durable subject?

综合：

- Candidate `name/scope/why_material`
- duplicate / unassigned 情况
- reviewer reasoning

找出最终真正要长期维护的经济对象。

不要把当前结论、Factor、Gap 搬进最终 Unit。

---

## 21.2 Does it deserve independent maintenance?

问：

> 把这个对象并入另一个 Unit，会不会丢掉一个值得单独形成和修订的经济判断？

反向：

> 单独保留它，是否只是因为客户、产品、阶段或证据接口不同？

这与 Part 1 的原则完全一致。

---

## 21.3 What should its final scope be?

Finalization 可以：

- 合并多个 Candidate 的有效范围；
- 去掉 temporary thesis leakage；
- 修正过宽或过窄的 scope；
- 吸收 reviewer 发现的 missing dimension。

但不能因此写成“大而空”。

推荐 reference wording：

> The final Unit scope should be broad enough to support multiple future mechanisms and revision paths, but specific enough that O1 can build one coherent expectation model around it.

---

# 22. Finalization｜如何处理 Candidate 合并

Synthesis 为 provenance 原因保留每个 `candidate_ref`，Finalization 才真正形成最终 Unit。

因此需要明确：

> **Candidate records are evidence fragments for architecture; final Units are not required to map one-to-one to them.**

当前实现也明确允许 Finalization 改变最终 Unit 数量。

多个 Candidate 合并时：

- 不需要在输出中保存 candidate_ref；
- 应吸收各 Candidate 中不同但有价值的 scope；
- 最终 `ref` 保留支撑最终对象的重要来源；
- 不要因为某个 Candidate 是代表记录，就丢掉另一个来源揭示的重要维度。

这点非常重要。

---

# 23. Finalization｜Resolve final Shells

Unit 决策稳定后，再进行 Shell 组织。

核心问题仍然是：

> **哪些 Unit 长期放在同一个 O1 研究上下文里，能够明显提高研究深度和复用？**

建议参考表述：

> Build Shells around durable research reuse, not around end-to-end causal completeness.
>
> A Shell should have a recognizable center of gravity: a business or market domain that gives its Units substantial common background.
>
> Related Units may remain in different Shells when their detailed research systems differ; different Units may remain in the same Shell even when their immediate outcomes and update events differ.

---

# 24. Finalization｜保留旧版有价值的 Research Owner Test

旧版这一条很好：

> 想象这个 Shell 长期对应什么 Research Owner。

可以保留但简化。

推荐：

> For each Shell, imagine the O1 that will own it for months:
>
> **What business system will this O1 keep understanding, updating, and reusing across its Units?**
>
> If the answer is merely “the company's full investment thesis”, the Shell is too diffuse.
>
> If two adjacent Shells would repeatedly rebuild essentially the same business context, they may be artificially fragmented.

这个 test 比按 Unit 数量触发 6+ Boundary Challenge 更自然。

旧版 `6+ Units` 可以删除或弱化成：

> broad Shells deserve an extra coherence check,

不要使用固定数字成为认知 anchor。

---

# 25. Finalization｜Write stable final seeds

这一部分要非常具体，因为最终语言会直接进入 O1。

最终 schema：

```text
shells[]
  name
  scope
  boundary
  ref[]
  units[]
    name
    scope
    horizon
    ref[]

finalization_note[]
warnings[]
```

当前实现就是这套结构。

---

## 25.1 Shell `name`

> 稳定、自然、名词化的业务研究域。

例如：

> `计算产品市场`

而不是：

> `计算产品需求与供给能否转化为可持续出货`

名称应该能在：

- 好消息；
- 坏消息；
- 新机制；
- 新竞争者；
- 新商业模式

出现后继续成立。

---

## 25.2 Shell `scope`

回答：

> **这个长期研究上下文主要负责深入理解什么？**

不是：

> Shell 内所有 Unit 最终共同要证明什么。

推荐参考表述：

> Describe the business or market domain the Shell owns in depth. Do not connect its Units into one joint success condition.

---

## 25.3 Shell `boundary`

回答：

> **与相邻 Shell 的研究深度职责怎么分。**

推荐直接写进 Skill：

> Boundary allocates research ownership; it does not prohibit cross-Shell influences or shared evidence.

不要把 boundary 写成：

> “S1不得使用S2证据。”

---

## 25.4 Unit `name`

> 经济对象本身。

不要带当前方向、证明条件和 outcome quality。

---

## 25.5 Unit `scope`

回答：

> **持续对这个对象的哪些经济方面形成判断。**

不要偷偷恢复 proposition。

建议 Skill 明确：

> Scope is descriptive, not conditional. It should not contain an implicit “only if”, “rather than”, or complete realization chain unless those words are literally necessary to define the economic object.

不是机械禁词，而是 semantic test。

---

## 25.6 Unit `horizon`

> 自然业务观察周期。

可以是：

- 未来数季度；
- 当前产品周期；
- 未来多年项目周期；
- 下一财报窗口。

但不是：

> 等到订单→出货→收入之后。

推荐：

> Horizon describes when this economic object meaningfully evolves, not when its thesis will be proven.

---

## 25.7 `ref`

保留能够解释最终对象来源与 materiality 的主要依据。

Finalization 不需要 citation dump。

如果多个 Candidate 合并：

> 合并相关来源 ref，而不是只保留被选作代表 Candidate 的来源。

---

# 26. Finalization｜Run the final topology pass

最终检查不应写成大量 failure checklist。

建议只保留五个真正有业务意义的 mental tests。

## 26.1 Business Coverage

> 公司当前重要的持续经济预期对象，是否都有合理位置？

不是每个 D1 driver 都要 Unit。

---

## 26.2 Reversal / Openness

> 当前判断完全反转后，Unit identity 是否仍成立？

> 新的重要 catalyst 是否能自然进入，而不用重写 Unit identity？

---

## 26.3 Multi-path Researchability

> 每个 Unit 是否能够容纳多个 State、Factors 和开放 Revision paths，而不是只有一条预设兑现链？

---

## 26.4 Granularity Balance

> 有没有大量内部微观对象被单独维护，却把真正影响大的外部业务变化塞进一个过度宽泛 Unit？

> 或反过来，有没有把多个真正独立的商业对象压进一个 mega-Unit？

这一项可以帮助延续前面 D2 重构中非常重要的 internal/external 粒度对称目标。

---

## 26.5 Shell Research Coherence

> 每个 Shell 是否代表一个 O1 能长期深入维护的研究系统？

> 相邻 Shell 是否主责清楚，但允许经济传导自由跨越？

---

# 27. Finalization｜不需要逐条 Candidate disposition ledger

当前 schema 没有要求最终逐项记录 Candidate 的去向。

因此 Skill 应让 Agent：

> 在内部完整处理所有 provisional / unassigned objects，

但不要增加 schema 外的：

```text
candidate_resolution[]
```

`finalization_note` 只记录真正重要、对理解最终结构有帮助的结构决策。

例如：

> “将两条分别来自 C1/C3 的数据中心需求候选合并为同一 Unit，因为两者维护的是同一经济对象；外部供电约束保留为后续 O1 因素研究。”

而不是每条 Candidate 都写 disposal log。

---

# 28. Finalization｜Warnings

`warnings` 只记录真正削弱最终结构可靠性的执行或输入问题。

例如：

- 关键 Candidate branch 缺失；
- 某个 Domain Review 因执行失败未提供；
- 某个重要对象存在明显结构不确定性，但当前输入不足以裁决。

不要把：

- review 之间存在正常分歧；
- 某些 business evidence 尚未完成；
- Unit 未来本身不确定；

当作 warning。

---

# 29. Finalization｜Completion

推荐用一段整体性结束语：

> Finalization is complete when the resulting topology gives O1 a stable set of material economic subjects and coherent shared research contexts, reflects the strongest structural insight available from the provisional draft and the Domain Reviews actually present, and no longer depends on temporary thesis wording or process handles.
>
> Stop when further changes would mainly produce alternate taxonomies rather than materially improve future expectation research.

这比：

> “all three reviews have informed one decision”

更符合实际，因为 Review 可能缺席。当前运行说明明确指出缺席 review 不能被当作“无问题”，也不能假称已经综合。

---

# 30. 两份 Skill 之间的职责边界

Work 在实际重写时应保持这个分工。

## Domain Review

输出的是：

> **这个领域认为现有结构哪里合理、哪里值得改变，以及为什么。**

它不拥有最终架构。

它不输出修订版 Shell。

它不为了体现 Review 价值而制造问题。

---

## Finalization

负责：

> **把 provisional structure、unassigned Candidates 和实际收到的多个 Domain Perspectives 转化为一个统一、可执行的最终 Research Topology。**

它既不是：

> Synthesis 的保守确认，

也不是：

> Domain Review 建议的机械执行器。

---

# 31. Part 2 的核心参考心智

如果只保留两句话，我建议写进两份 Skill 的开头附近。

### Domain Review

> **Review the topology as a domain expert deciding whether it is the right way to organize continuing research—not as an auditor trying to make the draft safer or harder to falsify.**

### Shell Finalization

> **Choose the topology that best preserves durable economic objects and useful shared research contexts; treat the provisional draft and Domain Reviews as evidence for that decision, not as structures or instructions that must be preserved.**

这两句话能够最直接地改变 GPT-6 在两个节点上的默认行为。

---

# 32. Part 2 重写验收示例

假设 provisional Unit 是：

> `AI客户采用与重复订单转化`

C3 Reviewer 发现行业研究实际上还包含：

- 总 AI 基础设施需求；
- 新型客户和 workload；
- 替代架构；
- 项目融资与基础设施约束。

### 错误 Review

> 当前缺少重复订单证据，因此建议将 Unit 限定为已确认客户采购，避免范围过宽。

这是典型的保守收缩。

### 正确 Review

> 当前 Unit 把长期研究对象绑定在“客户采用→重复订单”这一当前 realization path 上。C3 的行业研究显示，AI 基础设施需求、工作负载和客户架构选择可以在尚未形成 Intel 订单时发生重要变化，并独立改变 Intel 的业务暴露。建议把 Unit 重新界定为更稳定的 AI 基础设施/DCAI 需求对象，把客户采用到订单的转化留给 O1 的 Factor 与 Revision Space。

然后 Finalization 不应机械接受。

它还要结合 C1/C5 和相邻 Units 判断：

> “AI基础设施需求”是否应该独立；  
> 是否和“企业服务器需求”过度重叠；  
> 两者是否共享足够上下文放在同一 Shell；  
> 最终 name/scope 应多宽。

如果最终形成：

```text
Shell:
计算产品市场

Unit:
AI基础设施与DCAI需求
```

那是全局架构判断的结果，

而不是：

> “C3 reviewer 要求改名，所以改名。”

---

# 33. Part 2 最终目标

完成这两份 Skill 重写后，四阶段 O0 应形成一个连贯的认知流程：

```text
Candidate Discovery
发现值得持续维护预期的经济对象

↓

Shell Synthesis
初步判断对象独立性与共享研究上下文

↓

Domain Review
从真实领域知识出发判断这套研究拓扑是否合理

↓

Shell Finalization
综合全部结构信息，决定最终 Research Topology
```

其中 Review 和 Finalization 都不能重新引入旧的：

```text
proposition
→ validation path
→ complete realization
→ safer / narrower structure
```

思路。

**Domain Review 的价值在于补充高质量领域判断；Finalization 的价值在于做全局结构决策。**

只要这两个角色真正建立起来，O0 的后半段就不会再把 Part 1 已经打开的经济对象重新压缩成旧式 Expectation Thesis。