"""The MCP server: tokens, the caller-scoped API, and the real MCP protocol in-process."""

import json
import time
from datetime import UTC, datetime

import anyio
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_plans import disruption

from crewops.agent.pipeline import Pipeline
from crewops.agent.service import example_questions
from crewops.auth.identity import AuthError
from crewops.auth.tokens import TokenVerifier, bearer
from crewops.domain import Role
from crewops.engine import build_world
from crewops.ingestion.seed import load_seed
from crewops.mcp.api import ApiError, CrewOpsApi
from crewops.mcp.server import Gate, build_server
from crewops.store.ops_store import OpsStore
from crewops.store.users import get_profile, profile_item, put_profile
from crewops.tools import Caller, CrewOpsTools

ISSUER = "https://cognito-idp.ap-southeast-2.amazonaws.com/ap-southeast-2_TEST"
CLIENT = "client-1"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ROHIT = Caller("rohit", frozenset({Role.CONTROLLER}), desk="desk-2")
MEERA = Caller("meera", frozenset({Role.APPROVER}))
CREW = Caller("crew1042", frozenset({Role.CREW}), crew_id="C-1042")


class Keys:
    """Stands in for the pool's published keys."""

    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def token(key=KEY, **overrides):
    claims = {
        "token_use": "access",
        "iss": ISSUER,
        "client_id": CLIENT,
        "exp": int(time.time()) + 600,
        "username": "rohit",
        "cognito:groups": ["controller"],
    } | overrides
    return jwt.encode(claims, key, algorithm="RS256")


VERIFIER = TokenVerifier(ISSUER, CLIENT, keys=Keys())


# -- Tokens -------------------------------------------------------------------------------


def test_a_valid_token_gives_its_claims():
    assert VERIFIER.claims(token())["username"] == "rohit"


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: token(key=OTHER_KEY), "invalid token"),
        (lambda: token(exp=int(time.time()) - 1), "invalid token"),
        (lambda: token(iss="https://elsewhere"), "invalid token"),
        (lambda: token(client_id="client-2"), "not an access token for this app client"),
        (lambda: token(token_use="id"), "not an access token"),
        (lambda: "not.a.jwt", "invalid token"),
    ],
)
def test_bad_tokens_are_refused(make, message):
    with pytest.raises(AuthError, match=message):
        VERIFIER.claims(make())


def test_bearer_header():
    assert bearer("Bearer abc") == "abc" and bearer("bearer  abc ") == "abc"
    for header in (None, "", "Basic abc", "Bearer "):
        with pytest.raises(AuthError, match="sign in first"):
            bearer(header)


# -- The caller-scoped API ----------------------------------------------------------------


class Live:
    """The answer pipeline over the seed week, rules and templates only."""

    def __init__(self, tools):
        self.pipeline = Pipeline.offline(tools, example_questions())

    def ask(self, question, caller, previous=None):
        return self.pipeline.answer(question, caller, previous), {"calls": 0}


def assign(table, disruption_id, desk):
    """Record the desk a disruption was given (the runner picks the least-loaded one)."""
    table.update_item(
        Key={"pk": f"DISRUPTION#{disruption_id}", "sk": "META"},
        UpdateExpression="SET #a.#d = :desk",
        ExpressionAttributeNames={"#a": "analysis", "#d": "desk"},
        ExpressionAttributeValues={":desk": desk},
    )


@pytest.fixture
def api(ops_table):
    seed = load_seed()
    OpsStore(ops_table).put_seed(seed)
    disruption(ops_table, "r-sick1042", "C-1042", "2026-10-13", "2026-10-14")
    assign(ops_table, "r-sick1042", "desk-2")  # Rohit's: the next one analysed goes to desk-1
    sent = []
    built = []

    def live():
        built.append(1)
        return Live(CrewOpsTools(build_world(seed), seed))

    a = CrewOpsApi(ops_table, live, sent.append)
    a.sent, a.built = sent, built
    return a


def test_ask_answers_as_the_caller(api):
    out = api.ask(ROHIT, "Who is on reserve at BLR on 13 Oct?")
    assert out["verified"] and "D. Reddy (C-3310)" in out["answer"]
    mine = api.ask(CREW, "How many duty hours do I have?")
    assert "A. Nair (C-1042)" in mine["answer"]
    assert api.ask(CREW, "How many duty hours does C-3310 have?")["answer"] == (
        "That isn't available to your role."
    )


def test_whoami_is_the_verified_caller(api):
    assert api.whoami(CREW) == {
        "user_id": "crew1042",
        "roles": ["crew"],
        "crew_id": "C-1042",
        "desk": None,
    }
    assert api.whoami(ROHIT)["desk"] == "desk-2"


def test_crew_see_their_own_roster_only(api):
    roster = api.my_roster(CREW)
    assert roster["crew_id"] == "C-1042" and roster["clock"]["crew_id"] == "C-1042"
    assert [p["pairing_id"] for p in roster["pairings"]] == ["P-2291"]
    assert roster["pairings"][0]["role"] == "Captain"
    with pytest.raises(ApiError) as err:
        api.my_roster(ROHIT)
    assert err.value.code == "FORBIDDEN"


