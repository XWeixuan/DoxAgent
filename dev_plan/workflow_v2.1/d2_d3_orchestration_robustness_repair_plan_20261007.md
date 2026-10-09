# Document2 编排鲁棒性修复与 D2/D3 上下文索引方案

日期：2026-10-07。性质：代码勘察、根因诊断与可执行方案；本轮尚未实施代码修改。

## 1. 范围与核心决策

对应上一轮编排/代码侧问题的第 1、2、3、4、7 项：交付双轨、大上下文读取、工具启动与审批、校验与恢复、可选研究资产。依据 MU O0/O1 两份详细分析，以及当前工作区实际代码；包含尚未提交的已有修改，不把旧 Pilot 错误等同于今天仍未修复的错误。

分析依据：[O0 Pilot 详细分析](D:/DoxAgentPilot/cases/document2/_coordinators/mu-d2-o0-sdkpilot-20261006-01/PILOT_ANALYSIS.md)、[O1 Pilot 详细分析](D:/DoxAgentPilot/cases/document2/_coordinators/mu-d2-o0-sdkpilot-20261006-01/PILOT_ANALYSIS_O1.md)。Realization g1 离线复现使用 `d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de` 的冻结 context、`audit/research_output.g1/completion.json` 与成功 `output/completion.json`。

本次以不中断研究链为第一目标：有可用产物就接收；能确定性修复就修复；不能修复的局部内容记录诊断并隔离；没有新产物则沿用可靠上游继续。研究质量、覆盖完整性、记录闭合程度都不得成为整个 workflow 的失败门禁。

但“继续运行”不能被实现为伪造研究完成、冻结 SHA 或证据。源资产身份不对、冻结文件损坏时，不使用该份资产；降级这一分支并继续健康分支。源身份、可读性检查服务于正确选择回退来源，不享有阻断整个 workflow 的权力。

范围：D2 正式 Runner、编排器、checkpoint、接收与恢复、Pilot SDK 驱动和输入准备；D3 迁移同一套索引/分段读取能力，覆盖 v2 与 v2.1 的实际可见 workspace。保留现有版本开关、O1 同 Shell 同 thread、单节点单 Turn 的 Open Discovery。无需重建全局状态机或引入新索引服务。

不修改 `prompts/codex_v2/**` 的 agent prompt/internal skill，不修改消费者、前端和发布业务 schema。运行时 task.json、SDK 交付包装、技术回执及派生索引属于本轮编排合同。D3 的研究接纳规则不随本次 D2 校验改造一起重做。

## 2. 证据与根因

### 2.1 第 1 项：文件正确，SDK 回复错误

O1 Finalization 的文件含完整研究结果，SDK 回复却是空 Shell。这在 Pilot 中被接收，但正式 D2 未必能接收。

正式 `workflows/codex_document2/runner.py` 的 `run_turn` 当前只将 `job.final_response` 送入 `ingest_model`；普通节点没有优先读取该 attempt 的 `output/completion.json`。文件中的正确结果因此不能挽救错误回复。v2.1 fallback 主要覆盖 O0 Synthesis/Finalization，未覆盖 O1 可靠上游 Shell。回复结构可解析也不等于业务结果可信，故只增加 JSON 解析容错不够。

Pilot `pilot/sdk_runner.py::_accept_phase` 则优先读取 `output/completion.json`，缺文件时才尝试 SDK 回复。两个运行入口对相同交付采取不同权威来源，是主要根因。另有共同成本：SDK research Turn 仍绑定完整业务 schema，包装要求文件与最终回复重复交付；模型写完大文件后仍需重复生成长结果。

不能将 Pilot 的文件优先接纳推断为正式 workflow 已具备相同能力，也不能将末尾无新增事件的时间全部归因于重复生成；现有日志不足以做这个时间归因。

### 2.2 第 2 项：资产齐全，但缺少可导航的读取合同

D2 Candidate、Synthesis、Domain Review 和 O1 已注入各自合同中的完整正文。困难主要发生在访问：报告嵌在 `context.json` 长字符串中，与完整 Shell、Scan、Selection、累计 Late Addition/Resolution 混在一起。O1 Realization 的 context 已达 245,167 字节，后续还会增长。模型自行写 Python 提取、分页、判断 UTF-8，读取结果可能被终端截断。

