"""Worked examples shown to the model. Deliberately different from the eval questions in eval/text_to_sql/."""

EXAMPLES: list[tuple[str, str]] = [
    (
        "How many shipments are out for delivery right now?",
        "SELECT count(*) AS shipments FROM shipments WHERE status = 'out_for_delivery'",
    ),
    (
        "Which hubs have the most open high-severity exceptions?",
        """SELECT hub, count(*) AS open_high
FROM exceptions
WHERE status = 'open' AND severity = 'high'
GROUP BY hub
ORDER BY open_high DESC""",
    ),
    (
        "What share of express shipments delivered last week were on time?",
        """SELECT round(100.0 * count(*) FILTER (WHERE delivered_on_time) / count(*), 1) AS on_time_pct
FROM shipments
WHERE service_level = 'express'
  AND delivered_at >= TIMESTAMPTZ '{as_of}' - INTERVAL '7 days'""",
    ),
    (
        "Show the scan history of CO26100100191",
        """SELECT event_type, hub, reason, occurred_at
FROM tracking_events
WHERE tracking_number = 'CO26100100191'
ORDER BY occurred_at""",
    ),
]


def render(as_of: str) -> str:
    return "\n\n".join(f"Question: {q}\nSQL:\n{sql.format(as_of=as_of)}" for q, sql in EXAMPLES)
