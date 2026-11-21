# Delivery (owner B, spec §6)

| Module | What |
| --- | --- |
| `planning.py` | `plan_notifications(conn, alert, rule, now)`: called by the processor in its transaction for a new alert; inserts `notifications` rows (outbox). Contract: `contracts/delivery.md`. |
| `directory.py` | users, teams, on-call, time zones, preferences (`SqlDirectory`; `StaticDirectory` for tests) |
| `schedule.py` | pure time logic: schedule windows, preference lookup, rotations |
| `presence.py` | work time start / stop: who is away now, what an away person still gets, closing forgotten sessions |
| `channels/` | channel adapters: `smtp` (Mailpit), `fake_sms`, `fake_phone` (mocks/fake_gateway), `websocket` (UI); `registry.create(adapter, config)` |
| `inbound.py` | replies from the gateway: `ACK` / `APPROVE` / `REJECT <code>`, called by `POST /api/inbound/sms` |
| `codes.py` | 6-character reply codes (no 0/O/1/I) |
| `messages.py` | SMS (≤160 chars) and phone texts |
| `worker.py` | claim (lease + `SKIP LOCKED`) → render → send → record, retry with backoff, dead letters |
| `__main__.py` | worker process: `python -m services.delivery` |

Routing (spec §4, §6):

- `oncall: <team>`: the person on duty now from `oncall_schedules` (rotation list, shift length, handoff time,
  time zone, start date); the whole team if the team has no rotation.
- Channels: a list in the rule is used as is; a `schedule` in the rule is evaluated in the **recipient's** time
  zone (`users.timezone`); `preferences` (or omitted) uses `channel_preferences.by_severity` of the person, else the
  rule owner team's defaults (if the person is a member), else their first team's defaults, else built-in
  defaults (info/warning → ui, error → email, critical → sms + email). A missing severity level uses the nearest
  lower level. A preference value can itself be a schedule (working hours).
- People and teams come from `users` / `teams` / `team_members` (`directory.py`, `SqlDirectory`); unknown users or
  teams resolve to nobody instead of aborting the processor's batch.
- Throttling per person and channel (spec §6 step 4): `pg_advisory_xact_lock` on (person, channel), then count
  that person's non-held alert notifications on that channel created in the window. Under the limit → `pending`;
  first time over → one `kind = limit` message ("further alerts only in the UI") + the alert `held`; afterwards
  `held`. Held alerts don't count, so the person is unblocked when the window passes. Critical alerts by phone
  bypass the limit (spec §14). Limits: built-in (SMS 5 / 10 min, phone 5 / 10 min, email 20 / 10 min, UI none)
  < team `channel_preferences.limits` < personal limits; `null` removes a limit for a channel.
- Work time start / stop (spec §6 "Czas pracy"): a person with `work_tracking.enabled` and no open session younger
  than 12 h is *away* and gets only `ui` (alert in the feed, no email / SMS / phone). Exceptions: a recipient
  reached through `oncall` gets everything, and a `critical` phone call always goes through. People without
  tracking are unaffected. Approval requests for runbook steps do not depend on presence.

Processor usage:

```python
from services.delivery.planning import plan_notifications

result = plan_notifications(conn, AlertForPlanning(...), RuleForPlanning(...), now)  # no commit inside
```

## Channels

The `channels` table maps a channel name to an adapter and its config (demo defaults in
`db/migrations/202611211400_channels.sql`). A new provider: a class with a unique `adapter` name decorated with
`@register` in `channels/`, then `UPDATE channels SET adapter = ..., config = ... WHERE name = 'sms'`.
Adapters never raise for provider errors: 2xx → `sent`; timeouts, connection errors, 408/425/429, 5xx and SMTP
4xx → `retryable_error`; other 4xx, SMTP 5xx and missing phone/email → `permanent_error`.

## Worker

- Claims up to 50 rows: `pending` and due, or `sending` with an expired lease (60 s). Committed at once, so any
  number of instances can run (`docker compose up --scale delivery=3`).
- Renders at send time: `{{$count}}`, `{{$state}}`, `{{$alertUrl}}` are current. Links use `PUBLIC_BASE_URL`.
- Sends concurrently (max 20 at a time per worker), then records the result:
  `sent`; retryable → `pending` after 5 s, 10 s, 20 s, … (cap 5 min); 10th failure or a permanent error → `dead`.
  An unknown/disabled channel counts as retryable, so enabling it later delivers the backlog.
- A worker whose lease expired cannot overwrite the result of the worker that took the row over
  (the final UPDATE matches `status = 'sending'` and the attempt number).
- Polls every 0.5 s when idle (`DELIVERY_POLL_SECONDS`); reloads the `channels` table every 30 s.
- Until `alerts` (owner A) is migrated, the worker reads `alerts(id, title, body, severity, state, count)` as in
  spec §8; tests create that minimal table themselves.

## Replies by SMS and phone

- Every alert notification carries the person's `ACK <code>`: one code per (alert, person), the same on all of that
  person's channels; a person who already has a code for the alert keeps it.
- The fake gateway forwards replies and "press 1" to `POST /api/inbound/sms` (router in `services/api/routers/inbound.py`,
  logic in `inbound.py`). `ACK <code>` acknowledges through C's `ack_alert` (audit log + NOTIFY), only from the phone
  of the person the code was sent to; repeating it is harmless; a resolved alert gives `not_allowed`.
  `APPROVE` / `REJECT <code>` go to `services.executor.approvals.decide_by_code`.
- Commands are case-insensitive; phone numbers are compared without spaces, dashes or brackets.

## Connections from other services

Public entry points (`plan_notifications`, `plan_targets`, the executor's `start_run` / `approve` / …, approvals,
inbound) are wrapped in `db.rows.plain_rows`, so they work on any psycopg connection, including the API pool's
dict-row connections, and restore the caller's row factory afterwards.
