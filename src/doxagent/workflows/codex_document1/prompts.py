"""Prompt bundle loader with explicit node-to-agent routing."""

from __future__ import annotations

from pathlib import Path

from doxagent.codex_runtime.schema import CodexD1Node

_FILE_BY_NODE = {
    CodexD1Node.C4_PRE_SCAN: "c4.md",
    CodexD1Node.C1: "c1.md",
    CodexD1Node.C2: "c2.md",
    CodexD1Node.C3: "c3.md",
    CodexD1Node.O4_B: "o4_b.md",
    CodexD1Node.C4_ENRICHMENT: "c4.md",
    CodexD1Node.C4_FINALIZATION: "c4.md",
    CodexD1Node.O4_A: "o4_a.md",
}


class CodexD1PromptLoader:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._agents = (self._root / "AGENTS.md").read_text(encoding="utf-8")

    def render(
        self,
        *,
        node: CodexD1Node,
        attempt_id: str,
        context_path: str,
    ) -> str:
        instructions = (self._root / "agents" / _FILE_BY_NODE[node]).read_text(encoding="utf-8")
        return (
            f"{self._agents}\n\n{instructions}\n\n"
            f"Current node: {node.value}\n"
            f"Read the immutable input at {context_path}.\n"
            "The input file has already been created inside the current cwd and is readable. "
            "Use a file-reading tool to read it before answering; do not report it missing or "
            "unreadable unless a real tool call returns an error.\n"
            f"Attempt id: {attempt_id}.\n"
            "Return one JSON object matching the supplied output schema. Also include the complete "
            "Markdown report in report_markdown; do not modify files outside this attempt/run."
        )
