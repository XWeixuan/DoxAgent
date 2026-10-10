# D2 / D3 v2.1 下游消费者勘察

日期：2026-10-10。对象：当前本地 checkout，基准 HEAD `f91dbbc5`。本轮只勘察和进行隔离的合同探针，不修改业务代码、prompt/skill、运行库、活动版本或远端部署。

**2026-10-10 补充复核：** 已对照 W2R3 实施方案、按方向消费/重新授权需求和 WATCH 方案重新读取当前工作区。W2R3 相关实现已出现在未提交改动中，首轮关于“一轮直接执行/自动冻结开启”的描述不再代表当前状态。以下第 3、4、8、9 节已更新；首轮探针保留为当时证据，不冒充本次重跑结果。统一后续方案见 [下游消费者适配实施方案](d2_d3_v21_downstream_consumer_implementation_plan_20261010.md)。本轮仍只改文档，没有开启消费开关。

## 1. 结论与边界

**这不是一次字段改名适配，而是“版本化发布接入 + Condition 级执行与消费 + 维护闭环 + 读模型”的联合迁移。** D2/D3 v2.1 生产端已有独立合同和 staged 产物，但常规初始化协调器、活动读取、W2/W3、交易去重、闭市候选释放和前端仍主要使用旧合同。

目前这些缺口大部分是上一阶段明确暂缓的工作，不能据此认定 v2.1 编排实现失败：`d3_v2.1_orchestration_implementation_plan_20261004.md:382–399,461,566` 明确要求 staged 隔离、不开启活动 current/O4，暂不修改正式维护消费者。现在进入下游适配阶段，才需要补齐这条链。

需要区分三个事实：

- 显式选择 v2.1 的 runner 可以生成新研究产物。
- 新研究产物可以完成 staged 交付。
- 正式初始化协调器、活动指针和交易消费者已经支持新合同。

当前前两项已有实现，第三项尚未打通。手工串行运行正式 D1/D2/D3 不等于完整 `ticker_initialization` 激活及 Runtime 就绪。现有 `mu_v21_formal_remote_test_20261010.md` 也明确采用手工启动、跳过新 O2/O4；本轮没有重新验证该运行的远端状态。

本报告中的文件位置相对仓库根目录；行号以勘察时 checkout 为准。引用冻结需求优先于从旧消费者反推业务规则。没有进行真实模型、交易或生产数据库验证。

## 2. 已冻结的业务变化

### 2.1 D2 不再提供旧的来源树合同

`workflows/codex_document2/schema.py:279–366,503–525`：

| 项目 | 旧 D2 | 新 D2 |
|---|---|---|
| schema | `document2.v2` | `document2.v2.1` |
| Shell | `shell_id/core_question/boundary_rule` | `name/scope/boundary/ref/units` |
| Unit | `expectation_id` 及旧业务字段 | `name/scope/horizon/ref` 和新四块内容 |
| State | 旧参数/事实表达 | `parameters[] + values[]`，值关联 parameter、来源角色、时间与有效状态 |
| Baseline / Factor / Gap | 旧结构及 ID | 新 baseline、realization_factors、potential_gaps；Gap 包含 possibility_space |
| Shell outcome | `shell_id` | `shell` 名称 |

run_id、ticker、as_of、source_global_run_id、input_manifest 等外层身份仍可复用。但不能把新 `name` 当作跨 run 稳定业务 ID，也不能从新 Gap 强造旧 `gap_id` 来源约束。新 D2 不是简单给旧 Pydantic 模型添加几个可选字段。

### 2.2 D3 将交易方向和可执行入口下沉到 Condition

`workflows/codex_document3/schema_v21.py:99–155`、`runtime_projection.py:269–302`：

- 正式主 JSON：`document3.v3`；工作流版本：`document3.v2.1`。
- Policy：`policy_id/title/ref[]/transmission/match_scope/activation_conditions[]`，没有 `policy.decision`。
- Condition：`condition_id/decision/trigger_layer/criterion/calibration`。
- 新投影：`document3.runtime_projection.v5`，consumer contract 为 `persistent-runtime.v2.condition-policy.v1`；包含完整 Condition 及 `condition_revision`。
- `condition_revision` 由 `decision/trigger_layer/criterion/calibration` 计算；目前不包含 Policy title、ref、transmission、match_scope。
- `document2_ref/event_library_ref` 可空。开放研究 Policy 不必挂靠某个 D2 Gap。
- 四输出为同一版本的 `document3.json/runtime_projection.json/coverage_map.json/document3.md`。

