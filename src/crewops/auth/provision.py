"""Create and update the team's accounts, groups and profiles to match the team file.

    uv run python -m crewops.auth.provision                # show what would change
    uv run python -m crewops.auth.provision --apply        # make it so
    uv run python -m crewops.auth.provision --apply --reset-passwords
    uv run python -m crewops.auth.provision --apply --prune  # also delete accounts not in it

Runs against the stage's user pool (deploy the auth stack first) and operations table, with
the AWS profile from ``.env``. New accounts need a password: it is read from
``CREWOPS_TEAM_PASSWORD`` or asked for, never written anywhere, and set as permanent, so no
invitation or email is ever sent. Running it again changes only what differs.

An account that is not in the file loses its roles and profile, so it can sign in but do
nothing; ``--prune`` deletes it as well. An account left half-created (no usable password)
or disabled is repaired.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crewops.auth.team import TeamMember, load_team, validate_team
from crewops.domain import Role
from crewops.store.users import (
    delete_profile,
    get_profile,
    list_profiles,
    profile_item,
    put_profile,
)

COGNITO_SYMBOLS = "^$*.[]{}()?\"!@#%&/\\,><':;|_~`=+- "
"""The only characters Cognito counts as symbols. Any other character fails the password."""
PASSWORD_RULES: tuple[tuple[Callable[[str], bool], str], ...] = (
    (lambda p: 12 <= len(p) <= 256, "12 to 256 characters"),
    (lambda p: any(c.islower() and c.isascii() for c in p), "a lowercase letter"),
    (lambda p: any(c.isupper() and c.isascii() for c in p), "an uppercase letter"),
    (lambda p: any(c.isdigit() and c.isascii() for c in p), "a digit"),
    (lambda p: any(c in COGNITO_SYMBOLS and c != " " for c in p), "a symbol"),
    (
        lambda p: all(c.isascii() and (c.isalnum() or c in COGNITO_SYMBOLS) for c in p),
        "only letters, digits and the symbols " + COGNITO_SYMBOLS.strip(),
    ),
    (lambda p: p == p.strip(), "no space at the start or end"),
)
"""Exactly what the pool will accept, checked before any change: a password Cognito rejects
after an account is created would leave that account with no usable password."""
STUCK = frozenset({"FORCE_CHANGE_PASSWORD", "RESET_REQUIRED"})
ROLE_GROUPS = frozenset(r.value for r in Role)


class ProvisionError(Exception):
    pass


def password_problems(password: str) -> list[str]:
    """What the pool's password policy would reject."""
    return [need for check, need in PASSWORD_RULES if not check(password)]


def stack_output(cloudformation: Any, stack: str, key: str) -> str | None:
    try:
        stacks = cloudformation.describe_stacks(StackName=stack)["Stacks"]
    except Exception:  # no such stack, or no access: fall back to the pool's name
        return None
    for output in stacks[0].get("Outputs", []) if stacks else []:
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    return None


def find_user_pool(cognito: Any, name: str, from_stack: str | None = None) -> str | None:
    """The stage's pool: the auth stack's own output when there is one, else the one pool
    with this name. A pool kept from an earlier deployment can share the name, so two is an
    error, never a guess."""
    if from_stack:
        return from_stack
    found: list[str] = []
    token = None
    while True:
        page = cognito.list_user_pools(MaxResults=60, **({"NextToken": token} if token else {}))
        found += [p["Id"] for p in page.get("UserPools", []) if p["Name"] == name]
        token = page.get("NextToken")
        if not token:
            break
    if len(found) > 1:
        raise ProvisionError(f"{len(found)} user pools are named {name}: {', '.join(found)}")
    return found[0] if found else None


@dataclass(frozen=True, slots=True)
class Account:
    attributes: dict[str, str]
    status: str
    enabled: bool


def _existing_users(cognito: Any, pool_id: str) -> dict[str, Account]:
    """username -> account, for every account in the pool."""
    users: dict[str, Account] = {}
    token = None
    while True:
        extra = {"PaginationToken": token} if token else {}
        page = cognito.list_users(UserPoolId=pool_id, **extra)
        for u in page.get("Users", []):
            users[u["Username"].lower()] = Account(
                {a["Name"]: a["Value"] for a in u.get("Attributes", [])},
                u.get("UserStatus", "CONFIRMED"),
                u.get("Enabled", True),
            )
        token = page.get("PaginationToken")
        if not token:
            return users


def _groups_of(cognito: Any, pool_id: str, username: str) -> set[str]:
    page = cognito.admin_list_groups_for_user(UserPoolId=pool_id, Username=username, Limit=60)
    return {g["GroupName"] for g in page.get("Groups", [])}


@dataclass
class Plan:
    actions: list[str] = field(default_factory=list)
    steps: list[Callable[[], None]] = field(default_factory=list)
    needs_password: bool = False

    def add(self, text: str, step: Callable[[], None], needs_password: bool = False) -> None:
        self.actions.append(text)
        self.steps.append(step)
        self.needs_password = self.needs_password or needs_password


