# MU v2.1 远端正式初始化运行记录

日期：2026-10-10（Asia/Shanghai）。本记录跟踪一次正式 D1→D2→D3 初始化，不使用 Pilot 编排。

## 运行边界

- 代码：本地 `codex/v1-retirement-cleanup` 提交 `46b545198a98667eccd188eff9a36ad58604dee6`，已推送并在远端 `/home/ubuntu/doxagent-mu-v21-20261010` 独立检出。
- 生产：`/home/ubuntu/doxagent` 仍为 `b0f1caf50060b34483f13020a9d173ef57933afa`，运行容器及 v2.0 默认入口未切换。
- 运行方式：正式 D1 Global Research、`document2.v2.1` 和 D3 `v2.1` 编排器依次手工启动。明确跳过 CDECR、新 O2 和 O4；D2/D3 只读引用已有的 MU Published Event Library v1（2026-09-11 10:49:16 UTC 发布）。
- 数据隔离：从生产 `/data/research/research.sqlite3` 在线备份到 `/data/mu-v21-test/research.sqlite3`，新编排写入测试库；测试容器复用现有远端 Codex Worker 和 `/data/workspaces`，使用独立 run ID。
- 固定截止：`2026-10-09T17:42:05+00:00`。run prefix `mu-v21-20261010-01`。
- 模型纠正：用户要求所有节点实际调用 `gpt-6-sol / high`。首轮误用生产默认 `gpt-6-luna / max`，首轮运行已停止并作废；正确模型重跑使用 run prefix `mu-v21-20261010-02`，固定同一截止与 Event Library v1。

## 阶段状态

| 阶段 | Run ID | 状态 | 备注 |
| --- | --- | --- | --- |
| D1 | `mu-v21-20261010-01-d1` | RUNNING | 远端容器 `doxagent-mu-v21-d1` 已启动 |
| D2 | `mu-v21-20261010-01-d2` | PENDING | 等待 D1 Published handoff |
| D3 | `mu-v21-20261010-01-d3` | PENDING | 等待 D2 Published handoff |

以上为首轮历史状态，已于发现模型不符后作废。现行运行：

| 阶段 | Run ID | 状态 | 备注 |
| --- | --- | --- | --- |
| D1 | `mu-v21-20261010-02-d1` | PUBLISHED，C4 产品补齐 | 原 run 定点续跑，三个独立产品均 available |
| D2 | `mu-v21-20261010-02-d2` | PUBLISHED / PARTIAL | 12 个 shell 均完成；OPEN_DISCOVERY 验收告警使发布状态为 PARTIAL |
| D3 原运行 | `mu-v21-20261010-02-d3` | STAGED / PARTIAL，version 21 | 保留原暂存版本，不覆盖 |
| D3 定点续跑 | `mu-v21-20261010-02-d3-resume1` | STAGED / PARTIAL，version 22 | 105 个 Build、8 个补研 wave、26 个 Integration 任务全部完成；剩余研究缺口见最终验收 |

第三轮误启动记录（已停止，不再使用）：

| 阶段 | Run ID | 状态 | 备注 |
| --- | --- | --- | --- |
| D1 | `mu-v21-20261010-03-d1` | STOPPED | 用户要求断点续跑；已取消其唯一 Worker Job |
| D2 | `mu-v21-20261010-03-d2` | 未启动 | 不再使用 |
| D3 | `mu-v21-20261010-03-d3` | 未启动 | 不再使用 |

新 D1 首个真实 Worker 请求 `c4_pre_scan` 已核对为 `model=gpt-6-sol`、`effort=high`，状态 RUNNING。后续阶段仍逐一核对实际请求，不能仅依赖环境预检。

2026-10-10 02:27 CST 正确模型首轮约 15 分钟检查：D1 容器 RUNNING；checkpoint 完成 `program_collection`、`c4_pre_scan`，当前 `c1`、`c3`，失败节点为空。Worker JobStore 中 C4、C1、C3 三个真实请求均为 `gpt-6-sol / high`（C4 成功，C1/C3 运行中）。无干预。

2026-10-10 02:42 CST：D1 完成 `c1`、`c3`、`agent_normalization`，当前 `c5`，失败节点为空；C4/C1/C3 已成功，C5 运行中，四个真实 Worker 请求均为 `gpt-6-sol / high`。无干预。

2026-10-10 02:57 CST：第二轮 D1 以 `published` 退出，C4/C1/C3/C5 四个真实请求均为 `gpt-6-sol / high`。但 `c4f_future_nodes` 的提交被生产 Worker 以 HTTP 422 拒绝，`c4e_formal_scan`、`c4e_network_build` 因前置产品缺失被跳过；发布 bundle 的三项 `c4_product_status` 全为 `failed`，Future Nodes、Entity Relations 为空。这不是可接受的 v2.1 上游完整度，未启动 D2。

