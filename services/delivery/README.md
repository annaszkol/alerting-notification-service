# Delivery (owner B, spec §6)

| Module | What |
| --- | --- |
| `planning.py` | `plan_notifications(conn, alert, rule, now)`: called by the processor in its transaction for a new alert; inserts `notifications` rows (outbox). Contract: `contracts/delivery.md`. |
| `directory.py` | users, teams, on-call, time zones, preferences (`SqlDirectory`; `StaticDirectory` for tests) |
| `schedule.py` | pure time logic: schedule windows, preference lookup, rotations |
| `channels/` | channel adapters: `smtp` (Mailpit), `fake_sms`, `fake_phone` (mocks/fake_gateway), `websocket` (UI); `registry.create(adapter, config)` |
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
- Not yet: throttling (`feat/throttling`, limits will be read from `channel_preferences.limits`).

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
