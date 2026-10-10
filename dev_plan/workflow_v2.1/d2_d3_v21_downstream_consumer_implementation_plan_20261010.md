# D2 / D3 v2.1 下游消费者适配：统一实施方案

日期：2026-10-10。状态：可执行方案，尚未实施或部署。本轮仅更新勘察及编写本方案，不修改业务代码、提示词、消费开关或运行状态。

## 1. 依据、优先级与完整交付边界

依据：

- [更新后的下游勘察](d2_d3_v21_downstream_consumer_reconnaissance_20261010.md)。
- [W2R3 编排实施方案](../workflow_v2/persistent_runtime_w2r3_orchestration_implementation_plan_20261010.md)及其本地实现。
- [按方向消费与整 Policy 重新授权](d3_policy_direction_consumption_rearm_requirements_20261010.md)。
- [Policy 临时 WATCH 最小实现方案](../workflow_v2/persistent_runtime_policy_watch_implementation_plan_20261010.md)。
- D2/D3 v2.1 已冻结 producer 合同、`d3_v2.1.md` 及现有双 OPEN 编排。

本次用户确认优先于首轮勘察建议和旧方案的阶段限制：**恢复 READY 即冻结，采用 Policy×方向消费；规则变化不解冻，采用整 Policy MAINTAIN 显式重新授权；继续闭市维护。** WATCH 方案中的旧“相同规则是否消费”判断，相应改为方向资格判断；W2R3 方案中的暂停开关不再是目标行为。

完整范围包括正式候选发布/激活、版本读取、W2/W3 输入输出、交易意图与消费、闭市选择、单 MAINTAIN、WATCH、O4 交付、读模型/API/前端、内部重跑、冻结执行与历史兼容。不是只修 Runtime payload。

不重新设计 D2/D3 研究编排、双 OPEN、Event Library、W1 新颖性任务、Message Bus 抓取、交易执行器、全局模型默认值。不新增主动抓取、逐笔即时维护、WATCH 定时任务、通用状态机或常驻告警。代码实施不编辑、删除业务 prompt/skill；需同步给提示词会话的合同列在第 16 节。

## 2. 最新实现基线：哪些复用、哪些补齐

当前工作区已有另一会话未提交实现；后续必须基于这些文件继续修改，不能用 HEAD 覆盖它们。

| 项目 | 当前实现 | 本次工作 |
|---|---|---|
| W2 HIT/RELATED | 新 assessment、两组归因已实现 | 保留含义，接原生 v3 全文 |
| W2 R3 | 条件新事实对齐、继承父 R2、恢复已实现 | 保留，父轮输入改为新 Policy 后自然继承 |
| 一轮路由/标签 | 无直接交易，全部命中先 W3；标签与读侧已实现 | 保留，增加 WATCH 路由覆盖 |
| W1-R3 / W2-R3 | 两条继承链、有限恢复已实现 | 不扩大调用资格/预算 |
| Policy 冻结 | 共享 False；旧整 Policy revision ledger 保留 | 恢复共享 True，补方向状态/代次 |
| W3 最终交易 | NEW+HIT 直接按旧 Policy 方向交易 | 新合同显式选择 Condition，允许命中但不交易 |
| 新 D2/D3 发布 | D2 新正文可发布；D3 staged 四输出 | 接正式候选、版本读、activation/admission |
| 单 MAINTAIN | 新 agent 节点已有，正式调用仍旧 | 接新 base/feed/资产/receipt/rearm |
| WATCH | 只有方案，没有业务实现 | 按本方案新增最小能力 |
| API/前端 | W2R3 图与证据已更新，策略/预期仍旧结构 | 保留 R3 成果，适配 D2/D3 与方向资格 |

本轮实际验证 `tests/test_runtime_w2r3_orchestration.py` 与 `tests/v2_backend/test_w2r3_reads.py`：**21 passed in 9.07s**。不据此宣称完整下游、新消费、WATCH 或远端上线已验收；另一会话 acceptance 的其他测试属于既有记录。

三个需直接修正的技术冲突：

1. 只设 `POLICY_AUTO_FREEZE_ENABLED=True` 会恢复旧整 Policy revision 锁，仍不符合新方向规则。
2. 现有 W3 NEW+HIT 强制交易，不能真实表达“命中但暂不交易，SET WATCH”；必须补最终执行合同。
3. WATCH 允许 OLD+RELATED 进入 W3，该 case 不能使用强制 NEW/无 Policy 的 Mode 1。无需增加第三模式，使用现有 REVALIDATE_THEN_EVALUATE。

## 3. 确定的业务规则

| 对象 | 确定规则 |
|---|---|
| R1 召回 | 每条仅 `policy_id/title/match_scope`；中性召回，不以预判 LONG/SHORT 过滤 |
| R2 / W3 Policy | 完整 v3 Policy，全部方向条件；运行资格另附 sidecar，不改正文 |
| 命中 | W2 HIT/RELATED、W2 R3、W3 最终 HIT 保留各自证据，不相互偷换 |
| 最终 Policy 交易 | W3 显式选一个命中 Condition；方向取该固定 Condition；可以显式不交易 |
| 消费 | `(ticker, policy_id, direction)`；READY 接纳时冻结该方向所有现有和未来 Condition |
| 反方向 | 独立资格；LONG 消费不让 SHORT 从 R1/R2/W3 消失 |
| 规则 revision | 仅标识内容/判断候选是否仍有效；改稿、换 Condition ID、升版、跨日不解除消费 |
| 重新授权 | 整 Policy 请求，完整 upsert，成功激活后解除本轮看到且未变化的消费 ID |
| 维护 | 继续单 MAINTAIN 和闭市 sweep；消费待处理状态持续进入 feed |
| WATCH | Policy 级短期研判关注，仅改变路由；不改 W1、不放行冻结方向、不授予交易资格 |
| WATCH 期限 | 来源 case 的语义日 D 与 D+1，`boundary(D+2)` 到期；不续期、不跳周末 |
| 自动取消 | 任一方向成功 READY 消费，同事务取消该 Policy WATCH；反方向资格不受影响 |