根因复核：生产 Worker 的 `CodexD1Node` 枚举仍只有旧 C4 节点，不含 `c4f_future_nodes`、`c4e_formal_scan`、`c4e_network_build`。在同一远端服务器和 Docker 网络启动独立 `doxagent-mu-v21-worker`：从新代码检出加载 Worker，workspace/job store 为 `/data/mu-v21-test/workspaces`，与生产 Worker 分离；健康检查 `data_mcp_enabled=true`，服务 token/capability secret 与初始化容器匹配。生产 Worker 未升级或重启。

因第二轮 D1 已发布且其结果冻结为部分 C4 产品，原生 `run()` 会直接返回该 bundle，不能用原 run ID 补写已发布产物。本次是关键上游产品实际缺失，不属于小故障；保留第二轮证据，使用新 run prefix `mu-v21-20261010-03` 和同一截止，在隔离 Worker 上重跑正式 D1，再按新 ID 启动 D2/D3。第三轮 D1 容器为 `doxagent-mu-v21-03-d1`；四项模型/effort 覆盖保持 `gpt-6-sol / high`，Worker 地址改为 `http://doxagent-mu-v21-worker:8791`。

**纠正（用户明确要求仅断点续跑）**：第三轮 D1 刚启动即已停止，唯一的 C4 预扫描 Worker Job 已取消，第三轮不再使用。以上“需要新 run 全量重跑”的判断错误；旧版 `run()` 的 published 快捷返回并不等于无法定点修补缺失产品。恢复方案改为原 `mu-v21-20261010-02-d1`：把该 run 的 8.4 MB workspace 与 14 MB `.control` 证据复制给独立 Worker，校验原产物 SHA，按原 checkpoint 仅补跑 C4F、C4E formal、C4E network，发布新产品引用并更新原 bundle，保留现有 C1/C3/C5 与原文档 handoff。执行前先使用 `--check` 做只读预检。

只读预检通过：原 C4 pre/C1/C3/C5 四个完成产物、6 个横向状态、17 条归一化观察均可经独立 Worker 按 SHA 读取；恢复模型/effort 为 `gpt-6-sol / high`。执行前对测试 SQLite 另做 `/data/mu-v21-test/research-before-c4-resume.sqlite3` 快照。随后启动 `doxagent-mu-v21-02-c4-resume`，原 run ID 不变；自动跟进已改为只检查此容器及同一 run 的 C4 checkpoint，不再发起新 D1。

首次 C4F 恢复 attempt `c4f_future_nodes-2-93f7bd8825` 的真实 Worker 请求为 `gpt-6-sol / high`，但在 START_THREAD 因 Data MCP 握手失败，Worker 经过内部基础设施恢复仍失败。确认新 Worker 主进程通过 `PYTHONPATH` 读取新源码，而 Codex SDK 启动的 MCP 子进程没有继承该路径：在隔离 Worker 内取消 `PYTHONPATH` 后，Python 实际加载 `/app/src/doxagent` 旧版，节点枚举不含 C4F。此错误没有触发已完成节点重跑。

修复仅针对隔离测试 Worker：用测试专用 Dockerfile 从现有 Worker 镜像叠加当前 `src/` 到 `/app/src/`，构建 `doxagent-v2:mu-v21-worker-20261010`；验证无 `PYTHONPATH` 的 Python 仍识别 C4F、健康检查 Data MCP 启用。停掉旧隔离 Worker 并以新镜像在同一测试 workspace/job store 位置启动，生产 Worker 未动。原 run ID 上的新恢复容器为 `doxagent-mu-v21-02-c4-retry2`，只重试 C4F 起的缺失节点。

2026-10-10 03:50 CST：同一 `-02` run 的 C4F 已成功，C4E formal 正在执行，C4E network 等前置；新 C4F 与 C4E formal 的真实 Worker 请求均为 `gpt-6-sol / high`。已完成的 C1/C3/C5 没有新增请求。bundle 的产品状态仍保持旧 failed 值，按恢复入口设计将在三个产品均通过并发布后一次性更新。

2026-10-10 04:05 CST：C4E formal 已成功，C4E network 正在执行；checkpoint 失败节点已清空。C4F、formal、network 三个新真实请求均为 `gpt-6-sol / high`。bundle 尚未更新，等待 network 完成后一次性发布。

2026-10-10 04:14 CST：原 `mu-v21-20261010-02-d1` 的三个 C4 产品已定点续跑完成并发布；checkpoint 当前/失败节点均空。bundle 三项状态全部 `available`，包含 16 个 Future Nodes、60 条 Entity Relations、12,972 字符 network 报告；新增三个产品报告已列入原 bundle，原 C1/C3/C5 与文档 handoff 未重新执行。C4F/C4E formal/C4E network 成功的真实 Worker 请求全部为 `gpt-6-sol / high`。随后启动正式 D2 v2.1 容器 `doxagent-mu-v21-02-d2`，使用同一 prefix、截止、测试库和独立 Worker。

