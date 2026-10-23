"""Questions about the assistant and about the roles: "who are you", "what can I do", "what
can a super admin do that I can't", "can I relax a threshold". Answered from the caller's
verified roles, before routing.

Without this, "what can I do" read like a crew lookup and "what can I do that a super admin
can't" listed the controller desks. The duties here are the ones the server enforces
(``crewops.mcp.api``, ``crewops.plans.service``, ``crewops.auth.accounts``,
``crewops.store.rules``); nothing here grants or checks access.
"""

from __future__ import annotations

import re

from crewops.domain import Role
from crewops.tools import Caller

_ROLE_WORDS: tuple[tuple[Role, str], ...] = (
    (Role.SUPER_ADMIN, r"super[\s-]?admin(istrator)?s?"),
    (Role.ADMIN, r"admin(istrator)?s?"),
    (Role.CONTROLLER, r"(crew )?controllers?"),
    (Role.APPROVER, r"approvers?"),
    (Role.REPORTER, r"reporters?|(the )?reporting desk"),
    (Role.AUDITOR, r"auditors?"),
    (Role.CREW, r"crew( members?)?|pilots?|cabin crew"),
)
NAMES = {
    Role.SUPER_ADMIN: "a super admin",
    Role.ADMIN: "an admin",
    Role.CONTROLLER: "a controller",
    Role.APPROVER: "an approver",
    Role.REPORTER: "a reporter",
    Role.AUDITOR: "an auditor",
    Role.CREW: "crew",
    Role.SPEAKER: "the ops-room speaker",
}

_ADMIN = (
    "revoke a report filed in error",
    "book planned leave",
    "tighten a rule threshold",
    "reset a password for anyone but an admin",
    "read the activity log",
)
DUTIES: dict[Role, tuple[str, ...]] = {
    Role.CONTROLLER: (
        "plan cover for the disruptions on your desk",
        "submit a plan for approval",
        "close a disruption for an approver to sign off",
        "file a sick call, a delay or a gate change",
    ),
    Role.APPROVER: (
        "approve or reject a controller's plan",
        "confirm or return a controller's resolution",
        "file a sick call, a delay or a gate change",
    ),
    Role.REPORTER: ("file a sick call, a delay, a gate change or a gate closure",),
    Role.AUDITOR: ("read every disruption, plan and decision, without changing anything",),
    Role.ADMIN: _ADMIN,
    Role.SUPER_ADMIN: (
        *_ADMIN,
        "relax a threshold back towards the regulatory limit",
        "reset an admin's password",
    ),
    Role.CREW: (
        "check their own trips, standby days, days off, duty hours, certificates, and whether "
        "they're legal for a trip",
    ),
}
ASK: dict[Role, str] = {
    Role.CONTROLLER: "who covers C-1042 on P-2291, or what's open on my desk",
    Role.APPROVER: "what's waiting for my approval, or is C-1042 legal for P-2291",
    Role.AUDITOR: "what was decided on a disruption, or is C-1042 legal for P-2291",
    Role.REPORTER: "is gate BLR-G6 free, or explain the rest rule",
    Role.ADMIN: "how many open disruptions each controller has, or explain the rest rule",
    Role.SUPER_ADMIN: "how many open disruptions each controller has, or explain the rest rule",
    Role.CREW: "when do I fly next, am I on standby, or what are my duty hours",
}
ORDER = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.CONTROLLER,
    Role.APPROVER,
    Role.AUDITOR,
    Role.REPORTER,
    Role.CREW,
)