冻结方案 `d3_v2.1.md:563–593,684–710` 已明确以下规则，不需要再次征求意见：

1. Policy 中性；同向 Conditions 是独立充分的替代入口。
2. R1 使用中性 title、match_scope、必要 transmission；R2 看全部方向的 Conditions 和完整 calibration。
3. 命中必须归属具体 Condition；双向同时命中保留双方证据，由最终决策流程处理，不能由 schema 顺序/优先级替代方向判断。
4. 一条 Condition 触发不能机械消费整个 Policy 的所有方向；基线变化影响的条件要重新校准。
5. OUTER/MIDDLE/INNER 是位置标记，不是优先级、依赖图或隐藏 AND；transmission 也不是需要每次全部重新证明的 AND。
6. 旧文档保留只读与历史回放兼容，不通过机械补字段假装完成新研究。
7. PARTIAL 不自动禁止发布；失败造成空集合不能清空已有活动集合，显式研究退役全池可合法为空。

## 3. 当前实际链路

```text
常规初始化协调器
  D1 + CDECR → O2 → D2（默认旧 schema）→ D3（默认旧编排）
  → O4.configure / 可选 deliver / register
  → activation.prepare → commit → bus.ready → runtime.ready
                                      ↓
活动 revision 的 D1/D2/Event/D3/O4 引用
  → ActivatedRuntimeInputs → scheduler / frozen case / closed sweep
  → W1 / W2 R1召回 → W2 R2区分HIT/RELATED → 符合资格时 W2 R3
  → 一轮标签 / ARCHIVE / W3（不再一轮直接交易）
  → W3最终判断 → 旧首个 Policy 取顶层方向
  → REALTIME交易输出，或 CLOSED候选 → 闭市最终选择 → 输出
  → trade / badcase / gap / Event delta → O2/O3维护 → 新活动 revision

显式 v2.1 runner
  新 D2 published body（不抢旧 current）
  → 新 D3 Discovery/Planning/Build/Integration 或单 MAINTAIN
  → 四输出 + staged receipt（尚不进入上述旧活动读取链）
```

`ticker_initialization/catalog.py:14–54` 的主拓扑本身不需要推倒重建。O2 是 D2/D3 的上游，不是其新下游；不应因本次适配把 O2 改成读取新 D3 后才能启动。

## 4. 消费者清单与适配做法

### 4.1 正式初始化与发布接入：最高优先级

**现状与根因。** `ticker_initialization/research_adapter.py:350–433` 创建 D2 请求时没有选择新 document_schema_version；构建 D3 时使用 builder 默认 `v2`。`workflows/codex_document3/service.py:35–43,118` 默认值不是“跟随最新实现”。D2 v2.1 发布保留旧 current（`codex_document2/orchestrator.py:410–425`），这是隔离设计，不是发布丢失。

不能仅改 builder 参数：协调器传给 D3 initialize 的 `enqueue_o4=False/candidate_publication=True` 不被 v2.1 签名接受；结果读取也依赖旧 `reserved_policy/get_version` 和旧 published handoff。

**适配。** 在初始化 run 创建时明确并冻结 D2 schema、D3 orchestration/schema；恢复时沿用该 run 的选择。按版本调用对应 runner，并把新 staged release 接成可供 O4、激活、下载及 W3 读取的正式候选产物。保持“产物准备完成”和“活动指针切换”分离，不让生成新研究的动作自行覆盖当前活动集合。

`codex_document3/pinned_runner.py` 已有 v2.1 分支及版本参与幂等身份，可复用；不能把它误认为常规协调器已经接入。新增接入应复用现有初始化 lineage、lease、checkpoint、失败恢复与 candidate 模式，而不是另建一套初始化系统。

### 4.2 Policy 仓库、版本投影与激活：不能把 staged 当 active

**现状。** `codex_document3/repository.py:236–255` 已暴露 SQLite staged v3 存取；但旧 `get_version/get_projection/current/details/history` 仍属于旧合同。`state_v21.py:210–249` 的 staged 表独立；v2.1 `_publish` 最终写入该表，不注册成旧 published bundle。`runtime_projection.py:122–184` 只接受活动 v4 及可兼容的旧投影，拒绝 v5。

`ticker_initialization/activation_adapter.py` 的 prepare 直接走旧 get_projection；`ticker_initialization/runtime_inputs.py:12–79` 的活动输入加载也走旧 consumer。当前 Worker readiness 只验证服务/模型可用，不能证明消费者能解释新合同。