用户“R1 三字段”和“按完整 v2.1 schema 同步输入合同”落地为：**规范 D3/投影使用完整 v3/v5，R1 业务视图严格三字段，R2/W3 原生全文。** 不同时向 R1 内联完整条件；否则三字段缩减要求没有意义。原 R1 的 source_message 与返回候选合同保留，最多 3 个候选不变。

本轮明确的三字段要求覆盖早期 D3 冻结文档向 R1 提供“必要 transmission”的建议；transmission 仍完整进入 R2/W3，不从规范产物删除。

## 4. 版本模型与输入合同

### 4.1 新旧解码明确分派

新增小模块 `workflows/codex_document3/consumer_contracts.py`，集中定义和解码：

```text
parse_document2(payload) → Document2Document | Document2DocumentV21
parse_policy_set(payload) → PolicySet | PolicySetV3
parse_runtime_projection(payload) → 旧 RuntimePolicyProjection | RuntimePolicyProjectionV5
policy_details(...) → 对应版本的详情对象
```

D2 decoder 放在 D2 schema/assembler 附近的公共合同模块亦可，但只能有一个实现。分派依据实际 schema_version，未知版本报明确技术错误；旧 v1/v2/v3→v4 投影升级保留。不要把新对象转换成旧 PolicySet，也不要给新 Policy 补默认 LONG、旧 source_refs 或伪 transmission。

`RuntimePolicyProjectionV5` 精确对应现有 `project_policy_set_v3` 的输出：`schema_version/consumer_contract/ticker/policy_set_version/as_of/published_at/publication_state/policies`；每条 Policy/Condition 字段及 condition_revision 保留。用模型约束新投影，不修改规范投影为三字段。完整详情使用 `PolicyV3`，避免手工漏字段。

### 4.2 R1 三字段视图

`service.py:_w2_projection_business_payload` 对新合同输出：

```json
{
  "policies": [
    {"policy_id": "P1", "title": "中性观察范围", "match_scope": "召回范围"}
  ]
}
```

不发送 criterion、condition_ids、decision、trigger_layer、calibration、transmission、ref、revision 或使用代次。R1 只召回范围。title 从规范 Policy 取得，不用 match_scope 代填。旧正式集合如需使用相同摘要，也从其固定全文取得 title，不从 current head 补。

宿主只有在 Policy 所含方向全部冻结/不可执行时才从新的可交易召回集合移除该 Policy；只冻结 LONG 时保留整条中性摘要。WATCH 不扩大召回集合或候选上限。

### 4.3 R2 全文与资格 sidecar

`_w2_detail_business_payload` 的 `policies` 直接序列化召回的原生 PolicyV3：

```json
{
  "candidate_policy_ids": ["P1"],
  "policies": [{
    "policy_id": "P1", "title": "中性观察范围", "ref": [],
    "transmission": "预先研究的经济传导", "match_scope": "召回范围",
    "activation_conditions": [{
      "condition_id": "C1", "decision": "SHORT", "trigger_layer": "MIDDLE",
      "criterion": "独立充分条件",
      "calibration": {"reference_state": "当前比较面", "trigger_boundary": "增量边界"}
    }]
  }],
  "policy_usage": [{
    "policy_id": "P1", "executable_directions": ["SHORT"],
    "consumed_directions": ["LONG"]
  }]
}
```

Policy 中实际上存在的全部 Condition 均完整保留，示例仅缩成一条。资格 sidecar 不改变业务正文，也不把某方向删除后宣称给了完整 Policy。usage_generation、consumption_id、完整消费快照在宿主冻结输入保存，R2 不需要承担这些协议字段。

输出继续当前 `W2PolicyAssessmentResult`：policy_ids 只表示 HIT；hit_condition_ids 是 HIT；matched_condition_ids 是 RELATED。R3 仍只接 W1 reason 并继承父上下文，不新增重复正文包。空归因允许记录，未知引用按既有清洗处理，不新加全集匹配/互斥/语义重试。

当前 `_run_w2_hot` 在模型返回后重新过滤已消费 Policy，并可能把整个结果覆盖为 `policy_hit=false/policy_related=false`。新合同分支取消这种“消费状态改写判断”的处理：固定版本内的 HIT/RELATED 及合法归因保留，执行资格另行核对。否则并发消费一个 HIT Policy 可能抹掉另一 Policy 的 RELATED、错误改变 R3/WATCH 路由。raw turn 保持原样，不能为了拦截交易改造事实结论。

### 4.4 W3 上下文

PublishedW3ContextProvider 按 case pin 解码新 D2、新 D3；D1、Event 继续同一固定来源。提供完整 PolicySet、方向状态、case 的 usage_generation 快照及 WATCH 跟进小记录。首次真实使用的运行资格快照写入 case，再用于重试/候选，不在重试时刷新为新代次。

静态研究正文可按版本缓存；消费状态和 WATCH 不是静态缓存内容。新 case 读持久状态，已冻结 case 使用自己的判断证据，实际输出仍检查当前资格。W3 不再整条删除只有一个方向冻结的 Policy。

保留 original_w1、旧 HIT 视图 original_w2、完整 first_round_policy_assessment、alignment 和一轮标签。不把新 W2 的 RELATED 归因传成 W3 HIT。

## 5. 正式候选发布与激活接入

### 5.1 复用现有候选机制，不另建活动仓库

新增 `workflows/codex_document3/publication_v21.py`，实现 `finalize_candidate_v21(run_id)`：

1. 读取已完成 staged receipt；核对四输出、同版 PolicySet/投影、hash 和 release manifest。
2. 注册四文件 ArtifactRef、可本地读取的 PublishedDocument；保存已发布研究的 Document3Bundle/Handoff 传输外壳。
3. 外壳增加明确的 document_schema_version/projection_schema_version 及 release receipt 引用；保留既有技术 workflow/lane 标识的兼容用途，不能把它当作主正文 schema。正文始终是 document3.v3。
4. 将规范 v3 与原生 v5 通过 schema-aware `publish_candidate` 写入既有正式 Policy 仓库，保持不抢 current；版本必须等于该 run 已预留版本。
5. 全部准备完成才写候选完成 receipt，返回统一候选 handoff。崩溃重入补登记，不重新派发研究、不再分配版本；已有内容冲突明确失败。