def test_rule_thresholds_are_read_by_staff_and_changed_by_admins(api):
    admin = Caller("priya", frozenset({Role.ADMIN}))
    assert {t["param"] for t in api.thresholds(ROHIT)} >= {"min_rest_hours"}
    with pytest.raises(ApiError):
        api.thresholds(CREW)
    with pytest.raises(ApiError) as err:
        api.set_threshold(ROHIT, "RULE-REST-04", "min_rest_hours", 13, 12, "x")
    assert err.value.code == "FORBIDDEN"
    api.live()
    out = api.set_threshold(admin, "RULE-REST-04", "min_rest_hours", 13, 12, "fatigue review")
    assert out["value"] == 13 and api._live is None  # the next answer uses the new rule
    with pytest.raises(ApiError) as err:
        api.set_threshold(admin, "RULE-REST-04", "min_rest_hours", 20, 13, "x")
    assert err.value.code == "INVALID"
    assert api.activity(admin)[0]["action"] == "RULE_CHANGED"
    with pytest.raises(ApiError):
        api.activity(ROHIT)
    # Relaxing it again (13h back to 12h rest) is a super admin's call, not an admin's.
    with pytest.raises(ApiError) as err:
        api.set_threshold(admin, "RULE-REST-04", "min_rest_hours", 12, 13, "x")
    assert err.value.code == "FORBIDDEN"
    boss = Caller("vikram", frozenset({Role.SUPER_ADMIN}))
    assert api.set_threshold(boss, "RULE-REST-04", "min_rest_hours", 12, 13, "rota")["value"] == 12
    # Both changes may share a second, so it is found by content, not by its position.
    relaxed = [e for e in api.activity(admin) if e.get("actor") == "vikram"]
    assert [(e["action"], e["from"], e["to"]) for e in relaxed] == [("RULE_CHANGED", 13, 12)]


def test_the_feed_lists_disruptions_for_staff(api):
    feed = api.disruptions(ROHIT)
    assert feed[0]["disruption_id"] == "r-sick1042" and feed[0]["status"] == "OPTIONS_READY"
    assert feed[0]["report_type"] == "crew_unavailable"
    with pytest.raises(ApiError):
        api.disruptions(CREW)


def test_a_disruption_shows_its_desk_its_trip_and_who_else_wants_its_crew(api):
    # C-1443 is out on P-2216 on 13 Oct, the same day as C-1042's P-2291: both Konkan
    # Skyways trips, so both rank C-1526 and C-5566. It is analysed second: desk-1.
    disruption(api.table, "r-sick1443", "C-1443", "2026-10-13")
    shown = api.show(ROHIT, "r-sick1042")
    assert shown["desks"] == [{"desk_id": "desk-2", "controller": "Rohit Malhotra"}]
    assert shown["yours"] is True
    context = shown["seats"][0]["context"]
    assert context["aircraft"] == "VT-DXC" and context["aircraft_type"] == "A320"
    assert [f["flight_no"] for f in context["flights"]][:3] == ["KS412", "KS413", "KS588"]
    assert {f["state"] for f in context["flights"]} == {"uncrewed"}
    assert {c["crew_id"] for c in context["crew"] if c["state"] == "out"} == {"C-1042"}
    rivals = shown["contention"]["C-1526"]
    assert rivals[0]["disruption_id"] == "r-sick1443" and rivals[0]["crew_out"] == "C-1443"
    assert rivals[0]["desks"] == [{"desk_id": "desk-1", "controller": "Ananya Iyer"}]
    live = shown["seats"][0]["live"]
    assert (
        live["options"][0]["crew_id"] == "C-3310" and live["options"][0]["resilience"] is not None
    )
    assert {"cheapest", "least_delay", "keeps_most_reserve_cover"} <= set(live["policies"])
    assert all(o["resilience"] is not None for o in live["policies"].values())
    assert live["funnel"][0]["stage"] == "considered" and live["excluded"]
    assert live["excluded"][0]["reasons"]
    assert any(r["base"] == "BLR" for r in shown["seats"][0]["reserves"])
    assert (
        api.show(Caller("ananya", frozenset({Role.CONTROLLER}), desk="desk-1"), "r-sick1042")[
            "yours"
        ]
        is False
    )


def test_whoever_filed_a_report_sees_only_how_it_stands(api):
    disruption(api.table, "r-sick1938", "C-1938", "2026-10-13")
    filer = api.show(Caller("u-1", frozenset({Role.REPORTER})), "r-sick1042")
    assert filer["outcome"] == "Crew control is arranging cover."
    assert set(filer) == {
        "disruption_id",
        "status",
        "outcome",
        "report_type",
        "reported_utc",
        "reported_by",
        "details",
        "handled",
        "remark",
    }
    assert filer["handled"] is None  # covering crew is crew control's, not the filer's
    assert "r-sick1938" not in str(filer) and "C-3310" not in str(filer)


def filed(table, report_id, type_, details, by="u-1", roles=("reporter",)):
    """Store a report as the ingest worker would and analyse it, as the stream does."""
    from test_plans import AT
    from test_reports import report

    from crewops.analysis.runner import Operation, analyse_by_id
    from crewops.domain import Report
    from crewops.store.reports import record_filed, store_report

    doc = report(type_, details, report_id=report_id, roles=roles)
    doc["reported_by"]["user_id"] = by
    record_filed(table, Report.from_dict(doc))  # as the API does when it queues one
    store_report(table, Report.from_dict(doc), AT)
    assert analyse_by_id(table, report_id, Operation.load(table)).outcome == "stored"


