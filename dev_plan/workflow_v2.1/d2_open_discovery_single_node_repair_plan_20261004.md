# Document2 Open Discovery 单节点修复：勘察与实施方案

日期：2026-10-04（Asia/Shanghai）。状态：R1–R8 已完成开发及离线合同验收，组合回归 168 passed。见[实现与验收](d2_open_discovery_single_node_implementation_acceptance_20261004.md)。下文“当前现状”保留为修复前勘察证据；本方案的目标口径已落实。

本方案修正 2026-10-03 勘察、实施方案和实现中对 Open Discovery 的错误拆分，作为该部分后续开发依据。其余 D2 v2.1 已完成的局部重构继续沿用，不重新设计 O0、后四个 O1 阶段或发布流程。

## 1. 结论和需求解释

当前不对齐确实存在，而且涉及实际调用、持久化和 Pilot 排程。当前每个 Shell 的正常 O1 路径是六个独立节点执行：Scan、Selection、State、Realization、Gaps、Finalization；用户要求的是五个阶段、五个节点执行。

根因也在上一轮方案中：旧勘察 §6.1 和旧实施方案 §2 明确写成了“六次模型调用”“Scan/Selection 不合成一个 Turn”。代码和测试随后照此实现。这是上一轮将内部 Pass 映射成独立技术节点的解释错误，不能只修改页面名称。

原始 `d2_v2.1.md` §16 明确写了“只新增一个业务 Stage”，内部是 `Full Scan → Checkpoint → Selection`。§21 要求 Selection 前冻结 Scan，以保留“想到但没有深入”的候选审计。§22 的“第二个 Turn”与用户本次明确的“一次模型调用”存在措辞歧义；本次澄清优先，后续将其理解为同一次 Open Discovery 执行中的第二个内部 Pass，不再派发第二个 Worker/SDK Turn。

固定修复口径：

- 正常路径每个 Shell 五个 O1 节点，各一次 Worker 执行；Open Discovery 只调用一次 `thread.turn()`。
- Scan 是调用内部真实提交的冻结 checkpoint；Selection 在工具确认提交后使用完整冻结 Scan。
- Scan/Selection 保留各自 typed 过程模型和 sidecar，没有独立调度身份、成功 stage、Pilot case。
- 失败重试属于同一节点的新 attempt，不计为新增业务阶段；如果 Scan 已提交，则重试直接从 Selection 恢复。
- “一次调用”指一次节点级 Agent/SDK Turn。工具交互会涉及底层推理请求，不能承诺只发生一次模型 HTTP 请求。当前代码本来就是 Agent Turn 运行方式。

最后一项是技术边界：如果把“一次”定义成严格一次无工具的推理 HTTP 请求，就无法同时做到程序在 Selection 前接收、校验并冻结 Scan。此方案采用一个 SDK Turn 内的 checkpoint 工具交互，满足业务节点合并与中间冻结这两个要求。

## 2. 当前代码勘察

以下行号来自本次工作区，后续实施可能移动。

