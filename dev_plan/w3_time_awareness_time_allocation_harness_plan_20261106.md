# W3 Time Awareness / Time Allocation：Harness 实施方案

日期：2026-11-06（北京时间）  
状态：源码勘察与开发设计，尚未实施、测试或部署本方案。  
范围：只增强 W3 **Trade Decision** 的编排与 Codex Harness；**Post-Decision Operations 完全不采用该能力，不接收 Elapsed 或动态提醒**。

## 1. 方案结论与必须说明的边界

建议保留当前 Python Codex SDK、app-server、W3 两阶段执行和业务合同，在锁定的 Codex 0.159.3 源码上维护一个小补丁，复用 `CurrentTimeReminder` 的 developer 上下文片段与原生模型请求注入链路。DoxAgent 仅传入可信的 Case 计时元数据；Rust Harness 在每次 W3 研判请求及流式恢复请求前计算 Elapsed、生成对应短提醒。

选择同版本补丁，是因为已核实：现有原生功能能注入绝对时间，但没有 Case 耗时或分档提醒配置；Python 高层 SDK 也没有“每次内部推理前修改上下文”的回调。只在 `thread.turn()` 前拼接一次时间，或者在工具返回中补时间，都不能完整满足需求。

有三项边界不能通过软提醒消除：

1. **正在进行的一次推理无法收到更新。** 若请求在 Elapsed=110s 时开始，模型一直推理到 300s 才返回，期间不会收到 120/180/240s 提醒。下一次请求才更新；若本次直接交付终稿，就没有下一次提醒。方案不为此中断请求、缩短推理预算或增加请求。
2. **“≥240s 停止调查”与现行业务必需核验存在冲突。** 当前 Skill 要求拟 LONG/SHORT 在提交前实际完成适用的行情核验。时间提示不能授权跳过该规则，也不能因时间不足强制 NO_TRADE。本方案将“停止新增调查”落为停止扩展自主研究，保留现行合同必需的提交前核验；强提示仍要求立即收敛。若要求连尚未完成的必需核验也一律停止，就无法同时满足“业务逻辑不变、质量不受损”。
3. **不能事先保证行为干预绝不改变模型个别结论。** 可保证不改交易合同、方向规则、模型路由、输出映射和业务阈值；是否产生过早收束及倾向偏移，需要有对照的质量评估，不能用“耗时下降”代替质量验收。

不新增 Agent、调度器、阶段流程、耗时预测、时间工具、计时轮询或独立模型调用。不修改任何业务 `AGENTS.md`、`skill.md` 或提示词发布包。

## 2. 勘察基线与证据范围

### 2.1 当前工作区

- 勘察提交：`26c7a54a452558e87749591c1ea7c2f14cff58a1`，分支 `codex/agent-readonly-mcp`，无 upstream。
- 工作区已有其他任务的代码与文档改动，`changelog` 也已脏；本轮保留这些内容。
- 依据为当前 checkout、已安装 Python SDK/CLI、对应上游版本源码及仓库已有审计材料。**没有读取生产实时配置、调用生产模型或修改远端状态**，不据此声称生产运行版本已核对。
- 项目是 Python 后端与独立前端，后端由 `uv.lock` 锁依赖、pytest 验证；本方案不涉及前端、数据库结构迁移或交易执行器。

### 2.2 已核实的 SDK / 原生版本

| 项目 | 实际结果 | 对设计的意义 |
| --- | --- | --- |
| 项目依赖 | `openai-codex==0.159.3` | 保持 SDK 与锁文件不变 |
| 安装依赖 | `openai-codex-cli-bin==0.159.3` | SDK 默认启动其 bundled binary，不依赖终端 PATH 中的 Codex |
| 本机 bundled CLI | `codex-cli 0.159.3` | 已直接执行 `--version`，未启动模型 |
| 二进制覆盖入口 | `CodexConfig.codex_bin` | 可指定同版本补丁二进制，无需修改 site-packages |
| 上游标签 | `rust-v0.159.3` | annotated tag 对象为 `8e46774a94a745ffdf676bd7a8aa36466bbd4f99` |
| 上游实际 commit | `01fc69f4026735edfdf6789820549727a4867b11` | 补丁、构建与源码引用均固定到此 commit |

本机 SDK 的核查位置：`.venv/Lib/site-packages/openai_codex/client.py`、`api.py`、`async_client.py`、`generated/v2_all.py`。这些是只读证据，不是拟编辑文件。

