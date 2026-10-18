"""Compatibility entry point for stable dependency builds from committed source."""
import argparse
import json
from pathlib import Path
from storage_release import DEFAULT_ROOT, atomic, build, lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--receipt-dir',required=True,type=Path)
    args=parser.parse_args()
    with lock(DEFAULT_ROOT):
        receipt=build(Path.cwd(),args.commit,DEFAULT_ROOT,['backend','site','supervisor'])
        atomic(args.receipt_dir/'build.json',receipt)
    print(json.dumps(receipt))


if __name__=='__main__':main()
