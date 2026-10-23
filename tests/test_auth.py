"""The team file, the claims-to-caller mapping, and syncing accounts (in-memory Cognito)."""

import time

import pytest
from botocore.exceptions import ClientError

from crewops.auth.identity import AuthError, caller_from_claims, issuer_for
from crewops.auth.provision import find_user_pool, password_problems, plan_team
from crewops.auth.team import TeamMember, load_team, validate_team
from crewops.domain import Role
from crewops.ingestion.seed import load_seed
from crewops.store.users import get_profile, profile_item

POOL = "eu-pool"


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def member(username="ann", name="Ann Shah", roles=("approver",), crew_id=None, desk=None):
    return TeamMember(username, name, frozenset(Role(r) for r in roles), crew_id, desk)


# -- Team file --------------------------------------------------------------------------


def test_demo_team_is_valid_and_covers_every_role(seed):
    team = load_team()
    assert validate_team(team, seed) == []
    assert {r for m in team for r in m.roles} == set(Role)
    controllers = [m for m in team if Role.CONTROLLER in m.roles]
    assert sorted(m.desk for m in controllers) == ["desk-1", "desk-2", "desk-3"]
    assert len([m for m in team if Role.APPROVER in m.roles]) == 3


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (member(roles=("crew",)), "crew login needs the crew id"),
        (member(roles=("crew",), crew_id="C-1042"), "C-1042 is A. Nair, not Ann Shah"),
        (member(crew_id="C-1042"), "only a crew login"),
        (member(roles=("controller",)), "a controller needs a desk"),
        (member(roles=("controller",), desk="desk-1"), "is Ananya Iyer's desk"),
        (member(desk="desk-1"), "only a controller holds a desk"),
        (member(name="A. Shah"), "written like a crew member's name"),
    ],
)
def test_team_problems_are_named(seed, bad, message):
    errors = validate_team([bad], seed)
    assert any(message in e for e in errors), errors


def test_duplicates_are_refused(seed):
    errors = validate_team([member(), member(name="Bo Lee")], seed)
    assert "username 'ann' is used by 2 members" in errors


def test_team_file_shape_is_checked(tmp_path):
    from crewops.domain import DataError

    path = tmp_path / "team.json"
    path.write_text('{"members": [{"username": "A!", "name": "x", "roles": ["crew"]}]}')
    with pytest.raises(DataError, match="username"):
        load_team(path)
    path.write_text('{"members": [{"username": "abc", "name": "x", "roles": ["pilot"]}]}')
    with pytest.raises(DataError, match="unknown role"):
        load_team(path)
    path.write_text(
        '{"members": [{"username": "abc", "name": "x", "roles": ["crew"], "email": "a@b.c"}]}'
    )
    with pytest.raises(DataError, match="unknown fields"):
        load_team(path)


# -- Claims to caller -------------------------------------------------------------------

ISSUER = issuer_for("ap-southeast-2", POOL)
NOW = 1_800_000_000


def claims(**overrides):
    base = {
        "token_use": "access",
        "iss": ISSUER,
        "client_id": "client-1",
        "exp": NOW + 600,
        "username": "Crew1042",
        "cognito:groups": ["crew"],
    }
    return base | overrides


PROFILES = {
    "crew1042": {"crew_id": "C-1042"},
    "ananya": {"desk": "desk-1"},
    "meera": {"crew_id": "C-9999", "desk": "desk-9"},
}


def caller(c):
    return caller_from_claims(c, PROFILES.get, issuer=ISSUER, client_id="client-1", now=NOW)


def test_crew_caller_takes_crew_id_from_the_profile():
    who = caller(claims())
    assert who.user_id == "crew1042" and who.roles == {Role.CREW} and who.crew_id == "C-1042"


def test_controller_gets_their_desk_and_others_get_neither():
    who = caller(claims(username="ananya", **{"cognito:groups": ["controller"]}))
    assert who.desk == "desk-1" and who.crew_id is None
    approver = caller(claims(username="meera", **{"cognito:groups": ["approver"]}))
    assert approver.crew_id is None and approver.desk is None  # profile ignored for their role


@pytest.mark.parametrize(
    ("groups", "roles"),
    [
        ("[controller auditor]", {Role.CONTROLLER, Role.AUDITOR}),
        ('["controller", "auditor"]', {Role.CONTROLLER, Role.AUDITOR}),
        ("controller auditor", {Role.CONTROLLER, Role.AUDITOR}),
        ('["auditor,super_admin"]', set()),  # one odd name, not two roles
    ],
)
def test_groups_passed_on_as_text(groups, roles):
    c = claims(username="ananya", **{"cognito:groups": groups})
    if roles:
        assert caller(c).roles == roles
    else:
        with pytest.raises(AuthError, match="holds no role"):
            caller(c)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"token_use": "id"}, "not a Cognito access token"),
        ({"iss": issuer_for("ap-southeast-2", "other")}, "another user pool"),
        ({"client_id": "client-2"}, "another app client"),
        ({"exp": NOW}, "expired"),
        ({"username": ""}, "names no user"),
        ({"cognito:groups": ["pilots"]}, "holds no role"),
        ({"username": "nobody"}, "linked to no crew record"),
        ({"username": "x", "cognito:groups": ["controller"]}, "holds no desk"),
    ],
)
def test_unusable_claims_are_refused(override, message):
    with pytest.raises(AuthError, match=message):
        caller(claims(**override))


