"""Read-only `ask` schema for Text-to-SQL, and the `ask_reader` role that can read only it.

Generated SQL runs as ask_reader. The views join codes and names in so questions don't need ids, and leave
out anything the model must not see: sim_faults (ground truth), users, audit_log, triage_runs.

Roles are cluster-wide, so downgrade drops the schema (and with it every grant) but leaves the role.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 23:40:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEWS = {
    "hubs": """
        SELECT code, name, city, state, hub_type, latitude, longitude, is_active FROM public.hubs
    """,
    "routes": """
        SELECT o.code AS origin_hub, d.code AS destination_hub, r.mode, r.distance_km, r.transit_minutes, r.is_active
        FROM public.routes r
        JOIN public.hubs o ON o.id = r.origin_hub_id
        JOIN public.hubs d ON d.id = r.destination_hub_id
    """,
    "shipments": """
        SELECT s.tracking_number, s.service_level,
               o.code AS origin_hub, o.city AS origin_city, d.code AS destination_hub, d.city AS destination_city,
               s.origin_pincode, s.destination_pincode, s.weight_grams,
               s.booked_at, s.promised_by, s.status, s.last_event_at, l.code AS last_scan_hub, s.delivered_at,
               CASE WHEN s.delivered_at IS NULL THEN NULL ELSE s.delivered_at <= s.promised_by END AS delivered_on_time
        FROM public.shipments s
        JOIN public.hubs o ON o.id = s.origin_hub_id
        JOIN public.hubs d ON d.id = s.destination_hub_id
        LEFT JOIN public.hubs l ON l.id = s.last_scan_hub_id
    """,
    "tracking_events": """
        SELECT s.tracking_number, e.event_type, h.code AS hub, r.mode AS route_mode,
               rd.code AS route_destination_hub, e.reason, e.occurred_at
        FROM public.tracking_events e
        JOIN public.shipments s ON s.id = e.shipment_id
        LEFT JOIN public.hubs h ON h.id = e.hub_id
        LEFT JOIN public.routes r ON r.id = e.route_id
        LEFT JOIN public.hubs rd ON rd.id = r.destination_hub_id
    """,
    "exceptions": """
        SELECT x.id AS exception_id, s.tracking_number, x.rule, x.severity, x.status, h.code AS hub,
               x.detected_at, x.last_seen_at, x.resolved_at, x.resolved_by
        FROM public.exceptions x
        JOIN public.shipments s ON s.id = x.shipment_id
        LEFT JOIN public.hubs h ON h.id = x.hub_id
    """,
    "actions": """
        SELECT a.id AS action_id, a.exception_id, a.action_type, a.status, a.proposed_by, a.proposed_at,
               a.decided_by, a.decided_at, a.executed_at
        FROM public.actions a
    """,
}


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ask_reader') THEN
                CREATE ROLE ask_reader NOLOGIN;
            END IF;
        END $$
    """)
    op.execute("GRANT ask_reader TO CURRENT_USER")  # so the app can SET ROLE ask_reader
    op.execute("CREATE SCHEMA ask")
    for name, query in VIEWS.items():
        op.execute(f"CREATE VIEW ask.{name} AS {query}")
    op.execute("GRANT USAGE ON SCHEMA ask TO ask_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ask TO ask_reader")


def downgrade() -> None:
    op.execute("DROP SCHEMA ask CASCADE")