2026-10-10 04:35 CST：D2 容器仍在运行，O0 已产出 C1/C3/C5 候选、综合与三路复核，`d2_o0_finalization` 正在执行；bundle 为 draft，checkpoint 无失败节点。独立 Worker JobStore 中上述 O0 各任务的实际 `request.model/effort` 均为 `gpt-6-sol / high`。保持原 D2 run 继续。

2026-10-10 04:50 CST：D2 O0 完成，8 个 shell 启动 O1；7 个 `d2_o1_open_discovery` 成功，多个 `d2_o1_state` 正在运行。OPEN_DISCOVERY 的验收元数据已记录 `selection_not_bound_at_freeze`、`upstream_fallback`、`scan_unit_not_researched`、`discovery_unavailable`、`scan_recovered_after_turn`，原因是节点结束时缺少已冻结的有效 Scan，编排从 canonical shell 恢复空扫描并对 Selection 使用上游兜底；按现行发布规则这会使 D2 为 `PARTIAL`。此时未发生执行失败；不重跑已完成的 O1 节点，保留告警供完成后评估。O1 实际 Worker 请求均为 `gpt-6-sol / high`。

2026-10-10 06:20 CST：D2 全部 12 个 shell 已完成且无 shell 错误，73 个本轮可见 Worker 请求均为 `gpt-6-sol / high`；组装后的文档在发布时触发 `PUBLISHED_DOCUMENT_STORAGE_REQUIRED for Document2 artifact`，容器 exit 1，bundle 记为 failed、无 handoff。根因是隔离测试入口没有给 D2 编排器传入 PublishedDocumentStorage，超出 2 MiB 的文档不能直接入库。修复为测试数据卷内的 `/data/mu-v21-test/published` 文件存储，并将同一存储注入 D3 输入读取；仅恢复原 `mu-v21-20261010-02-d2` 的已完成 checkpoint，不再发起任何 shell Worker 任务。

2026-10-10 06:25 CST：原 D2 容器重启后在同一 `-02-d2` run 成功发布 `PARTIAL` handoff，恢复前后 Worker JobStore 最大 sequence 均为 725，未重做任何 shell。发布 16 份文档文件于隔离测试数据卷；Document2 JSON 为 2,386,738 bytes，文件 SHA-256 与 bundle 引用一致。`PARTIAL` 的原因是 35 条 OPEN_DISCOVERY/验收告警，不是 shell 执行失败；独立的 `citation_status` 也为 `PARTIAL`。随后启动正式 D3 v2.1 容器 `doxagent-mu-v21-02-d3`，实际容器进程环境读取的通用与 D3 初始化模型/effort 均为 `gpt-6-sol / high`；后续仍需用 Worker JobStore 核对实际请求。

2026-10-10 06:35 CST：D3 进入 `SHELL_DISCOVERY`，已调度四个普通 shell owner（其中一个完成、三个运行）；首批四个实际 Worker Job 均为 `d3_o3_discovery`、`gpt-6-sol / high`。D3 容器运行中且日志无错误。

2026-10-10 07:05 CST：D3 的 12 个普通 shell Discovery 全部完成，阶段推进到 `OPEN_DISCOVERY`；`open_research`、`open_event` 两个独立 OPEN owner 正在运行。累计 14 个 D3 Worker Job（12 成功、2 运行）全部实际 `gpt-6-sol / high`；任务错误与编排 diagnostics 均为空。

2026-10-10 07:20 CST：双 OPEN Discovery 均完成，D3 进入 GLOBAL `PLANNING`。目前 16 个任务完成、1 个 Planning 任务运行；17 个实际 Worker Job 全为 `gpt-6-sol / high`，无任务错误或编排 diagnostics。

2026-10-10 08:50 CST：D3 Planning 完成，254 条 Discovery Lead 经 13 批 Planning 形成 133 个 Topic，进入 `BUILD`，四个 Build 任务并行。32 个实际 Worker Job 全为 `gpt-6-sol / high`；无任务错误或编排 diagnostics。Topic 数量相对 shell/Lead 规模偏大，暂不据此中断正式运行，完成后评估是否存在过度拆分和后续节点负担。

2026-10-10 09:05 CST：D3 Build 容器 exit 1，错误为读取 `S0001/0000/20260930_price_table.jpg` 时 Worker 的文本文件接口 UTF-8 解码失败并返回 500。此时 34 个 D3 Worker Job 全为 `gpt-6-sol / high`；Build 已成功的 Job 不因该读取错误作废。根因是 v2.1 Runner 把输出目录的每个清单文件都调用 `read_text`，未区分二进制研究附图与可纳入 JSON/JSONL/Markdown 快照的文本产物。修复为只对约定的文本后缀收集/冻结内容，二进制文件保留在 owner workspace、不纳入结构化交付；随后沿同一 D3 run 的任务状态恢复，避免重新研究成功任务。

