"""Stable Pilot config and one-shot Codex App task templates."""

from __future__ import annotations

import json
from pathlib import Path


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


def render_task(*, case_root: Path, node: str, run_id: str, attempt_id: str) -> str:
    return f"""你现在执行一次 Document 1 v2 单节点 Pilot Test。

当前工作目录：`{case_root}`
测试节点：`{node}`
Run ID：`{run_id}`
Attempt ID：`{attempt_id}`

本次任务有两个同等重要、但输出必须隔离的目标：

1. 完成该节点真实研究任务，按生产文件合同生成正式节点报告。
2. 作为 Pilot tester，持续发现并记录妨碍 workflow 成功、证据闭环或报告质量的问题。

Pilot 分析只能写入：
`attempts/{attempt_id}/audit/pilot_issues.md`

这是 attempt-local `AGENTS.md` 中“只写 task.json 指定输出”的唯一额外例外。

## 不可修改

不得修改、删除或覆盖 `.codex/`、`.control/`、`context/`、`artifacts/`、
`attempts/{attempt_id}/input/`、任何 prompt/skill/schema/bundle manifest 或上游产物。
不要在 Pilot workspace 中修复发现的问题；只记录问题和建议修复方向。

你只能写：

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

严格按 attempt-local 指令、task.json、required skills 和 schema 工作：

- 使用 Data MCP 获取受治理数据；只引用当前 attempt 真实存在的 `【cite:O#】`。
- 不得编造 alias；EMPTY/FAILED/UNAVAILABLE 表示未知，不是零。
- 区分事实、解释和不确定性；Pilot 元分析不得进入研究报告。
- 按 required sections 顺序工作；每完成一节立即更新 report_draft.md 和 progress.json。
- observation_candidates.json 与最终结构化输出保持一致。
- 最终 report_markdown 与 report_draft.md 保持一致。

完成前检查 required sections、progress、draft、candidates、全部 citation，并确认未误改只读输入。
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


def _toml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml(value) for value in values) + "]"
