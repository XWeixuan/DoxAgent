# Document2 Open Discovery 单节点修复：实现与验收

日期：2026-10-04（Asia/Shanghai）。依据：[单节点勘察与修复方案](d2_open_discovery_single_node_repair_plan_20261004.md)。

本轮在既有 v2.1 上完成 R1–R8 的局部修复。每个 Shell 的正常 O1 路径为 **Open Discovery → State → Realization → Gaps → Finalization**；Open Discovery 只派发一个 Worker request、一个 attempt 和一个 SDK `thread.turn`，同一次 Turn 内通过 D2 工具冻结 Scan，再完成 Selection。工具往返可以包含多次底层模型推理请求；此处的“一次调用”指一次业务节点/Worker/SDK Turn，不承诺一次底层 HTTP 推理请求。

## 1. 实施项目与落点

| 项目 | 已实现内容 | 核验要点 |
| --- | --- | --- |
| R1 身份与模型 | `codex_runtime/schema.py` 注册 `O1_OPEN_DISCOVERY`；D2 schema 增加 completion/checkpoint/aggregate，stage 使用 `OPEN_DISCOVERY`；Data policy 仅给新节点原 Discovery 权限 | 历史 Scan/Selection enum 和 typed 模型可解析，但新 Runner/Pilot 不允许按旧节点派发 |
| R2 中间提交 | 新 `workflows/codex_document2/discovery_checkpoint.py`，stdio 只暴露 `commit_open_discovery_scan`；绑定 task/context、run、Shell、cutoff、seed SHA 和生产 attempt | 先保存权威 checkpoint，再保存确定性 Scan；返回时二者已落盘；同字节幂等、替换拒绝、跨进程互斥 |
| R3 SDK 接入 | `codex_worker/sdk_runtime.py` 只对新 Discovery 节点配置 required stdio 工具；该节点关闭 multi-agent，维持一次 `thread.turn` | State 等其他节点无新增工具配置；不修改 Worker HTTP 协议、通用调度或全局默认 |
| R4 Runner 聚合 | `runner.py` 区分最终 wire completion 与 durable aggregate；读冻结记录、校验 SHA 和 Selection 覆盖/MERGE；保存完整成功工件及 receipt | `_D2Codec` 无需修改即可恢复 checkpoint + Selection；失败也保留 Scan 来源；Discovery 不使用业务 fallback |
| R5 五阶段及恢复 | `orchestrator.py` 合并两 Pass 排程、维护五份 stage_outputs；重试只注入冻结 Scan/SHA/producer；父 sidecar 恢复同步；Pinned v2.1 ID 增加 single-v1 区分 | RUNNING attempt 复用原冻结 Worker request/幂等键；Worker 已成功或 raw aggregate 已成功时不重新执行；failed_stage 统一 `OPEN_DISCOVERY` |
| R6 Pilot | builder/coordinator/templates 使用一个 Discovery case、completion schema、同一 stdio 工具；推进前核验 checkpoint 并聚合 upstream | 含 Narrative 14 项，optional 缺失实际 13 项，末五项为五个 O1；source-attempt 导出保留原输入和首个 Scan producer 审计 |
| R7 定向验收 | 修订 v2.1 测试并新增 `tests/test_d2_discovery_checkpoint.py`，包含真实 stdio、跨进程及完整 Pilot 流程 | 覆盖下节场景，旧 v2/Pilot/初始化恢复/SDK 纳入组合回归 |
| R8 留痕 | 本验收文档、方案状态更新、changelog 追加 | 上一轮六节点/15 项说明仅作为历史实现记录，新方案为当前依据 |

## 2. 冻结、恢复与引用的具体行为

权威记录位于 Shell 子 workspace 的 `context/document2/open_discovery_checkpoint.json`；冻结 Scan 位于 `context/document2/open_discovery_scan.json`。记录包含 single-v1 合同、逻辑 run、Shell、cutoff、seed SHA、producer attempt、Scan SHA 和完整 Scan。Scan 使用确定性 JSON；工具只限定 `ref` 字段的本地别名，不改候选名称。普通 workspace 和物理 Pilot case 根目录分别校验。

记录保存后 Scan 导出中断，读取时可从权威记录重建缺失 Scan；已有 Scan 内容冲突、身份/SHA 错误按 SYSTEM 失败，不重扫掩盖。合法最终 JSON 缺 checkpoint 按 FORMAT 有界重试；SHA 不符按 SYSTEM；候选覆盖、MERGE 自指/环等沿用结构校验；空 Scan + 空 Selection 合法。