| 链路 | 现状与证据 | 修复影响 |
| --- | --- | --- |
| 原需求 | `dev_plan/workflow_v2.1/d2_v2.1.md:900` 五阶段；`:1103` Scan checkpoint；`:1145` Selection | 五阶段和先冻结后筛选同时保留 |
| 节点身份 | `src/doxagent/codex_runtime/schema.py:78`、`:79` 分别登记 `d2_o1_discovery_scan` / `d2_o1_discovery_selection` | 新执行仅用 `d2_o1_open_discovery` |
| Data 权限 | `src/doxagent/data_runtime/policy.py:81`、`:82` 两节点映射现有 O1 权限 | 单节点沿用同一 O1 权限，不扩大 Data MCP |
| Stage 和过程模型 | `src/doxagent/workflows/codex_document2/schema.py:379`、`:394` 两过程模型；`:445` stage 枚举；`:535` ShellRunState | 过程模型继续用，新增单 stage 和聚合成功结果 |
| 主调度 | `src/doxagent/workflows/codex_document2/orchestrator.py:913` 六项 stages；`:999` 第二个节点注入 Scan；`:1027` 每项分别调用 Runner | 将前两项替换为一个 Open Discovery |
| 真实冻结时点 | 同文件 `:1051` 第一项 Worker 完成后写 Scan；下一项 Worker 再做 Selection | 当前冻结真实有效，但依赖两次调用；合并后必须把冻结移入调用内部 |
| Worker 交接 | `src/doxagent/workflows/codex_document2/runner.py:93` 每次生成 NodeAttempt/WorkerRunRequest；`:175` 等待 Worker；随后才解析 final_response | 仅增加聚合最终 JSON 无法产生中间 checkpoint |
| SDK 能力 | `src/doxagent/codex_worker/sdk_runtime.py:212` 构造 MCP 配置；`:414` 一次 `thread.turn()`；工具事件落 sdk_loop 审计 | 可在单 Turn 内增加 D2 专属 checkpoint 工具，无需新调度器 |
| 冻结存储 | `src/doxagent/codex_worker/workspace_store.py:111` write_text；`:352` context/ 与 attempt/input 不可变 | 复用原有原子写入、路径约束和同字节幂等；不改共享 Store |
| 重试 | `orchestrator.py:1212` 当前重试始终传入同一 context | 首次调用提交 Scan 后失败，下一次必须重新读取 checkpoint 生成显式恢复 context |
| 成功恢复 | `orchestrator.py:1046` 先存 raw output 指针再做 sidecar 效果；入口按 Shell 查找已成功工件 | 合并后仍须保存一份包含 Scan/Selection 的聚合 raw output，不能只存最终 Selection |
| durable codec | `src/doxagent/ticker_initialization/substeps.py:170` `_D2Codec` 按传入 output_model 解码；`:326` 按成功工件恢复 | Runner 的持久化 output_model 应是聚合模型；无需修改通用 codec |
| 引用来源 | `runner.py:327` citation promotion；`:424` O# → attempt 限定 D2REF；`observations/promotion.py:16` 仅识别 cite 标记 | 冻结 Scan 必须保留首次生产 attempt 的引用来源，不能重试时全部重新限定 |
| Pinned 复用 | `pinned_runner.py:37` v2.1 ID 绑定 schema/cutoff，目前没有 Discovery 执行契约区分 | 防止新单节点运行直接复用旧六节点结果 |
| Pilot 调度 | `pilot/document2_coordinator.py:695` 注入两个节点；`:126` override 从 Scan 起跑 | 替换为一个节点，完整单 Shell 计划从 15 项降为 14 项 |
| Pilot 接边 | `pilot/document2_case_builder.py:87` 可选节点集；`:538` 两个 turn；`:549` 从两个上游 completion 读 sidecar；`:1077` 两资产合同 | 一个 Discovery case 内冻结 Scan；后四阶段从同一 case 提取两过程对象 |
| Pilot 完成判断 | `document2_coordinator.py:148` 检查完成后推进；`:501` 当前只确认 completion.json 可解析 | Discovery 必须同时核对 checkpoint 和 Selection，不能仅凭有 completion 文件推进 State |
| Pilot 工具模板 | `pilot/templates.py:12` render_config 供多个 workflow 使用；`:22` control_root 绑定 case | 新工具配置仅在 D2 新节点启用，不改变其他 Pilot |
| 错误验收目标 | `tests/test_codex_document2_v21_orchestration.py:446` six_rounds；`:451`、`:452` 分别断言 Scan/Selection 次数；`:456` 六份 stage_outputs；`:849` 15 节点 | 改验收断言，也增加真实调用中冻结/中断恢复测试 |

### 2.1 本次验证结果

本次复跑：

```powershell
uv run pytest tests/test_codex_document2_v21_orchestration.py tests/test_codex_document2_workflow.py tests/test_document2_pilot_coordinator.py -q
```

结果：**65 passed，3 warnings，67.92s**。它证明当前拆分实现的既有离线基线通过，不能证明五阶段要求已满足。旧验收报告的 74 项还包含 initialization durable 回归，是旧实现的历史结果，本次没有将其当成新修复验收。

