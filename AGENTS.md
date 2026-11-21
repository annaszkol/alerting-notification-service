# AGENTS.md

Instructions for AI coding agents (Claude Code, Cursor, Copilot, Codex, ...) and humans working in this repo.
Keep this file short. Details live in `docs/specs.md`. Changes to this file go through a PR.

## Project

Flexible Alerting & Notification Service (Hackathon 2026, Software Engineering track).
Ingests events in arbitrary schemas from several sources, matches them against team-defined rules,
deduplicates them into alerts, delivers notifications (UI, email, SMS, phone) and helps resolve them with runbooks.

## Sources of truth

1. `docs/specs.md` - the full specification (sections are numbered; cite them as "spec §5").
2. `contracts/` - event envelope, letter fields, condition JSON, template syntax, canonical runbook schema,
   OpenAPI, WebSocket / NOTIFY message formats.
3. `docs/specs.md` §13 - acceptance tests. A branch is done when its tests pass on `main`.

If code, spec and contracts disagree, stop and ask. Never silently change a contract to make code compile.

## Architecture in one paragraph

Adapters (HTTP, Kafka, log tailer) write an envelope to Kafka topic `events.raw`. The processor reads a batch,
processes it in ONE Postgres transaction (idempotency, mapping, rule matching, alert upsert, notification outbox,
runbook runs, `NOTIFY`), then commits the Kafka offset. Delivery workers and the runbook executor claim work from
Postgres with `FOR UPDATE SKIP LOCKED`. The API pushes changes to browsers over WebSocket after `LISTEN alerts_changed`.
There is no Redis: Postgres is the only source of truth. Diagram: spec §2.

## Repo layout and owners

| Path | Owner | Contents |
| --- | --- | --- |
| `contracts/` | shared (PR + all three approve) | interfaces between components |
| `libs/conditions/` | A | condition evaluator + templater |
| `services/ingest/`, `services/processor/` | A | adapters, mapping, rules, dedup, alert lifecycle |
| `services/delivery/`, `services/executor/`, `mocks/` | B | outbox delivery, channels, scheduler, runbook actions, mock gateways |
| `services/api/`, `web/` | C | REST API, auth, WebSocket, frontend |
| `db/migrations/` | each owner for own tables | `<YYYYMMDDHHMM>_<name>.sql`, never edit a merged migration |
| `deploy/` | B | `docker-compose.yml`, seed data |
| `loadtest/` | A | load-test scripts and raw results |

Work inside the directories of your branch. If you need a change elsewhere, say so instead of making it.

## Commands

<!-- TODO: fill in after the backend stack is chosen (spec §14). Do not guess. -->
- Start everything: `docker compose -f deploy/docker-compose.yml up`
- Smoke-test a running stack: `deploy/smoke-test.sh`
- Run tests: `uv run pytest` (DB tests need `TEST_DATABASE_URL`, see README)
- Lint / format: `uv run ruff check .` / `uv run ruff format .`
- Apply migrations: `DATABASE_URL=... uv run python -m db.migrate` (compose does it on `up`)
- Load test: TODO

## Invariants (do not break these)

- **One transaction per processor batch.** Insert `processed_events` first and skip already-processed event ids.
  Commit the Kafka offset only after the transaction commits.
- **One open alert per fingerprint**, enforced only by the partial unique index
  `ON alerts (fingerprint) WHERE state <> 'resolved'`. Pattern: `INSERT ... ON CONFLICT ... DO NOTHING RETURNING id`,
  and if nothing is returned, `UPDATE ... count = count + n`. Do not use the `xmax = 0` trick.
- **Notifications are created in the same transaction as the alert** (outbox). The processor never sends anything.
- **Delivery is at-least-once**; the notification id is the idempotency key passed to providers.
- **Condition evaluation never throws.** Missing field or wrong type evaluates to `false` (except `exists: false`, `not`).
- **Templates never execute code.** Live fields (`{{$count}}`, `{{$state}}`, `{{$alertUrl}}`) are rendered at display
  and send time, everything else once from the first event.
- **Paths:** `a.b` is nested, numeric segments index arrays, keys containing dots use brackets: `labels["a.b"]`.
- **Runbook actions run only from manager-approved runbooks.** A runbook may raise an action's approval level,
  never lower it below the action's minimum. HTTP actions only call allowlisted hosts.
- **No new infrastructure** (Redis, another broker, another database) without a team decision recorded in the spec.
- **Secrets only in environment variables.** The repo holds `.env.example`, never `.env`.

## Git workflow

- Short branches from `main`, named `feat/...`, `chore/...`, `contracts/...`, `docs/...`. The branch queue is in spec §12.
- One branch = one thing, a few hours to at most a day. Open a PR, get a quick review from another person, merge.
- `main` must always start with `docker compose up` and have green CI. Rebase on `main` before opening a PR.
- If a dependency from another person is not merged yet, code against the interface in `contracts/` and use a stub.
- Contract changes are a separate PR to `contracts/`, additive only (add fields, never change or remove them).
- No long-lived per-person branches.

## How agents should work here

1. Before writing code, read the spec sections for the branch and the relevant files in `contracts/`.
2. Propose a short plan and the tests you will write, then implement.
3. Every branch adds or updates tests; name acceptance tests after their spec §13 number where applicable.
4. Keep changes inside the branch's scope. Do not refactor other owners' code.
5. When behaviour or setup changes, update the matching README section in the same PR.
6. Do not paste secrets, tokens or real personal data into prompts, code or test fixtures.