需要先把 get_version/get_projection/details/history、consumer cache、bundle 外壳读取变为 schema-aware，才允许 v3 进入正式候选表。**不能把 is_current=0 当成旧解析器不会读到新对象的保障。** 旧阶段要求 staged 与旧表隔离继续保留；这里只有显式正式 promotion 会登记新合同。

复用 SQLitePolicyRepository 的既有候选表、reservation 和候选幂等；不新增第二张 current 表。正式 managed 工作流以本地仓库为读取依据，现有远端/Hybrid 解码入口同步支持已知版本；不借本次任务执行远端 DDL、镜像 staged 或切远端 current。若某旧 backend 分支未支持新正式写入，需明确能力边界，不能回退读取另一个版本。

具体修改现有 publish_candidate：重复内容比较调用分版本 decoder；投影按 v3/v5 生成；metadata 序列化兼容 V3 的字面 publication_state，不能继续调用 `.value`；沿用已有预留版本，不再因 promotion 增号。正式 bundle/PublishedDocument 的登记顺序使用耐久 receipt 收敛，非一次跨文件/跨库事务。

四正文不可在 activation 时再次改写；Markdown 的永久文字应使用“发布候选/研究产物”等准确表述，active 状态来自控制面。accepted rearm 作为 release receipt 元数据，不塞进 Policy JSON。

### 5.2 常规初始化与 MAINTAIN 调用

`ticker_initialization/research_adapter.py` 在创建 run 时固定 D2 v2.1、D3 v2.1 选择；重入从 run 读取，不临时跟随默认值。调用新版实际签名，再调用上述 candidate finalizer；不向新 initialize 传旧 enqueue_o4/candidate_publication。

O4 读取该候选 handoff 的全文/路径/hash；活动 prepare 仍引用 D1/D2/Event/D3/monitoring configuration 五项。单 MAINTAIN 同样完成 staged 后显式登记候选，NOOP 则保留原引用。

`activation_adapter.py/runtime_inputs.py` 核对版本、ticker、可读正文、规范投影与必要来源 pin，再走既有 CAS。有可执行 Policy 的 PARTIAL 可正常激活；失败空输出不替换旧集合，显式全退役允许空集。无须新增 COMPLETE-only 门槛。

### 5.3 Runtime admission 与授权应用

在 `runtime_scheduler/service.py:admit_activation` 的成功 ACK 前调用统一授权接入 helper，读取该已激活 release 的 accepted rearm bindings，在 Runtime journal 短事务中：

- 幂等应用授权，记录每个 binding 的实际 APPLIED/STALE/NOOP 结果；
- 写入本 revision 已处理授权的 `policy_authorization_applied` receipt；无授权也写空 receipt；
- 保留现有 control epoch/lease fencing。

维护在 control CAS 成功后也调用同一 helper，再完成自身 processed；初始化 consumer 恢复仍可重入补做。避免等待本线程下一轮 scheduler ACK 才能结束维护的循环依赖。

control DB 指针切换与 Runtime DB 状态写入不是一次事务，不承诺跨库原子性。CAS 后、授权尚未应用的短窗口中，新 revision 不可被当作可交易快照；由既有 admission/任务重试收敛。helper 只应用实际激活过的对应 release，不应用单独 staged 请求。实际输出检查对应活动授权已处理和使用代次，不以 Worker ready 代替。

增加 `policy_authorization_head[ticker]` 作为 Runtime 本地已准入 revision；与该 revision 的授权应用 receipt 同事务写。输出准备阶段取得当前固定规则的 `PolicyOutputGuard`（revision、规则引用/hash），输出事务核对本地 head/已应用 receipt 与 guard 一致，再核对方向代次。若准入已推进，重新读取守卫后按规则是否仍有效处置，不给旧 Trade 换规则/代次。不在输出事务中发模型/网络请求。这里保证 Runtime 本地准入与输出的线性化，不声称 control DB 切指针那一瞬间原子中止另一库已经开始的事务。

rollback 不倒退 usage_generation、不抹去消费，不重新执行过去已应用的授权 receipt。若回滚到旧规则，只能使用当时规则与当前持久资格的合法交集。

## 6. W3 最终执行合同：补齐 WATCH 的必要前提

### 6.1 新结果类型，旧 wire 保持不变

新增 `W3CaseResultV3`，不直接改旧 W3CaseResult 的冻结 schema。保留 novelty、policy、expert_trade、delta_candidates，新增：

```json
{
  "result_contract_version": "persistent-runtime.w3-result.v3",
  "policy_execution": {
    "selected_condition": {"policy_id": "P1", "condition_id": "C1"},
    "reason": "最终选择该条件作为本次交易依据"
  },
  "watch_actions": []
}
```

`policy_execution` 可为 null。对象中的 selected_condition 也可为 null，表示**完成 Policy 交易评估，明确暂不交易**；reason 必须非空。不增加分数、优先级、方向第三字段或第二个排序数组。方向、revision、generation 由宿主从选中条件和已冻结资格绑定。

| 最终事实/命中 | policy_execution | expert_trade / Delta |
|---|---|---|
| OLD | null，不产交易 | 保留原 OLD 禁止 Delta/专家交易；可合法 WATCH/CANCEL |
| NEW + HIT | 必须有评估对象，选择一个 HIT Condition 或显式 null | expert_trade 不承担另一条重复决策；保留 NEW 的事实捕获 |
| NEW + 无 HIT | null | 保留原专家研究 evaluated=true，既可交易也可否决；NEW 必须有真实 Delta |

