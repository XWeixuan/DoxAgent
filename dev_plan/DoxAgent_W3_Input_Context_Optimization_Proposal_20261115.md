# DoxAgent W3 Input 上下文优化：拟修改项与整体设计逻辑

> 状态：需求汇总 / 待 Codex 核对实施细节；**不是已实施配置**。  
> 依据：2026-11-15《W3生产上下文注入全貌与真实输入》及其替换后的 NBIS RELATED Case 样本，以及本轮逐项确认的修改要求。  
> 范围：W3 **模型可见的输入投影**、Data MCP 对 W3 暴露的研究能力、Trade/Operations 职责与输出契约；不以删除数据库业务字段或重构 Runtime 架构为目标。

## 一、整体修改方向与设计基线

### 1. 核心目的

近期 W3 生产环境出现明显 Badcase，而在去掉部分生产上下文的回测中曾得到 10/10 GOLD。这个现象**提示**上下文可能改变决策倾向，但尚不能单凭回测证明具体根因。本轮目标是：

- **W3 独立研判**：保留当前消息、可比既有事实、必要研究基线；减少 W2 的解释、Policy 预设方向、路由标签等对交易决策的先验引导。
- **阶段职责明确**：Trade 仅做交易研判；Operations 仅做 PIN/WATCH 后置维护。保留 `pins` 在两个阶段均可注入；`watch` 只在 Operations 注入。
- **减少无效输入和工具依赖**：只向模型展示对本轮任务有价值的信息；研究工具和 Web 均按需使用，而非强制先查 D1/D2/Policy。
- **保持现有两阶段线程结构**：公共 AGENTS/Skill 模板继承；Trade 和 Operations 各追加一次阶段性 User JSON，后者沿用本 Case 的前序历史。第二层外壳与第三/四层 `input` 是**父子嵌套关系**，不是同阶段重复注入。


## 二、具体拟修改项

### A. 公共外壳与阶段指令

| 当前 Agent 可见字段 | 修改方式 | 说明 |
| --- | --- | --- |
| `entry_version` | **从模型输入移除** | 版本仍由 Runtime 内部记录/使用。 |
| `contract_version` | **从模型输入移除** | 业务契约和 SDK 结构化输出约束仍在编排层生效。 |
| `context_manifest_sha` | **从模型输入移除** | 后端冻结资料一致性校验不可因此失效。 |
| `direct_objects` | **从模型输入移除** | 后端仍可标记哪些 Policy 已内联；不把 Policy ID 提示送给 Trade Agent。 |
| `instruction` | **语义微调** | 明确 `context_ref` 只是按需访问允许研究资料的入口；根据任务选择 D1/D2、Web 或其他允许工具；**不要求每个 Case 都读取研究工具**。只有实际读取到相关且未完整的分页结果时才沿 `next_cursor` 续读。 |
| `phase`、`output_fields`、`context_ref` | **保留** | `phase` 区分 Trade/Operations；`output_fields` 与阶段 Schema 对齐；`context_ref` 保留可选研究能力。 |

> 只从 **Agent User JSON** 移走以上元数据；不得因字段外显移除而删除 Runtime、日志、审计、工具校验实际依赖的版本或指纹信息。

### B. Trade：业务上下文投影

