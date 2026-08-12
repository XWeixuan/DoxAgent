"""Install the persistent local Pilot harness without printing any secrets."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Install DoxAgent Codex App Pilot runtime")
    parser.add_argument("--target", default=r"D:\DoxAgentPilot")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    target = Path(args.target).resolve()
    runtime = target / "runtime"
    cases = target / "cases"
    runtime.mkdir(parents=True, exist_ok=True)
    cases.mkdir(parents=True, exist_ok=True)
    for name in (
        "prepare_case.py",
        "doctor.py",
        "config.template.toml",
        ".env.local.example",
    ):
        shutil.copy2(repo_root / "pilot_runtime" / name, runtime / name)

    env_path = runtime / ".env.local"
    if not env_path.exists():
        env_path.touch()
    merged: dict[str, str] = {}
    for source in (repo_root / ".env", repo_root / ".env.providers.local"):
        if source.is_file():
            merged.update(
                {key: value for key, value in dotenv_values(source).items() if value is not None}
            )
    pilot_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if not pilot_python.is_file():
        pilot_python = Path(sys.executable).resolve()
    merged.update(
        {
            "DOXAGENT_PILOT_REPO_ROOT": str(repo_root),
            "DOXAGENT_PILOT_CASES_ROOT": str(cases),
            "DOXAGENT_PILOT_PYTHON": str(pilot_python),
            "DOXAGENT_PILOT_ENV_FILE": str(env_path),
        }
    )
    required = (
        "DOXAGENT_CODEX_WORKER_BEARER_TOKEN",
        "DOXAGENT_CODEX_CAPABILITY_SECRET",
    )
    missing = [key for key in required if not merged.get(key)]
    if missing:
        raise RuntimeError("required local secrets are missing: " + ", ".join(missing))
    for key, value in sorted(merged.items()):
        set_key(str(env_path), key, value, quote_mode="always")
    print(f"Pilot runtime installed: {runtime}")
    print(f"Persistent environment installed: {env_path}")
    print("Secrets were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
