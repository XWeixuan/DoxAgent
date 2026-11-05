"""Build the pinned Linux Harness artifact without touching installed SDK packages."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "deploy/codex-runtime")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    context = root / "deploy/codex-patches"
    subprocess.run(
        [
            "docker",
            "build",
            "--progress=plain",
            "--build-arg",
            f"CARGO_BUILD_JOBS={args.jobs}",
            "--output",
            f"type=local,dest={output}",
            str(context),
        ],
        check=True,
    )
    manifest = json.loads((output / "manifest.json").read_text())
    with (output / "codex").open("rb") as file:
        actual = hashlib.file_digest(file, "sha256").hexdigest()
    if actual != manifest["binary_sha256"]:
        raise ValueError("Built Codex digest does not match its manifest")
    companions = manifest.get("companions", {})
    if "codex-code-mode-host" not in companions:
        raise ValueError("Codex runtime bundle is missing its code-mode host")
    for name, record in companions.items():
        with (output / name).open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != record["sha256"]:
            raise ValueError(f"Built {name} digest does not match its manifest")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
