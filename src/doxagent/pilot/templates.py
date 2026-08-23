"""Stable Pilot config and one-shot Codex App task templates."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def render_config(
    *,
    python: Path,
    case_root: Path,
    run_id: str,
    attempt_id: str,
    case_id: str,
    capability: str,
    public_key: str,
    enabled_data_tools: list[str],
    ibkr: dict[str, str],
    runtime_env_file: Path,
) -> str:
    control_root = case_root / ".control" / run_id / attempt_id
    lines = [
        'model = "gpt-5.6-sol"',
        'model_reasoning_effort = "high"',
        'approval_policy = "never"',
        'sandbox_mode = "workspace-write"',
        "",
        "[features]",
        "multi_agent = true",
        "",
        "[mcp_servers.data]",
        f"command = {_toml(str(python))}",
        f"args = {_toml_array(['-m', 'doxagent.mcp.data_server'])}",
        f"cwd = {_toml(str(case_root))}",
        f"enabled_tools = {_toml_array(enabled_data_tools)}",
        "required = true",
        "startup_timeout_sec = 20",
        "tool_timeout_sec = 120",
        "",
        "[mcp_servers.data.env]",
        f"DOXAGENT_DATA_MCP_CAPABILITY = {_toml(capability)}",
        f"DOXAGENT_DATA_MCP_PUBLIC_KEY = {_toml(public_key)}",
        f"DOXAGENT_OBSERVATION_CONTROL_ROOT = {_toml(str(control_root))}",
        f"DOXAGENT_PILOT_ENV_FILE = {_toml(str(runtime_env_file))}",
        f"IBKR_TWS_ENABLED = {_toml(ibkr['enabled'])}",
        f"IBKR_TWS_HOST = {_toml(ibkr['host'])}",
        f"IBKR_TWS_PORT = {_toml(ibkr['port'])}",
        f"IBKR_TWS_CLIENT_ID = {_toml(ibkr['client_id'])}",
        f"IBKR_TWS_TIMEOUT_SECONDS = {_toml(ibkr['timeout_seconds'])}",
        f"IBKR_TWS_MARKET_DATA_TYPE = {_toml(ibkr['market_data_type'])}",
        "",
        "[mcp_servers.source_capture]",
        f"command = {_toml(str(python))}",
        f"args = {_toml_array(['-m', 'doxagent.mcp.source_capture_server'])}",
        f"cwd = {_toml(str(case_root))}",
        f"enabled_tools = {_toml_array(['capture_source'])}",
        "required = false",
        "startup_timeout_sec = 10",
        "tool_timeout_sec = 30",
        "",
        "[mcp_servers.source_capture.env]",
        f"DOXAGENT_CODEX_RUN_ID = {_toml(run_id)}",
        f"DOXAGENT_CODEX_ATTEMPT_ID = {_toml(attempt_id)}",
        f"DOXAGENT_PILOT_CASE_ID = {_toml(case_id)}",
        f"DOXAGENT_OBSERVATION_CONTROL_ROOT = {_toml(str(control_root))}",
        f"DOXAGENT_PILOT_ENV_FILE = {_toml(str(runtime_env_file))}",
        "",
    ]
    return "\n".join(lines)


def render_task(
    *,
    case_root: Path,
    node: str,
    run_id: str,
    attempt_id: str,
    profile: str = "functional",
    as_of_est: date | None = None,
    manual_upstream_paths: tuple[str, ...] = (),
    research_lane: str = "legacy_document1",
) -> str:
    current_est_date = as_of_est or datetime.now(ZoneInfo("America/New_York")).date()
    structured_c4 = node.startswith("c4_")
    if manual_upstream_paths:
        rendered_paths = "\n".join(f"- `{path}`" for path in manual_upstream_paths)
        manual_upstream_contract = f"""## 人工上游输入

本 case 已封存以下人工上游文件：

{rendered_paths}

开始研究前必须读取这些文件。它们覆盖 context 中同名的 source-run 上游结论，但只作为
研究上下文和线索；其中原 attempt 的 O# 已失效，不得作为当前 attempt 的引用。任何进入
正式产物的事实或结论，都必须用当前 attempt 可访问的证据重新核验并引用。
"""
    else:
        manual_upstream_contract = ""
    project_root_guard = f"""## 项目根硬检查

开始任何预检或写入前，先确认当前 Codex 项目根和工作目录**恰好是**：
`{case_root}`

