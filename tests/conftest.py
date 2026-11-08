from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.main import create_app
from app.settings import Settings
from core.db.seed import load_network, seed_network
from core.db.session import make_engine

ROOT = Path(__file__).resolve().parent.parent


class _IsolatedSettings(Settings):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        *args: object,
        **kwargs: object,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)  # no env vars, no .env


def make_settings(**overrides: object) -> Settings:
    """Settings that ignore the developer's .env and environment, so tests are reproducible."""
    values: dict[str, object] = {"app_env": "test", "postgres_password": "test-password", **overrides}
    return _IsolatedSettings(**values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # In-memory SQLite stands in for Postgres: /ready only runs SELECT 1.
    with TestClient(create_app(settings, engine=create_engine("sqlite://"))) as c:
        yield c


# --- Postgres -------------------------------------------------------------------------------------
# Tests marked `postgres` use a throwaway `<POSTGRES_DB>_test` database on the server from .env,
# recreated once per run. They're skipped if that server isn't reachable.


def alembic_config(url: URL) -> Config:
    cfg = Config(ROOT / "alembic.ini")
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="session")
def pg_url() -> Iterator[URL]:
    try:
        settings = Settings()  # uses .env, like the app
    except ValidationError:
        pytest.skip("no Postgres settings; copy .env.example to .env")
    test_db = f"{settings.postgres_db}_test"
    admin = make_engine(settings.database_url.set(database="postgres")).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{test_db}"'))
        yield settings.database_url.set(database=test_db)
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)'))
    except OperationalError:
        pytest.skip("Postgres not reachable; start it with `docker compose up -d --wait`")
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def pg_engine(pg_url: URL) -> Iterator[Engine]:
    command.upgrade(alembic_config(pg_url), "head")
    engine = make_engine(pg_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(pg_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test. Commits inside the test become savepoints."""
    with pg_engine.connect() as conn:
        outer = conn.begin()
        s = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield s
        s.close()
        outer.rollback()


@pytest.fixture
def hub_ids(session: Session) -> dict[str, int]:
    """Seed the network from config/hubs.yaml and return {hub code: id}."""
    from core.db.models import Hub

    seed_network(session, load_network(ROOT / "config" / "hubs.yaml"))
    return {h.code: h.id for h in session.query(Hub)}