**适配。** 明确区分 staged receipt、正式候选 release、已激活 revision；提供按版本读取的新旧合同解码入口、完整详情与历史/metadata。正式候选应带可读取的正文、四文件位置/摘要和来源 identity。活动 prepare 核对同一 ticker/version、正文与投影一致、必要来源 pin 和本地可读性，再走原 CAS 与真实 consumer ACK。不能放宽旧 decoder 后把 v5 数据塞进 v4 的名字里。

保留旧表/旧 codec 和旧活动集合；新对象不能伪装成旧 PolicySet 写回。SQLite 之外仓库接口的既有实现，以及 Runtime/读模型目前绕过接口直接构造 SQLite repo 的路径，也必须纳入实现检查，不能只修一个存取方法就宣称跨后端可用。本轮没有验证任何远端存储。

PARTIAL、零 Policy 的发布语义遵循冻结方案；不增加 COMPLETE-only 全局门槛。发布失败保留旧活动指针。带旧消费者的 ACK 不能成为新合同已可消费的假阳性。

### 4.3 W2 输入与输出：R3 已接通，新 Policy 合同仍未接通

**更新后的现状。** `persistent_runtime_v2/providers.py` 的 decision 仍读取 `policy.decision`；`service.py:94–140` 的 R1 仍是旧 criterion 数组投影，R2 手工构造 payload，遗漏新 decision、trigger_layer、transmission。另一方面，`schema.py:379–413` 已有新 `W2PolicyAssessmentResult` 和 `W2NovelFactAlignmentResult`：HIT 用 `hit_condition_ids`，RELATED 用 `matched_condition_ids`；旧 W2 的同名 matched 字段仍表示旧 HIT，由兼容入口转换。新 R3、标签、继承链和无一轮直接交易 Router 已实现；不能再计划重做这一部分。

最终执行仍在 `service.py:_apply_w3_result/_claim_first_policy` 从 W3 返回的排序 Policy 列表取首个可用项、读取顶层方向。当前 W3 的 matched 字段依旧是 HIT 归因，不能与新 W2 RELATED 混用。

**后果。** 即使放宽 JSON 解析，仍会缺方向、漏掉业务研究信息，或者错误依靠列表顺序选方向。直接平铺成旧 criterion[] 还会触发旧“criterion 文本必须唯一”的校验，丢失不同方向/校准下的合法区分。

**适配。** 建立独立的 v5 运行模型和新详情 codec。R1 保留中性召回信息；R2 获得召回 Policy 的完整条件对象与 calibration、transmission，来源 ref 按实际研究需要提供，不恢复旧 Gap 强制约束。版本摘要、hash 等审计字段仍由宿主保存，不必全部塞进模型输入。

新结果必须保留所有真实命中 `(policy_id, condition_id)`。最终执行选择放在 W3，明确到 Condition，执行方向由版本固定的该 Condition 取得；W2/R3 不增加一次交易选择。校验最终选择属于有效命中集合、引用存在且 revision 一致；没有有效选择不能默认为 LONG，也不能静默执行首个 Condition。R2 的空归因保持可记录，不因空归因增加全轮业务重试；技术执行身份只在真正输出交易时要求完整。旧 case 继续用旧结果 codec。

用户本轮进一步确定：R1 每条仅输入 `policy_id/title/match_scope`，不再发 criterion 数组，也不发送完整条件；R2/W3 使用完整原生 v3 Policy。底层 v5 投影仍保存完整 Condition，不能为缩短 R1 而裁掉规范投影。

当前候选上限、W1 新颖性判断、新闻路由与模型调用边界不因本次合同适配顺带重构。R1 的中性召回不应因部分 Condition 已消费而直接隐藏整个 Policy；R2 仍需可看完整条件及可执行状态，避免丢失双向关系，但已消费项不能再次被执行。

### 4.4 一次性消费、Trade 与并发去重：必须改到事务边界

**更新后的现状。** `policy_consumption.py` 明确为 `POLICY_AUTO_FREEZE_ENABLED=False`。service、W3 context、TradeOutputService 和读模型均使用该共享值：关闭时不按历史消费过滤、不写新消费 claim，但 Case/intent、日期、event trade、实时/CLOSED 去重仍在。保留的旧 claim 仍以 `(ticker, policy_id, activation_revision)` 为单位，Trade 仍无明确执行 Condition revision/使用代次。重新把开关设成 True 只会恢复旧整 Policy revision 锁，不能实现本轮需求。

