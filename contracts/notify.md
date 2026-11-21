# Postgres `NOTIFY` channels (owner C, spec §2, §4, §5)

Schema: [`notify.schema.json`](notify.schema.json). Helpers for senders: [`notify.py`](notify.py).

`NOTIFY` is always sent **inside the transaction that made the change** (`SELECT pg_notify(channel, payload)`),
so Postgres delivers it only after commit and drops it on rollback. Payloads carry ids only: the limit is
8000 bytes, and listeners read the current rows themselves. Notifications can be lost when a listener is
disconnected, so every listener also has a fallback (below).

## `alerts_changed`

- Payload: `{"ids": [<alert id>, ...]}`, at most **350** ids per message (≤ 7 KB even with 19-digit `bigint`
  ids). A sender with more ids sends several messages in the same transaction
  (`contracts.notify.alerts_changed_payloads(ids)` / `notify_alerts_changed(cursor, ids)` do the split).
- Sent whenever a row of `alerts` changes in a way the UI shows: insert, `count` / `last_seen`, state, severity,
  title, a notification turning `dead`, a runbook run / step / approval change of that alert.
- Senders: processor (A, step 6 of the batch algorithm), executor and scheduler (B), API (C: ack, resolve,
  manual steps, approvals), inbound SMS handler (B).
- Listener: each API instance (one connection), which reads the rows by id and pushes `alert.upsert`
  ([`ws-messages.md`](ws-messages.md)). Fallback: after re-`LISTEN` the API sends `resync` to its clients.

## `config_changed`

- Payload: `{"version": <config_version after the change>}`.
- Sent in the same transaction that bumps `config_version`: changes of sources, mappings, rules and runbooks.
- Senders: API (C). Listeners: processors (A) reload rules and mappings. Fallback: processors also poll
  `config_version` every 5 s (spec §4).
