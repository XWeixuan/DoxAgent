# INTC 正式 v2.1 远端初始化（2026-10-11）

## 范围与隔离

- 正式 D1 → D2 v2.1 → D3 v2.1；不运行 CDECR、Event Library O2、O4。
- Run prefix：`intc-v21-20261011-01`；截止：`2026-10-10T23:02:12.877682+00:00`。
- Event Library：现有 INTC Published v1，只读；发布时间 `2026-09-22T03:21:27.497514+00:00`；SHA256 `c8667116f4ec1fcf849b5d91e1d97fb1d4cf44d80ebe4cea9722469233113fd4`。
- 本地整体代码提交并推送：`ad4fdb453474885711294bd4af4f1c259d99d67c`，129 文件。原有未提交代码、prompt/skill、测试一并纳入；本地研究归档、临时 dump、凭据不纳入。
- 远端独立检出：`/home/ubuntu/doxagent-intc-v21-20261011`，已从 origin fetch 最新分支并切换到同一提交。
- 测试库：`/data/intc-v21-test/research.sqlite3`，由生产 SQLite 在线 backup 创建；新运行与生产研究结果隔离。
- 发布文件：`/data/intc-v21-test/published`。
- 隔离 Worker：`doxagent-intc-v21-worker`；workspace/JobStore：`/data/intc-v21-test/workspaces`。
- 测试镜像：`doxagent-v2:intc-v21-20261011`。直接叠加最新 `/app/src`、prompts、scripts，避免 Data MCP 子进程仍读取旧源码；健康检查 `ok=true`、`data_mcp_enabled=true`。
- 生产仓库基线：`b0f1caf50060b34483f13020a9d173ef57933afa`。未拉取、切换或重启生产 v2.0 服务。

## 模型和并发

按最新本地 `src/doxagent/codex_runtime/node_models.py` 路由，不沿用 MU 旧测试的统一覆盖：

| 节点 | 模型 / effort |
|---|---|
| C1/C3/C5、D2 O0 synthesis/finalization、全部 O1、D3 Discovery/Planning/Integration | gpt-6.1-sol / medium |
| C4 pre-scan、Future Nodes、Entity formal、D2 O0 candidate/domain review、D3 Build | gpt-6-luna / max |
| C4 Entity network | gpt-6.1-sol / high |

D2 单 ticker 并发 4；D3 单 ticker 并发 4；Worker startup concurrency 3、launch wave size 3、launch interval 2 秒；max_subagents 2、worker_subagents 0、节点 timeout 1800 秒、max_attempts 2。上述全部从本地有效 Settings 导出，并覆盖远端历史 env。

## O0 数量放行

`intc_v21_remote_initialize.py d2-o0` 使用正式 D2 原生 O0；父实现保存 final_shell_seeds 和 O0 checkpoint 后，在任何 O1 调用之前返回停点。3–6 个记录为待放行；超出范围记录为拒绝，停止后续并汇报，不强改数量。

数量合格后调用同一 prefix 的 `d2`，复用已冻结的 O0 产物、checkpoint 和工作线程；不重复 O0。该入口也复核数量，防止绕过限制。

- O0 完成前每15分钟检查。
- O0 实物核验合格并放行后改每30分钟检查。
- 错误优先同一 run 的 checkpoint/attempt 恢复，保留已成功 Worker Job，不因小问题全量重跑。
- 自动跟进：`intc-v2-1` 已启用，每15分钟；无变化静默，重大进展、故障、完成时汇报。

## 已执行

- 2026-10-11 约07:02 CST：正式 D1 容器 `doxagent-intc-v21-01-d1` 启动，容器 ID `70caca3a8182e129389f379a872fadf2b067c5369f835cfb04d4af8380e6e2d4`。
- 首次启动检查：D1/Worker 均运行；研究尚处于前置输入阶段，JobStore 尚无请求，后续检查需核验实际节点 request.model/effort。
- 新增 O0 边界测试：5 passed，Ruff 检查通过；含该测试的 D1/D2 v2.1/D3 v2.1 综合回归 86 passed（366.90秒，仅第三方 DeprecationWarning）。

## 操作与问题留痕

- 本地发现遗留 `.git/index.lock`（空文件，创建于05:33），无活跃 Git 进程；重命名留存为 `.git/index.lock.stale-20261011` 后提交推送成功。
- 预检曾误请求 `/health` 返回404；改为服务实际 `/healthz` 后健康验证通过，未影响正式研究请求。
- 远端紧凑检查：`sudo python3 /home/ubuntu/doxagent-intc-v21-20261011/status.py`。
- 启动后续阶段：`sudo python3 /home/ubuntu/doxagent-intc-v21-20261011/start_stage.py d2-o0|d2|d3`。脚本拒绝重复创建已存在的同名容器；失败恢复应先调查，再按原 run 显式恢复。

