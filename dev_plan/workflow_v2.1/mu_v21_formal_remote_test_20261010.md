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
| D1 | `mu-v21-20261010-02-d1` | PUBLISHED_PARTIAL，定点恢复中 | 四项模型正确；现补齐原 run 的新 C4 产品 |
| D2 | `mu-v21-20261010-02-d2` | 未启动 | 等待原 D1 产品补齐 |
| D3 | `mu-v21-20261010-02-d3` | 未启动 | 等待原 D2 发布 |

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

2026-10-10 01:58 CST 首次约 15 分钟检查：D1 容器 RUNNING；checkpoint 完成 `program_collection`、`c4_pre_scan`，当前 `c1`、`c3`，失败节点为空。无修复动作。

已设置本线程 15 分钟 heartbeat `mu-v2-1`，只在阶段完成、错误或需要处理时通知，继续串行启动 D2/D3 并在完成后停止跟进。

## 错误与修复

- 启动前本地定向回归 35 passed、1 failed：双 ticker D3 Build 并发测试夹具缺少新版 Agenda 所需的真实 fallback owner，在测量并发之前失败。补齐 `S0000` 后单测复验通过。该问题仅影响测试夹具，不影响正式编排或远端运行。
- 模型误配：首轮 D1 实际 Worker 请求记录为 `gpt-6-luna / max`；D3 初始化在首轮容器中配置为 `gpt-6.1-sol / medium`，D2/D3 尚未启动。停止首轮 D1 容器，取消唯一仍在运行的 C5 Worker Job（其余 3 个已完成），将首轮测试库归档为 `/data/mu-v21-test/research-wrong-model-01.sqlite3`。重新从生产库在线备份测试库，保持相同 cutoff，使用四个容器环境变量覆盖通用及 D3 初始化模型/effort 后重跑。首轮产物不进入新运行。

## 待填验收

各阶段结束后记录 Published/COMPLETE/PARTIAL 状态、关键产物、耗时、局部恢复及未解决风险。状态检查按约 15 分钟间隔进行。
