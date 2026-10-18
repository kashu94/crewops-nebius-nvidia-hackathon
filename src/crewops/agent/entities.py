"""Pull ids, dates, stations, roles and amounts out of a question, by pattern only.

``C-1042`` and ``C-1024`` look almost identical to an embedding model, and resolving the
wrong captain is the worst mistake this system can make. So identifiers are never found by
similarity or by asking a model. Patterns here decide which things the question names; the
router decides what kind of question it is.

Nothing here decides that a name is a person or that an id exists. The tools do that, and
ask when something does not match.
"""

from __future__ import annotations

import calendar
import difflib
import re
from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from typing import Any

from crewops.engine import World

# --------------------------------------------------------------------------
# What the operation contains
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Stations, tails and dates of the loaded operation, i.e. what a bare word can mean."""

    stations: frozenset[str]
    aircraft: frozenset[str]
    week_start: date
    week_end: date
    today: date
    """The operation's "now", the snapshot date the duty clocks were computed at."""

    @classmethod
    def from_world(cls, world: World) -> Vocabulary:
        flights = list(world.flights.values())
        stations = {f.dep_station for f in flights} | {f.arr_station for f in flights}
        dates = sorted({f.date for f in flights})
        return cls(
            stations=frozenset(stations),
            aircraft=frozenset(f.aircraft for f in flights),
            week_start=dates[0],
            week_end=dates[-1],
            today=world.snapshot_utc.date(),
        )


# --------------------------------------------------------------------------
# Spoken ids
# --------------------------------------------------------------------------

_D = r"(\d)[\s,.-]*"
_END = r"\b(?![\s,./-]*\d)"  # last digit of the id; more digits would make it a date
SPOKEN_CREW_RE = re.compile(rf"\b(?:c|see|sea|cee|si)\b[\s.-]*{_D}{_D}{_D}(\d){_END}", re.I)
SPOKEN_PAIRING_RE = re.compile(rf"\b(?:p|pee|pea)\b[\s.-]*{_D}{_D}{_D}(\d){_END}", re.I)
AIRLINE_CODES = ("DX", "KS", "NG")
"""Same codes as ``data/seed/airlines.json``. A test keeps them in step."""
_SPOKEN_CODE = {
    "DX": r"d\.?\s*x|dee\s*ex|d\s*ex",
    "KS": r"k\.?\s*s|kay\s*ess|k\s*ess",
    "NG": r"n\.?\s*g|en\s*gee|n\s*gee",
}
SPOKEN_FLIGHT_RE = re.compile(
    rf"\b({'|'.join(f'(?P<{c}>{p})' for c, p in _SPOKEN_CODE.items())})\.?[\s-]*"
    rf"{_D}{_D}(\d)\b(?![\s-]*\d)",
    re.I,
)
SPOKEN_TAIL_RE = re.compile(r"\bvt[\s.-]*(d[\s.-]*x[\s.-]*[a-f])\b", re.I)
LOWER_ID_RE = re.compile(rf"\b([cp])-(\d+)\b|\b({'|'.join(AIRLINE_CODES)})(\d{{3}})\b", re.I)


def _spoken_flight(m: re.Match[str]) -> str:
    code = next(c for c in _SPOKEN_CODE if m.group(c))
    return code + "".join(d for d in m.groups()[-3:])


def spoken_ids(text: str) -> str:
    """Rewrite ids the way speech recognition writes them into the form the patterns read.

    Voice input turns "C-3231" into "see 3231", "C 32 31" or "c3231", and "DX412" into
    "D X 412". Only the exact shapes of an id are rewritten (a letter or its sound, then
    four digits for crew and pairings, three for flights), so "see you at 10" is left
    alone. Whether the id exists is still up to the tools.
    """
    text = SPOKEN_CREW_RE.sub(lambda m: "C-" + "".join(m.groups()), text)
    text = re.sub(r"\bc(\d{4})\b", r"C-\1", text, flags=re.I)
    text = SPOKEN_PAIRING_RE.sub(lambda m: "P-" + "".join(m.groups()), text)
    text = re.sub(r"\bp(\d{4})\b", r"P-\1", text, flags=re.I)
    text = SPOKEN_FLIGHT_RE.sub(_spoken_flight, text)
    text = SPOKEN_TAIL_RE.sub(lambda m: "VT-" + re.sub(r"[\s.-]", "", m.group(1)).upper(), text)
    return LOWER_ID_RE.sub(
        lambda m: (
            f"{m.group(1).upper()}-{m.group(2)}"
            if m.group(1)
            else f"{m.group(3).upper()}{m.group(4)}"
        ),
        text,
    )


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