def test_a_delayed_seat_is_searched_on_the_day_of_the_delay(api):
    # KS412 on 13 Oct is the second day of P-2291's crew: live options must be worked out
    # for that day, as approval re-checks them.
    filed(
        api.table,
        "r-late412",
        "flight_delay",
        {"flight_id": "KS412-2026-10-13", "delay_minutes": 180},
    )
    seats = api.show(ROHIT, "r-late412")["seats"]
    assert seats and all(s["delay_date"] == "2026-10-13" for s in seats)


ANANYA = Caller("ananya", frozenset({Role.CONTROLLER}), desk="desk-1")
IMRAN = Caller("imran", frozenset({Role.REPORTER}))
GATE_CLOSED = {
    "gate": "DEL-G1",
    "from_utc": "2026-10-13T04:00:00Z",
    "to_utc": "2026-10-13T06:00:00Z",
    "reason": "security sweep",
}


def test_a_gate_closure_is_handled_internally_and_resolved_with_a_remark(api):
    filed(api.table, "r-gate-del1", "gate_closure", GATE_CLOSED, by="imran")
    shown = api.show(ANANYA, "r-gate-del1")  # DX402 flies VT-DXA, desk-1
    assert shown["yours"] is True and shown["seats"] == []
    assert shown["handling"]["kind"] == "internal"
    # DEL's only ordinary gate is closed: DX402 takes the station's emergency gate.
    assert [(m["flight_no"], m["to_gate"]) for m in shown["stands"]] == [("DX402", "DEL-E1")]
    assert any(f["flight_no"] == "DX402" and f["booked"] == 134 for f in shown["flights"])
    assert {n["team"] for n in shown["notices"]} >= {"gate_control", "station_ops", "flight_info"}
    assert api.show(ROHIT, "r-gate-del1")["yours"] is False
    assert "to_resolve" in {q["kind"] for q in api.queue(ANANYA)}

    with pytest.raises(ApiError) as err:
        api.close(ROHIT, "r-gate-del1", "re-gated")
    assert err.value.code == "FORBIDDEN"
    out = api.close(ANANYA, "r-gate-del1", "DX402 bussed from stand 14; screens updated")
    assert out["status"] == "CLOSED"
    trail = api.show(ANANYA, "r-gate-del1")["audit"]
    assert trail[-1]["reason"] == "DX402 bussed from stand 14; screens updated"

    # Whoever filed it reads what was done about the gate, nothing about crew.
    filer = api.show(IMRAN, "r-gate-del1")
    assert filer["outcome"] == "Resolved by crew control."
    assert filer["handled"].startswith("Handled at DEL by gate and stand control")
    assert filer["remark"] == "DX402 bussed from stand 14; screens updated"
    (mine,) = api.my_reports(IMRAN)
    assert mine["disruption_id"] == "r-gate-del1" and mine["handled"]
    assert api.my_reports(ROHIT) == []


def test_the_feed_carries_desks_and_how_each_is_handled(api):
    filed(api.table, "r-gate-del1", "gate_closure", GATE_CLOSED)
    feed = {d["disruption_id"]: d for d in api.disruptions(ROHIT)}
    assert feed["r-gate-del1"]["desks"] == ["desk-1"]
    assert feed["r-gate-del1"]["handling"] == "internal"
    assert feed["r-sick1042"]["desks"] == ["desk-2"]
    assert feed["r-sick1042"]["handling"] == "recrew"


def test_a_lapsing_certificate_shows_on_the_crew_members_page(api):
    lapse = {"crew_id": "C-5417", "cert_type": "recurrent_training", "valid_to": "2026-10-15"}
    filed(api.table, "r-cert5417", "certification_lapse", lapse)
    holder = Caller("crew5417", frozenset({Role.CREW}), crew_id="C-5417")
    (notice,) = api.my_roster(holder)["notices"]
    assert notice["disruption_id"] == "r-cert5417"
    assert "recurrent training expires on 2026-10-15" in notice["message"]
    assert api.my_roster(CREW)["notices"] == []


def test_reporters_get_what_a_report_can_name(api):
    ref = api.reference(IMRAN)
    assert "BLR-G2" in ref["gates"] and "VT-DXA" in ref["aircraft"]
    assert any(c["crew_id"] == "C-1042" for c in ref["crew"])
    assert any(f["flight_id"] == "KS412-2026-10-13" and f["gate"] for f in ref["flights"])
    assert "recurrent_training" in ref["cert_types"]
    with pytest.raises(ApiError):
        api.reference(CREW)


def test_rivals_carry_their_report_type(api):
    disruption(api.table, "r-sick1443", "C-1443", "2026-10-13")
    rivals = api.show(ROHIT, "r-sick1042")["contention"]["C-1526"]
    assert rivals[0]["report_type"] == "crew_unavailable"


def test_contention_is_only_for_trips_near_in_time(api):
    disruption(api.table, "r-sick5837", "C-5837", "2026-10-18")  # P-2207, 18 Oct
    assert "C-3983" not in api.show(ROHIT, "r-sick1042")["contention"]