Selection 失败后，新 attempt 读取同一 Scan/SHA/producer，并明确 `resume_from=SELECTION`。协调进程取消时保留正在运行的 attempt 和原 Worker request，恢复重新连接其幂等身份，而非启动第二个 live Worker。SDK 成功但 Runner 尚未聚合时复用成功响应；已保存 raw aggregate 而父指针或 canonical/sidecar 效果中断时重放成功工件。首次 Scan 与重试 Selection 的同名 O# 分别使用各自产生 attempt 的观察清单；首次 Selection 失败不会丢弃 Scan 引用。未解析来源继续作为 warning，不增加研究语义门禁。

Pilot coordinator 不接受“只有 completion 没有 checkpoint”的 Discovery case。后四阶段通过一个 upstream aggregate 获得完整 Scan/Selection。导出源 attempt 时不重查 provider、不重写原 context/task/schema；保留旧 Scan producer attempt 及审计，工具协议明确已有冻结记录优先于早于该记录的原输入。旧 split-v1 run/coordinator/Pinned 身份不与 single-v1 混用；既有历史文件不迁移、不覆写。

## 3. 验收证据

关键新增离线测试直接核验：

- 无重试正常请求序列精确为五个 O1，新 Discovery 一次，旧两个节点零次；aggregate 经原 `_D2Codec` 往返仍保留完整 checkpoint/Selection。
- fake SDK 一次 `thread.turn`；真实 stdio MCP 只列出一个工具，并在最终 Selection 生成前完成 Scan 落盘。Worker 暂停在 Scan 后的 live 恢复测试证明恢复复用同一执行。
- 双进程抢占冻结只允许首个 Scan；同字节幂等；记录/Scan 间中断可重建；身份/SHA/已有 Scan 损坏拒绝。
- Selection 首次失败仅重试 Selection；Scan 首次来源与重试 Selection 的 O# 不串来源；缺 checkpoint 不伪造候选。
- Worker live、Worker 成功但 Runner 中断、raw aggregate 保存后效果中断三种路径恢复均不增加实际 Discovery 执行。
- 完整 builder/coordinator Pilot（Narrative 有/无）实际完成 14/13 项，Discovery 一项，O1 五项；source 导出原输入原字节不变。
- v2.1 多 Shell 并行、失败隔离、late/resolution、发布与版本绑定，以及旧 v2、旧 Pilot、初始化 substep/receipt 恢复和 SDK 原配置持续回归。

最终组合回归：**168 passed，3 warnings，406.51s**。其中新 checkpoint/SDK/Pilot 测试 27 项、修订后的 v2.1 编排验收 35 项、旧 D2/Pilot/初始化 durable/SDK 回归 106 项。三项 warning 来自既有 OpenTelemetry metadata 弃用和 Agent Framework 的 experimental 标记；无失败或跳过。

变更 Python 文件 Ruff **All checks passed**；`git diff --check` 通过，只有 Windows LF/CRLF 提示。测试文件新增项及新模块亦经 Ruff 检查。完整 Pilot 两场景另一次定向执行为 2 passed，随后已包含在最终组合回归中。

组合命令：

```powershell
uv run pytest tests/test_d2_discovery_checkpoint.py tests/test_codex_document2_v21_orchestration.py tests/test_codex_document2_workflow.py tests/test_document2_pilot_coordinator.py tests/test_ticker_initialization_substeps.py tests/test_ticker_initialization_substep_recovery.py tests/test_ticker_initialization_internal_rerun.py tests/test_codex_runtime_v2.py -q
```

## 4. 验收边界

仅修改 D2 编排/schema/冻结恢复/Pilot，另在 Worker SDK 和 Pilot config 中增加新 D2 节点的条件配置。未改 prompt/skill 研究资产、O3/W3/Read/API/前端消费者、共享 codec、Repository/WorkspaceStore、Data MCP 服务或数据库。未处理工作区其他并行 D3/前端改动。

v2 仍为默认；v2.1 仍 staged、非 current。本轮没有真实模型研究、消费者适配、远程部署或生产启用。真实 `open-discovery` 研究 skill 与语义联调按已确认的后续资产轮次处理；缺资产维持 SYSTEM 失败，不冒用旧 skill 或伪造结果。测试通过证明编排/冻结/恢复合同，不代表真实研究质量已验收。