工作区已有上一轮 D2 修改和其他模块修改；本次仅新增/修订设计文档及 changelog，不覆盖这些代码，不调用真实模型、不部署。

## 3. 修改范围和最终流程

```text
既有 O0 Finalization
  ↓
每个 Shell：
  OPEN_DISCOVERY  [一个 NodeAttempt / WorkerRunRequest / SDK Turn，正常无重试]
    Full Scan
      → commit_open_discovery_scan 工具：校验、冻结、返回 Scan + SHA
      → Selection：逐个冻结 Candidate 做 DEEPEN/MERGE/PARK
      → 返回 Selection + checkpoint SHA
  STATE
  REALIZATION
  GAPS
  FINALIZATION
  ↓
既有 Assembler / staged Publish
```

Shell 之间保留现有 semaphore 并行，Shell 内五阶段串行。Open Discovery 仍由 O1、同一模型配置和同一预算机制完成，不派发内部子 Agent 来充当另一轮 Selection。

变更限制：D2 编排/schema/恢复/Pilot，加上 Worker SDK 配置中只对新 D2 节点生效的一处工具接入。此处共享文件改动服务于 D2 调用内部交接，不是重做 Runtime；不修改 Worker HTTP 协议、通用 durable codec、WorkspaceStore、Repository、Data MCP 或 Source Capture 服务。

保留 v2 默认与 v2.1 staged/non-current 边界，Published D2 正式 schema 仍是 `document2.v2.1`。O0、State/Realization/Gaps/Finalization 的研究业务、Late Additions/Resolution、O3/W3/Read/API/前端、初始化调度及部署均不扩展。

不修改研究 prompt/skill 文件。checkpoint 的工具使用与交付格式属于机械执行合同，通过工具说明、attempt task.json、Pilot 任务模板交付，不把经济研究方法加入 Python 提示语。合并后的研究 skill 仍由后续资产轮次提供，真实研究验收和启用仍另轮。

## 4. 具体实现设计

### 4.1 一个活跃节点、一个 stage

新增 `CodexD2Node.O1_OPEN_DISCOVERY = "d2_o1_open_discovery"` 和 `ShellResearchStage.OPEN_DISCOVERY = "OPEN_DISCOVERY"`。新编排列表仅为：

```text
O1_OPEN_DISCOVERY / O1_STATE / O1_REALIZATION / O1_GAPS / O1_FINALIZATION
```

原两个枚举 wire 值及过程 class 保留供历史记录解码，并标注 legacy；不得再进入新运行的 stage 列表、Pilot 可执行节点集、override 起始节点或新派发路径。不能因为枚举中留有历史值而统计成七个活跃阶段，也不能把旧 Scan 记录直接改名为新节点。

新节点映射已有 O1 Data 权限集合；旧值只承担历史解析，不能作为创建新版执行的合法入口。`failed_stage` 对 Scan、checkpoint 提交、Selection 任一失败统一报 OPEN_DISCOVERY；错误码/消息可区分内部 Pass，无需另建一套业务 stage 状态机。

### 4.2 两个过程对象、一份聚合成功结果

保留 `OpenDiscoveryScanV21`、`OpenDiscoverySelectionV21`，增加三个小模型：

```text
OpenDiscoveryCompletionV21                 # 模型最终响应 / Pilot completion.json
  scan_sha256: str                         # 64 位 SHA256
  selection: OpenDiscoverySelectionV21

OpenDiscoveryCheckpointV21                 # 工具生成，不由最终响应冒充
  schema_version: "d2-open-discovery-checkpoint-v1"
  discovery_contract_version: "single-v1"
  workspace_run_id: str
  shell: str
  research_cutoff_at: datetime
  seed_sha256: str                         # 本次冻结 Shell seed 的稳定摘要
  producer_attempt_id: str
  scan_sha256: str
  scan: OpenDiscoveryScanV21

OpenDiscoveryResultV21                     # Runner 的成功结果与 durable raw artifact
  checkpoint: OpenDiscoveryCheckpointV21
  selection: OpenDiscoverySelectionV21
```