保留 W3 最终全部 HIT 和归因；selected_condition 不删掉其他命中。技术校验仅要求明确身份在固定版本存在、属于最终 HIT、方向可解析。无效执行身份使用既有限次修复/失败路径，不能偷偷取列表首位或默认方向。WATCH 动作局部无效不触发整轮重跑。

### 6.2 落地与副作用

新 `route_w3_result` 按显式 Policy 选择或 expert_trade 决定是否产生交易；保留 NEW+HIT 但选择 null 的结果可记录 Delta/研究结果，并可 SET WATCH。

`_apply_w3_result` 的新分支按已选引用组装 Trade；不要继续 `_claim_first_policy` 的排序回退。READY 输出失败/方向已消费不再选另一个命中补交易。新事实捕获与交易资格分开：不能因交易被消费拦截而提前 return 丢掉合法 NEW Delta。保留现有 provisional 分配幂等。

新 Trade/candidate 增加 `executed_condition_id/executed_condition_revision/usage_generation`，方向仍用 decision；旧 activation_revision 保留旧 codec 的用途，新交易不用 ar_ 字段冒装完整 hash。规则快照/全文引用进入消费依据。独立 expert_trade 沿用原身份，不猜 Policy、不消费 Policy 方向。

WATCH 跟进强制使用 REVALIDATE_THEN_EVALUATE；未命中 WATCH 的模式规则不顺带重写。OLD+HIT 仍不新建 BADCASE；一轮也不恢复直接意图路径。

## 7. Policy×方向的持久消费

### 7.1 最小记录

扩展现有小模块 `persistent_runtime_v2/policy_consumption.py`，不建立新的消费执行器。在 journal.runtime_values 增加：

```text
policy_direction_usage : key = encode([ticker, policy_id, direction])
  ticker, policy_id, direction,
  usage_generation (初始1), consumed, consumption_id (未消费为null)

policy_consumptions : key = consumption_id
  上述身份/代次 + executed_condition_id/revision,
  rule_snapshot或固定内容引用、policy_set_version、activation_revision_id,
  case_id/source_message_id/intent_id/consumed_at
```

后一条为不可变消费依据，前一条为当前资格。消费 ID 由宿主确定性生成，例如 hash(ticker,policy_id,direction,generation,intent_id)，重试不换 ID。新方向惰性初始化；不存在的方向不强造 Condition。删除/再新增同方向条件也复用原资格行。

将共享 `POLICY_AUTO_FREEZE_ENABLED` 恢复 True，service/W3/输出/read 使用同一策略。必须在新资格路径、正式接入和兼容完成后修改，不能先开旧整 Policy 锁作为“已完成”。无运营 UI 或多级动态开关。

收敛为少量直接 helper：`read_usage_snapshot`、`claim_ready_in_transaction`、`accept_rearm_requests`、`apply_release_authorizations`。消费/授权 helper 接受调用方已有 db，不能各自另开事务；快照读取只负责资格，不过滤或改写业务判断。

### 7.2 READY 的单事务

`TradeOutputService.record` 保留已有短事务、assert_write_lease、output_permission、日期到期、intent 幂等、event trade 去重、CLOSED/realtime 去重。新 Policy 输出在同一事务内：

1. 已有该 intent 则直接返回，不再次消费。
2. 权限/日期/既有去重不通过则记录对应结果，不改方向状态和 WATCH。
3. 核对必要的活动授权已应用、选中规则仍可执行、方向状态未消费且代次等于 Trade 固定值；CLOSED 的规则检查见第 10 节。
4. 写方向 consumed=true、消费依据、Trade 和 READY intent；删除实际执行 Policy 的 WATCH。
5. 全部提交或全部回滚；外部 delivery/fill 不参与这个事务，也不解冻。

消费 LONG 后 LONG-C1/C2/后续 C9 都冻结；SHORT 未消费则仍可用。并发同方向输出只接受一次，不因多个 Condition 或新版本绕开。反方向是否交易仍由各 case 业务判断决定，不因 LONG 已消费自动变成交易信号。

无 journal 的旧/内存兼容路径保留既有行为并提供必要的可测试方向状态接口，不为 WATCH/rearm 新建一套存储。正式新资格/rearm/WATCH 接入使用现有带 journal 的 managed Runtime。

### 7.3 历史与暂停期

不清空旧 ledger，不回填暂停期间未写入的消费，不把旧 raw turn 改成新结果。旧 v4 结果、旧在途 case/候选保留 codec 与既有身份。

新方向状态已有行时永不因版本切换初始化覆盖。若正式接入沿用已有 policy_id，已有旧 claim 的方向只有在固定 PolicySet/正式 Trade 能确证时才按原消费依据导入一次；保留原来源、使用稳定迁移 receipt，不把一个旧 claim 猜成 LONG+SHORT。无法确证的实际旧对象继续旧合同处理并记录具体迁移缺口，不能假称已完成双向迁移，也不能默认为全可用。

新 INITIALIZE 重新研究的不同 ID 不按标题猜旧消费/alias；MAINTAIN 对已消费 Policy 的重授权必须使用原 ID，不能以普通新增对象冒充授权。宿主不增加语义相似度匹配引擎；只检查明确身份和已有处置关系。

## 8. 整 Policy 重新授权

### 8.1 每次维护都有持久消费输入

O3MaintenanceFeed 新增 `policy_consumption`，按 Policy 汇总当前仍 consumed 的方向、generation、consumption_id 和消费依据。新增字段缺省 [] 供旧存档读取；新维护 wire 版本固定为 v2，旧 v1 仍单独读。

具体保留旧 O3MaintenanceFeed v1 类型，新增 O3MaintenanceFeedV2；统一读取按 contract_version 分派。`feed_has_work` 当前显式只识别 v1，须改为识别 v1/v2 并检查新列表，不能把整个非空 v2 字典误作“永远有工作”。

`maintenance._frame` 首次冻结时从持久方向状态读取，不能只从该 sweep 当日未 processed Trade 推导。相同 task 恢复复用冻结 frame；新一轮维护重新读取仍冻结项。失败、漏跑、普通 upsert、合法维护但未授权，均不删除这些状态，下次继续出现。

