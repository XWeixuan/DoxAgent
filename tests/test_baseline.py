import tomllib
from pathlib import Path

import doxagent

ROOT = Path(__file__).resolve().parents[1]


def test_package_import_exposes_version() -> None:
    assert doxagent.__version__ == "0.1.0"


def test_phase0_baseline_files_exist() -> None:
    expected_paths = [
        ROOT / ".env.example",
        ROOT / ".python-version",
        ROOT / "README.md",
        ROOT / "dev_plan" / "PHASE0_BASELINE.md",
        ROOT / "pyproject.toml",
    ]

    for path in expected_paths:
        assert path.exists(), f"Missing Phase 0 baseline file: {path}"


def test_pyproject_keeps_external_agent_projects_out_of_dependencies() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = "\n".join(pyproject["project"]["dependencies"]).lower()

    assert "langchain" not in dependencies
    assert "langgraph" not in dependencies
    assert "agent-framework>=" not in dependencies
    assert "vibe" not in dependencies
    assert "hermes" not in dependencies
    assert "financial-services" not in dependencies