旧 activation_revision 聚合整条 Policy 的 criterion/calibration；新 condition_revision 的字段集合及长度又不同，不能继续塞进 `ar_<24hex>` 字段。

**已确认适配。** 消费身份改为 `(ticker, policy_id, direction)`，使用代次与规则 revision 分离。成功接纳 READY 时消费该方向全部 Condition，包括后续新增/修改条件；反方向保持独立资格。Trade/candidate 记录实际执行 Condition/revision 和 usage_generation，但消费锁不以 Condition/revision 为键。只有整 Policy MAINTAIN 重新研究、完整 upsert、显式 rearm 请求及成功激活才可解除本次实际看到的消费 ID；普通改稿/升版/跨日不解冻。

保留当前健康逻辑：REALTIME 在输出时消费；CLOSED 生成候选时不消费，最终选择释放时再消费。保留 event trade 去重和闭市释放针对已有 realtime 同 ticker/day/direction 的约束，不因为粒度迁移额外删除它们。历史旧 claim 不猜分配到新 Condition；旧版本回放仍按旧身份，新研究集合按新身份运行。

以上三项问答已全部确认，见第 9.1 节。实施时恢复共享冻结策略，不能把开关暂停期的所有输出补造成消费记录；本轮尚未修改开关。

### 4.5 W3、冻结 case 与闭市候选选择：三个入口一起改

**现状。** `persistent_runtime_v2/w3.py:54–60,141–179` 硬解析旧 D2 和旧 PolicySet，随后整 Policy 过滤已消费项。传新 D2 会直接报 `w3_document2_invalid`，不是“模型理解得不好”。W3PolicyResult 同样依赖 Policy 命中和可选 Condition 归属。

`service.py:463`、`coordinator.py:485–491` 会将已冻结投影硬解为旧 RuntimePolicyProjection。因此启动入口修好后，中断恢复/闭市 sweep 仍可能失败。`selection.py:85–155` 一方面加载 W3 上下文，一方面释放前检查旧整 Policy activation_revision；两处都需要适配。

**适配。** D2/D3/投影按显式 schema 解码；case、轮次输入、sweep 与候选保存实际合同身份，恢复时不按“当前最新”重解旧 payload。W3 仍读取 case pin 指定的 D1/D2/Event/D3；缺失时明确报错，不偷偷回退到最新 run。保持缓存的来源版本区分；新旧工作区/线程上下文不能因 ticker 相同串用。

W3 执行结果也明确命中和最终 Condition 选择，并允许保留真实 HIT 但明确不交易，才能支持 WATCH。闭市 selector 继续在冻结候选中明确选 candidate_id；释放时核对当前活动集合中选中 Condition 的 revision、方向和 usage_generation。重授权不得替旧候选刷新代次；兄弟 Condition 的改动不应凭整 Policy 聚合 hash 自动废弃一个未变化的候选，选中 Condition 被修改/退役则不能照旧释放。

W3 当前 D1 上下文主要是 aggregate document。新 Future Nodes/Entity Map 若未完整进入 aggregate，可按已固定 D1 run 补可读取的独立产品清单/文件；不读取其他 run 最新产品，也不凭本次合同迁移要求重做 D1。

### 4.6 日常 O3 MAINTAIN：节点可复用，调用链尚未接通

**现状。** v2.1 已有单 MAINTAIN 及 patch、来源冻结、局部接受、退役、NOOP/staged 恢复能力。它目前要求显式 staged v3 base（`orchestrator_v21.py:1393–1453`）。但 `persistent_runtime_v2/maintenance.py:325–376` 仍构建默认旧 O3，传旧 `cutoff_at/candidate_publication` 参数，并通过 `_agent._prompt_root` 注入旧 runner 资产。v2.1 maintain 要求 as_of，既不接受这些旧参数，也不以旧 `_agent` 私有属性加载资产。

**适配。** 由活动 base 的实际合同选择维护器，将正式候选/活动 v3 base 通过统一只读版本入口交给现有单 MAINTAIN，不为新版本另建多节点维护流。冻结 base JSON/coverage/来源、feed、Event delta、版本对应的执行资产；复用新版资产加载入口，不能仅改旧私有字段。

维护结果经过新正式 release 接入后，仍走现有 Event + Policy 引用的联合活动切换，保留 D1/D2/O4 configuration 引用。NOOP 保持原 D3 run/version；metadata-only 合法版本不得丢失新 Event 来源。完成记录消费必须跟随成功发布/激活，不能 MAINTAIN 模型返回成功就先标记 feed 已处理。

