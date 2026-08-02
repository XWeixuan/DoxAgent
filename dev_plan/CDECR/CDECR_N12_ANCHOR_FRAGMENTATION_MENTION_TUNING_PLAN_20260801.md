# CDECR N12、Anchor、碎片化评估与 Mention 调优计划

> 本文用于交给主对话 Codex 执行。本轮重点不是继续扩展 CDECR 功能，而是修复已经通过三轮
> 真实测试暴露出的效能和质量回归，并建立后续能够稳定比较的评估口径。

## 1. 结论与本轮范围

本轮分为四项工作：

1. 在不显著改变现有跨文档编排和 N12 业务决策的前提下，重做 N12 模型输入，降低重复
   Package profile、大 Package、Anchor 集合对 Payload、Token 和时长的放大；
2. 排查并修复 `local_package_hint` 的生成、Judge 继承、Mention→Atomic→Package 聚合和 N11
   canonicalization 闭环；
3. 建立一个简单、稳定、完全离线的 Atomic/Package 碎片化评估口径；
4. 完成上述三项后，开展最多五轮 Mention-only 真实测试和整体调优，目标为
   **Precision > 85%、Recall > 80%**。

本轮暂不做完整30篇端到端验收。Mention 达标后也只保存局部测试结果和候选代码状态，等待其余
优化项完成审查，再统一测试 Field、Atomic、Package 和全流程。

本轮不应继续把当前版本视为唯一正确基线。三次真实测试中，第一次 Mention 为
85.33% Precision / 71.64% Recall；第二次降至 76.88% / 57.09%；当前版本为 69.29% /
65.67%。当前 Recall 较第二次有所恢复，F1 也小幅回升，但 Precision 继续恶化，整体仍明显低于
第一次。调优必须回到三轮代码、Prompt 和编排变化的岔路口，确认哪些改动有效、哪些改动把系统
带偏，而不是默认保留全部新逻辑后继续缝补。

## 2. 当前问题基础

### 2.1 N12 已成为主要成本节点

本轮 N12 `package_assignment` 的运行数据为：

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 38 | 36 | -5.3% |
| request items | 196 | 234 | +19.4% |
| candidate assessment refs | 990 | 1,290 | +30.3% |
| Input Token | 821,126 | 1,607,339 | +95.75% |
| 平均 Input/调用 | 21,609 | 44,648 | +106.6% |
| Input/item | 4,189 | 6,869 | +64.0% |
| Payload bytes | 2,624,173 | 4,611,030 | +75.7% |
| aggregate latency | 1,668,112ms | 2,018,055ms | +21.0% |

N12 调用数没有增加，成本却接近翻倍，说明根因不是调用次数，而是每个 task 和 candidate profile
变重。对本轮36次 N12 调用按当时主 earnings Package 大小进行回放，Package size 与
Input/item 的 Pearson 相关系数约0.765，与 Payload bytes/item约0.780；控制 request item 数后，
Package size与Input、latency的偏相关仍约0.721和0.710。大 Package不是唯一原因，但已经是明确
的核心放大因素。

当前报告中的“51-Atomic earnings Package”物理上有51个成员 ID，其中包含一个已经 redirect
到目标 Atomic 的旧 singleton，因此逻辑上是50个 canonical Atomic。这个重复成员仍会进入物理
Package profile并放大请求，也说明后续成本统计和质量评估都必须先解析 redirect。

### 2.2 当前 N12 shadow Payload并未降本

当前代码同时构造 `legacy_payload` 和 `wire_payload`，但真实模型仍收到 `legacy_payload`。
现有 `wire_payload` 只是改成 task-local 外形，每个 task 中依然展开完整候选 Package，没有形成
请求内共享 Package 字典。

本轮36次 N12 `WIRE_PAYLOAD_SHADOW` 审计显示：

| Payload | 总字节 |
| --- | ---: |
| legacy | 4,611,030 |
| 当前 shadow wire | 4,612,383 |
| 变化 | +1,353（+0.029%） |

因此不能通过直接打开当前 `n12_wire_protocol=on` 解决成本问题；它既没有通过 node A/B gate，
也没有消除 Package profile重复。

### 2.3 Anchor 已有字段，但闭环不足

本轮254个 Mention中有101个 `local_package_hint`，覆盖39.76%；68个最终 Package中只有9个有
`package_anchor_ids`，8个有 `primary_anchor_id`，`anchor_artifact_id`仍为0。最大 earnings
Package保留了12个不兼容 Anchor，最终 `anchor_conflict=true`、`primary_anchor_id=null`。

