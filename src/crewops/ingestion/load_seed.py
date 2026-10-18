"""Load the operation into DynamoDB: the seed week and every week after it to 31 December.

    uv run python -m crewops.ingestion.load_seed            # validate, then write
    uv run python -m crewops.ingestion.load_seed --week-only  # just the seed week
    uv run python -m crewops.ingestion.load_seed --dry-run  # validate and count only
    uv run python -m crewops.ingestion.load_seed --replace  # dev/test: empty the table first

Settings come from ``.env`` and the environment (``AWS_PROFILE``, ``AWS_REGION``,
``CREWOPS_STAGE``), the same as the CDK app, so it writes to the table that stage deployed.
Validation errors stop the load before anything is written. Writing is idempotent: running
it again replaces the same items rather than duplicating them. It never deletes: records of
an older week stay beside the new ones, so a changed week is loaded with ``--replace``,
which first empties the table (every disruption, plan and audit event too) and is refused
on any stage but a disposable one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from crewops.config import Settings, load_env_file
from crewops.ingestion.seed import Severity, load_seed, validate

DISPOSABLE_STAGES = frozenset({"dev", "test"})
"""Keep in sync with the CDK app's list."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed-dir", type=Path, default=None, help="defaults to data/seed")
    parser.add_argument("--dry-run", action="store_true", help="validate and count only")
    parser.add_argument(
        "--week-only",
        action="store_true",
        help="load only the seed week, not the calendar carried forward to 31 December",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="dev/test only: delete every item in the table, then load the week",
    )
    args = parser.parse_args(argv)

    load_env_file()
    settings = Settings.from_env()

    seed = load_seed(args.seed_dir)
    if not args.week_only:
        from crewops.ingestion.calendar import LAST_DAY, extend

        print(f"Rostering every week to {LAST_DAY} (each checked legal; about 20 s)...")
        seed = extend(seed)
    issues = validate(seed)
    for issue in issues:
        print(issue, file=sys.stderr)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        print(f"{len(errors)} validation error(s); nothing written.", file=sys.stderr)
        return 1

    from crewops.store.ops_store import OpsStore, seed_records, table_for

    if args.dry_run:
        total = sum(1 for _ in seed_records(seed))
        print(f"Dry run: {total} items would be written to {settings.table_name}.")
        return 0

    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    if args.replace and settings.stage not in DISPOSABLE_STAGES:
        print(
            f"--replace deletes everything; stage {settings.stage!r} is not disposable "
            f"({', '.join(sorted(DISPOSABLE_STAGES))}).",
            file=sys.stderr,
        )
        return 1

    table = table_for(settings)
    if args.replace:
        removed = OpsStore(table).delete_all()
        print(f"Emptied {settings.table_name}: {removed} items deleted.")
    print(f"Writing to {settings.table_name} in {settings.aws_region} (profile {profile})...")
    counts = OpsStore(table).put_seed(seed)
    for entity, n in sorted(counts.items()):
        print(f"  {entity:<17} {n}")
    print(f"Done: {sum(counts.values())} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