CREW_RE = re.compile(r"\bC-\d{4}\b")
MALFORMED_CREW_RE = re.compile(r"\bC-(?!\d{4}\b)\d+\b")
"""Crew prefix with the wrong number of digits ("C-10"). Gets near matches offered, is
never looked up."""
PAIRING_RE = re.compile(r"\bP-\d{4}\b")
MALFORMED_PAIRING_RE = re.compile(r"\bP-(?!\d{4}\b)\d+\b")
FLIGHT_ID_RE = re.compile(r"\b[A-Z]{2}\d{3}-\d{4}-\d{2}-\d{2}\b")
FLIGHT_NO_RE = re.compile(r"\b[A-Z]{2}\d{3}\b")
"""An airline's two-letter code and three digits (DX401, KS431, NG451)."""
MALFORMED_FLIGHT_NO_RE = re.compile(rf"\b(?:{'|'.join(AIRLINE_CODES)})(?!\d{{3}}\b)\d+\b")
RULE_RE = re.compile(r"\bRULE-[A-Z]{3,4}-\d{2}\b")
AIRCRAFT_RE = re.compile(r"\bVT-[A-Z]{3}\b")
AC_TYPE_RE = re.compile(r"\b(A320|ATR[- ]?72)\b", re.I)
STATION_RE = re.compile(r"\b[A-Z]{3}\b")
GATE_RE = re.compile(r"\b([A-Z]{3}-G\d{1,2})\b", re.I)
ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
"""Matches inside a timestamp too, so "2026-10-14T08:30:00Z" names 14 Oct."""
TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*Z?\b", re.I)
TODAY_RE = re.compile(r"\b(today|right now|currently|as of now)\b", re.I)
TOMORROW_RE = re.compile(r"\btomorrow\b", re.I)

DELAY_RE = re.compile(
    r"\bdelay(?:ed)?\b[^.?!\n]{0,20}?(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b|minutes?|mins?|m\b)"
    r"|(\d+(?:\.\d+)?)\s*[- ]?(hours?|hrs?|h\b|minutes?|mins?|m\b)[^.?!\n]{0,20}?\bdelay(?:ed)?\b",
    re.I,
)

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS["sept"] = 9
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\b\.?(?:,?\s+(\d{{4}}))?", re.I
)
_MONTH_FIRST_ALT = "|".join(m for m in sorted(_MONTHS, key=len, reverse=True) if m != "may")
MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_FIRST_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I
)
"""Month first ("Oct 15"). Leaves out "may", since "who may 3 captains..." is not 3 May."""
BETWEEN_DAYS_RE = re.compile(
    rf"\bbetween\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+and\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"({_MONTH_ALT})\b",
    re.I,
)
""""between 13 and 15 Oct" gives both ends."""
DAY_RANGE_RE = re.compile(rf"\b(\d{{1,2}})\s*[–-]\s*(\d{{1,2}})\s+({_MONTH_ALT})\b", re.I)
""""13–14 Oct" gives both days."""
DATE_TEXT = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_ALT})\b"
    rf"|(?:{_MONTH_ALT})\.?\s+\d{{1,2}}\b)"
)
"""Any written date, for checking what a word like "before" is attached to."""
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RE = re.compile(rf"\b({'|'.join(WEEKDAYS)})\b", re.I)
BARE_DAY_RE = re.compile(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I)

ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Senior Cabin Crew", re.compile(r"\b(senior cabin crew|sccs?|pursers?)\b", re.I)),
    ("First Officer", re.compile(r"\b(first officers?|f/?os?|co-?pilots?)\b", re.I)),
    ("Captain", re.compile(r"\b(captains?|capts?|cpts?|commanders?)\b", re.I)),
    ("Cabin Crew", re.compile(r"(?<!senior )\b(cabin crew|flight attendants?)\b", re.I)),
)

CERT_RE = re.compile(
    r"\b(dangerous[_ ]goods|licen[cs]e|medical(?:[_ ]class ?1)?|recurrent[_ ]training)\b", re.I
)
CERT_NAMES = {
    "dangerous goods": "dangerous_goods",
    "dangerous_goods": "dangerous_goods",
    "licence": "licence",
    "license": "licence",
    "medical": "medical_class1",
    "recurrent training": "recurrent_training",
    "recurrent_training": "recurrent_training",
}

