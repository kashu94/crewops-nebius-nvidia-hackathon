"""Call the deployed MCP server as a member of the team, the way any MCP host would.

    uv run python -m crewops.mcp.client --as rohit.malhotra ask "Who covers C-1042 on 13 Oct?"
    uv run python -m crewops.mcp.client --as rohit.malhotra --conversation c1 ask "Captain"
    uv run python -m crewops.mcp.client --as rohit.malhotra my_queue
    uv run python -m crewops.mcp.client --as meera.bhatt approve_plan \
        '{"disruption_id": "r-...", "plan_id": "pl-..."}'
    uv run python -m crewops.mcp.client --as rohit.malhotra tools          # list the server's tools

Signs in to the team's Cognito pool with the member's username and password (from
``CREWOPS_TEAM_PASSWORD`` or asked for), then calls the AgentCore runtime with the access
token, over MCP. The pool, client and runtime are read from the deployed stacks' outputs.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any
from urllib.parse import quote


def invocation_url(region: str, runtime_arn: str) -> str:
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(runtime_arn, safe='')}/invocations?qualifier=DEFAULT"
    )


def outputs(cloudformation: Any, stack: str) -> dict[str, str]:
    found = cloudformation.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in found}


def sign_in(cognito: Any, client_id: str, username: str, password: str) -> str:
    result = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    if "AuthenticationResult" not in result:
        raise SystemExit(f"Sign-in needs another step ({result.get('ChallengeName')}).")
    return result["AuthenticationResult"]["AccessToken"]


async def call(url: str, token: str, tool: str | None, arguments: dict[str, Any]) -> Any:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    http = create_mcp_http_client(
        headers={"authorization": f"Bearer {token}"}, timeout=httpx.Timeout(120, read=300)
    )
    transport = streamable_http_client(url, http_client=http, terminate_on_close=False)
    async with http, transport as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        if tool is None:
            listed = await session.list_tools()
            return [t.name for t in listed.tools]
        result = await session.call_tool(tool, arguments)
        if result.isError:
            return {"error": " ".join(getattr(c, "text", "") for c in result.content)}
        if result.structuredContent is not None:
            return result.structuredContent
        texts = [getattr(c, "text", str(c)) for c in result.content]
        return _parsed(texts, as_list=tool in LIST_TOOLS)


LIST_TOOLS = frozenset(
    {
        "my_queue",
        "alert_addresses",
        "list_crew_tools",
        "rule_thresholds",
        "activity_log",
        "disruptions",
        "my_reports",
    }
)
"""The server's tools that return a list. Their results arrive as one text item per
element, so a one-element list would otherwise look like a single object."""


def _parsed(texts: list[str], as_list: bool = False) -> Any:
    """Decode each text item as JSON where it parses."""
    values = []
    for text in texts:
        try:
            values.append(json.loads(text))
        except ValueError:
            values.append(text)
    return values if as_list or len(values) != 1 else values[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as", dest="user", required=True, help="team member's username")
    parser.add_argument(
        "--conversation", default="", help="for ask: one id per conversation, for follow-ups"
    )
    parser.add_argument("tool", help="a tool name, 'ask', or 'tools' to list them")
    parser.add_argument("argument", nargs="?", default=None, help="question, or JSON arguments")
    args = parser.parse_args(argv)

    import anyio
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    from crewops.config import Settings, load_env_file

    load_env_file()
    if os.environ.get("AWS_PROFILE", "default") == "default":
        print("Set AWS_PROFILE (for example in .env) to a named profile.", file=sys.stderr)
        return 1
    settings = Settings.from_env()

    if args.tool == "tools":
        tool, arguments = None, {}
    elif args.tool == "ask":
        if not args.argument:
            parser.error("ask needs a question")
        tool, arguments = "ask", {"question": args.argument}
        if args.conversation:
            arguments["conversation_id"] = args.conversation
    else:
        tool = args.tool
        try:
            arguments = json.loads(args.argument) if args.argument else {}
        except json.JSONDecodeError as exc:
            parser.error(f"arguments must be JSON: {exc}")

    try:
        session = boto3.Session(region_name=settings.aws_region)
        cloudformation = session.client("cloudformation")
        auth = outputs(cloudformation, f"CrewOps-{settings.stage}-Auth")
        runtime_arn = outputs(cloudformation, f"CrewOps-{settings.stage}-Mcp")["McpRuntimeArn"]
        password = os.environ.get("CREWOPS_TEAM_PASSWORD") or getpass.getpass(
            f"Password for {args.user}: "
        )
        token = sign_in(
            session.client("cognito-idp"), auth["UserPoolClientId"], args.user, password
        )
    except (ClientError, BotoCoreError) as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1

    result = anyio.run(
        call, invocation_url(settings.aws_region, runtime_arn), token, tool, arguments
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if isinstance(result, dict) and "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
