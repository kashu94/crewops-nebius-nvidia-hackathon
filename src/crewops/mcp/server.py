"""The MCP server, as AgentCore Runtime hosts it: stateless streamable HTTP on
``0.0.0.0:8000/mcp``.

Every tool call is made as the signed-in user. AgentCore's authorizer admits only access
tokens from the team's Cognito pool and app client and passes the ``Authorization`` header
through; the server verifies that token again and builds the caller from the token's groups
and the user's profile. A request without a valid token does nothing.

Run locally (``python -m crewops.mcp.server``) it serves the same tools on the same port,
on 127.0.0.1 only, still requiring tokens from the configured pool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from crewops.auth.identity import AuthError, caller_from_claims
from crewops.auth.tokens import TokenVerifier, bearer
from crewops.mcp.api import ApiError, CrewOpsApi
from crewops.tools import Caller

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
CrewOps answers airline crew-control questions from the live roster, with every figure
checked against the legality engine. Use `ask` for questions in plain words ("who covers
C-1042 tomorrow?"), with one `conversation_id` per conversation. Use `my_queue` for what is
waiting for the signed-in person. Plans are submitted by a controller and approved by a
different person; approving commits the roster.
The tools available depend on the signed-in user's role."""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
COMMIT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
OUTWARD = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
"""Reaches outside CrewOps (SNS emails the address)."""


class Gate:
    """Turns a request into a ``Caller``, or refuses it."""

    def __init__(self, verifier: TokenVerifier, profiles: Any) -> None:
        self.verifier = verifier
        self.profiles = profiles

    def caller(self, ctx: Context) -> Caller:
        request = getattr(ctx.request_context, "request", None)
        header = request.headers.get("authorization") if request is not None else None
        try:
            claims = self.verifier.claims(bearer(header))
            return caller_from_claims(
                claims,
                self.profiles,
                issuer=self.verifier.issuer,
                client_id=self.verifier.client_id,
            )
        except AuthError as exc:
            raise ToolError(f"UNAUTHORIZED: {exc}") from None


