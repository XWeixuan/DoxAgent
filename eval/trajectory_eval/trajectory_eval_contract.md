# DoxAgent Trajectory Eval Contract

## 目标

`trajectory_eval` 用于对 DoxAgent workflow 中 agent loop / trajectory 进行人工可读、结构化、可复用的过程评估。

本 contract 的目标是建立后续评估指定 run 时必须遵循的 SOP、评分维度、报告模板和执行约束。它不用于启动新的 workflow run，也不用于修改业务代码、prompt、schema 或编排逻辑。

## 目录约定

```text
eval/trajectory_eval/
  trajectory_eval_contract.md
  report/
```

- `trajectory_eval_contract.md`: 本文件，记录 trajectory eval 的参考执行 SOP、评分维度、报告模板和执行约束。
- `report/`: 每次评估指定 run 后，将 eval report 存放在这里。

报告建议命名：

- `{run_id}_trajectory_eval.md`
- `{date}_{run_name}_trajectory_eval.md`

## 评估基本单位

trajectory eval 的基本单位不是整个 workflow，也不是最终输出，而是：

**workflow 中某个 agent 负责的单个节点。**

示例节点包括：

- `GenerateKnownEvents`
- `ReviewMonitoringConfig`
- `GenerateMonitoringPolicy`
- `ReviewExpectationConstruction`
- `ReviewExpectationFields`

每个节点必须单独评估。如果一个节点内部有多轮 loop，必须逐轮展开阅读、记录和评分依据。不得把多个节点合并成一个笼统结论，也不得只用最终 workflow 输出反推节点质量。

## 数据来源

每次评估由用户指定一个 run。评估者只读取该 run 的 trace 数据，不启动新的 run，不重新执行 workflow。

近期 runs 通常通过 LangSmith MCP 访问。对指定 run，必须读取每个被评估节点下每一轮 loop 的完整 trace，至少包括：

- input
- output
- tool call
- tool observation / tool result
- retry / repair / validation 信息
- 节点之间的路由或终止信息
- token、latency、loop count 等可见元数据

禁止只看 summary、最终输出、高层 trace 概览或 Brief State 就下结论。若某一类 trace 字段在 LangSmith MCP 中不可见，报告中必须明确标记为 `不可见`，并说明这会如何影响可信度。

## 总体判读原则

1. 先按 workflow node 定位，再进入该节点的 agent loop。
2. 先读完整 input / output / tool trajectory，再评分。
3. 过程质量和最终结果分开判断：结果可用不代表 trajectory 健康，trajectory 笨重也不一定代表结果失败。
4. 不从自然语言声明推断工具调用已经发生；必须以 tool call / observation 为准。
5. 不把格式完整等同于判断质量。
6. 不把 retry / repair 自动视为负面；要判断它是否必要、是否收敛、是否吸收了新证据。
7. 不把单点偶发问题直接放大为系统性问题；系统性结论必须有多个节点、多个 loop 或相同机制重复出现的证据。
8. 不在 trajectory eval 过程中修复业务代码。评估报告末尾只给优化方向研判，不给具体代码级执行方案。

## 强制执行约束

1. 不要启动新的 run。
2. 不要修改 workflow 业务代码。不要边评估边修复。
3. 必须完整读取每一轮 loop 的 trace，包括 input 和 output。
4. 禁止只看最终输出或 summary。
5. 必须按节点逐个评估。
6. 必须在读完每个节点的所有 loop 后，立即在 markdown report 中写入该节点的 eval 内容。
7. 不允许读完整个 run 后再一次性生成 report。
8. 如果 LangSmith MCP、trace 可见性、权限或数据缺失导致无法完整评估某节点，必须停止该节点的评分结论扩张，在报告中标记为 `证据不足`，并写清楚缺失字段。
9. 如果发现指定 run 与用户要求不一致，例如 run id 不存在、trace 不是 DoxAgent workflow run、节点缺失或节点名无法匹配，必须先说明异常，不得用相邻 run 或猜测数据替代。

## 节点评估维度

每个节点按以下 7 层逐项评分。每层都必须给出：

- 1-5 分评分
- 任务完成情况
- 具体问题
- 是否存在潜在优化点

评分标准：

- 5: 表现优秀，基本无明显问题
- 4: 整体可用，有轻微问题
- 3: 能完成任务，但存在明显质量或效率问题
- 2: 问题较重，影响节点可靠性
- 1: 基本失败，严重偏离目标或造成阻塞

### 1. 目标理解层

