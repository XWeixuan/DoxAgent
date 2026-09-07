"""Local build/contract fingerprints; never inspect credentials or environment files."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def execution_version() -> dict[str, str]:
    source = Path(__file__).resolve().parents[2]
    prompts = source.parent / "prompts"

    def digest(root: Path, pattern: str) -> str:
        result = hashlib.sha256()
        for path in sorted(root.rglob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                result.update(path.relative_to(root).as_posix().encode())
                result.update(b"\0")
                result.update(path.read_bytes())
        return result.hexdigest()

    return {
        "workflow": "V2",
        "contract": "ticker-initialization-v2",
        "code_revision": os.environ.get("DOXAGENT_BUILD_COMMIT", "unversioned-local-build")[:128],
        "code_sha256": digest(source, "*.py"),
        "prompt_skill_sha256": digest(prompts, "*") if prompts.is_dir() else "worker-owned",
    }