STATION_CONTEXT_RE = re.compile(
    r"\b(from|to|at|via|in|into|out of|station|based|base|depart\w*|arriv\w*)\s+$", re.I
)
STATION_FALSE_FRIENDS = frozenset(
    {
        "FDP",
        "UTC",
        "AND",
        "THE",
        "FOR",
        "WHO",
        "NOT",
        "ALL",
        "ANY",
        "CAN",
        "HOW",
        "AOG",
        "SCC",
        "ETA",
        "ETD",
        "STD",
        "STA",
        "PAX",
        "FAQ",
        "YES",
        "OFF",
        "NOW",
        "WHY",
        "ASAP",
        "GMT",
        "IST",
        "SMS",
        "FYI",
        "CEO",
    }
)
"""Upper-case three-letter words that are not attempts at a station code."""

STATION_ALIASES: dict[str, str] = {
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "bombay": "BOM",
    "mumbai": "BOM",
    "calcutta": "CCU",
    "kolkata": "CCU",
    "cochin": "COK",
    "kochi": "COK",
    "delhi": "DEL",
    "new delhi": "DEL",
    "goa": "GOI",
    "hyderabad": "HYD",
    "chennai": "MAA",
    "madras": "MAA",
}
"""City names people say instead of codes. Fixed lookup, no fuzzy matching."""
CITY_RE = re.compile(
    r"\b(" + "|".join(sorted(STATION_ALIASES, key=len, reverse=True)) + r")\b", re.I
)

PERSON_RE = re.compile(r"\b([A-Z]\.\s*[A-Z][a-z]{2,})\b|\b([A-Z][a-z]{2,})\b")
NOT_A_NAME = frozenset(
    [
        "The",
        "This",
        "That",
        "Who",
        "Which",
        "What",
        "When",
        "Where",
        "Why",
        "How",
        "Can",
        "Does",
        "Draft",
        "List",
        "Show",
        "Give",
        "Find",
        "Move",
        "Captain",
        "First",
        "Officer",
        "Senior",
        "Cabin",
        "Crew",
        "Reserve",
        "Pairing",
        "Flight",
        "Rule",
        "Duty",
        "Sick",
        "Both",
        "Please",
        "Would",
        "Should",
        "Their",
        "There",
        "They",
        "Available",
        "Legal",
        "Any",
        "All",
        "Next",
        "Cheapest",
        "Other",
        "Another",
        "Option",
        "Options",
        "Same",
        "Take",
        "Assign",
        "Call",
        "Book",
        "Choose",
        "Pick",
        "Instead",
        "Cost",
        "Alternative",
        "Second",
        "Third",
        "Last",
        "Best",
        "Station",
        "Gate",
        "Alexa",
        "Is",
        "Are",
        "Was",
        "Were",
        "If",
        "Has",
        "Have",
        "After",
        "Before",
        "Since",
        "Today",
        "Tomorrow",
        "Tell",
        "Get",
        "Let",
        "Check",
        "Whose",
        "Many",
        "Much",
        "Produce",
        "Outline",
        "Resolve",
        "Recovery",
        "Total",
        "Control",
        "Controller",
        "Controllers",
        "Desk",
        "Sep",
        "September",
        "Oct",
        "October",
        "Hello",
        "Hi",
        "Thanks",
        "Yes",
        "No",
        "Okay",
        "Our",
        "My",
        "Its",
        "Need",
        "Needs",
        "Will",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "May",
        "Am",
        "Flights",
        "Report",
        "Reports",
        "Pairings",
        "Hours",
        "Rest",
        "Legality",
        "Cover",
        "Covers",
        "Rated",
        "Play",
        "Set",
        "Order",
        "Translate",
        "Summarise",
        "Summarize",
        "Did",
        "Sept",
        "Send",
        "Remind",
        "Open",
        "Turn",
        "Start",
        "Stop",
    ]
)

HORIZON_RE = re.compile(
    r"\b(?:with)?in\s+(?:the\s+)?(?:next\s+)?(\d{1,3})\s*(day|week|month)s?\b"
    r"|\bnext\s+(\d{1,3})\s*(day|week|month)s?\b",
    re.I,
)
HORIZON_DAYS = {"day": 1, "week": 7, "month": 30}