2026-10-10 09:12 CST：原 D3 容器同一 run 重启，进程已加载二进制过滤修复。S0001 首个 Build 任务从已成功的 Worker Job 完成输出验收，持久状态转 `COMPLETED`，`attempt_count` 仍为 1，收集 8 个文本文件；不需重做该任务研究。修复回归 61 passed，Ruff/编译通过，本地代码已推送 `8aba9686`；远端仅更新独立测试检出的 Runner 文件，生产镜像/仓库未动。

2026-10-10 01:58 CST 首次约 15 分钟检查：D1 容器 RUNNING；checkpoint 完成 `program_collection`、`c4_pre_scan`，当前 `c1`、`c3`，失败节点为空。无修复动作。

已设置本线程 15 分钟 heartbeat `mu-v2-1`，只在阶段完成、错误或需要处理时通知，继续串行启动 D2/D3 并在完成后停止跟进。

## 错误与修复

- 启动前本地定向回归 35 passed、1 failed：双 ticker D3 Build 并发测试夹具缺少新版 Agenda 所需的真实 fallback owner，在测量并发之前失败。补齐 `S0000` 后单测复验通过。该问题仅影响测试夹具，不影响正式编排或远端运行。
- 模型误配：首轮 D1 实际 Worker 请求记录为 `gpt-6-luna / max`；D3 初始化在首轮容器中配置为 `gpt-6.1-sol / medium`，D2/D3 尚未启动。停止首轮 D1 容器，取消唯一仍在运行的 C5 Worker Job（其余 3 个已完成），将首轮测试库归档为 `/data/mu-v21-test/research-wrong-model-01.sqlite3`。重新从生产库在线备份测试库，保持相同 cutoff，使用四个容器环境变量覆盖通用及 D3 初始化模型/effort 后重跑。首轮产物不进入新运行。

## 待填验收

各阶段结束后记录 Published/COMPLETE/PARTIAL 状态、关键产物、耗时、局部恢复及未解决风险。状态检查按约 15 分钟间隔进行。

2026-10-10 16:20 CST 只读复核（用户要求暂停续跑）：D3 容器 `doxagent-mu-v21-02-d3` 已于 11:36:37 CST 退出，exit 0；原 run `mu-v21-20261010-02-d3` 的持久状态是 `STAGED / PARTIAL`，版本 21 已暂存，非完整业务验收。冻结计划为 254 条 Discovery Lead、13 批 Planning、133 个 Topic、105 个 Build wave（77 个单题、28 个双题）；14 个 Discovery 均完成。Build wave 为 31 完成、2 部分完成、72 失败；Integration 的 7 个主审批次及 consolidation 全部失败。隔离 Worker JobStore 记录 220 次 D3 请求，其中 58 成功、162 失败；多数失败记录为 `usage_limit_exceeded`，请求次数不能直接换算为额度消耗。现有 148 份基础 Policy 草稿、44 个 Topic 结果，但 Review 缺失，coverage 为空，98 项 missing，最终暂存仍是 PARTIAL。`initialize` 遇到现存 `commit` 会直接重走发布，不能凭原命令恢复 Build；版本 21 的 staged set 也有不可变校验。逻辑续跑点是冻结 Planning 后的缺失/部分完成 Build wave，保留已完成的 31 个 wave 与上游节点，之后再做 Integration；实际恢复入口需先解决已暂存提交与失败任务两次 attempt 均耗尽的问题。用户未授权启动续跑，本轮没有发起任何 Worker Job；15 分钟 heartbeat 已暂停。

2026-10-10 16:44 CST：用户授权仅续 72 个失败和 2 个部分完成的 Build wave，后续模型统一改为 `gpt-6-luna / max`。由于原 version 21 暂存不可改写，建立 successor D3 run `mu-v21-20261010-02-d3-resume1`，承接原冻结输入、14 个 Discovery、13 个 Planning、31 个完成 Build、已接纳 Policy ID 与快照；原 D1/D2 和 version 21 均不动。测试 SQLite 已先备份为 `research-before-d3-resume1.sqlite3`，恢复入口只在隔离测试库/Worker 工作。本地 D3 回归 41 passed，恢复断言 2 passed，Ruff 通过。先启动 `doxagent-mu-v21-02-d3-resume-canary`，单并发只调度首个未完成 wave；隔离 Worker JobStore 已核实首个真实请求 `gpt-6-luna / max`，后续待此 wave 结束再放行。

2026-10-10 16:55 CST：canary `research:S0001:3` 已 `COMPLETED`，唯一新 Worker Job 2942 `succeeded`，实际请求 `gpt-6-luna / max`。容器以 `WAVE_LIMIT_REACHED` 正常退出，未越过一个 wave。随后启动 `doxagent-mu-v21-02-d3-resume-main`，在同一 successor run 上按原 wave 顺序处理其余未完成项，单并发、首个失败即停；完成 Build 后才运行后续 Integration。15 分钟自动跟进已重新启用，提示词明确不得重复启动 main 或发 Sol 请求。

2026-10-10 16:58 CST：main 容器仍在运行，首个 main Worker Job 2952 实际请求也已核对为 `gpt-6-luna / max`；没有新 Sol 请求。后续按约 15 分钟检查。

