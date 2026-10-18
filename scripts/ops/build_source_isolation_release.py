"""Reuse immutable approved dependencies; build committed MAIN, never a dirty overlay.

Run on the Docker host as root with an exact commit and a private receipt directory.
No container, Profile, Registry or production environment is changed by this command.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--receipt-dir", required=True)
    args = parser.parse_args()
    root = Path(args.receipt_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def run(command):
        return subprocess.check_output(command, text=True).strip()

    assert run(["git", "rev-parse", "HEAD"]) == args.commit
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"])
    # This path copies source only (no apt/uv/browser installation). Preserve a
    # separate 1.5GiB post-build floor and abort on real growth, not a full-build gate.
    assert shutil.disk_usage("/").free >= 2 * 1024**3, "bounded_build_space_unavailable"
    containers = {
        "backend": "doxagent-v2-v2-message-bus-1",
        "site": "doxagent-v2-v2-site-access-1",
        "supervisor": "doxagent-v2-v2-chrome-supervisor-1",
    }
    bases = {}
    expected = {
        name: hashlib.sha256(Path(name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in ("pyproject.toml", "uv.lock")
    }
    for kind, container in containers.items():
        inspected = json.loads(run(["docker", "inspect", container]))[0]
        image = inspected["Image"]
        tag = "doxagent-source-deps:" + kind + "-" + image.split(":")[1][:12]
        subprocess.run(["docker", "tag", image, tag], check=True)
        actual = json.loads(
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    tag,
                    "-c",
                    "import hashlib,json,pathlib;print(json.dumps({n:hashlib.sha256("
                    "pathlib.Path('/app',n).read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest()"
                    " for n in ('pyproject.toml','uv.lock')}))",
                ]
            )
        )
        assert actual == expected, "dependency_manifest_changed:" + kind
        layers = json.loads(run(["docker", "image", "inspect", image]))[0]["RootFS"]["Layers"]
        bases[kind] = {"tag": tag, "image": image, "layers": layers}
    digest = hashlib.sha256()
    archive = subprocess.Popen(["git", "archive", "HEAD"], stdout=subprocess.PIPE)
    for chunk in iter(lambda: archive.stdout.read(1_048_576), b""):
        digest.update(chunk)
    assert archive.wait() == 0
    source = digest.hexdigest()
    receipt = {"commit": args.commit, "source_sha256": source, "bases": bases, "images": {}}
    for kind in containers:
        name = {
            "backend": "doxagent-v2",
            "site": "doxagent-site-access",
            "supervisor": "doxagent-chrome-supervisor",
        }[kind]
        tag = name + ":main-" + args.commit[:8]
        command = [
            "docker",
            "build",
            "-f",
            "deploy/Dockerfile.source-release",
            "--target",
            kind,
            "-t",
            tag,
            "--build-arg",
            "DOXAGENT_BUILD_COMMIT=" + args.commit,
            "--build-arg",
            "DOXAGENT_SOURCE_SHA256=" + source,
        ]
        for key, value in bases.items():
            command.extend(["--build-arg", key.upper() + "_BASE=" + value["tag"]])
        subprocess.run([*command, "."], check=True)
        value = json.loads(run(["docker", "image", "inspect", tag]))[0]
        inherited = bases[kind]["layers"]
        assert value["RootFS"]["Layers"][: len(inherited)] == inherited
        receipt["images"][kind] = {
            "tag": tag,
            "image": value["Id"],
            "dependency_layers_reused": True,
        }
        (root / "build.json").write_text(json.dumps(receipt, indent=2))
        assert shutil.disk_usage("/").free >= 1.5 * 1024**3, "release_growth_budget_exceeded"
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
