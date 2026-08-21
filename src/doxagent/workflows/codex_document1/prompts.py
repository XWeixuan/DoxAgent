"""Minimal file-first turn prompts for the seeded Codex D1 bundle."""

from __future__ import annotations

from doxagent.codex_runtime.schema import CodexD1Node


class CodexD1PromptLoader:
    def __init__(self, _root: object | None = None) -> None:
        # Assets are materialized by AttemptBundleSeeder; prompts only point to files.
        pass

    def render(
        self,
        *,
        node: CodexD1Node,
        attempt_id: str,
        task_path: str,
    ) -> str:
        return (
            f"Current node: {node.value}\n"
            f"Attempt id: {attempt_id}. Read attempts/{attempt_id}/input/AGENTS.md and "
            f"{task_path}. Then read every file named by task.json before doing research. "
            "Do not continue until those file reads succeed. Follow the output paths and return "
            "one JSON object matching the supplied schema."
        )