### 2.3 与耗时问题有关的既有证据

[`prompt_revision_findings.md`](../artifacts/audits/w3_top10_20261104/prompt_revision_findings.md) 记录了剔除已识别技术失败后的十例决策阶段长尾，模型阶段约 322—523s，且不包含上游、排队和后置执行。它支持研究本次收敛机制，但属于长尾定向样本，不能代表总体发生率。

现有 [`w3_turn_probe.py`](../src/doxagent/codex_worker/w3_turn_probe.py) 已能观察模型请求、工具区间、终稿尾段与输入增长；[`w3_timing.py`](../src/doxagent/w3_timing.py) 已有编排边界计时。复用这些观测，不重建性能平台，不把工具外的全部时间标成“无效推理”。

## 3. 当前架构、业务逻辑与任务机制

### 3.1 实际调用链

```text
消息进入持久化 Case
  → W1 事实新旧 / W2 Policy 判断与路由
  → ROUTE_TO_W3 effect，由现有租约和工作线程承接
  → W3Execution.trade(case)
      → durable trade_decision stage
      → 冻结输入 / 研究与历史 / 后台行情 / Template、Ready Slot、Fork 或 Resume
      → CodexW3AgentRunner.run_phase()
      → ReceiptWorker.run()：冻结请求、幂等派发、回执与恢复
      → Codex Worker Job / Capsule
      → OpenAICodexRuntime._start()
      → AsyncCodex → 本地 codex app-server → thread.turn()
          → 原生 Agent Loop：模型请求 ↔ Web / MCP 等工具
      → 解析并持久化 Trade Decision
      → 独立提交交易意图，再排入 W3_POST_DECISION effect
  → W3Execution.operations(case)，沿用 Case Thread
      → 维护事实、WATCH/PIN 等；不重做交易决定
```

关键源码与职责：

| 源码入口 | 已核实的职责 |
| --- | --- |
| [`service.py`](../src/doxagent/persistent_runtime_v2/service.py) 的 `_execute_effect()` | 将 `ROUTE_TO_W3` 与 `W3_POST_DECISION` 分别送入 `trade()` / `operations()` |
| [`w3_execution.py`](../src/doxagent/persistent_runtime_v2/w3_execution.py) 的 `trade()` / `operations()` | 两阶段状态、输入与输出提交；交易提交不等待 Operations |
| [`w3_threads.py`](../src/doxagent/persistent_runtime_v2/w3_threads.py) | Template、Case Thread 归属、关联复用、stage 与不可变已保存结果 |
| [`w3.py`](../src/doxagent/persistent_runtime_v2/w3.py) 的 `_run_phase_materialized()` | 组装当前阶段输入、Schema、模型 profile、workspace 与 WorkerRunRequest |
| [`worker_receipts.py`](../src/doxagent/persistent_runtime_v2/worker_receipts.py) | 请求冻结、同 job 接回、确认终态失败后的 generation 与恢复 |
| [`schema.py`](../src/doxagent/codex_worker/schema.py) 的 `WorkerRunRequest` | 内部执行协议，已显式区分三个 W3 task_phase |
| [`sdk_runtime.py`](../src/doxagent/codex_worker/sdk_runtime.py) | 官方 Python SDK 适配、稳定配置 scope、prepared/resident Thread 复用与 `thread.turn()` |
| [`capsules.py`](../src/doxagent/codex_worker/capsules.py)、[`capsule_child.py`](../src/doxagent/codex_worker/capsule_child.py) | 常驻 app-server 所在进程、预热、交接与运行所有权 |

**Case、Thread、SDK Turn、模型请求不能混同。** Thread 可通过关联路由承接不同 Case；一个 Case 有两个阶段；一个 SDK Turn 内可发生多次模型推理。计时按 Case 的 Trade Decision 归属，提醒可见性按当前 Trade Decision Turn 归属，不能使用 Thread 年龄。

### 3.2 本方案必须保持的业务行为

