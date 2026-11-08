"""Score triage: does it name the injected fault, and does everything it proposes pass the action policy?"""

from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.settings import get_settings
from core.actions.policy import load_policy
from core.llm import LLM, get_llm
from core.triage.graph import Triage

ROOT = Path(__file__).resolve().parent.parent
LLM_SAMPLE_PER_FAULT = 10

# The first exception raised for each faulty shipment: what an operator would triage first.
FIRST_EXCEPTIONS = """
SELECT DISTINCT ON (x.shipment_id) x.id, f.fault_type
FROM exceptions x JOIN sim_faults f ON f.shipment_id = x.shipment_id
ORDER BY x.shipment_id, x.detected_at, x.id
"""


def _score(engine: Engine, llm: LLM | None, cases: list[tuple[int, str]]) -> tuple[str, int]:
    policy = load_policy(ROOT / "config" / "action_policy.yaml")
    per_fault: dict[str, Counter[str]] = defaultdict(Counter)
    actions = rejected = 0
    with Session(engine) as session:
        triage = Triage(session, policy, llm, persist=False)
        for exception_id, fault in cases:
            state = triage.run(exception_id)
            per_fault[fault][state["diagnosis"].cause.value] += 1
            actions += len(state["diagnosis"].actions)
            rejected += len(state.get("rejected", []))
        session.rollback()

    correct = sum(c[f] for f, c in per_fault.items())
    rows = []
    for fault in sorted(per_fault):
        c = per_fault[fault]
        wrong = ", ".join(f"{cause} {n}" for cause, n in c.most_common() if cause != fault) or "-"
        rows.append(f"| {fault} | {sum(c.values())} | {c[fault]} | {100 * c[fault] / sum(c.values()):.0f}% | {wrong} |")
    table = "| fault | cases | correct | accuracy | diagnosed instead |\n|---|---|---|---|---|\n" + "\n".join(rows)
    summary = (
        f"Cause accuracy {correct}/{len(cases)} ({100 * correct / len(cases):.1f}%). "
        f"{actions} actions proposed, all within policy; {rejected} dropped by validation."
    )
    return f"{summary}\n\n{table}", correct


def run(engine: Engine) -> str:
    with engine.connect() as conn:
        cases = [(int(i), str(f)) for i, f in conn.execute(text(FIRST_EXCEPTIONS))]
    out = ["Each faulty shipment's first exception, triaged as of its last sighting. The rules' thresholds come from "
           "the simulator's timings, so they set a high bar on simulated data; the useful comparison is how close "
           "a model gets without being told them.",
           f"**Rules (baseline, no model):** {_score(engine, None, cases)[0]}"]  # fmt: skip

    llm = get_llm(get_settings())
    if llm is None:
        out.append("**LLM:** skipped, no LLM configured (`LLM_PROVIDER=none`).")
        return "\n\n".join(out)

    by_fault: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for case in cases:
        if len(by_fault[case[1]]) < LLM_SAMPLE_PER_FAULT:
            by_fault[case[1]].append(case)
    sample = [c for group in by_fault.values() for c in group]
    report, _ = _score(engine, llm, sample)
    seconds = sum(c.seconds for c in llm.calls)
    out.append(f"**LLM `{llm.name}`** ({len(sample)} cases, up to {LLM_SAMPLE_PER_FAULT} per fault; "
               f"{len(llm.calls)} calls, {seconds:.0f} s): {report}")  # fmt: skip
    return "\n\n".join(out)