模型最终响应不重新生成 Scan；Scan 来自已提交 checkpoint。Runner 传给 durable 装饰器的 `output_model` 是 `OpenDiscoveryResultV21`，仅在新节点内将 Worker 的 output_schema 和最终响应解析模型选择为 `OpenDiscoveryCompletionV21`。最终组装成完整 `OpenDiscoveryResultV21` 后，复用现有 snapshot、parent raw artifact、ArtifactRef 和成功 receipt 写入链。

因此 `_D2Codec` 和按哈希 raw artifact 的恢复仍能解出完整成功结果，不需要修改共享 codec，也不会将“一份 Selection 响应”误当成整个 Stage 的持久化结果。

### 4.3 调用内部的真实 checkpoint

新增 D2 自有模块 `src/doxagent/workflows/codex_document2/discovery_checkpoint.py`，承载 checkpoint 小服务、读取/校验/序列化 helper 和 stdio MCP 入口。对外只暴露一个工具：

```text
commit_open_discovery_scan(scan: OpenDiscoveryScanV21)
  → {scan, scan_sha256, checkpoint_path, producer_attempt_id}
```

工具仅负责冻结过程数据，不生成、筛选候选，也不访问研究数据库。

绑定与提交步骤：

1. 启动时从固定 env 获取 run/attempt/Pilot case 标识，从该 attempt 的不可变 task/context 读取 node、Shell seed、cutoff；核对节点确为新 Discovery。工具调用参数没有任意路径、run_id、shell_id 或覆盖开关。
2. 使用现有 Scan validator 核对 Shell、Unit 覆盖和候选名称身份；不强制候选数量或研究结论。
3. 冻结前用已有别名限定规则将当前本地 O# 固定到 **生产 Scan 的 attempt**，已有 D1REF/D2REF 保持原样。该限定仅作用于 ref 字段，不改变 Shell/Unit/Candidate 的名称；Selection 引用也按相同字段边界处理。之后不修改冻结 Scan 的引用字节。
4. 使用统一确定性序列化 helper：`model_dump(mode="json")` 后 `json.dumps(ensure_ascii=False, sort_keys=True, indent=2) + "\n"`，对 UTF-8 字节计算 Scan SHA。所有初始写入与恢复重建均调用这一 helper。
5. 在 Shell 子 workspace 的 `context/document2/open_discovery_checkpoint.json` 原子保存含 Scan 和生产身份的权威记录，再写 `context/document2/open_discovery_scan.json` 这一原需求命名的冻结文件。工具只有在两文件落盘并核对一致后才返回成功。
6. 同字节提交幂等返回；不同候选集的再次提交拒绝，绝不覆盖。提交服务用一个 D2 局部跨进程文件锁保护“检查已有记录—写入”，复用项目已有 Windows/Unix 文件锁做法，不新增锁框架或数据库。
7. 权威记录已写、Scan 文件未写时，重启从权威记录重建缺失文件；已存在但 SHA 不一致时报告完整性错误，不能覆盖。权威记录损坏也不能自动重扫修复。

两文件不是两笔独立业务事务：checkpoint 记录是唯一提交事实，Scan 文件是其确定性导出。这样既满足原文件要求，也能处理进程在两次写入之间退出的问题。

复用 `LocalWorkspaceStore` 的路径限制和不可变 context 写入，不让模型自行 shell 写文件充当 checkpoint。Store 不可变是程序接口约束，不是抵抗拥有 workspace 写权限的 Agent 的文件系统安全屏障；Runner 必须复核工具提交记录、Scan SHA 和最终响应 SHA，保留 SDK 工具调用状态审计，不宣称普通文件只读足以防篡改。当前 sdk_loop 投影只有工具名称/状态等紧凑事件，没有 Scan 正文或工具返回 SHA；恢复内容必须来自 typed checkpoint，不能假设从 telemetry 还原 Scan，也不为本修复全局保存工具完整 payload。