这说明“保留Anchor集合和冲突”已经工作，但以下问题尚未区分：

- 证据明明支持父事件，Grounder却没有输出 hint；
- Grounder已经输出，Judge split/repair时丢失；
- Mention有 hint，但 Atomic/Package聚合未继承；
- 同一父事件的多个短表述没有在 N11 收敛；
- 某些 hint本身只是Mention复述、公司名或宽泛主题，不是有效父边界。

不能仅通过提高 Anchor覆盖率解决问题。错误或过宽 Anchor会把不同事件更强地拉进同一个大包，
同时继续放大 N12 Payload。

### 2.4 Mention 已经连续偏离第一次测试状态

三轮 Mention变化不能简单归因于单一Prompt语句。当前版本同时包含：

- 更强的 candidate exactly-once/disposition要求；
- 一个 candidate 生成多个原子 draft 的 fan-out；
- Grounder单item repair及根级错误信息；
- missing candidate补处置；
- Judge coverage recovery和局部repair；
- 更严格的 atomicity、assertion和字段保留要求。

本轮从第二次测试恢复了23个 TP，但同时新增32个非严格输出。主要表现为同一 Gold被拆成多个
残片、qualifier/benchmark/period丢失、背景内容被提升为Mention以及新的compound。继续只强化
“每个 candidate必须被处理”很可能进一步提高输出数，却不能保证完整Gold事实的召回。

## 3. N12 Payload 与成本优化

### 3.1 目标边界

本项只优化 N12 输入表示和重复上下文，不改变：

- N12候选召回业务语义；
- `MEMBER / EXTERNAL_RELATED / NOT_RELATED` 等 relation含义；
- `ADD_TO_PACKAGE / CREATE_NEW_PACKAGE` action；
- reason协议；
- N12之后的Apply和Package持久化模型；
- 当前复杂判断节点的batch上限。

不通过删除必要身份信息、扩大复杂batch或改用更弱模型取得表面降本。

### 3.2 新 N12 Payload方向

实现新的请求内字典式 Payload，而不是复用当前无收益的 task-local shadow：

```json
{
  "events": {
    "e1": {
      "proposition": "...",
      "identity": {},
      "anchors": ["a1"]
    }
  },
  "packages": {
    "p1": {
      "family": "EARNINGS_DISCLOSURE",
      "summary": "...",
      "anchors": ["a1"],
      "representatives": [{}, {}]
    }
  },
  "tasks": [
    {
      "event_id": "e1",
      "seed": {},
      "candidates": [
        {
          "package_id": "p1",
          "routes": ["PACKAGE_ANCHOR"],
          "similarity": 0.86
        }
      ]
    }
  ]
}
```

同一个 Package在一个请求中只定义一次；不同task只引用短 `package_id`，并保留该task特有的
recall routes和similarity。输出仍使用现有 N12 Decision Schema，编排层负责短ID还原。

### 3.3 稳定、定长的 Package Card

大 Package不应因为成员从10增长到50而线性扩大模型输入。Package Card只保留判断父边界所需的
稳定信息：

- package family/kind；
- canonical parent anchor、period和必要entity；
- 简短且稳定的title/summary；
- 少量具有不同identity的代表Atomic；
- member count和必要冲突标记；
- 本轮真正影响Package身份的material profile字段。

不发送完整成员ID列表、历史版本、重复retrieval signal和仅用于编排审计的长字段。现有“最多5个
代表成员”可以保留，但需检查每个代表成员是否携带重复的identity、Evidence和source字段；在不
损伤relation判断的前提下，形成一个稳定上限。

Package发生普通成员增长时，不应不断重写越来越长的summary或Anchor文本集合。只有parent
identity、period、artifact、family或代表身份发生实质变化，才改变模型可见Profile。

### 3.4 Redirect与重复成员

构造 N12候选和Package Card前必须解析 Atomic redirect：

- 同一个canonical Atomic不得以source/target两个ID同时进入Package profile；
- member count按canonical ID计算；
-代表成员选择和摘要编译不得重复消费redirect source；
-这属于派生状态修正，不改变业务事实边界。

### 3.5 激活门槛

新 Payload不得直接全量开启。先使用本轮固定 N12输入和决策记录做离线回放，再做 N12-only真实
A/B。至少满足：

