from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


def read_secret(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def fingerprint(value: str) -> str:
    if len(value) > 8:
        return value[:4] + "..." + value[-4:]
    return "<short-set>" if value else "<empty>"


def find_value(lines: list[str], key: str) -> str | None:
    prefix = key + "="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def add_unique(keys: list[str], raw_value: str | None) -> None:
    if not raw_value:
        return
    for item in raw_value.replace(";", ",").split(","):
        key = item.strip()
        if key and key not in keys:
            keys.append(key)


def set_key(lines: list[str], key: str, value: str, *, after_key: str | None = None) -> list[str]:
    prefix = key + "="
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                out.append(prefix + value)
                replaced = True
            else:
                out.append(line)
        else:
            out.append(line)
    if replaced:
        return out
    insert_line = prefix + value
    if after_key is not None:
        after_prefix = after_key + "="
        for index, line in enumerate(out):
            if line.startswith(after_prefix):
                out.insert(index + 1, insert_line)
                return out
    out.append(insert_line)
    return out


def write_env(env_path: Path, new_key: str) -> Path:
    raw = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    trailing_newline = raw.endswith(("\n", "\r\n"))
    lines = raw.splitlines()

    fallbacks: list[str] = []
    add_unique(fallbacks, find_value(lines, "DASHSCOPE_API_KEY"))
    add_unique(fallbacks, find_value(lines, "DASHSCOPE_FALLBACK_API_KEY"))
    add_unique(fallbacks, find_value(lines, "DASHSCOPE_FALLBACK_API_KEYS"))
    fallbacks = [key for key in fallbacks if key != new_key]
    if not fallbacks:
        raise RuntimeError("No existing DASHSCOPE key found to preserve as fallback.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = env_path.with_name(f"{env_path.name}.before_dashscope_key_rotation_{stamp}")
    if env_path.exists():
        shutil.copy2(env_path, backup)

    lines = set_key(lines, "DASHSCOPE_API_KEY", new_key)
    lines = set_key(lines, "DASHSCOPE_FALLBACK_API_KEY", fallbacks[0], after_key="DASHSCOPE_API_KEY")
    lines = set_key(
        lines,
        "DASHSCOPE_FALLBACK_API_KEYS",
        ",".join(fallbacks[1:]),
        after_key="DASHSCOPE_FALLBACK_API_KEY",
    )
    text = newline.join(lines)
    if trailing_newline or raw:
        text += newline
    env_path.write_text(text, encoding="utf-8")
    print(f"backup={backup}")
    print("DASHSCOPE_API_KEY=" + fingerprint(new_key))
    print("DASHSCOPE_FALLBACK_API_KEY=" + fingerprint(fallbacks[0]))
    print("DASHSCOPE_FALLBACK_API_KEYS_COUNT=" + str(max(0, len(fallbacks) - 1)))
    return backup


def patch_text(root: Path) -> None:
    env_example = root / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    if "DASHSCOPE_FALLBACK_API_KEYS=" not in text:
        text = text.replace(
            "DASHSCOPE_FALLBACK_API_KEY=\n",
            "DASHSCOPE_FALLBACK_API_KEY=\nDASHSCOPE_FALLBACK_API_KEYS=\n",
            1,
        )
        env_example.write_text(text, encoding="utf-8")

    changelog = root / "changelog"
    line = "- Added multi-key DashScope/Bailian fallback support via `DASHSCOPE_FALLBACK_API_KEYS`, preserving the existing single fallback setting while allowing rotated provider keys to keep all prior keys available as fallback clients."
    text = changelog.read_text(encoding="utf-8")
    if line not in text:
        text = text.replace("## 2026-06-28\n\n", "## 2026-06-28\n\n" + line + "\n", 1)
        changelog.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("new_key_file")
    args = parser.parse_args()
    root = Path(args.root)
    write_env(root / ".env", read_secret(Path(args.new_key_file)))
    patch_text(root)


if __name__ == "__main__":
    main()