Pilot 的物理 case_root 与逻辑 run_id 不总相同：服务以实际 cwd 为 workspace 根，按现有 Source Capture 的 case manifest 绑定方式验证逻辑身份，不能错误创建 `case_root.parent / logical_run_id`。普通 Worker 以实际 Shell workspace 为根。同一逻辑提交协议用于两种入口。

SDK 配置只在新 Discovery 节点追加 `python -m doxagent.workflows.codex_document2.discovery_checkpoint` 的 stdio MCP server，enabled_tools 只有上述工具，required=true。其他节点不启动它。沿用现有 env 转发机制，并将新 server 纳入现有相关环境转发名单；不增加 WorkerRunRequest 开关、全局工具权限或新长期服务。

### 4.4 Runner：先承接 checkpoint，再承接最终响应

新节点的执行顺序：

1. 生成一个正常 NodeAttempt，冻结显式输入，执行一次 WorkerRunRequest。
2. 调用内部由工具完成 Scan 提交；Selection 使用工具返回的完整 Scan。attempt task 的机械协议明确这个顺序。
3. Worker 返回、抛错或给出非法最终 JSON 时，Runner 都先检查该 Shell 的 checkpoint。已提交的 Scan 必须仍可保存/读取和恢复，不能因为 Selection 失败而丢弃。
4. 对最终 completion 检查 `scan_sha256` 等于工具冻结 SHA，Selection 用 **checkpoint.scan** 做完整候选覆盖、MERGE 目标和环校验。不能拿模型自述的 Scan 当验证输入。
5. 无 checkpoint 的 Selection 响应是 FORMAT 失败；现存 checkpoint SHA/身份不一致是 SYSTEM 完整性失败；研究输出空 Scan/空 Selection 的合法组合继续允许。
6. 组成聚合成功结果，保存 raw artifact 和成功 receipt。调用未完成时不能提前把 OPEN_DISCOVERY 标为成功。

引用处理必须覆盖 Selection 失败的情况：先读取 checkpoint 的 producer_attempt_id，用现有 read_attempt_observations 取其观察，按 Scan 中实际引用生成临时 cite 标记交给现有 cited-only promotion。限定后的 D2REF 不能直接让只识别 `【cite:O#】` 的 promoter 忽略；也不能在重试时将 Scan 的 O9 指向新 attempt 的 O9。

Scan 的首次生产引用与 Selection 当前 attempt 的新引用分别处理，合并现有 manifests；promotion 失败保持原有非阻断 warning。不能为方便恢复而晋升整个观察清单。checkpoint/结果中的生产身份还应进入 D2 的 attempt_workspaces 记账，使后续发布引用按原来源解析。

### 4.5 主编排：五次调度，内部两类恢复状态

将 `_research_shell_v21` 的前两项合成 `(OPEN_DISCOVERY, O1_OPEN_DISCOVERY, "skills/open-discovery.md", OpenDiscoveryResultV21)`，后四项不变。

`skills/open-discovery.md` 只是合并后的资产合同名称，本轮编排修复不编写其研究内容、不将两个旧 skill 自动拼接。当前 Discovery 研究资产本来就未完成；Runner `_read_asset` 的 Discovery 特殊错误分支需纳入新节点，缺失时继续明确报告 D2_DISCOVERY_ASSET_MISSING，离线 fixture 提供机械合同。后续资产轮次只需提供一个完整 Discovery skill。

完成后从聚合结果拆出 scan/selection，沿用现有父级 Scan 冻结路径、Selection sidecar、artifact key 和后四阶段 context key。过程资产仍不进入 Published Shell。

`stage_outputs` 对新运行只保存五个成功 stage，Open Discovery 是一份聚合成功结果。`discovery_scan_ref`、`discovery_selection_ref` 保留，因为它们表达两个过程资产，而非两个节点。

失败/恢复处理如下：