- 当前 W3 v5 Trade Decision 输出 `LONG / SHORT / NO_TRADE`，Operations 输出 novelty、delta candidates、watch actions、pin actions。兼容 v4 的现有解析与映射保持原样。
- W2 的 HIT/RELATED 归因只读；W3 方向独立判断。当前新冻结 profile 为 HIT/RELATED → `gpt-6.1-sol/medium`，普通 OPEN → `gpt-6-luna/max`；既有冻结 profile 不改写。
- D1/D2/D3、Policy、历史事件、72h 交易意图与 WATCH/PIN 是研判上下文；必要调查、Surprise、ticker 传导与消息时效继续由现有业务合同定义。
- 行情只能用于现行规则许可的已完成重定价否决核验，不能变成方向、仓位或目标价依据；拟交易的必需核验不因提醒失效。
- Host 的输出接纳、Policy 使用约束、幂等、交易意图提交和后续执行逻辑不变。Elapsed 不进入业务输出 Schema，也不成为 Host 的 NO_TRADE 或失败条件。

### 3.3 已存在的硬超时

`persistent_runtime_v2_w3_timeout_seconds` 默认 600s，实际由部署配置决定。`sdk_runtime.py` 在 SDK 派发时设置 `sdk_deadline`；`turn_deadline.py::run_with_deadline()` 到期会尝试 interrupt，父层也有启动、接回与清理边界。

本次 120/180/240s **不得接入这些控制分支**，不改已有 timeout/deadline、quota、租约、恢复预算或清理策略。“不设置硬时限”在本方案中指本功能不新增硬时限，不能描述成当前系统完全没有硬超时。

## 4. 原生 CurrentTimeReminder 的能力与缺口

锁定源码中的实际路径已经拆分，不是仓库根部一个独立的 `time_reminder.rs`：

- [`core/src/session/time_reminder.rs`](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core/src/session/time_reminder.rs)：`maybe_record_current_time_reminder()`、间隔和上下文窗口判断。
- [`core/src/context/current_time_reminder.rs`](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core/src/context/current_time_reminder.rs)：developer 角色、`current_time.reminder` 分类与格式化片段。
- [`core/src/session/turn.rs`](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core/src/session/turn.rs)：外层 Loop 在克隆历史前调用提醒；`run_sampling_request()` 内另有流式恢复循环。
- [`core/src/config/mod.rs`](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core/src/config/mod.rs)、[`features/src/feature_configs.rs`](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/features/src/feature_configs.rs)：默认间隔 1s、`any_inference`，可配置为间隔 0；没有 elapsed 起点或模板配置。

原生提醒通过 `ContextualUserFragment` 和 `record_conversation_items()` 进入历史。因此，**仅在 Operations 时关闭提醒生产还不够**：先前 Trade Decision 的 developer 提醒仍可能通过同一 Thread 历史进入 Operations，或者被压缩摘要带过去。阶段隔离必须同时覆盖历史渲染和 compaction。

不采用以下替代路线：

| 路线 | 不选的原因 |
| --- | --- |
| 开启原生当前时间提醒，让模型自己减起点 | 增加模型计时负担，没有动态文案，也不能保证按 Case 计时 |
| Python 只修改 `request.prompt` | 只覆盖 SDK Turn 首次输入，不覆盖内部后续请求 |
| 定时 `turn/steer`、周期消息或额外时间工具 | 引入调度、上下文与时序成本，仍不能在长推理中自然更新 |
| 工具 Hook 注入 | 纯推理续轮及没有工具的长尾无法覆盖 |
| 增加自定义 RPC 字段并分叉 Python SDK | 此版本高层调用没有合适入口，扩大协议和通知订阅维护面；本需求不需要 |
| 每次覆盖一条只存在于当前请求的时间消息 | 虽然容易隔离，但会破坏前次请求的追加前缀；原生 WebSocket 增量复用要求输入是前次请求的扩展 |

## 5. 推荐实现：Case 起点 + 每 Turn 快照 + 原生逐请求注入

### 5.1 开关与内部协议

新增两个部署设置，开发与初次构建默认关闭：

- `DOXAGENT_W3_TIME_AWARENESS_ENABLED=false`：唯一行为开关。
- `DOXAGENT_CODEX_RUNTIME_BIN`：可选的、经过校验的同版本补丁二进制绝对路径，映射至 `CodexConfig.codex_bin`。

`WorkerRunRequest` 增加一个可空内部字段，拟名 `w3_time_awareness`：

```json
{
  "case_id": "case_...",
  "started_at_utc": "2026-11-06T02:00:00.000000+00:00"
}
```

仅当开关开启、`node == persistent_runtime_w3` 且 `task_phase == trade_decision` 时构造。Template、Operations、其他节点及旧冻结请求均为空。该字段不写入业务 task/prompt、研究资产或输出 Schema。