WHO_ARE_YOU_RE = re.compile(
    r"^(hi |hello |hey )?(who are you|what are you|what's your name|what is your name|"
    r"introduce yourself|tell me about yourself|what is crew ?ops|what's crew ?ops)$",
    re.I,
)
HELP_RE = re.compile(
    r"^(help( me)?|what can (you|i) do( here| for me)?|what can i ask( you)?|"
    r"what (do|can) you (know|help with)|how can you help( me)?|what should i ask|"
    r"what are my (options|permissions|rights|duties)|what (am i|is my role) allowed to do)$",
    re.I,
)
# non-capturing inside, so each role is one numbered group in the patterns below
_ROLE = "|".join(f"(?:{p.replace('(', '(?:')})" for _, p in _ROLE_WORDS)
_WHAT = r"\bwhat( things| else| all| more| powers)?"
MINE_NOT_THEIRS_RE = re.compile(
    rf"{_WHAT} (can|may) i do that (an? |the )?(?P<role>{_ROLE}) (can'?t|cannot|can not)\b",
    re.I,
)
THEIRS_NOT_MINE_RE = re.compile(
    rf"{_WHAT} (can|may|does) (an? |the )?(?P<role>{_ROLE}) do that i "
    r"(can'?t|cannot|can not|don'?t)\b",
    re.I,
)
DIFFERENCE_RE = re.compile(
    rf"\bdifference between (an? |the )?({_ROLE}) and (an? |the )?({_ROLE})\b", re.I
)
WHAT_DOES_RE = re.compile(
    rf"^what (can|does|do) (an? |the )?({_ROLE}) do$|^what is (an? |the )?({_ROLE})$", re.I
)
HOW_REPORT_RE = re.compile(
    r"^how (do|can|should) (i|we) (report|file|raise|log|record)\b(?!.*\bleave\b)", re.I
)
HOW_LEAVE_RE = re.compile(
    r"^how (do|can|should) (i|we) (book|file|record|add|enter)\b.*\bleave\b", re.I
)
CREW_OUT_RE = re.compile(
    r"\b((i am|i'm|im|i feel|feeling) (sick|unwell|ill|not well|fatigued|too tired)|"
    r"call(ing)? in sick|report (myself|me) (sick|unavailable)|who covers (me|my trip)|"
    r"(swap|drop|give up|change) my (trip|flight|duty|pairing))\b",
    re.I,
)
FILERS = frozenset({Role.REPORTER, Role.CONTROLLER, Role.APPROVER})
WHO_DOES_RE = re.compile(
    r"^who (approve|sign|plan|file|reset|revoke|book|relax|tighten|change|close|report)s?\b",
    re.I,
)
CAN_I_RE = re.compile(r"^(can|may|could) i\b|^am i (allowed|able) to\b|^who (can|may)\b", re.I)