2026-10-10 17:04 CST：用户指出单并发吞吐不符合原 D3 设计，已立即停止 `doxagent-mu-v21-02-d3-resume-main`，不取消其正在运行的 Worker Job 2952。停机时同一 successor run 的 Build 状态为 32 `COMPLETED`、1 `RUNNING`、72 `PENDING`；没有重置任何任务。恢复脚本改用原 D3 每 ticker 4 并发的 owner 队列调度，保持每个 owner 的 wave 顺序、成功任务复用、失败即停；启动 `doxagent-mu-v21-02-d3-resume-parallel` 接续同一 run，并继续强制 `gpt-6-luna / max`。原版本 21、D1/D2、生产 v2.0 均未改动。

2026-10-10 17:06 CST：并发启动验收：恢复容器仍在运行，新 Worker Job 2962、2966、2967 处于 `running`，2974 处于 `queued`，四项实际请求均为 `gpt-6-luna / max`；Job 2952 已成功，待其原任务由 owner 队列接续并验收。Build checkpoint 为 32 完成、5 记录为运行中、68 待处理，运行中记录包含前一容器留下的持久任务；并发槽上限仍为 4。定向回归 36 passed，Ruff 通过。

2026-10-10 17:20 CST：15 分钟例行检查，四并发恢复容器正常运行，D3 保持 `BUILD`，Build checkpoint 为 36 完成、5 持久记录运行中、64 待处理，无失败任务。隔离 Worker 当前恰有 4 个运行中请求，续跑累计 10 个请求（6 成功、4 运行），全部实际 `gpt-6-luna / max`。没有重启、重置任务或新增 run；继续原进程。

2026-10-10 17:35 CST：例行检查，恢复容器正常运行，D3 仍为 `BUILD`；38 个 Build wave 完成、62 待处理、5 持久记录运行中，无失败任务。续跑累计 12 个 Worker 请求（9 成功、3 运行），实际模型全部 `gpt-6-luna / max`。保留原进程及检查点，未发起任何重跑或配置变更。

2026-10-10 17:50 CST：例行检查，恢复容器正常运行，D3 仍为 `BUILD`；43 个 wave 完成、4 运行中、58 待处理，无失败任务。续跑累计 16 个 Worker 请求（12 成功、4 运行），全部实际 `gpt-6-luna / max`。前一容器留下的已成功 Worker Job 已完成任务验收，当前持久任务数与四并发运行一致；继续原进程，无重跑。

2026-10-10 18:05 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；47 个 wave 完成、4 运行中、54 待处理，无失败任务。续跑累计 20 个 Worker 请求（16 成功、4 运行），实际模型全部 `gpt-6-luna / max`；继续原进程，无重启或任务重置。

2026-10-10 18:20 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；53 个 wave 完成、3 运行中、49 待处理，无失败任务。续跑累计 25 个 Worker 请求（22 成功、3 运行），实际模型全部 `gpt-6-luna / max`。Build 完成数已过半；继续原四并发调度进程，无重跑或配置变更。

2026-10-10 18:35 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；56 个 wave 完成、4 运行中、45 待处理，无失败任务。续跑累计 29 个 Worker 请求（25 成功、4 运行），全部实际 `gpt-6-luna / max`。继续原四并发调度进程，未重启、重置或另起 run。

2026-10-10 18:50 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；62 个 wave 完成、4 运行中、39 待处理，无失败任务。续跑累计 35 个 Worker 请求（31 成功、4 运行），实际模型全部 `gpt-6-luna / max`。继续原四并发调度进程，无重跑或配置变更。

2026-10-10 19:05 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；67 个 wave 完成、4 运行中、34 待处理，无失败任务。续跑累计 40 个 Worker 请求（36 成功、4 运行），实际模型全部 `gpt-6-luna / max`。继续原四并发调度进程，无重启、重置或另起 run。

2026-10-10 19:20 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；74 个 wave 完成、4 运行中、27 待处理，无失败任务。续跑累计 47 个 Worker 请求（43 成功、4 运行），全部实际 `gpt-6-luna / max`。继续原四并发调度进程，无重跑或配置变更。

2026-10-10 19:35 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；78 个 wave 完成、4 运行中、23 待处理，无失败任务。续跑累计 51 个 Worker 请求（47 成功、4 运行），实际模型全部 `gpt-6-luna / max`。继续原四并发调度进程，无重启、重置或另起 run。

2026-10-10 19:50 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；85 个 wave 完成、4 运行中、16 待处理，无失败任务。续跑累计 58 个 Worker 请求（54 成功、4 运行），全部实际 `gpt-6-luna / max`。继续原四并发调度进程，无重跑或配置变更。

2026-10-10 20:05 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；91 个 wave 完成、4 运行中、10 待处理，无失败任务。续跑累计 64 个 Worker 请求（60 成功、4 运行），实际模型全部 `gpt-6-luna / max`。继续原四并发调度进程，无重启、重置或另起 run。