### 5.2 起点与真实耗时

计时语义固定如下，避免实现者自行选用不同时间：

1. 起点是该 Case **首次实际执行 `W3Execution.trade()` 且尚需模型研判时**。在准备上下文、行情、Thread 和 Worker 派发之前记录真实 UTC。已有已提交/已保存结果直接接回时不启动计时。
2. 包含起点之后的输入准备、Fork/Resume、内部排队、模型推理、工具调用和等待。消息抓取、W1/W2、进入 W3 之前的排队，以及公共 Template 提前预热时间不计入。
3. 用现有 RuntimeJournal 的小型 KV 记录 `namespace=w3_time_awareness`、`key=case_id`，只存版本与真实起点。首次写入受现有 Case 所有权/租约保护；不加表、索引、后台任务或独立状态机。现有 Case 清理路径一并删除该记录。
4. 同 Case 的实际重试、进程恢复和跨 SDK Turn 继续沿用起点，包含中间等待；不同 Case 即使复用同一 Thread 也各自起算。已终态失败的 Case 不因本功能被重派。
5. 不使用消息发布时间、`cutoff_at`、Thread 创建时间或 `journal.clock()` 作为实际耗时起点。后者允许测试/回放注入语义时间，不能拿历史交易时间计算现实运行秒数。
6. Rust 接收快照时计算一次 `elapsed_at_snapshot = max(0, system_utc_now - started_at_utc)`，随后使用 `elapsed_at_snapshot + Instant::elapsed()`；每次展示向下取整为秒。活跃 Turn 内不重复用墙钟相减，避免校时造成倒退。
7. 跨进程恢复的初始换算依赖宿主 UTC 正常；这是普通运行计时的边界，不为此增加分布式时钟系统。回放同样用真实运行时间，业务证据截止时间保持冻结。

`trade()` 之外的现有 decision-only 回测入口直接调用 `run_phase()`，应在它的首次 Trade Decision 派发点调用同一“取得或创建起点”辅助函数。不要因此把起点统一推迟到模型派发时，漏算生产的准备耗时。

### 5.3 复用 SDK 的轻量元数据通路

推荐用 **每个现有 Capsule 一份很小的控制文件**，不扩展 app-server RPC，也不修改 SDK：

- `OpenAICodexRuntime` 构造时，在现有 Capsule 控制目录下确定一个稳定路径，通过其 `CodexConfig.env` 向该 app-server 传入 `DOXAGENT_W3_TIME_CONTEXT_FILE`。
- 目录使用现有 `DOXAGENT_MCP_BUDGET_ROOT` 的父目录；非 Capsule 的本地测试运行使用该 runtime 私有临时目录。路径属于宿主控制面，不是模型工作区可改的业务输入。
- 每次 `thread.turn()` 之前，宿主原子替换该文件。Trade Decision 写入 `enabled/case_id/thread_id/task_phase/started_at_utc`；其他所有任务明确写入 `enabled=false`。
- 文件只含这些机器元数据，不能传入任意提醒文本。提醒内容是补丁中固定、可审查的常量。
- Rust 在 Turn 开始时读取一次，确认 `thread_id` 匹配且阶段为 `trade_decision`，将计时快照存入现有 `TurnContext.extension_data`。后续模型循环只读内存与单调时钟，无每轮文件/数据库访问。
- Turn 完成或异常退出后清除活动控制记录；下一次 Turn 仍必须先覆盖它，不能依赖清理必然成功。Capsule 原有单 Turn 所有权负责串行化，不新增锁或协调器。

这份控制文件是适配当前 SDK 能力缺口的一次性传递载体，不是新增调度状态。不要将 Case 起点塞入 `sdk_config` 或现有 prepared/resident scope 指纹：当前 `_start()` 以配置构成复用条件，变化的起点/Case ID 会破坏预热接回或导致重复 Resume。

### 5.4 原生注入位置与历史隔离

在原生 `session/time_reminder.rs` 扩展 W3 专用计时辅助逻辑，在 `context/current_time_reminder.rs` 内新增短小的 W3 reminder fragment；普通 `CurrentTimeReminder` 行为保持原样。

具体执行契约：