def build_server(
    api: CrewOpsApi, gate: Gate, *, host: str = "127.0.0.1", port: int = 8000
) -> FastMCP:
    mcp = FastMCP(
        "CrewOps",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        stateless_http=True,
    )

    async def run(ctx: Context, call: Any) -> Any:
        # Token check and call both block (key fetch, DynamoDB, Bedrock), so they go to a
        # worker thread. The event loop has to stay free for health pings.

        def work() -> Any:
            try:
                return call(gate.caller(ctx))
            except ToolError:
                raise
            except ApiError as exc:
                raise ToolError(f"{exc.code}: {exc.message}") from None
            except Exception:
                log.exception("MCP tool call failed")
                raise ToolError("INTERNAL: something went wrong; it has been logged") from None

        return await anyio.to_thread.run_sync(work)

    @mcp.tool(annotations=READ)
    async def ask(question: str, ctx: Context, conversation_id: str = "") -> dict:
        """Ask a crew-control question in plain words. The answer is built from the live
        roster and the legality engine, and every fact in it is checked before it is given.
        `awaiting` says when the answer is a question back (which id, which date). Pass the
        same `conversation_id` (letters, digits, - and _) on every question of one
        conversation, so a short reply ("Captain") or "is she legal for P-2291?" is read
        with the previous question."""
        return await run(ctx, lambda c: api.ask(c, question, conversation_id))

    @mcp.tool(annotations=READ)
    async def whoami(ctx: Context) -> dict:
        """The signed-in person as the server sees them: username, roles, and their crew id
        or desk where they have one."""
        return await run(ctx, lambda c: api.whoami(c))

    @mcp.tool(annotations=READ)
    async def my_queue(ctx: Context) -> list:
        """What is waiting for the signed-in person: plans to decide (approver), disruptions
        on their desk ready for a plan, and their plans sent back (controller)."""
        return await run(ctx, lambda c: api.queue(c))

    @mcp.tool(annotations=READ)
    async def speaker_feed(ctx: Context, scope: str = "room", limit: int = 20) -> dict:
        """Speaker devices only: the spoken alerts for a desk speaker (scope desk-1, desk-2,
        desk-3), the approvals corner ("approvals") or the whole ops room ("room"): the
        latest event of each disruption, newest first, each with an id to remember it by."""
        return await run(ctx, lambda c: api.speaker_feed(c, scope, limit))

    @mcp.tool(annotations=READ)
    async def ops_board(ctx: Context) -> dict:
        """The ops-room wall display: every open disruption (most serious and escalated
        first) with its desk, waiting time and recommended cover; each desk's load; and
        today's flights, crew out and reserves on call. Speaker devices and crew control."""
        return await run(ctx, lambda c: api.ops_board(c))

    @mcp.tool(annotations=WRITE)
    async def welcome_back(ctx: Context) -> dict:
        """Controllers and approvers: what came to your desk (or to approvals) since you last
        signed in, with a spoken summary. Records this sign-in."""
        return await run(ctx, lambda c: api.welcome_back(c))

    @mcp.tool(annotations=WRITE)
    async def push_subscribe(ctx: Context, endpoint: str, p256dh: str, auth: str) -> dict:
        """Controllers and approvers: turn on desktop alerts in this browser with the push
        subscription its service worker made (endpoint and keys)."""
        return await run(ctx, lambda c: api.push_subscribe(c, endpoint, p256dh, auth))

    @mcp.tool(annotations=WRITE)
    async def push_unsubscribe(ctx: Context, endpoint: str) -> dict:
        """Turn desktop alerts off for one browser."""
        return await run(ctx, lambda c: api.push_unsubscribe(c, endpoint))

    @mcp.tool(annotations=WRITE)
    async def still_here(ctx: Context) -> dict:
        """Controllers and approvers: record that your console is open now, so the next
        "welcome back" covers only what came after."""
        return await run(ctx, lambda c: api.still_here(c))

    @mcp.tool(annotations=READ)
    async def disruptions(ctx: Context, limit: int = 40) -> list:
        """Recent disruptions in every status, newest report first, with their analysis
        summary: the desk's chronological feed."""
        return await run(ctx, lambda c: api.disruptions(c, limit))

    @mcp.tool(annotations=READ)
    async def show_disruption(disruption_id: str, ctx: Context) -> dict:
        """A disruption's status, analysis summary, plans and audit trail."""
        return await run(ctx, lambda c: api.show(c, disruption_id))

    @mcp.tool(annotations=WRITE)
    async def report_disruption(
        report_type: str, details: dict, ctx: Context, narrative: str = ""
    ) -> dict:
        """Report a disruption (today to two months ahead), or, as an admin, book planned
        leave (planned_leave: crew_id, from_date, to_date, reason annual_leave, medical,
        training or personal; up to the end of the rostered period). report_type is one of
        crew_unavailable,
        multiple_crew_unavailable, certification_lapse, flight_delay, aircraft_technical,
        station_disruption, gate_closure, gate_change. details depend on the type, e.g.
        crew_unavailable: {"crew_id": "C-1042", "from_date": "2026-10-13", "to_date":
        "2026-10-14", "reason": "sick"}; multiple_crew_unavailable: the same with "crew_ids"
        (2 to 12) instead of "crew_id"; gate_closure: {"gate": "BLR-G2", "from_utc",
        "to_utc", "reason"}. Crew may report only their own unavailability. It is analysed
        automatically."""
        return await run(ctx, lambda c: api.report(c, report_type, details, narrative))

    @mcp.tool(annotations=WRITE)
    async def submit_plan(
        disruption_id: str,
        ctx: Context,
        choices: dict | None = None,
        note: str = "",
        accept_uncovered: bool = False,
    ) -> dict:
        """Controller: submit cover for a disruption on your desk. By default the analysis's
        recommendation; choices overrides a seat, e.g. {"P-2291/Captain": "C-1526"}. A
        different approver must approve it."""
        return await run(
            ctx, lambda c: api.submit(c, disruption_id, choices, note, accept_uncovered)
        )

    @mcp.tool(annotations=COMMIT)
    async def approve_plan(disruption_id: str, plan_id: str, ctx: Context) -> dict:
        """Approver: approve a plan you did not submit. Legality is checked again, and the
        roster is changed in one step; a plan whose roster changed meanwhile comes back
        STALE instead."""
        return await run(ctx, lambda c: api.approve(c, disruption_id, plan_id))

    @mcp.tool(annotations=WRITE)
    async def reject_plan(disruption_id: str, plan_id: str, reason: str, ctx: Context) -> dict:
        """Approver: send a plan back to its controller, saying why."""
        return await run(ctx, lambda c: api.reject(c, disruption_id, plan_id, reason))

    @mcp.tool(annotations=WRITE)
    async def withdraw_plan(
        disruption_id: str, plan_id: str, ctx: Context, reason: str = ""
    ) -> dict:
        """Controller: withdraw a plan you submitted that is still awaiting a decision."""
        return await run(ctx, lambda c: api.withdraw(c, disruption_id, plan_id, reason))

    @mcp.tool(annotations=WRITE)
    async def close_disruption(disruption_id: str, reason: str, ctx: Context) -> dict:
        """Controller: resolve a disruption that has no coverable seat, saying what was done
        (for example, the flights were cancelled), or one handled outside crew control (a
        gate closure re-gated, a delay every crew member absorbs). It then waits for an
        approver to sign it off."""
        return await run(ctx, lambda c: api.close(c, disruption_id, reason))

    @mcp.tool(annotations=WRITE)
    async def confirm_resolution(disruption_id: str, ctx: Context) -> dict:
        """Approver: sign off a disruption a controller resolved with a remark (it is then
        closed). Four eyes: never one you sent yourself."""
        return await run(ctx, lambda c: api.confirm_resolution(c, disruption_id))

    @mcp.tool(annotations=WRITE)
    async def return_resolution(disruption_id: str, reason: str, ctx: Context) -> dict:
        """Approver: send a resolved disruption back to its desk, saying why."""
        return await run(ctx, lambda c: api.return_resolution(c, disruption_id, reason))

    @mcp.tool(annotations=WRITE)
    async def suggest_cover(
        disruption_id: str, seat: str, crew_id: str, ctx: Context, note: str = ""
    ) -> dict:
        """Controller: suggest cover for a seat on another desk's disruption (seat as
        "P-2291/Captain/C-1042"). The owning desk sees it and may adopt it; it plans nothing."""
        return await run(ctx, lambda c: api.suggest(c, disruption_id, seat, crew_id, note))

    @mcp.tool(annotations=READ)
    async def my_roster(ctx: Context) -> dict:
        """Crew: your own pairings, duty days and duty clock."""
        return await run(ctx, lambda c: api.my_roster(c))

    @mcp.tool(annotations=READ)
    async def operations_overview(ctx: Context, start: str = "") -> dict:
        """A week of the operation at a glance, from ``start`` (YYYY-MM-DD; today if
        empty): every flight with its airline, aircraft and crew, who is reported
        unavailable when, and the open disruptions on each flight. For the reporting desk
        and crew control."""
        return await run(ctx, lambda c: api.operations(c, start))

    @mcp.tool(annotations=READ)
    async def my_reports(ctx: Context, scope: str = "mine", limit: int = 30) -> list:
        """Reports and how each stands, newest first: still being checked, refused (and
        why), or the disruption it became. scope "mine" (what you filed), "crew" (what
        crew filed about themselves, for the reporting desk and crew control) or "leave"
        (planned leave booked by admins)."""
        return await run(ctx, lambda c: api.my_reports(c, scope, limit))

    @mcp.tool(annotations=READ)
    async def report_reference(ctx: Context) -> dict:
        """Reporters, controllers and approvers: the crew, flights, aircraft, gates and
        stations a report can name."""
        return await run(ctx, lambda c: api.reference(c))

    @mcp.tool(annotations=READ)
    async def rule_thresholds(ctx: Context) -> list:
        """The legality rules' adjustable thresholds: current value, allowed range (never
        looser than regulation) and the rule's text."""
        return await run(ctx, lambda c: api.thresholds(c))

    @mcp.tool(annotations=COMMIT)
    async def set_rule_threshold(
        rule_id: str, param: str, value: float, expected: float, reason: str, ctx: Context
    ) -> dict:
        """Admin: change one threshold from the value you saw (`expected`) to `value`, saying
        why. Only stricter values, or the regulatory one, are accepted; the change is
        audited and the engine uses it within a minute."""
        return await run(
            ctx, lambda c: api.set_threshold(c, rule_id, param, value, expected, reason)
        )

    @mcp.tool(annotations=READ)
    async def activity_log(ctx: Context, limit: int = 50) -> list:
        """Admin: the latest audit events across the system, newest first."""
        return await run(ctx, lambda c: api.activity(c, limit))

    @mcp.tool(annotations=COMMIT)
    async def revoke_report(disruption_id: str, reason: str, ctx: Context) -> dict:
        """Admin: withdraw a report filed in error (a reporter's report, a crew member's own
        unavailability or planned leave), saying why. Only while nothing has been done about
        it: reported, being planned, or a plan waiting for approval (that plan is withdrawn
        too). Once cover is approved, crew control resolves it instead. The crew it named
        count as available again."""
        return await run(ctx, lambda c: api.revoke_report(c, disruption_id, reason))

    @mcp.tool(annotations=READ)
    async def team_accounts(ctx: Context) -> list:
        """Admin: the team's sign-ins, roles, and whether you may reset each password."""
        return await run(ctx, lambda c: api.team_accounts(c))

    @mcp.tool(annotations=COMMIT)
    async def reset_password(username: str, ctx: Context) -> dict:
        """Admin: give a team member a new random password, returned once. An admin resets
        crew, reporters, controllers, approvers and auditors; only a super admin resets an
        admin. Nobody resets their own here, nor the speaker device's."""
        return await run(ctx, lambda c: api.reset_password(c, username))

    @mcp.tool(annotations=READ)
    async def alert_addresses(ctx: Context) -> list:
        """Admin: the email addresses receiving alerts, the role each receives, and whether
        it has been confirmed."""
        return await run(ctx, lambda c: api.alert_addresses(c))

    @mcp.tool(annotations=OUTWARD)
    async def add_alert_address(email: str, role: str, ctx: Context) -> dict:
        """Admin: send one role's alerts (controller: options ready or a plan sent back;
        approver: a plan waiting), or one operational team's (gate_control, station_ops,
        flight_info, ops_monitoring, maintenance_control, crew_records, customer_care: the
        disruptions that team must act on), to an email address. AWS asks the address to
        confirm first. Limited to a few new addresses a day."""
        return await run(ctx, lambda c: api.add_alert_address(c, email, role))

    @mcp.tool(annotations=COMMIT)
    async def remove_alert_address(subscription_id: str, ctx: Context) -> dict:
        """Admin: stop sending alerts to an address (its id from `alert_addresses`). An
        address never confirmed lapses after three days but stays listed, and counts
        toward the limit, until it is removed here."""
        return await run(ctx, lambda c: api.remove_alert_address(c, subscription_id))

    @mcp.tool(annotations=READ)
    async def list_crew_tools(ctx: Context) -> list:
        """The deterministic crew-operations tools the signed-in person may call directly
        with `crew_tool`, with their input schemas."""
        return await run(ctx, lambda c: api.list_tools(c))

    @mcp.tool(annotations=READ)
    async def crew_tool(name: str, ctx: Context, arguments: dict | None = None) -> dict:
        """Call one deterministic tool by name (see `list_crew_tools`) with JSON arguments,
        e.g. name="find_options", arguments={"crew_id": "C-1042"}. Structured data, no
        model involved."""
        return await run(ctx, lambda c: api.run_tool(c, name, arguments))

    return mcp


