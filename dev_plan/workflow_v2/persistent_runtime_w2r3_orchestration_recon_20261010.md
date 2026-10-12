# 持久化运行 W2-R3 编排调整：代码勘察发现与待决问题

日期：2026-10-10。范围：本地代码、提示词、已有审查文件、定向离线测试。

## 1. 本轮边界与结论

本轮完成勘察和一轮集中问答准备，未实施业务代码、提示词、数据库或部署修改。开发方案在用户回答本文件第 11 节后形成。工作区原有修改和未跟踪文件均保留；用户新增的 `w2_r3.md` 未修改。

检查基线：分支 `codex/v1-retirement-cleanup`，HEAD `f91dbbc5`。这不是干净工作区。本文描述本地实现，不将其当作远端部署证明。

**新编排可以沿用现有框架实现，无须增加调度平台或业务阻塞校验；但不能仅新增一个模型轮次并修改 Router。** 必须一起覆盖结果合同、冻结执行包、恢复重放、W3 输入与复核语义、Policy 消费入口、读模型和前端合同。核心发现：

1. 当前 `policy_ids` 就是 HIT 标识，`matched_condition_ids` 也是 HIT 条件归因；没有“相关”结果。
2. 当前正常置信度 `NEW + HIT` 直接产生交易；正常 `OLD + HIT` 归档并写 BADCASE。低置信度仍先到 W3。
3. 当前传输层保存本地父响应关系，但不向百炼发送 `previous_response_id`。它只能引用一个父响应，不能直接合并 W1、W2 两条独立上下文链。
4. W1 reason 只解释决定消息级 NEW 的核心差异，不保证覆盖全文全部新事实。R3 FALSE 可以表示“没有被该理由证明为新”，不能据此证明全文 OLD。
5. 现有 W3 对 `NEW + Policy HIT` 只做新旧/Policy 复核后按 Policy 方向交易，其 schema 明确禁止该分支执行专家可交易性研判。
6. Policy 自动冻结实际由多处消费记录过滤和原子认领实现。只关闭最终写入仍会继续排除历史已消费 Policy。
7. 前端已有 R1/R2/R3 轮次基础结构；缺的是新的判定、语义标签、R3 结果明细和正确的路由投影。

## 2. 当前端到端执行路径

### 2.1 模型轮次与快路径

- `service.py:308` 创建 Case，冻结消息、已知事实/Policy 视图、提示词和模型配置。
- REALTIME 在 `service.py:495` 并行运行 W1、W2，各自先 R1 召回，再 R2 判定；等待两者完成后在 `:504` 路由。
- W1-R2 显式收到原文和召回到的 Event Detail；W2-R2 显式收到原文和召回到的 Policy Detail。父 R1 response ID 记在轮次记录中。
- W2 的末次 response ID 当前在汇合处被 `_` 丢弃。接入 W2-R3 时需保留它；W1 的末次 ID 已保留。
- W2-R1 没有候选时跳过 R2。新增 R3 应在 W1/W2 汇合后、持久化路由和副作用前运行，不能作为和交易意图并行的异步副作用。
- W1-R3 是事实抽取轮次，不是常规 W1 快路径的必经轮次。当前 REALTIME 路由 W3 时由 W3 负责 Delta；其他需要 EMIT_DELTA 的路径执行 W1-R3。
- CLOSED 扫描保留两阶段：W1 wave 后再执行 W2/ALL；W1 阶段可先执行 W1-R3。正常置信度 OLD 在现有 CLOSED 路径跳过 W2。本次若维持旧机制，不应顺便扩大 CLOSED 扫描范围。

### 2.2 当前路由表

以下 HIT 当前等于 `bool(w2.policy_ids)`，均指一轮判定。任一 confidence=LOW 优先进入 W3。

| W1 | W2 | 正常置信度主路由 | 当前主要副作用 |
|---|---|---|---|
| NEW | HIT | TRADE | 创建交易记录/意图；EMIT_DELTA |
| NEW | 无 HIT | W3 | W3 研判并负责后续 Delta |
| OLD | HIT | ARCHIVE | 归档；MARK_BADCASE |
| OLD | 无 HIT | ARCHIVE | 归档 |

