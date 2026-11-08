"""Simulation controls: the simulated clock, and running the detector on demand."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import Admin, Viewer
from app.deps import DbSession, get_rules
from core.actions.service import audit
from core.db.clock import sim_now
from core.detector.rules import RulesConfig
from core.detector.run import run_detector

router = APIRouter(prefix="/sim", tags=["sim"])


class Clock(BaseModel):
    as_of: datetime | None


class DetectorRun(BaseModel):
    as_of: datetime
    opened: int
    still_open: int
    resolved: int


@router.get("/clock")
def clock(session: DbSession, _: Viewer) -> Clock:
    return Clock(as_of=sim_now(session))


@router.post("/detect")
def detect(session: DbSession, user: Admin, rules: Annotated[RulesConfig, Depends(get_rules)]) -> DetectorRun:
    """Run the detector as of the latest tracking event."""
    as_of = sim_now(session)
    if as_of is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no tracking events; run scripts/backfill.py first")
    result = run_detector(session, as_of, rules)
    audit(session, user.username, "detector.run", "detector", 0, opened=result.opened, resolved=result.resolved)
    return DetectorRun(as_of=result.as_of, opened=result.opened, still_open=result.still_open, resolved=result.resolved)
