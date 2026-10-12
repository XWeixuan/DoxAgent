# C4 工作流失败复盘与非阻塞性审查

审查日期：2026-10-09。对象：本地 MU C4 四轮真实运行，以及当前工作区中 C4 / NodeRunner / SDK / Worker / Source Capture 实现。本文是现状审查与修复方案，**本次没有实施下面列出的业务代码修复，也没有追加真实模型运行或生产部署**。

## 1. 结论

前三轮没有取得完整验收，但失败性质不同：第一轮在启动接口处失败；第二轮发生证据链缺陷及 network-build 的确定性 API 拒绝；第三轮四个节点都完成，但测试驱动把 Data MCP 留在旧 attempt，证据作用域不合格。第四轮正常路径成功，不能据此宣称整个工作流已经具备可靠的异常恢复能力。

本次重新读取 Worker receipt、attempt 验证记录和 SDK 的公开工具完成事件，提取 **36 条异常记录**，包括已经修复的错误和节点完成过程中自行绕过的工具错误。另用临时 workspace / Fake Worker 做确定性故障注入，复现五类剩余问题：身份回调契约不兼容、C4f 失败扩散与发布后无法原 run 补跑、失败回复覆盖有效 Markdown 文件、坏关系行拖垮完整快照、用量记录异常触发重复研究。Windows 动态 Observation Pack 的 ACL 还显示真实可读性缺陷。

因此当前判断是：**健康路径已验证；异常隔离、产物接纳与恢复仍需修复。** 优先处理有效产物误接纳、非业务异常重复研究、C4f 失败扩散和动态文件不可读，再补发布后的定向恢复。保持一个 C4 thread、严格顺序和三个独立产物，无需引入更多内容阻塞门槛。

## 2. 证据与方法

- 原始实模：[运行报告](../../../eval/c4_live_mu_20261009/README.md)、四轮 `audit/jobs/*.json`、各 attempt 的 `audit/turn_summary.json`、SDK session 中公开的工具事件。
- [异常事件清单](evidence_inventory.json)：每条保留 run、节点（可取得时）、错误/警告、参数、原 session 路径和行号；重复 item 按 turn/item 去重。不读取或转录内部推理。
- [采集脚本](collect_evidence.py)、[确定性故障探针](review_probes.py)、[探针结果](probe_results.json)、[Windows ACL](acl_evidence.txt)。探针只使用临时目录和假 Worker，不产生新的模型费用。
- 扩大回归：`uv run python -m pytest tests/test_c4_split_workflow.py tests/test_codex_runtime_v2.py tests/test_codex_d1_attempt_protocol.py tests/test_worker_resource_budget.py -q`，**127 passed / 153.25s**；一项既有 OpenTelemetry importlib-metadata 弃用警告。

36 条是现存记录中提取出的 Worker 失败、公开工具失败和 Source Capture 软警告总数，不等同于 36 个根因，也不包括没有生成工具事件的推测性错误。引用未解析、旧作用域路由等问题另由验证记录和文件落点判断。回归通过与故障探针复现同时成立：既有测试未覆盖这些故障组合。

## 3. 四轮经过及所有已观测错误

| Run | 阶段结果 | 核心原因 | 异常事件数 |
| --- | --- | --- | ---: |
| 01 | pre-scan、C4f 各两次启动失败；后两阶段跳过 | SDK 身份回调两个参数，Worker 绑定方法需要 request/thread/turn 三个参数 | 4 Worker 失败 |
| 02 | pre 18 / future 12 / formal 31；network 两次失败 | Source Capture 批准缺失；Pack CRLF 比较误判；network string schema 被 API 拒绝；还有旧 MCP 作用域迹象 | 2 Worker + 7 工具失败 |
| 03 | pre 18 / future 16 / formal 40 / network 8,903 字符，四节点完成 | 同一 SDK/MCP 进程跨阶段，Data MCP 仍绑定 pre-scan attempt，证据链验收失败 | 1 工具失败 + 1 软警告 |
| 04 | pre 16 / future 14 / formal 41 / network 6,367 字符，四节点单次完成 | 正常路径恢复，但仍有可读性、错误 O 别名、路径选择、来源超时/不可达等非终态错误 | 9 工具失败 + 12 软警告 |

### 3.1 第一轮：研究开始前的接口错误

