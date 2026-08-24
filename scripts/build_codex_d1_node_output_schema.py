"""Regenerate the checked-in Codex D1 structured output schema."""

from __future__ import annotations

import json
from pathlib import Path

from doxagent.workflows.codex_document1.schema import NODE_OUTPUT_SCHEMA


def main() -> None:
    target = Path("prompts/codex_v2/document1/schemas/node_output.schema.json")
    target.write_text(
        json.dumps(NODE_OUTPUT_SCHEMA, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