生产未切换核对：初始化容器 ID 5086f68d262f8717e9be972877ad52c18cc0ed5c8f7fafd31a919bea8af341a6，启动时间2026-10-01T21:10:21.612946584Z；生产Worker ID f67f9dc85df2c192a1fc3017cb91827b0a436a97c3500ac66294f090ce381432，启动时间2026-10-02T19:04:44.31155304Z。两者未重启。

## 2026-10-11 07:18 CST 检查及定点恢复

实际请求模型配置符合当前节点策略，但初轮8个请求全部在 START_THREAD 前失败：`failed to load configuration: Model provider `` not found`。根因是测试环境导出把本地可选 provider 的 None 写成空字符串，SDK 将其视为显式空provider名称；未进入模型研究。D1因此缺C1/C3/C5，未发布。

修复：未来容器env中删除该可选项；测试入口将空provider规范化为None（提交735ca238，5项边界测试通过）。只把修复后的入口复制到原已停止D1容器，再docker start原容器；原run ID、前置横向输入、失败attempt证据全部保留，没有新建run或全量初始化。Worker未重启，生产不动。恢复后实际请求已核实：C4 pre-scan gpt-6-luna/max、model_provider=null、status=running、无error。

同时修正操作检查脚本的record_type过滤：数据库实际为bundles/checkpoints，原脚本误写单数，仅影响状态展示。

2026-10-11 07:33 CST：原D1续跑正常。C4 pre-scan已成功；C1/C3同时运行，实际请求均gpt-6.1-sol/medium，C4为gpt-6-luna/max。2次/节点的旧failed记录均属此前provider启动故障；日志尾部旧publish blocked堆栈不是本次新故障（容器当前Up 13分钟）。尚未发布，不启动D2。

2026-10-11 07:48 CST：C1/C3/C5均已成功，C4F Future Nodes运行中。实际节点模型符合策略：C1/C3/C5 gpt-6.1-sol/medium；C4F gpt-6-luna/max。未出现新增失败，D1尚未发布，继续原run等待C4产品完成。

2026-10-11 08:03 CST：C4F Future Nodes已成功，C4E formal scan正在同一run内运行，实际gpt-6-luna/max。C1/C3/C5及pre-scan成功状态保留，无新增失败；待formal与network完成后再推进D2 O0。

2026-10-11 08:18 CST：D1已published，所有7个研究节点成功，C4三产品均完成；network实际gpt-6.1-sol/high，其余请求符合节点配置，无新增失败。原D1容器exit0（约08:15完成）。现启动正式D2的d2-o0容器，run仍为intc-v21-20261011-01-d2；仅O0，未放行O1。继续15分钟检查，待最终shell实物数量3-6才续跑。

2026-10-11 08:33 CST：O0全部8个研究请求成功且模型符合策略，停点容器exit0。已对final_shell_seeds.json实物核验6个shell，SHA256 c33b2b5e00e648c36761e85d6f4a5692f010442735522f8b88d07272d4776439：客户端计算产品市场、数据中心计算产品市场、半导体制造与Foundry服务、关键组件供应市场、企业成本与资本财务、汽车智能计算业务。数量符合3-6，已放行同D2 run的O1后续容器doxagent-intc-v21-01-d2（8bc529325e377417cd5aa466f22d07ff1ce9360885a6bcf9d6ce907913ca1398），保留已完成O0，4并发。heartbeat已更新为每30分钟检查。

2026-10-11 09:04 CST：D2正常推进，3次shell finalization已成功，其余shell在state/open_discovery/gaps等阶段运行；所有O1实际模型均gpt-6.1-sol/medium，无新增failed Worker Job。数据中心计算产品市场OPEN_DISCOVERY出现5类验收告警（selection_not_bound_at_freeze、upstream_fallback、scan_unit_not_researched、discovery_unavailable、scan_recovered_after_turn；含阶段及shell维度共10条），当前按既有checkpoint恢复/降级机制继续，保留质量告警，不为此重做已完成研究。待最终产物检查其实际影响与PARTIAL状态。

2026-10-11 09:34 CST：6个shell的state/open_discovery/realization/gaps均成功；5个finalization成功，最后1个finalization运行中。所有O1请求实际gpt-6.1-sol/medium，无新增failed，验收告警仍为此前数据中心shell的10条，无扩大。D2未发布，保持原run继续，不提前启动D3。

