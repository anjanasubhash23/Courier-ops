"""Run the exception detector, once or replayed over a period of simulated time.

uv run python scripts/detect.py                                   # as of the latest tracking event
uv run python scripts/detect.py --as-of 2026-10-20T15:00+05:30
uv run python scripts/detect.py --replay-from 2026-10-01 --every 2   # every 2 h up to the latest event
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.settings import get_settings
from core.db.models import TrackingEvent
from core.db.session import make_engine
from core.detector.rules import load_rules
from core.detector.run import replay_times, run_detector

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ZoneInfo("Asia/Kolkata")


def _aware(value: str) -> datetime:
    t = datetime.fromisoformat(value)
    return t if t.tzinfo else t.replace(tzinfo=LOCAL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", type=_aware, help="default: the latest tracking event")
    parser.add_argument("--replay-from", type=_aware, help="run repeatedly from this time up to --as-of")
    parser.add_argument("--every", type=float, default=2.0, help="hours between replay runs")
    parser.add_argument("--rules", type=Path, default=ROOT / "config" / "rules.yaml")
    args = parser.parse_args()

    config = load_rules(args.rules)
    engine = make_engine(get_settings().database_url)
    with Session(engine) as session, session.begin():
        as_of = args.as_of or session.scalar(select(func.max(TrackingEvent.occurred_at)))
        if as_of is None:
            raise SystemExit("error: no tracking events; run scripts/backfill.py first")
        times = replay_times(args.replay_from, as_of, timedelta(hours=args.every)) if args.replay_from else [as_of]
        for t in times:
            result = run_detector(session, t, config)
        print(f"as of {result.as_of.isoformat()}: {result.opened} opened, {result.still_open} still open, "
              f"{result.resolved} resolved")  # fmt: skip
    engine.dispose()


if __name__ == "__main__":
    main()