原错误：`START_THREAD: WorkerJobManager._capture_identity() missing 1 required positional argument: 'turn'`。pre-scan 与 C4f 共四次 attempt 都命中同一确定性接口错误。formal/network 没有开展研究，不能把跳过算成新的模型失败。

直接 SDK runtime 调用 `receipt_callback(thread.id, turn_id)`，WorkerJobManager 安装的是 async `(request, thread, turn)`；既少 request，也没有 await。本地驱动绕过提前身份回调，保留终态 receipt，最终驱动逐 job 重建 runtime。**核心直接接入接口仍未修复**；生产 Capsule 子进程有二参转 IPC 适配，父进程使用三参 await，不能把本地错误直接推断为生产 Capsule 必然失败。

### 3.2 第二轮：权限、引用完整性与 schema 兼容

1. pre-scan 三次、C4f 一次 Source Capture 被拒：`MCP tool call requires approval, but approval policy is never`。工具未显式自动批准；原生 web search 与 MCP 工具权限是不同通道，不能由 web 可用推断捕获可用。
2. pre-scan 的动态 Pack shell 读取失败：文件存在但 sandbox 访问被拒。此问题后续两轮仍出现，未被 CRLF 修复解决。
3. pre-scan 调用 `data_tool_guide({})` 缺少必填 task，是 Agent 的参数错误，可恢复。
4. formal-scan 读取旧 `c4e_formal_scan-1-2078211c18` catalog 路径失败，显示跨阶段旧路径/作用域问题；第三轮进一步确认 SDK 进程复用缺陷。
5. pre-scan canonical store 已有 666 个 Observation，但 12 个被引 O 别名全部未解析：Pack 为 CRLF，校验对照 LF 原文误判。这里不能按“无证据”解释，属于校验器错误。
6. C4f 五个被引别名在自己的 attempt store 不存在，formal 未形成可解析引用；这一现象不能全部归因于 CRLF。跨阶段 MCP 配置滞留是独立问题。
7. network-build 两次都在研究前 HTTP 400：`Invalid schema ... type object, got type string` / `invalid_json_schema`。轻量 Markdown schema 合法，但不符合 Codex structured response API 的顶层 object 约束；重试同一错误没有价值。

### 3.3 第三轮：节点完成，证据作用域不合格

Source Capture 批准和 Markdown API 兼容修复已生效；pre 引用 2/2 解析。仍观察到 pre 动态 Pack 读取权限失败、C4f 一次 Source Capture ConnectError 软警告（没有生成 alias）。

更关键的是 Data MCP 仍向 pre-scan attempt 写入/路由，后续正式研究虽能使用 web 和直接 URL 完成，却未建立当前 attempt 的可解析 O 引用。修复在**测试驱动生命周期**：各 job 完成关闭 SDK/MCP，然后 resume 相同 thread ID。生产 Global C4 的 `CapsuleRuntime.release` 已有这一释放行为。第四轮证实新驱动的落点正确；未以此替代真正 Capsule 进程级故障恢复验收。

### 3.4 第四轮：成功过程中残留的工具错误

| 节点 | 硬工具失败 | 软警告（调用 completed，但无新捕获 alias） |
| --- | --- | --- |
| pre-scan | 1 次 shell Pack 权限拒绝；读取 O133、O134 两次 observation_not_found | 无 |
| C4f | 读取 O103、O104、O105、O149 四次 observation_not_found；Samsung 捕获一次 30s MCP timeout；一次从 workspace 根目录读取 skill/schema 路径失败 | GlobalWafers 页面过大 1；Micron Q3 ConnectError 1；GM news/engineering/investor PDF 三个 403 |
| formal-scan | 无 hard failed 事件 | GlobalWafers 两个 URL 页面过大 |
| network-build | 无 hard failed 事件 | NIST FEIS PDF 过大 1；OCWA 403 1；Idaho PUC order/docket ConnectError 3 |

这些 O 读取错误表明 Agent 尝试了当时作用域内不存在的别名，尚无证据证明 Observation store 丢失数据；后续补捕获并最终解析成功。应指导使用当前返回 alias / catalog，不能跨 attempt 查找来“修好”一个不存在的 O#。

skill/schema 实际在 attempt 的 `input/skills/...`、`input/schemas/...`，task 已给出路径；该 shell 错误属于 Agent 自行选择相对根目录错误，**不是没有注入**。优先明确以 task 中路径为准，不必复制一套根目录文件掩盖问题。