2026-10-10 20:20 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；96 个 wave 完成、4 运行中、5 待处理，无失败任务。续跑累计 69 个 Worker 请求（65 成功、4 运行），实际模型全部 `gpt-6-luna / max`。继续原四并发调度进程，无重跑或配置变更。

2026-10-10 20:35 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；100 个 wave 完成、2 运行中、3 待处理，无失败任务；Discovery 14 与 Planning 13 个完成任务仍保留。续跑累计 71 个 Worker 请求（69 成功、2 运行），全部实际 `gpt-6-luna / max`。尾部按 owner 队列顺序继续，四并发上限未改动，未启动 Integration 或任何重跑。

2026-10-10 20:50 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；102 个 wave 完成、1 运行中、2 待处理，无失败任务；Discovery/Planning 完成状态保留。续跑累计 72 个 Worker 请求（71 成功、1 运行），全部实际 `gpt-6-luna / max`。尾部继续按同一 owner 的 wave 顺序执行，四并发配置未降低，无重跑或任务重置。

2026-10-10 21:05 CST：例行检查，恢复容器正常运行，D3 保持 `BUILD`；103 个 wave 完成、1 运行中、1 待处理，无失败任务；Discovery/Planning 完成状态保留。续跑累计 73 个 Worker 请求（72 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原进程完成同一 owner 的尾部 wave，未重启、重置或另起 run。

2026-10-10 21:20 CST：Build 定点续跑完成：105 个 wave 全部 `COMPLETED`，原已完成 31 个保留，原 72 个失败与 2 个部分完成共新增 74 个 Worker 请求，全部 `succeeded`，实际模型全部 `gpt-6-luna / max`，没有新增失败任务。Discovery 14、Planning 13 完成任务保留。容器仍运行，run phase 暂为 `BUILD`，尚无 Integration 任务；让同一进程继续标准 Integration/发布路径，不另起任务或重跑研究。

2026-10-10 21:35 CST：同一容器已进入 `INTEGRATION`，首个 Integration 任务正在运行，Build 105 个完成状态保留，无失败任务。新 Worker Job 3682 实际请求为 `gpt-6-luna / max`；续跑累计 75 个请求（74 成功、1 运行），模型全部一致。继续同一正式流程，未重跑 Discovery/Planning/Build。

2026-10-10 21:50 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 1 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 76 个 Worker 请求（75 成功、1 运行），全部实际 `gpt-6-luna / max`。继续原流程，无重跑或配置变更。

2026-10-10 22:05 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 2 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 77 个 Worker 请求（76 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原流程，无重启、重置或另起 run。

2026-10-10 22:20 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 3 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 78 个 Worker 请求（77 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原流程，无重跑或配置变更。

2026-10-10 22:35 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 4 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 79 个 Worker 请求（78 成功、1 运行），全部实际 `gpt-6-luna / max`。继续原流程，无重启、重置或另起 run。

2026-10-10 22:50 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 5 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 80 个 Worker 请求（79 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原流程，无重跑或配置变更。

2026-10-10 23:05 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 7 个任务完成、1 个运行中，Build 105 个完成状态保留，无失败任务。续跑累计 82 个 Worker 请求（81 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续同一正式流程，无重启、重置或另起 run。

2026-10-10 23:20 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 10 个任务完成，当前 `integration:main:10` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 85 个 Worker 请求（84 成功、1 运行），全部实际 `gpt-6-luna / max`。继续主审查的原分批流程，无重跑或配置变更。

2026-10-10 23:35 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 12 个任务完成，当前 `integration:main:12` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 87 个 Worker 请求（86 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原主审查分批流程，无重启、重置或另起 run。

2026-10-10 23:50 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 13 个任务完成，当前 `integration:main:13` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 88 个 Worker 请求（87 成功、1 运行），全部实际 `gpt-6-luna / max`。继续原主审查分批流程，无重跑或配置变更。

2026-10-11 00:05 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 15 个任务完成，当前 `integration:main:15` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 90 个 Worker 请求（89 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原主审查分批流程，无重启、重置或另起 run。

2026-10-11 00:20 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 16 个任务完成，当前 `integration:main:16` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 91 个 Worker 请求（90 成功、1 运行），全部实际 `gpt-6-luna / max`。继续原主审查分批流程，无重跑或配置变更。

2026-10-11 00:35 CST：例行检查，容器正常运行，D3 保持 `INTEGRATION`；Integration 19 个任务完成，当前 `integration:main:19` 运行中，Build 105 个完成状态保留，无失败任务。续跑累计 94 个 Worker 请求（93 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续原主审查分批流程，无重启、重置或另起 run。

2026-10-11 00:50 CST：主 Integration 已完成 21 个任务，流程自动进入 `SUPPLEMENT`：冻结补研计划含 10 个 Topic、8 个 wave，当前 4 个补研任务并发运行（OPEN_RESEARCH、S0001、S0005、S0006），无失败任务。Build 105 个完成状态保留。续跑累计 99 个 Worker 请求（95 成功、4 运行），全部实际 `gpt-6-luna / max`。这是主审查产生的标准后续补研，未重复原 Build；继续同一进程，随后按原流程复核/最终写入与发布。