| 退出位置或已有状态 | 下次行为 | 是否重新调用模型 |
| --- | --- | --- |
| Scan 未提交，节点失败 | 按原失败分类/预算重试 OPEN_DISCOVERY，输入仍是完整发现 context | 允许同节点新 attempt |
| Scan 已提交，Selection 非法或可重试失败 | 保留冻结 Scan；新 attempt 显式注入 Scan/SHA/生产身份，`resume_from="SELECTION"`；禁止再次生成/替换 Scan | 允许同节点新 attempt；不新增 Selection 节点 |
| 进程在 Scan 提交后中断，父 checkpoint 尚未更新 | 先读 Shell 子 workspace checkpoint，再按现有 Worker durable job/receipt 判断复用、等待或失败重试；不能看见 Scan 就并发开启第二个 live Worker | 仅确需新 attempt 时调用 |
| Worker 成功但 Runner 尚未保存聚合结果 | 复用该 Worker 的成功 final_response 与冻结 Scan，完成解析与聚合 | 不重跑 Worker |
| 聚合 raw output 已保存，sidecar 效果中断或 stage 指针丢失 | 按现有 Shell 限定工件查找恢复，校验 SHA，重放 Scan/Selection sidecar | 不调用模型 |
| 成功聚合输出缺失/损坏，或 checkpoint 不一致 | 显式 SYSTEM 错误；不静默重扫，不把旧候选替换掉 | 不自动调用 |

`_run_with_retry` 仅对新节点增加 checkpoint 感知：每次 attempt 前重新读提交状态、生成新 context，而非原样重发初次 context。其他节点仍使用原重试逻辑。Scan 未提交时保持完整发现输入；已提交时显式带上冻结过程资产和 SELECTION 恢复位置。新线程执行策略不变，不依赖旧线程记忆。

在该阶段的成功路径及异常收尾路径，都同步可用的冻结 Scan 到父级现有 `context/document2/discovery/<shell-key>/open_discovery_scan.json` 并保存 sidecar 引用/生产 attempt 记账；调用进行中不要求新增父级轮询器，子 workspace 的提交记录就是中间持久化源。

保持“成功 raw output 指针先保存、效果后应用”的既有顺序。重放成功结果时可以恢复缺失的子 checkpoint/Scan，但已有文件哈希冲突必须报错。checkpoint 必须保存 `producer_attempt_id`，不能重放时改成 Selection 重试的 attempt。

### 4.6 历史执行身份：只补一个 D2 局部标记

在 D2 checkpoint 和 Pilot coordinator/case manifest 记录 `discovery_contract_version`：缺失按旧 `split-v1` 解释；新建 v2.1 执行为 `single-v1`。v2 行为及身份不变。这是 D2 执行契约标记，不升级正式 Document2 业务版本，也不建立通用版本注册系统。

新路径复用 run_id 前必须核对该标记，检查应早于“已有 Published bundle 直接返回”。旧 split-v1 记录继续可解码、可读，不伪装成一次调用成果；旧未完成六节点运行不在本修复中自动迁移或重新标记，需要新 run 执行单节点版。发现不匹配时返回具体的版本绑定错误，不新增用户审批步骤。

Pinned v2.1 identity 在当前 schema/cutoff 基础上增加 `single-v1`；v2 identity 的原字符串、hash 和默认路径保持原样。只防止复用错误执行，不升级 workflow_version、不新建 lane、不清理历史产物。

### 4.7 Pilot：一个 case 内提交 Scan 和完成 Selection

coordinator 在 O0 Finalization 后只插入一个 OPEN_DISCOVERY；State 依赖它，后四阶段的前序依赖和累计 late/resolution 保持原来方式。包含可选 Narrative 槽位的完整计划是 **14 个节点**；跳过该可选槽位时实际执行 **13 个 case**。O1 始终五个 case。旧 v2 的 13 项计划不变。

case builder 和模板具体修改：

