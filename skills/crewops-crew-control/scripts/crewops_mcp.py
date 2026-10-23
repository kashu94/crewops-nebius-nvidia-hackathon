#!/usr/bin/env python3
"""Call the CrewOps MCP server from a shell, as one signed-in person.

For agents (or people) without an MCP client configured. Signs in to CrewOps' Cognito
user pool, then speaks MCP over Streamable HTTP to the AgentCore runtime:

    python scripts/crewops_mcp.py tools
    python scripts/crewops_mcp.py url      # the server URL, for an MCP client configuration
    python scripts/crewops_mcp.py token    # a 1-hour access token for that configuration
    python scripts/crewops_mcp.py call whoami
    python scripts/crewops_mcp.py call my_queue
    python scripts/crewops_mcp.py call ask \
        '{"question": "Who covers C-1042 tomorrow?", "conversation_id": "c1"}'
    python scripts/crewops_mcp.py call crew_tool '{"name": "check_legality",
        "arguments": {"crew_id": "C-3310", "pairing_id": "P-2391"}}'

Settings come from the environment (the web app's `.env.local` has the first three):

    CREWOPS_REGION               e.g. ap-southeast-2
    CREWOPS_USER_POOL_CLIENT_ID  the web app client (no secret)
    CREWOPS_MCP_RUNTIME_ARN      the AgentCore runtime (or CREWOPS_MCP_URL for the full URL)
    CREWOPS_USERNAME             who to act as, e.g. rohit.malhotra
    CREWOPS_PASSWORD             optional; asked for when not set, never printed
    CREWOPS_ACCESS_TOKEN         optional; skips signing in

Needs Python 3.12+, boto3 and mcp (`pip install boto3 mcp`). Prints JSON.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
from typing import Any
from urllib.parse import quote


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Set {name} (see the top of this script).")
    return value


def server_url() -> str:
    if url := os.environ.get("CREWOPS_MCP_URL", "").strip():
        return url
    arn, region = _env("CREWOPS_MCP_RUNTIME_ARN"), _env("CREWOPS_REGION")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )


def access_token() -> str:
    """A CrewOps access token: the given one, or a fresh sign-in."""
    if token := os.environ.get("CREWOPS_ACCESS_TOKEN", "").strip():
        return token
    import boto3

    username = _env("CREWOPS_USERNAME")
    password = os.environ.get("CREWOPS_PASSWORD") or getpass.getpass(f"Password for {username}: ")
    cognito = boto3.client("cognito-idp", region_name=_env("CREWOPS_REGION"))
    result = cognito.initiate_auth(
        ClientId=_env("CREWOPS_USER_POOL_CLIENT_ID"),
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    if "AuthenticationResult" not in result:
        raise SystemExit(f"Sign-in needs another step ({result.get('ChallengeName')}).")
    return result["AuthenticationResult"]["AccessToken"]


async def run(tool: str | None, arguments: dict[str, Any]) -> Any:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    http = create_mcp_http_client(
        headers={"authorization": f"Bearer {access_token()}"},
        timeout=httpx.Timeout(120, read=300),
    )
    transport = streamable_http_client(server_url(), http_client=http, terminate_on_close=False)
    async with http, transport as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        if tool is None:
            return [
                {"name": t.name, "description": " ".join((t.description or "").split())}
                for t in (await session.list_tools()).tools
            ]
        result = await session.call_tool(tool, arguments)
        if result.isError:
            return {"error": " ".join(getattr(c, "text", "") for c in result.content)}
        if result.structuredContent is not None:
            return result.structuredContent
        values = []
        for c in result.content:
            text = getattr(c, "text", str(c))
            try:
                values.append(json.loads(text))
            except ValueError:
                values.append(text)
        return values[0] if len(values) == 1 else values


def _failure(exc: BaseException) -> str:
    """``CODE: message`` for a failure outside a tool result, so the agent sees the same shape
    as a refusal (references/errors.md) instead of a stack trace."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # from the HTTP transport
        exc = exc.exceptions[0]
    if isinstance(exc, json.JSONDecodeError):
        return f"INVALID: the arguments are not valid JSON ({exc.msg})"
    response = getattr(exc, "response", None)
    if isinstance(response, dict):  # botocore ClientError from the Cognito sign-in
        code = response.get("Error", {}).get("Code", "")
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            return "UNAUTHORIZED: wrong username or password"
        return f"UNAVAILABLE: sign-in failed ({code or type(exc).__name__})"
    status = getattr(response, "status_code", None)  # httpx.HTTPStatusError from the server
    if status in (401, 403):
        return "UNAUTHORIZED: the access token was refused (expired?); sign in again"
    if status is not None:
        return f"UNAVAILABLE: the server answered HTTP {status}"
    return f"UNAVAILABLE: {type(exc).__name__}: {exc}"


def main(argv: list[str]) -> int:
    try:
        return _main(argv)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        print(json.dumps({"error": _failure(exc)}, indent=2, ensure_ascii=False))
        return 1


def _main(argv: list[str]) -> int:
    if argv[:1] == ["url"]:
        print(server_url())
        return 0
    if argv[:1] == ["token"]:
        # For an MCP client's Authorization header. It is the person's own sign-in: keep it
        # out of chats, files and screenshots; it expires within the hour.
        print(access_token())
        return 0
    if not argv or argv[0] not in ("tools", "call") or (argv[0] == "call" and len(argv) < 2):
        print(__doc__, file=sys.stderr)
        return 2
    tool = None if argv[0] == "tools" else argv[1]
    arguments = json.loads(argv[2]) if argv[0] == "call" and len(argv) > 2 else {}
    if not isinstance(arguments, dict):
        raise SystemExit("Arguments are one JSON object.")
    out = asyncio.run(run(tool, arguments))
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 1 if isinstance(out, dict) and "error" in out else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