2026-10-11 01:05 CST：例行检查，容器正常运行，D3 保持 `SUPPLEMENT`；补研 8 个 wave 中 6 完成、2 运行中（OPEN_RESEARCH、S0006），无失败任务；原 Build 105、主 Integration 21 完成状态保留。续跑累计 103 个 Worker 请求（101 成功、2 运行），实际模型全部 `gpt-6-luna / max`。继续同一流程，无重跑或配置变更。

2026-10-11 01:20 CST：补研 8 个 wave 全部完成，Integration 累计 22 个任务全部完成，流程进入 `FINALIZE`；容器仍运行，当前无运行中 Worker Job、无失败任务，尚无 commit。续跑累计 104 个 Worker 请求全部成功，实际模型全部 `gpt-6-luna / max`。继续同一进程完成最终写入/验收及发布，不重复已有研究或审查。

2026-10-11 01:35 CST：例行检查，容器正常运行，D3 保持 `FINALIZE`；Integration 累计 25 个任务完成，`integration:consolidation` 正在运行，Build 105 与补研 8 个完成状态保留，无失败任务，尚无 commit。续跑累计 108 个 Worker 请求（107 成功、1 运行），实际模型全部 `gpt-6-luna / max`。继续同一最终写入/整合流程，无重跑或配置变更。

## 最终验收（2026-10-11 01:50 CST）

同一恢复容器于 01:45:00 CST 正常退出，exit 0；D3 于 01:44:57 CST 提交隔离测试库的 version 22，并完成四文件发布副本验证。最终是 **STAGED / PARTIAL**，不是完整业务验收通过。没有追加新的研究轮次，也没有将该版本切入生产。

| 阶段 | 最终结果 | 时间边界与耗时 |
| --- | --- | --- |
| D1 | Published；C4F/C4E formal/C4E network 均 available，原 C1/C3/C5 保留 | 10-10 02:11:33 首次启动，04:14:09 缺失产品恢复容器结束；含排障约 2 小时 2 分 36 秒。bundle 原 published_at 保留首次发布时间，不能拿它当补齐完成时间 |
| D2 v2.1 | Published / PARTIAL；12 个 shell 全部完成，35 条 OPEN_DISCOVERY 验收告警保留 | bundle 04:20:42→06:25:52，约 2 小时 5 分 9 秒；发布故障恢复没有新增 Worker Job |
| D3 原运行 | version 21 STAGED / PARTIAL；原 31 个 Build wave 成功，其余受额度错误影响 | 10-10 06:25 后启动，11:36:37 退出；期间二进制读取故障按同一任务恢复；历史记录保留 |
| D3 定点续跑 | version 22 STAGED / PARTIAL；新增 108 次请求全部成功 | canary 10-10 16:44:15→10-11 01:45:00，总跨度约 9 小时 45 秒，包含并发切换和宿主验收时间；四并发容器自身 8 小时 41 分 2 秒 |

续跑的真实 Worker 阶段时间如下（阶段首个 started_at 至最后一个 finished_at；并行阶段的 wall time 不等于各请求耗时之和）：

| 阶段 | 请求数 | 时间跨度 | Wall time |
| --- | ---: | --- | ---: |
| 原失败/部分 Build 恢复 | 74 | 10-10 16:45:24→21:19:53 | 4 小时 34 分 29 秒 |
| Integration 主审查 | 21 | 21:21:59→10-11 00:47:10 | 3 小时 25 分 10 秒 |
| 补研 | 8 | 00:47:52→01:14:59 | 27 分 7 秒 |
| 补研后复核 | 1 | 01:15:20→01:20:43 | 5 分 24 秒 |
| 最终写入 | 3 | 01:20:57→01:33:54 | 12 分 57 秒 |
| 最终 consolidation | 1 | 01:34:09→01:44:50 | 10 分 41 秒 |

### 产物和校验

