# Expectation Shell Construction v2

## 一、目标

Shell Construction 的职责不是重新做一次研究，也不是直接生成完整 Expectation Detail，而是把已经发布的研究结果转换为一套稳定的 Expectation Shell / Unit 结构。

当前 Global Research 已经由 C1、C3、C5 分别承担公司基本面、行业与价值链、市场隐含预期研究，C4 enrichment 最终整合并产出 `future_nodes`。因此 Shell Construction 应主要消费这些已经形成的研究资产，而不是重新依赖 Narrative Report 决定结构。

最终流程：

```text
Independent Candidate Discovery
        ↓
Candidate Structure Synthesis
        ↓
Domain Review
        ↓
Final Apply
        ↓
Final Expectation Shells
```

核心原则：

> Candidate Discovery 追求 Recall；
> Structure Synthesis 负责第一次收敛；
> Domain Review 负责发现结构错误；
> O0 保留最终架构裁决权。

---

# 二、输入层级

## 主要研究输入

四个独立 Candidate Discovery 来源：

* C1 Global Research；
* C3 Global Research；
* C5 Market-implied Research；
* Narrative Research。

其中 C1/C3/C5 是主要研究依据，Narrative Research 只是独立的辅助市场视角，不拥有更高结构权重。

## 共同辅助上下文

四个 Candidate Threads 都可以读取：

* Future Nodes；
* Event Library；
* `document1` 的 `d1_horizontal_indicators_collection`。

三者都只作为辅助信息源，不直接决定 Candidate 或 Shell。

Future Nodes 提供未来事项背景。

Event Library 提供近期已经发生的原子事实与事件聚合，帮助 Agent 理解近期现实上下文。

`d1_horizontal_indicators_collection` 提供前面阶段收集的横向数据指标，作为 Document2 研究时的全局参考，帮助 Agent 补充和比较不同 Candidate、Shell 或 Unit 涉及的行业、公司及市场背景。它不替代针对具体 Expectation 的研究，也不要求其中的每个指标都被映射到 Candidate、State、Factor 或 Gap。

不建立：

```text
一个 Future Node → 一个 Candidate
一个 Event Cluster → 一个 Candidate
一个横向指标 → 一个 Candidate
```

这样的机械映射。

---

# 三、Node 1：Independent Candidate Discovery

## 1. Workspace 与 Thread

建立一个固定的 O0 Shell Construction Workspace，并创建四个彼此独立的临时 Threads：

```text
O0-C1
O0-C3
O0-C5
O0-Narrative
```

分别读取：

```text
对应研究 Artifact
+
Future Nodes
+
Event Library
+
统一 Candidate Unit 定义
```

四个 Thread 彼此看不到其他 Thread 的 Candidate 输出。

完成 Candidate Discovery 后结束这些 Threads。

这样可以避免：

> 先读取某个研究板块后形成早期 ontology，再把后续领域的信息强行解释进已有结构。

---

# 四、Candidate Unit 的定义

Candidate 不是“重要信息”，而是：

> **未来仍需要持续判断、结果变化可能实质改变公司盈利、现金流、风险或估值等基本面或各方对公司预期，并有潜力成为独立可更新中层预期命题的问题。**

例如：

合格：

> Micron 能否在 Vera Rubin 周期获得具有实质财务贡献的 HBM4 订单份额？

不合格：

> Micron Q3 财报超预期。

后者是已经发生的 Event。

也不应直接把：

> HBM4 良率

当作 Candidate，因为它更接近 State Parameter 或 Realization Factor。

但：

> Micron 的 HBM4 技术和量产能力是否足以支持有意义的市场份额？

可以成为 Candidate。

---

# 五、Node 1 的 Candidate 输出

每个 Candidate 使用简单临时编号：

```text
U1
U2
U3
...
```

编号只服务 Shell Construction 与 Review，不进入最终 Document2。

最小内容：

```text
candidate_id
candidate
reason
references
```

### `candidate_id`

如：

```text
U1
```

用于后续 O0 和 Reviewer 精确引用。

### `candidate`

用一句中层命题描述候选 Expectation Unit。

### `reason`

简要说明为什么它值得成为独立持续管理的预期问题。

重点包括：

* 对公司价值是否重要；
* 是否存在未来独立更新空间；
* 是否不仅仅是一个 Event、State 或普通风险。

### `references`

提供主要研究 citation。

Candidate Discovery 阶段宁可保留重复 Candidate，也不要主动为了去重而减少召回。

---