2026-10-11 10:04 CST：D2已published/PARTIAL，约09:36完成，6个shell全部5阶段研究成功（30个O1请求均gpt-6.1-sol/medium）。PARTIAL源于数据中心shell OPEN_DISCOVERY 5类10条验收告警，没有failed shell/新增failed Worker Job；保留告警而不全量重跑。已启动正式D3容器doxagent-intc-v21-01-d3，run intc-v21-20261011-01-d3，采用同一冻结D1/D2及Event Library v1，继续30分钟跟进。

2026-10-11 10:34 CST：D3的8个Discovery请求全部成功（6个普通shell owner及2个OPEN owner），Planning已成功3个请求、另1个运行中；实际均gpt-6.1-sol/medium。D3无failed请求，容器正常，尚未进入Build；继续同一run等待Planning。

2026-10-11 11:04 CST：D3 Planning的7个请求全部成功（gpt-6.1-sol/medium），已进入Build；4个Build请求成功，另4个同时running，实际全部gpt-6-luna/max，确认原工作流4并发生效。D3无failed请求，继续同run执行。

2026-10-11 11:34 CST：D3 Build成功请求由4增至17，4个请求同时运行，实际均gpt-6-luna/max；Discovery 8、Planning 7成功保留。D3无failed请求，日志无新增异常，保持同一run和4并发继续。

2026-10-11 12:04 CST：D3 Build成功请求由17增至28，仍4个并行运行，实际均gpt-6-luna/max；D3无failed请求、日志无新异常，继续原run。

2026-10-11 12:34 CST：D3 Build成功请求由28增至38，当前3个running（并发配置仍4，按owner/wave可调度工作推进），实际均gpt-6-luna/max。无D3 failed、无新错误，继续原run。

2026-10-11 13:04 CST：D3 Build成功请求由38增至48，4个并行运行，实际均gpt-6-luna/max；无新增failed、日志无新错误，继续原run。

2026-10-11 13:34 CST：D3 Build成功请求由48增至59，当前1个running（配置仍4并发），实际gpt-6-luna/max；尚无Integration请求。无D3 failed或新日志错误，继续同run执行。

2026-10-11 14:04 CST：D3 Build成功请求由59增至63，当前1个running，实际gpt-6-luna/max；尚未出现Integration请求。持续有新成功请求，无D3 failed及新日志错误，保持原run等待。

2026-10-11 14:34 CST：D3初始Build的64个请求全部成功（gpt-6-luna/max），已进入Integration；Integration 4个请求成功、1个运行中，实际gpt-6.1-sol/medium。D3无failed请求、日志无新增异常，沿原run继续复核/补研/最终写入和发布。

2026-10-11 15:04 CST：D3 Integration成功请求由4增至7，1个运行中，实际均gpt-6.1-sol/medium；Build 64个成功保留，无新增Build补研请求、无D3 failed或日志错误，继续原run。

2026-10-11 15:34 CST：D3 Integration成功请求由7增至11，1个运行中，实际gpt-6.1-sol/medium；初始Build 64成功保留，无D3 failed或日志新错误，继续原run。

2026-10-11 16:04 CST：Integration成功请求由11增至13（gpt-6.1-sol/medium），进入补研Build；Build累计成功由64增至68，4个补研请求并行running，实际gpt-6-luna/max。无D3 failed或新日志错误，继续同run补研与后续复核。

2026-10-11 16:34 CST：D3 Build累计成功由68增至78，4个补研请求并行运行，实际均gpt-6-luna/max；Integration累计13成功（gpt-6.1-sol/medium），无D3 failed或新日志错误，继续原run补研。

2026-10-11 17:04 CST：D3 Build累计成功由78增至90，3个补研请求运行中，实际均gpt-6-luna/max；Integration仍13成功。无D3 failed及日志新错误，继续原run。

2026-10-11 17:35 CST：D3 Build累计成功由90增至101，3个补研请求运行中，实际均gpt-6-luna/max；Integration仍13成功。无D3 failed及日志新错误，继续原run。

2026-10-11 18:05 CST：D3主编排在runner_v21输出inventory读取处出现WorkerUnavailable并退出（约17:48）；Worker保持运行，Build累计104成功，存在CODEX_TURN_TIMEOUT补研请求（RUN_TURN超1800秒预算）。SSH本轮一度响应延迟后恢复；主机可用内存约3.2GB、swap已用满、磁盘剩22GB，尚不能据此认定通信超时根因。代码复核：runner会复用record中已终态Worker Job，按原task/checkpoint验收输出。现仅docker start原D3容器、沿原run断点恢复，未重启Worker、未新建run、未全量重做成功节点。后续核对超时任务的实际输出验收与重试结果。

