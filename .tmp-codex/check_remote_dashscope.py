from __future__ import annotations
from pathlib import Path
import subprocess

keys = {
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_FALLBACK_API_KEY",
    "DASHSCOPE_FALLBACK_API_KEYS",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_CHAT_BASE_URL",
    "DASHSCOPE_MODEL",
    "DASHSCOPE_ENABLE_THINKING",
    "DASHSCOPE_THINKING_BUDGET",
}

def fp(key: str, value: str) -> str:
    if key.endswith("KEY") or key.endswith("KEYS"):
        if len(value) > 8:
            return value[:4] + "..." + value[-4:]
        return "<short-set>" if value else "<empty>"
    return value

root = Path("/root/doxagent")
p = root / ".env"
if not p.exists():
    print("NO_REMOTE_ENV")
else:
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in keys:
            print(f"{key}={fp(key, value)}")
print("-- code --")
for path in ["src/doxagent/settings.py", "src/doxagent/agents/runner.py", ".env.example", "changelog"]:
    text = (root / path).read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if "dashscope_fallback_api_keys" in line or "DASHSCOPE_FALLBACK_API_KEYS" in line]
    print(path + ":" + ("present" if hits else "missing"))
