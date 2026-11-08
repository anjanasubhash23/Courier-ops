"""Question -> SQL -> rows, with a repair loop when the guard or the database rejects the SQL."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

from core.ask import examples
from core.ask.execute import QueryFailed, QueryResult, run_readonly
from core.ask.guard import MAX_ROWS, UnsafeSQL, check
from core.llm import LLM, LLMError

PROMPTS = Path(__file__).resolve().parent.parent.parent / "prompts"
MAX_ATTEMPTS = 3  # the first try plus two repairs


@dataclass(frozen=True)
class Attempt:
    sql: str | None
    error: str | None


@dataclass
class Answer:
    question: str
    model: str
    sql: str | None = None
    note: str | None = None
    result: QueryResult | None = None
    error: str | None = None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result is not None


def system_prompt(as_of: datetime) -> str:
    template = (PROMPTS / "text_to_sql.md").read_text()
    stamp = as_of.isoformat()
    return template.format(as_of=stamp, examples=examples.render(stamp))


def ask(question: str, llm: LLM, engine: Engine, as_of: datetime, *, max_rows: int = MAX_ROWS) -> Answer:
    answer = Answer(question=question, model=llm.name)
    system = system_prompt(as_of)
    prompt = f"Question: {question}"
    for _ in range(MAX_ATTEMPTS):
        try:
            reply = llm.complete_json(system, prompt)
        except LLMError as exc:
            answer.error = f"model error: {exc}"
            return answer
        sql, answer.note = reply.get("sql"), reply.get("note")
        if not sql:
            answer.attempts.append(Attempt(None, None))
            answer.error = answer.note or "the model couldn't answer this from the available data"
            return answer
        try:
            answer.sql = check(str(sql), max_rows)
            answer.result = run_readonly(engine, answer.sql, max_rows=max_rows)
        except (UnsafeSQL, QueryFailed) as exc:
            answer.attempts.append(Attempt(str(sql), str(exc)))
            answer.error = str(exc)
            prompt = (PROMPTS / "repair_sql.md").read_text().format(question=question, sql=sql, error=exc)
            continue
        answer.attempts.append(Attempt(answer.sql, None))
        answer.error = None
        return answer
    answer.sql = None
    return answer