# 六、Node 2：O0 Structure Synthesis

四个 Candidate Threads 完成后，新建：

> O0 Synthesis Thread

一次性读取：

```text
C1 Candidate Set
C3 Candidate Set
C5 Candidate Set
Narrative Candidate Set
Future Nodes
Event Library
```

这个 Thread 不继承任何 Candidate Producer 的 thread context。

---

# 七、Candidate Relation Analysis

Node 2 不应直接：

> 读取 Candidate → 生成几个 Shell。

O0 首先需要理解 Candidate 之间的结构关系。

Candidate Relation Analysis 是一种思考方法，而不是要求 Agent 给每一对 Candidate 打标签。

O0 需要从几个角度思考。

## 1. 两个 Candidate 是否实际上在问同一个问题？

例如：

> HBM 紧缺持续多久？

与：

> HBM 什么时候恢复供需平衡？

可能只是不同表述。

如果核心判断、未来验证事件和结果高度重合，应考虑统一表达。

---

## 2. 两个 Candidate 是否存在明显的上下游关系？

例如：

```text
AI Capex 持续性
→
HBM 需求持续性
```

存在因果关联并不意味着它们必须成为同一个 Unit。

如果两者拥有不同：

* 时间范围；
* Event Space；
* 独立验证方式；
* 对其他公司的传导；

仍可能保持两个 Unit。

---

## 3. 哪些 Candidate 必须共享完整上下文才能研究？

需要考虑它们是否大量共享：

* 业务与行业背景；
* 关键外部主体；
* State Parameters；
* Realization Factors；
* 未来事件空间；
* 价值传导路径。

共享程度高，是进入同一 Shell 的重要原因。

---

## 4. 哪些 Candidate 虽然都影响同一 ticker，但实际上属于独立系统？

例如：

> AI 存储周期与盈利兑现

与：

> 出口管制永久改变中国业务价值。

如果两者拥有基本独立的：

* 研究逻辑；
* 事件空间；
* 状态参数；
* 终端价值结果；

则更适合拆分 Shell。

---

## 5. Candidate 本身是否真的应该成为 Unit？

O0 还需要主动判断：

> 某些 Candidate 虽然重要，但是否更适合作为后续 Unit 的 State Parameter、Realization Factor、Event、背景条件或研究 Unknown？

也就是说，Candidate Relation Analysis 不只是“怎么分组”，还要重新审查 Candidate 的对象层级。

---

# 八、Provisional Shell Draft

完成关系分析后，O0 形成第一次 Shell Draft。

## 临时编号

Shell 使用：

```text
S1
S2
S3
```

Candidate Unit 继续使用：

```text
U1
U2
U3
```

这些都只是 review handle。

---

## 每个 Provisional Shell

包含：

```text
shell_temp_id
core_question
boundary_reasoning
candidate_units
```

### `shell_temp_id`

如：

```text
S1
```

### `core_question`

这个 Shell 真正共同解决的终端投资问题。

### `boundary_reasoning`

详细但聚焦地说明：

* 为什么这些 Candidate Units 应共享同一研究上下文；
* 它们共享什么主要业务或价值传导系统；
* 为什么它们仍然需要保持为独立 Units；
* 当前 Shell 与其他 Shell 的实际边界在哪里。

这是 Draft 审查最重要的依据之一。

### `candidate_units`

列出：

```text
U1
U3
U7
...
```

同时保留 Candidate 的当前统一表述。

---

# 九、未进入 Draft 的 Candidate

任何 Candidate 都不能静默消失。

未进入 Provisional Shell Draft 的 Candidate 单独记录：

```text
candidate_id
candidate
reason
```

例如：

```text
U8

candidate:
Micron下一季度HBM4良率变化

reason:
该问题具有研究价值，但更适合作为“Micron HBM4订单份额兑现”Unit下的State/Realization Factor，而不是独立的中层Expectation Unit。
```

O0 直接用简短自然语言解释为什么当前没有把它作为独立 Unit 保留即可。

这样 Reviewer 能区分：

> O0 主动判断它不应成为 Unit

和：

> O0 单纯遗漏。

---

# 十、Node 3：Domain Review

Provisional Draft 分别复制到：

```text
C1 Workspace
C3 Workspace
C5 Workspace
```

恢复它们原先完成 Global Research 时使用的 role thread。

这样 Reviewer 保留完整领域研究上下文。