1. W3 fragment 仍为 developer 角色，使用独立标记 `<w3_time_awareness>` 和 native `ContentItemKind` 分类；分类带当前原生 Turn ID，例如 `w3.time_awareness.<turn_id>`。片段必须 `requires_separate_message=true`，避免与业务指令合并后难以隔离。
2. 在 `run_sampling_request()` 的**每一次请求构造/流式恢复循环**中，在 `try_run_sampling_request()` 前重新计算并记录片段，再从已记录的规范历史生成该次 Prompt，保持 item ID 与顺序一致。首次已准备的 input 分支和从历史重建的 retry 分支都要覆盖，不能因首轮 input 已构造而漏掉提醒，也不能重复拼接；不得仅改外层 `run_turn` 的原生提醒调用。
3. 当前 Trade Decision Turn 内按原生机制追加历史，保留前轮片段和新片段的顺序，只新增短尾部，避免每轮修改已有请求前缀。最新片段是当前 Elapsed；不压缩、合并或覆盖前轮片段。
4. 为 `ContextManager` 的模型输入渲染增加一个小的 W3 scope 过滤能力：默认渲染排除所有 `w3.time_awareness.*`；Trade Decision 的 sampling 显式允许**当前 Turn ID**的片段。仅按 Harness 内容分类处理，不对业务文本或普通当前时间提醒做模糊删除。
5. 首次 input、retry 重建、Fork/Resume 后输入均遵循同一过滤规则。Operations、Template、其他 Case 和其他节点走默认渲染，即使底层历史包含旧片段，模型输入中也没有这些提醒。
6. 本地 compaction 的 `for_prompt()` 与 remote compaction 的 `for_prompt_annotated()` 均走默认过滤，不能把 W3 提醒发给摘要模型。压缩结束后，计时快照仍在 TurnContext，下一次研判采样生成最新提醒。压缩请求不附加收敛行为指令，它是上下文维护请求，不是 W3 决策采样。
7. 持久化 rollout 可保留片段作为审计证据，但不能把它当业务记忆重放。禁用功能时，同样应用默认过滤，保证切到 Operations 或后续 Case 不继承停止调查指令。

需要检查 `compact_remote_v2_attempt.rs` 与 `compact_remote_history.rs` 的替换历史路径，确保恢复摘要不会绕过过滤。无需改变摘要业务文本、追加摘要提示词或重新初始化 Thread。

以上复用原生上下文分类、历史与渲染，新增的只有一个 Case 起点、一份 Capsule 控制记录和一个 Turn 内计时快照。没有逐工具计时账本或按调查步骤的时间规划。

### 5.5 提醒分档与固定文案

区间使用半开区间；阈值按真实 elapsed 判断，展示为整数秒。同一档每次请求仍刷新秒数。

| Elapsed | 注入正文；下列文案存于 Harness 常量，不写业务提示词文件 |
| --- | --- |
| `0 ≤ t < 120s` | `Elapsed: {n}s` |
| `120 ≤ t < 180s` | `Elapsed: {n}s。任务耗时较长；评估现有研判和必要核查是否已充分，若充分，请尽快提交最终结果。` |
| `180 ≤ t < 240s` | `Elapsed: {n}s。任务耗时已明显偏长；除非仍有可能实质改变结论的高价值事实或变量待研究，请在后续几轮内尽早提交最终结果。` |
| `t ≥ 240s` | `Elapsed: {n}s。停止新增调查；基于现有研判，完成现行合同要求的必要提交核验后，立即提交最终结果。` |

≥240s 文案中的必要提交核验仅指现有合同的明确义务，不包括补引用、扩背景、追求完整性或再确认一次。该解释是第 1 节冲突的设计取舍，不新增研究阶段或保守交易门槛。

所有档位均不触发工具禁用、强制输出、固定剩余轮数、token/effort 降档、取消、job 失败、自动 NO_TRADE 或模拟成交。时间信息与市场时间、新闻年龄、交易有效期保持独立；不能把“本任务用了四分钟”解释成“新闻已失效”。

### 5.6 W3 隔离与异常处理

