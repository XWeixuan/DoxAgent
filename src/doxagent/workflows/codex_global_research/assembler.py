"""Deterministic Global Research document assembly."""

from doxagent.codex_runtime.schema import CodexD1Node
from doxagent.workflows.codex_document1.schema import NodeOutput


def assemble_global_research(ticker: str, outputs: dict[CodexD1Node, NodeOutput]) -> str:
    sections = (
        ("C1 基本面研究", CodexD1Node.C1),
        ("C3 行业与价值链研究", CodexD1Node.C3),
        ("C5 市场隐含预期研究", CodexD1Node.C5),
    )
    blocks = [f"# {ticker.upper()} Global Research"]
    for title, node in sections:
        markdown = outputs[node].report_markdown.strip()
        blocks.extend([f"## {title}", markdown])
    return "\n\n".join(blocks).rstrip() + "\n"
