from __future__ import annotations
from pathlib import Path
import difflib

root = Path(r"C:\Users\WEIXUANXIE\Desktop\DoxAgent")
remote = root / ".tmp-codex" / "remote-dashscope-code"
patch_path = root / ".tmp-codex" / "dashscope_multi_fallback_remote_code.patch"
items = [
    (remote / "settings.py", root / "src" / "doxagent" / "settings.py", "src/doxagent/settings.py"),
    (remote / "runner.py", root / "src" / "doxagent" / "agents" / "runner.py", "src/doxagent/agents/runner.py"),
]
chunks: list[str] = []
for old_path, new_path, rel in items:
    old = old_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new = new_path.read_text(encoding="utf-8").splitlines(keepends=True)
    chunks.extend(
        difflib.unified_diff(
            old,
            new,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
        )
    )
patch_path.write_text("".join(chunks), encoding="utf-8", newline="\n")
print(patch_path)