def test_issuer_and_client_must_be_given():
    forged = claims(iss="https://elsewhere", **{"cognito:groups": ["super_admin"]})
    with pytest.raises(TypeError):
        caller_from_claims(forged, PROFILES.get)  # no silent default that skips the checks
    with pytest.raises(AuthError, match="must be given"):
        caller_from_claims(forged, PROFILES.get, issuer="", client_id="")


def test_expiry_defaults_to_the_clock():
    with pytest.raises(AuthError, match="expired"):
        caller_from_claims(
            claims(exp=time.time() - 1), PROFILES.get, issuer=ISSUER, client_id="client-1"
        )


# -- Syncing accounts -------------------------------------------------------------------


class FakeCognito:
    """The handful of Cognito admin calls the sync uses, in memory."""

    def __init__(self):
        self.users = {}
        self.groups = {}
        self.passwords = {}
        self.calls = []
        self.status = {}
        self.disabled = set()

    def list_user_pools(self, MaxResults, NextToken=None):  # noqa: N803
        return {
            "UserPools": [{"Name": "other", "Id": "x"}, {"Name": "crewops-test-users", "Id": POOL}]
        }

    def list_users(self, UserPoolId, PaginationToken=None):  # noqa: N803
        users = [
            {
                "Username": u,
                "Attributes": [{"Name": k, "Value": v} for k, v in a.items()],
                "UserStatus": self.status.get(u, "CONFIRMED"),
                "Enabled": u not in self.disabled,
            }
            for u, a in sorted(self.users.items())
        ]
        start = int(PaginationToken or 0)  # two pages, to exercise pagination
        page = {"Users": users[start : start + 10]}
        if start + 10 < len(users):
            page["PaginationToken"] = str(start + 10)
        return page

    def admin_enable_user(self, UserPoolId, Username):  # noqa: N803
        self.disabled.discard(Username)

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction):  # noqa: N803
        assert MessageAction == "SUPPRESS"  # never send an invitation
        self.calls.append(("create", Username))
        self.users[Username] = {a["Name"]: a["Value"] for a in UserAttributes}
        self.groups[Username] = set()
        self.status[Username] = "FORCE_CHANGE_PASSWORD"

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):  # noqa: N803
        assert Permanent
        self.passwords[Username] = Password
        self.status[Username] = "CONFIRMED"

    def admin_list_groups_for_user(self, UserPoolId, Username, Limit):  # noqa: N803
        return {"Groups": [{"GroupName": g} for g in sorted(self.groups[Username])]}

    def admin_add_user_to_group(self, UserPoolId, Username, GroupName):  # noqa: N803
        self.groups[Username].add(GroupName)

    def admin_remove_user_from_group(self, UserPoolId, Username, GroupName):  # noqa: N803
        self.groups[Username].discard(GroupName)

    def admin_update_user_attributes(self, UserPoolId, Username, UserAttributes):  # noqa: N803
        self.users[Username].update({a["Name"]: a["Value"] for a in UserAttributes})

    def admin_delete_user(self, UserPoolId, Username):  # noqa: N803
        if Username not in self.users:
            raise ClientError({"Error": {"Code": "UserNotFoundException"}}, "AdminDeleteUser")
        del self.users[Username]
        del self.groups[Username]


def apply(plan):
    for step in plan.steps:
        step()


def test_sync_creates_then_changes_nothing(ops_table):
    cognito, team = FakeCognito(), load_team()
    asked = []

    def password():
        asked.append(1)
        return "Correct-Horse-9"

    plan = plan_team(cognito, POOL, ops_table, team, password)
    assert asked == []  # nothing is asked for until a step runs
    apply(plan)
    assert len(cognito.users) == len(team) == 23 and set(cognito.passwords.values()) == {
        "Correct-Horse-9"
    }
    assert cognito.groups["ananya.iyer"] == {"controller"}
    assert get_profile(ops_table, "c1042.nair")["crew_id"] == "C-1042"
    assert get_profile(ops_table, "ananya.iyer")["desk"] == "desk-1"
    again = plan_team(cognito, POOL, ops_table, team, password)
    assert again.steps == [] and again.actions == []


