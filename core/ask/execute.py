"""Run checked SQL as `ask_reader`: read-only, time-limited, row-limited."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

STATEMENT_TIMEOUT_MS = 5000

Value = str | int | float | bool | None


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Value]]
    truncated: bool


class QueryFailed(RuntimeError):
    """The database rejected or aborted the query. The message is safe to show and to repair from."""


def _json_value(value: object) -> Value:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return str(value)


def run_readonly(engine: Engine, sql: str, *, max_rows: int, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> QueryResult:
    with engine.connect() as conn:
        try:
            with conn.begin() as tx:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL ROLE ask_reader"))
                conn.execute(text("SET LOCAL search_path = ask"))
                conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                # exec_driver_sql: the SQL goes to Postgres as written, with no bind-parameter parsing
                # (so a literal like '10:30' isn't mistaken for a :30 parameter).
                result = conn.exec_driver_sql(sql)
                columns = list(result.keys())
                fetched = result.fetchmany(max_rows + 1)
                tx.rollback()
        except DBAPIError as exc:
            message = str(exc.orig).splitlines()[0] if exc.orig else str(exc)
            raise QueryFailed(message) from None
    rows = [[_json_value(v) for v in row] for row in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > max_rows)
