# Document2 本地 SDK Pilot

本轮将 D2 v2/v2.1 Pilot 改为由 Codex 助手调用本地 Codex SDK 执行。无需在 Codex App
逐个打开 case、发任务或观察隐藏思维链。沿用现有 case builder/coordinator、签名 Data MCP、
源输入冻结、上游 output 封存和单节点 Open Discovery，不改正式 workflow 或研究 prompt/skill。

## 执行与反馈

每个节点执行两项任务：

1. **研究 Turn**：读取冻结的 agent/skill/task/context/schema，按节点合同完成研究并交付
   `output/completion.json`。SDK 最终 JSON 可作为文件漏写时的落盘来源；文件存在时校验文件。
2. **复盘 Turn**：沿用该节点 thread，读取产物和研究时的简短问题笔记，提交结构化 Pilot
   复盘。驱动器保存 `audit/pilot_review.json` 并追加到 `audit/pilot_issues.md`。
   复盘使用只读 sandbox，正式产物目录哈希在复盘前后必须一致。

问题类型包括 bug、任务理解歧义、写作/字段组织困难、执行困难、证据/工具能力不足、质量风险。
每项说明具体位置、实际表现、影响、处理办法、是否已解决和改进建议；已解决的真实困难仍应
记录。没有问题时必须明确解释。`blocker/major/minor` 是反馈等级，不是额外研究质量门禁。
程序异常单独写执行 receipt，不冒充 Agent 自报问题；缺复盘不能视为无问题。

O0 各研究节点独立 thread；每个节点自己的研究与复盘共享 thread。同一 Shell 的 O1 四阶段
（v2）或五阶段（v2.1）共享一个 thread，后续节点以新 task 执行；各 case 仍有独立 cwd、
attempt 输入、工具权限和输出。SDK resume 时显式更新配置，离开 Discovery 后关闭其 MCP。
Open Discovery 的 Scan→Selection 仍是**一次研究 Turn**，中间调用工具冻结 checkpoint；
之后额外的复盘 Turn 只做 audit，不构成第六个业务阶段。

## 助手调用入口

在仓库目录使用 `uv run python -m doxagent.pilot.document2_sdk`。正常情况下无需设置模型，
沿用 case 的模型与 effort；可显式传入 `--model/--effort`。每 Turn 超时默认 7200 秒，
可通过 `--timeout-seconds` 调整。执行使用当前本地 Codex 登录状态，不经正式 Worker 派发研究。
源报告/Worker workspace 导出和签名权限仍需既有 runtime 配置。

从已发布 Global Research 启动（占位符须替换为真实值）：

```powershell
uv run python -m doxagent.pilot.document2_sdk start `
  --source-global-run-id <global_run_id> `
  --coordinator-id <pilot_id> `
  --runtime-env-file D:/DoxAgentPilot/runtime/.env.local `
  --document-schema-version document2.v2.1
```

默认 cases 位于 `D:/DoxAgentPilot/cases`，coordinator 位于
`D:/DoxAgentPilot/coordinators`，可用 `--cases-root/--coordinators-root` 覆盖。
现有成功 D2 可用 `--source-d2-run-id` 从本地 runtime SQLite 读取 checkpoint；也可显式
提供 `--checkpoint <json_path>`。原始 source-attempt 输入保持原字节，不能借 SDK 包装升级旧输入。
**v2 源运行须选择 `--document-schema-version document2.v2`**。

已有 case 单独执行：

```powershell
uv run python -m doxagent.pilot.document2_sdk case --case-root <case_root>
```

该入口不自动搜索其他 case 的 thread；完整 O1 连续性由 coordinator 驱动保证。
旧 App case 可直接使用：SDK 重建执行包装，冻结的研究输入保留，不要求重开 App 项目。

继续流程、选择 Shell 或限制本次执行节点数：

```powershell
uv run python -m doxagent.pilot.document2_sdk continue `
  --coordinator-id <pilot_id> --shell-key <shell_id> `
  --runtime-env-file D:/DoxAgentPilot/runtime/.env.local --max-nodes 1
```

