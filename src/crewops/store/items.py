"""Convert between typed records and DynamoDB items.

Each item carries its keys (see ``schema``), an ``entity`` name, and the record's fields in
the same shape as the seed JSON. Reading an item back therefore goes through the same
``from_dict`` parsers that validate the seed, so data in the table is checked exactly as
strictly as data on disk.

DynamoDB has no float or datetime type: numbers become ``Decimal`` and instants become
ISO-8601 UTC strings on the way in, and the reverse on the way out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from crewops.domain import (
    Certification,
    CostTable,
    Crew,
    DataError,
    DutyClock,
    Flight,
    GateSlot,
    Pairing,
    ReserveDuty,
    RiskSignal,
    RosterException,
    Rule,
)
from crewops.domain.airlines import Airline, EmergencyGate
from crewops.domain.contacts import Contact
from crewops.store import schema
from crewops.store.schema import SEPARATOR as S

ENTITY = "entity"
KEY_ATTRIBUTES = frozenset(
    {schema.PK, schema.SK, schema.GSI1_PK, schema.GSI1_SK, schema.GSI2_PK, schema.GSI2_SK}
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def to_dynamo(value: Any) -> Any:
    """A plain Python value in a form boto3 can write."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_dynamo(v) for v in value]
    raise TypeError(f"cannot store {type(value).__name__}")