# "can I ...": what the question asks to do, and who may do it.
ACTIONS: tuple[tuple[re.Pattern[str], str, frozenset[Role]], ...] = (
    (
        re.compile(r"\b(relax|loosen|ease)\b.*\b(threshold|limit|rule)", re.I),
        "relax a threshold",
        frozenset({Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\b(tighten|change|edit|set|adjust)\b.*\b(threshold|limit|rule)", re.I),
        "tighten a threshold (only a super admin relaxes one)",
        frozenset({Role.ADMIN, Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\breset\b.*\badmin('s|s)?\b.*\bpassword", re.I),
        "reset an admin's password",
        frozenset({Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\breset\b.*\bpassword", re.I),
        "reset someone else's password (only a super admin resets an admin's)",
        frozenset({Role.ADMIN, Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\b(revoke|withdraw|cancel|delete)\b.*\breport", re.I),
        "revoke a report filed in error",
        frozenset({Role.ADMIN, Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\b(book|file|record|add)\b.*\bleave\b", re.I),
        "book planned leave",
        frozenset({Role.ADMIN, Role.SUPER_ADMIN}),
    ),
    (
        re.compile(r"\b(approve|sign off|confirm)\b.*\b(my own|own|myself)\b", re.I),
        "approve your own plan or resolution: a second person always signs off",
        frozenset(),
    ),
    (
        re.compile(r"\b(approve|reject|sign off|confirm)\b", re.I),
        "approve or reject a plan, or sign off a resolution",
        frozenset({Role.APPROVER}),
    ),
    (
        re.compile(r"\b(close|resolve)\b.*\bdisruption", re.I),
        "close a disruption",
        frozenset({Role.CONTROLLER}),
    ),
    (
        re.compile(r"\b(plan|submit|choose|pick|assign)\b.*\b(cover|plan|option|reserve)", re.I),
        "plan cover and submit it for approval",
        frozenset({Role.CONTROLLER}),
    ),
    (
        re.compile(
            r"\b(report|file|raise|log)\b.*\b(sick|delay|gate|unavailab|disruption|absen)", re.I
        ),
        "file a sick call, a delay or a gate change",
        frozenset({Role.REPORTER, Role.CONTROLLER, Role.APPROVER}),
    ),
)


def _bare(question: str) -> str:
    text = re.sub(
        r"\b(alexa|crewops|crew ops|please|exactly|actually|then)\b", " ", question, flags=re.I
    )
    text = re.sub(r"[^\w\s'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _role_of(word: str) -> Role:
    return next(r for r, p in _ROLE_WORDS if re.fullmatch(p, word.strip(), re.I))


def _main(caller: Caller) -> Role | None:
    return next((r for r in ORDER if r in caller.roles), None)


def _join(items: list[str], last: str = "and") -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {last} " + items[-1]


def _duties(roles: frozenset[Role]) -> list[str]:
    out: list[str] = []
    for role in ORDER:
        if role in roles:
            out += [d for d in DUTIES.get(role, ()) if d not in out]
    return out


def help_for(caller: Caller) -> str:
    """What this caller can ask, with examples for their role."""
    role = _main(caller)
    if role is None:
        return "Ask me about crew control: rosters, duty hours, legality, disruptions or a rule."
    return f"As {NAMES[role]}, you can ask, for example: {ASK[role]}."


def _you(text: str) -> str:
    return re.sub(r"\btheir\b", "your", re.sub(r"\bthey're\b", "you're", text))


def _can(role: Role) -> str:
    """What a role can do, as "can ..." (a super admin's said as an admin's plus two)."""
    if role == Role.SUPER_ADMIN:
        extra = [d for d in DUTIES[role] if d not in DUTIES[Role.ADMIN]]
        return f"can do everything an admin can, and also {_join(extra)}"
    return f"can {_join(list(DUTIES.get(role, ())))}"


def _compare(mine: frozenset[Role], theirs: Role, *, mine_first: bool) -> str:
    you, them = _duties(mine), _duties(frozenset({theirs}))
    name = NAMES[theirs]
    if theirs in mine:
        return f"You're {name} yourself."
    if mine_first:
        extra = [d for d in you if d not in them]
        if not extra:
            more = [d for d in them if d not in you]
            also = f" and also {_join(more)}" if more else ""
            return f"Nothing: {name} can do everything you can{also}."
        return f"You can {_you(_join(extra))}; {name} can't."
    extra = [d for d in them if d not in you]
    if not extra:
        return f"Nothing: you can do everything {name} can."
    return f"{name[0].upper()}{name[1:]} can {_join(extra)}; you can't."


def about_roles(question: str, caller: Caller) -> str | None:
    """The answer to a question about the assistant or the roles, or None to route as usual."""
    bare = _bare(question)
    if not bare:
        return None
    if WHO_ARE_YOU_RE.match(bare):
        return (
            "I'm CrewOps, the crew-control assistant for Deccan Express, Konkan Skyways and "
            "Nilgiri Connect. I'm here to help keep every flight legally crewed. "
            + help_for(caller)
        )
    if HELP_RE.match(bare):
        duties = _duties(caller.roles)
        role = _main(caller)
        if not duties or role is None:
            return help_for(caller)
        return f"As {NAMES[role]}, you {_you(_can(role))}. Try asking: {ASK[role]}."
    if m := MINE_NOT_THEIRS_RE.search(bare):
        return _compare(caller.roles, _role_of(m.group("role")), mine_first=True)
    if m := THEIRS_NOT_MINE_RE.search(bare):
        return _compare(caller.roles, _role_of(m.group("role")), mine_first=False)
    if m := DIFFERENCE_RE.search(bare):
        a, b = _role_of(m.group(2)), _role_of(m.group(4))
        if a == b:
            return None
        only_a = [d for d in _duties(frozenset({a})) if d not in _duties(frozenset({b}))]
        only_b = [d for d in _duties(frozenset({b})) if d not in _duties(frozenset({a}))]
        parts = []
        if only_a:
            parts.append(f"Only {NAMES[a]} can {_join(only_a)}.")
        if only_b:
            parts.append(f"Only {NAMES[b]} can {_join(only_b)}.")
        return " ".join(parts) or None
    if m := WHAT_DOES_RE.match(bare):
        role = _role_of(m.group(3) or m.group(5))
        return f"{NAMES[role][0].upper()}{NAMES[role][1:]} {_can(role)}."
    if Role.CREW in caller.roles and CREW_OUT_RE.search(bare):
        return (
            "I can't change your roster. Call crew control or the reporting desk: they record it, "
            "and crew control finds legal cover for your trips within minutes."
        )
    if HOW_LEAVE_RE.match(bare):
        if caller.roles & {Role.ADMIN, Role.SUPER_ADMIN}:
            return (
                "On the Admin page in the CrewOps console, under Planned leave: pick the crew "
                "member, the dates and the reason. Their trips on those days are re-crewed ahead."
            )
        return "Planned leave is booked by an admin, on the Admin page in the CrewOps console."
    if HOW_REPORT_RE.match(bare):
        if caller.roles & FILERS:
            return (
                "Open Report in the CrewOps console, choose what happened (sick crew, a delay, a "
                "gate change, a gate closure or an aircraft fault), fill in the flight or crew "
                "member, and send. Crew control see it with ranked legal cover within seconds."
            )
        return (
            "Reports are filed by the reporting desk, a controller or an approver, from Report "
            "in the CrewOps console."
        )
    if (m := WHO_DOES_RE.match(bare)) and not re.search(r"\d", bare):
        # "who approves plans" reads as "who can approve plans"
        bare = f"who can {m.group(1)} {bare[m.end() :].strip()}".strip()
    if CAN_I_RE.search(bare):
        for pattern, action, who in ACTIONS:
            if not pattern.search(bare):
                continue
            if not who:
                return f"No one can {action}."
            names = _join([NAMES[r] for r in ORDER if r in who], "or")
            if bare.startswith("who"):
                return f"{names[0].upper()}{names[1:]} can {action}."
            if caller.roles & who:
                return f"Yes: you can {action}."
            return f"No: to {action}, you need to be {names}."
    return None


__all__ = ["about_roles", "help_for"]