证据：`router.py:17-67`。`route_w3_result(:70)` 和 `service.py:_apply_w3_result(:1303)` 也会为 W3 最终 OLD+HIT 写 BADCASE，不能只删一轮 Router 的分支。

### 2.3 编排与恢复约束

- `RuntimeModelTurn` 已允许 W1/W2、R1/R2/R3，并保存父响应、输出、原始响应、token、attempt；不需要为 W2-R3 新建独立轮次存储体系。
- `_call_with_retry(service.py:775)` 复用已成功轮次，冻结 round input、instructions、schema hash 和父 ID；显式 resume 会保留成功的上游轮次。
- 当前 R3 动态提示词处理在 `service.py:807` 使用 W1-R3 的长度/后缀逻辑。新增 W2-R3 必须按 lane 区分，避免 W1 专属处理落到 W2。
- 当前默认每轮最多两次尝试、请求超时 120 秒、重试延迟 5 秒。新增串行 R3 增加一个失败面和约一轮请求延迟；不能通过静态勘察承诺全链路失败率。
- 任务 heartbeat 在模型请求期间续租；无需新增等待锁、前置一致性门槛或全局互斥。
- `_enqueue_route_effects(:996)` 已有 Case、效果、W3 任务的事务持久化；复用它即可。失败继续隔离到 Case、记录错误，不阻塞健康消息。

## 3. W2 输出与 Policy 召回合同

### 3.1 现有数据结构

`schema.py:337-368`：

```text
W2MatchedConditions = { policy_id, condition_ids[] }
W2PolicyResult = { policy_ids[], matched_condition_ids[], confidence, reason }
```

- `policy_ids` 最多 3 个，当前表示 HIT。
- Condition 归因为辅助信息，可空；现有清洗会去掉不在 `policy_ids` 内的归因。
- 新增“相关”以后，不能再用上述清洗规则删除相关 Policy 的归因。
- W2-R2 现有提示词只判断触发，未触发返回空 Policy；需改为分别解释 HIT 的触发依据和 RELATED 的相关观察事实。
- W2 reason 当前最长 1000 字符，没有结构化的每条件事实依据。W2-R3 继承上下文可以读取原文、Policy Detail、R2 输出，但其对多个条件的依据识别仍依赖提示词质量。
- Document3 的 Condition ID 只要求在本 Policy 内唯一（`workflows/codex_document3/schema.py:207`）。多个 Policy 同为 C1 合法；R3 只返回裸 C1 会歧义。

### 3.2 可直接确定的最小改法

以下是方案建议中的默认决策，不需要为字段拼写单独问答：

- 新必填字段采用 JSON 原生布尔值，例如 `policy_hit`、`policy_related`；不使用字符串 `"True"`、`"False"`。
- 保留 `policy_ids` 的 HIT 身份含义；`hit_condition_ids` 保存 HIT 的分 Policy 条件归因；`matched_condition_ids` 保存 RELATED 的分 Policy 条件归因。后者已有 `policy_id`，无需为相关再增加一套 Policy ID 列表。相关的条件列表可空。
- 不再由 `bool(policy_ids)` 推导两类新判定；不能把相关 Policy 当成命中 Policy 计数或生成 Policy 方向交易。
- 两个布尔值可以同时为 TRUE。按用户定义，同一 Policy 有任一 Condition HIT 时，该 Policy 归 HIT；同消息的其他 Policy 可归 RELATED。不新增阻止两个布尔同时为 TRUE 的校验。
- 两个归因列表均可空；不添加“TRUE 必须有非空列表”、结果必须覆盖全部条件、交叉字段一致性等业务阻塞校验。
- R3 结果采用最小列表包装，每项只包含 `condition_ids` 和布尔值；Condition 引用使用 Policy 限定形式，例如 `policy_A/C1`，解决跨 Policy 同名问题，无需增加第三个业务字段。不输出 reason/confidence。
- 空复核列表不构成“有任一 TRUE”的证据，不因列表空而阻塞 Case；原 LOW→W3 机制优先保留。具体混合分支见问题 Q4。

### 3.3 R1 的实质冲突

`w2_r1.md` 目前排除明确未到 trigger boundary、没有合理最终 HIT 可能的消息。“相关”却恰好需要保留 match_scope 内但未触发的消息。若 R1 提示词也必须原样维持，很多 RELATED 永远到不了 R2，R3 无法补救。这是召回口径冲突，不是增加一个布尔值能解决的问题。见 Q2。

