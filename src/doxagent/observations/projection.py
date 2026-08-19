"""Compact Agent-facing projections for private attempt observations."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from doxagent.observations.models import PersistedObservation


def observation_source(observation: PersistedObservation) -> dict[str, str]:
    """Return the bounded source identity useful to an Agent."""

    source = {"provider": observation.provider}
    locator = observation.source_locator or observation.locator
    if locator:
        source["locator"] = locator
    return source


def observation_projection(
    observation: PersistedObservation,
    *,
    content: Any | None = None,
) -> dict[str, Any]:
    """Project a canonical record without runtime-only identifiers or hashes."""

    return {
        "alias": observation.alias,
        "title": observation.title,
        "content": deepcopy(observation.content if content is None else content),
        "source": observation_source(observation),
    }


def render_observation_projection(observation: PersistedObservation) -> str:
    """Render the canonical compact JSON projection written to the workspace."""

    return json.dumps(
        observation_projection(observation),
        ensure_ascii=False,
        indent=2,
    )


def projection_matches(observation: PersistedObservation, value: str) -> bool:
    """Accept the compact projection and immutable legacy full-record mirrors."""

    if value == render_observation_projection(observation):
        return True
    try:
        legacy = PersistedObservation.model_validate_json(value)
    except ValueError:
        return False
    return legacy == observation


__all__ = [
    "observation_projection",
    "observation_source",
    "projection_matches",
    "render_observation_projection",
]