Source Capture 的准确计数如下。此前报告把 completed 数叫“成功”不够准确，本次已更正：

| 节点 | 总调用 | 返回 alias | 软警告 | 硬失败/超时 |
| --- | ---: | ---: | ---: | ---: |
| pre-scan | 6 | 6 | 0 | 0 |
| C4f | 19 | 13 | 5 | 1 |
| formal-scan | 21 | 19 | 2 | 0 |
| network-build | 14 | 9 | 5 | 0 |
| 合计 | 60 | 47 | 12 | 1 |

四节点原生 web search 分别 3/25/9/4 次；最终当前 attempt 引用分别 16/16、23/23、41/41、50/50 解析。这些证明失败来源可被替代，不能证明每次捕获都成功或所有研究事实都经过人工核实。

## 4. 已修复问题是否彻底

| 问题 | 当前措施与证据 | 判断 |
| --- | --- | --- |
| c4f skill 名称 | manifest / prompt 指向 `c4f_future_node.md`；实际 workspace 与实模读取确认 | 正确注入；相对路径误用另需提示修整 |
| web search 权限 | SDK live 配置、四阶段权限测试、实模四节点搜索记录 | 本范围闭环 |
| Source Capture 批准 | 显式 capture_source approve；第四轮 47 次 alias 返回 | 权限问题闭环；来源可用性/超时未解决 |
| CRLF 引用误判 | 按换行规范化比较，回归和第四轮引用解析通过 | 原缺陷闭环；ACL 是另一缺陷 |
| string schema HTTP400 | 仅 object schema 传 API structured output；Markdown schema 继续注入并本地验证；第三/四轮成功 | API 兼容闭环；输出接纳另有漏洞 |
| 跨阶段 MCP 旧 attempt | 驱动按 job 释放 SDK/MCP，同 thread resume；第四轮写入正确 attempt | 本地驱动闭环；正式 Capsule 已有释放设计，仍需故障注入验证 |
| 身份回调 | 本地驱动绕过不兼容回调 | 仅绕过，直接 runtime 接口仍不兼容 |

## 5. 仍待修复的确定性问题

### P1-01：失败回复可以覆盖有效 network Markdown，并被标记 available

位置：`src/doxagent/workflows/codex_document1/node_runner.py:277`、`:295`，`recovery.py`。当前只要 final_response 是非空字符串就可满足轻量 schema；有内容后不再尝试已落盘的正式输出。探针给 Worker failed + `I could not complete the request.`，同时保存有效 report，最终 available 的却是失败句子。

方案：以当前 attempt 指定输出文件作为产物交付入口优先恢复；Worker failed 的普通聊天回复不得自动等同完成产物。保存文件有效时允许恢复已完成研究；没有正式输出则保持失败/可恢复状态。成功 Worker 的 ack 也不应覆盖文件。无需增设字数、章节数、关系数量或错误关键词黑名单。验收覆盖 failed+有效文件、failed+错误句无文件、success+ack+有效文件、纯合法 Markdown 正常交付。

### P1-02：用量记录异常把已成功研究改成失败，重复消耗并扩散

位置：`node_runner.py:348-414`。产物已写入、attempt 已标 succeeded 后调用 `_record_usage`；异常落入统一失败分支，再次调用同一用量记录入口。探针注入仅 C4f 用量落库 OSError，触发两次研究、两次 attempt failed，并导致三个最终产物失败。

方案：用量/诊断写入与产物接纳分开；失败记录为可补偿 telemetry 警告，不能重跑研究或覆盖成功 attempt。真实产物持久化失败仍应报错，但恢复应重试保存而非无差别重新研究。验收注入用量存储故障，研究只调用一次、产物 available，后续遥测补写有独立收据。

### P1-03：C4f 失败仍阻断两项 Entity Map 产物

位置：`src/doxagent/workflows/codex_global_research/orchestrator.py:166-194`、`:305`。formal predecessor 指向 C4f ref；Future 失败无 ref 就跳过 formal，network 随之跳过。探针确认两个节点根本没有被调用。当前三个状态字段独立，但执行依赖仍错误地耦合独立业务产物。