- request内candidate coverage=100%；
-每个candidate恰好有一个可还原assessment；
- action、target和relation不存在Schema结构性漂移；
- Gold/人工复核质量不低于legacy；
- Payload bytes和Input Token有实质下降；
-大Package阶段的Input/item不再随成员数显著增长；
-任何非法item只局部降级，不扩大成整批或整文档失败。

Prompt若必须说明字典引用，只增加类似以下短句，并在实施报告中提交完整Prompt差异：

> Resolve each task using the request-local package definitions and return every supplied candidate exactly once.

## 4. Anchor 生成、继承与归一优化

### 4.1 先逐条排查，而不是直接改Prompt

对本轮254个原始Mention逐条读取Source Evidence、Mention字段、Grounder draft、Judge决策、
最终Atomic和Package，建立简单的Anchor审查表：

| 字段 | 含义 |
| --- | --- |
| mention_id/source | 当前Mention及原始证据 |
| expected_anchor | 证据是否支持明确父事件 |
| actual_hint | Grounder最终hint |
| valid_hint | 当前hint是否真是父边界 |
| lost_stage | 未生成或丢失的最早节点 |
| root_cause | Prompt理解、Judge继承、编排聚合或N11归一 |

`expected_anchor`只分三类即可：

- `SUPPORTED`：Source明确存在承载该Mention的父发生、披露容器或持续事项；
- `NOT_SUPPORTED`：只有当前最小事实，没有可证实父边界；
- `AMBIGUOUS`：证据不足，不用于强指标和自动修改。

不要引入复杂的新Gold或评分体系。

### 4.2 重点排查的 missing-anchor bad case

排查不应局限于财报。需要覆盖开放世界中的父事件，例如发布、会议、交易、诉讼、调查、商业
计划、产品活动、市场事件、持续事项等，但不在Prompt中罗列长清单。

重点判断：

- 同一文档多个Mention明显来自同一父发生，却只有部分Mention有hint；
- 财报release/call/report的同一父边界被写成多个不同hint；
- Judge split后子Mention失去原hint；
- reaction错误继承披露Anchor，或披露内容错误继承市场reaction Anchor；
-明确父事件被留空；
-公司名、ticker、文章标题、`latest report`、宽泛主题被误当Anchor；
- hint只是当前Mention换一种说法，没有上位关系。

### 4.3 分层修复方向

按首次失败节点修复，避免所有问题都推给模型：

1. **Grounder未输出**：只有在Prompt和Schema不足以让无上下文模型理解任务时，才做最短必要
   Prompt修正；不追求每条Mention强制有Anchor；
2. **Judge丢失**：KEEP/REVISE/SPLIT默认继承原hint，除非子Mention有明确独立父事件或
   reaction边界；
3. **Mention→Atomic聚合丢失**：Atomic收集全部成员Mention anchors，不选择第一个；
4. **Atomic→Package聚合丢失**：Package取全部成员Atomic anchors并解析redirect；
5. **N11未归一**：同source或有明确artifact/source fingerprint支持的同一父事件短表述应收敛
   到同一canonical anchor；仅文本相似或`latest report`等泛文本不得跨文档合并；
6. **Anchor冲突**：保留完整集合和conflict，但不要因为冲突直接表现成“完全没有Anchor”；同时
   不能让冲突集合无限进入N12 Payload。

### 4.4 Anchor验收

Anchor不以“非空率越高越好”为目标。至少报告：

- eligible Mention中的Anchor Recall：`SUPPORTED`里最终有效hint覆盖率；
- Anchor Precision：输出hint中真正属于父边界的比例；
- Judge继承成功率；
-同父事件hint的canonical收敛率；
-错误跨父事件继承数；
-N12每个Package携带的Anchor数量及冲突分布。

修复后应让相同父边界更稳定、无父边界时仍允许为空，并同步降低大Package的Anchor Payload，
而不是单纯把39.76%覆盖率推高。

## 5. 简化、有效且离线的碎片化指标

### 5.1 边界

碎片化评估绝不能进入Dreamer、Grounder、N9、N12、Prompt、Schema、候选召回或Apply逻辑。
它只存在于离线验收脚本和报告中，不新增模型字段、不影响运行时决策。

singleton比例、Atomic/Mention比例只能作为结构观察，不能作为核心指标。第一轮33-member
supercluster会降低singleton比例，却显然不是高质量结果；同样，一个51-member Package可让
Recall接近100%，也不能证明Package正确。

### 5.2 稳定Gold映射

使用已有30篇Gold，但把比较单位固定在Source事实而不是每轮生成的Mention ID：