O0 产生多个 Shell 时返回 `selection_required` 和可选身份，不擅自选一个。助手应汇报待选择项，
再以选定 Shell 继续。完成整个流程时返回 `completed`；主动限制节点数时返回 `paused`。

失败后先读取报告；需要重试时在同一命令增加 `--retry`。成功产物与成功复盘直接复用；
研究成功但复盘失败只重跑复盘。研究失败重试前将已有输出移入 audit 留证，冻结 Scan 不清空，
Discovery 按既有 checkpoint 继续 Selection。进程锁阻止同一 case/流程并发驱动。
驱动进程中断后先读取 SDK 原 Turn：仍运行就不重复启动；已完成则接纳产物/复盘。
极窄的“请求已提交但 Turn ID 未落盘”窗口保留不确定状态，需先检查 SDK thread，不能盲目重试。

只重新汇总，不调用模型：

```powershell
uv run python -m doxagent.pilot.document2_sdk report --coordinator-root <coordinator_root>
```

安装器也复制 `pilot_runtime/document2_sdk.py`；安装后的脚本读取 runtime 的 `.env.local`，
自动使用既有 cases 与 `cases/document2/_coordinators`。旧 `document2_coordinator.py` 保留为
历史 case 的低层准备/状态工具，不再作为推荐执行入口。两种入口继续同一流程时须指定相同 root。

## 助手怎样交付

命令返回 JSON 中的 `report/artifacts/manifest` 路径。每次执行（含失败或暂停）生成：

- `PILOT_REPORT.md`：各节点状态、SDK 可见过程、工具事件统计、Turn/耗时/token 使用与问题。
- `pilot_delivery.zip`：正式 output、问题文件、结构化复盘、SDK receipt、过程记录和节点报告。
- `pilot_delivery_manifest.json`：包内各节点文件的 SHA-256。

case 单独执行的交付放在 `<case_root>/pilot_delivery/`；完整流程放在 coordinator root。
包不包含 `.codex/config.toml`、签名权限、原始 input 或凭证文件；观察文本按配置中的敏感值脱敏。
审计记录失败代次留在 case 内，默认包提供当前产物与跨代次过程，需进一步追溯可查看原 case。

助手调用后应阅读报告与 issues，给用户归纳：哪些节点完成、采取了哪些可见动作、哪里困难、
怎么处理、哪些问题未解决、对 prompt/skill 或编排有什么改进建议，并附交付包和产物链接。
自动报告是事实汇总，助手的判断需与 Agent 自报、程序异常分开。

过程只保存 SDK 提供的 commentary、**公开 reasoning summary**、工具事件、状态和 usage。
不会读取或保存 reasoning item 的隐藏 `content`。未提供公开摘要时明确注明，不能据此声称掌握
完整思维链；原工具返回正文不复制进过程日志，完整研究结论由正式产物承载。
SDK 会话/事件能力参考 [官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。

## 验收边界

SDK 执行器校验当前 output_schema、对应 D2 模型及现有 context-bound 结构合同；对冻结
Pilot 上游优先于 source-runtime 的情况，校验使用相应 Pilot Candidate/Scan/Selection/累计记录，
不改写源 input。Discovery 继续调用现有冻结/覆盖/聚合校验，不添加研究质量门槛。

本轮使用离线 SDK 事件替身测试真实 SDK 类型和执行链，没有运行真实研究 Pilot 或部署。
研究资产仍由冻结输入决定，旧 prompt 与新 schema 不匹配、缺 `open-discovery.md` 等现有问题
不会被该执行器自动修复。已准备 case 若源 O1 checkpoint 与新版 Pilot topology 不一致，应通过
新 bootstrap case 研究，不能覆盖冻结记录。

最终定向检查：新增 SDK Pilot 14 项通过；既有 coordinator/checkpoint 34 项通过；D2 v2/v2.1、
O1 thread 与 D1 Pilot 相关回归 81 项通过，共 129 项不同测试通过。另有既有
`test_failed_worker_turn_persists_bounded_turn_summary` 缺 `turn_summary.json` 的失败，单独重跑
复现；本轮未修改该 Worker 路径，不计作通过。Ruff、CLI help 与 diff check 通过。