- 宿主以节点及阶段双重限定，原生以可信控制记录、Thread ID 和 Turn scope 限定；不依赖模型识别“现在是哪个阶段”。
- 不改变 Web/MCP 工具清单，不为本功能启用 `clock`/sleep 工具，也不要求模型主动获取时间。普通绝对时间机制若原本启用，沿用现状；Operations 排除的是本次新增能力。
- 未配置补丁二进制、控制记录缺失或不可读时，不阻塞业务、不合成交易结果；记录一次简短的 Harness 诊断，按原流程运行。功能验收必须识别这种降级，不能将“任务正常结束”记作注入成功。
- 发布开启前必须核对补丁 manifest/二进制摘要与注入证据；不能仅因 Python 开关为 true 就宣称生效。
- 旧 `ReceiptWorker` 冻结请求不回填或改 hash；初次上线只让新建、满足开关条件的请求携带元数据。不重放历史 Case，不改已有结果及失败状态。

## 6. 拟修改文件与逐项开发任务

以下均为后续开发范围，本轮未修改。

### 6.1 DoxAgent 仓库

| 文件 | 必要变更 |
| --- | --- |
| `src/doxagent/settings.py`、`.env.v2.example` | 两个设置及默认关闭；不新增可随意调整的阶段阈值面板 |
| `src/doxagent/persistent_runtime_v2/w3_execution.py` | 需要模型执行的 `trade()` 起始处取得/创建真实起点，接回已完成结果不计时 |
| `src/doxagent/persistent_runtime_v2/w3.py` | 只给 Trade Decision 组装内部计时元数据；其他入口复用同一辅助函数 |
| `src/doxagent/persistent_runtime_v2/w3_time_awareness.py`（新增小模块） | 起点读写与元数据构造；不维护每轮计时状态；接入现有 Case 清理 |
| `src/doxagent/codex_worker/schema.py` | 可空的内部元数据类型，保留旧请求兼容 |
| `src/doxagent/codex_worker/sdk_runtime.py` | 二进制路径选择、稳定控制文件环境、Turn 前原子写入与结束清理；不改变 scope 指纹或订阅方式 |
| `src/doxagent/codex_worker/thread_lifecycle.py` | 复用相同二进制选择设置，保证辅助 app-server 与执行 runtime 的版本一致 |
| `src/doxagent/w3_backtest/decision_runner.py` | decision-only 入口的真实起点与显式开关，业务 cutoff 不变 |
| `Dockerfile.v2`、`docker-compose.v2-production.yml` | 打包固定补丁产物、配置可选路径；不新增常驻组件 |
| `deploy/codex-patches/` 与一个构建脚本（新增） | 保存小补丁、上游 revision、toolchain、补丁/二进制 SHA256、构建命令与产物 manifest；不复制整个上游仓库进业务仓库 |
| 对应 pytest、验收报告及 `changelog` | 覆盖实际行为与重要变更留痕，不批量重写现有测试 |

二进制选择应尊重调用者已显式传入的 `CodexConfig.codex_bin`；未配置时仍使用 SDK bundled binary。不修改 SDK 安装目录，不新增双池调度。Worker 可统一使用该同版本补丁二进制，但非目标任务在缺少有效计时上下文时保持原行为，必须有负向验证。

### 6.2 锁定版本的 Codex 小补丁

| 上游文件 | 变更 |
| --- | --- |
| `codex-rs/core/src/session/time_reminder.rs` | W3 元数据快照、单调 elapsed、分档、逐请求生成与原生记录辅助函数 |
| `codex-rs/core/src/context/current_time_reminder.rs`、`context/mod.rs` | W3 专用片段与导出，保持普通时间片段兼容 |
| `codex-rs/core/src/session/turn.rs` | Turn 快照初始化、sampling 两条输入分支及 retry 注入 |
| `codex-rs/core/src/context_manager/history.rs` | 默认排除及当前 Turn 显式允许的 scoped 渲染；保留 Harness 分类元数据 |
| `codex-rs/core/tests/suite/current_time_reminder.rs` 及必要的 history 测试 | 按真实模型请求 payload 验证，而非只测字符串格式化 |

compaction 正常路径复用默认渲染过滤；若恢复/替换历史路径有旁路，只在对应文件补同一过滤调用，不重构 compaction。无需改 app-server RPC schema、Python SDK 生成文件、全局 feature 配置结构或交易输出协议。

### 6.3 实施顺序与完成物

