# Courier Ops Copilot — project notes

## What it does

1. Simulates a courier network: hubs, routes, shipments and tracking events, with injected faults.
2. Detects problem shipments with SQL exception rules.
3. Investigates exceptions with a LangGraph agent that proposes corrective actions.
4. Requires a second person's approval before any action runs.
5. Answers operational questions with guarded Text-to-SQL.

Results: [eval/results.md](eval/results.md) (detector, Text-to-SQL, triage) and
[eval/db/results.md](eval/db/results.md) (query timings).

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Datastore | PostgreSQL 17 only, in Docker | One store for data, rules, views and audit; free and local |
| Background jobs | None; the detector is a CLI (`scripts/detect.py`) or `POST /sim/detect` | No Redis or worker to run. Add a scheduler when a live simulator needs one |
| Partitions | Not used | Measured: nothing at ~400k events benefits (eval/db/results.md) |
| LLM provider | Configurable, default `none` | `core/llm.py` supports Ollama, OpenAI-compatible servers and Anthropic. Without a model, triage uses its rules and Ask is unavailable |
| Agent framework | LangGraph, for triage only | A small fixed graph with one retry loop; no tool-calling, so it works with small local models |
| Auth | JWT bearer tokens, Argon2 passwords, four roles | Enough for a local multi-user demo; users are looked up on every request |
| Frontend | Next.js + Tailwind, client-rendered, proxying `/api` to FastAPI | No CORS; the API stays the only place with business rules |
| Repository | This repo (`feat/restart`); the old Java app stays on `master` | |

## Schema

| Table | Holds | Notes |
|---|---|---|
| `hubs` | gateways, sort centers, delivery centers | Seeded from `config/hubs.yaml`; never deleted, only deactivated |
| `hub_service_areas` | pincode prefix → delivery center | Match a pincode by its longest prefix |
| `routes` | one-way legs between hubs (road or air) | Unique per origin, destination and mode |
| `shipments` | booking details and current state | `status`, `last_event_at`, `last_scan_hub_id`, `delivered_at` are a projection of the events |
| `tracking_events` | every scan | Append-only (trigger). `occurred_at` is simulated time; `recorded_at` is wall-clock |
| `sim_faults` | faults the simulator injected | Ground truth for evals. Not in the `ask` schema |
| `exceptions` | problems the detector found | At most one open per shipment and rule (partial unique index) |
| `actions` | proposed corrective actions and their outcome | One pending proposal per exception and action type |
| `audit_log` | who did what | Append-only (trigger) |
| `users` | logins and roles | |
| `triage_runs` | each diagnosis, its evidence and the actions it proposed | |
| `ask.*` views | read-only reporting views for Text-to-SQL | The only thing the `ask_reader` role can read |

- **One write path.** A shipment's state changes only through `core.db.events` (`record_event`, or `apply_event` for bulk loads). A late event is stored but doesn't overwrite newer state.
- **Enums are VARCHAR + CHECK,** so adding a value is an ordinary migration.
- **Simulated time everywhere.** The network's "now" is its latest tracking event (`core.db.clock.sim_now`). Rules, triage and Text-to-SQL all work as of that time, never the wall clock.

## Simulator (Phase 2)

`core/simulator/` is pure Python working on hub codes; only `run.write` touches the database.

| Module | Does |
|---|---|
| `config.py` | Validates `config/simulator.yaml` |
| `network.py` | Hubs, routes, pincodes; fastest path (Dijkstra). Express may fly, standard goes by road |
| `demand.py` | Each day's bookings: volume, origin and destination, weight, service level, promised date |
| `faults.py` | Picks at most one fault per shipment from the configured rates |
| `movement.py` | Turns a booking into tracking events, applying the fault |
| `run.py` | `simulate` (pure), `write` (bulk insert), `backfill` (both, plus the safety checks) |

- **Deterministic.** Each day's demand and each journey have their own random stream, seeded from `seed`, the date and the tracking number.
- **Promises are achievable.** The promise is the configured SLA, pushed later where the route can't make it. A fault-free shipment is never late, so lateness is a clean signal.
- **Faults:** `pickup_delay` (1–2 days late), `hub_stuck` (held 1.5–4 days), `missed_connection` (waits for the next departure), `misroute` (wrong hub, then re-routed), `failed_delivery` (1–3 failed attempts; 3 means return to origin), `lost` (never scanned after departing).

## Detector (Phase 3)

Seven rules in `core/detector/rules.py`, thresholds in `config/rules.yaml`: `pickup_overdue`, `stuck_at_hub`, `stuck_at_delivery_center`, `in_transit_overdue`, `delivery_failed`, `misrouted`, `sla_breach`.

- Rules rebuild each shipment's state from `tracking_events` up to `as_of`, so the detector can be replayed over history and gives the answer it would have given then.
- One SQL statement evaluates every rule. `run_detector` opens new exceptions, refreshes ones still hitting, and resolves ones that cleared.
- Measured against `sim_faults`: 100% precision, 96.8% shipment-level recall on a simulated month. Misses are faults that began in the last day of the run, and misroutes that never tripped a rule.

## Approvals (Phase 4)

- `config/action_policy.yaml` lists which actions may be proposed for each rule; `core/actions/policy.py` validates their parameters.
- `core/actions/service.py` is the only code that changes an action's status. Approving executes the action in the same transaction, under a row lock, so it runs at most once. Nobody can approve their own proposal; approving needs the supervisor role.
- Proposals accept an `Idempotency-Key` header, so a resent request returns the same action.
- Every login, proposal, decision, execution, triage and question goes to `audit_log`.