def test_a_disruption_shows_its_ranked_options(api):
    seats = api.show(ROHIT, "r-sick1042")["seats"]
    assert seats and seats[0]["pairing_id"] == "P-2291" and seats[0]["crew_out"] == "C-1042"
    best = seats[0]["options"][0]
    assert best["crew_id"] == "C-3310" and best["cost_inr"] == 23090


def test_reports_carry_the_signed_in_sender(api):
    out = api.report(
        CREW, "crew_unavailable", {"crew_id": "C-1042", "from_date": "2026-10-15", "reason": "sick"}
    )
    (sent,) = api.sent
    assert sent["report_id"] == out["report_id"] and out["status"] == "queued"
    assert sent["reported_by"] == {"user_id": "crew1042", "roles": ["crew"], "crew_id": "C-1042"}
    with pytest.raises(ApiError) as err:
        api.report(
            CREW,
            "crew_unavailable",
            {"crew_id": "C-3310", "from_date": "2026-10-15", "reason": "sick"},
        )
    assert err.value.code == "FORBIDDEN"
    with pytest.raises(ApiError) as err:
        api.report(ROHIT, "crew_unavailable", {"crew_id": "C-1042"})
    assert err.value.code == "INVALID"
    assert len(api.sent) == 1


def test_plans_through_the_api_reload_the_operation(api):
    api.ask(ROHIT, "Who is on reserve at BLR on 13 Oct?")
    submitted = api.submit(ROHIT, "r-sick1042")
    assert submitted["status"] == "SUBMITTED" and api._live is None  # reloaded next time
    with pytest.raises(ApiError) as err:
        api.approve(ROHIT, "r-sick1042", submitted["plan_id"])
    assert err.value.code == "FORBIDDEN"
    done = api.approve(MEERA, "r-sick1042", submitted["plan_id"])
    assert done["status"] == "COMMITTED"
    shown = api.show(MEERA, "r-sick1042")
    assert shown["status"] == "COMMITTED" and shown["audit"][-1]["action"] == "COMMITTED"


def test_reading_a_disruption_needs_a_role_or_being_its_reporter(api):
    with pytest.raises(ApiError) as err:
        api.show(CREW, "r-sick1042")
    assert err.value.code == "NOT_FOUND"  # the same as a missing one: ids cannot be probed
    with pytest.raises(ApiError) as err:
        api.show(ROHIT, "r-nothere1")
    assert err.value.code == "NOT_FOUND"


def test_direct_tools_follow_the_role(api):
    names = {t["name"] for t in api.list_tools(CREW)}
    assert names == {
        "duty_clock",
        "notification_brief",
        "check_legality",
        "explain_rule",
        "search_rules",
    }
    assert api.run_tool(CREW, "lookup", {"entity": "crew"})["error_code"] == "FORBIDDEN"
    out = api.run_tool(ROHIT, "find_options", {"crew_id": "C-1042"})
    assert out["ok"] and out["result"]["recommended"]["crew_id"] == "C-3310"


class Memory:
    """Stands in for AgentCore Memory: one list of turns per (actor, conversation)."""

    def __init__(self):
        self.turns = {}

    def last_turn(self, actor, conversation, now):
        turns = self.turns.get((actor, conversation))
        return turns[-1] if turns else None

    def remember(self, actor, conversation, turn, answer_text):
        self.turns.setdefault((actor, conversation), []).append(turn)


class AtOnce:
    """Runs a memory write straight away instead of after the answer is returned."""

    def submit(self, fn, *args):
        fn(*args)


def test_a_conversation_reads_a_reply_with_its_question(api):
    api.memory, api._remembering = Memory(), AtOnce()
    first = api.ask(ROHIT, "Who can cover P-2291 on 13 Oct?", "conv-1")
    assert first["awaiting"] == "detail"
    reply = api.ask(ROHIT, "Captain", "conv-1")
    assert "Captain seat on P-2291" in reply["answer"]
    assert "read together with the question it answers" in reply["notes"]


def test_conversations_are_kept_apart_by_person_and_id(api):
    api.memory, api._remembering = Memory(), AtOnce()
    api.ask(ROHIT, "Who can cover P-2291 on 13 Oct?", "conv-1")
    assert api.memory.turns.keys() == {("rohit", "conv-1")}
    other = api.ask(MEERA, "Captain", "conv-1")  # someone else, same id: nothing carried
    assert "read together" not in " ".join(other["notes"])
    no_id = api.ask(ROHIT, "Captain")
    assert "read together" not in " ".join(no_id["notes"])
    with pytest.raises(ApiError) as err:
        api.ask(ROHIT, "Captain", "not ok!")
    assert err.value.code == "INVALID"


def test_alert_tools_say_when_alerts_are_not_set_up(api):
    with pytest.raises(ApiError) as err:
        api.alert_addresses(Caller("asha", frozenset({Role.ADMIN})))
    assert err.value.code == "UNAVAILABLE"


def test_alert_tools_are_for_admins(api):
    import boto3

    from crewops.notify.subscriptions import AlertSubscriptions

    sns = boto3.client("sns", region_name="ap-southeast-2")
    topic = sns.create_topic(Name="crewops-test-alerts")["TopicArn"]
    api.alerts = AlertSubscriptions(sns, topic, api.table)
    admin = Caller("asha", frozenset({Role.ADMIN}))
    added = api.add_alert_address(admin, "ops@example.com", "approver")
    assert [a["email"] for a in api.alert_addresses(admin)] == ["ops@example.com"]
    with pytest.raises(ApiError) as err:
        api.remove_alert_address(ROHIT, added["subscription_id"])
    assert err.value.code == "FORBIDDEN"
    assert api.remove_alert_address(admin, added["subscription_id"])["status"] == "removed"