将 policy_consumption 纳入 `feed_has_work`、维护触发/no Event 分支。单 MAINTAIN 仍可复用；无 Event delta 但有消费待处理项时必须进入研究，不被 NOOP 提前跳过。

复用现有每个闭市 sweep 结束后生成 MAINTENANCE 的调度，不增加即时维护任务。增加“零新消息/零新增 Event、上一轮未授权消费仍在”的端到端调度用例；若现有入口在空成员提前返回，则修该早退，使持久冻结项进入已有本轮维护。Event 无变化沿用原 ref，不为触发 O3 强造新 Event 版本。

### 8.2 patch 与局部接纳

MaintenancePatchV3 增加默认 [] 的 rearm_requests，并同步 WORK_SCHEMAS、宿主解析/局部问题记录及 `document3_v2.1_contracts.schema.json` 的维护工作合同：

```json
{"rearm_requests": [{"policy_id": "P1", "reason": "整个观察范围已经重新研究，可建立新的独立交易机会"}]}
```

请求不接受 direction、condition_id、generation 或 consumption_id；agent 无权选择解哪一个锁。宿主从冻结 feed 绑定该 Policy 当时看到的 consumed 方向及 ID。

RearmRequest 使用局部严格对象，越权字段使该条请求被记录并排除，不让 WorkModel 的 extra=ignore 悄悄接受一个“仅解 LONG”的错觉请求。`validation_v21.structured` 已有逐项列表接纳能力，直接复用，不新增全 patch 拒绝器。

逐项接纳条件：Policy 属于 base/消费输入；本轮有对应**完整且被接纳**的同 ID Policy upsert；没有同时 retire；有可绑定的消费依据。重复完全一致请求去重；同 ID 冲突请求局部拒绝并记录，不使其他合法修改失败。研究理由的文字质量不加关键词/LLM 闸门。

upsert 接纳成功但 rearm 不合法，普通改稿仍可发布；rearm 合法但其他 Policy 有问题，合法 PARTIAL 可携带授权。所有字段未变化仍可接受完整 upsert 和 rearm，不能机械要求反方向每条改稿。

### 8.3 release 与授权 CAS

接受记录写入不可变 release/activation receipt：policy_id、reason、maintenance_run_id、base release/hash，以及宿主绑定的 `(direction, consumption_id, usage_generation)` 列表。将这份记录纳入 commit identity/hash，恢复不能换一批消费 ID。业务 Policy/Condition JSON 不增加资格字段。

新候选成功激活后，第 5.3 节 helper 在 Runtime 同一短事务内逐方向比较：

```text
当前仍 consumed
AND consumption_id == 本轮冻结ID
AND usage_generation == 本轮冻结代次
  → consumed=false, consumption_id=null, generation += 1
否则 → STALE/NOOP，保持当前状态
```

每份 activation/action receipt 只应用一次。未消费方向不增加代次；维护期间新消费的另一方向没有绑定，不解除；同方向在别次授权/消费后 ID 或代次不同，不解除。重复激活、恢复、回滚不重复加代次。

普通 upsert、正文 revision 变化、staged 成功、候选成功、发布/激活失败均不解冻。**合法 rearm 本身是实质变化**，MAINTAIN 的结果 NOOP 比较必须包含 accepted rearm，不能只比较 Policy 文本。

## 9. Policy 临时 WATCH

### 9.1 输出、存储与作用域

W3CaseResultV3.watch_actions 默认 []；新模型调用严格 schema 中声明该字段。动作只包含非空 policy_id、SET/CANCEL、非空 reason。Agent 不设置期限、WATCH ID、优先级或状态。

解析时保留原始响应，对 watch_actions 逐条做结构接纳/记录；坏动作不导致核心 W3 研究结果被整轮重跑。novelty/最终执行等核心合同格式错误仍走既有有限修复。宿主动作接纳另外核对 Policy 和上下文边界，不能因动作结构合法就直接写状态。

新增小模块 `policy_watch.py`，复用 journal：

- `policy_watch` key=encode([ticker,policy_id])，保存 source_case_id、reason、start_semantic_day、expires_at。
- `policy_watch_applied` key=case_id，保存本 case 动作已处理及实际接纳/忽略原因；与动作变更同事务。

不新建 WATCH 业务表/历史库；W3 result、路由依据和 receipt 已提供历史。WATCH 跟随 Policy ID，不绑定规则 revision；普通同 ID 改稿不续期，新 ID 不继承，退役后无可用匹配、自然到期。

### 9.2 SET/CANCEL 接纳

SET 只在最终明确没有交易决定、没有 CLOSED 候选时接受；交易被权限/重复/过期拦截也不是“明确不交易”，不能据此接受原本冲突的 SET。对象必须属于本条消息 W2 清洗后的 HIT/RELATED 集合和固定可用 Policy；R1 候选不是相关证据。

OLD 结果也可 SET，不改变 novelty。理由交由 agent，宿主不评估“短期”措辞质量。当前已有有效 WATCH 则不覆盖原因/来源/期限；过期记录可被下一合法 SET 覆盖。有效时间按来源 case D 计算，迟到超过 D+2 则忽略。

同 Policy SET/CANCEL 冲突时 CANCEL 优先，不同一次取消后重设延长。CANCEL 只可删除该 case 实际冻结看到的 WATCH，宿主比较 source_case_id；旧迟到 CANCEL 不删除后来新记录。缺失/已替换/已到期为幂等无操作。

方向语义：只冻结 LONG、SHORT 尚可用时，不因为 LONG 的历史消费一概禁止后来为剩余机会 SET WATCH；若无任何可用方向则忽略 SET。消费任一方向后都取消当前 Policy WATCH，但不消耗反方向。

### 9.3 W2 后的一次性路由覆盖

完成 W2 清洗及按既有资格执行 R3 后：