父目录（例如 `{case_root.parent}`）不合格，因为 Codex 不会加载本 case 的
`.codex/config.toml`。如果当前根目录不完全一致，立即停止，不写问题日志，直接要求用户
以该 case 目录重新打开一个可信项目并创建新任务。不要把这种启动错误诊断成 capability
或 Data MCP 故障。
"""
    if profile == "quality":
        quality_focus = {
            "c1": "完整执行 attempt-local C1 fundamental-research skill",
            "c3": "完整执行 attempt-local C3 industry-research skill",
            "c2": "完整执行独立 Market Situation C2 宏观研究合同",
            "c5": "完整执行 attempt-local C5 market-implied-expectations skill",
            "o4": "完整执行独立 Market Situation O4 价格研究合同",
            "c4_pre_scan": "完整执行 C4 前置实体地图与未来节点扫描合同",
            "c4_enrichment": "完整执行 C4 研究后未来节点补充合同",
        }.get(node, f"完整执行 attempt-local {node} skill")
        objective = (
            f"本次正式产物质量是唯一主目标。{quality_focus}，"
            "不得沿用 functional smoke 的简短输出标准。\n\n"
            "Pilot tester 记录是次要目标：只记录直接阻塞证据闭环或显著损害报告质量的 "
            "blocker/major；不要为了审计而额外编写哈希、schema 或引用校验脚本，"
            "不要让过程排查挤占正式研究。"
        )
    else:
        objective = """本次任务有两个同等重要、但输出必须隔离的目标：

1. 完成该节点真实研究任务，按生产文件合同生成正式节点报告。
2. 作为 Pilot tester，持续发现并记录妨碍 workflow 成功、证据闭环或报告质量的问题。"""
    if structured_c4:
        execution_contract = """严格按 attempt-local 指令、task.json、required skill
和 schema 工作：

- 使用 Data MCP 获取受治理数据；只引用当前 attempt 真实存在的 `【cite:O#】`。
- 不得编造 alias；EMPTY/FAILED/UNAVAILABLE 表示未知，不是零。
- 区分直接事实、转述事实、C4 推断和 Unknowns；Pilot 元分析不得进入正式产物。
- 只完成 context.json 指定的当前 C4 阶段，不越权代做其他 C4 Turn。
- 实体关系与未来节点必须严格使用治理规定的五个公开字段；不得增加内部 ID、枚举、
  可靠性、重要性、影响、Gap 或 priced-in 字段。
- C4 没有 progressive Markdown 合同；不要创建或寻找 report_draft.md、progress.json
  或 observation_candidates.json。
- 将完整 NodeOutput JSON 写入 task.json 的 `structured_output_path`；该文件是唯一正式
  C4 输出落点。最终回复中的 structured completion 必须与文件内容一致并通过 output schema。

完成前检查 structured output、全部 citation、五字段边界和阶段边界，并确认未误改只读输入。"""
    else:
        execution_contract = """严格按 attempt-local 指令、task.json、required skills
和 schema 工作：

- 使用 Data MCP 获取受治理数据；只引用当前 attempt 真实存在的 `【cite:O#】`。
- 不得编造 alias；EMPTY/FAILED/UNAVAILABLE 表示未知，不是零。
- 区分事实、解释和不确定性；Pilot 元分析不得进入研究报告。
- 按 required sections 顺序工作；每完成一节立即更新 report_draft.md 和 progress.json。
- observation_candidates.json 与最终结构化输出保持一致。
- 最终 report_markdown 与 report_draft.md 保持一致。

完成前检查 required sections、progress、draft、candidates、全部 citation，并确认未误改只读输入。"""
    lane_title = {
        "global_research": "Global Research / Document 1",
        "market_situation_research": "Market Situation Research",
        "legacy_document1": "Legacy Document 1 v2",
    }.get(research_lane, research_lane)
    return f"""你现在执行一次 {lane_title} 单节点 Pilot Test。

当前工作目录：`{case_root}`
测试节点：`{node}`
Research Lane：`{research_lane}`
Run ID：`{run_id}`
Node Attempt ID：`{attempt_id}`（这是 capability、MCP 与 O# 命名空间的 canonical ID；
持久化对象中的 legacy `attempt_id` 必须与其逐字相等）
当前日期（EST）：`{current_est_date.isoformat()}`

报告或正式产物的任何内容与结论都必须在该时间点仍具参考价值，不要给出过时结论。

{objective}

{manual_upstream_contract}

{project_root_guard}

Pilot Agent 的直接写入只能落到：
`attempts/{attempt_id}/audit/pilot_issues.md`

