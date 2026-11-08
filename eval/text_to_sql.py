"""Score Text-to-SQL: guard coverage (no model needed), gold-SQL sanity, and model accuracy if one is configured."""

from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.settings import get_settings
from core.ask.execute import run_readonly
from core.ask.generate import ask
from core.ask.guard import UnsafeSQL, check
from core.db.clock import sim_now
from core.llm import get_llm

DIR = Path(__file__).resolve().parent / "text_to_sql"
MAX_ROWS = 5000
# Tables whose row counts must not change while adversarial questions run.
WATCHED = ("shipments", "tracking_events", "exceptions", "actions", "users", "sim_faults")


def load() -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    questions = yaml.safe_load((DIR / "questions.yaml").read_text())
    adversarial = yaml.safe_load((DIR / "adversarial.yaml").read_text())
    return questions, adversarial


def _normalise(rows: list[list[Any]], ordered: bool) -> list[tuple[Any, ...]] | Counter[tuple[Any, ...]]:
    def value(v: Any) -> Any:
        return round(float(v), 1) if isinstance(v, int | float) and not isinstance(v, bool) else v

    tuples = [tuple(value(v) for v in row) for row in rows]
    return tuples if ordered else Counter(tuples)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.scalar(text(f"SELECT count(*) FROM {t}")) or 0 for t in WATCHED}


def run(engine: Engine) -> str:
    questions, adversarial = load()
    with Session(engine) as session:
        as_of = sim_now(session)
    assert as_of is not None, "eval database has no events"
    stamp = as_of.isoformat()
    lines: list[str] = []

    blocked = 0
    for sql in adversarial["sql"]:
        try:
            check(sql)
        except UnsafeSQL:
            blocked += 1
    lines.append(f"**Guard:** {blocked}/{len(adversarial['sql'])} adversarial queries rejected before execution.")

    gold: list[tuple[dict[str, Any], Any]] = []
    for q in questions:
        result = run_readonly(engine, check(q["sql"].format(as_of=stamp), MAX_ROWS), max_rows=MAX_ROWS)
        gold.append((q, _normalise(result.rows, q.get("ordered", False))))
    lines.append(f"**Gold SQL:** all {len(gold)} gold queries pass the guard and run as `ask_reader`.")

    llm = get_llm(get_settings())
    if llm is None:
        lines.append("\n**Model accuracy:** skipped, no LLM configured (`LLM_PROVIDER=none`).")
        return "\n\n".join(lines)

    rows_out: list[tuple[str, str, int]] = []
    correct = 0
    for q, expected in gold:
        answer = ask(q["question"], llm, engine, as_of, max_rows=MAX_ROWS)
        got = _normalise(answer.result.rows, q.get("ordered", False)) if answer.result else None
        verdict = "correct" if got == expected else ("wrong rows" if answer.ok else f"failed: {answer.error}")
        correct += verdict == "correct"
        rows_out.append((q["question"], verdict, len(answer.attempts)))

    # Anything that does run, runs as ask_reader and can only read the ask views, so the measures are how
    # often the attack was refused or blocked, and whether the database changed.
    before = _counts(engine)
    refused = sum(not ask(question, llm, engine, as_of).ok for question in adversarial["questions"])
    unchanged = _counts(engine) == before

    seconds = sum(c.seconds for c in llm.calls)
    table = "\n".join(f"| {q} | {v} | {n} |" for q, v, n in rows_out)
    lines += [
        f"\n**Model:** `{llm.name}`, {len(llm.calls)} calls, {seconds:.0f} s total.",
        f"**Execution accuracy:** {correct}/{len(gold)} ({100 * correct / len(gold):.0f}%).",
        f"**Adversarial questions:** {refused}/{len(adversarial['questions'])} refused or blocked; the rest answered "
        f"from the ask views only. Database {'unchanged' if unchanged else 'CHANGED'}.",
        "\n| question | result | attempts |\n|---|---|---|\n" + table,
    ]
    return "\n\n".join(lines)
