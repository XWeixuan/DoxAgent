# D3 本地 SDK Pilot 与资产接边修复

日期：2026-10-07。已实现，由助手通过 CLI 驱动，不需要在 Codex App 启动 Pilot 任务。默认正式 workflow 仍为 V2；本轮没有生产启用或真实研究调用。

## 1. Pilot 的新工作方式

复用实际 D3 编排、文件交付、schema、owner 会话、恢复与发布流程，替换传输为本地 Codex SDK。每个业务调用结束后，在同一 thread 单独执行一次只读 Pilot 复盘；分批 Planning/Integration 与补研的调用也逐次复盘。研究过程中允许追加本 attempt 的 `audit/pilot_issues.md`，结束复盘由宿主将结构化结果追加到同一文件。

与 D2 已有 SDK Pilot 共用问题 schema，支持 bug、task_ambiguity、writing_difficulty、execution_difficulty、evidence_limitation、quality_risk；每条包含位置、具体表现、影响、处理办法、建议和是否解决。没有问题要明确给出理由；不要求每类都报，不编造问题。不把复盘当作继续研究或另一轮业务修改。

研究与复盘分别保留公开过程日志、公开 reasoning summary（SDK 实际提供时）、工具/命令事件、usage、耗时及执行回执。隐藏 reasoning 内容不读取、不存储；SDK 没提供公开摘要时报告明确说明，不编造思维链。汇总报告提供节点状态、过程摘要和全部 issues，完整事件留在 JSONL。

复盘关闭 Data MCP、source capture、其他操作工具和 Web；后续研究重新按自身任务启用权限。研究产物先保存，复盘中断后只恢复复盘；复盘异常记录独立 audit error，不伪装成无问题，也不清除业务成果。若复盘意外改研究文件，宿主恢复文件并记错。已经完成的任务不重复执行；SDK 研究中断时通过已记录 thread/turn 查询终态恢复，未确定是否结束时不另开重复研究。

## 2. 助手调用接口

```powershell
uv run python -m doxagent.pilot.document3_driver start --pilot-root D:/DoxAgentPilot/document3/<pilot-id> --request <request.json> --runtime-env-file <runtime.env> --source-snapshot-db <source.sqlite> --source-workspaces <source-workspaces>
uv run python -m doxagent.pilot.document3_driver continue --pilot-root D:/DoxAgentPilot/document3/<pilot-id> --runtime-env-file <runtime.env>
uv run python -m doxagent.pilot.document3_driver report --pilot-root D:/DoxAgentPilot/document3/<pilot-id>
```

`scripts/codex_document3_pilot.py` 的 start/continue/report 同样转入这个驱动器；原 `--run-id` 只读指标评估保留，不负责启动 App 任务。start/continue 可指定 `--model`、`--effort`，不指定则沿用当前配置。report 不调用模型。

初始化 request 示例（来源 ID 必须替换为真实输入）：

```json
{
  "orchestration_version": "v2.1",
  "mode": "initialize",
  "kwargs": {
    "ticker": "MU",
    "run_id": "d3-sdkpilot-mu-example",
    "as_of": "2026-10-07T00:00:00Z",
    "document2_run_id": "published-source-d2-run",
    "source_global_run_id": "published-source-global-run"
  }
}
```

v2.1 的 D2/Global 可空，additional_materials 等与正式 initialize 接口一致；mode=maintain 时 kwargs 使用 base_policy_version、maintenance_feed、delta 等实际接口，base 必须存在于本地隔离数据库。orchestration_version=v2 时调用旧编排和 legacy 资产，参数也遵循旧接口，不将新 v3 base 交给旧 maintain。

Pilot 创建独立 `runtime.sqlite` 和 `workspaces/`。`--source-snapshot-db` 使用只读 SQLite backup 复制来源；`--source-workspaces` 仅导入 Global 已登记引用的原始文件并核对 hash。D2 正式 JSON 从已复制的 Published 元数据/已配置只读存储读取；缺来源会按实际 preparer 规则报错/记录缺项，不会假装构造研究输入。读取外部 Published 源可保留原配置；旧 V2 的发布 put 则改为仅捕获到 Pilot 本地，不能写外部发布存储。Pilot 不使用线上数据库作为自己的写入库。

模型调用需要当前本地 SDK 登录与配置的实际工具环境；本轮提供驱动能力，没有擅自选择真实研究 corpus 执行。后续助手按指定 corpus 调用后，应读取报告及具体节点过程/产物，结合结构校验与业务成果总结，再向用户交付。

## 3. 交付位置