维护 feed 应带明确的执行 Condition/revision、双方命中证据和相应 Trade/badcase/gap，并新增独立持久读取的 `policy_consumption`；不能只传 Policy ID 后要求 agent 猜是哪一方向。消费状态不跟随当日 Trade processed 消失，未重新授权的冻结项继续进入后续维护。已确定维持闭市周期，不新增逐笔交易即时维护。

### 4.7 O4 / Message Bus：传输大体兼容，入口和行为上下文需对齐

`codex_monitoring_o4/orchestrator.py:65–92,419–430` configure 接收通用 dict，冻结 PolicySet SHA 并检查 source_needs 的 policy_ids；没有硬读旧 Policy.decision。`schema.py:145–205` SourceNeed 仍关联 Policy ID，作为披露来源的聚合范围是合理的，不必为每个 Condition 强造独立 SourceNeed。

需要修的是新 D3 正式候选交付路径、版本/hash 身份与实际 O4 上下文，使其能读取中性范围、transmission 和全部条件。JSON 可以传进去不代表旧提示词已经理解新业务；后续 prompt/skill 任务需要同步这一点，本轮不改提示词。

保持初始化显式执行 O4；`codex_monitoring_o4/integration.py` 的旧 hook 为 NOOP，不因本轮恢复成每次 D3 维护都触发 O4 重配置。现行 Message Bus 抓取、正文补全、监控配置应用不是 D2/D3 schema 消费者，不需要无故重写。

### 4.8 读模型 / API / 前端：不能留到上线后才发现看不了

`v2_read/artifacts.py:254–332` 硬读旧 D2，并依赖 shell_id/expectation_id、core_question/boundary_rule 与旧 Unit 校验。`v2_read/formal.py:207–304` 硬读旧 D3 published handoff/PolicySet、必需 document2_ref、source_refs 来源树及单 direction。`v2_read/lifecycle.py`、`policy_hits.py` 的状态与命中统计按整 Policy activation_revision 聚合。

`api_v2/wire_schema.json` 与 `frontend/v2/src/core/wire-schema.json` 固化这些结构；`frontend/v2/src/pages/expectations.tsx` 依赖旧 ID/Gap 字段，`strategy.tsx:408–451` 在 Policy 顶部展示单方向，Condition 不展示方向/层级，并生成旧 Gap 深链接。仅改后端 parser 会继续在严格 wire 校验或页面渲染处失败。

**适配。**

- D2 新视图按新内容展示，不造旧 proposition/Gap。UI 导航用 run 内 JSON pointer/数组位置生成表示层定位 ID；保留名称，区分它和研究业务 ID，不宣称跨 run 稳定。失败 outcome 的 seed/名称保留，不能丢掉失败 Shell。
- D3 展示中性 Policy 范围、transmission、各 Condition 方向/层级/校准，以及共享的方向消费状态。同方向条件不得展示成各自独立交易资格；Policy 层可同时表达 LONG 冻结、SHORT 可用。
- `ref[]` 按真实来源展示；有可解析的固定来源才生成导航，无法解析则保留原文引用。不能正则猜出 D2 Gap，不能用活动 D2 冒充原来源。document2_ref 可空时仍可查看 Policy。
- 同步正式产物索引、下载、版本历史、activation 页、case detail、命中记录和消费统计；新 staged/candidate 的可查看状态与 active 明确区分。
- 按仓库现有契约生成/同步流程更新双份 wire schema；历史旧详情继续原样读，不用新页面逻辑反向改旧存档。

### 4.9 内部节点重跑与旧结果回放

`ticker_initialization/internal_adapter.py:182–214` 仍通过旧 Document2Document 和旧 D3 `_agent.run`/阶段模型恢复。`substeps.py` 的耐久包装可以复用，但不表示 v2.1 owner/task 已纳入内部节点重跑。

需要把 v2.1 实际 owner、phase、wave、checkpoint 与执行资产身份接入现有独立 rerun/receipt；保留“重跑子任务不隐式发布父 run”的边界。不能把 Discovery 强行命名为旧 Stage A 后套用旧输入模型。旧回放与新执行按已冻结合同各自解码，不能复用当前最新 prompt/投影解释历史结果。

## 5. 隔离探针与证据强度

使用本地 `.venv/Scripts/python.exe`，新建最小合法对象及临时 SQLite，没有读取/修改正式库，没有启动模型。成功运行得到：

