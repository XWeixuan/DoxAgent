"""Rewrite generated catalog arrays with canonical compact UTF-8 JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_OUTPUT_DIR, read_json, write_json
from validate_catalogs import SCHEMAS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    for catalog in SCHEMAS:
        path = args.output_dir / f"{catalog}.json"
        if path.exists():
            write_json(path, read_json(path))
            print(f"{path.name}: {path.stat().st_size}")


if __name__ == "__main__":
    main()
