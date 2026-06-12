"""Prompt resource hygiene checks."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from doxagent.prompts.registry import default_prompt_root, load_prompt_definitions

FORBIDDEN_FRONT_MATTER_KEYS = {"allowed_tools", "output_requirements", "guardrails"}
MOJIBAKE_MARKERS = ("�", "锟", "浣犻", "鏃", "缂", "鐩", "嬫")


@dataclass(frozen=True)
class PromptLintIssue:
    path: Path
    message: str


def lint_prompt_resources(root: Path | None = None) -> list[PromptLintIssue]:
    resolved_root = root or default_prompt_root()
    issues: list[PromptLintIssue] = []
    for path in sorted(resolved_root.rglob("*.md")):
        raw = _read_utf8(path, issues)
        if raw is None:
            continue
        if any(marker in raw for marker in MOJIBAKE_MARKERS):
            issues.append(PromptLintIssue(path, "contains mojibake marker"))
        front_matter = _front_matter(path, raw, issues)
        if front_matter is None:
            continue
        try:
            data = tomllib.loads(front_matter)
        except tomllib.TOMLDecodeError as exc:
            issues.append(PromptLintIssue(path, f"invalid TOML front matter: {exc}"))
            continue
        kind = data.get("kind")
        if kind not in {"prompt_block", "internal_task_skill", "external_skill_package"}:
            issues.append(PromptLintIssue(path, f"invalid prompt resource kind: {kind}"))
        duplicate_keys = sorted(FORBIDDEN_FRONT_MATTER_KEYS.intersection(data))
        if duplicate_keys:
            issues.append(
                PromptLintIssue(
                    path,
                    "runtime constraints belong in agent registry, not prompt front matter: "
                    + ", ".join(duplicate_keys),
                )
            )
        if kind in {"internal_task_skill", "external_skill_package"} and not (
            data.get("applicable_agents") or data.get("applicable_task_types")
        ):
            issues.append(PromptLintIssue(path, "skill resource lacks agent/task binding"))
    try:
        load_prompt_definitions(resolved_root)
    except Exception as exc:
        issues.append(PromptLintIssue(resolved_root, f"prompt registry load failed: {exc}"))
    return issues


def _read_utf8(path: Path, issues: list[PromptLintIssue]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        issues.append(PromptLintIssue(path, f"not valid UTF-8: {exc}"))
        return None


def _front_matter(
    path: Path,
    raw: str,
    issues: list[PromptLintIssue],
) -> str | None:
    if not raw.startswith("+++\n"):
        issues.append(PromptLintIssue(path, "missing TOML front matter"))
        return None
    try:
        _, front_matter, _ = raw.split("+++\n", 2)
    except ValueError:
        issues.append(PromptLintIssue(path, "front matter is not closed"))
        return None
    return front_matter