def from_dynamo(value: Any) -> Any:
    """Undo boto3's ``Decimal`` so records parse exactly as from JSON."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Record -> seed-shaped dict
# --------------------------------------------------------------------------


def _crew(c: Crew) -> dict[str, Any]:
    return {
        "crew_id": c.crew_id,
        "name": c.name,
        "rank": c.rank,
        "base": c.base,
        "ratings": list(c.ratings),
        "seniority": c.seniority,
        "reachability_minutes": c.reachability_minutes,
        "status": c.status,
        **({"airline": c.airline} if c.airline else {}),
        **({"experience_years": c.experience_years} if c.experience_years is not None else {}),
        **({"command_qualified": True} if c.command_qualified else {}),
        **({"right_seat_qualified": True} if c.right_seat_qualified else {}),
    }


def _airline(a: Airline) -> dict[str, Any]:
    return {"code": a.code, "name": a.name, "aircraft": list(a.aircraft), "color": a.color}


def _emergency(g: EmergencyGate) -> dict[str, Any]:
    return {"gate": g.gate, "station": g.station}


def _cert(c: Certification) -> dict[str, Any]:
    return {
        "crew_id": c.crew_id,
        "cert_type": c.cert_type,
        "valid_from": c.valid_from,
        "valid_to": c.valid_to,
    }


def _clock(c: DutyClock) -> dict[str, Any]:
    return {
        "crew_id": c.crew_id,
        "as_of_utc": c.as_of_utc,
        "duty_hours_7d": c.duty_hours_7d,
        "flight_hours_28d": c.flight_hours_28d,
        "last_rest_ended": c.last_rest_ended,
        "daily_history": [
            {"date": h.date, "duty_hours": h.duty_hours, "flight_hours": h.flight_hours}
            for h in c.daily_history
        ],
    }


def _reserve(r: ReserveDuty) -> dict[str, Any]:
    return {
        "crew_id": r.crew_id,
        "base": r.base,
        "dates": list(r.dates),
        "oncall_window_utc": {"start": _hhmm(r.window_start), "end": _hhmm(r.window_end)},
    }


def _risk(r: RiskSignal) -> dict[str, Any]:
    return {
        "crew_id": r.crew_id,
        "as_of_utc": r.as_of_utc,
        "disruption_risk_score": r.score,
        "drivers": list(r.drivers),
    }


def _flight(f: Flight) -> dict[str, Any]:
    return {
        "flight_id": f.flight_id,
        "flight_no": f.flight_no,
        "date": f.date,
        "dep_station": f.dep_station,
        "arr_station": f.arr_station,
        "dep_utc": f.dep_utc,
        "arr_utc": f.arr_utc,
        "block_hours": f.block_hours,
        "aircraft": f.aircraft,
        "aircraft_type": f.aircraft_type,
        "seats": f.seats,
        **({"booked": f.booked} if f.booked is not None else {}),
    }


def _contact(c: Contact) -> dict[str, Any]:
    return {"team": c.team, "scope": c.scope, "name": c.name, "title": c.title}


def _pairing(p: Pairing) -> dict[str, Any]:
    return {
        "pairing_id": p.pairing_id,
        "aircraft": p.aircraft,
        "days": [
            {
                "date": d.date,
                "flights": list(d.flight_ids),
                "report_utc": d.report_utc,
                "release_utc": d.release_utc,
            }
            for d in p.days
        ],
        "crew": [{"crew_id": s.crew_id, "role": s.role} for s in p.crew],
    }


def _exception(e: RosterException) -> dict[str, Any]:
    return {"crew_id": e.crew_id, "date": e.date, "rule": e.rule_id, "note": e.note}


def _gate(g: GateSlot) -> dict[str, Any]:
    return {
        "boarding_gate_number": g.gate,
        "pairing_id": g.pairing_id,
        "flight_id": g.flight_id,
        "date": g.date,
        "boarding_start_time": g.start_utc,
        "boarding_end_time": g.end_utc,
        "aircraft_type": g.aircraft_type,
    }


def _rule(r: Rule) -> dict[str, Any]:
    return {"rule_id": r.rule_id, "text": r.text, "params": dict(r.params)}


def _costs(c: CostTable) -> dict[str, Any]:
    return {
        "currency": c.currency,
        "reserve_callout_pilot": c.reserve_callout_pilot,
        "reserve_callout_cabin": c.reserve_callout_cabin,
        "dayoff_callout_pilot": c.dayoff_callout_pilot,
        "dayoff_callout_cabin": c.dayoff_callout_cabin,
        "deadhead_positioning": c.deadhead_positioning,
        "delay_cost_per_duty_hour": c.delay_per_duty_hour,
        "cancellation_per_flight": c.cancellation_per_flight,
        "hotel_overnight": c.hotel_overnight,
        "seniority_allowance_pilot": c.seniority_allowance_pilot,
        "seniority_allowance_cabin": c.seniority_allowance_cabin,
        "travel_per_10_minutes": c.travel_per_10_minutes,
    }


# --------------------------------------------------------------------------
# Keys per entity
# --------------------------------------------------------------------------


def _keys(pk: str, sk: str, gsi1: tuple[str, str] | None = None) -> dict[str, str]:
    keys = {schema.PK: pk, schema.SK: sk}
    if gsi1:
        keys[schema.GSI1_PK], keys[schema.GSI1_SK] = gsi1
    return keys


def _crew_keys(c: Crew) -> dict[str, str]:
    return _keys(
        f"CREW{S}{c.crew_id}", "PROFILE", (f"BASE{S}{c.base}", f"RANK{S}{c.rank}{S}{c.crew_id}")
    )


def _flight_keys(f: Flight) -> dict[str, str]:
    return _keys(
        f"FLIGHT{S}{f.flight_id}",
        "META",
        (f"DATE{S}{f.date.isoformat()}", f"DEP{S}{_iso(f.dep_utc)}{S}{f.flight_id}"),
    )


def _pairing_keys(p: Pairing) -> dict[str, str]:
    return _keys(
        f"PAIRING{S}{p.pairing_id}",
        "META",
        (f"AIRCRAFT{S}{p.aircraft}", f"DATE{S}{p.first_day.date.isoformat()}"),
    )


def _gate_keys(g: GateSlot) -> dict[str, str]:
    return _keys(
        f"GATE{S}{g.gate}",
        f"SLOT{S}{_iso(g.start_utc)}{S}{g.flight_id}",
        (f"FLIGHT{S}{g.flight_id}", "GATE"),
    )


class Codec:
    """How one entity is keyed, written and read."""

    def __init__(
        self,
        entity: str,
        keys: Callable[[Any], dict[str, str]],
        dump: Callable[[Any], dict[str, Any]],
        load: Callable[[dict[str, Any], str], Any],
    ) -> None:
        self.entity = entity
        self.keys = keys
        self.dump = dump
        self.load = load

    def to_item(self, record: Any) -> dict[str, Any]:
        item = to_dynamo(self.dump(record))
        clash = KEY_ATTRIBUTES.intersection(item) | ({ENTITY} & set(item))
        if clash:
            raise DataError(f"{self.entity}: field names clash with key attributes {clash}")
        return {**self.keys(record), ENTITY: self.entity, **item}

    def from_item(self, item: Mapping[str, Any]) -> Any:
        data = {
            k: from_dynamo(v) for k, v in item.items() if k not in KEY_ATTRIBUTES and k != ENTITY
        }
        return self.load(data, f"{self.entity}[{item.get(schema.PK)}|{item.get(schema.SK)}]")


CODECS: dict[type, Codec] = {
    Crew: Codec("crew", _crew_keys, _crew, Crew.from_dict),
    Certification: Codec(
        "certification",
        lambda c: _keys(f"CREW{S}{c.crew_id}", f"CERT{S}{c.cert_type}"),
        _cert,
        Certification.from_dict,
    ),
    DutyClock: Codec(
        "duty_clock",
        lambda c: _keys(f"CREW{S}{c.crew_id}", "CLOCK"),
        _clock,
        DutyClock.from_dict,
    ),
    ReserveDuty: Codec(
        "reserve",
        lambda r: _keys(f"CREW{S}{r.crew_id}", f"RESERVE{S}{r.base}"),
        _reserve,
        ReserveDuty.from_dict,
    ),
    RiskSignal: Codec(
        "risk_signal",
        lambda r: _keys(f"CREW{S}{r.crew_id}", "RISK"),
        _risk,
        RiskSignal.from_dict,
    ),
    RosterException: Codec(
        "roster_exception",
        lambda e: _keys(f"CREW{S}{e.crew_id}", f"EXCEPTION{S}{e.date.isoformat()}{S}{e.rule_id}"),
        _exception,
        RosterException.from_dict,
    ),
    Flight: Codec("flight", _flight_keys, _flight, Flight.from_dict),
    Pairing: Codec("pairing", _pairing_keys, _pairing, Pairing.from_dict),
    GateSlot: Codec("gate_slot", _gate_keys, _gate, GateSlot.from_dict),
    Rule: Codec("rule", lambda r: _keys("CONFIG", f"RULE{S}{r.rule_id}"), _rule, Rule.from_dict),
    CostTable: Codec("costs", lambda c: _keys("CONFIG", "COSTS"), _costs, CostTable.from_dict),
    Airline: Codec(
        "airline", lambda a: _keys("CONFIG", f"AIRLINE{S}{a.code}"), _airline, Airline.from_dict
    ),
    EmergencyGate: Codec(
        "emergency_gate",
        lambda g: _keys(f"GATE{S}{g.gate}", "EMERGENCY"),
        _emergency,
        EmergencyGate.from_dict,
    ),
    Contact: Codec(
        "contact",
        lambda c: _keys("CONFIG", f"CONTACT{S}{c.team}{S}{c.scope}"),
        _contact,
        Contact.from_dict,
    ),
}

BY_ENTITY: dict[str, Codec] = {codec.entity: codec for codec in CODECS.values()}


def record_dict(record: Any) -> dict[str, Any]:
    """A record in the seed JSON's own field names (values still typed)."""
    try:
        codec = CODECS[type(record)]
    except KeyError:
        raise TypeError(f"no codec for {type(record).__name__}") from None
    return codec.dump(record)


def to_item(record: Any) -> dict[str, Any]:
    try:
        codec = CODECS[type(record)]
    except KeyError:
        raise TypeError(f"no codec for {type(record).__name__}") from None
    return codec.to_item(record)


def from_item(item: Mapping[str, Any]) -> Any:
    entity = item.get(ENTITY)
    try:
        codec = BY_ENTITY[entity]
    except KeyError:
        raise DataError(f"unknown entity {entity!r} at {item.get(schema.PK)}") from None
    return codec.from_item(item)
