"""Work plans from the terminal, as a member of the team.

    uv run python -m crewops.plans.desk --as rohit.malhotra queue
    uv run python -m crewops.plans.desk --as rohit.malhotra show r-demo-0001
    uv run python -m crewops.plans.desk --as rohit.malhotra submit r-demo-0001
    uv run python -m crewops.plans.desk --as rohit.malhotra submit r-demo-0001 \
        --choose P-2291/Captain=C-1526
    uv run python -m crewops.plans.desk --as meera.bhatt approve r-demo-0001 pl-...
    uv run python -m crewops.plans.desk --as meera.bhatt reject r-demo-0001 pl-... --reason "..."
    uv run python -m crewops.plans.desk --as rohit.malhotra withdraw r-demo-0001 pl-...
    uv run python -m crewops.plans.desk --as rohit.malhotra close r-demo-0002 \
        --reason "flights cancelled"

An operator's tool: it acts as the named team member, using the roles and desk recorded in
their profile, with the AWS profile from ``.env`` (whoever holds those credentials can
already change anything). People reach the same rules through the assistant, signed in.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from crewops.domain import Role


def caller_for(table: Any, username: str):
    from crewops.store.users import get_profile
    from crewops.tools import Caller

    profile = get_profile(table, username)
    if profile is None:
        raise SystemExit(f"No team member {username!r}: run crewops.auth.provision first.")
    roles = frozenset(Role(r) for r in profile.get("roles", []))
    return Caller(profile["username"], roles, profile.get("crew_id"), profile.get("desk"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as", dest="user", required=True, help="team member's username")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("queue")
    show = sub.add_parser("show")
    show.add_argument("disruption")
    submit = sub.add_parser("submit")
    submit.add_argument("disruption")
    submit.add_argument("--choose", action="append", default=[], help="SEAT=CREW_ID")
    submit.add_argument("--note", default="")
    submit.add_argument(
        "--accept-uncovered", action="store_true", help="submit although some seats have no cover"
    )
    close = sub.add_parser("close")
    close.add_argument("disruption")
    close.add_argument("--reason", required=True)
    for name in ("approve", "reject", "withdraw"):
        p = sub.add_parser(name)
        p.add_argument("disruption")
        p.add_argument("plan")
        p.add_argument("--reason", default="")
    args = parser.parse_args(argv)

    from crewops.config import Settings, load_env_file

    load_env_file()
    if os.environ.get("AWS_PROFILE", "default") == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.plans.service import PlanError, Plans
    from crewops.store import plans as store
    from crewops.store.ops_store import table_for
    from crewops.store.reports import get_disruption

    table = table_for(Settings.from_env())
    try:
        caller = caller_for(table, args.user)
        plans = Plans(table)
        if args.action == "queue":
            items = plans.queue(caller)
            for item in items:
                print(_line(item))
            if not items:
                print("Nothing is waiting for you.")
        elif args.action == "show":
            item = get_disruption(table, args.disruption)
            if item is None:
                print(f"No disruption {args.disruption}.", file=sys.stderr)
                return 1
            print(f"{args.disruption}: {item['status']}")
            print((item.get("analysis") or {}).get("summary", ""))
            for plan in store.plans_for(table, args.disruption):
                print(_line({"kind": plan["status"].lower(), **_brief(plan)}))
            for event in store.audit_trail(table, args.disruption):
                extra = f" ({event['reason']})" if event.get("reason") else ""
                print(f"  {event['at']} {event['action']} by {event['actor']}{extra}")
        elif args.action == "submit":
            choices = dict(c.split("=", 1) for c in args.choose)
            out = plans.submit(caller, args.disruption, choices, args.note, args.accept_uncovered)
            print(f"Submitted {out.plan['plan_id']}, ₹{out.plan['total_cost_inr']:,}.")
            for warning in out.warnings:
                print(f"  contention: {warning}")
            for seat in out.plan["uncovered"]:
                print(f"  uncovered: {seat['seat']}: {seat['reason']}")
        elif args.action == "close":
            plans.close(caller, args.disruption, args.reason)
            print(f"{args.disruption}: CLOSED")
        else:
            if args.action == "approve":
                out = plans.approve(caller, args.disruption, args.plan)
            elif args.action == "withdraw":
                out = plans.withdraw(caller, args.disruption, args.plan, args.reason)
            else:
                out = plans.reject(caller, args.disruption, args.plan, args.reason)
            print(f"{args.plan}: {out.status}")
            for reason in out.reasons:
                print(f"  {reason}")
    except PlanError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    return 0


def _brief(plan: dict[str, Any]) -> dict[str, Any]:
    from crewops.plans.service import summary

    return summary(plan)


def _line(item: dict[str, Any]) -> str:
    if "plan_id" in item:
        seats = "; ".join(item.get("assignments", []))
        reason = f" Reason: {item['reason']}" if item.get("reason") else ""
        extra = "".join(f"\n    contention: {w}" for w in item.get("warnings", []))
        extra += "".join(f"\n    uncovered: {u}" for u in item.get("uncovered", []))
        return (
            f"[{item['kind']}] {item['disruption_id']} {item['plan_id']} by "
            f"{item['submitted_by']} ({item['desk']}): {seats}. Total "
            f"₹{item['total_cost_inr']:,}.{reason}{extra}"
        )
    return f"[{item['kind']}] {item['disruption_id']}: {item.get('summary', '')}"


if __name__ == "__main__":
    raise SystemExit(main())
