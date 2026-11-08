"""exceptions: problems the detector found, at most one open per shipment and rule.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 21:53:06.213268
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exceptions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "rule",
            sa.Enum(
                "pickup_overdue",
                "stuck_at_hub",
                "stuck_at_delivery_center",
                "in_transit_overdue",
                "delivery_failed",
                "misrouted",
                "sla_breach",
                name="exception_rule",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("low", "medium", "high", name="severity", native_enum=False, create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("open", "resolved", name="exception_status", native_enum=False, create_constraint=True, length=32),
            server_default="open",
            nullable=False,
        ),
        sa.Column("hub_id", sa.Integer(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], name=op.f("fk_exceptions_hub_id_hubs")),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], name=op.f("fk_exceptions_shipment_id_shipments")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exceptions")),
    )
    op.create_index(op.f("ix_exceptions_shipment_id"), "exceptions", ["shipment_id"], unique=False)
    op.create_index("ix_exceptions_status_rule", "exceptions", ["status", "rule"], unique=False)
    op.create_index(
        "uq_exceptions_open_shipment_rule",
        "exceptions",
        ["shipment_id", "rule"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_exceptions_open_shipment_rule", table_name="exceptions", postgresql_where=sa.text("status = 'open'")
    )
    op.drop_index("ix_exceptions_status_rule", table_name="exceptions")
    op.drop_index(op.f("ix_exceptions_shipment_id"), table_name="exceptions")
    op.drop_table("exceptions")
