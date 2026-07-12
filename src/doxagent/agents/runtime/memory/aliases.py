"""Task-local, stable aliases for Observation Blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ALIAS_RE = re.compile(r"^O[1-9]\d*$")


@dataclass
class ObservationAliasRegistry:
    """Map opaque Observation Block ids to stable agent-facing ``O#`` aliases.

    A registry belongs to exactly one AgentTask.  Registering an already-known
    block is idempotent, so aliases remain stable across every ReAct loop.
    """

    _alias_to_block_id: dict[str, str] = field(default_factory=dict)
    _block_id_to_alias: dict[str, str] = field(default_factory=dict)
    _next_alias: int = 1

    def register(self, block_id: str) -> str:
        existing = self._block_id_to_alias.get(block_id)
        if existing is not None:
            return existing
        alias = f"O{self._next_alias}"
        self._next_alias += 1
        self._alias_to_block_id[alias] = block_id
        self._block_id_to_alias[block_id] = alias
        return alias

    def register_many(self, block_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(self.register(block_id) for block_id in block_ids)

    def resolve(self, alias: str) -> str | None:
        if not _ALIAS_RE.fullmatch(alias):
            return None
        return self._alias_to_block_id.get(alias)

    def alias_for(self, block_id: str) -> str | None:
        return self._block_id_to_alias.get(block_id)

    def contains(self, alias: str) -> bool:
        return self.resolve(alias) is not None

    def audit(self) -> dict[str, str]:
        return dict(self._alias_to_block_id)


__all__ = ["ObservationAliasRegistry"]