但 review 中涉及事实和结构判断的依据仍应尽量落到已发布研究结果和 citation，而不是单纯使用隐含 thread memory。当前实现也明确将 Thread 定位为可恢复执行句柄，业务事实仍以 artifact、bundle 和 citation 为准。

---

# 十一、Domain Review 的定位

Reviewer 不负责重新生成 Shell Draft。

它的任务是：

> **从自己完整领域研究的视角，对 O0 的 Provisional Structure 进行有证据的结构挑战。**

每个 Reviewer 输出一份完整 Review Feedback。

建议结构如下。

## 1. Overall Assessment

简洁评价当前整体结构：

* 是否基本合理；
* 最主要的问题在哪里；
* 本领域有没有明显被低估或过度提升。

---

## 2. Targeted Feedback

每一条 Feedback 必须指向明确对象。

例如：

```text
Target:
S1

或

Target:
S1 / U3

或

Target:
Missing Candidate
```

然后说明：

### Issue

具体存在什么结构问题。

### Reasoning

为什么这是问题。

必须结合领域研究解释，不能只写：

> “我认为应该拆分。”

而应说明：

* 两个问题为什么不是同一个可更新预期；
* 它们的业务逻辑、验证事件或时间范围有什么区别；
* 为什么当前划分会导致后续 State/Factor/Gap 混乱。

### References

给出支持该 Review 判断的主要 citations。

### Recommendation

明确建议 O0 如何处理。

可以包括：

* 调整 Candidate 表述；
* 新增 Candidate；
* 删除 Candidate；
* 把 Candidate 降为 State / Factor；
* 把 Unit 移到其他 Shell；
* Split / Merge；
* 调整 Shell Boundary。

但 Reviewer **不直接重写 Shell Draft**。

---

# 十二、Review 的关键原则

Domain Reviewer 不是：

> “保护自己领域内容的人。”

而应该同时检查：

> **本领域内容有没有被漏掉，也有没有被过度提升。**

例如 C3 不只是应该问：

> 有没有漏掉行业问题？

也应该问：

> 某个行业变量是不是其实只应该成为 State/Factor，而不值得独立成为 Unit？

C5 也不能因为某个问题在市场定价中很突出，就自动要求独立 Shell。

---

# 十三、Node 4：Final Apply

Node 4 恢复 Node 2 的原 O0 Synthesis Thread。

原因是 Domain Review 天然带有局部视角，而 O0 已经完成：

* 全 Candidate 阅读；
* Candidate Relation Analysis；
* Shell Boundary 判断；
* Provisional Draft。

因此保留原 Synthesis Thread 有助于用全局结构认知约束领域 Reviewer 的局部偏好。

---

# 十四、避免 Final Apply 的顺序偏差

Node 4 不允许：

```text
读取 C1 Review
→ 修改 Draft

读取 C3 Review
→ 再修改

读取 C5 Review
→ 再修改
```

而应一次性将以下内容提交给 O0：

```text
Provisional Shell Draft

C1 Review Feedback
C3 Review Feedback
C5 Review Feedback
```

O0 在完整看到所有 Review 后统一裁决，然后**一次性输出最终结果**。

---

# 十五、Final Apply 的审计说明

O0 可以额外输出一份非常简短的：

> Finalization Note

只说明重大结构修改：

例如：

```text
- 接受 C3 对 U4/U7 过度合并的意见，将两者拆分为独立 Units；
- 未接受 C5 将估值争议独立为 Shell 的建议，因为其主要作为现有盈利兑现预期的市场定价层，而非独立经济系统；
- 新增 C1 提出的现金流兑现 Candidate，并纳入 S1。
```

这个 Note 只用于审计，不进入 Document2 正文。

不需要保存长篇 O0 reasoning。

---

# 十六、Final Apply 的正式交付

Final Apply 的正式结果应直接向 Document2 Schema 靠拢。

临时编号：

```text
S1
U1
U2
```

全部结束使命。

O0 将它们转换成最终自然语言语义 ID。

---

## Final Shell

严格使用：

```text
shell_id
core_question
boundary_rule
units
```

这与当前 Document2 Shell Schema 一致。

例如：

```json
{
  "shell_id": "AI存储周期与Micron盈利兑现",
  "core_question": "AI存储需求、行业供给约束与Micron竞争兑现，能否持续支撑其盈利增长？",
  "boundary_rule": "本Shell覆盖AI存储需求经行业供需、Micron业务份额与产品结构向收入、利润和现金流兑现的主要传导系统；拥有独立终端价值结果、事件体系和预期状态，且无需依赖该传导背景即可独立研究的预期应拆分至其他Shell。",
  "units": [...]
}
```

