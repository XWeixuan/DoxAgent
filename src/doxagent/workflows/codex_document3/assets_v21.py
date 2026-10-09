"""Explicit V21 asset routing; foundation is part of the O3 agent."""

from pathlib import Path

ATLAS_NAMES = {
    "open_atlas_general": "Open_Event_Atlas_General.md",
    "open_atlas_l1_01": "Open_Event_Atlas_L1-01.md",
}
ATLAS_ROOT = "context/document3/v21/assets/open_event_atlas"


def default_node_assets(root: Path | None = None, atlas_root: Path | None = None) -> dict[str, str]:
    root = root or Path(__file__).resolve().parents[4] / "prompts/codex_v2/document3"
    atlas_root = atlas_root or root.parent / "open_event_atlas"
    return {
        **{key: str(atlas_root / filename) for key, filename in ATLAS_NAMES.items()},
        "role": str(root / "AGENTS.md"),
        "common": str(root / "agents/o3.md"),
        "discovery_open": str(root / "skills/initialize_discovery_open.md"),
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
