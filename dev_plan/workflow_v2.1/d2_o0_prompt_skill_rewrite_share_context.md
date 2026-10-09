# Document2 O0 v2.1 重构背景

Document2 的定位是建立一个可持续更新的 **Expectation Model**：系统未来会不断接收新的公司、行业、市场和外部事件信息，并据此修订对某个 ticker 的研究判断。O0 位于 D2 最前端，它不负责完成具体预期研究，而是负责先决定：**哪些经济对象值得长期维护预期，以及哪些对象应该共享同一个研究上下文。**

v2.1 重构的主要原因，是旧版 O0 把这一步做得过于接近“预先构造投资命题”。Shell 常被写成“某些业务因素能否转化为某种最终结果”，Unit 又进一步写成带有客户采用、订单、出货、重复兑现、排除 ASP/mix 等条件的完整 proposition。这样虽然看起来精确，但实际上把当前研究中的一条因果解释固化成了长期研究对象。后续 O1 因而天然围绕这条既定路径研究 State、Factor 和 Gap，O3 也更容易继续把这些局部兑现步骤编译成狭窄 Policy，而对尚未进入原始因果链、但可能真正改变预期的重要外部变化缺乏自然入口。

v2.1 的核心变化因此不是“把名称写短”，而是重新定义 O0 的工作：

> **O0 构造 Research Topology，而不是 Expectation Thesis。**

最终的 **Unit** 是一个未来仍值得持续形成和修订判断的经济对象，例如某类市场需求、业务、商业化方向、盈利对象或资本结构问题。它不是当前对该对象的结论，也不是一条需要被完整证明的兑现链。Unit 应能被未来不同类型的信息独立更新，同时足够宽，可以容纳多个 State、Factor 和 Revision Path；但又不能宽到变成整个公司的笼统前景。

最终的 **Shell** 是研究容器：把需要长期复用相似业务背景、主要参与者、行业环境、数据与证据体系的 Units 放在一起研究。Shell 不是一个更大的总命题，也不要求其中所有 Units 共同回答同一个“能否成功兑现”的问题。经济传导可以跨 Shell；Shell boundary 只是研究深度和长期上下文的分工，不是经济影响的隔离墙。

这也是为什么 v2.1 的最终 seed 只保留：

```text
Shell:
name
scope
boundary

Unit:
name
scope
horizon
```

而不再由 O0 输出旧式 `core_question`、`proposition` 等完整判断。具体“世界现在是什么”“当前默认未来路径是什么”“为什么这些状态会影响结果”“未来世界还能怎样变化”，由后续 O1 的 State、Expectation Baseline、Realization Factor / Materiality 和 Potential Gap / Revision Space 研究完成。当前设计已明确把 O0 定位为 Research Topology construction，并把这些具体研究职责留给 O1。

## O0 四个阶段现在分别做什么

**Candidate Discovery** 是宽召回阶段。C1、C3、C5，以及可用时的 Narrative 分别从自己的研究视角独立发现“未来仍值得持续维护判断的经济对象”。它的任务不是决定最终 Unit，也不是寻找完整未来命题，而是从当前事实、争议、驱动、市场定价和未决问题背后识别长期研究对象。这个阶段宁可多召回，不应因为猜测其他来源会覆盖、当前证据不足或对象仍有重叠就提前删除。

**Shell Synthesis** 第一次从全局视角收敛这些候选。它判断哪些 Candidate 实际是在描述同一个长期经济对象，哪些确实值得分别维护，并把暂时保留的对象组织成共享研究上下文。这里的重点不是寻找共同因果链，而是判断哪些 Units 长期共同研究会真正复用重要背景。Synthesis 产出的是 provisional topology，仍保留候选 provenance，供下一阶段挑战。

**Domain Review** 由 C1/C3/C5 分别从自己的真实领域研究知识出发，判断 provisional topology 是否是合理的业务研究架构。Reviewer 不是 QA 或风险审核员，也不以“发现越多问题越好”为目标；它要独立判断该领域真正重要的持续经济对象是否被合理表达、颗粒度是否合适、Shell 分组是否会帮助后续研究。Review 可以支持现有结构，也可以建议扩大、缩小、合并、拆分、移动或补充对象。当前实际编排中，三个领域分别审阅同一 provisional result，再由 O0 统一处理。

**Shell Finalization** 是 O0 的最终全局结构决策。它综合 provisional topology、未分配候选和实际收到的 Domain Reviews，决定最终哪些经济对象成为 Units、怎样定义它们的范围，以及如何组成最终 Shell。它不是机械执行 Reviewer 修改意见，也不是尽量保存 Synthesis 原结构；二者都只是最终架构判断的输入。Finalization 输出稳定、自然、开放的 Shell/Unit seed，交给 O1 开始真正的预期研究。

整个 O0 可以简化理解为：

```text
Candidate Discovery
发现值得长期研究什么

→ Shell Synthesis
初步判断哪些对象独立、哪些研究应共享

→ Domain Review
从领域业务知识判断这套拓扑是否合理

→ Shell Finalization
统一决定最终 Research Topology

→ O1
在这些对象内部真正研究当前预期模型
```

重构最终想解决的不是“旧 O0 写得不够规范”，而是避免 O0 在研究开始前就替下游决定**正确的因果路径和成功标准**。好的 O0 结果应该让未来新的内部或外部信息都有合理的研究对象可以落入，同时又保持足够明确的业务边界，使 O1 能对每个对象建立深入、可维护、可持续修订的 Expectation Model。