```text
Source Gold occurrence
  → Gold Atomic ID
  → Gold Package ID
```

每轮只需把当前Mention映射为对应Gold occurrence、EXTRA或AMBIGUOUS，再解析Atomic/Package
redirect。无需建立新的复杂知识库，也不要求把评估信息写回Registry业务表。

### 5.3 仅保留三个碎片化指标

#### 1. Pair Fragmentation Rate

同一Gold簇成员被拆到不同预测簇的pair占全部Gold同簇pair的比例：

```text
Pair Fragmentation Rate = 1 - Pair Recall
```

这是主指标，Atomic和Package分别计算。

#### 2. Fragmented Cluster Rate

```text
被拆成两个及以上预测组件的多成员Gold簇
÷ 全部多成员Gold簇
```

它防止大Gold簇完全支配Pair指标。

#### 3. Component Profile

对每个碎片bad case只记录：

```text
Gold cluster ID
Gold成员数
当前组件数
组件成员分布，例如 7+1+1+1
最大组件覆盖率
首次分歧节点/根因
```

不再设计复杂熵、综合权重或新的运行时评分。

### 5.4 与Precision绑定

所有碎片化指标必须与Pair Precision并列展示。禁止通过扩大Atomic/Package取得低碎片化。

三轮对比表至少包含：

| 层级 | Pair Precision | Pair Fragmentation Rate | Fragmented Cluster Rate | 典型组件分布 |
| --- | ---: | ---: | ---: | --- |
| Atomic | — | — | — | — |
| Package | — | — | — | — |

评估时先解析redirect。当前51-member Package中的redirect source/target不得作为两个独立Atomic
计pair，否则会制造额外50个物理pair并污染Precision/Recall。

### 5.5 Package的两种观察口径

为了区分上游Atomic碎片和N12/N13自身问题，报告两行即可：

- **End-to-end Package fragmentation**：按最终输出观察用户实际看到的Package碎片；
- **Package-conditional fragmentation**：先把同一Gold Atomic的上游碎片折叠，再判断不同
  Gold Atomic是否进入同一Gold Package。

不把这两种口径用于业务分支，只用于定位责任节点。

## 6. Mention-only 最多五轮真实调优

### 6.1 目标和不变量

目标为：

```text
Mention Precision > 85%
Mention Recall > 80%
```

且不得通过以下方式达标：

- 把同一Gold拆成大量PARTIAL Mention；
- 增加compound；
- 提升非Gold背景陈述；
- 丢失qualifier、benchmark、period、basis或assertion；
- 显著增加UNRESOLVED或Evidence异常；
- 修改后续Field/Atomic/Package依赖的Mention字段和业务含义。

测试路径只运行：

```text
Preprocess
→ Dreamer
→ Grounder及item-local repair
→ Judge及item-local repair/coverage
→ Mention materialize
→ Evidence deterministic location
→ STOP
```

不运行N5.5–N13，避免跨文档模型成本和下游聚类波动干扰Mention归因。

### 6.2 测试前先还原三轮变化

在第一轮真实测试前，必须以三轮测试对应的commit、Prompt版本、Schema和运行配置建立差异表，
至少覆盖：

- Dreamer候选召回Prompt；
- Grounder atomicity、exactly-once、rejection ledger和fan-out；
- Grounder语义validator和repair；
- missing candidate补处置；
- Judge KEEP/REVISE/SPLIT、字段保留和coverage recovery；
- Evidence materialize；
-各轮模型、Prompt hash和batch差异。

这一步的目的是利用已经完成的三次真实测试做历史归因，不是重新运行三个旧版本。模型和输入未变
时，原样重跑第一次或当前版本不会提供足够的新信息，也不应为形式上的“消融”重复消耗Token。

主对话需要根据三轮已有Registry、输出、Prompt和代码差异，先判断：

- 哪些改动与TP、Recall或字段完整性提升相关，应保留；
- 哪些改动与fragment、compound、背景提升和Precision下降相关，应回滚或重构；
- 哪些韧性改动只缩小失败范围、没有改变正常业务结果，应继续保留；
- 哪些现象可能来自模型波动，现有证据不足以宣称代码因果。

在此基础上形成第一版完整Mention重构方案，再进入第一轮真实测试。

### 6.3 单一实验轨道，而不是固定实验组

本阶段只有一个持续演进的实验轨道。每一轮只运行该轮完成后的一个Mention版本，不固定设置
A/B/C，也不把未变化的旧版或当前版作为真实模型实验组重复运行。

