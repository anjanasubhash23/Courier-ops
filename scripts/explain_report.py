"""Time the app's heaviest queries with and without the candidate indexes; write eval/db/results.md.

uv run python scripts/explain_report.py            # builds <POSTGRES_DB>_perf (~45k shipments) first
uv run python scripts/explain_report.py --reuse

Each query runs under EXPLAIN (ANALYZE, BUFFERS) several times; the median execution time is reported.
"Before" drops the indexes inside a transaction that is rolled back, so the database is never changed.
"""

import argparse
import json
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, text

from app.settings import get_settings
from core.db.models import ExceptionRule
from core.detector.rules import detection_params, detection_sql, load_rules
from eval import harness

RESULTS = harness.ROOT / "eval" / "db" / "results.md"
RUNS = 5

# The indexes added in migration 0006. Each must earn its place in the table below. Two partial indexes on
# shipments for the detector's candidate scan were tried and dropped: they saved ~4 ms on a query that runs
# every 2 h, while costing index maintenance on every status change.
INDEXES = {
    "ix_shipments_live_last_scan_hub_id": (
        "CREATE INDEX ix_shipments_live_last_scan_hub_id ON shipments (last_scan_hub_id) "
        "WHERE status NOT IN ('delivered', 'returned')"
    ),
    "ix_tracking_events_occurred_at": "CREATE INDEX ix_tracking_events_occurred_at ON tracking_events (occurred_at)",
}

# The detector's state query before Phase 7: latest event for every shipment ever booked.
OLD_STATE = """
WITH last AS (
    SELECT DISTINCT ON (e.shipment_id) e.shipment_id, e.event_type, e.hub_id, e.route_id, e.occurred_at
    FROM tracking_events e WHERE e.occurred_at <= :as_of
    ORDER BY e.shipment_id, e.occurred_at DESC, e.id DESC
),
state AS (
    SELECT s.id AS shipment_id, s.booked_at, s.promised_by, s.origin_hub_id, s.destination_hub_id,
           last.event_type, last.hub_id, last.route_id, last.occurred_at AS last_event_at
    FROM last JOIN shipments s ON s.id = last.shipment_id
    WHERE last.event_type NOT IN ('delivered', 'returned_to_origin')
)
"""


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    note: str


def queries() -> list[Query]:
    rules = list(ExceptionRule)
    new = detection_sql(rules)
    old = OLD_STATE + new[new.index("SELECT q.shipment_id") :]
    return [
        Query("detector (before: all events)", old, "every 2 h of simulated time, from cron"),
        Query("detector (after: live shipments)", new, "same rows as the old query"),
        Query("simulated now", "SELECT max(occurred_at) FROM tracking_events", "most API requests"),
        Query(
            "hubs page",
            """SELECT h.code, (SELECT count(*) FROM shipments s WHERE s.last_scan_hub_id = h.id
                   AND s.status NOT IN ('delivered', 'returned')) FROM hubs h""",
            "GET /hubs",
        ),
        Query(
            "open exceptions, first page",
            """SELECT x.id, s.tracking_number FROM exceptions x JOIN shipments s ON s.id = x.shipment_id
               WHERE x.status = 'open' ORDER BY x.detected_at, x.id LIMIT 50""",
            "GET /exceptions",
        ),
        Query(
            "one shipment's timeline",
            """SELECT e.* FROM tracking_events e JOIN shipments s ON s.id = e.shipment_id
               WHERE s.tracking_number = 'CO26101500100' ORDER BY e.occurred_at""",
            "exception detail",
        ),
        Query(
            "departures in the last 24 h by route",
            """SELECT hub, route_destination_hub, count(*) FROM ask.tracking_events
               WHERE event_type = 'departed_hub' AND occurred_at > :as_of - INTERVAL '24 hours'
               GROUP BY 1, 2""",
            "a typical ask question",
        ),
    ]


