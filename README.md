# Flexible Alerting & Notification Service

Hackathon 2026, Software Engineering track. Specification: [`docs/specs.md`](docs/specs.md).
Rules for humans and AI agents: [`AGENTS.md`](AGENTS.md).

> Status: repo skeleton. Services are placeholders until their feature branches land (spec §12).

## Quick start

Requires Docker with Compose v2.

```sh
docker compose -f deploy/docker-compose.yml up --build
```

No `.env` is needed; defaults are in the compose file. To override them, copy `.env.example` to `deploy/.env`.

| What | Address |
| --- | --- |
| **Web UI** (login: pick a seeded user) | http://localhost:3000 |
| API (`/healthz`, docs: `/api/docs`) | http://localhost:8000 |
| HTTP ingest (`/healthz`) | http://localhost:8080 |
| Mailpit (caught emails) | http://localhost:8025 |
| Fake SMS / phone gateway (messages, replies, outage toggle) | http://localhost:8090 |
| Runbook action mocks (call log, failure toggles) | http://localhost:8091 |
| Postgres | `localhost:5432`, user / password / db: `alerting` |
| Redpanda (Kafka API) | `localhost:19092`, topic `events.raw` (12 partitions) |

Check that the stack is healthy: `deploy/smoke-test.sh`.

Video demo, scene by scene (sends the events, toggles the mocks, says what to show): `deploy/demo.sh`
(scenes `events burst outage runbook unknown`; CI rehearses all but `unknown` on every PR).

Demo data (`deploy/seed/people.yaml`): users `admin`, `anna`, `bob`, `carol`, `dave`, `erin`, `frank`, `grace`, `henry`,
`ivan`, `julia` in teams settlements, pricing, rates, payments and counterparty-support. Details and on-call rotations:
[`deploy/seed/README.md`](deploy/seed/README.md).

## Development

```sh
uv sync                     # Python 3.13+, installs runtime + dev dependencies into .venv
uv run ruff check .         # lint
uv run ruff format .        # format
uv run pytest               # tests; DB tests are skipped unless TEST_DATABASE_URL is set
```

DB tests create and drop their own temporary databases, so any Postgres works, e.g. the one from compose:

```sh
docker compose -f deploy/docker-compose.yml up -d postgres
TEST_DATABASE_URL=postgresql://alerting:alerting@localhost:5432/alerting uv run pytest
```

All Python processes share one image (`Dockerfile`); compose picks the process with `command:`
(`python -m services.<name>` or `uvicorn services.<name>.app:app`).

## API and login

REST API under `/api` (contract: [`contracts/openapi.yaml`](contracts/openapi.yaml), details:
[`services/api/README.md`](services/api/README.md)). **Login is a mock:** pick any seeded user, no password
(`POST /api/auth/login {"userId": "anna"}` returns a JWT). Set `JWT_SECRET` outside local demos.

## Database migrations

- Files: `db/migrations/<YYYYMMDDHHMM>_<name>.sql` (lowercase name, `_` only). Each owner adds migrations for their own tables.
- Compose runs `python -m db.migrate` once before the app services start. Each file runs in its own transaction
  and is recorded in `schema_migrations`.
- Never edit a merged migration: the runner stores a checksum and refuses to start if an applied file has changed.
  Add a new migration instead.
- Branches merge out of order, so an older, not yet applied timestamp still runs.

## Repo layout

| Path | Owner | Contents |
| --- | --- | --- |
| `contracts/` | shared | interfaces between components |
| `libs/conditions/` | A | condition evaluator + templater |
| `services/ingest/`, `services/processor/` | A | adapters, mapping, rules, dedup, alert lifecycle |
| `services/delivery/`, `services/executor/`, `mocks/` | B | delivery, channels, scheduler, runbook actions, mocks |
| `services/api/`, `web/` | C | REST API, auth, WebSocket, frontend |
| `db/migrations/` | each owner | SQL migrations |
| `deploy/` | B | compose, seed, smoke test |
| `loadtest/` | A | load-test scripts and raw results |

## CI

GitHub Actions (`.github/workflows/ci.yml`) on every PR and on `main`:

- `lint-test`: ruff, then pytest with a Postgres service (DB tests included).
- `compose-smoke`: `docker compose up --build`, then `deploy/smoke-test.sh`.