## 4. R3 提示词、上下文继承与可证明范围

### 4.1 新提示词尚未接入

用户新增 `prompts/persistent_runtime_v2/w2_r3.md` 当前为未跟踪文件。`prompts.py:10-34` 和 `execution_bundle.py:49-53` 仍只包含 core、W1 三轮、W2 两轮；仅将 md 放进目录不会进入新执行包。

新提示词有三处需要同步：

1. 开头和任务定义只说 HIT/Condition 成立，需要覆盖 RELATED 的“相关观察事实”，不能把未触发误读成已经成立。
2. 末段要求输出 reason，与极简输出需求冲突。以用户本轮极简 schema 为准删去 reason 要求。
3. “不要重新判断消息是否 NEW”可以保留为模型任务边界，但 Router 所谓“重判新旧”需要定义为 Policy 依据层面的有效新旧，不能不加区别地覆盖原始 W1 verdict。见 Q3。

### 4.2 当前传输和继承限制

`transport.py:122-144` 构造实际请求，`previous_response_id` 在 `:138` 明确只保留本地 lineage，不发送；`store=True` 并不自动接续下一轮。

上一轮已用当前百炼 `qwen3.8-flash` 做过两请求最小实测：第二次仅发送问题和第一次 response ID，正确复述只在第一次输入的随机标识。该证据证明当前配置的**即时单父响应继承可用**；未验证长时间保留、进程重启后恢复、两条独立链合并或完整业务稳定性。本轮未再次调用提供方。

SDK `previous_response_id` 为单个字符串。需求字面“W1-R2 和 W2-R3、W2-R2 和 W2-R3 间继承”不能通过两个父 ID 完成。建议链为：

```text
W1-R2 ── previous_response_id ──> W1-R3（不再发送原文/W1结果业务包）
W2-R2 ── previous_response_id ──> W2-R3（新增输入仅为 W1-R2 reason）
```

W2-R3 的原文、Policy、R2 判断来自 W2-R2 父上下文。R1/R2 的当前显式业务输入和不向提供方发送父 ID 的行为保持。发送继承参数必须按目标轮次启用，不能全局放开后意外改变所有 R1→R2 请求。

每轮仍发送本轮 core/round instructions 和输出 schema；顶层 instructions 不应假定由父响应继承。W1-R3 提示词也需改为从继承上下文识别当前消息，防止把父上下文中的既有 Event Detail 当成待抽取的新原文。

### 4.3 W1 reason 不是完整的新事实集合

`w1_r2.md` 要求至少一个未覆盖核心事实即可 NEW，reason 简述决定性差异。`schema.py:266-289` 无完整 NEW facts 列表；fact_attributions 是已知事实归因。

已有同 20 条审查文件 `eval/persistent_runtime_v2/new_provisional_audit_20261010/w1_reason_reassessment.md` 显示：14 条理由可定位核心新事实、5 条部分定位、1 条无法作为事实级依据。这不是线上总体准确率。本次唯一 W2 HIT 样本 #5 的机架功率上修事实未被 W1 reason 点名。因此严格 reason 对齐会过滤部分未被理由证明、但实际上可能为新的事实。

R3 FALSE 的含义应明确区分：事实确为旧；或 reason 未能证明它属于 NEW。两者在本次二值输出里无法进一步区分，可以共同进入复核路径，但不能宣称已证实全文 OLD。

另一个现有约束是 W1 OLD 必须携带已知 Event reference。把 W1 NEW 原地改成 OLD、却没有 E# 依据，会撞上现有 schema，且丢失文章其他新事实。建议保存原始 W1，单独保存 R3 对 Policy 依据的对齐结果及路由标签。

## 5. W3 的真实权责与新增输入

### 5.1 当前二轮并非所有分支都做专家交易判断