| 探针 | 实际结果 | 含义 |
|---|---|---|
| 合法空 Shell 的 D2 v2.1 → 旧 Document2Document | ValidationError | 即使没有复杂内容也因版本合同失败 |
| 合法中性双向 PolicySetV3 → 旧 PolicySet | ValidationError，12 项 | 不是只补一个方向字段即可兼容 |
| 原生 v5 projection → 活动 upgrade_runtime_projection | unsupported runtime projection v5 | 活动 decoder 明确不接受 |
| 两个不同方向 Condition 使用相同 criterion → 旧投影平铺 | criterion 唯一性 ValidationError | 强转旧 criterion[] 不是无损适配 |
| 写 staged v3 后分别读取 | staged=True；旧 get_version=False；current projection=False | staged 隔离实际生效，不是缺少某个调用参数 |
| 旧 W2PolicyResult 只有命中 Policy、无 Condition | 正常接受，matched_condition_ids=[] | 新方向归属要求尚未进入校验 |
| v2.1 initialize / maintain 函数签名 | 无旧 candidate_publication/enqueue_o4；maintain 必需 as_of | 直接切 builder 会造成调用不兼容 |

代码扫描也检查了 `Document2Document/PolicySet/RuntimePolicyProjection/policy.decision/source_refs/staged_v3/condition_revision` 的相关引用。上述探针是机制证据，不是端到端验收；本轮未运行全套回归或真实模型 Pilot。

探针初次执行修正了类名引用，随后临时 SQLite 的 GC 延迟关闭导致 Windows 临时目录清理失败；显式回收连接后完整运行成功。该清理现象不计入下游业务缺陷，也不据此扩展代码修复范围。

## 6. 建议的落地组织与验收

适配应是一条完整交付链，分步骤实施、保留旧活动版本，不能分别上线几个 parser 后让新集合进入交易：

1. **统一版本解码与正式候选 release 接口。** D2/D3/Projection 新旧 codec、候选身份/文件/摘要、来源 pin；打通版本读取和历史，不改变 producer 业务 schema。
2. **Condition 执行选择与方向消费模型。** 按第 9.1 节已确认答案同步 W2/W3 结果、provider、Trade/candidate、claim、事务与旧结果兼容。
3. **冻结恢复与闭市释放。** 新 case/sweep/候选从冻结输入恢复，选中 Condition 的身份与活动有效性检查。
4. **正式初始化与单 MAINTAIN 接入。** 显式版本选择、资产冻结、签名/receipt 适配、candidate → activation → consumer ACK，保持原子性与 NOOP。
5. **O4 输入、读模型/API/前端和内部重跑。** 补齐运行、观察、维护、故障恢复的使用闭环。
6. **隔离库验证后再做完整串行 MU 验证。** 本轮不启动验证运行或上线。

至少需要覆盖以下有实际区别的场景：

- 旧活动集合与新候选并存；新初始化中断恢复；候选失败不污染 active。
- 中性 Policy 的 LONG/SHORT 双命中保留；最终明确选择一个 Condition，不从数组首位推方向。
- 执行一个入口不消费反方向；同时命中多个入口不造成重复 Trade；并发争用在输出事务中只有一个成功。
- 新版本未改变 Condition 时不重置已消费状态；改动/退役选中入口后旧 CLOSED 候选拒绝释放；兄弟入口修改不会无理由废弃未变入口。
- PARTIAL 有可执行条件可发布；失败空输出保留旧 active；明确全退役可合法发布空集。
- v2.1 MAINTAIN 的局部修改、metadata-only、NOOP、失败恢复及 Event + Policy 联合切换；不是只验 patch 可解析。
- W3 读取固定新 D2、新 D3、正确 D1 和 Event；缺 pin 不偷换最新来源；旧 case/replay 继续可读。
- O4 configure 的真实候选正文/hash；策略页、预期页、来源导航、下载、生命周期及 case 命中能表达新结构。
- activation.prepare 成功后真实 Runtime admission 能读 v5，而非只证明 Worker 端口可用。

## 7. 我已自行确定、不需要占用问答的细节

- 不改 D2/D3 已冻结业务结构来迁就旧消费者；不补回旧业务 ID、不恢复顶层 decision、不补造反向条件。
- 保留旧合同的历史读取与既有活动版本；按快照版本选择 codec/维护器，不全局拿新 parser 重解所有存档。
- 保留现有 Event Library、Message Bus、W1、O4 SourceNeed 聚合、candidate/activation/CAS/ACK 和交易输出事务能力。
- 保留 CLOSED 候选的显式最终选择，不把候选生成当消费；不恢复每次维护自动跑 O4。
- 不因同一 Policy 下其他 Condition 改动自动让未变 Condition 重新可交易；不靠 trigger_layer 排序执行。
- 新 D2 导航 ID 只在表示层生成；无法准确定位的 ref 原文可见，不编造深链接。
- 下一阶段需给 prompt/skill 会话同步新运行上下文与输出选择合同，但本轮不修改提示词，也不能宣称代码适配会自动改变旧 agent 行为。

