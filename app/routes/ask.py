"""Ask a question about the network in plain English; get the SQL and the rows."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.auth import Viewer
from app.deps import DbSession, get_engine, get_llm_dep
from core.actions.service import audit
from core.ask.execute import Value
from core.ask.generate import ask
from core.db.clock import sim_now
from core.llm import LLM

router = APIRouter(tags=["ask"])


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class AttemptOut(BaseModel):
    sql: str | None
    error: str | None


class AnswerOut(BaseModel):
    ok: bool
    question: str
    sql: str | None
    note: str | None
    columns: list[str]
    rows: list[list[Value]]
    truncated: bool
    error: str | None
    attempts: list[AttemptOut]
    model: str


@router.post("/ask")
def ask_question(
    body: Question,
    session: DbSession,
    user: Viewer,
    engine: Annotated[Engine, Depends(get_engine)],
    llm: Annotated[LLM | None, Depends(get_llm_dep)],
) -> AnswerOut:
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no LLM configured: set LLM_PROVIDER and LLM_MODEL")
    as_of = sim_now(session)
    if as_of is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no tracking events yet; run scripts/backfill.py")
    answer = ask(body.question, llm, engine, as_of)
    audit(session, user.username, "ask.question", "ask", 0, question=body.question, sql=answer.sql, error=answer.error)
    return AnswerOut(
        ok=answer.ok,
        question=answer.question,
        sql=answer.sql,
        note=answer.note,
        columns=answer.result.columns if answer.result else [],
        rows=answer.result.rows if answer.result else [],
        truncated=answer.result.truncated if answer.result else False,
        error=answer.error,
        attempts=[AttemptOut(sql=a.sql, error=a.error) for a in answer.attempts],
        model=answer.model,
    )
