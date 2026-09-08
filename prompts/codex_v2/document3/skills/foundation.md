# O3 Shared Foundation

本文件定义 O3 各阶段共同使用的业务语义。当前 stage skill 决定本 Turn 的执行方法；supplied schema 决定对象的精确字段。

## 1. 系统语义链

```text
D2 Expectation Model
→ O3 Operationalization and Calibration
→ D3 Canonical Policy Set
→ Monitoring Configuration
→ Runtime Message Evaluation
```

- **D2** 研究当前 expectation、现实依赖、潜在修订空间、经济传导与完整识别方式。它提供 O3 的研究边界和方向依据，不直接提供 Runtime 条件集合。
- **O3** 把 expectation-level 研究对象转化为 future fact-level Policy 标准，并在消息到来前完成现实校准、充分性判断和方向选择。
- **D3** 是由可召回、可判定、方向预定的 Direct Trading Policies 组成的 canonical Policy Set。
- **W2 / Runtime Evaluator** 接收一条新消息，召回相关 Policy 并判断其中是否有 Condition 成立。它应用既定标准，不重新建立 thesis 或决定某项事实是否具有交易意义。
- **Event / Fact Library 与 Reference View** 为 O3 提供当前现实的版本化参考。它们不是实时消息进入 Policy Evaluation 的同步必经路径；缺少某项记录不等于现实未发生，也不能替代 D2 的 expectation research。

## 2. 共享业务对象

D2 的 Potential Gap 是可能迫使 expectation 修订的现实偏离空间。O3 从完整 D2 研究中展开并研究具体未来事实，再把合格事实编译成 Direct Trading Policies。Potential Gap、研究阶段 Candidate 与最终 Condition 不是可直接映射的同一种对象。

一条 **Direct Trading Policy** 代表一个 principal expectation revision、一个预定 `LONG` / `SHORT` 方向和一次决策含义。它服务 Runtime 的消息召回与事实判定，而不是重新呈现 D2 thesis。

## 3. Direct Trading Sufficiency

**Direct Trading Sufficiency** 指：相对于当前现实与当前市场 expectation，一个未来 occurrence 单独得到确认、其他候选事实仍未知时，已经足以产生显著、方向明确并支持预定 `LONG` / `SHORT` 的 expectation revision。

统一使用以下反事实：

> 如果只有该事实成立，其他候选事实仍未知，它是否已经足以形成同一方向的显著 expectation update？

它要求该事实相对 baseline 有新增量、对 ticker 足够重要、经济传导与方向稳定，并能由现实消息或数据确认。它可以完成整个 revision，也可以是在后续经营结果仍未知时已经具有交易意义的一次推进：

```text
Condition fulfillment
≠ full Gap confirmation
≠ full expectation or earnings realization
≠ guaranteed stock-price reaction
```

Direct Trading Sufficiency 判断的是“这项新信息是否已经足以改变 expectation”，而不是“这项 expectation 是否已经被经营结果证明”。如果事件到 ticker 的 transmission 已由 D2 与 O3 research 建立，不能仅为了增加确定性继续要求目标公司订单、shipment、收入、利润或份额兑现。

“交易充分”只表示足以支持 Policy 预先规定的方向性响应标准，不表示 O3 执行订单、决定仓位或保证市场反应。

## 4. Activation Condition 与 OR 语义

**Activation Condition** 是一个经过研究和 Calibration、仍面向未来、可由现实证据判定的自然完整 occurrence；它单独成立时已经满足对应 Policy 的 Direct Trading Sufficiency。

Condition 的最小性是业务边界的最小充分，而不是语法或 Boolean 原子化。主体或主体类别、动作、对象、方向、必要范围、期间、数量、阈值及法律、合同、产品或商业状态可以共同定义一个 occurrence。若继续放宽 actor 重要性、承诺强度、范围、数量或阶段便不再足以形成同样的 expectation update，边界已经达到所需最小性。

同一 Policy 的全部 Conditions 采用固定 `OR` 语义：

```text
Policy Activated = C1 OR C2 OR ... OR Cn

For every Ci:
Ci alone supports the Policy direction.

For all Ci in one Policy:
same principal expectation revision
+ same LONG / SHORT direction
+ same execution meaning
```

只有一个独立充分 occurrence 时，Policy 只有一个 Condition。存在多个各自独立充分、但代表同一次 principal revision 和一次决策边界的替代 occurrences 时，才形成多条件 Policy。

若 A、B 单独均不充分，但 `A + B` 共同构成一个自然完整、现实中可由一条消息或一个可判断状态确认的充分 occurrence，则将整体写成一个复合 Condition。A、B 是该 occurrence 的必要属性，而不是两个 OR Conditions。

Natural composite 中的多个属性应共同定义同一次现实动作、决定、合同、规则、交易或生产状态。仅处于同一因果链并不足以构成一个 occurrence。客户决定、供应商订单、目标公司份额、shipment 和利润等由不同主体或后续阶段产生的事实，不因经济相关就自动属于同一个 Condition。

## 5. Occurrence 与 supporting information

**Occurrence** 是 Condition 描述的现实变化本身，回答“现实中发生了什么”。**Evidence** 是确认该 occurrence 已成立的披露、文件、数据或其他观测，回答“凭什么认定它发生了”。

```text
One occurrence + multiple evidence forms
≠ multiple Activation Conditions
```

公司、客户、供应商、监管文件、产品材料、合同、采购状态、可信报道与可比经营数据都可能成为 Evidence。同一 occurrence 的不同来源或证明形式仍是一个 Condition。只提高信心、解释经济后果或单独不改变 expectation 的信息属于 supporting information，不形成 Condition。

## 6. 一次性决策边界与生命周期

同一 Policy 的 OR Conditions 必须是同一次决策的替代充分路径。若一个 Condition 今天触发并使当前 baseline 更新，另一个 occurrence 明天发生仍会产生新的、值得再次交易的 expectation delta，它们属于不同 Policies。

Active Policy 在任一 Condition 首次满足后，其当前决策边界即被消费。Runtime 对该次消息只形成一次 activation；Maintenance 再基于更新后的现实判断 retire 或重新校准新的未来 Policy，而不是继续等待原 Policy 的其他 OR Conditions。

## 7. 语义所有权

本文件只定义跨阶段不变的业务语义。Supplied Policy schema 是 Published Policy 字段含义与输出结构的唯一 canonical contract；Trigger Calibration skill 负责如何研究 Candidate，Policy Compile skill 负责如何形成 Policy grouping 和字段值，Final Review 负责按既有合同验收。各阶段引用这些语义，不另建一套字段 ontology。