def from_environment(host: str = "127.0.0.1") -> FastMCP:
    """The deployed server: settings from the environment the stack sets."""
    import boto3

    from crewops.agent.memory import ConversationMemory, memory_client
    from crewops.agent.service import build
    from crewops.auth.accounts import Accounts
    from crewops.config import Settings
    from crewops.notify.subscriptions import AlertSubscriptions
    from crewops.store.ops_store import table_for
    from crewops.store.users import get_profile

    settings = Settings.from_env()
    if not (settings.issuer and settings.user_pool_client_id and settings.ingest_queue_url):
        raise SystemExit(
            "CREWOPS_USER_POOL_ID, CREWOPS_USER_POOL_CLIENT_ID and CREWOPS_INGEST_QUEUE_URL "
            "must be set"
        )
    table = table_for(settings)
    session = boto3.Session(region_name=settings.aws_region)
    sqs = session.client("sqs")

    def send(report: dict) -> None:
        sqs.send_message(QueueUrl=settings.ingest_queue_url, MessageBody=json.dumps(report))

    memory = (
        ConversationMemory(memory_client(settings.aws_region), settings.memory_id)
        if settings.memory_id
        else None
    )
    alerts = (
        AlertSubscriptions(session.client("sns"), settings.alerts_topic_arn, table)
        if settings.alerts_topic_arn
        else None
    )
    accounts = Accounts(session.client("cognito-idp"), settings.user_pool_id or "")
    api = CrewOpsApi(
        table,
        lambda: build(settings, table),
        send,
        memory=memory,
        alerts=alerts,
        accounts=accounts,
    )
    gate = Gate(
        TokenVerifier(settings.issuer, settings.user_pool_client_id),
        lambda username: get_profile(table, username),
    )
    return build_server(api, gate, host=host)


def main(host: str = "127.0.0.1") -> None:
    logging.basicConfig(level=logging.INFO)
    from_environment(host).run(transport="streamable-http")


if __name__ == "__main__":
    main()
