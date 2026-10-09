"""Standalone standard-library reader copied into each indexed workspace."""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument(
        "--group", choices=["fields", "reports", "units", "records"], default="fields"
    )
    reading = commands.add_parser("read")
    select = reading.add_mutually_exclusive_group(required=True)
    select.add_argument("--asset")
    select.add_argument("--pointer")
    reading.add_argument("--section")
    for command in (listing, reading):
        command.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent

    def read(path):
        target = (base / path).resolve()
        if not target.is_relative_to(base):
            raise ValueError("Only declared index inputs may be read")
        with target.open(encoding="utf-8", newline="") as stream:
            return stream.read()

    index = json.loads(read("index.json"))
    if args.command == "list":
        group = index["groups"][args.group]
        paths = [f"{group['catalog']}/{n}.json" for n in range(1, group["total_pages"] + 1)]
        if not paths and args.page == 1:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(json.dumps(dict(page=1, total_pages=0, has_more=False, entries=[])))
            return
    else:
        if args.pointer is not None:
            path = (
                "pointers/"
                + hashlib.sha256(args.pointer.encode("utf-8")).hexdigest()[:20]
                + ".json"
            )
        else:
            if not args.asset.isalnum():
                raise ValueError("Invalid asset ID")
            path = f"entries/{args.asset}.json"
        entry = json.loads(read(path))
        paths = entry["pages"]
        if args.section:
            paths = next(h["pages"] for h in entry["sections"] if h["id"] == args.section)
    if not 1 <= args.page <= len(paths):
        raise ValueError(f"Page must be between 1 and {len(paths)}")
    content = read(paths[args.page - 1])
    if args.command == "list":
        value = json.loads(content)
    else:
        value = dict(
            page=args.page, total_pages=len(paths), has_more=args.page < len(paths), content=content
        )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
