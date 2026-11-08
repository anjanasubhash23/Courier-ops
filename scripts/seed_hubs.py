"""Seed hubs, service areas and routes from config/hubs.yaml. Safe to re-run.

uv run python scripts/seed_hubs.py [--config config/hubs.yaml]
"""

import argparse
from pathlib import Path

from sqlalchemy.orm import Session

from app.settings import get_settings
from core.db.seed import load_network, seed_network
from core.db.session import make_engine

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "hubs.yaml")
    args = parser.parse_args()

    network = load_network(args.config)  # validate before touching the database
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session, session.begin():
        counts = seed_network(session, network)
    engine.dispose()
    print(", ".join(f"{n} {name}" for name, n in counts.items()))


if __name__ == "__main__":
    main()