方案：严格串行等待 C4f 达到终态后，以 formal 真正必需的 pre/C1/C3/C5 输入判断是否可执行；Future 失败只记录自己的失败，不引入混合回退。network 保留 formal 完整快照的真实依赖；formal 完全失败时明确 report 不可用，不能偷偷把 pre-scan 当正式快照。合法空快照与失败区分，现有空快照可继续的行为保留。验收 Future 失败而 formal/network 正常完成，三状态 failed/available/available。

### P1-04：部分产物失败后发布，同 run 再调用不会恢复

位置：global orchestrator `:37`、`:285`。存在 published bundle 就立即返回。探针先造成 Future 失败，再修复假 provider 并调用相同 run，新增研究调用为 0，三个 failed 原样返回。

方案：明确独立产物修复入口与修复 generation / 新版本发布；只恢复失败节点及真实下游，不重做 C1/C3/C5。published 代表发布动作完成，另持久化 complete/partial 与各产品状态，消费端必须展示未完成项。不要静默修改既有发布快照，也不要用另一项成功掩盖失败。需要补 managed initialization / durable parent 链路验收；本地 orchestrator 探针不能证明所有生产父任务的聚合行为。

### P1-05：Windows 动态 Observation Pack 不可读

位置：`src/doxagent/observations/pack.py:40`、`:146`、`:177`。第二至四轮均发生 shell access denied。原文件确实存在，ACL 仅 SYSTEM、Administrators、OWNER RIGHTS，缺少输入文件具备的 Codex sandbox 用户权限。当前使用私有 `tempfile.mkdtemp` staging、写入后 rename，并 chmod 只读；与 staging 保留私有 ACL 的根因高度吻合。

方案：让随机 staging 继承当前受控 Pack 父目录所需的读取/遍历 ACL，再原子 rename；若需显式授权，限定当前 run/attempt 的 sandbox 身份与只读范围。保留只读证据和路径隔离，不扩大 `.control`、密钥或其他 run 权限。验收必须使用实际 Windows sandbox 在 MCP 返回后读取新 selected/catalog/block；管理员 PowerShell 可读不算通过。另保留直接 MCP 读取/网页来源降级路径，避免一次 shell 失败停止研究。

### P2-01：直接 SDK 身份回调仍不兼容

位置：`src/doxagent/codex_worker/sdk_runtime.py:478-503`、`jobs.py:106-108`。探针直接签名绑定仍报 missing turn。统一 async callback(request,thread,turn) 或显式适配器，保证 start/resume 两路径均 await；测试身份在运行中持久化、终态仍正确，不能依赖禁用回调长期绕过。生产 Capsule IPC 适配保留，不因局部修复破坏它。

### P2-02：一条坏关系行导致完整有效快照丢弃

位置：`src/doxagent/workflows/codex_document1/recovery.py`。整段 wire schema 先验证，才进入行级 recovery，现有容错无法处理混合好坏行。探针一条五字段有效行加一条缺字段行即 StructuredOutputInvalid。

方案：顶层类型/产物身份严格；逐行五字段校验，保留合法行、隔离坏行并记录原因与原始位置。优先同 attempt 纠正坏行或明确 partial，不能把静默丢行后的结果冒称完整 formal 快照；只有没有可用结果才失败。不要因为坏行重新执行整个网络研究。验收混合行、全坏行、合法空列表、错误产物字段与完整快照语义。

## 6. 其他代码风险与验证缺口

以下来自代码审查，尚未在本轮实模中造成终态故障；与上述已复现问题分开列示。