| 模块 | 具体修改 | 保留/边界 |
| --- | --- | --- |
| `trading_context` | **整个对象不进入 Agent 输入** | 交易语义日、意图过期、持仓退出、候选/交易资格等仍由 Runtime 负责执行与约束；`cutoff_at` 和新闻发布时间依然保留在 Trade 输入。 |
| `case_type` | **移除** | 不再额外用 `POLICY_HIT_REVIEW` / `POLICY_RELATED_EVALUATION` / `OPEN_EVENT_EVALUATION` 给 W3 定义不同任务类型。 |
| `message`、`ticker`、`cutoff_at`、`additional_tags`、`w1` | **暂时保留原结构** | 保持当前新闻和 W1 Event/已知事实证据。 |
| `w2` | 删除 `reason`、`policy_ids`、`related_policy_ids`、`hit_condition_ids`、`matched_condition_ids` | 保留 `policy_hit`、`policy_related`、`confidence`；用新的不含内部 Policy ID 的结构指明实际关联/命中的 Policy 和 Condition（见下）。 |
| `policies[]` | **后端取完整 Policy，向 Agent 只投影必要字段** | 保留 `title`、`transmission`、`activation_conditions[].condition_id`、`criterion`、`calibration.reference_state`；每条新增 `cutoff`。 |
| `policies[]` 内需隐藏 | `policy_id`、`ref`/`refs`（以及同类来源身份指针）、`match_scope`、`decision`（包括 Condition 内）、`condition_revision`、`trigger_layer`、`calibration.trigger_boundary` | 不得误删 DB 或 W2/Runtime 内部数据。注意真实样本的 `decision` 位于 Condition 内。 |
| `watch` | **从 Trade 输入移除** | 保留 Operations 中现有 WATCH 快照及操作能力。 |
| `pins` | **保留原样** | Trade/Operations 都可注入；重复是有意的阶段状态提供。 |
| `recent_trade_intents` | 改名 `recent_trade`；窗口 **72h → 48h** | **仅实际成交的 Case**；每项只保留 `message`、`w3_decision`。有交易意图、已下发/接受订单但未成交的 Case 不入列。 |
| `contexts` | **仅保留 D1、D2** 的 `name`/`cutoff_at` | 删除 PolicySet、Reference View 项。`null` 仍表示未记录截止时间，不是假定实时最新。 |
| `alignment` | **保留原有字段** | 包括 `status`、`error` 及存在时的 `hit_has_new_fact`、`related_has_new_fact`，不补造缺失值。 |

**W2 新指向字段的建议形状（字段名可按现有契约定稿）：**

```json
{
  "policy_hit": false,
  "policy_related": true,
  "confidence": "normal",
  "policy_matches": {
    "hit": [],
    "related": [
      {"policy_title": "新园区接电与有效服务容量爬坡的关键路径", "condition_ids": null},
      {"policy_title": "项目资本排序、预付款保障与售后租回现金责任", "condition_ids": null},
      {"policy_title": "AI云专属容量合同的净新增承诺与采购质量", "condition_ids": null}
    ]
  }
}
```

- **HIT**：现有生产 W2 已有 Condition 级 `hit_condition_ids`（按内部 Policy ID 分组）。组装时在后端把内部 ID 映射为 `policy_title + condition_id`，例如 `{"policy_title":"……","condition_ids":["C1"]}`；给 Agent 的是标题和 Condition ID，**不是 Policy ID**。
- **RELATED**：新审计样本只确认到 Policy 级 `related_policy_ids`，没有证据证明本例存在 Condition 级 RELATED 关联。先如实表达 Policy 级关联，`condition_ids:null` 表示**未提供 Condition 级指向**；若生产 W2 实际有可靠的 RELATED Condition 结果，再原样投影其真实 ID。**不可由 W3/组装代码猜测或补造匹配 Condition**。
- **映射约束**：同个 Policy 内 `C1/C2` 可能重用，必须与 `policy_title` 联合引用；后端仍用稳定 `policy_id` 定位，并校验同一次输入中的标题不歧义。此结构仅是对既有匹配信息的展示裁剪，不让 W3 重判 W2 命中。

**Policy `cutoff` 语义：**使用对应 Policy 真正生成或最后维护的时间（以最新有效维护时间为准）；不使用消息总线 `cutoff_at`、PolicySet 级时间或请求当下时间替代。源记录缺失时需显式标识未知，不得伪造。

**`recent_trade` 成交口径：**使用权威成交回执/对账的实际成交数量判定，部分成交也属于实际成交；`EXECUTION_ACCEPTED`、`intent_released=true` 或仅产生方向不等于成交。48h 过滤保持时点一致，不可注入 Case 判断时点之后的未来成交信息。`w3_decision` 内仍保留已有四个决策字段。

### C. Operations：PIN/WATCH 专属后处理