1. policy_hit=true 时取 HIT Policy/归因；policy_related=true 时取 matched 分组 Policy，合并为身份集合。只有布尔而无法定位时不猜。
2. 首次判路由读取有效 WATCH，冻结完整小记录到 `case.frozen_inputs.policy_watch_followup`；无命中也保存 []。
3. 若命中，primary_route=W3、reason 追加 POLICY_WATCH_FOLLOWUP 与 Policy IDs；重新构造唯一 W3 effect，不残留原 ARCHIVE/EMIT_DELTA 效果。
4. 保留 W1/W2/R3 与 first_round_tags；原本已去 W3 的 HIT 也标明原因。
5. 重试不重新查状态改变同一 case 路线；WATCH 到期/取消不撤回已排队 W3。

不绕过技术失败，不跳 W1/W2/R3，不扩大 CLOSED 原有 OLD 跳过 W2 的入口；未进入 W2 的消息不受 WATCH 影响。这是原最小 WATCH 方案的明确覆盖边界，不能宣称它监控所有 OLD 消息。

W3 task.json 携带冻结记录与期限，Mode 使用 REVALIDATE_THEN_EVALUATE，允许复核 OLD、形成最终 HIT、明确不交易或取消关注。数据只写 case 专属 task，不写共享 AGENTS/长期 thread 配置。

### 9.4 消费、迟到结果与恢复

成功 READY 消费事务自动删除 executed_policy_id 的 WATCH；只删除实际执行者，不按 HIT/候选列表批量取消。CLOSED 候选不取消，最终释放才取消。无 Policy 的 expert_trade 不猜取消对象。

W3 正常副作用完成后，提交动作和 applied receipt；动作提交前再次在同事务读取当前 WATCH/方向状态，并查询该 Policy 自来源 case 开始后是否已有成功 READY 输出，防止迟到 SET 在消费取消之后重新挂回。使用持久 Trade/intent 的实际成功接纳时间，不按可变 delivery status 判断；暂停开关兼容测试中也使用 READY 接纳证据。

WATCH 先提交、消费后提交→消费删除；消费先提交→旧 SET 忽略。同 case 重放只读 applied receipt。沿用 journal.clock、lease fencing、短事务，不新增取消墓碑/水位表/研究任务。

## 10. 闭市维护、候选释放与恢复

候选固定实际选中 Policy/Condition、condition_revision、direction、usage_generation、来源版本。生成候选不消费、不取消 WATCH。

WeekendSelection 保留现有冻结 selection_snapshot、显式 candidate_id 和无选项结果，不重建已有候选。最终释放同时检查：

- 选中 Policy/Condition 在当前活动集合可读，方向与 condition_revision 未变化；
- 当前方向未 consumed，generation 等于候选原值；相关活动授权已应用；
- 既有输出权限、语义日和实时输出去重成立。

兄弟条件改动不凭整 Policy hash 废弃选中条件；选中条件改动/退役或代次变化则拒绝旧候选，不自动换成当前新规则。重新授权不会把旧候选升级到新代次。

正式维护将 Event + Policy 候选通过既有 dependency CAS 联合切换，保留 D1/D2/O4 configuration。feed processed 只能在发布/激活及授权接入成功后完成；崩溃恢复先幂等补授权再 processed。processed 只处理本轮 Trade/Gap 等，不删除持久方向冻结。

`service.py`、`coordinator.py` 的冻结 projection 解码、W3 context cache、内部 task receipts 和 selection payload 全部改为按固定合同分派。旧成功轮次复用，不临时重跑 R3/W3、不以最新正文替换历史 pin。

## 11. 其余消费者与正式操作闭环

### 11.1 D2 / D1 上下文

W3 与 read 的 D2 parser 接原生 v2.1；Shell→Unit→四块新内容，不把 name 当跨 run 业务 ID。前端定位 ID 使用 run 内 pointer/数组位置，失败 outcome/seed 保留，不补造旧 Gap。

W3 如需独立 Future Nodes/Entity Map，使用已固定 D1 run 的产品索引/文件，不拿其他 run 最新产品填入。D2 新来源已能进入公共上下文，不重新研究 D1/D2。

### 11.2 O4、来源与内部重跑

O4 继续接受候选全文、D2 路径及 SHA；SourceNeed 保持 Policy 聚合，不拆每 Condition 一个来源需求。初始化显式 configure/register，不恢复每次 MAINTAIN 自动重配 O4。

来源 ref 按真实固定文件/文档定位，无法解析则显示原文；document2_ref 可空，不为 OPEN 产物强造 D2 Gap 链。原 PolicySet.run/version 来源 pin 完整保留。

内部 rerun 接 v2.1 实际 owner/phase/wave 与工作合同、冻结资产和 checkpoint；不把 Discovery 当旧 Stage A。独立重跑不隐式发布父 run，也不重复应用授权。

### 11.3 读模型、API 与前端

D2 新结构、D3 中性全文和新 handoff 进入 artifacts/formal 投影。PolicySummary 不再强制单 direction；各 Condition 显示方向/位置/校准，资格在 Policy×方向汇总，不能显示成每 Condition 各自一次机会。

消费/授权时间线区分规则变更与交易资格变化；READY 接纳时间与实际成交分开。保留旧历史 consumed 审计，但 effective 必须服从当前方向资格，不继续套用暂停开关的“consumed 也有效”。

保留 W2R3 已有图、筛选、HIT/RELATED 详情与导出；最终 HIT 统计不加入 RELATED 或 WATCH。不新增 WATCH 页面/图节点/运营开关；case 详情和导出补 route.reason、policy_watch_followup、watch_actions 及动作 receipt 即可。

同步公共 TypeScript 合同、后端/前端 wire schema、route contract、case/export 白名单、policy_hits/lifecycle 及 shadow rebuild。遵循 frontend/v2/AGENTS.md 的紧凑桌面规则，不在策略卡片重新堆叠版本/消费/生效元数据行；必要方向可执行状态放在条件/筛选与既有详情位置。

