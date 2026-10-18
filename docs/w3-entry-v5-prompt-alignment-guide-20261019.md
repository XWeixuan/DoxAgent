# W3 新编排下的 Agent 操作与提示词对齐说明

面向负责 AGENTS/Skill 重构的对话。依据当前本地实现；适用于提供 `entry_version=w3-entry-v5`、`context_ref` 的新入口，不代表生产已经切换。业务输出合同仍为 `persistent-runtime.w3.v4`。

## 1. 提示词需要适应的主要变化

| 任务 | 新编排下的完成方式 |
| --- | --- |
| 获得任务规则 | 公共母 Thread 已加载 AGENTS 和唯一 Skill，Case 继承；直接处理本轮输入，无需例行重新读取 AGENTS、Skill、task.json、output_schema.json。真正出现上下文丢失或合同疑点时，仍可必要回查。 |
| 理解当前 Case | 消息、W1/W2、关联 Policy、相关状态、历史意图与 WATCH/PIN 等必读内容由当前 user prompt 直接提供。已给出的完整内容直接使用，不先按 ID 再取一遍。 |
| 获取可选研究 | 通过 W3 Research Assets MCP 按需读取，不再依赖每个 Workspace 的完整研究副本，也不再猜目录、文件名或编写 Shell/Python 解析脚本。 |
| 查看行情 | 后台已与研判并行准备行情；需要时调用统一 `market_view`，不自行调用 IBKR、找合约、分配 client_id 或维护连接。 |
| 交付结果 | 先提交交易阶段结果，宿主持久化后再执行同一 Case 的运营阶段；不在第一阶段提前完成整套运营输出。 |

这是信息交付和操作方式的变化。是否研究、研究哪些对象、何时可以作出判断，仍由业务提示词决定；有工具入口不等于必须调用所有工具。

## 2. 进入 Case 后，先用已有信息

公共规则已继承，但当前 Case 的事实、阶段、截止时间、状态和 Schema，以本轮输入及 SDK 下发的结构化输出合同为准。新 Fork 不代表没有历史；复用 Thread 也不代表自动知道其他 Case 的最新进展。

`case_type` 仍分为 `POLICY_HIT_REVIEW`、`POLICY_RELATED_EVALUATION`、`OPEN_EVENT_EVALUATION`。附加标签只提示已知情况，不增加工具权限、不触发额外研究阶段，也不直接决定能否交易。本轮编排优化没有重新定义这些业务分支。

### Policy 的特别变化

- HIT、RELATED、有效 WATCH 明确关联的 Policy，按需内联完整正文及对应使用状态；已完整交付的对象可能标记 `provided_inline=true`，不要求重复读取。
- `policy_usage_scope=ASSOCIATED_ONLY` 表示内联状态只覆盖关联对象，**不表示其他 Policy 都可执行，也不表示全库没有其他机会**。
- Agent 仍可自主发现其他 Policy；通过 MCP 读取 Policy 时，可同时取得该对象的冻结使用状态 `frozen_usage`。状态未知时不能自行解释为“可执行”。

## 3. 用四个工具获取可选资料

工具属于 `w3_research` MCP，实际调用名称以 SDK 暴露为准。下表和示例使用其工具短名；每次都要显式传本轮 `context_ref`。

| 工具 | 什么时候用 | 怎么用 |
| --- | --- | --- |
| `read` | 已知道要读哪个对象 | 直接传 `objects`；不要为了走固定流程先 catalog/search 一遍。 |
| `catalog` | 不清楚有哪些相关对象 | 可用 `kind`、`shell` 缩小目录，取得业务名称后再读。 |
| `search` | 需要按主题或关键词发现材料 | 传 `query`，可按 `kind` 限定；搜索片段用于定位，不自动替代必要正文。 |
| `market_view` | 需要判断机会是否已被市场消化 | 一次读取统一行情视图，见下一节。 |

主要对象种类：D1 章节 `d1_section`、D2 Unit `d2_unit`、Policy `policy`、Reference `reference/reference_event`、历史意图 `trade_intent`、历史消息 `historical_message`、运行状态 `runtime_state`、使用状态 `policy_usage`。具体 ID 以当前直接入口或目录为准。

**D2 Unit 的 ID 就是已有 Unit 名称**；同名时加 `shell` 区分。不为 Factor、State 猜另一套对象 ID。可按业务问题选择 `parts`：`state`、`baseline`、`factors`、`gaps`；省略则读取完整业务内容。

例如，调用 `read` 的参数：

```json
{
  "context_ref": "本轮提供的引用",
  "objects": [
    {
      "kind": "d2_unit",
      "id": "已有Unit名称",
      "shell": "所属Shell名称",
      "parts": ["state", "baseline"]
    }
  ]
}
```

允许一次读取多个对象，但整个批次共用输出预算，并非每个对象单独享有一页容量。仍可跨 Unit、跨 Shell、自主研究未预关联的对象；`direct_objects` 是便捷入口，不是访问白名单。

### 阅读格式和完整性