## 8. 首轮问答存档（已由第 9 节 B / B / A 答复取代）

以下仅保留首轮提问与当时建议，已不是待决策项。最终采用第 9 节用户确认；A/A 的原建议不进入实施方案。

### Q1. 同 Policy、同方向的多个独立入口，消费边界是什么？

例如同一 Policy 有 LONG-C1、LONG-C2、SHORT-C3，最终通过 C1 输出交易。冻结方案已排除三者整池消费，但没有精确规定 C2。

- **A（建议）：只消费实际最终选中的 C1 revision。** C2/C3 仍须通过当前事实与 calibration 判断才可执行；同一 case 多命中只输出最终选中的一个，不同时开多笔。
- B：消费该 Policy 当前全部 LONG Conditions；SHORT 保留独立判断。

A 更符合“独立充分替代入口”的最小身份，但不同消息可能通过同方向另一入口再次形成交易；B 更接近同一方向一次性机会，却会丢掉独立入口。这是业务选择，不能靠数据库主键偶然决定。

### Q2. 已消费 Condition 的 revision 改变，是否自动重新可用？

现有新投影 hash 包含 decision、trigger_layer、criterion、calibration；连措辞或层级调整都可能改变 revision。标题/ref/transmission/match_scope 改动则不会改变它。

- **A（建议）：沿用当前确定性 revision 作为重新可用边界。** 同 ID 同 revision 不重置；上述字段产生新 revision 则可重新评估/执行。维护方必须认识到改这些字段会重新打开入口，不能把纯润色当无影响操作。
- B：revision 改变也不自动重开，另需 MAINTAIN 显式“重新启用”处置和宿主状态。

A 不增加语义 diff 引擎或新业务状态；代价是纯文本变化也可能重新打开交易机会。B 对重开更可控，但要增加明确维护输出/receipt。无论哪项，都不能通过兄弟 Condition 改动或 PolicySet 版本号增长重开未变入口。

### Q3. 一次交易后，兄弟条件的基线复核何时调度？

冻结方案要求受影响条件重新校准，但当前正式维护是闭市 sweep 驱动，没有每笔交易立即维护的支线。

- **A（建议）：保持闭市维护周期。** 交易与命中信息进入既有 feed；期间每条新消息的 R2/W3 都必须以完整 calibration 和最新事实判断有效性，不能把未消费等同于仍成立；闭市 MAINTAIN 处理实际受影响范围。不按 Policy 归属自动冻结全部兄弟条件。
- B：交易后增加一次即时受影响范围的维护/校准调度，在完成前限制相应入口继续使用。

A 复用既有工作流，不增加每笔交易后的研究成本；B 缩短基线校准滞后，但新增执行、去重和活动版本竞争。不能把“成交”无条件视为所有兄弟条件基线失效，也不能把一次研究的 calibration 视为永久有效。

## 9. 问答已确认及另一会话实现复核

### 9.1 已确定：B / B / A

用户于 2026-10-10 确认：

1. **恢复 READY 即冻结；按 Policy × 方向消费。** LONG-C1 被接纳 READY 后全部 LONG 冻结，SHORT 仍可召回、判断和消费；CLOSED 候选生成不消费，最终释放 READY 才消费，未成交不自动解冻。
2. **改规则不解冻；整 Policy 显式重新授权。** 以 [按方向消费与整 Policy 重新授权](d3_policy_direction_consumption_rearm_requirements_20261010.md) 为准。完整 upsert + 合法 rearm_requests + 成功激活才解除维护实际看到、仍对应同一消费 ID 的方向；未消费方向不重置，维护期间新消费不被旧请求解开。
3. **保留闭市维护周期。** 不新增每笔交易后的即时 MAINTAIN；未消费方向仍需基于完整 calibration 判断有效性。

规则 revision 只验证规则快照，usage_generation 单独管理交易资格。

### 9.2 当前已实现的部分

对照 `workflow_v2/persistent_runtime_w2r3_orchestration_implementation_plan_20261010.md` 及当前源码，已确认：

