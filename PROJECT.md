# Courier Ops Copilot — project notes

## What it does

1. Simulates a courier network: hubs, routes, shipments and tracking events.
2. Detects problem shipments with SQL exception rules.
3. Investigates exceptions with an agent that proposes corrective actions.
4. Requires human approval before any action runs.
5. Answers operational questions with guarded Text-to-SQL.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Datastore | PostgreSQL 17 only, in Docker | One store for data, rules, views and audit; free and local |
| Background jobs | None at first; the detector runs as a CLI command | Avoids running Redis and a worker. Add one when the live simulator needs it |
| Partitions | Added only after `eval/db` timings show they help | Avoids complexity before there's data to justify it |
| Agent framework | LangGraph, introduced in the triage phase | Not needed before then |
| LLM provider | Not decided yet | `core/llm.py` will be the only module that calls an LLM, so the choice stays isolated |
| Frontend and auth | After the backend works end to end | Keeps early phases small |
| Repository | This repo (`feat/restart`); the old Java app stays on `master` | |

## Schema (Phase 1)

| Table | Holds | Notes |
|---|---|---|
| `hubs` | gateways, sort centers, delivery centers | Seeded from `config/hubs.yaml`; never deleted, only deactivated |
| `hub_service_areas` | pincode prefix → delivery center | Match a pincode by its longest prefix |
| `routes` | one-way legs between hubs (road or air) | Unique per origin, destination and mode |
| `shipments` | booking details and current state | `status`, `last_event_at`, `last_scan_hub_id`, `delivered_at` are a projection of the events |
| `tracking_events` | every scan, in order of `occurred_at` | Append-only, enforced by a trigger. `occurred_at` is simulated time; `recorded_at` is wall-clock |

- **One write path.** Code changes a shipment's state only through `core.db.events.record_event`. It appends the event and updates the shipment in the same transaction, with a row lock. A late event (older `occurred_at`) is stored but doesn't overwrite newer state.
- **Enums are VARCHAR + CHECK,** not native Postgres enums, so adding a value is an ordinary migration.
- **Constraints carry rules the simulator mustn't break:** valid pincodes, `promised_by > booked_at`, a hub on every hub scan, a route on every departure.
- **Indexes are minimal on purpose** (keys, foreign keys used in lookups, and `(shipment_id, occurred_at)` for timelines). Phase 7 adds more, measured against real queries.

## Layout

```
app/                     # HTTP only: no business logic
├── main.py              # app factory; owns the database engine
├── settings.py          # typed config from env vars / .env, validated at startup
├── auth.py              # later: JWT, Argon2, roles
└── routes/              # health (Phase 0); exceptions, actions, ask, hubs, metrics, sim (later)
config/                  # rules, simulator, hubs, action policy as YAML data
core/
├── db/                  # models, engine, events.py (write path), seed.py (network), SQL for views/roles/indexes
├── simulator/           # network, demand, movement, injected faults
├── detector/            # SQL rules that turn shipments into exceptions
├── triage/              # agent: investigates an exception, proposes policy-checked actions
├── ask/                 # Text-to-SQL: examples, generate, guard, execute (read-only)
└── llm.py               # the one LLM wrapper
migrations/              # Alembic; reads the URL from app.settings
prompts/                 # prompt templates
eval/                    # Text-to-SQL accuracy, triage accuracy, query timings
scripts/                 # seeding, backfill, EXPLAIN reports
web/                     # later: Next.js frontend
tests/
```

## Risks and how they're handled

- **Text-to-SQL safety.** Layered guards: a read-only database role, access to approved views only, statement validation with sqlglot, statement timeout and row limit.
- **Actions without approval.** The agent only writes proposals. A separate service runs an action after a human approves it, with an idempotency key and an audit record.
- **Eval leakage.** The simulator records the faults it injects in `sim_faults`. The agent and the Text-to-SQL role must not be able to read it, or eval scores mean nothing. Enforced with database roles.
- **Simulated time.** Rules take an `as_of` time instead of `now()`, so backfills give correct results.
- **Duplicate exceptions.** At most one open exception per shipment and rule, enforced by a unique index.
- **Local LLM quality.** Structured output from local models may be unreliable; test early once the provider is chosen.

## Roadmap

| Phase | Build | Done when |
|---|---|---|
| 0 ✅ | Tooling, Compose, FastAPI, Alembic, health checks | API and Postgres run; checks pass |
| 1 ✅ | Schema: hubs, routes, shipments, append-only tracking events | Migrations apply and roll back cleanly |
| 2 | Simulator and backfill, recording injected faults | Same seed gives the same data |
| 3 | Detector rules | Precision and recall measured against `sim_faults` |
| 4 | Exceptions and actions API, no AI | Manual approval flow works and is audited |
| 5 | Text-to-SQL with guardrails | Gold questions scored; adversarial cases blocked |
| 6 | LangGraph triage with human approval | Triage scored; it can't act without approval |
| 7 | Performance: indexes, views, partitions | Before/after timings recorded |
| 8 | Next.js UI, auth, docs | Full demo runs locally |
