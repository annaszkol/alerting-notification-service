# API (owner C, spec §9)

REST API under `/api`, mock login with JWT, role checks. Contract: [`contracts/openapi.yaml`](../../contracts/openapi.yaml)
(interactive docs of what is implemented: `http://localhost:8000/api/docs`). Plan: [`docs/plans/c-api-web.md`](../../docs/plans/c-api-web.md).

| Module | What |
| --- | --- |
| `app.py` | `create_app(settings, pool)`: routers under `/api`, error format, `/healthz` |
| `settings.py` | env: `DATABASE_URL`, `JWT_SECRET`, `JWT_TTL_SECONDS` (default 12 h), `PUBLIC_BASE_URL` (UI origin, used in `{{$alertUrl}}`) |
| `auth.py` | JWT (HS256, only `sub`), `current_user`, `require_admin`, `Principal.can_edit_owned(owner)` |
| `errors.py` | `{"error": {"code", "message", "details"}}` for every error, incl. FastAPI validation |
| `schemas.py` | validators built from `contracts/` (requests are checked against the contract itself) |
| `domain/alerts.py` | feed, details, timeline; `ack_alert` / `resolve_alert` (shared with B's inbound SMS handler) |
| `domain/rules.py` | rule CRUD; every write bumps `config_version` + `NOTIFY config_changed` in the same transaction |
| `ws.py` | WebSocket `/ws`: one `LISTEN alerts_changed` per instance, batched reads by id, per-client filters, ping, `resync` |
| `stubs.py` | TEMPORARY stand-ins for `libs/conditions` (paths, live fields, condition check) until A merges it |

## Login is a mock

`POST /api/auth/login {"userId": "anna"}` returns a token for any seeded user, with no password (spec §9).
`GET /api/auth/users` lists them for the login screen. Roles are read from `users.is_admin` and
`team_members.role` on every request, so the token never carries stale roles.

`JWT_SECRET` must be set outside local demos. Without it the API uses a built-in development secret and logs
a warning.

## Permissions

| Action | Who |
| --- | --- |
| read alerts, rules; ack / resolve | any logged-in user |
| create / edit / disable / delete a team rule | manager of that team, admin |
| personal rule (`owner: {user}`) | that user, admin |
| move a rule to another owner | rights on both owners |

## WebSocket

`ws://<host>/ws?token=<jwt>`, messages in [`contracts/ws-messages.md`](../../contracts/ws-messages.md).

- One `LISTEN alerts_changed` connection per API instance. Ids arriving within 100 ms are read in one query and
  pushed as `alert.upsert` to clients whose `team` / `user` / `severity` filters match (state is filtered by the
  client, so it sees an alert leave its `state` filter).
- Listener lost → reconnect with backoff (0.5 s … 10 s) → `resync` to every client.
- A client that falls 2000 messages behind gets one `resync` instead of the backlog.
- Close codes: `4401` invalid / expired token, `4408` two unanswered pings (ping every 20 s).

## For other services

- `services.api.domain.alerts.ack_alert(conn, alert_id, actor, via)` and `resolve_alert(conn, alert_id, actor,
  note=, how=, via=)` run in the caller's transaction, write `audit_log` and `NOTIFY alerts_changed`. They raise
  `AlertNotFound` / `AlertStateConflict` (`.code` is `not_found` / `conflict`).
- `contracts.notify.notify_alerts_changed(cursor, ids)` sends `alerts_changed` with the agreed payload.

## Tests

`uv run pytest services/api` (DB tests need `TEST_DATABASE_URL`). Until A's migrations for `rules`,
`config_version` and `alerts` are merged, the tests create those tables from
[`tests/sql/assumed_tables.sql`](tests/sql/assumed_tables.sql); in compose those endpoints return 500 until then.
