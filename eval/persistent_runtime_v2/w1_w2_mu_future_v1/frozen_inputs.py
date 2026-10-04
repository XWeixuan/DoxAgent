"""Resolve hash-preserved local copies without rewriting historical provenance."""

import json
from copy import deepcopy
from pathlib import Path


def resolved_manifest(root: Path, manifest: dict) -> dict:
    mapping = json.loads((root / "frozen_input_mapping.json").read_text(encoding="utf-8"))
    by_purpose = {item["purpose"]: item for item in mapping["inputs"]}
    result = deepcopy(manifest)
    for item in result["frozen_inputs"]:
        local = by_purpose[item["purpose"]]
        if local["sha256"] != item["sha256"]:
            raise ValueError(f"Frozen input mapping disagrees: {item['purpose']}")
        item["original_path"] = item["path"]
        item["path"] = str((root / local["local_path"]).resolve())
    return result