这是 attempt-local `AGENTS.md` 中“只写 task.json 指定输出”的唯一额外例外。

## 不可修改与 MCP 管理路径

不得由 Agent 直接修改、删除或覆盖 `.codex/`、`.control/`、`context/`、`artifacts/`、
`attempts/{attempt_id}/input/`、任何 prompt/skill/schema/bundle manifest 或上游产物。
不要在 Pilot workspace 中修复发现的问题；只记录问题和建议修复方向。

Data MCP 与 Source Capture MCP 被明确授权仅写以下运行时管理投影；这些写入不属于
Agent 文件写入，也不得由 Agent 手工创建或编辑：

- `.control/{run_id}/{attempt_id}/observations.sqlite3*`
- `context/data_tool_catalog/{attempt_id}.md`
- `context/mcp_data/{attempt_id}/`
- `attempts/{attempt_id}/audit/observations/`

Agent 自己只能写：

- `task.json` 指定的 output 文件
- `attempts/{attempt_id}/audit/pilot_issues.md`

## 开始前检查

先读取 `attempts/{attempt_id}/input/AGENTS.md`、`task.md`、`task.json`、
`context.json`、所有 required skills、可选 `horizontal.json` 和 output schema。
检查 Data MCP、Source Capture MCP、当前节点 semantic tools 是否可见，并确认非白名单工具被隐藏。
若 MCP、工具或权限异常，立即在问题日志记录 blocker；不要修改 config 或 capability。

开始正式研究前，在问题日志写入标题、节点、run ID、attempt ID 和开始时间。
每个问题用以下结构即时追加：

### P-001
- 发生步骤：
- 分类：Data MCP | Schema | Prompt | Skill | Context | Citation | Workspace | Agent Loop
- 严重度：blocker | major | minor | observation
- 现象：
- 对 workflow 或报告质量的影响：
- 相关文件、工具或 Observation alias：
- 初步原因：
- 建议修复方向：

未知工具选择、参数歧义、返回过大/过小/重复、必要指标或时间窗口缺失、O# 难以引用、
context 冲突或缺失、prompt/skill 冲突、schema 表达困难、progressive output 同步困难、
无效搜索/重复调用/等待/绕路以及 subagent 分工或合并问题，都应如实记录；不要制造问题。

## 正式研究合同

{execution_contract}
最后在问题日志追加：

## Final summary
- 本轮是否完成正式节点任务：
- 正式产物是否通过自检：
- 阻碍 workflow 的问题：
- 影响报告质量的问题：
- Data MCP 问题：
- Prompt/Skill/Schema 问题：
- 可暂缓的易用性问题：
- 建议修复顺序：
- 是否建议进入 SDK 真实循环测试：yes | no
- 判断理由：

最终回复只需简要说明节点任务是否完成、正式输出路径、问题日志路径、
blocker/major/minor 数量及是否建议进入第二阶段。现在开始，不要先修改配置或生产资产。
"""


def render_document2_task(
    *,
    case_root: Path,
    node: str,
    run_id: str,
    attempt_id: str,
) -> str:
    return f"""# Document2 v2 Formal Pilot Task

## 项目根硬检查

开始任何读取或写入前，确认当前 Codex 项目根和工作目录**恰好是**：
`{case_root}`

如果不完全一致，立即停止并要求用户以该 case 目录重新打开可信项目。

## 正式目标

完整重跑 Document2 节点 `{node}`；这不是 smoke test。按顺序完整读取：

1. `attempts/{attempt_id}/input/AGENTS.md`
2. `attempts/{attempt_id}/input/agent.md`
3. `attempts/{attempt_id}/input/skill.md`
4. `attempts/{attempt_id}/input/task.json`
5. `attempts/{attempt_id}/input/context.json`
6. `attempts/{attempt_id}/input/output_schema.json`

严格遵循 attempt-local prompt/skill 与三份 Document2 v2 方案所形成的节点合同。允许 Agent
按当前节点合同使用已签名 Data MCP；引用失败或未解析只能作为 warning，不得阻止正式产物。

将唯一完整 JSON 结果写入：
`attempts/{attempt_id}/output/completion.json`

该 JSON 必须匹配 `output_schema.json`。最终回复必须与文件内容一致。Pilot 过程中发现的问题
仅追加记录到：
`attempts/{attempt_id}/audit/pilot_issues.md`

不得修改 input、context、上游 artifact 或其他 attempt。当前 workspace run id 为 `{run_id}`。
"""


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml(value) for value in values) + "]"