第一轮应先完成一版完整的Mention节点重构。重构方案必须来自：

- 前三轮真实结果和逐条bad case；
- 三轮之间的代码、Prompt、Schema和编排差异；
- 第一次测试中较高Precision/Recall对应的有效机制；
- 当前版本已经证明有效的item-local韧性和必要审计；
- 当前fragment、qualifier丢失、background promotion和compound的根因。

这里的“完整重构”是指对Dreamer→Grounder→Judge→Mention materialize整条单文档Mention路径
形成一致的职责和处理方案，不是只针对一两个bad case改Prompt或补规则。它可以大量复用或回退
第一次测试时的处理方式，也可以保留后来已证明有效的局部韧性，但必须解释各部分为什么组合在
一起，而不是把三轮逻辑简单叠加。

第一轮完成重构后，运行一次Mention-only真实测试，并直接与前三轮历史结果比较。随后逐条排查
本轮问题，再形成下一版整体方案。第二至第五轮均重复这一过程，每轮只有一个待验证版本。

离线recorded response回放、代码diff和历史Registry可以用来辅助判断具体机制，但不应把它们
扩展成多个真实模型实验组。五轮预算应优先用于五次有明确整体假设的新版本，而不是复跑未变化
的基线。

### 6.4 每轮必须完成的详细评估

每轮使用同一30篇、同一Gold、同一provider/model和隔离Registry。真实调用完成后，必须逐个
Mention核对：

- 对应Gold、TP/FP/FN/PARTIAL/EXTRA；
- predicate和participants/roles；
- primary metric和quantity；
- qualifier、benchmark、comparison、basis；
- period/time和assertion；
- Evidence是否exact/source-equivalent；
- 是否compound；
- 是否把同一Gold拆碎；
- 是否把分析背景或umbrella陈述提升为Mention；
- 首次错误节点和相关模型/repair/recovery决策。

同时统计：

- Precision、Recall、F1及逐文档分布；
- candidate coverage和disposition；
- Grounder/Judge repair合法恢复率；
- recovery新增TP、FP、fragment和Token；
- Split Rate、Compound Rate、qualifier/period/assertion丢失数；
- Input/Output Token和时长，但质量优先于局部成本。

对每个典型bad case建立三轮和当前轮的迁移关系：

```text
第一次正确 → 第二次丢失 → 当前拆碎
第一次缺失 → 当前正确恢复
第一次compound → 当前仍compound
当前新出现的background promotion
```

### 6.5 每轮之间不是微调，而是整体复审

完成逐条评估后，必须先形成当轮根因报告，再决定下一轮。下一轮改动应是对Mention生成路径的
一个一致方案，例如：

-整体回退某一阶段到第一次状态，并保留已证实有效的item-local韧性；
-重新划分Grounder主生成与recovery责任，避免两者都追求覆盖；
-重构fan-out/atomicity的信息守恒方式；
-简化或替换导致模型拆细的Prompt要求；
-保留repair机制，但恢复第一次较合理的正常路径。

禁止仅针对D20、Micron、某个metric、某个数字或一两条Prompt例子做补丁后立即重跑。即使发现
一个直接bug，也要完成整个Mention路径复审，确认它是否足以解释主要分布，才开启下一轮。

“整体复审”不等于每轮必须增加大量代码。若证据证明最优动作是删除或回滚一组新机制，应优先
做减法，而不是发明新的补偿层。每轮可以向第一次测试状态靠近，也可以在前一轮重构上继续调整，
但必须形成一个新的、完整且可解释的Mention版本后才允许使用下一次真实测试机会。

### 6.6 最多五轮的决策循环

每轮流程固定为：

```text
基于前三轮历史与上一轮结果确定本轮整体重构方案
→ 完成该轮唯一Mention版本
→ 单Mention真实测试
→ 逐条字段级评估
→ 与前三轮历史、上一轮和当前最佳轮比较
→ 根因和收益/回归归因
→ 保留、回滚或整体重构
→ 决定是否进入下一轮
```

规则：

- 达到Precision和Recall目标且fragment/compound等护栏无恶化，可提前停止；
- 最多五轮，不为“再试一次”无限消耗真实模型；
- 连续两轮没有实质提升时，必须停止当前方向并回到更早历史分支；
- 每轮都保留可比较的代码、Prompt、结果和评估快照，不因后一轮失败丢失前一轮收益；
- 五轮结束后选择Precision、Recall和质量护栏综合最优的一轮，不机械选择最后一轮；
- 五轮仍未达标时，保留所有历史和本轮中的最优版本，提交未解决根因，不继续堆叠复杂度；
- 任何一轮出现大面积Schema非法或文档失败，应熔断该轮，不用更多调用掩盖结构问题。

