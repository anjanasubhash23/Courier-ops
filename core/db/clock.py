"""Simulated time. The network's "now" is its latest tracking event, not the wall clock."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.db.models import TrackingEvent


def sim_now(session: Session) -> datetime | None:
    return session.scalar(select(func.max(TrackingEvent.occurred_at)))
