"""Deterministic final Document 1 assembly."""

from __future__ import annotations

from doxagent.codex_runtime.schema import CodexD1Node
from doxagent.workflows.codex_document1.schema import NodeOutput

_ORDER = (
    CodexD1Node.C1,
    CodexD1Node.C2,
    CodexD1Node.C3,
    CodexD1Node.C4_FINALIZATION,
    CodexD1Node.O4_B,
    CodexD1Node.O4_A,
)

_TITLE = {
    CodexD1Node.C1: "C1 公司基本面研究",
    CodexD1Node.C2: "C2 宏观环境研究",
    CodexD1Node.C3: "C3 行业与产业链研究",
    CodexD1Node.C4_FINALIZATION: "C4 实体关系与未来节点",
    CodexD1Node.O4_B: "O4-B 市场事实与历史定价",
    CodexD1Node.O4_A: "O4-A 定价逻辑与隐含情景",
}


def assemble_document1(ticker: str, outputs: dict[CodexD1Node, NodeOutput]) -> str:
    sections = [f"# {ticker.upper()} Document 1 v2", ""]
    for node in _ORDER:
        output = outputs.get(node)
        if output is None:
            sections.extend([f"## {_TITLE[node]}", "该节点未产出结果。", ""])
            continue
        sections.extend([f"## {_TITLE[node]}", output.report_markdown.strip(), ""])
        if output.warnings:
            sections.extend(["### 已知缺口", *[f"- {warning}" for warning in output.warnings], ""])
    return "\n".join(sections).strip() + "\n"
