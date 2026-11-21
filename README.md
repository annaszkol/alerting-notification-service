# Flexible Alerting & Notification Service

Hackathon 2026, Software Engineering track. Specification: [`docs/specs.md`](docs/specs.md).
Rules for humans and AI agents: [`AGENTS.md`](AGENTS.md).

Events in any schema come in from several sources (HTTP, Kafka topics, log files), are mapped to a common letter,
matched against team rules, deduplicated into alerts and delivered to people (UI, email, SMS, phone) with runbooks
that help resolve them.

```mermaid
flowchart LR
  HTTP[HTTP ingest] --> K[(Kafka events.raw)]
  KA[Kafka adapter] --> K
  T[Log tailer] --> K
  K --> P[Processor<br/>map, rules, dedup]
  P -->|one transaction per batch| PG[(Postgres)]
  PG -->|outbox, SKIP LOCKED| D[Delivery workers]
  PG -->|runbook steps, SKIP LOCKED| E[Executor]
  D --> SMS[SMS / phone / email]
  PG -->|NOTIFY alerts_changed| API[API + WebSocket]
  API --> UI[Web UI]
```

Postgres is the only source of truth (no Redis); details in spec §2.

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

## What is mocked

| Real system | In the demo | Where |
| --- | --- | --- |
| SMS and voice provider | fake gateway: records messages, reply form, "press 1", outage toggle | `mocks/fake_gateway/`, :8090 |
| SMTP server | Mailpit catches every email | :8025 |
| Workflow engine, orchestrator, tickets, counterparty service, webhooks | one mock with failure toggles | `mocks/action_targets/`, :8091 |
| Single sign-on | login picks a seeded user, no password (JWT signed with `JWT_SECRET`) | `services/api/auth.py` |
| Real event producers | demo generator (`python -m mocks.generator ...`) and `curl` | `mocks/generator/` |
| Kafka cluster | one Redpanda node | compose |

Everything else (ingest, mapping, rules, dedup, outbox, retries, throttling, on-call, approvals, runbook execution)
is the real implementation.

## Recipes

Configuration lives in Postgres. The demo values come from `deploy/seed/config.yaml` (sources, mappings, rules),
`deploy/seed/people.yaml` (users, teams, on-call, preferences) and `deploy/seed/runbooks.yaml`. Each recipe can be done
in the seed file (then `docker compose -f deploy/docker-compose.yml run --rm seed python -m deploy.seed.load --reset`)
or through the API (`http://localhost:8000/api/docs`). The processor picks up config changes within 5 s, no restart.

**New source.** Add an entry under `sources:` (`type: http | kafka | logfile`, `parser: json | logfmt | regex` (regex: named groups in `parser_config.pattern`), for
Kafka `config: {topic: ...}`, for logs `config: {path: ...}`), or `POST /api/sources` as admin. HTTP sources send
`POST /ingest/<sourceId>` with `X-Source-Token` (or `Authorization: Bearer`); body limit 256 KiB, optional
`Idempotency-Key`. The tailer picks up new log sources within 30 s; the Kafka adapter reads its topic list at start, so restart
it after adding a Kafka source (`docker compose -f deploy/docker-compose.yml restart kafka-adapter`).

**New schema (mapping).** Add a mapping under `mappings:`: `detect` (a condition that recognises the schema),
`priority`, and `fields` that build the letter (`$service`, `$severity` with a value map, `$eventTime`, `$summary`,
`$correlationKey`, ...). Events that no mapping recognises are kept and listed in `GET /api/unmapped`; try a mapping on
real samples with `POST /api/mappings/preview` before saving it. Paths: `a.b`, `items.0.id`, `labels["a.b"]`.
Field and condition syntax: spec §3, `contracts/`.

**New rule.** Add a rule under `rules:`: `owner`, `when` (condition), `route` (who: `oncall`, `team` or `user`, and
which channels, optionally by time of day), `present` (title / body templates, `{{field}}`, live `{{$count}}`),
`dedup` (key fields and `until_resolved` or a `window`), optional `autoResolveAfter` and `runbooks`. Test it against
recent events with `POST /api/rules/test` before saving (`POST /api/rules`). Spec §4.

**New channel provider.** Write an adapter class with a unique `adapter` name, decorate it with `@register` in
`services/delivery/channels/`, then point a channel at it: `UPDATE channels SET adapter = ..., config = ... WHERE
name = 'sms'`. Adapters return `sent`, `retryable_error` or `permanent_error`; the worker retries with backoff.
Details: `services/delivery/README.md`.

**New runbook action.** Add a class with `name`, `params_schema`, `min_approval` and `execute(params, ctx)` to
`build_registry` in `services/executor/actions/builtin.py`. HTTP actions may only call hosts listed in
`ACTION_HTTP_ALLOWLIST`. Details: `services/executor/README.md`.

**New runbook.** Write it as YAML (steps with actions, retries, approvals), Markdown or a checklist; add it to
`deploy/seed/runbooks.yaml` or import it with `POST /api/runbooks/import`. A team manager approves it
(`POST /api/runbooks/<id>/approve`); only approved runbooks run actions. Link it from a rule with `runbooks: [...]`.

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

## Load test

Scripts, scenarios and raw results: [`loadtest/README.md`](loadtest/README.md). Results (events/s, p50/p95/p99
end to end, hardware, instance count, duration): see that file, filled in from measured runs only.

## Security

- HTTP ingest requires the source token (stored as SHA-256) and limits the body to 256 KiB.
- Runbook HTTP actions only reach allowlisted hosts, checked after the template is rendered (SSRF).
- Templates never execute code; condition evaluation never throws.
- Approvals: the role is checked in the API; an SMS approval is accepted only from the addressee's number and with the
  code of that request.
- Secrets only in environment variables; the repo holds `.env.example`, never `.env`.

## Known limitations

- One Redpanda node in the demo (production: 3 nodes, replication factor 3).
- Replay of events older than 24 h is not supported (they are not kept for reprocessing).
- Log lines are parsed as JSON, logfmt or with one regex per source; a custom multi-line log format needs its own parser.
- Login is a mock (no passwords, no SSO).
- The Kafka adapter reads its list of external topics at start; new Kafka sources need its restart.
- The fake gateway keeps messages in memory; restarting it clears them.

## How we used AI tools

We built the service with AI coding agents (Claude Code), each working on one short branch under the rules in
[`AGENTS.md`](AGENTS.md): read the spec sections and `contracts/` first, propose a plan and tests, stay inside the
branch's directories, and never change a contract silently.

How we checked the output:

- every branch adds tests; CI runs ruff, pytest with a real Postgres and a full `docker compose up` with an end-to-end
  smoke test (`deploy/smoke-test.sh`) on every PR;
- every PR was reviewed by a person before merge, and merges were done by people;
- the spec's acceptance tests (spec §13) are the definition of done;
- load-test numbers are measured by scripts in `loadtest/`, never estimated.

No secrets, tokens or real personal data went into prompts, code or fixtures: demo people use fake phone numbers and
the reserved `example.test` domain.