def plan_team(
    cognito: Any,
    pool_id: str,
    table: Any,
    team: list[TeamMember],
    password: Callable[[], str],
    *,
    reset_passwords: bool = False,
    prune: bool = False,
) -> Plan:
    """Every change needed for the pool and profiles to match ``team``. Nothing runs until
    the steps are called; the password is asked for only by a step that needs it."""
    plan = Plan()
    existing = _existing_users(cognito, pool_id)
    for m in team:
        user, roles = m.username, sorted(r.value for r in m.roles)
        current: set[str] = set()
        if user not in existing:
            plan.add(
                f"create {user} ({m.name}; {', '.join(roles)})",
                lambda m=m: _create(cognito, pool_id, m, password()),
                needs_password=True,
            )
        else:
            account = existing[user]
            current = _groups_of(cognito, pool_id, user)
            if not account.enabled:
                plan.add(
                    f"enable {user}",
                    lambda u=user: cognito.admin_enable_user(UserPoolId=pool_id, Username=u),
                )
            if reset_passwords or account.status in STUCK:
                why = "" if reset_passwords else " (it has no usable one)"
                plan.add(
                    f"set the password of {user}{why}",
                    lambda u=user: _set_password(cognito, pool_id, u, password()),
                    needs_password=True,
                )
            if account.attributes.get("name") != m.name:
                plan.add(
                    f"rename {user} to {m.name}",
                    lambda m=m: cognito.admin_update_user_attributes(
                        UserPoolId=pool_id,
                        Username=m.username,
                        UserAttributes=[{"Name": "name", "Value": m.name}],
                    ),
                )
        for group in sorted(set(roles) - current):
            plan.add(
                f"add {user} to {group}",
                lambda u=user, g=group: cognito.admin_add_user_to_group(
                    UserPoolId=pool_id, Username=u, GroupName=g
                ),
            )
        for group in sorted((current & ROLE_GROUPS) - set(roles)):
            plan.add(
                f"remove {user} from {group}",
                lambda u=user, g=group: cognito.admin_remove_user_from_group(
                    UserPoolId=pool_id, Username=u, GroupName=g
                ),
            )
        wanted = profile_item(user, m.name, roles, m.crew_id, m.desk)
        if get_profile(table, user) != wanted:
            plan.add(f"write the profile of {user}", lambda w=wanted: put_profile(table, w))

    members = {m.username for m in team}
    for user in sorted(set(existing) - members):
        if prune:
            plan.add(
                f"delete {user} (not in the team)",
                lambda u=user: _delete(cognito, pool_id, table, u),
            )
            continue
        # Not in the file: no roles and no profile, so the account can do nothing.
        for group in sorted(_groups_of(cognito, pool_id, user) & ROLE_GROUPS):
            plan.add(
                f"remove {user} from {group} (not in the team)",
                lambda u=user, g=group: cognito.admin_remove_user_from_group(
                    UserPoolId=pool_id, Username=u, GroupName=g
                ),
            )
        if get_profile(table, user) is not None:
            plan.add(
                f"delete the profile of {user} (not in the team)",
                lambda u=user: delete_profile(table, u),
            )
    for profile in list_profiles(table):
        user = profile.get("username", "")
        if user not in members and user not in existing:
            plan.add(
                f"delete the profile of {user} (no such account)",
                lambda u=user: delete_profile(table, u),
            )
    return plan


def _create(cognito: Any, pool_id: str, m: TeamMember, password: str) -> None:
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=m.username,
        UserAttributes=[{"Name": "name", "Value": m.name}],
        MessageAction="SUPPRESS",
    )
    _set_password(cognito, pool_id, m.username, password)


def _set_password(cognito: Any, pool_id: str, username: str, password: str) -> None:
    cognito.admin_set_user_password(
        UserPoolId=pool_id, Username=username, Password=password, Permanent=True
    )


def _delete(cognito: Any, pool_id: str, table: Any, username: str) -> None:
    cognito.admin_delete_user(UserPoolId=pool_id, Username=username)
    delete_profile(table, username)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument("--reset-passwords", action="store_true")
    parser.add_argument("--prune", action="store_true", help="delete accounts not in the file")
    args = parser.parse_args(argv)

    from crewops.config import Settings, load_env_file
    from crewops.ingestion.seed import load_seed

    team = load_team()
    if errors := validate_team(team, load_seed()):
        print("The team file has problems:", *errors, sep="\n  ", file=sys.stderr)
        return 1

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.store.ops_store import table_for

    settings = Settings.from_env()
    cached: list[str] = []

    def password() -> str:
        if not cached:
            value = os.environ.get("CREWOPS_TEAM_PASSWORD") or getpass.getpass(
                "Password for the accounts: "
            )
            if problems := password_problems(value):
                raise ProvisionError("The password needs " + "; ".join(problems) + ".")
            cached.append(value)
        return cached[0]

    try:
        session = boto3.Session(region_name=settings.aws_region)
        cognito = session.client("cognito-idp")
        output = stack_output(
            session.client("cloudformation"), f"CrewOps-{settings.stage}-Auth", "UserPoolId"
        )
        pool_id = find_user_pool(cognito, settings.user_pool_name, output)
        if pool_id is None:
            print(
                f"No user pool {settings.user_pool_name}: deploy the auth stack first.",
                file=sys.stderr,
            )
            return 1
        plan = plan_team(
            cognito,
            pool_id,
            table_for(settings),
            team,
            password,
            reset_passwords=args.reset_passwords,
            prune=args.prune,
        )
        if not plan.steps:
            print("Everything already matches the team file.")
            return 0
        for action in plan.actions:
            print(("  " if args.apply else "  would ") + action)
        if not args.apply:
            print("Run again with --apply to make these changes.")
            return 0
        if plan.needs_password:
            password()  # asked and checked once, before the first change
        for step in plan.steps:
            step()
        print(f"Done: {len(plan.steps)} changes.")
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    except ProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