DATE_OPS: tuple[tuple[str, str], ...] = (
    ("on or before", "lte"),
    ("on or after", "gte"),
    ("prior to", "lt"),
    ("earlier than", "lt"),
    ("up to", "lte"),
    ("through", "lte"),
    ("later than", "gt"),
    ("since", "gte"),
    ("before", "lt"),
    ("after", "gt"),
)
"""A comparison word beside a date makes it a range. Longest phrase first, or "on or
before" would match as a bare "before"."""


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Entities:
    """Everything a question names, deduplicated, in the order it was said."""

    crew_ids: list[str] = field(default_factory=list)
    malformed_crew_ids: list[str] = field(default_factory=list)
    pairing_ids: list[str] = field(default_factory=list)
    malformed_pairing_ids: list[str] = field(default_factory=list)
    flight_ids: list[str] = field(default_factory=list)
    flight_nos: list[str] = field(default_factory=list)
    malformed_flight_nos: list[str] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    aircraft: list[str] = field(default_factory=list)
    aircraft_types: list[str] = field(default_factory=list)
    stations: list[str] = field(default_factory=list)
    unknown_stations: list[str] = field(default_factory=list)
    """Upper-case three-letter words that look like a station code but aren't one. We ask
    about these rather than drop them, or "flights from BLR to XYZ" quietly becomes
    "from BLR"."""
    dates: list[str] = field(default_factory=list)
    date_range: dict[str, str] | None = None
    times: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    cert_types: list[str] = field(default_factory=list)
    gate_numbers: list[str] = field(default_factory=list)
    delay_minutes: float | None = None
    horizon_days: int | None = None
    names: list[str] = field(default_factory=list)
    """Words that may be a crew member's name. Only the roster decides."""

    def to_dict(self) -> dict[str, Any]:
        return {f.name: v for f in fields(self) if (v := getattr(self, f.name))}

    @property
    def crew(self) -> str | None:
        return self.crew_ids[0] if self.crew_ids else None

    @property
    def pairing(self) -> str | None:
        return self.pairing_ids[0] if self.pairing_ids else None

    @property
    def date(self) -> str | None:
        return self.dates[0] if self.dates else None

    @property
    def role(self) -> str | None:
        return self.roles[0] if self.roles else None


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------


