"""Read-only local check that retired modules do not re-enter current assembly."""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {
    "agents",
    "adapters",
    "annotations",
    "audit",
    "blackboard",
    "context",
    "workflow_memory",
    "examples",
    "persistent_runtime",
    "revenue_audit",
    "gateway",
    "prompts",
    "skills",
    "stocktwits",
    "dashboard_api",
}


def main() -> None:
    errors = []
    for name in sorted(RETIRED):
        if (ROOT / "src/doxagent" / name).exists():
            errors.append(f"retired source still present: {name}")
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in imports:
                parts = module.split(".")
                if len(parts) > 1 and parts[0] == "doxagent" and parts[1] in RETIRED:
                    errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: {module}")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for module in project["project"]["scripts"].values():
        path = ROOT / "src" / (module.split(":", 1)[0].replace(".", "/") + ".py")
        if not path.is_file():
            errors.append(f"missing console entrypoint: {module}")
    for path in [
        "src/cdecr/prompts/v1",
        "src/cdecr/catalogs/v1",
        "src/cdecr/catalogs/v2",
        "prompts/codex_v2/document1/compatibility/legacy_document1",
        "prompts/persistent_runtime_v2",
        "dev_plan/workflow_v2/api_contract",
        "dev_plan/workflow_v2/DOXAGENT_V2_API_CONTRACT.md",
        "dev_plan/workflow_v2.1/document3_v2.1_contracts.schema.json",
        "eval/persistent_runtime_w3/corpus_v1.json",
        "eval/trade_execution/20260928_audit/ledger_redacted.json",
        "frontend/v2/package.json",
        "Dockerfile.v2",
        "docker-compose.v2-production.yml",
    ]:
        if not (ROOT / path).exists():
            errors.append(f"required current resource missing: {path}")
    if errors:
        raise SystemExit("\n".join(errors))
    print("PASS: current imports, console entrypoints, and protected V2 resources")


if __name__ == "__main__":
    main()
