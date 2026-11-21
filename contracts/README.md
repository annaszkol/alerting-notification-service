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
| [`openapi.yaml`](openapi.yaml) | C | REST API under `/api`: auth, alerts, rules + tester, runbooks, runs, approvals, mappings, preferences, on-call, suggestions, admin, inbound SMS |
| [`ws-messages.md`](ws-messages.md), [`ws-messages.schema.json`](ws-messages.schema.json) | C | WebSocket `/ws`: subscribe / `alert.upsert` / `ping` / `resync`, client rules |
| [`notify.md`](notify.md), [`notify.schema.json`](notify.schema.json), [`notify.py`](notify.py) | C | `NOTIFY alerts_changed` / `config_changed` payloads and helpers for senders |

`contracts/tests/` validates the spec examples against the schemas (`uv run pytest contracts`).