| 文件 | 内容 |
| --- | --- |
| `PILOT_REPORT.md` | 节点过程摘要、SDK 暴露的摘要、usage/耗时、issues |
| `pilot_delivery.json` | 研究/复盘回执与汇总结果，可用于助手检索 |
| `pilot_artifacts.zip` | 业务输出和审计记录；不打包冻结来源副本、数据库、凭证与 MCP 控制文件 |
| `workspaces/<owner-run>/attempts/<attempt>/audit/pilot_issues.md` | 过程记录与结束复盘合并的问题文件 |
| 同目录 `pilot_review.json` / `research_process.jsonl` / `review_process.jsonl` | 结构化问题与可观测循环事件 |
| `pilot_result.json` | 编排返回的实际 COMPLETE/PARTIAL/NOOP/DEGRADED 等结果与驱动状态 |

`completed` 表示驱动完成，应同时读取业务 result.status；不等于经济研究正确或生产可用。有 audit failure 时驱动状态单独标注。导出按配置凭证值脱敏；正式研究证据与业务输出只发送到用户指定的 SDK/工具环境。

## 4. 正式/新版资产修复

| 路径/逻辑 | 当前行为 |
| --- | --- |
| 默认 V2 Runner | 从 `prompts/codex_v2/document3/v2.0_legacy/` 装载 AGENTS、o3、foundation、旧节点 skills 与旧 schemas |
| 显式 v2.1 service | 默认绑定根目录新版资产；仍支持明确 node_assets 覆盖 |
| role | 根目录 AGENTS 改为实际新版工作区执行指引，移除错误的旧 Stage-A 路径/阅读合同 |
| common | `agents/o3.md`，已包含 foundation；新版不寻找或重复注入 foundation |
| 节点 | discovery/planning/integration 对应各 initialize skill；build 显式映射 `initialize_policy_build.md`；maintain 对应 `maintain.md` |
| 根目录 schemas | 新版未读取，旧静态 `policy_set.schema.json`、`policy_patch.schema.json` 已删除；legacy schemas 保留 |
| 新版 task/schema | 宿主实时生成 Lead/Agenda/Result/Review/Policy/Consolidation/Patch、PolicySet、技术回执 schemas；task 明确提供 schemas 字典与 ticker/as_of，所有节点上下文均附带 schema |
| Integration 研究导航 | 新增只读 main Agenda/Topic Results 与可用 supplement Agenda/Results 的映射，防止 skill 只能看到 Policy 却无法判断原课题及零结果的实际处置；不替代本批完整 Policy |

没有改写用户新版 `agents/o3.md` 和五个 internal skills 的业务内容，也没有修改 legacy 资产内容。新根目录 AGENTS 是宿主执行合同修正。正式缺省版本和活动消费者不切到 v2.1；新路径仍 staged。

## 5. 验证

新增离线 SDK Pilot 验收覆盖：完整四节点逐次复盘/导出、复盘中断只恢复复盘、坏复盘与文件篡改恢复、真实资产路径、只读隔离数据库、原生 SDK typed stream 的公开摘要/隐藏内容排除/权限恢复/已结束 turn 重放，以及 start→continue→report 默认资产链。

新增测试：`tests/test_document3_sdk_pilot.py`，8项组合通过，随后新增 D2 Published 原字节推导 Global 来源导入验收1项通过；合计9项不同测试。D3 新旧编排/维护、候选初始化、既有 SDK Pilot 与 context index 组合 **84 passed**；通用 SDK 定向 **52 passed / 14 deselected**。合计 **145项不同离线测试通过**，均只有既有 opentelemetry warning。Integration 映射补齐后的完整 Pilot、补研中断与发布恢复另行定向复验；不重复累计这些重叠测试。相关文件 Ruff 与 diff check 通过。

主要命令：

```powershell
uv run pytest tests/test_document3_sdk_pilot.py -q
uv run pytest tests/test_codex_document3_v21_orchestration.py tests/test_codex_document3_v21_maintenance.py tests/test_codex_document3_workflow.py tests/test_ticker_initialization_d3_candidates.py tests/test_pilot_sdk_runner.py tests/test_context_index.py -q
uv run pytest tests/test_codex_runtime_v2.py -k 'sdk_runtime or sdk_gpt6' -q
```

参考 SDK 已安装接口签名及 [OpenAI 官方流事件说明](https://developers.openai.com/api/docs/guides/streaming-responses)；公开摘要通过 SDK turn.summary 请求。研究与工具均使用离线替身验收，不能把这些结果写成真实研究/生产实测。
