"""Deterministic Market Situation document assembly."""

from doxagent.codex_runtime.schema import CodexD1Node
from doxagent.workflows.codex_document1.schema import NodeOutput


def assemble_market_situation(ticker: str, outputs: dict[CodexD1Node, NodeOutput]) -> str:
    sections = (
        ("C2 大盘与宏观环境", CodexD1Node.C2),
        ("O4 个股价格面与走势", CodexD1Node.O4),
    )
    blocks = [f"# {ticker.upper()} Market Situation Research"]
    for title, node in sections:
        blocks.extend([f"## {title}", outputs[node].report_markdown.strip()])
    return "\n\n".join(blocks).rstrip() + "\n"