- `service.py:1027` 在正常 NEW 且无命中时选择 `UNCOVERED_NEW`，其他选择 `REVALIDATE_THEN_EVALUATE`。
- Mode 1 必须保持 NEW+无 Policy（`w3.py:515`），不能把需推翻一轮判断的疑似重复命中塞进去。
- `schema.py:672-691`：OLD 禁止 Delta/专家交易判断；NEW+Policy 禁止 expert_trade.evaluated；NEW+无 Policy 必须 expert_trade.evaluated；NEW 必须产生 Delta。
- `service.py:1371` 对 W3 最终 NEW+HIT 按 Policy decision 生成交易；专家交易判断用于 NEW+无 Policy 分支。

所以“新增 W3 二轮研判”有两种开发范围：复用现有新旧+Condition 复核后按 Policy 交易；或者增加 Policy HIT 分支的独立交易否决权。后者需要改变 W3 schema、提示词、落地方向/输出规则，不能只调整入口路由。见 Q5。

### 5.2 本次必须带入的证据

`w3.py:392` 当前输入仅原始 W1/W2、消息、route_reason，没有 W2-R3。需要同时传入 R3 条件结果和一轮语义标签，让 W3 对齐“哪一个 Condition 的哪项观察事实被证明/未被证明为新”。否则只是延后当前同 fact 漏洞。

疑似重复命中需要可重新核验的模式；相关事件没有命中时仍允许沿用原 NEW+无 Policy 的专家判断机制。W3 最终 OLD+HIT 也停止创建新 BADCASE，正常归档并保留判定证据。

## 6. 路由语义、标签与 Delta

建议复用物理主路由 W3 和现有 Case 生命周期状态，新增的是可投影、可筛选的一轮语义标签，不另建三套执行器或生命周期。

| 一轮证据 | 需求中的语义 | 物理去向 |
|---|---|---|
| NEW + HIT + 至少一个 HIT Condition R3 TRUE | 一轮判定交易 | W3；不得直接产意图 |
| NEW + HIT + HIT 条件均未通过 | 疑似重复命中 | W3；不再 BADCASE |
| OLD + HIT | 疑似重复命中 | W3；不再 BADCASE |
| NEW + RELATED + 至少一个 RELATED Condition R3 TRUE | policy相关事件 | W3 |
| 任一一轮 confidence=LOW | 延续低置信度复核 | W3 |
| 原 NEW + 无 HIT/RELATED | 现有未覆盖新事件分支 | W3 |
| 相关复核无 TRUE，且无 HIT、正常置信度 | 需确定有效新旧口径 | 见 Q3/Q4；不添加失败门槛 |

- “相关”不产生 HIT 权限；不能用 RELATED 条件的 TRUE 给 HIT 条件放行。
- 同 Case 多标签只运行一次 W3，不因为同时 HIT/RELATED 重复创建任务。
- 一轮标签保留历史，W3 最终推翻时不抹去；一轮筛选和最终结果筛选分开。
- 前端按需求增加两个节点；“疑似重复命中”可做 Case 标签/筛选，不额外增加用户未要求的第三个图节点。
- 保留原始 W1 的事实捕获权责。若某消息仅 Policy 依据复核未通过而走归档，文章其余 NEW facts 不应随之丢失；可复用已有 EMIT_DELTA/W1-R3。原始 W1 和有效 Policy 新旧须分开，具体口径待 Q3。
- 原有低置信度分支优先，不能把低置信度候选显示成确定“一轮判定交易”。

## 7. 暂停 Policy 自动冻结的完整范围

自动冻结依赖 `(ticker, policy_id, activation_revision)` 的消费 ledger，不等同于修改 Policy lifecycle 为 RETIRED。

| 位置 | 当前行为 | 暂停冻结时的处理范围 |
|---|---|---|
| `service.py:359,724,738` | W2 前后过滤已消费 Policy | 未来执行停止按消费记录排除 |
| `service.py:1426` | 非 journal 路径抢占 Policy activation | 停止以一次性消费决定是否交易 |
| `w3.py:132,205` | 包括缓存重用时过滤已消费 Policy | 同步停用 |
| `trade_output.py:161-212` | 意图落地事务中认领/检查消费；重复返回 DUPLICATE_POLICY | 同步停用；覆盖 CLOSED 最终候选释放 |
| `repository.py:663` | 消费 ledger 的唯一键/历史数据 | 保留历史，不删除 |
| `v2_read/projectors.py:304`、`v2_read/lifecycle.py:86,168` | consumed 导致 effective=false | 同步调整有效性投影，历史 consumed 可保留为事实 |