Roles: **viewer** (read, ask), **operator** (+ propose, triage, resolve), **supervisor** (+ approve/reject), **admin** (+ run the detector, read the audit log).

## Text-to-SQL (Phase 5)

`core/ask/`: the model writes SQL from a schema description and examples, and gets errors back to repair (up to 3 attempts).

Two layers of defence:

1. **Guard** (`guard.py`, sqlglot): exactly one SELECT, only the six `ask` views, no writes, locking, `SET`, `COPY`, `pg_*`/`lo_*` or other dangerous functions, and a row limit.
2. **Database** (`execute.py`): runs as `ask_reader`, which can read only the `ask` views, in a read-only transaction with a 5 s statement timeout. Tests bypass the guard and confirm the database still refuses.

All 27 adversarial queries are rejected by the guard, and all 20 gold queries run. Model accuracy is scored by `eval/text_to_sql.py` once an LLM is configured.

## Triage (Phase 6)

A LangGraph graph in `core/triage/graph.py`: **gather** evidence → **diagnose** → **validate** against the policy (retry once if the model's output is invalid) → **propose**.

- **Evidence** (`tools.py`) is read as of the exception's last sighting: the timeline, derived signals (time to pickup, longest wait, failed attempts, hubs off the planned path, overdue departures) and hub load.
- **Diagnosers** (`diagnose.py`): an LLM (`prompts/triage.md`), or transparent rules when no model is configured or the model fails twice. The rules are also the baseline the model is scored against.
- **The agent only proposes.** Its actions go into the normal approval queue as `agent:triage`; nothing in the graph can approve or execute.

The rules name the injected fault in 99.7% of cases on simulated data, but their thresholds come from the simulator, so that's a ceiling rather than a real-world estimate.

## Performance (Phase 7)

Measured by `scripts/explain_report.py` on 43k shipments and 410k events (eval/db/results.md):

- The detector's state query was rewritten to consider only live shipments and use the existing `(shipment_id, occurred_at)` index: 112 ms → 20 ms.
- Two indexes kept: `tracking_events (occurred_at)` (the simulated clock) and a partial index on live shipments' last-scan hub (the hubs page).
- Two candidate partial indexes were dropped (about 4 ms saved, with write cost), and partitioning wasn't needed.

## Web UI (Phase 8)

`web/`: Next.js App Router, client components, Tailwind. Pages: overview, exceptions list and detail (timeline, triage, propose, approve, resolve), approval queue, Ask, hubs. The token is kept in `localStorage`, which is fine for a local demo; use an httpOnly cookie for anything shared.

## Layout

```
app/                     # HTTP only: routes, auth, schemas, dependencies
config/                  # hubs, simulator, rules, action policy (YAML)
core/
├── db/                  # models, engine, write path (events.py), seeding, simulated clock
├── simulator/           # config, network, demand, faults, movement, run
├── detector/            # rules (SQL) and the open/refresh/resolve cycle
├── actions/             # policy, service (propose/approve/reject), executors
├── ask/                 # Text-to-SQL: examples, generate, guard, execute
├── triage/              # schema, evidence tools, diagnosers, LangGraph graph
└── llm.py               # the one LLM wrapper
migrations/              # Alembic, 0001–0006
prompts/                 # text_to_sql.md, repair_sql.md, triage.md
eval/                    # harness (own database), detector, text_to_sql, triage; results.md
scripts/                 # seed_hubs, backfill, detect, create_user, explain_report
web/                     # Next.js frontend
tests/
```

## Risks and how they're handled

- **Text-to-SQL safety.** Guard plus database role, read-only transaction, timeout and row limit; adversarial tests for both layers.
- **Actions without approval.** Agents and operators only propose. Execution happens only in `service.approve`, by someone else, once, and is audited.
- **Eval leakage.** `sim_faults` isn't in the `ask` schema, so Text-to-SQL can't read it at the database level. Triage runs as the app user: its evidence queries never touch `sim_faults`, and a test checks the context it builds. That's enforced in code, not by the database.
- **Simulated time.** Everything works as of the latest event.
- **Duplicate exceptions or proposals.** Partial unique indexes.
- **Local LLM quality.** The graph validates model output and falls back to rules. The model hasn't been scored yet; run the evals once a provider is chosen.

## Roadmap

| Phase | Build | Status |
|---|---|---|
| 0 | Tooling, Compose, FastAPI, Alembic, health checks | ✅ |
| 1 | Schema: hubs, routes, shipments, append-only tracking events | ✅ |
| 2 | Simulator and backfill, recording injected faults | ✅ same seed gives the same data |
| 3 | Detector rules | ✅ precision and recall measured |
| 4 | Exceptions and actions API, auth, audit | ✅ |
| 5 | Text-to-SQL with guardrails | ✅ guard and sandbox tested; model accuracy pending an LLM |
| 6 | LangGraph triage with human approval | ✅ rules baseline scored; model accuracy pending an LLM |
| 7 | Performance: indexes, partitions | ✅ before/after timings recorded |
| 8 | Next.js UI, docs | ✅ builds and runs against the API |

Next: choose an LLM, run `uv run python -m eval.run_eval all`, and tune `prompts/` against the results.
