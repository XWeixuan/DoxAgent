"""The primary and existing ops refresh ledgers share the Worker data volume."""
from pathlib import Path


def control_paths(settings):
    value=getattr(settings,'ticker_initialization_control_path',None)
    if not value:return []
    primary=Path(value).resolve();root=primary.parent.parent
    paths=[primary,*sorted((root/'ops').glob('*/control.sqlite3'))]
    return list(dict.fromkeys(p.resolve() for p in paths
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root)))