# -- The MCP protocol, in-process ---------------------------------------------------------


def mcp_call(server, headers, tool, arguments):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app = server.streamable_http_app()

    async def run():
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
            headers=headers,
        )
        async with server.session_manager.run(), http:
            transport = streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)
            async with transport as (read, write, _), ClientSession(read, write) as session:
                await session.initialize()
                if tool is None:
                    return [t.name for t in (await session.list_tools()).tools]
                return await session.call_tool(tool, arguments)

    return anyio.run(run)


@pytest.fixture
def server(api):
    put_profile(api.table, profile_item("rohit", "Rohit Malhotra", ["controller"], None, "desk-2"))
    return build_server(api, Gate(VERIFIER, lambda u: get_profile(api.table, u)))


def test_tools_are_listed(server):
    names = mcp_call(server, {}, None, {})
    assert {"ask", "my_queue", "submit_plan", "approve_plan", "crew_tool"} <= set(names)
    assert {"alert_addresses", "add_alert_address", "remove_alert_address"} <= set(names)


def test_a_call_without_a_token_does_nothing(server):
    result = mcp_call(server, {}, "my_queue", {})
    assert result.isError and "UNAUTHORIZED" in result.content[0].text


def test_a_signed_in_call_acts_as_that_user(server):
    headers = {"authorization": f"Bearer {token()}"}
    result = mcp_call(server, headers, "my_queue", {})
    assert not result.isError
    payload = json.loads(result.content[0].text)
    assert payload["disruption_id"] == "r-sick1042" and payload["kind"] == "ready_for_plan"


def test_a_forged_token_does_nothing(server):
    forged = {"authorization": f"Bearer {token(key=OTHER_KEY)}"}
    result = mcp_call(server, forged, "my_queue", {})
    assert result.isError and "invalid token" in result.content[0].text


def test_an_unexpected_failure_reaches_the_caller_without_internals(server, api, monkeypatch):
    def broken(caller):
        raise RuntimeError("table arn:aws:dynamodb:secret-detail")

    monkeypatch.setattr(api, "queue", broken)
    headers = {"authorization": f"Bearer {token()}"}
    result = mcp_call(server, headers, "my_queue", {})
    text = result.content[0].text
    assert result.isError and "INTERNAL:" in text and "secret-detail" not in text


def test_a_failing_tool_reports_no_internals(api, monkeypatch):
    def broken(**_):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(api.live().pipeline.tools, "find_options", broken)
    out = api.run_tool(ROHIT, "find_options", {"crew_id": "C-1042"})
    assert out["error_code"] == "INTERNAL" and "secret-detail" not in out["error"]


def test_invocation_url_encodes_the_runtime_arn():
    from crewops.mcp.client import invocation_url

    arn = "arn:aws:bedrock-agentcore:ap-southeast-2:111111111111:runtime/crewops_dev_mcp-AbC"
    assert invocation_url("ap-southeast-2", arn) == (
        "https://bedrock-agentcore.ap-southeast-2.amazonaws.com/runtimes/"
        "arn%3Aaws%3Abedrock-agentcore%3Aap-southeast-2%3A111111111111%3Aruntime%2F"
        "crewops_dev_mcp-AbC/invocations?qualifier=DEFAULT"
    )


def test_client_shows_results_as_json():
    from crewops.mcp.client import _parsed

    assert _parsed(['{"answer": "yes"}']) == {"answer": "yes"}
    assert _parsed(['{"a": 1}', '{"b": 2}']) == [{"a": 1}, {"b": 2}]
    assert _parsed(["plain words"]) == "plain words"
    assert _parsed([]) == []
    assert _parsed(['{"a": 1}'], as_list=True) == [{"a": 1}]


def test_the_client_knows_which_tools_return_lists(api):
    import inspect

    from crewops.mcp.client import LIST_TOOLS

    server = build_server(api, Gate(VERIFIER, lambda u: None))
    returns_list = {
        t.name
        for t in server._tool_manager.list_tools()
        if inspect.signature(t.fn).return_annotation in (list, "list")
    }
    assert returns_list == LIST_TOOLS


def test_packaged_entry_point_finds_its_data():
    from crewops.mcp.package import MAIN

    compile(MAIN, "main.py", "exec")
    assert "CREWOPS_DATA_DIR" in MAIN and 'main(host="0.0.0.0")' in MAIN


# -- Reports as filed, refusals and the reporting desk ---------------------------------


