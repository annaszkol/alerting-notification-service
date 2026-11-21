# Delivery (owner B, spec §6)

| Module | What |
| --- | --- |
| `planning.py` | `plan_notifications(conn, alert, rule, now)`: called by the processor in its transaction for a new alert; inserts `notifications` rows (outbox). Contract: `contracts/delivery.md`. |
| `directory.py` | users and team membership (`SqlDirectory`; `StaticDirectory` for tests) |
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
