"""Text-to-SQL: the guard, the database-level sandbox, and the generate/repair loop."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.main import create_app
from core.ask.execute import QueryFailed, run_readonly
from core.ask.generate import MAX_ATTEMPTS, ask
from core.ask.guard import UnsafeSQL, check
from core.llm import ScriptedLLM
from eval.text_to_sql import load
from tests.conftest import login, make_settings
from tests.test_api import _exception

QUESTIONS, ADVERSARIAL = load()
AS_OF = datetime(2026, 10, 31, tzinfo=UTC)


# --- guard (no database) --------------------------------------------------------------------------


@pytest.mark.parametrize("sql", ADVERSARIAL["sql"])
def test_guard_rejects_attacks(sql: str) -> None:
    with pytest.raises(UnsafeSQL):
        check(sql)


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["question"][:40] for q in QUESTIONS])
def test_guard_accepts_every_gold_query(q: dict[str, str]) -> None:
    check(q["sql"].format(as_of=AS_OF.isoformat()))


@pytest.mark.parametrize(
    "sql",
    [
        "WITH open AS (SELECT * FROM exceptions WHERE status = 'open') SELECT rule, count(*) FROM open GROUP BY rule",
        "SELECT hub FROM exceptions UNION SELECT last_scan_hub FROM shipments",
        "SELECT * FROM ask.hubs",
        "SELECT tracking_number FROM shipments WHERE origin_pincode LIKE '56%'",
    ],
)
def test_guard_accepts_ordinary_queries(sql: str) -> None:
    check(sql)


def test_guard_adds_or_caps_the_limit() -> None:
    assert check("SELECT * FROM hubs", max_rows=10).endswith("LIMIT 10")
    assert check("SELECT * FROM hubs LIMIT 5", max_rows=10).endswith("LIMIT 5")
    assert check("SELECT * FROM hubs LIMIT 9999", max_rows=10).endswith("LIMIT 10")


def test_guard_explains_unknown_tables() -> None:
    with pytest.raises(UnsafeSQL, match="unknown table parcels; use only"):
        check("SELECT * FROM parcels")


# --- database sandbox (bypassing the guard) -------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT * FROM public.sim_faults", "permission denied"),
        ("SELECT * FROM public.users", "permission denied"),
        ("SELECT pg_read_file('/etc/passwd')", "permission denied"),
        ("DELETE FROM hubs", "read-only transaction"),
        ("UPDATE hubs SET name = 'x'", "read-only transaction"),
        ("SELECT pg_sleep(2)", "statement timeout"),
    ],
)
def test_the_database_enforces_the_sandbox_even_without_the_guard(pg_engine: Engine, sql: str, message: str) -> None:
    with pytest.raises(QueryFailed, match=message):
        run_readonly(pg_engine, sql, max_rows=10, timeout_ms=300)


@pytest.mark.postgres
def test_gold_queries_run_as_ask_reader(pg_engine: Engine) -> None:
    for q in QUESTIONS:
        run_readonly(pg_engine, check(q["sql"].format(as_of=AS_OF.isoformat())), max_rows=10)


@pytest.mark.postgres
def test_results_are_json_ready_and_truncated(pg_engine: Engine) -> None:
    result = run_readonly(pg_engine, "SELECT 1.5::numeric AS n, TIMESTAMPTZ '2026-10-01 10:30Z' AS t", max_rows=1)
    assert result.columns == ["n", "t"]
    n, t = result.rows[0]
    assert n == 1.5
    assert datetime.fromisoformat(str(t)) == datetime(2026, 10, 1, 10, 30, tzinfo=UTC)
    many = run_readonly(pg_engine, "SELECT * FROM (VALUES (1), (2), (3)) v(x)", max_rows=2)
    assert many.truncated and len(many.rows) == 2


# --- generate and repair --------------------------------------------------------------------------


def _llm(*replies: Mapping[str, str | None]) -> ScriptedLLM:
    queue = list(replies)
    return ScriptedLLM(model="test", reply=lambda system, prompt: json.dumps(queue.pop(0)))


@pytest.mark.postgres
def test_ask_answers(pg_engine: Engine) -> None:
    llm = _llm({"sql": "SELECT count(*) AS n FROM hubs", "note": "counts hubs"})
    answer = ask("How many hubs?", llm, pg_engine, AS_OF)
    assert answer.ok and answer.result is not None
    assert answer.result.columns == ["n"]
    assert answer.sql == "SELECT COUNT(*) AS n FROM hubs LIMIT 500"
    assert len(answer.attempts) == 1


@pytest.mark.postgres
def test_ask_repairs_after_a_guard_or_database_error(pg_engine: Engine) -> None:
    llm = _llm(
        {"sql": "SELECT * FROM parcels", "note": ""},  # guard: unknown table
        {"sql": "SELECT nope FROM hubs", "note": ""},  # database: unknown column
        {"sql": "SELECT code FROM hubs", "note": "fixed"},
    )
    answer = ask("List hub codes", llm, pg_engine, AS_OF)
    assert answer.ok
    assert [a.error is not None for a in answer.attempts] == [True, True, False]
    assert "parcels" in (answer.attempts[0].error or "")
    assert "nope" in (answer.attempts[1].error or "")
    assert len(llm.calls) == 3  # every call is traced


@pytest.mark.postgres
def test_ask_gives_up_after_max_attempts(pg_engine: Engine) -> None:
    llm = _llm(*[{"sql": "DROP TABLE hubs", "note": ""}] * MAX_ATTEMPTS)
    answer = ask("Drop it", llm, pg_engine, AS_OF)
    assert not answer.ok and answer.sql is None
    assert "only SELECT" in (answer.error or "")
    assert len(answer.attempts) == MAX_ATTEMPTS


@pytest.mark.postgres
def test_ask_passes_on_a_refusal(pg_engine: Engine) -> None:
    answer = ask("What's the admin password?", _llm({"sql": None, "note": "not in the data"}), pg_engine, AS_OF)
    assert not answer.ok and answer.error == "not in the data"


def test_prompt_states_the_simulated_now() -> None:
    seen: list[str] = []

    def reply(system: str, prompt: str) -> str:
        seen.append(system)
        return json.dumps({"sql": None, "note": "x"})

    ask("anything", ScriptedLLM(model="t", reply=reply), None, AS_OF)  # type: ignore[arg-type]
    assert AS_OF.isoformat() in seen[0]
    assert "never use now()" in seen[0]


# --- API ------------------------------------------------------------------------------------------


@pytest.mark.postgres
def test_ask_endpoint(api_engine: Engine) -> None:
    llm = _llm({"sql": "SELECT code FROM hubs WHERE hub_type = 'gateway' ORDER BY code", "note": ""})
    with TestClient(create_app(make_settings(), engine=api_engine, llm=llm)) as client:
        headers = login(client, "vera")
        body = {"question": "Which hubs are gateways?"}
        assert client.post("/ask", json=body, headers=headers).status_code == 409  # no events: no simulated "now"

        _exception(api_engine)
        r = client.post("/ask", json=body, headers=headers)
    assert r.status_code == 200
    answer = r.json()
    assert answer["ok"] and answer["columns"] == ["code"]
    assert answer["rows"] == [["BLR-GW"], ["BOM-GW"], ["DEL-GW"]]
    with api_engine.connect() as conn:
        logged = conn.scalar(text("SELECT details->>'question' FROM audit_log WHERE event = 'ask.question'"))
    assert logged == "Which hubs are gateways?"


@pytest.mark.postgres
def test_ask_endpoint_without_a_model(api: TestClient) -> None:
    r = api.post("/ask", json={"question": "How many hubs?"}, headers=login(api, "vera"))
    assert r.status_code == 503