1. 固定当前源码基线，建立 feature flag、内部协议与真实起点；完成 Case/阶段计时测试。
2. 在固定上游 commit 实现原生小补丁，先完成 mocked Responses 请求级验证：首轮、工具续轮、无工具续轮、流式恢复、压缩及 Operations 隔离。
3. 接入 SDK 控制通路与二进制选择，验证 Template/Ready Slot/Fork/Resume/resident 路径，不改 warm profile 或并发。
4. 构建固定平台二进制，在隔离本地 Worker 运行有真实 SDK 的测试；补齐缓存、性能与业务质量对照。
5. 形成代码、补丁 manifest、测试报告、质量对照和发布/回退说明；在 `changelog` 追加重要变更摘要。
6. 上述完成后再进入独立发布步骤。本方案交付不代表已获得或执行生产发布。

构建命令在固定上游源码的 `codex-rs` 目录运行，使用其指定 toolchain 与 lockfile，例如 `cargo build --locked --release -p codex-cli --bin codex`。产物必须在目标 Linux 架构构建并校验动态依赖；本机 Windows 源码勘察不等于 Linux 构建已验证。

## 7. 验证方法与验收标准

### 7.1 确定性机制验证：无需真实模型

用可注入计时源和 mocked Responses 服务验证实际发出的 Prompt，避免靠等待数分钟测阈值：

| 场景 | 必须成立 |
| --- | --- |
| `0/73/119/120/179/180/239/240/301s` | 秒数与档位准确；120s 前无行为提示；240s 不取消 |
| 首次、工具返回、纯推理续轮、多工具并发返回后续轮 | 每个新的决策采样请求都有更新后的提醒，不要求额外工具 |
| 流式错误后恢复、重连后新采样 | 每次新建采样请求重新算 Elapsed，起点不重置、不重复同一次的片段 |
| 工具/等待耗时 80s | 下一请求包括该 80s，不把并发工具耗时累加成多份 |
| 一次推理跨过三个阈值 | 请求不中断；有下一请求时才看到最新档位 |
| 两个 Case 并行；同 Thread 后续 Case | 起点互不污染；不使用 Thread 年龄 |
| 同 Case job 接回、新 generation、进程恢复 | 既有结果直接接回；确需重新执行时沿用原始起点 |
| 同 Thread 的 Trade Decision → Operations | Operations 的首个及所有后续请求没有 W3 时间片段；原有业务输入/Schema 与模型 profile 保留 |
| Template、其他研究节点、开关关闭、旧冻结请求 | 没有本能力的片段、额外时间工具或元数据副作用 |
| 本地/remote compaction 与压缩后续轮 | 摘要请求排除提醒；后续研判 Elapsed 连续；Operations 不继承提醒摘要 |
| 无控制文件、错误 Thread、记录不可读 | 跳过本功能并可观察到降级；不形成业务失败或新派发 |
| 墙钟在活跃 Turn 中向前/后调整 | 活跃 Turn 的 Elapsed 仍单调；不用业务历史时间 |

“每次请求”指新的 W3 决策采样构造，包括 Harness 流式恢复循环；传输层对同一已构造请求的透明重发沿用原 payload，不为刷新秒数修改网络重试语义。需要实测确认低层不会把等待后重新采样误当透明重发。自动压缩等维护请求不加入收敛指令，也不增加模型调用。

### 7.2 既有编排与业务回归

优先复用 `test_w3_v5_execution.py`、`test_w3_v5_contracts.py`、`test_w3_ready_v5.py`、`test_w3_thread_lifecycle.py`、`test_w3_writer_handoff.py`、`test_w3_boundary_timing.py`、`test_w3_turn_probe.py` 与 `test_codex_runtime_v2.py` 中相关测试。

重点证明：

- Trade Decision 仍先提交，Operations 不阻塞、不撤销或补发交易。
- LONG/SHORT 的必需核验与现有输出校验仍有效，不出现按 Elapsed 生成 NO_TRADE 的 Host 代码。
- 准备好的 Thread 被接回，不因动态计时数据改变配置指纹而多做 Fork/Resume。
- 回执、原 job/Future 所有权、幂等 key、已保存结果不可变及既有硬超时仍工作。
- `AGENTS.md`、`skill.md`、合同 JSON、模型路由、业务 Schema 与交易执行源码的摘要未因本功能改变。

原生测试以 `cargo test -p codex-core --test all current_time_reminder` 为起点，该 tag 存在 `core/tests/all.rs`；按新增过滤器补必要的 history/compaction 用例。Python 只运行本次涉及的集合与静态检查；本轮仅交付方案，不运行开发验收测试。

### 7.3 成本与缓存验证

