"""Normalize the upstream release's local versions and record the built artifact."""

import hashlib
import json
import platform
import re
import subprocess
import sys
import tomllib
from pathlib import Path

UPSTREAM = "01fc69f4026735edfdf6789820549727a4867b11"


def normalize_release_lock(source):
    workspace = source / "codex-rs"
    version = tomllib.loads((workspace / "Cargo.toml").read_text())["workspace"]["package"][
        "version"
    ]
    lock = workspace / "Cargo.lock"
    blocks = lock.read_text().split("[[package]]")
    for i, block in enumerate(blocks):
        if '\nsource = "' not in block:
            blocks[i] = re.sub(r'(?m)^version = "0\.0\.0"$', f'version = "{version}"', block)
    lock.write_text("[[package]]".join(blocks))


def manifest(source, patch, output):
    def digest(path):
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()

    value = dict(
        schema_version=1,
        capability="w3-time-awareness-v1",
        upstream_commit=UPSTREAM,
        patch_sha256=digest(patch),
        cargo_lock_sha256=digest(source / "codex-rs/Cargo.lock"),
        binary_sha256=digest(output / "codex"),
        companions={
            "codex-code-mode-host": {
                "sha256": digest(output / "codex-code-mode-host"),
                "source": "same pinned upstream build",
            }
        },
        symbols_sha256=digest(output / "symbols/codex.debug"),
        packaging="GNU split debug; strip --strip-unneeded; symbols excluded from runtime image",
        architecture=platform.machine(),
        rustc=subprocess.check_output(["rustc", "--version"], text=True).strip(),
        build_command="cargo build --locked --release -p codex-cli -p codex-code-mode-host --bins",
        lock_preparation="upstream local workspace versions only; third-party pins unchanged",
    )
    (output / "manifest.json").write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "normalize":
        normalize_release_lock(Path(sys.argv[2]))
    else:
        manifest(*(Path(p) for p in sys.argv[2:]))
