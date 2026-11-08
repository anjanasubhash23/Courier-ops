"""sim_faults: ground truth for faults the simulator injects.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08 21:18:14.296380
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sim_faults",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "fault_type",
            sa.Enum(
                "pickup_delay",
                "hub_stuck",
                "missed_connection",
                "misroute",
                "failed_delivery",
                "lost",
                name="fault_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("hub_id", sa.Integer(), nullable=True),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("injected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(["hub_id"], ["hubs.id"], name=op.f("fk_sim_faults_hub_id_hubs")),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name=op.f("fk_sim_faults_route_id_routes")),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"], name=op.f("fk_sim_faults_shipment_id_shipments")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sim_faults")),
    )
    op.create_index(op.f("ix_sim_faults_shipment_id"), "sim_faults", ["shipment_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sim_faults_shipment_id"), table_name="sim_faults")
    op.drop_table("sim_faults")