默认业务视图保留研究正文，将 `ref` 等引用附属内容分开；不应把正文中没有引用链接理解为“没有来源”。需要追溯时，用同一 `read` 请求加 `view="sources"`；需要 Policy/D2 Unit 原始结构可用 `view="raw"`，完整文档可读 `raw_document`（D1/D2/Policy/Reference 对应 ID 为 `d1/d2/policy/reference`）。

重复表格可能用 `columns/rows/missing` 表达：按列名理解行值，`missing` 记录该行原本不存在的列位置，不能将其与显式 null 混为一谈。

返回的 `body` 是正文字符串片段，单页不保证自身就是完整 JSON。关注 `page.range`、`page.complete`、`next_cursor` 和末尾 `receipt` 中的 END 收据：

1. 决策所需正文尚未交付完整时，保持原对象、view、parts 等参数，携带返回的 `cursor` 续读；按范围顺序理解或拼接正文。
2. 经 Code Mode 调用时，一次只回显一页 text，给外层足够输出预算（如 6000 Token），不要同时打印整个 MCP 返回对象和其中的 text。需要研读的正文应实际交付给模型，不能只回显页数或摘要。
3. 外层截断时，用**该页原请求的 cursor** 降低 `max_bytes` 重取该页；第一页原本无 cursor 就仍不带 cursor。未完整取得的下一页指针不能猜，也不要从头反复读取整份资料。
4. 缺页、PREPARING、读取失败或索引不可用，都是信息边界，不是“该事实不存在”的证据。索引不可用但原件可用时，可以直接读原件。

研究资料与运行状态绑定本阶段的冻结版本。进入运营阶段，默认使用本轮新的 `context_ref`；需要明确回查旧阶段时才使用旧引用，不能把旧 WATCH/PIN 状态当作当前状态。

## 4. 行情改为读取后台结果

调用 `market_view`，只传 `context_ref` 即可得到统一视图：五个完成交易日日线、近 24 小时小时线、近四小时五分钟线。后台目标刷新间隔为 30 秒，Agent 不负责定时器或连接。

- 首次 fine 仍为 `PREPARING` 时，固定数据可以先用，继续其他必要研究；不要每秒重试，也不要绕过接口自行调 IBKR。
- 按各层状态、`coverage`、`observed_at`、`refresh`、缺口和形成中 bar 理解数据；不要把部分可用误认为全部完整、最新。`observed_at` 是行情观察时间，不能替代新闻证据截止时间。
- 再次检查行情时，可传上次的 `since_version`。返回 `unchanged=true` 时复用已有内容；仍要看新的健康/陈旧信息，不能解释为价格已完成重定价。
- 标准窗口覆盖不到较早披露时，可传带时区的 `event_time` 请求异步补充。等待期间保留判断边界，不把“尚无数据”解释为市场尚未定价。
- **行情的业务权限不变：用于机会时效性与已完成重定价判断，不用于产生交易方向、重要性或信心。**

离线回测同样走 MCP，但只返回预先准备的历史文件数据，Web/Broker 不可用；数据集可能只有十分钟线，必须保留其实际粒度，不能当作五分钟线。

## 5. 两个阶段分别交付什么

| 阶段 | Agent 应做的事 | 交付 |
| --- | --- | --- |
| `trade_decision` | 基于当前消息、已有依据和必要补充研究，完成本次交易判断；不先写全套运营结论 | `decision`、`reason`、`policy_trade`、`expert_trade`；决策为 `NO_TRADE/POLICY_TRADE/EXPERT_TRADE`，仅填写对应分支，其余分支为 null。 |
| `post_decision_operations` | 继承第一阶段研究，读取已提交决策 `submitted_decision`、宿主受理结果 `output_disposition` 及刷新的 WATCH/PIN；补必要的事实、覆盖与运营动作 | `novelty`、`policy`、`delta_candidates`、`watch_actions`、`pin_actions`，严格服从本轮 Schema。 |

第二阶段不重新进行一轮 Alpha 研究，不撤销、改写或补发第一阶段交易，也不在 Policy 交易未被受理后补一个 Expert 交易。Agent 输出结构化建议；实际状态写入、幂等受理、交易提交和资源回收由宿主执行。

## 6. 提示词对齐时应删除或改写的旧操作指令

- 每次任务先重读 AGENTS/Skill/task/schema，或先恢复整个旧 Workspace。
- 先探索研究目录，再用复杂 Shell/Python 命令解析文件；已知对象仍先全库列举。
- 把内联使用状态当成全量 Policy 状态，或把“没有内联”当成禁止自主发现。
- 自行调用 IBKR、分配连接参数、分别拉取各周期行情，或等待行情 ready 后才开始研判。
- 每个阶段从零研究一次，或要求交易前先完成全部 WATCH/PIN/事实运营。

Thread 创建、Fork/Resume、预热库存、研究共享与版本绑定均由编排层完成，Agent 无需操作内部管理接口。保留现有核验深度、Prior/Delta 方法、交易资格与输出业务规则；本说明仅要求把获取信息和执行阶段的方式对齐新入口。旧 Case 未提供 `context_ref` 时，继续遵守其冻结的旧入口，不猜测新工具或混用版本。
