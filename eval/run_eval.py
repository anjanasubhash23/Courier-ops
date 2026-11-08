"""Run evals and write the results into eval/results.md.

uv run python -m eval.run_eval detector          # rebuilds the eval database first
uv run python -m eval.run_eval all
uv run python -m eval.run_eval text-to-sql --reuse   # use the eval database from the last run
"""

import argparse
import importlib
import re
from collections.abc import Callable
from datetime import date

from sqlalchemy import Engine

from app.settings import get_settings
from eval import harness

RESULTS = harness.ROOT / "eval" / "results.md"

Eval = Callable[[Engine], str]


EVALS = {"detector": "eval.detector", "text-to-sql": "eval.text_to_sql", "triage": "eval.triage"}


def _load(name: str) -> Eval:
    run: Eval = importlib.import_module(EVALS[name]).run
    return run


def write_section(name: str, body: str) -> None:
    """Replace (or append) the section between this eval's markers in results.md."""
    content = RESULTS.read_text() if RESULTS.exists() else "# Eval results\n"
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    section = f"{start}\n## {name} ({date.today().isoformat()})\n\n{body.strip()}\n{end}"
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
    content = (
        pattern.sub(lambda _: section, content) if pattern.search(content) else f"{content.rstrip()}\n\n{section}\n"
    )
    RESULTS.write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", choices=[*EVALS, "all"])
    parser.add_argument("--reuse", action="store_true", help="skip rebuilding the eval database")
    args = parser.parse_args()

    settings = get_settings()
    engine = harness.connect(settings) if args.reuse else harness.build(settings)[0]
    for name in EVALS if args.name == "all" else [args.name]:
        body = _load(name)(engine)
        write_section(name, body)
        print(f"## {name}\n\n{body}")
    engine.dispose()


if __name__ == "__main__":
    main()