推荐默认：历史已消费 Policy 也恢复参与未来执行，前提仍是正式 ACTIVE/未到期等原有有效条件；否则“关闭冻结”后存量仍被冻住。保留历史消费审计；不删除 ledger，不批量重写旧交易。

保留独立的意图幂等、Case 幂等、控制/lease/fencing、到期限制、专家事件交易去重。`trade_output.py:22` 的 event_trade_key 与一次性 Policy 消费是不同机制，不应一起关闭。

## 8. 前端、读模型和历史兼容

### 8.1 图和筛选不是只改 JSX

- `frontend/v2/src/pages/runtime.tsx:38,483,639`：节点名、筛选项、位置、颜色、路径。
- `src/doxagent/v2_read/graph.py:8`：节点、结果集合、Case 成员和边生成。
- `api_v2/graph.py`：图计数、点击筛选和 SSE 接口。
- `api_v2/runtime.py:75`、`v2_read/repository.py:663`：结果过滤白名单及查询。
- `v2_read/projectors.py:532`：一轮与最终判定投影。目前 W3 未结束时最终判定 unavailable，最终 W3 会覆盖 W1/W2；新增标签不能只从最终结果临时推断。

新增节点应在已完成一轮分类时可见，而不是等 W3 attempt 已产生才计数。边需要表达 W1/W2→语义节点→W3，并保留其他既有直达 W3 分支。两个节点位于 W3 上下，通用“向右连线”算法不适用于同列上下边，需要明确走线，避免穿过无关节点。计数、筛选成员和边必须一致。

移除 BADCASE 节点及新筛选入口，但历史 BADCASE 存储和旧 Case 证据保留。历史直接交易不得伪造成经历过 W3；不批量重跑旧消息或补发意图。

### 8.2 研判明细与 W1-R3

- `api_v2/case_detail.py:75,223,284`：证据读取及 W2 hit 展示，目前未暴露 W1 extraction、W2-R3、新标签。
- `api_v2/case_evidence.py:98` 已允许 R3 轮次；`runtime-case.tsx:304` 已有通用 attempt 表。因此“新增 W1-R3 查看”需补抽取结果、轮次执行/未执行原因和可读关联，不能只再加一个 R3 名称。
- `runtime-case.tsx:55,104`：分别展示 W1 最终判定、W1-R3 抽取、W2 HIT/RELATED、W2-R3 条件对齐；保留原始理由。
- `runtime-case-export.ts` 也需更新，否则导出内容和页面不一致。
- `v2_read/runtime.py:17` 和 `case_evidence.py:79` 当前将全部 R3 排除出 hot-path。W2-R3 新增到热路径，W1-R3 仍为事实捕获，耗时口径需要按 lane 区分。通用 turn token 记录可复用。

### 8.3 合同与旧包

规范合同位于 `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`：ResultKind、NodeId、W1/W2/CaseDetail 等需同步。RuntimeRound 本身已有 R3。

生成文件分别由 `frontend/v2/scripts/generate-schema.mjs` 和 `scripts/generate_v2_schema.cjs` 产生；不能只手改一侧 wire-schema。

后台同一个 W2 模型嵌套在旧 Case、TradeRecord、W3RouteCase 中；新字段必填会使旧 JSON 读入/成功轮次重用失败。建议区分新模型输出的必填合同与存量读取兼容：旧 `matched_condition_ids` 按旧 HIT 归因解释，旧记录不捏造 RELATED 或 R3 结果。已有成功旧轮次、冻结输入不能因发布新 schema 自动失效。

`execution_bundle.py`、`prompts.py`、`factory.py` 需接入新 prompt 并明确旧 bundle 缺少它时的兼容路径；发布后新 Case 使用新包，已运行 Case 保持可解释的冻结合同。不以本次需求为由全面重构执行包或增加强制全库迁移。

BADCASE 还进入日维护汇总、O3 feedback 和历史计数（`maintenance.py`、`bounded_inputs.py`、`daily.py`）。停止新建及取消运行图节点即可；无需删除历史反馈或扩展成 O3 重构。

## 9. 稳定性评估及验证范围

### 可从代码确认

