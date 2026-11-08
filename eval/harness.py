"""A throwaway database filled with a known simulated month, shared by the evals.

The evals never touch the dev database: they build `<POSTGRES_DB>_eval` from scratch.
"""

from datetime import date, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, text
from sqlalchemy.orm import Session

from app.settings import Settings
from core.db.seed import load_network, seed_network
from core.db.session import make_engine
from core.detector.rules import load_rules
from core.detector.run import replay_times, run_detector
from core.simulator.config import load_simulator_config
from core.simulator.run import backfill, default_as_of

ROOT = Path(__file__).resolve().parent.parent
START = date(2026, 10, 1)
DAYS = 30
DETECT_EVERY = timedelta(hours=2)


def eval_url(settings: Settings, suffix: str = "eval") -> URL:
    return settings.database_url.set(database=f"{settings.postgres_db}_{suffix}")


def build(
    settings: Settings,
    *,
    days: int = DAYS,
    shipments_per_day: int | None = None,
    replay: bool = True,
    suffix: str = "eval",
) -> tuple[Engine, datetime]:
    """Recreate the database: migrate, seed, backfill, then replay the detector (or run it once at the end).

    Returns (engine, as_of).
    """
    url = eval_url(settings, suffix)
    admin = make_engine(settings.database_url.set(database="postgres")).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    cfg = Config(ROOT / "alembic.ini")
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")

    sim = load_simulator_config(ROOT / "config" / "simulator.yaml")
    if shipments_per_day is not None:
        sim = sim.model_copy(update={"demand": sim.demand.model_copy(update={"shipments_per_day": shipments_per_day})})
    as_of = default_as_of(START, days, sim)
    rules = load_rules(ROOT / "config" / "rules.yaml")
    engine = make_engine(url)
    with Session(engine) as session, session.begin():
        seed_network(session, load_network(ROOT / "config" / "hubs.yaml"))
        backfill(session, sim, START, days, as_of)
        start = datetime.combine(START, datetime.min.time(), sim.tz)
        for t in replay_times(start, as_of, DETECT_EVERY) if replay else [as_of]:
            run_detector(session, t, rules)
    return engine, as_of


def connect(settings: Settings, suffix: str = "eval") -> Engine:
    """The existing database, without rebuilding it."""
    return make_engine(eval_url(settings, suffix))