| 当前字段/能力 | 修改方式 | 说明 |
| --- | --- | --- |
| `submitted_decision` | **保留** | 接收 Trade 已提交的结论，不重新做交易方向判断。 |
| `output_disposition` | **保留** | 区分 W3 的决策与 Runtime 实际处置。 |
| `candidate_eligibility` | **从 Agent 输入移除** | Candidate 资格/处置仍由 Runtime 内部管理。 |
| `watch`、`pins` | **保留** | Operations 阶段提供当前有效状态，允许相对 Trade 快照刷新。 |
| Policy ID | **只允许在 Operations 注入必要的 Policy ID 映射** | 支持 WATCH 绑定；建议提供与 Trade 所见 `title` 相对应的精简 `policy_targets`，并保留既有 WATCH 项目中的 `policy_id`。不要重新注入完整 Policy。 |
| `allowed_reference_event_ids` | **移除** | 不再负责 OLD/Event 引用合法性选择。 |
| `reference_rule` | **移除** | 不再负责 Novelty/事实覆盖判定。 |
| 输出 `delta_candidates`、`novelty` | **从 Operations 输出 Schema 移除** | W3 后置阶段不再产出新增 Fact 候选、NEW/OLD/Novelty 判断或相应 reasoning；不承担 W1/W2 命中复核。 |
| 输出 `pin_actions`、`watch_actions` | **仅保留这两个输出** | 同步修改 SDK Schema、Runtime 解析、校验、持久化及 AGENTS/Skill 中对应阶段说明。 |

> Operations 的 Policy 身份例外是**阶段专属能力**，不构成允许 Policy ID 回流 Trade 的理由；Runtime 按 `policy_id` 完成 WATCH 创建/更新/失效的实际持久化。

### D. Data MCP / 研究对象可见性

1. **W3 可见研究目录中移除** Policy 查询、PolicySet 和 Reference View 相关查询/发现能力，并同步裁剪 `contexts` 目录。W2/Runtime 后端仍可使用完整 PolicySet，不改变 W2 业务能力。
2. 保留按需读取 D1/D2 的能力，以及原本允许的 Web Search、行情或其他必要工具；不要因取消某种 Data MCP 对象误删无关工具。
3. 同步处理 `catalog` / `search` / `read` 的可见对象、权限与返回说明，避免 Agent 仍从工具描述或另一查询路径读取已要求隐藏的 Policy 内容。
4. 保留正常 `context_ref` 研究绑定及后端 manifest，不要求 Agent 每个 Case 都查询。工具结果只在实际调用后进入线程，不把整个冻结资产预先视为输入。

### E. 第一层与输出契约同步

- 公共 AGENTS/Skill 的研究、独立判断和时间意识核心思路保持；**只对旧字段引用、固定研究必经措辞和 Operations 职责相关条款做必要的局部修改**，不全局重写。
- Trade 仍返回四字段 `decision / expectation_delta / prior_expectation / reason`。
- Operations **只返回** `pin_actions / watch_actions`；彻底移除不再使用的 Schema 属性与下游依赖，而非单纯让模型返回空数组。
- `entry_version`、`contract_version`、`context_manifest_sha`、`trading_context`、`direct_objects`、`candidate_eligibility` 虽从 Agent 输入消失，后端仍按需要保存和消费其内部业务/审计数据。

## 三、修改后的预期输入形状（示意，不是完整生产 JSON）

### 1. Trade

```json
{
  "phase": "trade_decision",
  "input": {
    "ticker": "NBIS",
    "cutoff_at": "2026-11-14T04:20:14.625556+00:00",
    "additional_tags": [],
    "message": {"title": "NBIS Q3 Earnings Call Highlights Expansion Plans and Funding", "body": "……", "source_name": "news.google.com", "published_at": "2026-11-14T04:18:49Z", "url": "……"},
    "w1": {"result": "NEW", "confidence": "normal", "reason": "……", "events": ["……"]},
    "w2": {
      "policy_hit": false,
      "policy_related": true,
      "confidence": "normal",
      "policy_matches": {
        "hit": [],
        "related": [
          {"policy_title": "新园区接电与有效服务容量爬坡的关键路径", "condition_ids": null},
          {"policy_title": "项目资本排序、预付款保障与售后租回现金责任", "condition_ids": null},
          {"policy_title": "AI云专属容量合同的净新增承诺与采购质量", "condition_ids": null}
        ]
      }
    },
    "policies": [{
      "title": "AI云专属容量合同的净新增承诺与采购质量",
      "cutoff": null,
      "transmission": "……",
      "activation_conditions": [{
        "condition_id": "C1",
        "criterion": "……",
        "calibration": {"reference_state": "……"}
      }]
    }],
    "pins": {"limit": 2, "used": 2, "items": ["……"]},
    "recent_trade": [],
    "contexts": [
      {"name": "D1", "cutoff_at": null},
      {"name": "D2", "cutoff_at": "2026-10-17T11:05:48.453869+00:00"}
    ],
    "alignment": {"status": "COMPLETE", "error": null, "hit_has_new_fact": false, "related_has_new_fact": true}
  },
  "context_ref": "w3ctx_……",
  "instruction": "Use context_ref only when task-relevant frozen research is needed; choose research/Web tools as appropriate. Continue paginated results only if relevant and incomplete.",
  "output_fields": ["decision", "expectation_delta", "prior_expectation", "reason"]
}
```