公共 DTO 采用明确的新旧分支：PolicySummaryV3 以 `directions/executable_directions/consumed_directions` 替代必填顶层 decision，保留已有 policy_revision_id 作为定义身份；PolicyDetailV3 使用原生 PolicyV3、可空 source_document2 及方向资格。旧 PolicySummary/Detail 仍读取旧历史。新 D2 Shell DTO 使用 `view_id + name/scope/boundary`，Unit DTO 使用 `view_id + name/scope/horizon` 及原生四块内容；URL 仍可复用现有资源层级，路径参数是 run 内表示层定位符，不把 view_id 写回研究 JSON。最终 HIT 统计继续按全部真实 HIT，选中交易与方向消费是另外的事实，不能把未执行的 HIT 从统计删掉。

## 12. 冻结包与兼容合同

新执行能力增加明确的合同标记：Policy schema、projection schema、maintenance feed schema、W3 result schema。新组合可使用 orchestration_revision=3；现有 1/2 是已知旧组合，不改其原含义。R3 资格判断从仅 ==2 改为明确支持 2/3，不能让新合同漏跑已实现 R3。

必须逐处检查当前 `==2`：R2 output_model 选择、W2 R3 资格、W1 R3 空 payload/inherit_context/cache keys、manifest 的 w2_r3 文件加载均要支持 3；只改 RuntimeCase 的 Literal 会使新版本退回旧 R2/继承行为。W3 当前直接动态生成旧 schema，改为从该 case 的冻结 W3 合同/schema 取值。

ExecutionBundles/factory 保存这些标记和对应资产摘要；round_inputs、W3 调用的 output schema/hash 也必须冻结。新组合只在其输入合同、schema 和执行资产配套时创建；历史成功结果不新增 watch_actions 副作用，旧存档缺字段正常读取，不重写 raw payload/schema hash。

存量 v4 ticker/在途任务仍有旧合同入口；不能把它们强行读取为原生 v3。旧结果缺执行 Condition/generation 时不套当前新代次。已有旧候选保留原选择证据；迁移接入需要时保存一次明确的旧资格基线，绝不在释放重试时取最新 generation 给旧候选续命。

全局恢复冻结与新 v3 方向状态是同一交付的一部分。旧路径仍读取其保留 ledger；这不被宣传为旧稿已完成双向重研究。新 v3 同 ID 的持久资格遵循第 7.3 节，不允许通过 parser 分支重置。

## 13. 文件级任务与执行顺序

路径前缀除特别说明外为 `src/doxagent/`。

| 顺序 | 文件/模块 | 具体完成条件 |
|---|---|---|
| T1 合同与 decoder | D2 公共 decoder；D3 consumer_contracts.py/runtime_projection.py/schema_v21.py；Runtime schema.py | 原生 v5/详情、新 W3 执行/WATCH、方向资格/feed/rearm 类型；旧 wire hash 不改 |
| T2 正式候选 | D3 repository.py/publication_v21.py/state_v21.py；codex_runtime 的 bundle 外壳/发布存取 | staged→候选四文件/正文可读，版本幂等；所有正式读取能分派新旧 schema |
| T3 方向状态 | policy_consumption.py；repository.py/trade_output.py/providers.py | 当前状态/消费依据、generation claim、READY 单事务、旧审计兼容 |
| T4 W2/W3 主链 | service.py/router.py/w3.py | R1 三字段、R2 全文+资格、现有 R3 保留、显式执行/不交易、Delta 不因消费丢失 |
| T5 重授权闭环 | maintenance.py；D3 orchestrator_v21.py/runner_v21.py/validation_v21.py；工作 JSON Schema | 持久 feed、完整 upsert/rearm 局部接纳、非 NOOP、receipt 消费 ID 绑定 |
| T6 活动接入 | ticker_initialization research/activation/runtime_inputs/consumers；runtime_scheduler/service.py | candidate→CAS→授权应用→ACK，恢复幂等，不作跨库原子承诺 |
| T7 WATCH | policy_watch.py；service/router/W3/task/trade_output | 期限、冻结路由、mode、动作局部处置、SET/消费竞态及取消同事务 |
| T8 CLOSED/执行兼容 | selection.py/coordinator.py/execution_bundle.py/factory.py/substeps/internal_adapter | 候选双身份检查，包/轮次/schema 固定；旧成功输出不补副作用 |
| T9 其他消费者 | O4 adapter；v2_read artifacts/formal/lifecycle/policy_hits/projectors；API 与公共 types；两个前端业务页/既有case详情 | 查看/下载/来源/历史/方向状态/证据完整；不重做已有 R3 图 |
| T10 验收与记录 | 相关 tests、验收 MD、根 changelog | 各场景实际证据、兼容与浏览器边界；最后统一恢复冻结策略 |

T1–T3 可先搭模型与持久能力，但在 T4–T8 完整之前不让新候选进入正式交易。T9 不能留成“上线后再看”；它决定能否准确观察方向冻结/授权和故障。沿已有模块作直接适配，新增模块仅收敛合同、发布、消费和 WATCH，不建通用插件系统。

## 14. 必须验证的场景与命令

### 14.1 行为验收矩阵