def test_every_report_can_be_followed_by_whoever_filed_it(api):
    from crewops.store.reports import store_rejection

    crew = Caller("crew2087", frozenset({Role.CREW}), crew_id="C-2087")
    queued = api.report(
        crew, "crew_unavailable", {"crew_id": "C-2087", "from_date": "2026-10-16", "reason": "sick"}
    )
    (mine,) = api.my_reports(crew)
    assert mine["disruption_id"] == queued["report_id"] and mine["status"] == "QUEUED"
    # The worker refuses it: the person who sent it sees why.
    store_rejection(
        api.table, queued["report_id"], "unknown crew C-2087", "{}", datetime.now(UTC), "m-1"
    )
    (mine,) = api.my_reports(crew)
    assert mine["status"] == "REJECTED" and "unknown crew C-2087" in mine["outcome"]
    # The reporting desk sees what crew report about themselves; crew control too.
    inbox = api.my_reports(IMRAN, "crew")
    assert [r["disruption_id"] for r in inbox] == [queued["report_id"]]
    assert inbox[0]["reported_by"] == "crew2087"
    with pytest.raises(ApiError):
        api.my_reports(crew, "crew")


def test_the_same_days_cannot_be_reported_twice(api):
    # r-sick1042 already has C-1042 out on 13 and 14 Oct.
    with pytest.raises(ApiError) as err:
        api.report(
            CREW,
            "crew_unavailable",
            {"crew_id": "C-1042", "from_date": "2026-10-14", "reason": "sick"},
        )
    assert err.value.code == "DUPLICATE" and "r-sick1042" in err.value.message
    queued = api.report(
        CREW, "crew_unavailable", {"crew_id": "C-1042", "from_date": "2026-10-15", "reason": "sick"}
    )
    assert api.my_roster(CREW)["reported_unavailable"][0]["disruption_id"] == "r-sick1042"
    # Still in the queue, not yet stored: sending the same day again is refused too.
    with pytest.raises(ApiError) as err:
        api.report(
            CREW,
            "crew_unavailable",
            {"crew_id": "C-1042", "from_date": "2026-10-15", "reason": "sick"},
        )
    assert err.value.code == "DUPLICATE" and queued["report_id"] in err.value.message


def test_an_option_reported_sick_later_is_flagged_with_when(api):
    # C-3310 is the top option for r-sick1042; then they report sick for 13 Oct.
    filed(
        api.table,
        "r-sick3310",
        "crew_unavailable",
        {"crew_id": "C-3310", "from_date": "2026-10-13", "reason": "sick"},
    )
    gone = api.show(ROHIT, "r-sick1042")["unavailable_now"]
    assert gone["C-3310"]["disruption_id"] == "r-sick3310" and gone["C-3310"]["reason"] == "sick"


def test_open_disruptions_are_listed_live_and_by_desk():
    from crewops.tools import CrewOpsTools

    seed = load_seed()
    items = [
        {
            "disruption_id": "r-sick1042",
            "status": "OPTIONS_READY",
            "analysis": {"desk": "desk-2", "seats": []},  # as analysed: assigned to desk-2
            "report": {
                "report_id": "r-sick1042",
                "type": "crew_unavailable",
                "reported_by": {"user_id": "u-1", "roles": ["reporter"], "crew_id": None},
                "reported_utc": "2026-10-12T19:00:00Z",
                "details": {
                    "crew_id": "C-1042",
                    "from_date": "2026-10-13",
                    "to_date": "2026-10-13",
                    "reason": "sick",
                },
                "narrative": "",
            },
        }
    ]
    tools = CrewOpsTools(build_world(seed), seed, open_disruptions=lambda: items)
    everyone = tools.list_open_disruptions()
    assert everyone["count"] == 1 and everyone["disruptions"][0]["desks"] == ["desk-2"]
    assert everyone["disruptions"][0]["what"] == "C-1042 unavailable 2026-10-13 (sick)"
    assert tools.list_open_disruptions(desk="desk-1")["count"] == 0


def test_another_desk_suggests_cover_and_the_owner_sees_it(api):
    seat = "P-2291/Captain/C-1042"
    out = api.suggest(ANANYA, "r-sick1042", seat, "C-3983", "rested and nearby")
    assert out["status"] == "suggested"
    (s,) = api.show(ROHIT, "r-sick1042")["suggestions"]
    assert (s["by"], s["desk"], s["seat"], s["crew_id"], s["name"], s["note"]) == (
        "ananya",
        "desk-1",
        seat,
        "C-3983",
        "N. Chandra",
        "rested and nearby",
    )
    for caller, code in ((ROHIT, "INVALID"), (CREW, "FORBIDDEN"), (MEERA, "FORBIDDEN")):
        with pytest.raises(ApiError) as err:
            api.suggest(caller, "r-sick1042", seat, "C-3983")
        assert err.value.code == code
    with pytest.raises(ApiError) as err:
        api.suggest(ANANYA, "r-sick1042", "P-9999/Captain/-", "C-3983")
    assert err.value.code == "INVALID"


def test_a_filed_report_names_the_controller_desk_it_went_to(api):
    from test_reports import VALID_DETAILS

    queued = api.report(IMRAN, "gate_closure", VALID_DETAILS["gate_closure"])
    (mine,) = api.my_reports(IMRAN)
    assert mine["disruption_id"] == queued["report_id"] and mine["desks"] == []  # not yet
    # Once analysed it goes to the least-loaded desk: desk-2 already has r-sick1042.
    filed(api.table, "r-gate0042", "gate_closure", VALID_DETAILS["gate_closure"], by="imran")
    shown = {r["disruption_id"]: r for r in api.my_reports(IMRAN)}
    assert shown["r-gate0042"]["desks"] == [{"desk_id": "desk-1", "controller": "Ananya Iyer"}]


