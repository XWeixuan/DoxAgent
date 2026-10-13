"""Read-only host capacity report. Never deletes data or changes retention."""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = args.data_root.resolve(strict=True)
    disk = shutil.disk_usage(root)
    databases = {str(p.relative_to(root)): p.stat().st_size
                 for name in ("read", "bus", "runtime")
                 for p in (root / name).glob("*.sqlite3*") if p.is_file()}
    backups = []
    backup_roots = [root / name for name in ("backups", "production-backups", "bus/backups", "bus/repair-backups")]
    backup_roots += args.backup_root or [Path('/home/ubuntu/doxagent-backups'),
                                       Path('/var/lib/doxagent/initialization-repair/backups')]
    for target in backup_roots:
        if not target.is_symlink() and target.is_dir():
            files = [p for p in target.rglob('*') if p.is_file() and not p.is_symlink()]
            backups.append({"path": str(target), "bytes": sum(p.stat().st_size for p in files),
                            "file_count": len(files),
                            "completed_migrations": len(list(target.glob('*/.completed-migration-backup.json')))})
    print(json.dumps({"disk": disk._asdict(), "databases": databases, "backups": backups,
                      "warning": disk.free < 20 * 1024**3,
                      "critical": disk.free < 1024**3}, indent=2))


if __name__ == "__main__":
    main()