def _mk_date(day: int, month: int, year: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_dates(text: str, vocab: Vocabulary) -> list[str]:
    """ISO dates. A day without a year is in the operation's year. "The 15th", "on Friday"
    and "today" look ahead from the operation's "now" to the next such day in the rostered
    period."""
    year = max(vocab.today, vocab.week_start).year
    found: list[tuple[int, str]] = []
    for m in ISO_DATE_RE.finditer(text):
        if iso := _mk_date(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            found.append((m.start(), iso))
    for m in BETWEEN_DAYS_RE.finditer(text):
        month = _MONTHS[m.group(3).lower()]
        for day in (m.group(1), m.group(2)):
            if iso := _mk_date(int(day), month, year):
                found.append((m.start(), iso))
    for m in DAY_RANGE_RE.finditer(text):
        month = _MONTHS[m.group(3).lower()]
        for day in range(int(m.group(1)), int(m.group(2)) + 1):
            if iso := _mk_date(day, month, year):
                found.append((m.start(), iso))
    for m in DAY_MONTH_RE.finditer(text):
        y = int(m.group(3)) if m.group(3) else year
        if iso := _mk_date(int(m.group(1)), _MONTHS[m.group(2).lower()], y):
            found.append((m.start(), iso))
    for m in MONTH_DAY_RE.finditer(text):
        y = int(m.group(3)) if m.group(3) else year
        if iso := _mk_date(int(m.group(2)), _MONTHS[m.group(1).lower()], y):
            found.append((m.start(), iso))
    if not found:
        for m in BARE_DAY_RE.finditer(text):
            if iso := _next_day_of_month(int(m.group(1)), vocab):
                found.append((m.start(), iso))
    if not found:
        # "on Friday" means the next Friday from today
        for m in WEEKDAY_RE.finditer(text):
            wanted = WEEKDAYS.index(m.group(1).lower())
            day = max(vocab.today, vocab.week_start)
            while day <= vocab.week_end:
                if day.weekday() == wanted:
                    found.append((m.start(), day.isoformat()))
                    break
                day += timedelta(days=1)
    if not found and TODAY_RE.search(text):
        found.append((0, vocab.today.isoformat()))
    if not found and TOMORROW_RE.search(text):
        found.append((0, (vocab.today + timedelta(days=1)).isoformat()))
    return _dedupe([iso for _, iso in sorted(found, key=lambda t: t[0])])


def _next_day_of_month(day: int, vocab: Vocabulary) -> str | None:
    """ "The 15th" is this month's if still to come, else next month's, within the period."""
    start = max(vocab.today, vocab.week_start)
    month, year = start.month, start.year
    for _ in range(3):
        iso = _mk_date(day, month, year)
        if iso and start.isoformat() <= iso <= vocab.week_end.isoformat():
            return iso
        month, year = (1, year + 1) if month == 12 else (month + 1, year)
    return None


def extract_date_range(text: str, dates: list[str]) -> dict[str, str] | None:
    """ "flights before 17 Oct" is a range, not the same question as "flights on 17 Oct"."""
    if not dates:
        return None
    if re.search(r"\bbetween\b", text, re.I) and len(dates) >= 2:
        lo, hi = sorted(dates[:2])
        return {"gte": lo, "lte": hi}
    for phrase, op in DATE_OPS:
        # Only when the word sits right before a date. "delayed before DX401 on 14 Oct" is
        # about one day, not a range.
        if re.search(rf"\b{re.escape(phrase)}\s+(?:the\s+)?{DATE_TEXT}", text, re.I):
            return {op: dates[0]}
    return None


def extract_times(text: str) -> list[str]:
    out = []
    for hh, mm in TIME_RE.findall(text):
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            out.append(f"{h:02d}:{m:02d}")
    return _dedupe(out)


def extract_delay_minutes(text: str) -> float | None:
    """ "delayed by 90 minutes", "a 2 hour delay" -> minutes."""
    m = DELAY_RE.search(text)
    if not m:
        return None
    value, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    minutes = float(value) * (60 if unit.lower().startswith("h") else 1)
    return minutes if minutes > 0 else None


def extract_horizon(text: str) -> int | None:
    """ "within 30 days", "next 2 weeks" -> days. A month counts as 30."""
    m = HORIZON_RE.search(text)
    if not m:
        return None
    count, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return int(count) * HORIZON_DAYS[unit.lower()]


def extract_names(text: str) -> list[str]:
    found = []
    for full, bare in PERSON_RE.findall(text):
        value = (full or bare).strip()
        if value and value not in NOT_A_NAME and value.lower() not in STATION_ALIASES:
            found.append(re.sub(r"\.\s*", ". ", value))
    return _dedupe(found)


def extract(text: str, vocab: Vocabulary) -> Entities:
    flight_ids = FLIGHT_ID_RE.findall(text)
    in_ids = " ".join(flight_ids)
    flight_nos = [n for n in FLIGHT_NO_RE.findall(text) if n not in in_ids]

    tails = AIRCRAFT_RE.findall(text)
    gates = [g.upper() for g in GATE_RE.findall(text)]
    stations, unknown = [], []
    for m in STATION_RE.finditer(text):
        word = m.group()
        if word in STATION_FALSE_FRIENDS:
            continue
        if text[max(0, m.start() - 3) : m.start()] == "VT-":
            continue  # the letters of a tail, not a station
        if word == "ATR" and re.match(r"-?72", text[m.end() :]):
            continue  # the aircraft type
        if word in vocab.stations:
            stations.append(word)
        elif STATION_CONTEXT_RE.search(text[max(0, m.start() - 14) : m.start()]) or (
            difflib.get_close_matches(word, sorted(vocab.stations), n=1, cutoff=0.67)
        ):
            # Count it as a station attempt only after "from", "to", "at"... or when it's one
            # letter off a real code. "FYI the CEO asked" is not about stations.
            unknown.append(word)
    stations += [STATION_ALIASES[c.lower()] for c in CITY_RE.findall(text)]

    dates = extract_dates(text, vocab)
    return Entities(
        crew_ids=_dedupe(CREW_RE.findall(text)),
        malformed_crew_ids=_dedupe(MALFORMED_CREW_RE.findall(text)),
        pairing_ids=_dedupe(PAIRING_RE.findall(text)),
        malformed_pairing_ids=_dedupe(MALFORMED_PAIRING_RE.findall(text)),
        flight_ids=_dedupe(flight_ids),
        flight_nos=_dedupe(flight_nos),
        malformed_flight_nos=_dedupe(MALFORMED_FLIGHT_NO_RE.findall(text)),
        rule_ids=_dedupe(RULE_RE.findall(text)),
        aircraft=_dedupe(tails),
        aircraft_types=_dedupe([re.sub(r"[- ]", "", t.upper()) for t in AC_TYPE_RE.findall(text)]),
        stations=_dedupe(stations),
        unknown_stations=_dedupe(unknown),
        dates=dates,
        date_range=extract_date_range(text, dates),
        times=extract_times(text),
        roles=[name for name, pattern in ROLE_PATTERNS if pattern.search(text)],
        cert_types=_dedupe(
            [CERT_NAMES.get(c.lower().replace("_", " "), c.lower()) for c in CERT_RE.findall(text)]
        ),
        gate_numbers=_dedupe(gates),
        delay_minutes=extract_delay_minutes(text),
        horizon_days=extract_horizon(text),
        names=extract_names(text),
    )


# --------------------------------------------------------------------------
# Things the question states about someone, which the roster can contradict
# --------------------------------------------------------------------------

RANK_WORDS = (
    r"captain|cpt|capt|commander|first officer|f\.?/?o\.?|co-?pilot|"
    r"senior cabin crew|scc|purser|cabin crew|flight attendant"
)
RANK_OF = {
    "captain": "Captain",
    "cpt": "Captain",
    "capt": "Captain",
    "commander": "Captain",
    "first officer": "First Officer",
    "fo": "First Officer",
    "copilot": "First Officer",
    "co-pilot": "First Officer",
    "senior cabin crew": "Senior Cabin Crew",
    "scc": "Senior Cabin Crew",
    "purser": "Senior Cabin Crew",
    "cabin crew": "Cabin Crew",
    "flight attendant": "Cabin Crew",
}
STATED_RANK_RE = re.compile(rf"\b({RANK_WORDS})\s+(C-\d{{4}})\b", re.I)
"""A rank describing a named crew member ("FO C-2087"), as opposed to a seat to fill
("as Captain")."""
_BASE_BEFORE_ID_RE = re.compile(r"\b([A-Z]{3})-based\b[^.?!]{0,40}?\b(C-\d{4})\b")
_ID_BEFORE_BASE_RE = re.compile(
    r"\b(C-\d{4})\b[^.?!]{0,40}?\bbased\s+(?:at|in|out of)\s+([A-Z]{3})\b"
)
_ID_PAREN_BASE_RE = re.compile(r"\b(C-\d{4})\s*\(([A-Z]{3})[- ]?based?\)")
""""C-2210 (DEL base)"."""
_RATING_RE = re.compile(r"\b(A320|ATR-?72)-rated\b[^.?!]{0,40}?\b(C-\d{4})\b", re.I)
_ID_RATING_RE = re.compile(r"\b(C-\d{4})\b[^.?!]{0,40}?\bis\s+(A320|ATR-?72)-rated\b", re.I)


def _rank_key(word: str) -> str:
    return word.lower().replace(".", "").replace("/", "").strip()


def stated_ranks(text: str) -> list[tuple[str, str]]:
    """(crew id, rank the question says they hold)."""
    out = []
    for word, crew_id in STATED_RANK_RE.findall(text):
        if rank := RANK_OF.get(_rank_key(word)):
            out.append((crew_id, rank))
    return out


def stated_bases(text: str, vocab: Vocabulary) -> list[tuple[str, str]]:
    """(crew id, station the question says they are based at), e.g. "C-2210 (DEL base)"."""
    out = [(c, s) for s, c in _BASE_BEFORE_ID_RE.findall(text)]
    out += [(c, s) for c, s in _ID_BEFORE_BASE_RE.findall(text)]
    out += [(c, s) for c, s in _ID_PAREN_BASE_RE.findall(text)]
    return [(c, s) for c, s in out if s in vocab.stations]


def stated_ratings(text: str) -> list[tuple[str, str]]:
    """(crew id, aircraft type the question says they are rated on)."""
    out = [(c, r.upper().replace("-", "")) for r, c in _RATING_RE.findall(text)]
    out += [(c, r.upper().replace("-", "")) for c, r in _ID_RATING_RE.findall(text)]
    return out