def test_the_same_seat_on_another_day_is_one_cover_not_a_rival(api):
    # r-sick1042 has C-1042 out of P-2291's captain seat; another report says the same for
    # 14 Oct. Their options are the same people, but they need one cover, not two.
    disruption(api.table, "r-c104214", "C-1042", "2026-10-14")
    shown = api.show(ROHIT, "r-sick1042")
    (same,) = shown["same_seat"]
    assert (same["disruption_id"], same["seats"]) == ("r-c104214", ["P-2291 Captain"])
    rivals = {r["disruption_id"] for entries in shown["contention"].values() for r in entries}
    assert "r-c104214" not in rivals
    assert api.show(ROHIT, "r-c104214")["same_seat"][0]["disruption_id"] == "r-sick1042"


def test_covering_one_day_of_a_trip_covers_the_other_days_report(api):
    # Read the roster from the table, as the deployed server does, so approvals show.
    api = CrewOpsApi(api.table, lambda: Live(CrewOpsTools.load(api.table)), lambda r: None)
    # C-1042 is also reported out for 14 Oct, the second day of the same trip: one seat.
    disruption(api.table, "r-c104214", "C-1042", "2026-10-14")
    first = api.submit(ROHIT, "r-sick1042")
    second = api.submit(ROHIT, "r-c104214")
    picked = {a["crew_id"] for p in (first, second) for a in p["picks"]}
    assert picked == {"C-3310"}  # both would use the same reserve captain
    # The first approval re-checks every rule for the whole trip (both days) and commits.
    assert api.approve(MEERA, "r-sick1042", first["plan_id"])["status"] == "COMMITTED"
    # The second, made against the old roster, is refused: nothing is written twice.
    stale = api.approve(MEERA, "r-c104214", second["plan_id"])
    assert stale["status"] == "STALE" and "the crew of P-2291 changed" in " ".join(stale["reasons"])
    # Its record shows the seat already filled, and who flies it, instead of options.
    (seat,) = api.show(ROHIT, "r-c104214")["seats"]
    assert seat["live"] is None
    assert seat["re_crewed"] == {
        "cancelled": False,
        "holders": [{"crew_id": "C-3310", "name": "D. Reddy"}],
    }
    with pytest.raises(ApiError) as err:
        api.submit(ROHIT, "r-c104214")
    assert err.value.code == "NOTHING_TO_COVER"


SPEAKER = Caller("ops.speaker", frozenset({Role.SPEAKER}))


def test_the_speaker_hears_its_desk_and_the_room_only(api):
    from crewops.alerts import spoken

    (alert,) = api.speaker_feed(SPEAKER, "desk-2")["alerts"]
    assert alert["event"] == "OPTIONS_READY" and alert["disruption_id"] == "r-sick1042"
    assert alert["text"].endswith(
        "Recommended cover for P-2291 Captain: D. Reddy (C-3310), a reserve callout, "
        "₹23,090. On Rohit Malhotra's desk."
    )
    assert alert["speech"] == spoken(alert["text"]) and "C 3310" in alert["speech"]
    assert "23,090 rupees" in alert["speech"] and "13 October" in alert["speech"]
    assert api.speaker_feed(SPEAKER, "desk-1")["alerts"] == []
    assert [a["id"] for a in api.speaker_feed(SPEAKER, "room")["alerts"]] == [alert["id"]]
    # Submitted: it leaves the desk speaker and reaches the approvals corner, as a new event.
    api.submit(ROHIT, "r-sick1042")
    assert api.speaker_feed(SPEAKER, "desk-2")["alerts"] == []
    (waiting,) = api.speaker_feed(SPEAKER, "approvals")["alerts"]
    assert waiting["event"] == "SUBMITTED" and waiting["id"] != alert["id"]
    assert "waiting for approval, from rohit" in waiting["text"]
    # Only a speaker device hears the feed; people read the console.
    for caller in (ROHIT, MEERA, CREW, IMRAN):
        with pytest.raises(ApiError) as err:
            api.speaker_feed(caller, "room")
        assert err.value.code == "FORBIDDEN"
    with pytest.raises(ApiError) as err:
        api.speaker_feed(SPEAKER, "desk-9")
    assert err.value.code == "INVALID"
    # A speaker device does not ask questions (no model calls on its account).
    with pytest.raises(ApiError) as err:
        api.ask(SPEAKER, "who covers P-2291?")
    assert err.value.code == "FORBIDDEN"


def test_welcome_back_says_what_came_while_away(api):
    from datetime import timedelta

    first = api.welcome_back(ROHIT)
    assert first == {"since": None, "alerts": [], "speech": ""}  # first sign-in: nothing
    # Signed in "an hour ago"; since then a plan of Rohit's was sent back.
    real = api.clock
    api.clock = lambda: real() - timedelta(hours=1)
    api.welcome_back(ROHIT)
    api.clock = real
    plan = api.submit(ROHIT, "r-sick1042")
    api.reject(MEERA, "r-sick1042", plan["plan_id"], "Reserve needed for tomorrow")
    back = api.welcome_back(ROHIT)
    (alert,) = back["alerts"]
    assert alert["event"] == "REJECTED"
    assert alert["text"].startswith("The plan for C-1042 unavailable 2026-10-13 to 2026-10-14")
    assert back["speech"].startswith("Welcome back, Rohit. While you were away, 1 update came")
    assert "Reserve needed for tomorrow" in back["speech"]
    assert api.welcome_back(ROHIT)["alerts"] == []  # now seen
    assert api.welcome_back(IMRAN)["alerts"] == []  # reporters have no desk to come back to