看当前 loop 到底在解决什么子任务，是否和 workflow 当前阶段匹配。

重点检查：

- 节点是否做了不属于自己的事
- agent 是否发生目标漂移
- 是否为了满足格式而牺牲判断质量
- 是否清楚理解本节点的职责边界

### 2. 上下文层

看输入是否足够、是否过载、是否包含 stale / duplicate / irrelevant 信息。

重点检查：

- context 是否过长或噪音过多
- 是否缺少关键输入
- 是否有无关材料淹没重点
- 是否旧状态污染新判断
- 是否重复读取或重复总结同一批信息

### 3. 路由层

重点看为什么继续 loop、为什么停止。

重点检查：

- stop condition 是否清晰
- retry 是否过度
- 失败后是否进入错误 fallback
- 是否存在循环无法收敛的问题
- agent 是否清楚说明当前状态、下一步意图和终止依据

### 4. Tool Calling 层

看 tool 选择、参数、顺序和结果吸收是否合理。

重点检查：

- 是否选错工具
- tool 参数是否正确
- 是否存在参数幻觉
- 是否重复调用低价值工具
- 是否调用工具后没有吸收结果
- 是否用自然语言假装完成了工具调用
- tool result 是否被正确转化为后续判断

### 5. 状态变更层

重点考察每一步是否遗漏上一轮已经知晓的数据或信息。

重点检查：

- 是否遗漏前一轮已获得的信息
- 是否重复写入
- 是否旧结论覆盖新证据
- patch 粒度是否过大
- 是否存在事务边界不清
- 是否对 blackboard / memory / DB / 中间状态造成潜在污染

### 6. 质量层

看最终结论是否更准确、更可执行、更有证据。

重点检查：

- 输出是否只是格式完整但判断空泛
- 引用、证据和结论是否脱节
- 是否有新增分析价值
- 是否能推动 workflow 进入下一阶段
- 是否存在 unsupported claim、泛化判断或证据不足的问题

### 7. 效率层

看 token、latency、tool 次数、loop 次数、失败重试次数。

重点检查：

- 节点成本占比是否异常
- 是否反复总结同一信息
- 是否有低价值 agent 消耗主成本
- 是否存在不必要的 retry、repair、review
- 是否可以通过 prompt、上下文裁剪或编排边界减少 loop

## 节点评分记录格式

每个节点评分必须使用以下结构：

```markdown
## 节点：<workflow_node>

### Trace 覆盖
- LangSmith run / child run:
- loop 数:
- 已读取字段:
- 不可见或缺失字段:
- 节点入口:
- 节点出口 / 路由:
- token / latency / tool count 摘要:

### 7 层评分
| 维度 | 分数 | 任务完成情况 | 具体问题 | 潜在优化点 |
| --- | ---: | --- | --- | --- |
| 目标理解层 |  |  |  |  |
| 上下文层 |  |  |  |  |
| 路由层 |  |  |  |  |
| Tool Calling 层 |  |  |  |  |
| 状态变更层 |  |  |  |  |
| 质量层 |  |  |  |  |
| 效率层 |  |  |  |  |

### 节点小结
- 主要结论:
- 主要风险:
- 是否建议进入系统性问题清单:
```

## Loop 逐轮梳理要求

在完成 7 层评分后，必须继续按 loop 逐轮梳理该节点内部发生了什么。

有多少轮 loop，就写多少条。每一轮 loop 至少记录：

```markdown
#### Loop <n>

- Loop 编号:
- 本轮输入概况:
- 本轮 agent 判断的当前状态:
- 本轮 agent 打算解决什么问题:
- 本轮实际做了什么:
- 调用了什么工具:
- tool 参数是否合理:
- tool 结果是什么:
- tool 结果是否被吸收:
- 本轮输出概况:
- retry / repair / validation 信息:
- token / latency / metadata:
```

如果某轮没有 tool call，也必须明确写 `无 tool call`，并判断这是否合理。

## Report 文件模板

每次指定 run 的评估报告应写入 `eval/trajectory_eval/report/`，并使用以下模板。

