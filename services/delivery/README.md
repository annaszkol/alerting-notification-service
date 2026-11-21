# Delivery (owner B, spec §6)

| Module | What |
| --- | --- |
| `planning.py` | `plan_notifications(conn, alert, rule, now)`: called by the processor in its transaction for a new alert; inserts `notifications` rows (outbox). Contract: `contracts/delivery.md`. |
| `directory.py` | users and team membership (`SqlDirectory`; `StaticDirectory` for tests) |
| `channels/` | channel adapters: `smtp` (Mailpit), `fake_sms`, `fake_phone` (mocks/fake_gateway), `websocket` (UI); `registry.create(adapter, config)` |
| `messages.py` | SMS (≤160 chars) and phone texts |
| `__main__.py` | delivery worker (placeholder until `feat/delivery-retry-lease`) |

Current limits (filled in by later branches): `oncall` = whole team (`feat/preferences-oncall`), `schedule` and
`preferences` channels = `ui` (`feat/preferences-oncall`), no throttling (`feat/throttling`).
People and teams come from the `users` / `teams` / `team_members` tables (`directory.py`, `SqlDirectory`);
unknown users or teams in a rule resolve to nobody instead of aborting the processor's batch.

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
