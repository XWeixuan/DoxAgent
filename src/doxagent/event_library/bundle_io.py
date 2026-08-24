"""Event-per-file Canonical Revision Bundle loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from doxagent.event_library.contracts import (
    CanonicalRevisionBundle,
    CanonicalRevisionBundleManifest,
)


class RevisionBundleIO:
    @staticmethod
    def load(path: str | Path) -> CanonicalRevisionBundle:
        root = Path(path)
        manifest_contract = CanonicalRevisionBundleManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest = manifest_contract.model_dump(mode="json", exclude={"event_revisions"})
        event_paths = manifest_contract.event_revisions
        events: list[dict[str, Any]] = []
        for relative in event_paths:
            candidate = (root / str(relative)).resolve()
            if root.resolve() not in candidate.parents:
                raise ValueError("Bundle event path escapes the Bundle root")
            events.append(json.loads(candidate.read_text(encoding="utf-8")))
        retirements_path = root / "retirements.json"
        residual_path = root / "residual_delta_resolutions.jsonl"
        retirements = (
            json.loads(retirements_path.read_text(encoding="utf-8"))
            if retirements_path.exists()
            else []
        )
        residuals = []
        if residual_path.exists():
            residuals = [
                json.loads(line)
                for line in residual_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return CanonicalRevisionBundle.model_validate(
            {
                **manifest,
                "event_revisions": events,
                "event_retirements": retirements,
                "residual_delta_resolutions": residuals,
            }
        )