def test_sync_fixes_drift(ops_table):
    cognito, team = FakeCognito(), load_team()
    apply(plan_team(cognito, POOL, ops_table, team, lambda: "Correct-Horse-9"))
    cognito.groups["meera.bhatt"] |= {"admin"}  # someone granted an extra role by hand
    cognito.users["rohit.malhotra"]["name"] = "R. M."
    cognito.disabled.add("divya.rao")
    cognito.status["farah.qureshi"] = "FORCE_CHANGE_PASSWORD"  # a create whose password never set
    plan = plan_team(cognito, POOL, ops_table, team, lambda: "Correct-Horse-9")
    assert "remove meera.bhatt from admin" in plan.actions
    assert "rename rohit.malhotra to Rohit Malhotra" in plan.actions
    assert "enable divya.rao" in plan.actions
    assert "set the password of farah.qureshi (it has no usable one)" in plan.actions
    apply(plan)
    assert cognito.groups["meera.bhatt"] == {"approver"} and "divya.rao" not in cognito.disabled
    assert cognito.status["farah.qureshi"] == "CONFIRMED"
    assert plan_team(cognito, POOL, ops_table, team, lambda: "x").steps == []


def test_leaving_the_file_removes_access_and_prune_deletes(ops_table):
    cognito, team = FakeCognito(), load_team()
    apply(plan_team(cognito, POOL, ops_table, team, lambda: "Correct-Horse-9"))
    without_priya = [m for m in team if m.username != "priya.desai"]
    plan = plan_team(cognito, POOL, ops_table, without_priya, lambda: "x")
    assert plan.actions == [
        "remove priya.desai from admin (not in the team)",
        "delete the profile of priya.desai (not in the team)",
    ]
    apply(plan)
    assert cognito.groups["priya.desai"] == set() and get_profile(ops_table, "priya.desai") is None
    apply(plan_team(cognito, POOL, ops_table, without_priya, lambda: "x", prune=True))
    assert "priya.desai" not in cognito.users


def test_a_profile_with_no_account_is_removed(ops_table):
    from crewops.store.users import put_profile

    put_profile(ops_table, profile_item("ghost", "Ghost Writer", ["crew"], "C-1042", None))
    plan = plan_team(FakeCognito(), POOL, ops_table, [], lambda: "x")
    assert plan.actions == ["delete the profile of ghost (no such account)"]


def test_reset_passwords(ops_table):
    cognito, team = FakeCognito(), load_team()
    apply(plan_team(cognito, POOL, ops_table, team, lambda: "Correct-Horse-9"))
    plan = plan_team(
        cognito, POOL, ops_table, team, lambda: "Battery-Staple-7", reset_passwords=True
    )
    apply(plan)
    speaker = next(m.username for m in team if Role.SPEAKER in m.roles)
    people = {u: p for u, p in cognito.passwords.items() if u != speaker}
    assert set(people.values()) == {"Battery-Staple-7"} and plan.needs_password
    # the speaker keeps its own password: the hosted app and the TV sign in with it
    assert cognito.passwords[speaker] == "Correct-Horse-9"


def test_profile_never_holds_contact_details():
    item = profile_item("ann", "Ann Shah", ["approver"], None, None)
    assert set(item) == {"pk", "sk", "entity", "username", "name", "roles"}


@pytest.mark.parametrize(
    "bad",
    ["Abcdefghijk1£", "Abcdefghijk1é", " Abcdefghijk1!", "Abcdefghijk1! ", "short-A1", "a" * 300],
)
def test_passwords_cognito_would_reject_are_refused_first(bad):
    assert password_problems(bad)


def test_password_policy_and_pool_lookup():
    assert password_problems("Correct-Horse-9") == []
    assert password_problems("Correct Horse-9") == []  # an inner space is a symbol to Cognito
    assert "a symbol" in password_problems("CorrectHorse99")
    assert find_user_pool(FakeCognito(), "crewops-test-users") == POOL
    assert find_user_pool(FakeCognito(), "missing") is None
    assert find_user_pool(FakeCognito(), "crewops-test-users", from_stack="p-1") == "p-1"


def test_two_pools_with_one_name_is_an_error():
    from crewops.auth.provision import ProvisionError

    class Twins(FakeCognito):
        def list_user_pools(self, MaxResults, NextToken=None):  # noqa: N803
            return {"UserPools": [{"Name": "crewops-test-users", "Id": i} for i in ("a", "b")]}

    with pytest.raises(ProvisionError, match="2 user pools"):
        find_user_pool(Twins(), "crewops-test-users")


def test_role_combinations_and_name_forms(seed):
    assert any(
        "nobody both submits and approves" in e
        for e in validate_team(
            [member(roles=("controller", "approver"), desk="desk-1", name="Ananya Iyer")], seed
        )
    )
    crew_admin = member(roles=("crew", "admin"), crew_id="C-1042", name="A. Nair")
    assert any("holds no other role" in e for e in validate_team([crew_admin], seed))
    # A speaker is a device, so it can never also be a person with a role.
    speaker_admin = member(
        username="ops.speaker", name="Ops Room Speaker", roles=("speaker", "admin")
    )
    assert any("a speaker is a device" in e for e in validate_team([speaker_admin], seed))
    assert (
        validate_team(
            [member(username="ops.speaker", name="Ops Room Speaker", roles=("speaker",))], seed
        )
        == []
    )
    for name in ("A.Shah", "Shah A."):
        assert validate_team([member(name=name)], seed)
