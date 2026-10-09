"""Business ownership, not arbitrary line/file-count limits."""

from __future__ import annotations

import ast
from pathlib import Path

from pydantic import Field

from .schema import Model, RepairReport
from .workspace import Workspaces, safe_file


class SourcePolicy(Model):
    site_id: str | None = None
    # Path -> site-owned top-level function/class names. No wildcard shared core access.
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    tests: list[str] = Field(default_factory=list)
    normal_fixture_test: str | None = None
    failure_fixture_test: str | None = None
    services: dict[str, list[str]] = Field(default_factory=dict)
    canaries: list[dict] = Field(default_factory=list)
    parameter_fields: list[str] = Field(default_factory=list)


def _partition(text: str, allowed: set[str]) -> tuple[str, dict[str, str]]:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    owned = {}
    for node in reversed(tree.body):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name not in allowed:
                continue
            first = min([node.lineno] + [x.lineno for x in node.decorator_list]) - 1
            end = node.end_lineno or node.lineno
            owned[node.name] = "".join(lines[first:end])
            lines[first:end] = ["\n"]
    # AST comparison outside owned symbols ignores harmless whitespace, not imports/constants.
    return ast.dump(ast.parse("".join(lines)), include_attributes=False), owned


def validate_candidate(path: Path, report: RepairReport, policies: list[SourcePolicy]) -> list[str]:
    changed = [x for x in Workspaces.changed(path) if not x.startswith(".maintenance/")]
    if set(changed) != set(report.changed_files):
        raise ValueError("report and actual candidate diff disagree")
    owners: dict[str, set[str]] = {}
    for policy in policies:
        for relative, names in policy.symbols.items():
            owners.setdefault(relative, set()).update(names)
    allowed_tests = {test for policy in policies for test in policy.tests}
    for relative in changed:
        candidate = safe_file(path, relative)
        if not candidate.is_file():
            raise ValueError("file deletion unsupported in automatic source patch")
        if relative in allowed_tests:
            continue
        if relative not in owners:
            raise ValueError("shared core or non-source file requires review: " + relative)
        original = Workspaces.original(path, relative)
        before, _ = _partition(original, owners[relative])
        after, _ = _partition(candidate.read_text(encoding="utf-8"), owners[relative])
        if before != after:
            raise ValueError("diff escapes registered site-owned symbols: " + relative)
    for policy in policies:
        if (
            not policy.normal_fixture_test
            or not policy.failure_fixture_test
            or policy.normal_fixture_test.split("::")[0] not in allowed_tests
            or policy.failure_fixture_test.split("::")[0] not in allowed_tests
        ):
            raise ValueError("trusted normal and failure fixture test targets required")
    return changed
