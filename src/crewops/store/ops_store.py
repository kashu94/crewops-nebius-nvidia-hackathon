"""Read and write the operation in the DynamoDB table.

The whole operating week is about 1,400 small items, so writes go through one batch
writer and a full read is a paginated scan. Both are cheap at this size: a full load reads
well under 1 MB. Targeted queries (a crew member's records, flights on a date) come with
the tools that need them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

import boto3

from crewops.config import Settings
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
from crewops.ingestion.seed import SeedData
from crewops.store import schema
from crewops.store.items import ENTITY, from_item, to_item

SORT_KEYS: dict[type, Any] = {
    Crew: lambda r: r.crew_id,
    Certification: lambda r: (r.crew_id, r.cert_type),
    DutyClock: lambda r: r.crew_id,
    ReserveDuty: lambda r: (r.crew_id, r.base),
    RiskSignal: lambda r: r.crew_id,
    RosterException: lambda r: (r.crew_id, r.date, r.rule_id),
    Flight: lambda r: r.flight_id,
    Pairing: lambda r: r.pairing_id,
    GateSlot: lambda r: (r.gate, r.start_utc, r.flight_id),
    Rule: lambda r: r.rule_id,
    Contact: lambda c: (c.team, c.scope),
    Airline: lambda a: a.code,
    EmergencyGate: lambda g: g.gate,
}
"""A scan returns items in hash order, so sort to make reads deterministic."""


def table_for(settings: Settings, session: boto3.Session | None = None) -> Any:
    """The boto3 Table resource for this stage, using the active AWS profile."""
    session = session or boto3.Session(region_name=settings.aws_region)
    return session.resource("dynamodb", region_name=settings.aws_region).Table(settings.table_name)


def seed_records(seed: SeedData) -> Iterator[Any]:
    """Every record in the seed, in a stable order."""
    yield from seed.crew
    yield from seed.certifications
    yield from seed.duty_clocks
    yield from seed.reserves
    yield from seed.risk_signals
    yield from seed.exceptions
    yield from seed.flights
    yield from seed.pairings
    yield from seed.gates
    yield from seed.rules
    yield seed.costs
    yield from seed.contacts
    yield from seed.airlines
    yield from seed.emergency_gates


class OpsStore:
    def __init__(self, table: Any) -> None:
        self.table = table

    def put_records(self, records: Iterable[Any]) -> Counter[str]:
        """Write records, replacing any item with the same keys. Returns counts by entity.

        Every record is converted and checked before the first write, so a bad batch is
        refused whole instead of being left half-written.
        """
        items = [to_item(record) for record in records]
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = (item[schema.PK], item[schema.SK])
            if key in seen:
                raise DataError(f"two records map to the same key {key}")
            seen.add(key)

        counts: Counter[str] = Counter()
        with self.table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
                counts[item[ENTITY]] += 1
        return counts

    def put_seed(self, seed: SeedData) -> Counter[str]:
        return self.put_records(seed_records(seed))

    def delete_all(self) -> int:
        """Delete every item in the table: for reloading a disposable stage from scratch."""
        keys = [{schema.PK: i[schema.PK], schema.SK: i[schema.SK]} for i in self.scan()]
        with self.table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)
        return len(keys)

    def scan(self) -> Iterator[dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        while True:
            page = self.table.scan(**kwargs)
            yield from page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def load_seed(self) -> SeedData:
        """Read the whole operation back in the shape the seed loader returns. Disruptions,
        plans, audit events and the like share the table and are skipped."""
        by_type: dict[type, list[Any]] = defaultdict(list)
        for item in self.scan():
            if item.get(ENTITY) not in _OPERATION_ENTITIES:
                continue
            record = from_item(item)
            by_type[type(record)].append(record)

        costs = by_type.get(CostTable, [])
        if len(costs) != 1:
            raise DataError(f"expected exactly one cost table, found {len(costs)}")

        def ordered(cls: type) -> tuple[Any, ...]:
            return tuple(sorted(by_type.get(cls, []), key=SORT_KEYS[cls]))

        return SeedData(
            crew=ordered(Crew),
            flights=ordered(Flight),
            pairings=ordered(Pairing),
            exceptions=ordered(RosterException),
            reserves=ordered(ReserveDuty),
            certifications=ordered(Certification),
            duty_clocks=ordered(DutyClock),
            risk_signals=ordered(RiskSignal),
            rules=ordered(Rule),
            costs=costs[0],
            gates=ordered(GateSlot),
            contacts=ordered(Contact),
            airlines=ordered(Airline),
            emergency_gates=ordered(EmergencyGate),
        )


_OPERATION_ENTITIES = frozenset(
    {
        "crew",
        "certification",
        "duty_clock",
        "reserve",
        "risk_signal",
        "roster_exception",
        "flight",
        "pairing",
        "gate_slot",
        "rule",
        "costs",
        "contact",
        "airline",
        "emergency_gate",
    }
)
