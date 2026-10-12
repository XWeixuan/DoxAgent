"""Versioned prompt loading for Persistent Runtime V2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeV2PromptSet:
    core: str
    w1_r1: str
    w1_r2: str
    w1_r3: str
    w2_r1: str
    w2_r2: str

    @classmethod
    def load(cls, root: Path | None = None) -> RuntimeV2PromptSet:
        prompt_root = root or Path("prompts/persistent_runtime_v2")

        def read(name: str) -> str:
            value = (prompt_root / name).read_text(encoding="utf-8").strip()
            if not value:
                raise ValueError(f"Persistent Runtime V2 prompt is empty: {name}")
            return value

        return cls(
            core=read("core.md"),
            w1_r1=read("w1_r1.md"),
            w1_r2=read("w1_r2.md"),
            w1_r3=read("w1_r3.md"),
            w2_r1=read("w2_r1.md"),
            w2_r2=read("w2_r2.md"),
        )

    def instructions(self, round_prompt: str) -> str:
        return f"{self.core}\n\n{round_prompt}"