- 最终包含 **262 条 Policy**、143 项 Topic coverage disposition（133 个原 Topic＋10 个补研 Topic）。Discovery 14、Planning 13、Build 105、Supplement 8、Integration 26 个持久任务全部 COMPLETED，无 FAILED/RUNNING 任务。
- 续跑 108 次真实 Worker 请求全部 succeeded，实际 model/effort 全部 `gpt-6-luna / max`。原阶段使用 Sol/high 的历史结果按用户要求复用，未改写历史请求。
- 四份版本 22 产物及发布副本 SHA-256 全部匹配；下载至本地后再次通过四文件校验。D2 下载 JSON 仍为 2,386,738 bytes，SHA-256 `3ca00df0d85eef063dc64979f0d7175d8182d532d5b41c825f8b0bf80dd9924d`。
- [D3 可读文档](mu_v21_formal_remote_test_20261010_artifacts/artifacts/document3/releases/22/document3.md)、[D3 JSON](mu_v21_formal_remote_test_20261010_artifacts/artifacts/document3/releases/22/document3.json)、[runtime projection](mu_v21_formal_remote_test_20261010_artifacts/artifacts/document3/releases/22/runtime_projection.json)、[coverage map](mu_v21_formal_remote_test_20261010_artifacts/artifacts/document3/releases/22/coverage_map.json)。
- [D2 JSON](mu_v21_formal_remote_test_20261010_artifacts/d2/document2.json)、[D2 citation manifest](mu_v21_formal_remote_test_20261010_artifacts/d2/citation_manifest.json)、[补齐后的 D1 bundle](mu_v21_formal_remote_test_20261010_artifacts/mu-v21-d1-bundle.json)。D1 各节点 completion/report 与 D2 全部发布文件保留在同目录的 upstream-products 压缩包及解包目录。
- [最终结构化证据](mu_v21_formal_remote_test_20261010_artifacts/final_evidence.json)保存真实请求 ID/模型/时间、阶段耗时、校验值、缺口及原 version 21 不变的记录；[容器证据](mu_v21_formal_remote_test_20261010_artifacts/mu-v21-container-evidence.json)保存容器镜像、启动/退出时间及并发配置。

### PARTIAL 的实际原因与边界

宿主最终 missing 为 `remaining research gaps` 和两条 `unresearched late lead`，不是执行失败。coverage_map 有 9 项 remaining_gaps：

1. PCN 36461 缺少 Micron 原始逐料号通知、客户 BOM/平台暴露与实际续供路径。
2. 企业 SSD 盘内 DRAM 与 host/DPU RAM/总 NAND 的同负载可比生产配置不足。
3. 高端手机缺少同期间、同地区/价格/渠道的前向销量和加权容量比较面。
4. 企业 PC 缺少设备年龄/岗位、更新节奏、OEM/型号与容量配置的可复用机群分母。
5. GP1/XL-FLASH 资源分配与普通 TLC/QLC bit 供给净影响缺少可比桥接。
6. GDDR 生命周期缺少原始 PCN/EOL 及其与量产板卡/后继供货的连接。
7. AI-RAN 商用生产平台缺少内存 SKU/BOM、容量及 Micron 资格/采购位置。
8. 外部中介层/CoWoS 供给权益 late lead，路径 `output/work/v21/late/d271037bfe679bce695f459b.jsonl#L1`。
9. 大型 PC OEM 普通机型内存集中采购 late lead，路径 `output/work/v21/late/7891999c6615df10fae1cda8.jsonl#L1`。

最后两项存在覆盖核算上的潜在不一致：主 Review 的累计 coverage_notes 对前者明确说明由 S0003/0104 承接；后者的引用处理也需以最终 Policy 与 Topic ref 逐条复核。宿主关闭 late lead 的条件仅是“该精确 ref 出现在已有最终 coverage 的 Topic.ref 中”，不会解析 Review 的自然语言承接说明（orchestrator_v21.py 的 closed_refs 检查）。因此不能直接把两条 missing 都解释为完全没有研究，也不能仅凭文字说明删去缺口。此次保留原验收结果，不在发布后的不可变 version 22 上改写结果或额外启动研究；后续可用现成产物单独复核引用承接，决定是否需要修正覆盖记账。其余七项是最终 consolidation 明示的证据缺口，执行成功不代表这些事实已补齐。

### 排障与生产隔离结论

本次发生的模型误配、误启动第三轮、隔离 Worker 旧 schema/MCP 子进程版本不一致、D2 大文档发布存储缺失、D3 JPEG 被当文本读取、额度耗尽、单并发吞吐错误均已在上文逐项留痕。用户要求断点续跑后，C4 产品、D2 发布、D3 输出验收和 74 个未完成 wave 均按相应检查点恢复；最后一轮 108 次请求没有任何失败。两次不合理决策（第三轮全量 D1、单并发恢复）已撤回并纠正。

最终只读检查确认生产仓库 `/home/ubuntu/doxagent` 仍为 `b0f1caf50060b34483f13020a9d173ef57933afa`；生产 initialization 容器自 10-02 05:10 CST 持续运行，生产 Codex Worker 自 10-03 03:04 CST 持续运行，均未被本测试重建/重启。测试代码、SQLite、Worker 和 version 22 均位于独立路径/容器；生产保持 v2.0，未做迁移，也未启动测试 CDECR/O2/O4。流程已结束，停止本线程 heartbeat。

2026-10-11 追加离线导出：按最终接纳研究稿来源拆分 Sol/high 140 条与 Luna/max 122 条，完整版本22不变。按ID首次出现原是148/114，其中8条保留原ID但由Luna重新交付，归入Luna；Sol组另8条经Luna最终条件编辑，来源明细均已标注。另提取262条Policy标题与全部576条condition criterion。文件与口径见 mu_v21_formal_remote_test_20261010_artifacts/model_split/README.md；只读追溯，无模型调用、无远端变更。