---

## Final Unit Seed

Shell Construction 阶段只冻结 Unit Definition：

```text
expectation_id
proposition
horizon
```

例如：

```json
{
  "expectation_id": "Micron在Vera Rubin周期获得具有实质财务贡献的HBM4订单份额",
  "proposition": "在Vera Rubin产品周期内，Micron能够取得足以形成实质财务贡献的HBM4订单份额。",
  "horizon": "Vera Rubin首轮量产及主要订单分配周期"
}
```

随后 Detail Workflow 在**同一个 Expectation Unit 对象上继续补充**：

```text
state
realization_factors
potential_gaps
```

最终形成完整 Document2 Unit Schema。当前完整 Unit 的目标结构本身也是 `expectation_id + proposition + horizon + state + realization_factors + potential_gaps`。

---

# 十七、Future Nodes 与 Event Library 的定位

Future Nodes 与 Event Library 都属于 O0 Candidate Discovery / Synthesis 阶段的**辅助上下文**。

不赋予它们：

* Candidate 权重；
* Shell 验证权；
* Candidate Priority；
* Final Review 特殊裁决逻辑。

尤其 Event Library 只覆盖近几周现实事件，不能因为近期某类事件多，就推断它对应的 Expectation 更重要。

它的作用仅限于：

> 在 Agent 阅读研究、理解当前现实背景和发散 Candidate 时提供额外事实上下文。

不再额外设计“Event Library 检验 Shell”的规则。

---

# 十八、Narrative Research 的定位

Narrative Research 与 C1/C3/C5 一样拥有独立 Candidate Discovery Thread，但语义仍然不同。

它擅长发现：

* 当前市场关注什么；
* 市场争论什么；
* 哪些问题存在明显公共叙事。

它并不能证明：

> 某个 Narrative 一定值得成为 Expectation Unit。

因此 Narrative Candidate 最终仍由 O0 与其他研究来源一起进行结构判断。

不设人为降权分数，也不因为 Narrative 热度自动晋升 Candidate。

---

# 十九、最终 Workflow

```text
                   O0 Shell Construction Workspace
                                │
       ┌────────────────┬───────┼───────────────┐
       │                │       │               │
 O0-C1 Thread      O0-C3 Thread O0-C5 Thread O0-Narrative Thread
       │                │       │               │
 C1 Candidates      C3 Candidates C5 Candidates Narrative Candidates
       │                │       │               │
       └────────────────┴───────┴───────────────┘
                                │
                      Candidate Threads End
                                │
                                ▼
                     Fresh O0 Synthesis Thread
                                │
                  Candidate Relation Analysis
                                │
                    Provisional Shell Draft
                    + Unselected Candidates
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
    C1 Original Thread    C3 Original Thread    C5 Original Thread
          │                     │                     │
    Review Feedback       Review Feedback       Review Feedback
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                │
                                ▼
                    Resume O0 Synthesis Thread
                                │
                  一次性读取全部 Review Feedback
                                │
                       Unified Final Apply
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            Finalization Note        Final Shell Objects
               （审计）             （进入Document2）
```

---

# 二十、各阶段错误偏好

整个 Workflow 应故意让不同阶段承担不同错误方向。

## Node 1

> 宁可多，不要漏。

高 Recall。

---

## Node 2

> 删除错误 Candidate，建立正确边界。

第一次 Precision 收敛。

---

## Node 3

> 找结构错误，不重新建立全局架构。

领域挑战。

---

## Node 4

> 综合全部信息后一次性裁决。

最终 Precision。

---

# 二十一、方案的核心价值

这套 Shell Construction 不再让：

```text
Narrative Report
→
单个 O1
→
直接决定整个 Document2
```

而是：

```text
多个独立研究视角
→
独立 Candidate Discovery
→
O0 全局结构综合
→
原领域 Agent 有证据挑战
→
原 O0 全局裁决
```

同时又没有引入：

* Global Research Agenda；
* Agent 投票；
* 多轮自动辩论；
* Shell Scoring；
* 固定 Candidate Relation 枚举；
* Reviewer 直接修改架构；
* 无限 Repair Loop。

最终 Shell Construction 的职责可以概括成：

> **尽可能完整地发现值得长期管理的中层预期问题，再通过全局结构分析和领域证据审查，将这些问题收敛成一套具有清晰上下文边界、能够继续进入 Expectation Detail 构建的 Shell / Unit 架构。**