| 风险 | 依据及影响 | 修复/验证方案 |
| --- | --- | --- |
| Source Capture 内外超时竞争（P2） | `source_capture.py` HTTP timeout 30s，SDK MCP tool timeout 也30s；实际发生一次 MCP timeout。HTTP 分阶段 timeout 也不是包含 DNS、重定向与解析的总截止时间 | 内层总预算短于外层，或外层留有限余量；DNS/解析一并有总预算。最多在预算内重试一次瞬态错误，403/过大改换来源，不重复撞同 URL |
| completed 误作捕获成功（P2） | 最终60调用仅47返回 alias，12软警告被 completed 汇总掩盖 | 持久化 alias_success / warning / failed 三类计数、URL、原因；最终验收不再只读工具状态 |
| Data MCP 启动硬依赖（P2） | `sdk_runtime.py:366` 对 C4 required=true；MCP 起不来可能连原生 web 研究也启动不了 | 分离必需能力与可选数据源。故障注入验证启动失败能否依靠已有输入+web 继续；若能力确实必须，明确配置错误并快速失败，避免重复完整研究 |
| cleanup 外层无截止时间（P1条件风险） | `jobs.py:335` await runtime.release 无总 deadline；Capsule 多数步骤有超时，但 IPC drain / SDK close 未完整包住。可能卡 CLEANING、同 thread 队列不能释放 | 关闭总预算、进程身份核验、必要时隔离 capsule；确认旧进程退出后才释放同 thread，不能为非阻塞制造并发双执行。验证 unrelated jobs 可继续；没有证据称本次已发生泄漏 |
| embedded 等待无整体预算（P2） | embedded client 持续等待 queued/running；执行 timeout 不覆盖所有排队/清理阶段 | 区分排队、执行、清理预算与明确恢复状态；不能简单超时后重复提交同一 thread |
| 引用/遥测后置失败扩大成研究失败（P2） | NodeRunner 引用提升 OSError/sqlite 异常可重新触发整个 attempt；写 completion 后才追加 unresolved warning | 把研究与引用/保存后处理拆成可恢复步骤；引用暂缺明确降级并保留完整性警告，不伪造 resolved。最终 completion/manifest/返回值警告一致；关键身份/范围/真实落盘错误仍严格 |
| full-run inventory 随规模增长（P2） | 每阶段引用提升做整个 run workspace inventory，非仅当前引用/attempt | 使用现有 prefix 范围或引用相关索引；测大 workspace 的后处理耗时后再调优。本次未量化性能，不宣称它已造成超时 |
| 统一重试确定性错误（P2） | 第二轮同一 invalid_json_schema 重试两次；接口错误也重复 | 参数/合同/权限配置错误分类快速结束，修复配置后新 generation 重试；瞬态错误才有限重试。现有节点 timeout、有限 attempts 和基础设施恢复上限保留 |
| 上游证据重绑异常边界（P2） | rebinder 对缺 manifest 有降级，但源读取/远端异常并非全部在降级边界内 | 对来源不可用记录输入证据限制，允许当前 web 重新核验；权限/路径越界不可降级。补完整上游 Observation store 实际重绑测试 |

源码已有一些合理防阻塞措施：节点运行预算、interrupt 超时、有限基础设施恢复；Source Capture 非 required；缺失旧证据可提示重新查证；无效空产物与失败状态区分；Global C4 每阶段释放进程保留同一 thread。修复时保留这些行为，不添加关系数量、报告字数或章节模板等研究完成门槛。

## 7. 推荐修复顺序与验收

1. **产物与执行隔离**：先修 P1-01/P1-02/P1-03，固化本报告的三组故障探针为回归；确保文件可恢复、遥测不重跑、Future 失败不拖垮 Entity Map。
2. **环境及恢复**：修 Windows Pack ACL、身份 callback、发布后独立修复入口；补真实 Capsule 生命周期与 Windows sandbox 动态文件读取测试。采用新 attempt/generation，保持旧收据和三个产物身份。
3. **研究容错与可观测性**：行级隔离、Source Capture 总预算、软警告统计、completion/manifest 警告一致性、MCP 降级、cleanup 截止时间；只补必要恢复步骤。
4. **最终验证**：原有127项回归 + 上述故障组合；用真实 Capsule 做四阶段 smoke，强制一次来源失败、一次 Future 失败、一次后处理故障、一次 interrupted/restart，再验证定向恢复和发布消费状态。真实研究若再运行，沿用授权 corpus / 模型，启动后按约10分钟检查，不高频轮询。

验收要求是：正常路径仍使用同一 C4 thread；三个产物独立保留；可选工具与遥测故障不扩大；有效文件不被 ack 覆盖；必须的输入/身份/完整性问题明确终态；可恢复故障能只恢复必要步骤；没有无限排队/清理；部分发布可见且可定向修复。**当前尚未满足完整异常恢复验收，不能承诺后续零阻塞。**

## 8. 本次交付与边界

交付本文、36条证据清单、五类确定性探针及结果、ACL记录；修正前轮报告的 Source Capture 统计口径。未改动生产工作流代码、prompt 或源 MU 资产，未实施上述修复方案，未部署。下一轮实施应以这里的坏例为回归目标，而非再用单轮成功代替恢复性验收。