### 2026-10-11 18:35 CST — checkpoint transport recovery
D3 again exited during accepted snapshot GET replay (Server disconnected without sending a response). Worker remains alive; Build has 104 succeeded and 2 timeout jobs, Integration 13 succeeded. No new model requests during failed replay. Added bounded GET-only transport retries (3 attempts, 0.25/0.5s backoff), excluding writes and HTTP status errors. Six boundary tests and Ruff pass. Deploy only orchestration client into stopped original D3 container and resume same run; preserve all successful tasks. Host load and swap pressure observed; no OOM evidence, root cause of server disconnect not established.

### 2026-10-11 19:05 CST — scheduled check
Original D3 container running 25 minutes after checkpoint transport fix. Build Worker jobs: 112 succeeded, 3 running, 2 historical timeout failures; Integration 13 succeeded. New requests remain gpt-6-luna/max, Discovery/Planning/Integration gpt-6.1-sol/medium. No new container errors in last 30-minute logs. D1/D2 remain published; no restart or redispatch performed at this check. Continue 30-minute cadence.

### 2026-10-11 19:35 CST — scheduled check
Original D3 container healthy, up 55 minutes. Build: 118 succeeded (+6), 1 running, 2 historical timeout failures. Integration remains 13 succeeded. Actual requests retain configured Luna/max Build and Sol6.1/medium review models. No new container errors; D1/D2 unchanged. No restart or redispatch performed; next scheduled check in 30 minutes.

### 2026-10-11 20:05 CST — scheduled check
D3 continues normally into Integration: Build 119 succeeded, no running Build jobs, 2 historical timeout failures retained; Integration 16 succeeded (+3), 1 running. Actual Build Luna/max and Integration Sol6.1/medium match configuration. Original container remains running, no new errors in last 30-minute logs. D1/D2 unchanged. No restart/redispatch performed; continue 30-minute cadence.

### 2026-10-11 20:35 CST — scheduled check
D3 original container healthy. Integration 20 succeeded (+4), 1 running, actual gpt-6.1-sol/medium. Build unchanged at 119 succeeded and 2 historical timeout failures, no running Build jobs. No new container errors in last 30-minute logs; D1/D2 remain published. No restart/redispatch performed; continue 30-minute cadence.

### 2026-10-11 21:05 CST — scheduled check
D3 Integration 25 succeeded (+5), 1 running, actual gpt-6.1-sol/medium. Build remains 119 succeeded with 2 historical timeout failures. Original container healthy, no new errors in last 30-minute logs. D1/D2 unchanged; no restart or redispatch performed. Continue 30-minute cadence.

### 2026-10-11 21:35 CST — scheduled check
D3 Integration 30 succeeded (+5), 1 running, actual gpt-6.1-sol/medium. Build unchanged: 119 succeeded, 2 historical timeout failures. Original container healthy with no new errors in last 30-minute logs; D1/D2 remain published. No restart or redispatch performed; continue 30-minute cadence.

### 2026-10-11 22:05 CST — scheduled check
D3 Integration 33 succeeded (+3), 1 running, actual gpt-6.1-sol/medium. Build remains 119 succeeded, 2 historical timeout failures. Original container healthy, no new errors in last 30-minute logs; D1/D2 remain published. No restart/redispatch performed; continue 30-minute cadence.

## 最终验收（2026-10-11 22:35 CST）
D1 published，7研究节点成功；D2 published/PARTIAL，6 shells全部完成，JSON 1,199,642 bytes，SHA256 54cbb1aa9567525ca7a80057bda7a8bea5fdd7ebcefe2fc23d1ba970590f1c0c。D3于22:25:52 CST退出0，STAGED/PARTIAL，version17，173 policies；document3.json 1,238,507 bytes，SHA256 8e280dfa1a3fc7cc1d1027b674110a592a5c338d259fab356721e0aac45ddc07，runtime_projection/coverage_map/md哈希与handoff一致。D3全部Worker统计：Discovery8成功、Planning7成功、Build119成功及2历史超时、Integration37成功。D3总墙钟约12小时22分（含断点恢复），D1有效恢复运行55分21秒，D2 O0 14分4秒+后续62分15秒。
D3缺口保留：4份final edit incomplete（chiplet采购、制造税抵现金口径、股权融资每股口径、Secure Enclave公共资本条款），remaining research gaps及3条未研究late lead；不宣称质量完整，不重跑成功任务。最终产物已下载至artifacts/intc-v21-20261011-01。
生产HEAD仍b0f1caf50060b34483f13020a9d173ef57933afa，生产Worker ID及启动时间未变。生产初始化容器ID未变但StartedAt变为2026-10-11T10:27:55Z，与初始基线不同；本线程未操作该生产容器，不能声称整个测试期间生产完全未重启。测试始终独立库/源码/Worker，未迁移生产至v2.1。已删除intc-v2-1 heartbeat。