- 新节点集、role 映射、turn、override 入口和 bootstrap contract 对齐单节点；只保留一个 Discovery 资产入口。
- 新 Discovery case 的 output_schema/completion.json 用 `OpenDiscoveryCompletionV21`；case 内 checkpoint 工具写出完整冻结记录和 Scan。
- `render_config` 增加仅由新 D2 case 传入的可选配置，默认不追加工具；绑定实际 case_root/run/attempt/case_id。
- `PILOT_TASK.md` 的机械交付约定是“调用工具提交 Scan—收到完整冻结 Scan—完成 Selection—输出 completion”，同一个 case 连续完成，无中途推进 coordinator。
- coordinator 接受 Discovery 完成前校验 completion、checkpoint、Scan SHA 和候选覆盖，并记录由工具/代码组装的 `open_discovery_result.json` 到该 attempt/output。completion JSON 可解析但 checkpoint 缺失时不能标 completed，错误需可读。
- 后续 `_upstream_completions` 对新 Discovery case 读取并验证聚合结果，向后四轮仍提供 `open_discovery_scan` 和 `open_discovery_selection` 两个既有 context key，避免改后四轮的研究合同。
- source-attempt 导出保留冻结 checkpoint/Scan 原字节、来源 attempt 和引用观察审计；bootstrap/retry case 绑定新的当前执行身份，同时保留 Scan 的 producer 身份。不得仅复制最后的 Selection JSON、再次查询上游 provider，或将生产来源改成新 case attempt。
- 对已存在的 split-v1 coordinator/case，保留历史文件，不重写冻结 input/output、不把两个历史 case 自动合并。新的 single-v1 coordinator 不继续旧六节点计划。

不增加通用 Pilot 输出规则引擎；对新 D2 节点增加局部 typed 验证，其余节点和其他 workflow 的完成判断保持原行为。

## 5. 可执行开发项及顺序

| 项目 | 文件和动作 | 完成条件 |
| --- | --- | --- |
| R1 身份/模型 | `codex_runtime/schema.py`、`data_runtime/policy.py`、D2 `schema.py`：新增单节点/stage、completion/checkpoint/result 模型和局部执行标记，保留历史解析 | 新执行只有五个 stage；旧 wire 记录可解码 |
| R2 中间提交 | 新 D2 `discovery_checkpoint.py`：实现绑定、校验、来源限定、确定性序列化、幂等冻结、记录与 Scan 导出、文件锁和 stdio 单工具 | 工具返回前已落盘；重启不覆盖候选；普通 Worker/Pilot 根目录正确 |
| R3 工具接入 | `codex_worker/sdk_runtime.py`：新 D2 节点条件配置；不改共享请求模型；D2 Runner task 合同增加工具与最终交付约定 | 新节点配置工具，其他节点配置不变；SDK 只启动一个 Turn |
| R4 Runner 聚合 | D2 `runner.py`、`validation.py`、必要的 `recovery.py` 局部分支：区分 wire 与 durable 模型，读 checkpoint、SHA/覆盖校验、保存聚合输出，失败也承接 Scan 观察来源 | 成功 receipt/codec 解码完整；非法 Selection 不丢 Scan；不使用业务 fallback 伪造 Discovery |
| R5 五阶段/恢复 | D2 `orchestrator.py`：合并 stages、更新 failed_stage、重试读取冻结状态、保存过程 refs、五份 stage_outputs、恢复聚合效果、旧契约复用检查；`pinned_runner.py` 更新 v2.1 身份 | 正常五次；已冻结重试只做 Selection；raw success 重放零新调用 |
| R6 Pilot | `pilot/document2_coordinator.py`、`document2_case_builder.py`、`templates.py`：单 case 排程/合同/工具、checkpoint-aware 完成核验、聚合 upstream、导出和历史身份区分 | 完整计划 14 项；同 case 内先冻结后选择；源输入原字节保留 |
| R7 定向验收 | 修订 `test_codex_document2_v21_orchestration.py` 和 Pilot 回归；增加 D2 checkpoint 服务测试及 SDK 条件配置测试 | 第 6 节场景覆盖，并保留旧 D2/Pilot/durable 基线 |
| R8 交付留痕 | 新验收 Markdown、changelog；更新旧文档指向新方案，明确五阶段口径 | 不再出现新方案要求六轮/15 节点的矛盾说明 |

R1 → R2 → R3/R4 → R5 → R6 → R7 → R8。在共享文件中只提交新 D2 条件分支；不整理其他 dirty 文件，不修改研究资产或消费者。

