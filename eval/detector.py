"""Score the detector against the simulator's ground truth (sim_faults)."""

from sqlalchemy import Engine, text

# Per fault type: share of faulty shipments that got any exception, and how long after the fault began.
RECALL = """
WITH first_detection AS (
    SELECT shipment_id, min(detected_at) AS detected_at FROM exceptions GROUP BY shipment_id
)
SELECT f.fault_type,
       count(*) AS faults,
       count(d.shipment_id) AS detected,
       round(100.0 * count(d.shipment_id) / count(*), 1) AS recall_pct,
       round((percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM d.detected_at - f.injected_at) / 3600))::numeric, 1) AS median_hours_to_detect
FROM sim_faults f LEFT JOIN first_detection d ON d.shipment_id = f.shipment_id
GROUP BY f.fault_type ORDER BY f.fault_type
"""

# Per rule: share of exceptions on shipments that really had a fault.
PRECISION = """
SELECT x.rule,
       count(*) AS exceptions,
       count(*) FILTER (WHERE f.id IS NOT NULL) AS on_faulty_shipments,
       round(100.0 * count(*) FILTER (WHERE f.id IS NOT NULL) / count(*), 1) AS precision_pct,
       string_agg(DISTINCT f.fault_type, ', ' ORDER BY f.fault_type) AS fault_types
FROM exceptions x LEFT JOIN sim_faults f ON f.shipment_id = x.shipment_id
GROUP BY x.rule ORDER BY x.rule
"""

# Why faults went undetected: too recent to show by the end of the run, or delivered without tripping a rule.
MISSES = """
SELECT count(*) AS missed,
       count(*) FILTER (WHERE f.injected_at > :as_of - INTERVAL '24 hours') AS began_in_last_24h,
       count(*) FILTER (WHERE f.injected_at <= :as_of - INTERVAL '24 hours' AND s.status = 'delivered')
           AS delivered_without_tripping_a_rule
FROM sim_faults f JOIN shipments s ON s.id = f.shipment_id
WHERE NOT EXISTS (SELECT 1 FROM exceptions x WHERE x.shipment_id = f.shipment_id)
"""

OVERALL = """
SELECT
    (SELECT count(DISTINCT shipment_id) FROM exceptions) AS flagged,
    (SELECT count(DISTINCT x.shipment_id) FROM exceptions x JOIN sim_faults f USING (shipment_id)) AS flagged_faulty,
    (SELECT count(*) FROM sim_faults) AS faulty,
    (SELECT count(*) FROM shipments) AS shipments
"""


def _table(columns: list[str], rows: list[tuple[object, ...]]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def run(engine: Engine) -> str:
    with engine.connect() as conn:
        flagged, flagged_faulty, faulty, shipments = conn.execute(text(OVERALL)).one()
        recall = conn.execute(text(RECALL))
        recall_table = _table(list(recall.keys()), [tuple(r) for r in recall])
        precision = conn.execute(text(PRECISION))
        precision_table = _table(list(precision.keys()), [tuple(r) for r in precision])
        as_of = conn.scalar(text("SELECT max(occurred_at) FROM tracking_events"))
        missed, recent, delivered = conn.execute(text(MISSES), {"as_of": as_of}).one()

    p = 100 * flagged_faulty / flagged if flagged else 0.0
    r = 100 * flagged_faulty / faulty if faulty else 0.0
    return f"""\
{shipments} shipments, {faulty} with an injected fault. Detector replayed every 2 h of simulated time.

**Shipment level:** {flagged} flagged, precision {p:.1f}%, recall {r:.1f}%.

Recall by fault type (time to detect is from the fault starting to the shipment's first exception):

{recall_table}

Precision by rule:

{precision_table}

Of the {missed} missed faults, {recent} began in the last 24 h of the run, before any rule could fire, and
{delivered} were delivered without tripping one (misroutes whose detour neither reached a wrong delivery center
nor passed a hub twice, and still arrived on time).

Precision is {p:.1f}% largely because the simulator never delays a fault-free shipment, and the thresholds
sit just above its normal timings. Real networks are noisier, so expect lower precision on real data.
"""