- 框架具备持久化轮次、有限重试、成功结果复用、事务效果、独立 W3 派发、CLOSED 失败隔离基础。
- W2-R3 放在汇合后即可串行运行，不需要改变 W1/W2 并行结构。
- 当前 W3 按 Case 启动线程，`repository.py:1236` 的 slot 获取没有实际执行所传 max_concurrency 硬上限。本次将原直接 HIT 和 OLD+HIT 转入 W3，会增加 Worker 调用和排队负担。
- 新增串行 R3、取消请求业务包后对父响应的依赖、旧 schema/冻结包兼容、W3 负载是具体风险点。不建议为了这些风险新增业务阻塞门槛；应针对恢复、负载、读合同做验证。

### 本轮已经执行

```powershell
uv run pytest tests/test_persistent_runtime_v2.py::test_router_matches_frozen_sixteen_row_matrix tests/test_persistent_runtime_v2.py::test_any_policy_is_consumed_once_and_removed_from_next_w2_input tests/test_persistent_runtime_w3.py::test_published_w3_context_filters_consumed_policy_even_when_heavy_context_is_cached tests/test_runtime_orchestration_foundation.py::test_hot_bundle_keeps_old_contents_after_new_publication tests/test_runtime_orchestration_execution.py::test_incomplete_round_reuses_pinned_inputs_and_successful_outputs -q
```

**结果：20 passed，3.30s；进程最终退出码 0。** 16 行旧 Router 参数化加 4 个消费/缓存/冻结包/恢复用例。测试使用假模型，只证明当前基线和被审查约束存在，不能证明尚未实施的新编排正确。本轮未做新编排真实模型测试、浏览器验收、生产负载测试或部署。

### 后续验证应覆盖的具体边界

1. 新路由矩阵：HIT/RELATED 同时 TRUE、跨 Policy 重名 C1、HIT FALSE/RELATED TRUE、无归因、全 FALSE、LOW、W2 无候选。
2. 新输出真实请求：严格布尔/schema、R3 单父继承、只发送 W1 reason、W1-R3 无业务包、当前轮 instructions 有效。
3. 故障/恢复：R3 失败后 Case 隔离、恢复不重跑成功父轮、旧 schema/bundle 成功轮次复用、父 ID 不可用的实际错误处理。
4. 冻结关闭：W2/W3 均能重新看到历史已消费有效 Policy；REALTIME/CLOSED 意图输出一致；独立幂等仍有效。
5. W3：同一触发事实的前后事实对照，不能只以文章整体 NEW 放行；原始 NEW facts 的 Delta 不丢失。
6. 读模型/浏览器：新节点数量及成员、筛选、旧 Case、明细、W1/W2-R3、导出、热路径耗时；遵循前端 AGENTS 要求的 1262/1559 宽度实看和连线验收。

## 10. 相关文件清单

均相对于仓库根目录，以上章节给出了重点定位。

| 模块 | 文件 |
|---|---|
| 核心合同/请求/路由 | `src/doxagent/persistent_runtime_v2/{schema,transport,router,service}.py` |
| 提示词/执行包/模型固定 | `prompts/persistent_runtime_v2/{w1_r2,w1_r3,w2_r1,w2_r2,w2_r3}.md`；`persistent_runtime_v2/{prompts,execution_bundle,factory}.py` |
| W3/Policy/交易 | `persistent_runtime_v2/{w3,trade_output,repository,selection}.py`；`workflows/codex_document3/{schema,runtime_projection}.py` |
| 扫描/恢复/维护 | `persistent_runtime_v2/{coordinator,heartbeat,operations,maintenance,bounded_inputs,daily}.py` |
| 读模型 | `src/doxagent/v2_read/{graph,projectors,repository,lifecycle,runtime,policy_hits}.py` |
| API | `src/doxagent/api_v2/{graph,runtime,case_detail,case_evidence,wire_schema.json}` |
| 前端 | `frontend/v2/src/pages/{runtime,runtime-case}.tsx`；`frontend/v2/src/core/{runtime-case-export.ts,wire-schema.json}` |
| 共享合同/生成 | `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`；`frontend/v2/scripts/generate-schema.mjs`；`scripts/generate_v2_schema.cjs` |
| 测试 | `tests/test_persistent_runtime_v2.py`、`test_persistent_runtime_w3.py`、`test_runtime_orchestration_{foundation,execution,recovery}.py`、`tests/v2_backend/`、`frontend/v2/tests/`、`frontend/v2/tests/browser/` |

