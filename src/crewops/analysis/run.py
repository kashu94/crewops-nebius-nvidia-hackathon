"""Analyse stored disruptions from the command line.

    uv run python -m crewops.analysis.run --pending          # every disruption still REPORTED
    uv run python -m crewops.analysis.run --id r-demo-0001   # one disruption

For reports stored before the stream-triggered analysis existed, or ones whose analysis
failed and was parked. Storing is exactly-once, so running it again is harmless.
Settings and the AWS profile come from ``.env``, as for the other commands.
"""

from __future__ import annotations

import argparse
import os
import sys

from crewops.config import Settings, load_env_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--pending", action="store_true", help="all REPORTED disruptions")
    which.add_argument("--id", help="one disruption id")
    args = parser.parse_args(argv)

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    from crewops.analysis.runner import Operation, analyse_by_id, analyse_item
    from crewops.store.ops_store import table_for
    from crewops.store.reports import disruptions_with_status

    table = table_for(Settings.from_env())
    operation = Operation.load(table)
    if args.id:
        jobs = [(args.id, lambda: analyse_by_id(table, args.id, operation))]
    else:
        jobs = [
            (item["disruption_id"], lambda item=item: analyse_item(table, item, operation))
            for item in disruptions_with_status(table)
        ]

    if not jobs:
        print("Nothing to analyse: no disruption is waiting in REPORTED.")
    failed = 0
    for disruption_id, job in jobs:
        try:
            r = job()
        except Exception as exc:  # report it and carry on with the rest
            failed += 1
            print(f"{disruption_id}: failed ({type(exc).__name__}: {exc})")
            continue
        print(f"{r.disruption_id}: {r.outcome}")
        if r.summary:
            print(f"  {r.summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
