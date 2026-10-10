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