## 11. 本轮一次性待决问题

下列问题决定开发范围或事实语义。字段命名、历史保留、无新增阻塞校验、保留 confidence、限定 Condition 引用等细节已在上文给出默认决策，不重复征询。

### Q1：是否确认为两条各自的 R2→R3 继承链？

需求字面写 W1-R2→W2-R3 和 W2-R2→W2-R3。API 只有一个父响应。

**推荐：W1-R2→W1-R3、W2-R2→W2-R3；W2-R3 额外只输入 W1-R2 reason。** R1/R2 仍保持当前请求行为。请确认是否原文第一处是笔误；若确实要 W2-R3 同时继承两链，需要改变“只传 reason”或改变前序请求链，范围更大。

### Q2：“W2-R1 维持现状”是否允许只调整召回提示词口径？

现有 R1 排除明确未触发消息，与 RELATED 需要召回未触发场景冲突。

**推荐：保持 R1 输入、schema、轮次和候选数现状，调整提示词为召回 match_scope 内实质相关的场景，含明确未到 trigger boundary 的情景。** 如果提示词也完全不动，需要接受 RELATED 的系统性漏召回。

### Q3：是否接受 R3 FALSE 是“未证明属于 W1 reason 的 NEW”，并只重判 Policy 依据新旧？

当前 W1 理由非穷尽；没有点名不等于事实 OLD。直接覆盖消息 NEW→OLD 既可能错误也会撞上 OLD 必须引用旧 Event 的现有合同。

**推荐：保留 W1 原始 NEW/OLD 和全文 Delta 权责；R3 只控制 Policy 相关依据的有效新旧。** 不明确覆盖也给 FALSE（遵守二值结果），疑似重复命中交 W3重新核验。若要 FALSE 代表事实确证为旧，需要额外已知事实比较或改变 W1 输出合同，超出“只输入 reason”的当前设计。

### Q4：HIT 条件全 FALSE，但 RELATED 条件有 TRUE 时，以哪一类路由？

两类判定允许并存，因此“任一 TRUE 等于 NEW”与“HIT 全 FALSE 等同 OLD+HIT”会在同 Case 冲突。

**推荐：HIT 授权只看 HIT 条件，绝不用 RELATED TRUE 放行；本例归 policy相关事件→W3，同时保留命中依据未通过的证据，不标一轮判定交易。** 正常置信度仅 RELATED 且无 TRUE 时按有效 OLD/无 HIT 归档，但原 W1 NEW 事实仍可捕获。请确认这两种混合/未通过情形的优先级。

### Q5：W3 对一轮判定交易，是复核触发事实，还是还要独立判断可交易性？

当前 W3 最终 NEW+HIT 按 Policy 方向交易，不允许专家交易判断。

**推荐本次沿用既有权责：W3 重新核验同一触发事实是否新、Condition 是否成立，通过后按 Policy 交易；NEW+无 HIT 才进入现有专家研判。** 若需求是即使最终 NEW+HIT，W3 也能基于预期差/交易价值否决交易，需要明确新增这一权力及对应合同。

### Q6：父响应不可用时，是否允许仅在实际报错后重建上下文？

健康路径不传 R3 业务包以后，恢复依赖提供方保留的父响应。当前 resume 复用旧父 ID，现有即时继承测试未证明长期可用。

**推荐：仅在提供方明确返回父响应不可用时，用本地冻结的父输入/输出恢复等价上下文，再执行该轮；健康路径仍按要求只走继承，不增加预检或阻塞校验。** 如果“取消业务数据”也禁止异常时重建，则保留 Case 隔离失败和人工处理，需要接受这种恢复边界。不能把随意重新跑父轮当成等价恢复。

## 12. 后续产物

用户回答 Q1—Q6 后，产出整体开发方案 MD，包含确定的新合同、完整路由矩阵、W3 权责、冻结暂停范围、旧包/恢复处理、前后端同步、针对性测试和部署/验收边界。届时仍按授权区分方案、实现与部署；本报告不代表已实现或生产验收。
