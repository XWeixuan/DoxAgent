"""Small, bounded maintenance of regenerable files, never browser identity data."""

import os
import stat
import subprocess
import time
from pathlib import Path

from .runtime import OwnerFileLock

CACHES = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
}


def _files(root, *, deadline, limit=10000):
    """Do not follow symlinks/junction-like mount boundaries or scan unbounded trees."""
    result = []
    stack = [root]
    device = root.stat().st_dev
    while stack and len(result) < limit and time.monotonic() < deadline:
        directory = stack.pop()
        if directory.is_symlink() or directory.stat().st_dev != device:
            continue
        for child in directory.iterdir():
            if len(result) >= limit or time.monotonic() >= deadline:
                break
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                continue
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
            elif stat.S_ISREG(info.st_mode):
                result.append((child, info))
    return result, not stack


def _in_use(paths, deadline):
    """Run in the Chrome PID namespace; unreadable process evidence means defer."""
    protected = set()
    wanted = {str(path.resolve()): path for path in paths}
    if os.name != "posix":
        return set(paths)
    for process in Path("/proc").iterdir():
        if time.monotonic() >= deadline:
            return set(paths)
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                    if target in wanted:
                        protected.add(wanted[target])
                except FileNotFoundError:
                    continue
            for line in (process / "maps").read_text().splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) == 6 and parts[5] in wanted:
                    protected.add(wanted[parts[5]])
        except FileNotFoundError:
            continue
        except PermissionError:
            return set(paths)
    return protected


def maintain_profile(root, directory_key, *, allow_cache=False, seconds=1, now=None):
    root = Path(root).resolve()
    directory = root / directory_key
    if directory.is_symlink() or directory.resolve().parent != root or not directory.is_dir():
        return {"status": "unmanaged", "deleted_bytes": 0}
    deadline = time.monotonic() + min(2, seconds)
    instant = now or time.time()
    deleted = 0
    lock = None
    try:
        if allow_cache:
            lock = OwnerFileLock(root / ".profile-locks" / f"{directory_key}.lock")
            lock.acquire()  # Also protects against a Chrome in a different container.
            from .runtime import profile_browser_processes

            if profile_browser_processes(directory):
                return {"status": "live_writer", "deleted_bytes": 0}
            candidates = []
            cache_directories = []
            for parent in (directory, directory / "Default"):
                if not parent.resolve().is_relative_to(directory):
                    continue
                for name in sorted(CACHES, key=lambda value: (value != "Cache", value)):
                    target = parent / name
                    if target.is_dir() and not target.is_symlink():
                        cache_directories.append(target)
            measured_total = 0
            if os.name == "posix" and cache_directories:
                try:
                    # Native traversal avoids spending the entire Python budget
                    # stat-ing 100k tiny cache entries. Never follows symlinks or
                    # crosses filesystems; timeout leaves the lower-bound path.
                    usage = subprocess.check_output(
                        ["du", "-sb", "--one-file-system", "--", *map(str, cache_directories)],
                        timeout=min(0.7, max(0.05, (deadline - time.monotonic()) / 3)),
                        stderr=subprocess.DEVNULL,
                    )
                    measured_total = sum(
                        int(line.split(maxsplit=1)[0]) for line in usage.splitlines()
                    )
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
            scan_deadline = min(deadline - 0.1, time.monotonic() + min(0.7, seconds / 3))
            for target in cache_directories:
                entries, _ = _files(target, deadline=scan_deadline)
                candidates.extend(entries)
            total = max(measured_total, sum(info.st_size for _, info in candidates))
            # A bounded partial scan is a safe lower bound for regenerable
            # caches. Do not perpetually defer large trees, or spend the whole
            # budget scanning and leave no time for deleting known files.
            if total > 512 * 1024**2:
                for path, info in sorted(candidates, key=lambda item: item[1].st_mtime):
                    if total <= 256 * 1024**2 or time.monotonic() >= deadline:
                        break
                    path.unlink(missing_ok=True)
                    total -= info.st_size
                    deleted += info.st_size
        # Only recognized diagnostic files, not HTTP/SW/Local Storage, may be
        # removed online; retain 48h and every open/mapped file.
        diagnostics = []
        for relative in ("BrowserMetrics", "Crashpad/reports", "Crash Reports"):
            target = directory / relative
            if (
                target.is_dir()
                and not target.is_symlink()
                and target.resolve().is_relative_to(directory)
            ):
                entries, _ = _files(target, deadline=deadline, limit=2000)
                diagnostics.extend((p, s) for p, s in entries if p.suffix in {".pma", ".dmp"})
        if sum(s.st_size for _, s in diagnostics) > 128 * 1024**2:
            old = [(p, s) for p, s in diagnostics if instant - s.st_mtime > 48 * 3600]
            used = _in_use([p for p, _ in old], deadline)
            for path, info in sorted(old, key=lambda item: item[1].st_mtime):
                if time.monotonic() >= deadline:
                    break
                if path not in used:
                    path.unlink(missing_ok=True)
                    deleted += info.st_size
        return {"status": "managed", "deleted_bytes": deleted}
    except (OSError, RuntimeError):
        return {"status": "deferred", "deleted_bytes": deleted}
    finally:
        if lock:
            lock.release()
