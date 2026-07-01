from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

TARGET_KEYS = {"STOCKTWITS_RAPIDAPI_KEY", "STOCKTWITS_RAPIDAPI_FALLBACK_KEY"}


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_path")
    parser.add_argument("new_key_file")
    parser.add_argument("--fallback-key-file")
    parser.add_argument("--backup-dir")
    args = parser.parse_args()

    env_path = Path(args.env_path)
    new_key = read_secret(Path(args.new_key_file))
    raw = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    trailing_newline = raw.endswith(("\n", "\r\n"))
    lines = raw.splitlines()

    existing_primary = find_value(lines, "STOCKTWITS_RAPIDAPI_KEY")
    fallback = None
    if existing_primary and existing_primary != new_key:
        fallback = existing_primary
    elif args.fallback_key_file:
        fallback = read_secret(Path(args.fallback_key_file))
    else:
        fallback = find_value(lines, "STOCKTWITS_RAPIDAPI_FALLBACK_KEY")
    if not fallback:
        raise RuntimeError("No fallback key available; refusing to erase fallback semantics.")

    if env_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = Path(args.backup_dir) if args.backup_dir else env_path.parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{env_path.name}.before_stocktwits_key_rotation_{stamp}"
        shutil.copy2(env_path, backup)
        print(f"backup={backup}")

    lines = set_key(lines, "STOCKTWITS_RAPIDAPI_KEY", new_key)
    lines = set_key(
        lines,
        "STOCKTWITS_RAPIDAPI_FALLBACK_KEY",
        fallback,
        after_key="STOCKTWITS_RAPIDAPI_KEY",
    )
    text = newline.join(lines)
    if trailing_newline or raw:
        text += newline
    env_path.write_text(text, encoding="utf-8")
    print("STOCKTWITS_RAPIDAPI_KEY=" + fingerprint(new_key))
    print("STOCKTWITS_RAPIDAPI_FALLBACK_KEY=" + fingerprint(fallback))


if __name__ == "__main__":
    main()
