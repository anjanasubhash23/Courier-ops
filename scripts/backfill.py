"""Fill the database with simulated shipments, tracking events and injected faults.

uv run python scripts/backfill.py --start 2026-10-01 --days 30 [--seed 7] [--as-of 2026-10-31T12:00+05:30] [--reset]
"""

import argparse
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.settings import get_settings
from core.db.session import make_engine
from core.simulator.config import load_simulator_config
from core.simulator.run import backfill, default_as_of

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="first booking day (local)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--as-of", type=datetime.fromisoformat, help="simulated 'now'; default: end of the last day")
    parser.add_argument("--seed", type=int, help="overrides the seed in the config")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "simulator.yaml")
    parser.add_argument("--reset", action="store_true", help="delete existing shipments, events and faults first")
    args = parser.parse_args()

    cfg = load_simulator_config(args.config)
    if args.seed is not None:
        cfg = cfg.model_copy(update={"seed": args.seed})
    as_of = args.as_of or default_as_of(args.start, args.days, cfg)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=cfg.tz)

    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session, session.begin():
            counts = backfill(session, cfg, args.start, args.days, as_of, replace=args.reset)
    except RuntimeError as exc:
        raise SystemExit(f"error: {exc}") from None
    finally:
        engine.dispose()
    print(f"seed {cfg.seed}, as of {as_of.isoformat()}")
    for name, n in counts.items():
        print(f"  {name:<18} {n:>8}")


if __name__ == "__main__":
    main()
