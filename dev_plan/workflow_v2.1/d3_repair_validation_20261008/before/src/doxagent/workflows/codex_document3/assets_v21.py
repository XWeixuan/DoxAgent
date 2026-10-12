"""Explicit V21 asset routing; foundation is part of the O3 agent."""

from pathlib import Path


def default_node_assets(root: Path | None = None) -> dict[str, str]:
    root = root or Path(__file__).resolve().parents[4] / "prompts/codex_v2/document3"
    return {
        "role": str(root / "AGENTS.md"),
        "common": str(root / "agents/o3.md"),
        **{
            node: str(root / f"skills/initialize_{skill}.md")
            for node, skill in {
                "discovery": "discovery",
                "planning": "planning",
                "build": "policy_build",
                "integration": "integration",
            }.items()
        },
        "maintain": str(root / "skills/maintain.md"),
    }
