"""Rebuild immutable human/JSON exports from a Published Event Library version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.repository import EventLibraryRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-library", type=Path, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--library-version", type=int)
    args = parser.parse_args(argv)
    compiler = EventLibraryViewCompiler(EventLibraryRepository(args.event_library))
    exports = compiler.export_published(
        ticker=args.ticker,
        output_dir=args.output_dir,
        version=args.library_version,
    )
    print(json.dumps({key: str(value) for key, value in exports.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
