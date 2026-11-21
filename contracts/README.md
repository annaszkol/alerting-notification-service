# Contracts

Interfaces between components (spec §12, "Kontrakty"). Changes go in a separate `contracts/...` PR,
are additive only and need approval from all three owners.

| Contract | Owner |
| --- | --- |
| Event envelope, letter fields, path syntax, condition JSON, template syntax | A |
| `plan_notifications(alert, rule)` signature, channel adapter interface, canonical runbook model, action interface | B |
| OpenAPI, WebSocket and `NOTIFY` message formats | C |

Each owner fills in their part on a `contracts/v1-...` branch.

## Files

| File | Owner | What |
| --- | --- | --- |
| [`delivery.md`](delivery.md), [`delivery.py`](delivery.py) | B | `plan_notifications`, channel adapter, message formats, reply codes |
| [`route.schema.json`](route.schema.json) | B | `rule.route` (recipients and channels) |
| [`runbooks.md`](runbooks.md), [`runbook.schema.json`](runbook.schema.json), [`actions.py`](actions.py) | B | canonical runbook, action interface and registry |

`contracts/tests/` validates the spec examples against the schemas (`uv run pytest contracts`).
