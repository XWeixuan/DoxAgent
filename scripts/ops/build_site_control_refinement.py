"""Immutable package-only refinement when all other application blobs are unchanged."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--receipt-dir", required=True)
    args = parser.parse_args()
    root = Path(args.receipt_dir)
    manifest = json.loads((root / "build.json").read_text())

    def run(command):
        return subprocess.check_output(command, text=True).strip()

    assert run(["git", "rev-parse", "HEAD"]) == args.commit
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"])
    baseline = manifest["commit"]
    changed = run(
        [
            "git",
            "diff",
            "--name-only",
            baseline,
            args.commit,
            "--",
            "src",
            "prompts",
            "pyproject.toml",
            "uv.lock",
        ]
    ).splitlines()
    assert all(p.startswith("src/doxagent/site_strategy/") for p in changed)
    digest = hashlib.sha256()
    process = subprocess.Popen(["git", "archive", args.commit], stdout=subprocess.PIPE)
    for chunk in iter(lambda: process.stdout.read(1_048_576), b""):
        digest.update(chunk)
    assert process.wait() == 0
    source_hash = digest.hexdigest()
    for kind in ("site", "supervisor"):
        before = manifest["images"][kind]
        image = json.loads(run(["docker", "image", "inspect", before["tag"]]))[0]
        assert image["Id"] == before["image"]
        previous_commit = image["Config"]["Labels"]["org.opencontainers.image.revision"]
        previous_changes = run(
            [
                "git",
                "diff",
                "--name-only",
                previous_commit,
                args.commit,
                "--",
                "src",
                "prompts",
                "pyproject.toml",
                "uv.lock",
            ]
        ).splitlines()
        assert all(p.startswith("src/doxagent/site_strategy/") for p in previous_changes)
        tag = "doxagent-site-access" if kind == "site" else "doxagent-chrome-supervisor"
        tag += ":main-" + args.commit[:8]
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "deploy/Dockerfile.site-control-refinement",
                "--build-arg",
                "APPROVED_BASE=" + before["tag"],
                "--build-arg",
                "DOXAGENT_BUILD_COMMIT=" + args.commit,
                "--build-arg",
                "DOXAGENT_SOURCE_SHA256=" + source_hash,
                "-t",
                tag,
                ".",
            ],
            check=True,
        )
        after = json.loads(run(["docker", "image", "inspect", tag]))[0]
        inherited = image["RootFS"]["Layers"]
        assert after["RootFS"]["Layers"][: len(inherited)] == inherited
        manifest["images"][kind] = {
            "tag": tag,
            "image": after["Id"],
            "source_commit": args.commit,
            "dependency_layers_reused": True,
            "approved_parent": before,
        }
    manifest["commit"] = args.commit
    manifest["source_sha256"] = source_hash
    (root / "build.json").write_text(json.dumps(manifest, indent=2))
    print(
        json.dumps(
            {"commit": args.commit, "images": {k: v["tag"] for k, v in manifest["images"].items()}}
        )
    )


if __name__ == "__main__":
    main()
