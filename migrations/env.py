"""Alembic environment: connection settings come from app.settings, schema from core.db.models.

Tests target another database by setting `config.attributes["url"]`.
"""

from logging.config import fileConfig

from alembic import context

from app.settings import get_settings
from core.db.models import Base
from core.db.session import make_engine

config = context.config
if config.config_file_name is not None:
    # Keep the app's loggers working when migrations run in-process (tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
url = config.attributes.get("url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Print SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=url.render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
