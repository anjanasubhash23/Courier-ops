"""Network (hubs, service areas, routes), shipments and append-only tracking events.

Revision ID: 0001
Revises:
Create Date: 2026-11-08 20:26:18.459814
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# tracking_events is the source of truth for shipment history, so rows are never changed or removed.
# TRUNCATE is still allowed, to reset the simulator.
APPEND_ONLY_FUNCTION = """
CREATE FUNCTION forbid_tracking_event_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tracking_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER tracking_events_append_only
BEFORE UPDATE OR DELETE ON tracking_events
FOR EACH ROW EXECUTE FUNCTION forbid_tracking_event_changes()
"""


revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hubs",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column(
            "hub_type",
            sa.Enum(
                "gateway",
                "sort_center",
                "delivery_center",
                name="hub_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name=op.f("ck_hubs_latitude_range")),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name=op.f("ck_hubs_longitude_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hubs")),
        sa.UniqueConstraint("code", name=op.f("uq_hubs_code")),
    )
    op.create_table(
        "hub_service_areas",
        sa.Column("pincode_prefix", sa.String(length=6), nullable=False),
        sa.Column("hub_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "pincode_prefix ~ '^[1-9][0-9]{0,5}$'", name=op.f("ck_hub_service_areas_pincode_prefix_format")
        ),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], name=op.f("fk_hub_service_areas_hub_id_hubs")),
        sa.PrimaryKeyConstraint("pincode_prefix", name=op.f("pk_hub_service_areas")),
    )
    op.create_index(op.f("ix_hub_service_areas_hub_id"), "hub_service_areas", ["hub_id"], unique=False)
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("origin_hub_id", sa.Integer(), nullable=False),
        sa.Column("destination_hub_id", sa.Integer(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("road", "air", name="route_mode", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("distance_km", sa.Integer(), nullable=False),
        sa.Column("transit_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("distance_km > 0", name=op.f("ck_routes_distance_positive")),
        sa.CheckConstraint("origin_hub_id <> destination_hub_id", name=op.f("ck_routes_distinct_hubs")),
        sa.CheckConstraint("transit_minutes > 0", name=op.f("ck_routes_transit_positive")),
        sa.ForeignKeyConstraint(["destination_hub_id"], ["hubs.id"], name=op.f("fk_routes_destination_hub_id_hubs")),
        sa.ForeignKeyConstraint(["origin_hub_id"], ["hubs.id"], name=op.f("fk_routes_origin_hub_id_hubs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routes")),
        sa.UniqueConstraint("origin_hub_id", "destination_hub_id", "mode", name="uq_routes_origin_destination_mode"),
    )
    op.create_index(op.f("ix_routes_destination_hub_id"), "routes", ["destination_hub_id"], unique=False)
    op.create_table(
        "shipments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("tracking_number", sa.String(length=20), nullable=False),
        sa.Column(
            "service_level",
            sa.Enum("standard", "express", name="service_level", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("origin_hub_id", sa.Integer(), nullable=False),
        sa.Column("destination_hub_id", sa.Integer(), nullable=False),
        sa.Column("origin_pincode", sa.String(length=6), nullable=False),
        sa.Column("destination_pincode", sa.String(length=6), nullable=False),
        sa.Column("weight_grams", sa.Integer(), nullable=False),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("promised_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "booked",
                "picked_up",
                "at_hub",
                "in_transit",
                "out_for_delivery",
                "delivery_failed",
                "delivered",
                "returning",
                "returned",
                name="shipment_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="booked",
            nullable=False,
        ),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_hub_id", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "destination_pincode ~ '^[1-9][0-9]{5}$'", name=op.f("ck_shipments_destination_pincode_format")
        ),
        sa.CheckConstraint("origin_pincode ~ '^[1-9][0-9]{5}$'", name=op.f("ck_shipments_origin_pincode_format")),
        sa.CheckConstraint("promised_by > booked_at", name=op.f("ck_shipments_promise_after_booking")),
        sa.CheckConstraint("weight_grams > 0", name=op.f("ck_shipments_weight_positive")),
        sa.ForeignKeyConstraint(["destination_hub_id"], ["hubs.id"], name=op.f("fk_shipments_destination_hub_id_hubs")),
        sa.ForeignKeyConstraint(["last_scan_hub_id"], ["hubs.id"], name=op.f("fk_shipments_last_scan_hub_id_hubs")),
        sa.ForeignKeyConstraint(["origin_hub_id"], ["hubs.id"], name=op.f("fk_shipments_origin_hub_id_hubs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("tracking_number", name=op.f("uq_shipments_tracking_number")),
    )
    op.create_table(
        "tracking_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "booked",
                "picked_up",
                "arrived_at_hub",
                "departed_hub",
                "out_for_delivery",
                "delivery_attempt_failed",
                "delivered",
                "rto_initiated",
                "returned_to_origin",
                name="event_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("hub_id", sa.Integer(), nullable=True),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "event_type <> 'departed_hub' OR route_id IS NOT NULL", name=op.f("ck_tracking_events_departure_has_route")
        ),
        sa.CheckConstraint(
            "event_type NOT IN ('arrived_at_hub', 'departed_hub') OR hub_id IS NOT NULL",
            name=op.f("ck_tracking_events_hub_scan_has_hub"),
        ),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], name=op.f("fk_tracking_events_hub_id_hubs")),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name=op.f("fk_tracking_events_route_id_routes")),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipments.id"], name=op.f("fk_tracking_events_shipment_id_shipments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracking_events")),
    )
    op.create_index(
        "ix_tracking_events_shipment_id_occurred_at", "tracking_events", ["shipment_id", "occurred_at"], unique=False
    )
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER tracking_events_append_only ON tracking_events")
    op.execute("DROP FUNCTION forbid_tracking_event_changes()")
    op.drop_index("ix_tracking_events_shipment_id_occurred_at", table_name="tracking_events")
    op.drop_table("tracking_events")
    op.drop_table("shipments")
    op.drop_index(op.f("ix_routes_destination_hub_id"), table_name="routes")
    op.drop_table("routes")
    op.drop_index(op.f("ix_hub_service_areas_hub_id"), table_name="hub_service_areas")
    op.drop_table("hub_service_areas")
    op.drop_table("hubs")
