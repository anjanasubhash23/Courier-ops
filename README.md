# Courier Ops Copilot

AI operations copilot for a parcel delivery network. It simulates a courier network, detects problem shipments with SQL rules, investigates them with an agent that proposes fixes for a human to approve, and answers operational questions with guarded Text-to-SQL.

Everything runs locally with free tools: Python 3.12 via uv, FastAPI, and PostgreSQL 17 in Docker. See [PROJECT.md](PROJECT.md) for the architecture, decisions and roadmap.

**Status: Phase 1 (schema).** The API, health checks and the database schema work: hubs, routes, shipments and append-only tracking events, plus a seeded network of 13 hubs across 7 Indian cities. The simulator, detector and AI features come in later phases.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/): `brew install uv`. uv installs Python 3.12 itself (pinned in `.python-version`).
- Docker with Compose v2: Docker Desktop, OrbStack or Colima (all free for personal use).

## Setup

```bash
cp .env.example .env              # set POSTGRES_PASSWORD (min 8 chars)
uv sync                           # creates .venv from uv.lock, including dev tools
docker compose up -d --wait       # starts Postgres and waits for its healthcheck
uv run alembic upgrade head       # creates the tables
uv run python scripts/seed_hubs.py  # loads hubs and routes from config/hubs.yaml (safe to re-run)
```

`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` only take effect when the `pgdata` volume is first created. If you change them later, reset the volume with `docker compose down -v` (this deletes all data).

Connect with psql inside the container: `docker compose exec postgres psql -U courier -d courier_ops`

## Run the API

```bash
uv run uvicorn --factory app.main:create_app --reload
curl localhost:8000/health        # {"status":"ok","version":"0.1.0"}
curl localhost:8000/ready         # {"status":"ok","postgres":"ok"}, or 503 if Postgres is down
```

Interactive docs: http://localhost:8000/docs

## Checks

```bash
uv run pytest                     # Postgres tests are skipped if it isn't running
uv run pytest -m postgres         # only the database tests
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The database tests create a separate `courier_ops_test` database on the same server and drop it afterwards, so your dev data is untouched.

## Migrations

```bash
uv run alembic revision --autogenerate -m "add shipments"   # after changing core/db/models.py
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic check                                        # fails if models and migrations differ
```

Autogenerate doesn't detect triggers or functions; add those to the migration by hand (see the append-only trigger in `migrations/versions/20261108_0001_network_and_shipments.py`).

## Stop

```bash
docker compose down               # keeps data
docker compose down -v            # deletes data
```

## Configuration

All settings come from environment variables or `.env`; see `.env.example`. The app refuses to start if a value is invalid, such as a missing or too-short `POSTGRES_PASSWORD`. Secrets are stored as `SecretStr`, so they're masked in logs and error messages.
