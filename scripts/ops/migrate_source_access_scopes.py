"""Preview by default; apply only against an explicitly frozen Registry revision."""

import argparse
import asyncio
import json
from pathlib import Path

from doxagent.site_strategy.isolation_migration import (
    apply_migration,
    inspect_legacy_targets,
    migration_plan,
)
from doxagent.site_strategy.repository import SiteStrategyRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-revision")
    parser.add_argument("--backup")
    parser.add_argument("--supervisor-socket")
    args = parser.parse_args()
    repository = SiteStrategyRepository(args.registry, read_only=not args.apply)
    try:
        plan = migration_plan(repository)
        observations = (
            asyncio.run(
                inspect_legacy_targets(
                    repository,
                    args.supervisor_socket,
                    [
                        update["spec"]["identity_id"]
                        for item in plan["scopes"]
                        for update in item["identities"]
                        if update["action"] == "UPDATE_ON_COLD_START"
                    ],
                )
            )
            if args.supervisor_socket
            else {}
        )
        plan["legacy_targets"] = observations
        if args.apply:
            if not args.expected_revision or not args.backup:
                parser.error("--apply requires --expected-revision and --backup")
            plan = apply_migration(
                repository,
                plan,
                expected_revision=args.expected_revision,
                backup_path=Path(args.backup),
                observations=observations,
            )
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
