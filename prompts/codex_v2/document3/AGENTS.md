# D3 / O3 Workspace Contract

使用当前注入的 `context/document3/**` 作为 DoxAgent V2 Document3 的完整运行合同。`codex_document3_v1` 是这套 V2 D3 workflow 的第一版 contract 名，不表示旧 V1 Document3 语义。

## 1. 读取顺序与权限层级

先读取 `task.json`，确认生命周期 mode、ticker、run、上游引用、failed shells 和 warnings；再读取：

1. `agent.md`：O3 的长期角色、业务目标和责任边界；
2. `foundation.md`：所有 O3 Turns 共用的业务语义；
3. 当前节点 prompt 指定的 stage skill：本 Turn 的执行方法；
4. 当前节点的 `*.output_schema.json`：最终回复的精确合同；
5. 当前模式的业务输入、已有工作文件和适用的 Policy schema。

当前节点 prompt 决定本 Turn 的 stage。Trigger Calibration、Policy Compile 和 Final Global Pass 共同构成一次 INITIALIZE 生命周期，并复用同一份 `task.json`，其中 mode 保持 `O3_INITIALIZE`。Stage skill 决定执行流程，supplied schemas 决定精确字段，业务文件提供实际研究内容。使用两份 Trigger Calibration schema 校验 Stage-A 工件，使用 `policy_set.schema.json` 校验 Policy draft 或完整 Policy；`O3_MAINTAIN` 同时使用 `policy_patch.schema.json`。

## 2. 模式与 Workspace

| 当前节点 | 主要业务输入 | 业务工作文件 | 最终回复 |
| --- | --- | --- | --- |
| `O3_TRIGGER_CALIBRATION` | `document2.json`、可选 `reference_event_view.md`、可为 `null` 的 `previous_policy_set.json` | `worklist.jsonl`、`trigger_calibrations.jsonl`、`trigger_calibration_state.json` | `TriggerCalibrationRunResult` |
| `O3_POLICY_COMPILE` | D2、Previous Policy Set、完整 Stage-A 工件和 Policy schema | `calibration_log.jsonl`、`policies/*.json`、最终 Worklist status 与 `wave_state.json` | `O3RunResult` |
| `O3_FINAL_REVIEW` | Initialize 的全部冻结输入、Trigger 工件、编译工作文件及 provisional `coverage_map.json` | 直接修复并同步 Trigger、worklist、calibration、wave、Policy drafts 和 coverage | `ReviewResult` |
| `O3_MAINTAIN` | `current_policy_set.json`、最新 `reference_event_view.md` | `maintenance_candidates.jsonl`、`policy_patch.json` | `O3RunResult` |

Trigger Calibration、Policy Compile 与 Final Global Pass 在同一 thread 和 workspace 中连续执行，并使用同一次 Input Preparation 冻结的业务输入。已有工作文件是本次 run 和 retry 之间的持久状态；恢复时先理解已经完成的工作，再从未闭合处继续。Maintenance 是独立的变化驱动运行，以 Current Published Policy Set 为业务基线。

## 3. 输入与研究边界

Published D2 是 Initialize 的 expectation research prior；publication state 为 `PARTIAL` 时仍是有效输入，`task.json` 中的 failed shells 表示已知研究边界。Previous Policy Set 提供语义和身份连续性，Current Policy Set 是 Maintenance 的当前业务状态。

Reference View 是版本化的现实与事件上下文，不是完整世界状态；其中没有记录某项变化，不能反向证明该变化尚未发生。Trigger Calibration 围绕明确的 actor、current state 和 Candidate Trigger 问题使用 Web Search 或只读 Data MCP；Policy Compile 消费冻结的 Stage-A Trigger surface，不承担常规开放研究。所有研究遵守本 Turn supplied cutoff，业务修改只落在当前 workspace 的既有字段中。

## 4. 写入与完成合同

`context/document3/**` 是冻结输入。Agent 的业务写入范围是 `output/work/**`；确定性流程负责 `output/final/**`、Published artifacts、稳定 Policy/Condition ID、Policy Set version、Coverage assembly、Runtime Projection 和发布。

JSON、JSONL、Policy 和 Patch 使用 supplied schemas 及现有过程字段。完整业务产物写入 workspace，最终回复只返回当前 `*.output_schema.json` 对应的单个小型 JSON 对象，不附加报告、Policy Set 或解释文本。