复用现有 SDK token usage、TurnProbe 与编排计时，对开关前后同类请求比较：

- 模型请求总数没有因时间功能增加；没有新时间工具调用或后台 timer。
- 每个 Turn 至多读取一次控制文件，每个 Case 至多新增一份小起点记录；每次请求只做单调时间读取、短格式化和原生上下文追加。
- 同一 Trade Decision 内前次请求仍为后次请求的前缀，WebSocket `previous_response_id` 的增量条件仍成立；补丁没有每轮覆写历史或修改 base instructions。
- 分别报告输入 token 增长与 cached input，不将“只增加几十个字”直接当作成本已通过。切换阶段、重试恢复或 compaction 本身引起的前缀变化单独列出。
- 对提醒条数、token 总增量和格式化/控制 I/O 开销给出实测值；若显著破坏缓存或增加首请求延迟，先修复，不以业务耗时可能下降抵消机制缺陷。

### 7.4 真实行为与投研质量验证

在隔离、只记录结果的回测通路做开关对照，使用同一业务输入、研究版本、可得证据、冻结行情、profile 和输出合同。保持业务 prompt 文件不变；外部搜索可变化的 live 结果与冻结证据评估分开报告。

样本应覆盖 HIT、RELATED、OPEN、LONG、SHORT、NO_TRADE、时效不明、关键反证、需要补行情和原长尾；不能只拿十例最长 NO_TRADE 样本推断整体质量。原生机制测试和模型效果测试分开，mock 的“按提醒立刻结束”不能证明真实模型效果。

记录 Trade Decision 全流程与模型阶段各自的 p50/p90/p95、超过 180/240s 的比例、请求/搜索/研究读取次数、最后工具到终稿耗时和 token 成本。另列单次长推理导致提醒未及时送达的比例，解释本方案可触达的长尾范围。

质量评审使用去除开关标记的配对结果，检查经济分量、预期修正、ticker 传导、消息时效、关键反证和必需核验；特别检查强提示后是否跳过决定性事实、把未知机械转成不交易、LONG/SHORT 被系统性压低，或改变 WATCH/PIN 后续维护。Operations 本身输入应排除本能力，但前一阶段研究结果变化仍可能间接影响其业务内容，不能承诺全文完全一致。

通过条件：机制隔离、合同与必需核验全部通过；真实对照未发现可归因于提醒的实质质量退化，且耗时/重复调查改善有实际证据。少量样本只能给初步结果，不宣称统计意义上的“质量绝不下降”。单次长推理未改善可如实保留，不升级为硬中断补救。

## 8. 发布与回退设计

本节是后续实施步骤，本轮不执行。

1. 保存上游 commit、补丁、目标平台构建环境、二进制 SHA256、依赖锁摘要、提示词摘要、测试与质量报告；不要用浮动最新 Codex 替代锁定版本。
2. 先以关闭状态安装补丁二进制，验证真实 SDK 的 initialize、Template/Fork/Resume/Turn 事件与输出兼容；缺少模型调用的 readiness 不能当作完整行为验收。
3. 仅对新受理且符合条件的 Trade Decision 开启；继续保留自然失败、超时、回执和版本证据。模型任务仍由原系统受理，不通过历史重放制造验收样本。
4. 验证真实目标请求有 elapsed/档位证据，同 Thread Operations 的请求没有 W3 提醒；同时核对 prompt/Schema、profile、runtime 及镜像版本。
5. 回退先关闭注入开关，让新 Turn 不再取得计时上下文；当前已运行 Turn 不被取消，也不会瞬间移除已送达上下文。需要恢复原二进制时，沿用现有服务发布和在途任务交接流程，不清库、不重置 Case、不清理订单。

最终交付应区分“代码完成”“原生机制已验证”“投研质量对照已验证”“生产已发布/自然流量已验证”。只有第一项不能宣称本需求已达成。

## 9. 参考与本轮交付边界

官方接口背景参照 [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) 与 [Codex App Server](https://learn.chatgpt.com/docs/app-server)；具体可用参数以本机安装版与上述固定 commit 源码为准，不把在线新文档的接口推定为旧 SDK 已支持。

本轮新增的仅为本方案 MD。没有实现补丁、修改代码或业务提示词，没有改锁文件、配置、`changelog`、运行任务或生产环境。后续重要代码修改必须按项目要求追加 `changelog`，并保留当前其他任务的未提交改动。
