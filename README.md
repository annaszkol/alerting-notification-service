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

## Metrics

Prometheus text format (spec §10 point 3; definitions in [`libs/metrics.py`](libs/metrics.py)). The ingest app serves
`GET /metrics` on its own port (`http://localhost:8080/metrics`); worker processes serve `/metrics` on `METRICS_PORT`:
processor 9101, delivery 9102, executor 9103, tailer 9104, kafka-adapter 9105. These ports are not published by
compose (scaled instances would clash); read them inside the network, e.g.
`docker compose -f deploy/docker-compose.yml exec processor python -c "import urllib.request as u; print(u.urlopen('http://localhost:9101/metrics').read().decode())"`.

| Metric | Meaning |
| --- | --- |
| `ingest_events_accepted_total{adapter}` | events acknowledged by Kafka in `events.raw` (`http`, `logfile`, `kafka`) |
| `ingest_events_rejected_total{reason}` | refused HTTP requests (`unauthorized`, `too_large`, `invalid`, `unavailable`) |
| `processor_events_total{result}` | events processed (`new`, `duplicate`); events/s = `rate(...[1m])` |
| `processor_letters_total{mapped}`, `processor_alerts_total{change}` | letters after mapping, alerts created / updated / resolved |
| `processor_batch_seconds` | histogram of one batch (transaction + offset commit) |
| `processor_consumer_lag{partition}` | messages behind the end of `events.raw`, refreshed every 5 s |
| `delivery_attempts_total{channel,outcome}` | send attempts: `sent`, `pending` (will retry), `dead` |
| `notifications_queue{status}` | notifications per status, counted in Postgres every 5 s |
| `executor_steps_total{outcome}` | runbook step runs: `succeeded`, `waiting`, `skipped`, ... |

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