说明：此处只示意一条 Policy/Condition，真实 Case 按相关集合内联完整的**裁剪后** Policy；`cutoff:null` 是尚无真实单条 Policy 更新时间时的示意，不代表新系统应该永远为空；`recent_trade:[]` 仅示意本样本没有可由现有回执证实成交的历史，不代表生产总是空。字符串 `"……"` 均为省略占位，不可作为可解析业务对象直接使用。

### 2. Operations

```json
{
  "phase": "post_decision_operations",
  "input": {
    "submitted_decision": {
      "decision": "NO_TRADE",
      "expectation_delta": "……",
      "prior_expectation": "……",
      "reason": "……"
    },
    "output_disposition": "NO_TRADE",
    "watch": {"limit": 2, "used": 2, "items": ["……"]},
    "pins": {"limit": 2, "used": 2, "items": ["……"]},
    "policy_targets": [
      {"policy_id": "pol_90d7c24fb5d048b8be7bed29ec918066", "title": "AI云专属容量合同的净新增承诺与采购质量"}
    ]
  },
  "context_ref": "w3ctx_……",
  "instruction": "Maintain PIN and WATCH only. Use optional research tools when needed.",
  "output_fields": ["pin_actions", "watch_actions"]
}
```

Operations 仍可继承本 Case 的 Trade 线程历史；`policy_targets` 仅为 WATCH 绑定提供身份映射，不恢复 Trade 阶段的 Policy ID 外显。`output_fields` 是顶层字段名，完整属性类型和规则仍由 SDK Schema 约束。

## 四、实施前需校验的少量关键边界

1. **HIT 与 RELATED 的真实粒度**：按生产 W2 契约确认 HIT 有 Condition 指向；RELATED 当前样本仅能证明 Policy 级指向。若 RELATED 未提供 Condition 匹配，不得自造 `C1/C2`。同名 Policy 要有确定的内部映射办法。
3. **职责移交**：Novelty/Fact 候选及 Event 引用规则从 W3 Operations 移除后，核实新编排已经有明确负责节点，所有旧输出消费者同步修改。
4. **成交历史的真实性**：以实际成交/部分成交作准，避免把订单已接受误当成交，也避免因回执延迟误认为没有成交；48h 过滤不越过 Case 的历史判断时点。
5. **去除 Agent 字段不影响硬约束**：交易资格、意图过期、退出时间、候选处置、版本和 manifest 校验等仍由 Runtime 执行；`trading_context` 等字段从 Agent 删除不代表这些约束被删除。

## 五、最终收敛

这是一轮**输入投影与职责边界的精益优化**，而不是数据库精简、Policy 业务逻辑重写或 W3 Prompt 全面重做：

- **编排层保留完整信息和控制权**：全量 Policy、身份、命中细节、时间与执行状态在后台仍可用。
- **Trade 只看到支撑独立判断的事实与研究基线**：删去上游详细判断理由、预设方向、路由标签与无关运行元数据；保留 W2 基本状态及准确的标题＋Condition 指向、W1、Policy 研究正文、`alignment`、PIN、48h 已成交历史。
- **Operations 专注 PIN/WATCH**：仅阶段性给出 WATCH 和必要 Policy ID 映射，移除 Event 白名单、Novelty/Fact 输出及相关冗余说明。
- **研究按需调用**：只保留 W3 需要的研究接口，取消从 Data MCP 再查 PolicySet/Reference View/Policy 的路径；保留 D1/D2、Web 等必要能力。

**设计原则：后台信息完整，模型外显克制；减少判断锚定，不牺牲事实依据；只调整必要的字段、Schema 与调用边界，保持精益轻量。**