D2 `runner.py::_seed_attempt` 只生成输入合同文件，没有资产目录/章节索引。Pilot case builder 同样没有统一的分段入口。D3 v2 已有拆分文件，v2.1 `inputs_v21.py` 已有拓扑清单，`runner_v21.py` 有 owner 的 `read_mapping`；这些解决路径与身份映射，但不解决报告章节、长 Unit 和累计记录的定长读取。只在准备阶段生成一个总索引也不够：必须让 D3 owner workspace 中映射后的文件能实际被索引读取。

根因不是“应该少给正文”，而是全文可见与定位入口尚未同时提供。

### 2.3 第 3 项：真实门禁在 MCP required；审批错误已有局部修复

当前未发现一个独立实现的“全部工具健康检查通过才启动”的业务 preflight。实际存在的类似门禁是 SDK MCP 配置：`codex_worker/sdk_runtime.py` 中 Data MCP、D2 Discovery 设置 `required=True`；Pilot `templates.py` 同样有 required 配置。官方配置明确说明 required server 初始化失败会使启动/恢复失败。[官方配置说明](https://learn.chatgpt.com/docs/config-file/config-reference)

因此，未必需要工具的节点也可能在执行研究前被工具启动失败拦住。source_capture 已是非 required，不应声称它也有同样启动门禁。

Open Discovery 首次冻结失败的审批根因已被局部修复：当前正式与 Pilot 配置对 `d2_commit_open_discovery_scan` 设置单工具 `approval_mode="approve"`，而 SDK 总体仍 `deny_all`。这次应保留并验证该修复，不能再全局放开审批。普通 O0 禁用 MCP 时缺 transport 的 InvalidRequest 也已有修复；应做回归，不重复发明另一套 SDK 启动入口。

还需处理同 thread 的配置生命周期：正式路径仅在 Discovery 添加服务器配置，后续阶段没有像 Pilot 那样明确以完整 transport 定义禁用它。必须消除旧 attempt 的 cwd/绑定残留风险；这是代码潜在风险，不是已有日志证明发生了跨 attempt 写入。

### 2.4 第 4 项：局部内容问题被提升为整轮 FORMAT，恢复再次拒收

`workflows/codex_document2/validation.py` 通过 ValueError 强制要求唯一名称、候选精确覆盖、MERGE 合法、Late Addition 属于当前阶段、Finalization 所有方向有闭合记录、destination 可定位等。Runner 将 ValueError 转为可重试 FORMAT。局部账本问题因此引发完整研究重跑。

已离线复现 Realization g1：使用冻结 `input/context.json` 调用当前 `validate_output`，报 `late_additions: unknown unit or incorrect discovered_during`。g1 沿用 State 记录，`discovered_during=STATE`，当前 `turn=REALIZATION`。g1 与成功产物的 `canonical_shell` 完全相同。此处失败不需要重跑机制研究，只需正确区分历史累计记录与本轮新增。

还有两处接收后的问题：

- `orchestrator.py::_research_shell_v21` 恢复 saved success 时重新调用同一严格 validator，异常转 RuntimeError；只修正常接收仍会被恢复路径重新拦住。
- Finalization 直接用本轮 `authored_resolution` 替换累计 resolution；若放宽缺项校验但不改累积方式，会丢掉前几轮已形成的处置。

Pilot `_validation_context` 累积历史 Late Addition，却未采用同一公共函数累积 Resolution。正常接收、恢复、Pilot 之间需要共享账本构造与规范化规则，而非再加一套特殊豁免。

### 2.5 第 7 项：可选性只体现为捕获异常，尚未限制等待和失败缓存

D2 `_safe_optional_load` 捕获异常但无总等待上限，准备阶段 `asyncio.gather` 仍等待两个 Provider。`PublishedEventLibraryProvider.load` 在 async 方法中同步调用 `reference_view`，慢调用可阻塞事件循环。故可选资产即使最终降级，也可能先拖住整个输入准备。

Pilot `_bootstrap_narrative` 将 UNAVAILABLE 等所有状态写入 `narrative.json`，后续只要文件存在就返回；Event Library 缓存相同。临时 SSL EOF 因此可能固化成同一 source run 下的持续缺失。TLS、代理或服务端断开哪个是 SSL EOF 的底层根因尚未证实，不能据此设计关闭证书验证之类的“修复”。

Pilot builder 默认 `UnconfiguredEventLibraryProvider`。正式 ticker initialization adapter 已显式接入 Published Event Library Reader，因此 Pilot 的 NOT_CONFIGURED 不能证明正式 workflow 未接入。需要修 Pilot 配置接线，而非将 Event Library 改成必需资产。

## 3. 修复包 A：统一交付权威，消除重复交付门禁

### A1. 公共接收函数与固定产物位置

在 D2 增加小型 `acceptance.py`，由正式 Runner、Pilot、恢复入口共用。输入为 node/version、冻结 context、当前 attempt 的文件读入口、SDK reply、可靠上游；输出为接纳业务模型、规范化后的派生内容及诊断列表。函数不调用模型、不访问市场数据。

所有节点的业务文件位置固定为 `attempts/<attempt_id>/output/completion.json`；完整业务 schema 继续放在 input。正式 Runner 通过现有 WorkspaceClient 读取，不假定控制器能访问远端本机路径。只读取当前 attempt 的文件，不搜索“最近一个 completion”。Discovery 另读取本 attempt 或既有绑定的 checkpoint。

接收顺序：

1. 当前 attempt 文件能接纳，采用文件，SDK reply 不参与投票。
2. 文件不存在或不可接纳，尝试旧格式 SDK 完整业务回复；能够接纳则控制器写入/封存权威产物。
3. 两者都不可接纳，先提取可消费的局部结果；其余沿用可靠上游。仍无新内容则产出带诊断的降级结果，继续研究链。

文件与 SDK 都有效但内容不同，只记录 `sdk_reply_mismatch`；文件胜出。不因为 SDK 失败状态自动丢弃已落地文件；停止后的实际文件仍应尝试接纳。LeaseLost、run/attempt 身份不匹配、未授权写入不作为成功提交处理，也不跨 run 取文件补位。

原始 SDK reply、模型原始文件与规范化接纳快照分开保存。历史 Pilot 文件与冻结输入不原地改写；新 snapshot 中记录 source、原始摘要哈希、修复项和 fallback 来源。既有 Citation promotion 的非阻塞行为保留。

### A2. 小型技术回执，业务 schema 只约束业务文件

D2 v2.1 SDK `output_schema` 改为小型技术回执，字段仅包含 `completion_path` 和可选的简短状态说明；业务字段不得复制进回执。路径通过控制器固定期望值解释，不授权读取任意回复路径，也不要求模型自行计算文件 SHA。控制器负责实测哈希。

运行时 task.json 和 transport 包装明确：将完整业务结果写入固定文件；最终 SDK 返回技术回执。完整 output_schema.json 保持业务结构合同。Pilot 去掉“最终回复必须与文件一致”的包装要求。旧研究资产不编辑；它们若仍返回完整 JSON，接收器继续支持，不能因回执格式不对而拒绝正确文件。

v2 先采用文件优先接收与兼容旧回复，保留其现有 SDK schema；v2.1 切小回执。这样无需全局修改 WorkerRunRequest，也无需迁移其他 workflow。小回执改善返回负担的实际程度须后续模型 Pilot 验证，本轮不将离线通过宣称为内容行为已对齐。

### A3. 无新结果时的明确回退

O1 State/Realization/Gaps/Finalization 均可沿用上一可靠 canonical_shell，保留已有累计账本。该阶段以现有状态模型的降级诊断继续下一任务，不伪造新 Factor/Gap、完整研究或已闭合处置。

O0 Candidate 无可用内容返回空候选集并记录域缺失；Review 无可用结果不阻断 provisional 的 Finalization；Synthesis/Finalization 保留并统一现有 fallback。这些降级应反映在最终运行诊断中，不能只藏在日志。不新增业务完成状态枚举；现有 PARTIAL/警告路径足够。具体业务资产与运行质量标志分开。

## 4. 修复包 B：接纳优先的规范化、局部隔离与恢复

### B1. 校验处理表

| 当前拒收条件 | 新处理 | 不由编排层做的事 |
|---|---|---|
| 历史 Late Addition 被重交 | `(unit,name)` 合并；沿用已知首次发现阶段；历史重交不计为本轮新增；内容更新保留审计 | 不把 STATE 改写成 REALIZATION 来“过校验” |
| 新记录阶段与当前阶段不同 | 记录 provenance 警告；仍保留模型声明及实际接收阶段 | 不推断模型实际发现时间 |
| Late Addition / Resolution 重复键 | 完全相同去重；历史与本轮同键时本轮正文更新，保留首次发现来源；本轮冲突用最后一条并保留原始替代项和诊断 | 不要求研究重跑去重 |
| Finalization 未覆盖全部方向 | 累积前轮处置，再合并本轮；缺项列入 unresolved 诊断，保留方向 | 不捏造 closure 或自动宣称不重要 |
| 空 resolution / 未知 destination | 保留原文，标为未闭合/未定位，继续使用 Shell | 不猜一个 Factor/Gap 作为归属 |
| 候选/Shell/Unit/研究对象重名 | 同一父级内相同内容去重；冲突按稳定顺序选择最后完整对象，保留冲突诊断和原件 | 不自动改名或替模型重新划业务边界 |
| Value 找不到 Parameter / 类型不合 | 仅隔离该条 Value 到诊断附件；若有同身份上游有效 Value 则沿用；其他研究对象继续接收 | 不强行数字转换，不凭 Value 编一个 Parameter |
| Synthesis disposition 漏项/重复 | 对有效引用确定性去重；漏项加入已有 unassigned 结构，给“未被本轮处置”的编排说明；未知引用隔离 | 不直接分配到某个 Shell |
| Scan 漏 Unit | 为输入已有 Unit 补空候选清单，记录未研究；未知 Unit 作为额外方向附件 | 不把空候选解释为研究后无变化 |
| Selection 漏项 / MERGE 无目标或循环 | 接收有效决策；无有效决策的候选进入 pending 诊断，在后续上下文保留为待判断方向 | 不伪造 DEEPEN/DROP 或使用循环合并 |
| JSON/Pydantic 局部不可解析 | 对可确定的字段缺省、可解析子对象做局部恢复；问题项留原件/诊断；否则回退上游 | 不用宽泛类型转换“修好”业务数值 |

以上 pending/unresolved 存在编排诊断 sidecar，不向现有 Selection/Resolution 业务枚举塞新值。后续 context 附带这些未丢失的方向，是否研究由 agent 决定。控制器不替代内容判断。

仍调用 Pydantic 构造最终可消费模型，但结构失败不直接升级为全节点异常。现有 `validate_output` 可保留为离线严格检查工具；正式接收不再以它的 ValueError 为总闸。用于身份/引用定位的检查返回诊断与修复动作，而非要求全体对象同时健康。

### B2. 累计账本只有一种构造方式

抽出 `merge_discovery_records(previous,current)`：Late Addition 的首次发现 provenance 不因重交改变；Resolution 采用已有累计记录再按本轮键更新。Finalization 与中间阶段同样累积，不再清空历史账本。Runner 规范化、orchestrator 副作用重建、Pilot validation context 全部共用。

规范化幂等：接纳结果再接纳不生成新 warning、不反复改字段。所有修复记录带对象路径与原因；业务原件永远可查。不要构建新的“研究质量评分器”。

### B3. 恢复采用已接纳快照，不重新施加新语义门禁

新快照携带简短 `acceptance_version` 与诊断 sidecar 引用。恢复先核对已有快照身份、完整性与可消费结构，再重放副作用；不因新的覆盖/唯一性规则 RuntimeError 终止。

旧成功快照用同一规范化函数生成新派生接纳快照，旧文件不覆盖。损坏快照回退该 Shell 最近可靠结果；没有则沿用 seed，记录降级并继续健康 Shell。`errors.py` 的 SYSTEM 全局失败不可继续兜住这些可修复内容错误；真正 lease/写入权限问题仍按已有运行治理处理，不把每种异常一律吞掉。

Pilot review 失败不得让已接纳研究重新研究：研究接纳完成后即可供下游使用，复盘独立重试一次，失败生成 `review_unavailable` 诊断。Coordinator 的 ready 判断改为检查接纳快照，而非仅 JSON 可读取；不以是否已有完美 review 为业务依赖门禁。复盘不得改研究输出，已有 digest 防修改保留。

### B4. Open Discovery：工具可选启动，冻结仍然真实

正常路径仍由 commit 工具在单一 Turn 中冻结 Scan，再做 Selection；不拆节点，不先取消冻结再补一个假 SHA。

复用 `discovery_checkpoint.py` 的提交实现，增加可直接执行的轻量本地 CLI 入口，以同样 run/attempt 绑定读取 workspace 文件、原子写入实际 checkpoint。运行时 task.json/包装列出 MCP 和该 CLI 两种等价提交入口；不编辑 research skill。CLI 不依赖另一 MCP 服务，无须新增后台进程。允许同一正文幂等提交，不允许覆盖另一份已冻结正文；冲突时采用已有 checkpoint 并记录诊断。

若两种入口都未完成，但 Scan 文件已存在，控制器回收 Scan 并真实封存；未经该实际冻结绑定的 Selection 不假装合格，保留为未绑定原始判断并降级。后续仍拿到 Scan 与 pending 方向，继续 State。若连 Scan 都没有，生成空 Scan 清单并记录 Discovery 未完成；不为追求覆盖率重跑全部研究。若只有绑定字段不匹配而 checkpoint 内容/生产身份可证实，则控制器修正派生接纳绑定并记录，不因一个回执 SHA 错误丢弃全部选择。

## 5. 修复包 C：非 required 工具与精确审批

在 `codex_worker/sdk_runtime.py` 对 D2 请求显式设置 Data MCP、Discovery `required=False`；Pilot templates 与 SDK resume 覆盖配置同步。保留真实启用条件、数据权限、tool allowlist 和单工具 approve。不得依赖旧 thread 中已有配置碰巧是 false；每次 request 都明确声明。

非 Discovery D2 节点以完整 transport 定义显式禁用 Discovery，并清除当前任务之外的 commit 可用性。Discovery 的 cwd/run/attempt 每次重绑定。普通 O0 不输出缺 command/url 的 disabled stub。SDK 总体仍 deny_all；source_capture 和 O4 operations 的其他治理行为不顺带修改。

Data MCP 未启动或调用失败只造成该次查询缺失，已有全文、上游文件和本地工作工具仍可用。控制器不增加“先探测所有工具”的全局健康门禁。按调用记录诊断失败工具即可，不要求节点证明它们全部健康。

此包范围以 D2 为主；D3 的索引读取不依赖 Data MCP，因此不因索引迁移扩大到 D3 全部工具策略重构。

## 6. 修复包 D：共享派生索引与 UTF-8 分段入口

### D1. 固定文件布局与索引内容

新增共享标准库模块 `codex_runtime/context_index.py`。接收 asset role、源引用/源 SHA、实际可见正文和 JSON 数据，输出确定性的派生文件；不调用 LLM，不改变 context.json 和原始报告。

每个 task/attempt 目录附加：

```text
input/context_index/index.json          # 总目录，体量受控
input/context_index/overview.md         # role、路径、长度、分段入口
input/context_index/assets/<id>.txt     # 完整正文的可读派生副本
input/context_index/pages/<id>/<n>.txt  # 有界 UTF-8 阅读页
input/context_index/records/<id>.json   # Unit/单条累计记录等完整对象
input/context_index/read_context.py    # 标准库读取器
```

D3 采用 task key 派生的同类目录，文件路径符合 owner workspace 的 read_mapping。`id` 用 role + 内容摘要短后缀，不直接把长中文名称拼进路径。

索引包括：

- context 顶层字段：JSON Pointer、类型、记录数或字符数、完整读取路径；不抄写长正文。
- C1/C3/C5、Narrative 等长报告：role、原引用、源与派生内容 SHA、Markdown 标题层级、章节范围、分页面。忽略代码围栏内的伪标题；没有标题则按页导航。
- Shell/Unit：Shell、Unit 名称、JSON Pointer；State/Parameter/Value/Baseline/Factor/Gap 子集合入口，长 Unit 可继续按集合和页读。
- Scan、Selection、累计 Late Addition/Resolution：按 Unit 与稳定记录键定位；累计与本轮增量区别明确；同名对象保留父级路径。
- 列表过长时目录本身分页，总目录仅提供分组/页数入口，避免索引再次长到被截断。

单页默认最多 6,000 字符，优先在段落边界截取；超长单行按字符分片，不切坏 UTF-8。章节可跨多页，不因某章节过长整体输出。每次读取给 `page/total_pages/has_more`，模型知道还有下一页。完整 .txt 和 context.json 始终可读。

### D2. 稳定读取接口

标准库脚本支持 `list --group reports|units|records`、`read --asset <id> --section <id> --page <n>`、`read --pointer <json-pointer> --page <n>`。默认输出有界，不因一次请求输出整份大报告。中文、包含斜杠的 JSON key 按 JSON Pointer 规范处理。

Python 显式 `encoding='utf-8'` 读取并以 UTF-8 输出。Windows 的 task 包装同时给出 `Get-Content -LiteralPath ... -Encoding utf8` 的预分页文件读取例子。脚本不可用时直接读页文件，不能让 Python 命令失败阻断研究。脚本只允许读取该索引声明的 workspace 输入，不可用 reply 提供的任意路径打开本机文件。

task.json 添加索引路径与简短读取入口；SDK task 包装提示完整正文、原文件和索引都可用。不得将原合同改成“只读索引”；是否读取哪些正文仍由 agent 与 skill 决定。索引中不放模型摘要，防止索引替正文产生新事实。

### D3. 集成位置

1. D2 正式 `_seed_attempt`：对冻结 context 建索引；所有 O0/O1 使用同一模块。
2. D2 Pilot builder/SDK task 准备：对同一 case context 建索引；已冻结旧 case 通过新 attempt 派生，不往旧 input 添文件破坏 digest。
3. D3 v2 `runner.py::seed_initialize/seed_maintenance`：为 shared/global 及 D2 研究对象创建索引并注入 task 指针。
4. D3 v2.1 `inputs_v21.py` 保留源 role 元数据，`runner_v21.py` 在 owner 的 read_mapping 完成后生成实际本地路径索引；包括当前任务借入正文和已接纳上游对象。不得给 owner 一个只能在主 workspace 打开的路径。

索引生成失败只是 `context_index_unavailable` 警告，仍保留全文和旧任务合同。相同 task/source 内容重建字节一致，按已有不可变输入机制封存；版本变化生成新派生目录，不覆盖旧冻结输入。无需全文检索引擎、向量数据库、新 MCP 或跨任务索引同步服务。

## 7. 修复包 E：可选资产有界读取、正确接线和可恢复缓存

### E1. 输入准备时限

Narrative/Event Library 各自默认总预算 15 秒，并行读取；预算参数只在控制器输入层，不增加用户交互。最多一次瞬时错误重试，且必须在同一总预算内，无额外叠加等待。Provider HTTP timeout 也受剩余预算约束；不能只 asyncio timeout 而让后台网络线程永久占着连接。

Event Library 的同步 reader 移到受控 `to_thread`，避免卡住事件循环；本地 DB/客户端本身使用有限调用超时。超时、临时错误均返回 UNAVAILABLE、简短原因和尝试元数据，后续照常执行。不添加 Provider 健康门禁，不因缺可选资产创建新的研究任务。

### E2. 只缓存可复用的有效资产

Pilot source cache 不再永久缓存 UNAVAILABLE/NOT_CONFIGURED/ABSENT。旧负缓存检测后忽略，在下一次新 case 准备重新读取；本轮已封存 case 不自动变更输入。AVAILABLE 缓存按 ticker、source run、as_of、Provider 身份/配置和版本定位，加载时复核时点与完整性。

新请求失败可使用确实符合当前 cutoff 和 Narrative 七天窗口的已有 AVAILABLE 缓存，并记录 reused_from。没有合格缓存就明确缺失，不把过期资产重新标成最新。DoxAtlas 当前接口不能据此假定支持 as_of 历史查询；只使用已知可读取的 published run/已有合格缓存，不编造 API 参数。

### E3. 给 Pilot 接上正式已发布 Event Library 读取口

Pilot SDK/coordinator 的输入准备显式接入 `PublishedEventLibraryProvider`，复用正式流程的只读 Reader；支持可选 pinned version，并在 case manifest 固定来源。没有可用 backend 或无已发布版本时分别保持 NOT_CONFIGURED/ABSENT，不阻塞。

当前 NOT_CONFIGURED 负缓存不得覆盖新接入 Provider 的结果。Narrative SSL EOF 则先用有限请求记录定位 DNS/连接/TLS/响应阶段，再按实际证据修连接配置；不关闭 TLS 校验，也不将一次 EOF 归罪于研究逻辑。

## 8. 落地顺序、文件边界与验收

| 顺序 | 改动范围 | 交付与验证 |
|---|---|---|
| 1 | D2 acceptance/validation/账本、runner、orchestrator；Pilot 接纳/coordinator | 文件优先、历史重交不重跑、Finalization 不丢历史、恢复不二次拒收 |
| 2 | sdk_runtime、Pilot templates、Discovery CLI/包装 | D2 required 门禁解除；单工具审批保留；工具缺失可降级；同 thread 绑定不残留 |
| 3 | context_index、D2 seed/Pilot、D3 inputs/runner v2/v2.1 | 全文保留；章节/Unit/累计记录有界 UTF-8 读取；owner 路径实际可读 |
| 4 | D2 inputs、Pilot builder/coordinator 接线 | 可选读取预算、负缓存恢复、Event Library 来源正确、固定 case 不变 |
| 5 | D2 v2.1 技术回执包装与 SDK schema | 大结果只写文件；旧回复兼容；错误回执不影响正确文件 |

严格离线验收采用现有 Pilot 坏例与有意义的故障注入，不为每个字段写镜像测试：

- 将 O1 Finalization 正确文件与错误/空 SDK 回复送入正式接收路径，必须选文件；SDK FAILED 但文件已完整也必须回收。
- Realization g1 原件无需修改即接收；canonical_shell 不变，历史 STATE provenance 保留，不触发模型重试。
- Finalization 缺一条处置仍可接收，旧处置保留、未闭合方向可见；恢复同结果不抛旧 RuntimeError。
- 一条错误 Value、一个冲突记录不能影响其余 Unit；原文可查，无编造数值、归属与 closure。
- Data MCP/Discovery 启动失败时 SDK 能启动；Discovery CLI 实际冻结、SHA 可复算；两入口都失败也不阻断健康研究链。检查非 Discovery 请求中的旧 commit 不可用。
- 大报告含中文、代码围栏、无标题、超长单行；Unit/列表目录与正文均能逐页完整重组且不截坏字符。D3 owner read_mapping 后的文件能逐段读取。索引构建失败仍可工作。
- 可选 Provider 超时不会超过预算拖住输入；旧 UNAVAILABLE 缓存不再永久遮蔽恢复；错误 cutoff/版本只降级该资产。
- Pilot review 故障不导致已接纳研究重新运行，下游可继续；研究原件 digest 不变。

优先运行现有 `test_codex_document2_workflow.py`、`test_codex_document2_v21_orchestration.py`、`test_d2_discovery_checkpoint.py`、`test_pilot_sdk_runner.py`、`test_document2_pilot_coordinator.py`、`test_document2_o1_thread.py`；索引迁移覆盖 `test_codex_document3_workflow.py`、`test_codex_document3_v21_orchestration.py`，并添加共享索引边界测试。以 `uv run pytest` 执行；只在失败或新改动要求时扩大测试。

代码落地后追加 changelog，说明范围和降级合同；不覆盖当前 dirty checkout 的已有 prompt/skill 等修改。本轮方案不触发新模型 Pilot 或远端部署。真实模型验收留到实施后：至少观察一轮文件交付、一次正常 Discovery 冻结和一次索引读取；无需为了格式元数据再重跑完整 MU 研究链。离线合同通过与真实研究质量接受分别记录。

## 9. 需要避免的错误修复

不能仅 try/except 掉 validator 后宣称通过：历史账本仍可能被 Finalization 覆盖，恢复仍可能 RuntimeError。不能只放宽 SDK JSON 解析：正式 Runner 仍可能忽略正确文件。不能用 required=false 代替 checkpoint 提交回退：研究能启动不等于冻结工具能用。不能只给报告摘要或总目录：正文和实际 owner 读取路径必须保留。不能只“重试 Narrative”而保留永久 UNAVAILABLE 缓存：后续仍看不到恢复。

最终方案是小范围改变交付与接纳权威、移除工具启动门禁、增加派生读取入口及修复可选资产准备；研究如何理解、取舍与写作，留给下一轮 prompt/skill。
