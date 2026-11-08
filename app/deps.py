"""Shared FastAPI dependencies."""

from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.settings import Settings
from core.actions.policy import ActionPolicy, load_policy
from core.detector.rules import RulesConfig, load_rules
from core.llm import LLM

ROOT = Path(__file__).resolve().parent.parent


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_llm_dep(request: Request) -> LLM | None:
    llm: LLM | None = request.app.state.llm
    return llm


def get_session(engine: Annotated[Engine, Depends(get_engine)]) -> Iterator[Session]:
    """One transaction per request: committed if the handler returns, rolled back if it raises."""
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session


@cache
def get_policy() -> ActionPolicy:
    return load_policy(ROOT / "config" / "action_policy.yaml")


@cache
def get_rules() -> RulesConfig:
    return load_rules(ROOT / "config" / "rules.yaml")


DbSession = Annotated[Session, Depends(get_session)]
Policy = Annotated[ActionPolicy, Depends(get_policy)]