def test_the_reporting_desk_sees_the_week_with_crew_and_who_is_out(api):
    imran = Caller("imran.sheikh", frozenset({Role.REPORTER}))
    week = api.operations(imran)
    assert week["week"][0] == "2026-10-12" and len(week["flights"]) > 100
    on_2291 = [f for f in week["flights"] if f["pairing_id"] == "P-2291"]
    assert on_2291 and all(f["captain"]["crew_id"] == "C-1042" for f in on_2291)
    out = {f["date"]: f["captain"]["out"] for f in on_2291}
    assert out["2026-10-13"] and out["2026-10-14"]  # reported sick those days
    assert all(f["airline"] for f in week["flights"])
    hit = [f for f in on_2291 if f["disruptions"]]
    assert hit and hit[0]["disruptions"][0]["disruption_id"] == "r-sick1042"
    assert hit[0]["disruptions"][0]["severity"] in ("low", "medium", "high")
    (sick,) = [u for u in week["unavailable"] if u["crew_id"] == "C-1042"]
    assert sick["from_date"] == "2026-10-13" and sick["rank"] == "Captain"
    # A reporter sees who is out and when, never why or the record behind it.
    assert "reason" not in sick and "disruption_id" not in sick
    (full,) = [u for u in api.operations(ROHIT)["unavailable"] if u["crew_id"] == "C-1042"]
    assert full["reason"] == "sick" and full["disruption_id"] == "r-sick1042"
    with pytest.raises(ApiError):
        api.operations(CREW)


def test_reports_cover_today_to_two_months_and_leave_is_for_admins(api):
    imran = Caller("imran.sheikh", frozenset({Role.REPORTER}))
    priya = Caller("priya", frozenset({Role.ADMIN}))
    sick = {"crew_id": "C-2087", "from_date": "2026-10-15", "reason": "sick"}
    assert api.report(imran, "crew_unavailable", sick)["status"] == "queued"
    for bad in ("2026-10-11", "2026-12-20"):  # yesterday; beyond two months
        with pytest.raises(ApiError) as err:
            api.report(imran, "crew_unavailable", {**sick, "from_date": bad})
        assert err.value.code == "INVALID" and "outside" in err.value.message
    with pytest.raises(ApiError) as err:
        api.report(imran, "flight_delay", {"flight_id": "DX401-2026-10-11", "delay_minutes": 30})
    assert err.value.code == "INVALID"
    leave = {"crew_id": "C-2087", "from_date": "2026-10-17", "to_date": "2026-10-18"}
    with pytest.raises(ApiError) as err:
        api.report(imran, "planned_leave", {**leave, "reason": "annual_leave"})
    assert err.value.code == "FORBIDDEN"
    out = api.report(priya, "planned_leave", {**leave, "reason": "annual_leave"})
    assert out["status"] == "queued"
    with pytest.raises(ApiError):  # leave starts tomorrow at the earliest
        api.report(
            priya, "planned_leave", {**leave, "from_date": "2026-10-12", "reason": "medical"}
        )
    (booked,) = api.my_reports(priya, "leave")
    assert booked["details"]["reason"] == "annual_leave"
    with pytest.raises(ApiError):
        api.my_reports(imran, "leave")


def test_the_operations_board_moves_a_week_at_a_time_inside_the_window(api):
    first = api.operations(ROHIT)
    assert first["window"]["today"] == "2026-10-12" and first["week"][0] == "2026-10-12"
    later = api.operations(ROHIT, "2026-10-14")
    assert later["week"][0] == "2026-10-12"  # the seed week ends on the 18th: clamped
    assert {f["date"] for f in later["flights"]} <= set(later["week"])
    with pytest.raises(ApiError):
        api.operations(ROHIT, "not-a-date")


def test_the_ops_room_board_shows_every_open_disruption_most_serious_first(api):
    speaker = Caller("ops.speaker", frozenset({Role.SPEAKER}))
    board = api.ops_board(speaker)
    (card,) = [c for c in board["disruptions"] if c["disruption_id"] == "r-sick1042"]
    assert card["severity"] in ("high", "medium", "low") and card["reasons"]
    assert card["desk"]["desk_id"] == "desk-2" and card["what"].startswith("C-1042")
    assert card["cover"] and card["cover"][0]["how"]
    assert board["counts"]["open"] == len(board["disruptions"]) >= 1
    assert [d["desk_id"] for d in board["desks"]] == ["desk-1", "desk-2", "desk-3"]
    assert board["day"]["flights"] > 0 and board["today"] == "2026-10-12"
    assert card["impact"]["flights"] >= 1 and card["impact"]["passengers"] > 0
    assert board["latest"] and board["latest"][0]["title"]
    assert len(board["departures"]) == 8 and all(d["state"] for d in board["departures"])
    assert {r["airline"] for r in board["reserve_cover"]} == {"DX", "KS", "NG"}
    assert board["system"]["models"]["state"]
    assert api.ops_board(ROHIT)["counts"] == board["counts"]
    with pytest.raises(ApiError):
        api.ops_board(CREW)