```markdown
# Trajectory Eval Report - <run_id or run_name>

## 基本信息

- run_id:
- run name:
- ticker / scenario:
- 评估日期:
- 评估者:
- 数据来源:
- LangSmith project / trace link:
- 评估范围:
- 未评估节点及原因:

## 执行约束确认

- 是否启动新 run: 否
- 是否修改业务代码: 否
- 是否逐节点评估: 是
- 是否逐 loop 读取 input / output / tool trace: 是
- 是否存在 trace 缺失:
- 缺失字段说明:

## 节点评估索引

| 节点 | loop 数 | 综合风险 | 主要问题 |
| --- | ---: | --- | --- |
|  |  |  |  |

<!--
以下内容必须按节点逐个写入。
每读完一个节点的全部 loop，就立即把该节点内容写入本报告。
不得等全 run 读完后再统一生成。
-->

## 节点：<workflow_node>

### Trace 覆盖
- LangSmith run / child run:
- loop 数:
- 已读取字段:
- 不可见或缺失字段:
- 节点入口:
- 节点出口 / 路由:
- token / latency / tool count 摘要:

### 7 层评分
| 维度 | 分数 | 任务完成情况 | 具体问题 | 潜在优化点 |
| --- | ---: | --- | --- | --- |
| 目标理解层 |  |  |  |  |
| 上下文层 |  |  |  |  |
| 路由层 |  |  |  |  |
| Tool Calling 层 |  |  |  |  |
| 状态变更层 |  |  |  |  |
| 质量层 |  |  |  |  |
| 效率层 |  |  |  |  |

### Loop 逐轮梳理

#### Loop 1

- Loop 编号:
- 本轮输入概况:
- 本轮 agent 判断的当前状态:
- 本轮 agent 打算解决什么问题:
- 本轮实际做了什么:
- 调用了什么工具:
- tool 参数是否合理:
- tool 结果是什么:
- tool 结果是否被吸收:
- 本轮输出概况:
- retry / repair / validation 信息:
- token / latency / metadata:

### 节点小结
- 主要结论:
- 主要风险:
- 是否建议进入系统性问题清单:

## 全 run 统一总结

### 1. 主要问题清单

| 编号 | 问题 | 涉及节点 | 证据 | 影响 |
| --- | --- | --- | --- | --- |
| P1 |  |  |  |  |

### 2. 严重程度排序

| 严重程度 | 问题 | 涉及节点 | 原因 |
| --- | --- | --- | --- |
| High |  |  |  |
| Medium |  |  |  |
| Low |  |  |  |

### 3. 单点偶发 vs 系统性模式

| 问题 | 类型 | 判断依据 |
| --- | --- | --- |
|  | 单点偶发 / 系统性模式 |  |

### 4. 当前最需要的优化方向

- 研判结论:
- 优先级:
- 不展开到代码级执行方案:
```

## 推荐执行 SOP

1. 确认用户指定的 `run_id`、评估范围和目标节点列表。
2. 通过 LangSmith MCP 定位该 run 的根 trace 和 workflow child runs。
3. 建立节点清单，按 workflow 顺序排列。
4. 选择第一个待评估节点。
5. 读取该节点所有 loop 的完整 input、output、tool call、tool observation、retry、repair、validation、routing 和元数据。
6. 若该节点 trace 不完整，先记录缺口，不扩张结论。
7. 对该节点完成 7 层评分。
8. 对该节点逐 loop 写入梳理记录。
9. 立即把该节点 eval 内容写入 report 文件。
10. 再进入下一个节点，重复步骤 5-9。
11. 所有节点完成后，再写全 run 统一总结。
12. 总结中只给问题清单、严重程度排序、单点/系统性判断和优化方向研判，不给代码级修复方案。

## 异常处理

如果遇到以下情况，应暂停相关判断并在报告中说明：

- 指定 run 无法访问。
- LangSmith MCP 返回的 trace 不包含节点级 child run。
- 某节点缺少 input 或 output。
- tool observation 缺失，无法确认工具结果是否被吸收。
- retry / repair / validation 信息不可见。
- 节点名称与 workflow 中的已知节点无法匹配。
- trace 数据和本地 workflow 命名明显不一致。

异常不等于直接失败。应区分：

- `证据缺失`: 无法评分或只能低置信评分。
- `节点行为失败`: trace 证据显示节点没有完成职责。
- `workflow 编排异常`: 节点路由、终止或 retry 边界异常。

## 禁止事项

- 禁止启动新的 workflow run。
- 禁止为了补 trace 而重新执行 run。
- 禁止修改 workflow 业务代码、prompt、schema、validator 或编排逻辑。
- 禁止只看最终输出、Brief State、summary 或 dashboard 视图就评分。
- 禁止把自然语言自述当作 tool call 证据。
- 禁止在没有逐 loop 证据的情况下下系统性结论。
- 禁止读完整个 run 后再一次性生成 report。
