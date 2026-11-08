"""Migrations against a real Postgres. Skipped when it isn't running."""

import pytest
from alembic import command
from sqlalchemy import URL, Engine, inspect, text

from tests.conftest import alembic_config

pytestmark = pytest.mark.postgres

TABLES = {
    "hubs", "hub_service_areas", "routes", "shipments", "tracking_events", "sim_faults", "exceptions",
    "users", "actions", "audit_log", "triage_runs",
}  # fmt: skip


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_migrations_upgrade_downgrade_and_upgrade_again(pg_url: URL, pg_engine: Engine) -> None:
    cfg = alembic_config(pg_url)
    assert _tables(pg_engine) == TABLES

    command.downgrade(cfg, "base")
    assert _tables(pg_engine) == set()
    with pg_engine.connect() as conn:
        leftover = conn.execute(text("SELECT 1 FROM pg_proc WHERE proname = 'forbid_tracking_event_changes'"))
        assert leftover.first() is None

    command.upgrade(cfg, "head")
    assert _tables(pg_engine) == TABLES


def test_models_match_migrations(pg_url: URL, pg_engine: Engine) -> None:
    # Raises if `alembic revision --autogenerate` would generate anything.
    command.check(alembic_config(pg_url))
