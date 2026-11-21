# Contracts

Interfaces between components (spec §12, "Kontrakty"). Changes go in a separate `contracts/...` PR,
are additive only and need approval from all three owners.

| Contract | Owner |
| --- | --- |
| Event envelope, letter fields, path syntax, condition JSON, template syntax | A |
| `plan_notifications(alert, rule)` signature, channel adapter interface, canonical runbook model, action interface | B |
| OpenAPI, WebSocket and `NOTIFY` message formats | C |

Filled in on branch `contracts/v1`.