## 6. 必须通过的验收

### 6.1 直接验证原需求

1. 无重试时一个 Shell 的 Worker request 序列恰为五个 O1 节点；Discovery 只有一个 request/attempt，Scan/Selection 旧节点次数为零。
2. SDK fake thread 记录 `thread.turn` 恰为一次；新节点工具可用，普通 D2/v2、D1/O3/O4 等节点没有新增 checkpoint 工具。
3. fake Worker 的 `run()` **尚未返回**时调用真实 checkpoint 服务，确认 Scan 文件已存在且 SHA 正确；随后在同一次 run 内生成 Selection。不能用最终组合 JSON 返回后再写 Scan 的测试替代中间 checkpoint。
4. Pilot 一个 Discovery case 完成 Scan 和 Selection；coordinator 不为内部 Pass 新建 case。含 Narrative 的计划 14 项，O1 五项；跳过 optional 时实际执行数单独核对。

### 6.2 恢复和完整性

- 同字节提交幂等，不同 Scan 拒绝；并发提交首次冻结不被另一进程覆盖。
- Scan 记录落盘后、导出 Scan 前崩溃可重建缺失文件；已有文件内容冲突报 SYSTEM。
- 提交 Scan 后 Selection 首次 FORMAT 失败：同节点第二 attempt 输入带相同 Scan/SHA/producer，Scan 生成次数为一；stage_outputs 最终仍五份。
- 提交后进程中断/Worker 仍 live：依据原 durable request/receipt 恢复，不并发重复派发；Worker 已成功则复用最终响应而非重跑。
- 最终 completion 缺 checkpoint、SHA 不匹配、遗漏候选、非法 MERGE/环分别按设计处理；空 Scan + 空 Selection 合法。
- 成功聚合 raw artifact 已保存但父指针/sidecar 效果中断：重放，无新模型调用；成功工件损坏不得重扫掩盖。
- 首次 Scan 和重试 Selection 都有 O1/O9 本地别名：最终按各自 attempt 解析，Scan 字节不变；首次 Selection 失败也保留 Scan 引用；未解析来源 warning 仍非阻断。
- 多 Shell 并行和失败隔离维持现状，failed_stage 统一 OPEN_DISCOVERY，后四阶段继续累计 late/resolution。
- 新 v2.1 不复用 split-v1 run/Pinned ID；v2 请求、身份、发布和历史 typed 解码保持兼容。
- Pilot 仅有合法 completion JSON 但缺 checkpoint 时不推进；后四轮收到完整冻结 Scan/Selection；source-attempt 不查询 provider、不改冻结源输入字节。

实施后定向回归至少包括本次三个测试文件、现有初始化 substep/receipt 恢复测试、新 checkpoint 测试和 `test_codex_runtime_v2.py` 中相关 SDK 配置/调用测试；运行变更文件 Ruff 和 `git diff --check`。出现失败先区分新引入与已有基线，不重复无关全量测试。

本次勘察的 65 passed 只作为修复前基线。新修复的通过数量、调用序列与 checkpoint 中断证据必须在完成实现后重新记录。

## 7. 明确的做法边界

这个修复删除一处错误的调度拆分，补一个调用内部提交点和必要的恢复契约。既不只做节点改名，也不重新做全局编排：不用新的 Agent、workflow、HTTP checkpoint 服务、数据库表、通用版本框架、文件监控循环、研究语义门禁或消费者适配。

保留 Scan 冻结，是为了审计完整发现过程；保留 Selection 的 DEEPEN/MERGE/PARK，是为了让后四阶段有研究导航；保留后续 Late Additions/Resolution，是为了不把开放发现锁死在第一次扫描。将它们放回同一个 Open Discovery 节点，只改变阶段执行边界，不削弱这些业务目的。

本方案完成后的编排验收仍是离线契约验收。完整研究 skill、真实研究语义联调和新版启用按既定后续轮次进行；不把“工具接入测试通过”当成真实研究能力或生产启用证据。
