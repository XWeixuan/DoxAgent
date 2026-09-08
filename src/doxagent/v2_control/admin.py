"""Explicit local profile binding; never infers Live authority from the global profile."""

import argparse

from doxagent.persistent_runtime_v2.journal import RuntimeJournal

from .repository import ControlRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-db", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--mode", required=True, choices=("PAPER_TRADING", "LIVE_TRADING"))
    parser.add_argument("--profile-revision", required=True)
    parser.add_argument("--expected-profile-revision")
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    repository = ControlRepository(RuntimeJournal(args.runtime_db, initialize=False))
    repository.bind(
        args.ticker,
        args.mode,
        args.profile_revision,
        expected=args.expected_profile_revision,
        actor=args.actor,
    )
    print("Profile binding recorded. This command does not start analysis or release orders.")


if __name__ == "__main__":
    main()
