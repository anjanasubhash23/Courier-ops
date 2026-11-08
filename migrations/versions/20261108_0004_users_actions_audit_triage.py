"""Users, actions awaiting approval, the append-only audit log, and triage runs.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 21:59:17.060095
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

AUDIT_FUNCTION = """
CREATE FUNCTION forbid_audit_log_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$
"""
AUDIT_TRIGGER = """
CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION forbid_audit_log_changes()
"""

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "viewer",
                "operator",
                "supervisor",
                "admin",
                name="role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("exception_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "priority_pickup",
                "escalate_to_hub",
                "reroute",
                "open_trace",
                "reschedule_delivery",
                "notify_customer",
                "initiate_rto",
                name="action_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("rationale", sa.String(length=2000), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "rejected",
                "executed",
                "failed",
                name="action_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="proposed",
            nullable=False,
        ),
        sa.Column("proposed_by", sa.String(length=64), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=2000), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["exception_id"], ["exceptions.id"], name=op.f("fk_actions_exception_id_exceptions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_actions_idempotency_key")),
    )
    op.create_index(op.f("ix_actions_exception_id"), "actions", ["exception_id"], unique=False)
    op.create_index("ix_actions_status", "actions", ["status"], unique=False)
    op.create_index(
        "uq_actions_pending_exception_type",
        "actions",
        ["exception_id", "action_type"],
        unique=True,
        postgresql_where=sa.text("status = 'proposed'"),
    )
    op.create_table(
        "triage_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("exception_id", sa.BigInteger(), nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("summary", sa.String(length=2000), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("diagnoser", sa.String(length=64), nullable=False),
        sa.Column(
            "action_ids", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name=op.f("ck_triage_runs_confidence_range")),
        sa.ForeignKeyConstraint(
            ["exception_id"], ["exceptions.id"], name=op.f("fk_triage_runs_exception_id_exceptions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triage_runs")),
    )
    op.create_index(op.f("ix_triage_runs_exception_id"), "triage_runs", ["exception_id"], unique=False)
    op.execute(AUDIT_FUNCTION)
    op.execute(AUDIT_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION forbid_audit_log_changes()")
    op.drop_index(op.f("ix_triage_runs_exception_id"), table_name="triage_runs")
    op.drop_table("triage_runs")
    op.drop_index(
        "uq_actions_pending_exception_type", table_name="actions", postgresql_where=sa.text("status = 'proposed'")
    )
    op.drop_index("ix_actions_status", table_name="actions")
    op.drop_index(op.f("ix_actions_exception_id"), table_name="actions")
    op.drop_table("actions")
    op.drop_table("users")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_table("audit_log")