| 能力 | 代码证据与当前状态 |
|---|---|
| HIT / RELATED 分离 | schema.py 的 W2PolicyAssessmentResult；两个严格 boolean、两组归因，旧结果经 normalize_w2_result 转换 |
| W2 R3 | service.py:_run_w2_r3；输入仅 w1_r2_reason，inherit_context=True，父为成功 W2 R2 |
| 标签 / 无一轮直接交易 | router.py:first_round_tags/route_runtime_case；W3 为命中研判入口，原始 W1 不被覆盖 |
| 两条继承链 / 父失效恢复 | service.py:_call_with_retry、transport.py；冻结输入与恢复 receipt 已有实现 |
| W3 前置证据 | w3.py 的 original_w2 保留旧 HIT 视图，另发完整 first_round_policy_assessment、alignment 与标签 |
| 暂停自动冻结 | policy_consumption.py=False，service/W3/trade_output/read 共用；历史 ledger 保留 |
| 读侧与图 | 当前未提交代码已含标签筛选、R3 详情/导出、W1-R3 候选和图调整 |

本次实际运行：`.venv/Scripts/python.exe -m pytest tests/test_runtime_w2r3_orchestration.py tests/v2_backend/test_w2r3_reads.py -q --tb=short`，**21 passed in 9.07s**。这是本轮定向验证；另一会话 acceptance 中更广回归、真实 API、浏览器和“未部署”的记录为既有记录，本轮没有重新执行或验证部署。

### 9.3 仍未实现，不能从 R3 已完成推断的部分

- 新 D2 / D3 / v5 投影的正式候选发布、活动读取和完整详情适配仍未接通。
- W2 R1 仍发旧 criterion 投影，R2 仍手工省掉新字段；R3 已实现不代表它的父轮已经获得 v3 全文。
- W3 最终 NEW+HIT 仍禁止 expert_trade 判断，并直接按旧 Policy 方向落地；没有独立“命中但暂不交易”的最终选择合同。
- 新方向状态、usage_generation、消费 ID、维护持久 feed、rearm 请求/接受 receipt/激活应用均未实现。
- schema.py、service.py、router.py、w3.py、trade_output.py 中没有 Policy WATCH 动作/存储/跟进逻辑；WATCH 方案目前是待实现。
- 正式单 MAINTAIN 仍走旧调用签名；feed_has_work 当前也不检查 policy_consumption。

### 9.4 合并方案时必须处理的冲突

1. **WATCH 与当前 W3 强制交易冲突。** NEW+HIT 时若缺少交易价值/证据，仍必须保留 HIT；不能为了 SET WATCH 假报未命中。下一阶段最小新增最终 Policy 执行选择，允许显式不交易，并继续保留 NEW 无 Policy 的专家研究要求。
2. **WATCH 跟进与 Mode 1 冲突。** OLD+RELATED 因 WATCH 进入 W3 后不能套用“必须 NEW、不得命中 Policy”的 UNCOVERED_NEW 校验；WATCH 跟进使用 REVALIDATE_THEN_EVALUATE。
3. **内容身份与消费身份不能混用。** condition_revision 验证选中规则，direction + usage_generation 决定能否再次输出；换条件/改稿不解除同方向冻结。
4. **WATCH 清理与方向冻结协同。** 消费任一方向同事务取消该 Policy WATCH，但不消费反方向；此前已冻结 LONG 不等于之后 SHORT 不能建立合法 WATCH。全方向不可用时不新增 WATCH，迟到 SET 不得在本 case 之后的消费后重新挂回。
5. **激活不是跨库事务。** 发布接受 rearm 不等于授权已应用。现有 control DB 切指针、Runtime DB 写状态分开，必须复用 admission/receipt 做幂等收敛；ACK 和实际输出检查本地已应用授权，不能宣称两库一次提交。
6. **版本恢复不能临时换合同。** 当前 W2R3 revision=2、旧 W3 schema 与在途 turn 已冻结；新增 W3 选择/WATCH 需要新执行合同和包，旧成功输出不能补执行 WATCH/rearm。
7. **消费过滤不能改写事实判断。** `_run_w2_hot` 当前在返回后过滤已消费 HIT，并可能把整个 HIT/RELATED 结果置 false。恢复消费后，这可能因一个 HIT 的竞态消费抹掉其他 Policy 的 RELATED。新路径应保留固定输入下的判断，资格与最终执行另查；不能让一次消费改变 R3/WATCH 的证据含义。

以上已合并入新的统一实施方案，不再把 W2R3 已完成代码当作待重建项目。本轮只更新文档，不覆盖工作区另一会话的源码、测试、提示词和 changelog 改动。