| 组 | 必须证明的实际差异 |
|---|---|
| 合同 | 新 D2/v3/v5 可读；旧历史可读；R1 每条严格三字段；R2 全字段/双方向；相同 criterion 不被旧唯一性误杀 |
| 前置编排 | 既有 R3 资格/继承/父失效恢复、混合 HIT/RELATED、LOW、标签和无一轮直接交易不退化 |
| 最终决策 | 双向同时命中保留；显式选一个条件；NEW+HIT 可不交易并 WATCH；NEW 无 Policy 仍必须专家研究 |
| 消费 | LONG-C1 READY 后所有 LONG 冻结、SHORT 可用；新增/改稿/跨日不解冻；同方向并发只一次；输出事务回滚无消费 |
| 重授权 | 完整同 ID upsert +请求+激活才解；普通改稿/staged/失败不解；文本不变 rearm 非 NOOP；PARTIAL 局部成功 |
| 消费 ID CAS | 维护未看到的新 SHORT 消费保留；别次 rearm/消费后的新 ID 不被旧请求解除；未消费方向不加代次；恢复只应用一次 |
| 维护 | 无 Event 有冻结项仍运行；失败/未授权后下轮继续看到；processed 不丢冻结状态；单节点资产/来源固定 |
| CLOSED | 候选不消费/不取消WATCH；释放 READY 才消费；规则改动或代次变化拒绝；兄弟规则修改不误拒绝；旧候选不跨代 |
| WATCH 路由 | OLD+RELATED、RELATED 未对齐因有效关注去 W3；原HIT也携原因；空身份/无W2不猜；Mode 1不误拒绝 |
| WATCH 动作 | 只因无交易明确SET；R1召回不可SET；局部冲突CANCEL优先；旧CANCEL不删新来源；消费后迟到SET不挂回 |
| WATCH 时间 | D/D+1有效、D+2边界过期；周末/DST/迟到；重复SET不续期；重试沿冻结路由 |
| 发布/恢复 | 四文件/正文/投影同版；候选不抢active；失败空集保旧；真实v5 admission；CAS后崩溃能补授权 |
| 观察 | D2新结构、OPEN来源、策略方向状态、命中/相关区分、WATCH证据、时间线/导出、旧历史与重建一致 |

新增测试建议：`test_runtime_policy_direction_usage.py`、`test_runtime_policy_rearm.py`、`test_runtime_policy_watch.py`、`test_document3_v21_publication.py`、`test_persistent_runtime_v21_consumers.py`。复用隔离 journal、现有 fake Worker/Responses、clock 与 lease fixtures，不新增测试框架。竞态验证实际事务交错，不仅检查字段赋值。

### 14.2 执行命令

后续实施完成相应文件后执行：

```powershell
uv run pytest tests/test_runtime_policy_direction_usage.py tests/test_runtime_policy_rearm.py tests/test_runtime_policy_watch.py tests/test_document3_v21_publication.py tests/test_persistent_runtime_v21_consumers.py -q --tb=short
uv run pytest tests/test_runtime_w2r3_orchestration.py tests/test_persistent_runtime_v2.py tests/test_persistent_runtime_w3.py tests/test_runtime_orchestration_execution.py tests/test_runtime_orchestration_recovery.py tests/test_ticker_initialization_activation.py tests/test_codex_document3_v21_maintenance.py -q --tb=short
uv run pytest tests/v2_backend/test_wire.py tests/v2_backend/test_formal.py tests/v2_backend/test_case_evidence.py tests/v2_backend/test_policy_consumption_coverage.py tests/v2_backend/test_trade_outcomes.py tests/v2_backend/test_w2r3_reads.py -q --tb=short
node scripts/generate_v2_schema.cjs
pnpm --dir frontend/v2 schema
pnpm --dir frontend/v2 typecheck
pnpm --dir frontend/v2 test
pnpm --dir frontend/v2 build
```

内部 rerun/来源/publish 的实际修改涉及哪个现有测试文件，就将该文件加入定向组合；不凭列出命令宣称已执行。Ruff 和已有合同一致性检查覆盖实际变更。

浏览器按 frontend AGENTS 在 1262/1559 桌面宽度实际检查策略、D2、case/watch证据、来源与导出，再做相关 Playwright fixtures。保留 R3 已验收图，不无理由重新布置运行页。shadow rebuild 使用既有 verify/switch 流程，核对新旧定义/方向消费与 HIT 统计，不清库。

离线通过后，以隔离库做完整 MU 新合同串行验收，实际核对模型请求、四文件、正式候选、admission、W2R3/W3、READY消费、闭市维护授权和后续反方向/WATCH消息；这属于后续明确的验证步骤，本轮不启动模型或交易。

## 15. 发布、回退与完成定义

后续实现完成时同批交付 backend、合同生成物、读投影、frontend 和配套执行资产；恢复共享冻结策略包含在该交付中。仅发布代码但让旧 W3 skill 面对新输出 schema，不能作为业务完成。

生产部署/当前活动切换不包含在本轮方案交付中。未来上线保留旧执行包、历史数据、消费依据与原活动 revision；新 case 使用新固定合同，在途按已冻结合同完成。回退构建必须能读新字段/消费状态；不能直接假设旧镜像 extra=forbid 可读，也不通过恢复旧 DB 或清空 ledger 来撤销已经发生的消费/授权。

代码完成需同时满足 T1–T10 和上述行为矩阵；根 changelog 追加重要变更、实际命令/结果。验收文档区分离线、真实模型、浏览器、隔离完整工作流与生产部署，不用 staged 成功代替正式消费成功，不用 READY 代替实际成交。

## 16. 需要同步给 prompt/skill 会话的内容

本次代码不修改提示词，但应向负责提示词的会话提供以下确定合同：

- R1 仅三字段；R2 原生 v3 全文和方向可执行状态；transmission 不是隐含 AND，层级不是优先级。
- W2 HIT/RELATED 与 R3 保持当前含义；最终交易权在 W3，双向命中必须保留，明确选一个 Condition 或有理由不交易。
- Policy 执行与事实 HIT 是不同判断；不能为了 WATCH 否认真实 HIT，NEW 无 Policy 也不能用 WATCH 逃避专家研究。
- LONG 被 READY 消费后所有 LONG 条件冻结，改稿不能解冻；SHORT 仍可研究/执行。交易未成交不恢复资格。
- MAINTAIN 每轮看见未授权消费依据；整 Policy 重研究+完整同 ID upsert+rearm_requests 请求，宿主绑定消费 ID，激活后才生效。不要求机械重写所有字段。
- WATCH 的短期缺口理由、SET/CANCEL 边界、D/D+1期限、不续期、跟进可能仍为OLD，以及消费时自动取消。
- WATCH 跟进走复核模式，case task 提供原原因与期限；长期共享 thread 不承担记住 WATCH 状态的职责。

这些是本实施方案的配套输入输出与行为边界，不额外扩展为提示词重写项目。具体业务文案由负责会话修改，本方案不以代码拼接隐藏业务指令替代。