### 6.7 如何决定下一轮方向

每一轮都应与第一次、第二次、当前版本、上一轮和当前最佳轮对照。重点不是证明某个局部改动
“有效”，而是决定下一版Mention路径应整体向哪里移动：

| 本轮表现 | 下一轮方向 |
| --- | --- |
| Precision和Recall同时提高，字段完整性稳定 | 保留本轮主结构，只处理分布性根因后形成下一版 |
| Recall提高但Precision、fragment或background明显恶化 | 回退扩张性机制，重构coverage与atomicity职责 |
| Precision提高但Recall显著下降 | 排查候选丢失、rejection和repair，不直接放宽全部输出 |
| 接近第一次Precision但Recall仍低 | 保留高精度核心，重构缺失候选处理而不是恢复当前全量扩张逻辑 |
| 主要bad case与前三轮重复 | 说明对应历史修复无效，应回到更早岔路口重构而非继续微调 |
| 出现新的系统性bad case | 检查本轮整体设计的副作用，必要时回滚到当前最佳轮 |

每轮进入下一轮前都必须给出：本轮保留什么、删除什么、回到哪个历史岔路、下一版如何重新分配
Dreamer/Grounder/Judge/recovery职责。缺少这一完整判断时，不允许开启下一轮真实测试。

## 7. 实施顺序与阶段交付

### 阶段一：N12和Anchor

1. 完成本轮原始Mention Anchor逐条排查和根因分类；
2. 修复Judge继承、层级聚合、N11 canonicalization和redirect消费；
3. 实现N12请求内Package字典与稳定Package Card；
4. 完成离线回放和N12 node-only A/B；
5. 输出N12成本、质量和Anchor评估报告。

### 阶段二：碎片化离线指标

1. 使用Source Gold occurrence固定三轮比较单位；
2. 解析Atomic/Package redirect；
3. 计算Pair Fragmentation、Fragmented Cluster和Component Profile；
4. 输出三轮Atomic/Package碎片化对比和bad cluster清单；
5. 不改任何运行时业务逻辑。

### 阶段三：Mention-only调优

1. 还原三轮Mention改动和第一次状态；
2. 基于历史证据先完成第一版整体重构；
3. 每轮只测试该轮唯一版本，并与前三轮及当前最佳轮比较；
4. 每轮完成逐Mention字段评估和根因报告；
5. 每轮评估后只允许基于整体判断形成下一版重构；
6. 最多五轮，最终选择综合准召和质量护栏最优的版本；
7. 暂不启动完整CDECR验收。

## 8. 交付物

本轮主对话最终至少应交付：

- N12新旧Payload设计、字节/Token/latency和node-only质量对比；
- Anchor missing/invalid/inheritance/canonicalization bad case清单及根因分布；
- 简化的三轮Atomic/Package碎片化对比；
- 每一轮Mention-only逐条评估报告和轮次对比；
- 三轮历史改动的收益、无效项和回归项判断；
- 最终选择的Mention版本及保留/回滚理由；
- 所有Prompt新增、删除和修改语句的完整清单；
- 尚未解决的问题和后续完整验收前置条件。

## 9. 方向性约束

1. **不显著增加系统复杂性**：优先删除、回滚、合并职责和重构旧路径，谨慎叠加新节点、规则和
   状态；
2. **平衡全局质量**：不得为Recall显著牺牲Precision，不得用超大Atomic/Package换低碎片化；
3. **不做30篇过拟合**：从bad case提炼通用模式，不写专有实体、数字、文章或机构特例；
4. **不设计整篇阻塞**：单item非法只修复、规范化、保留其他合法结果或局部降级；
5. **Prompt与业务合同同步**：任何逻辑、字段或Payload变化都要从无上下文模型视角审查Prompt和
   Schema是否足够自洽；
6. **审计让位于业务成功**：只增加评估所需的短ID、计数和错误码，不扩张LLM reasoning或模型
   Schema；
7. **不抱侥幸心理**：三轮真实测试已经证明当前方向存在系统性偏差。找不到可证实的根因和整体
   方案时，不以局部微调开启下一轮真实调用；
8. **本轮不做完整验收**：完成局部修复、N12/Anchor验证、离线碎片化评估和Mention-only调优后
   停止，等待其余优化项审查。
