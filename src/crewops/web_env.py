"""Write the web app's settings from the deployed stacks' outputs.

    uv run python -m crewops.web_env             # writes web/.env.local
    uv run python -m crewops.web_env --speaker   # ...and the desk speaker's device sign-in

The web app needs the Cognito app client, the MCP runtime and the live-events API. They
are read from the ``CrewOps-<stage>-Auth``, ``-Mcp`` and ``-Notify`` stack outputs, so
nothing is copied by hand. The file holds no secret (the app client has none) and is
ignored by git. The AWS profile named in ``.env`` is passed on for the spoken answers
(Amazon Polly), which the app's server calls with your credentials when run locally.

``--speaker`` gives the desk speaker's device account (``ops.speaker``, the speaker role) a
new random password of its own, set on the account here (never the team's), and a random
pairing code that screens of a hosted app enter once; both go into the file so the app's
server can sign in as the device. Those two are secrets: the file stays ignored by git.
Running the command again without ``--speaker`` keeps them; with it, they are replaced.

Desktop alerts: once the push keys exist (``python -m crewops.notify.webpush keys``), the
public one, which browsers need to subscribe, is written too. It is not a secret.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
SPEAKER_KEYS = (
    "CREWOPS_SPEAKER_USERNAME",
    "CREWOPS_SPEAKER_PASSWORD",
    "CREWOPS_SPEAKER_PAIRING_CODE",
)


def kept_speaker_lines(path: Path) -> list[str]:
    """So a re-run without --speaker doesn't wipe the speaker sign-in."""
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.split("=", 1)[0] in SPEAKER_KEYS]


SAFE = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
"""Survives unquoted in .env (no "#", "$" or quotes). Lookalikes like 0/O and 1/l/I are out."""


def new_secret(length: int, symbol: bool) -> str:
    """``symbol=True`` makes it pass the pool's password policy (upper, lower, digit,
    symbol). Retries until it does."""
    import secrets

    while True:
        value = "".join(secrets.choice(SAFE) for _ in range(length))
        if symbol:
            value = f"{value[:8]}-{value[8:]}"  # "-" is a symbol Cognito counts
        if not symbol or (
            any(c.isupper() for c in value)
            and any(c.islower() for c in value)
            and any(c.isdigit() for c in value)
        ):
            return value


def speaker_lines(password: str, pairing_code: str) -> list[str]:
    for name, value in (("password", password), ("pairing code", pairing_code)):
        if not value or any(c not in SAFE + "-" for c in value):
            raise ValueError(f"the speaker's {name} must be letters, digits and '-' only")
    return [
        "CREWOPS_SPEAKER_USERNAME=ops.speaker",
        f"CREWOPS_SPEAKER_PASSWORD={password}",
        f"CREWOPS_SPEAKER_PAIRING_CODE={pairing_code}",
    ]


def lines(region: str, auth: dict, mcp: dict, notify: dict, profile: str | None) -> list[str]:
    out = [
        "# Written by `python -m crewops.web_env` from the stack outputs. Not committed.",
        f"CREWOPS_REGION={region}",
        f"CREWOPS_USER_POOL_CLIENT_ID={auth['UserPoolClientId']}",
        f"CREWOPS_MCP_RUNTIME_ARN={mcp['McpRuntimeArn']}",
        f"CREWOPS_EVENTS_HTTP_DOMAIN={notify['EventsHttpDomain']}",
        f"CREWOPS_EVENTS_REALTIME_DOMAIN={notify['EventsRealtimeDomain']}",
    ]
    if profile:
        out.append(f"AWS_PROFILE={profile}")
    return out


def push_key_line(ssm, prefix: str) -> list[str]:
    """Empty if the VAPID keys haven't been created, which leaves desktop alerts off."""
    from botocore.exceptions import ClientError

    try:
        value = ssm.get_parameter(Name=f"{prefix}/vapid-public")["Parameter"]["Value"]
    except ClientError:
        return []
    return (
        [f"CREWOPS_PUSH_PUBLIC_KEY={value}"]
        if value.replace("-", "").replace("_", "").isalnum()
        else []
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=WEB_DIR / ".env.local")
    parser.add_argument(
        "--speaker", action="store_true", help="also write the desk speaker's device sign-in"
    )
    args = parser.parse_args(argv)

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.config import Settings, load_env_file
    from crewops.mcp.client import outputs

    load_env_file()
    profile = os.environ.get("AWS_PROFILE")
    if not profile or profile == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1
    settings = Settings.from_env()
    try:
        cloudformation = boto3.Session(region_name=settings.aws_region).client("cloudformation")
        stack = f"CrewOps-{settings.stage}"
        found = [outputs(cloudformation, f"{stack}-{part}") for part in ("Auth", "Mcp", "Notify")]
        body = lines(settings.aws_region, *found, profile)
        ssm = boto3.Session(region_name=settings.aws_region).client("ssm")
        body += push_key_line(ssm, settings.push_parameter_prefix)
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"A stack output is missing: {exc}. Deploy the stacks first.", file=sys.stderr)
        return 1
    if args.speaker:
        # Fresh password just for the device account, so the team's shared password never
        # ends up in .env.local.
        password, code = new_secret(24, symbol=True), new_secret(10, symbol=False)
        try:
            boto3.Session(region_name=settings.aws_region).client(
                "cognito-idp"
            ).admin_set_user_password(
                UserPoolId=found[0]["UserPoolId"],
                Username="ops.speaker",
                Password=password,
                Permanent=True,
            )
        except (ClientError, BotoCoreError) as exc:
            print(
                f"Could not set the speaker's password (provision the team first): {exc}",
                file=sys.stderr,
            )
            return 1
        body += speaker_lines(password, code)
        print(f"Speaker pairing code, for screens of a hosted app: {code}")
    else:
        body += kept_speaker_lines(args.out)
    args.out.write_text("\n".join(body) + "\n")
    os.chmod(args.out, 0o600)  # it can hold the speaker device's password
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