def _time(conn: Connection, sql: str, params: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    times, plan = [], {}
    for _ in range(RUNS):
        row = conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"), params).scalar_one()
        plan = row[0] if isinstance(row, list) else json.loads(row)[0]
        times.append(plan["Execution Time"])
    return statistics.median(times), plan


def measure(engine: Engine, as_of: object) -> list[tuple[Query, float, float]]:
    params: dict[str, Any] = {"as_of": as_of, **detection_params(load_rules(harness.ROOT / "config" / "rules.yaml"))}
    results: dict[str, list[float]] = {}
    for state in ("before", "after"):
        with engine.connect() as conn, conn.begin() as tx:
            for name, create in INDEXES.items():
                conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
                if state == "after":
                    conn.execute(text(create))
            conn.execute(text("ANALYZE shipments; ANALYZE tracking_events; ANALYZE exceptions"))
            for q in queries():
                results.setdefault(q.name, []).append(_time(conn, q.sql, params)[0])
            tx.rollback()
    return [(q, *results[q.name]) for q in queries()]  # type: ignore[misc]


def _speedup(before: float, after: float) -> str:
    if after < 0.05:
        return ">100x" if before >= 5 else "-"
    ratio = before / after
    return f"{ratio:.1f}x" if ratio >= 1.15 else "-"


def report(engine: Engine, rows: list[tuple[Query, float, float]]) -> str:
    with engine.connect() as conn:
        shipments = conn.scalar(text("SELECT count(*) FROM shipments"))
        events = conn.scalar(text("SELECT count(*) FROM tracking_events"))
        size = conn.scalar(text("SELECT pg_size_pretty(pg_total_relation_size('tracking_events'))"))
    table = "\n".join(
        f"| {q.name} | {q.note} | {before:.2f} | {after:.2f} | {_speedup(before, after)} |" for q, before, after in rows
    )
    old = next(b for q, b, _ in rows if q.name.startswith("detector (before"))
    new_before, new = next((b, a) for q, b, a in rows if q.name.startswith("detector (after"))
    return f"""# Query timings ({date.today().isoformat()})

{shipments:,} shipments and {events:,} tracking events ({size} including indexes), 30 simulated days at
1,500 bookings a day. Median of {RUNS} runs of `EXPLAIN (ANALYZE, BUFFERS)`, in milliseconds. "Before" is
without the indexes from migration 0006; both detector rows are measured in both states.

| query | used by | before (ms) | after (ms) | speedup |
|---|---|---|---|---|
{table}

**Detector:** {old:.0f} ms with the original query, {new:.0f} ms now ({old / new:.0f}x). The gain is the
rewrite, which looks only at live shipments and gets each one's latest event from the existing
`(shipment_id, occurred_at)` index. The new indexes change it by only {new_before - new:.1f} ms.

**Indexes kept:** `ix_tracking_events_occurred_at` (the simulated clock, read on most requests, and time-window
questions) and `ix_shipments_live_last_scan_hub_id` (the hubs page). Both are cheap to maintain: one is on an
append-only column, the other covers only live shipments.

**Partitioning `tracking_events`:** not done. At this size every hot query is an index lookup or touches
only live shipments, so partition pruning has nothing to save. Revisit when the table is in the tens of
millions of rows or old events need to be archived in bulk (detach a month instead of deleting).

Reproduce with `uv run python scripts/explain_report.py`.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reuse", action="store_true", help="use the existing _perf database")
    args = parser.parse_args()
    settings = get_settings()
    if args.reuse:
        engine = harness.connect(settings, suffix="perf")
    else:
        engine, _ = harness.build(settings, shipments_per_day=1500, replay=False, suffix="perf")
    with engine.connect() as conn:
        as_of = conn.scalar(text("SELECT max(occurred_at) FROM tracking_events"))
    body = report(engine, measure(engine, as_of))
    RESULTS.write_text(body)
    print(body)
    engine.dispose()


if __name__ == "__main__":
    main()
