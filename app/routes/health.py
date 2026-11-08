"""Liveness (`/health`) and readiness (`/ready`) endpoints.

`/health` never touches dependencies, so a database outage doesn't get the API process restarted.
`/ready` runs `SELECT 1` against Postgres and returns 503 if it fails.
"""

import logging
from importlib.metadata import version
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

Check = Literal["ok", "unavailable"]


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Readiness(BaseModel):
    status: Check
    postgres: Check


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


@router.get("/health")
def health() -> Health:
    return Health(status="ok", version=version("courier-ops-copilot"))


@router.get("/ready", responses={503: {"model": Readiness}})
def ready(engine: Annotated[Engine, Depends(get_engine)], response: Response) -> Readiness:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.warning("Postgres readiness check failed: %s", exc.__class__.__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="unavailable", postgres="unavailable")
    return Readiness(status="ok", postgres="ok")
