"""Indexes that measurably help (eval/db/results.md): the simulated clock, and live shipments by hub.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08 22:18:38.644191
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_shipments_live_last_scan_hub_id",
        "shipments",
        ["last_scan_hub_id"],
        unique=False,
        postgresql_where=sa.text("status NOT IN ('delivered', 'returned')"),
    )
    op.create_index("ix_tracking_events_occurred_at", "tracking_events", ["occurred_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tracking_events_occurred_at", table_name="tracking_events")
    op.drop_index(
        "ix_shipments_live_last_scan_hub_id",
        table_name="shipments",
        postgresql_where=sa.text("status NOT IN ('delivered', 'returned')"),
    )
