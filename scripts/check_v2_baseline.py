"""Reproduce selected offline failures against HEAD source without changing this checkout."""

from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "dev_plan/workflow_v2/backend_delivery/baseline-regression.txt"
    tests = sys.argv[1:] or ["tests/test_persistent_runtime_w3.py", "tests/test_persistent_runtime_v2.py"]
    with TemporaryDirectory(prefix="doxagent-head-") as directory:
        archive = subprocess.check_output(["git", "archive", "HEAD", "src"], cwd=root)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(directory, filter="data")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(directory) / "src")
        command = [sys.executable, "-m", "pytest", *tests, "-q", "--disable-warnings",
                   "--override-ini", "pythonpath="+str(Path(directory) / "src")]
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("HEAD="+subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                           text=True).strip()+"\n"+result.stdout+result.stderr, encoding="utf-8")
        print(f"HEAD baseline pytest exit={result.returncode}; report={output}")


if __name__ == "__main__":
    main()
