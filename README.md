# Courier Ops Copilot

An AI operations copilot for a parcel delivery network. It simulates a courier network across Indian cities, detects problem shipments with SQL rules, triages them with a LangGraph agent that proposes fixes for a supervisor to approve, and answers questions in plain English with guarded Text-to-SQL.

Everything runs locally with free tools: Python 3.12 (uv), FastAPI, PostgreSQL 17 in Docker, Next.js. An LLM is optional; see [Choosing an LLM](#choosing-an-llm).

Architecture, decisions and results: [PROJECT.md](PROJECT.md), [eval/results.md](eval/results.md), [eval/db/results.md](eval/db/results.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/): `brew install uv` (installs Python 3.12 itself)
- Docker with Compose v2: Docker Desktop, OrbStack or Colima
- Node.js 20 or later, for the web UI: `brew install node`

## Set up

```bash
cp .env.example .env                    # set POSTGRES_PASSWORD and JWT_SECRET
uv sync
docker compose up -d --wait             # Postgres
uv run alembic upgrade head             # tables, views, roles, indexes
uv run python scripts/seed_hubs.py      # 13 hubs in 7 cities, 36 routes
uv run python scripts/backfill.py --start 2026-10-01 --days 30          # ~8,600 shipments, ~82,000 events
uv run python scripts/detect.py --replay-from 2026-10-01T00:00 --every 2   # exceptions, as a 2-hourly cron would find them

uv run python scripts/create_user.py olga --role operator     # prompts for a password (10+ characters)
uv run python scripts/create_user.py sam --role supervisor
uv run python scripts/create_user.py ada --role admin
```

Generate `JWT_SECRET` with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Run

```bash
uv run uvicorn --factory app.main:create_app --reload       # API on http://localhost:8000 (docs at /docs)
cd web && npm install && npm run dev                         # UI on http://127.0.0.1:3000
```

The UI forwards `/api/*` to the API (set `API_URL` if it isn't on port 8000).

A typical session:
1. Sign in as `olga` (operator) and open an exception. Click **Run triage**: the agent explains the likely cause and proposes actions.
2. Sign in as `sam` (supervisor) and open **Approvals**. Approving runs the action; nobody can approve their own proposal.
3. Ask questions on **Ask**, once an LLM is configured.

| Role | Can |
|---|---|
| viewer | read everything except the audit log; ask questions |
| operator | + propose actions, run triage, resolve exceptions |
| supervisor | + approve or reject actions |
| admin | + run the detector, read the audit log (`GET /audit`) |

## Choosing an LLM

Set in `.env`; restart the API afterwards.

| Provider | Settings | Notes |
|---|---|---|
| none (default) | `LLM_PROVIDER=none` | Triage uses its built-in rules; Ask returns 503 |
| Ollama | `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5-coder:7b` | Free and local: `brew install ollama && ollama serve`, then `ollama pull qwen2.5-coder:7b` |
| OpenAI-compatible | `LLM_PROVIDER=openai`, `LLM_MODEL=...`, `LLM_API_KEY=...` or `LLM_BASE_URL=http://localhost:1234/v1` | api.openai.com, LM Studio, vLLM... |
| Anthropic | `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-opus-5-5` | Key from `LLM_API_KEY`, `ANTHROPIC_API_KEY` or `ant auth login` |

Then measure it: `uv run python -m eval.run_eval all`.

## Simulated data

The network's "now" is its latest tracking event, not the wall clock. Rules, triage and questions all work as of that time.

```bash
uv run python scripts/backfill.py --start 2026-10-01 --days 30 --reset           # replace the data
uv run python scripts/backfill.py --start 2026-10-01 --days 30 --seed 7 --reset  # a different dataset
uv run python scripts/backfill.py --start 2026-10-01 --days 30 --as-of 2026-10-20T15:00 --reset
uv run python scripts/detect.py                                                  # detector as of now
```

The same seed and config always give the same data. `--reset` deletes shipments and everything about them (events, faults, exceptions, actions, triage) but keeps hubs, routes and users. Demand, timings and fault rates are in `config/simulator.yaml`; rule thresholds in `config/rules.yaml`; allowed actions in `config/action_policy.yaml`.

## Evals and benchmarks

```bash
uv run python -m eval.run_eval all        # detector, Text-to-SQL, triage -> eval/results.md
uv run python scripts/explain_report.py   # query timings, before/after indexes -> eval/db/results.md
```

Both build their own databases (`courier_ops_eval`, `courier_ops_perf`) and never touch your data. Model-dependent sections are skipped when `LLM_PROVIDER=none`.

## Checks

```bash
uv run pytest                     # Postgres tests skip if it isn't running; no LLM needed
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run alembic check              # models and migrations match
cd web && npm run typecheck && npm run build
```

Database tests use a separate `courier_ops_test` database on the same server and drop it afterwards.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe the change"   # after changing core/db/models.py
uv run alembic upgrade head
uv run alembic downgrade -1
```

Autogenerate doesn't see triggers, views or roles; write those into the migration by hand (see 0001, 0004 and 0005).

## Stop

```bash
docker compose down               # keeps data
docker compose down -v            # deletes data
```

## Configuration and security notes

All settings come from environment variables or `.env` (see `.env.example`); the app refuses to start on an invalid value. Secrets are held as `SecretStr` and masked in logs.

This is a local demo. The UI keeps the login token in `localStorage`, and Postgres and the servers listen on localhost only. Before exposing it anywhere, move the token to an httpOnly cookie, add HTTPS, and rate-limit login.
