# WebSocket messages (owner C, spec §2, §9)

Schema: [`ws-messages.schema.json`](ws-messages.schema.json). Shapes of `AlertSummary`: [`openapi.yaml`](openapi.yaml).

## Connection

- `GET /ws?token=<jwt>` on the API (through the UI origin: `ws://<base>/ws`). The token goes in the query string
  because browsers cannot set headers on a WebSocket.
- Invalid or expired token: the server accepts and closes immediately with code **4401**. The client goes to login.
- Every API instance holds **one** `LISTEN alerts_changed` connection and fans out to its own clients
  (see [`notify.md`](notify.md)). No sticky sessions are needed: every instance sees every change.
- `NOTIFY` is delivered only after commit, so a client never sees a rolled-back change.

## Client → server

| Message | Meaning |
| --- | --- |
| `{"type": "subscribe", "filters": {"team"?: "rates", "user"?: "anna", "severity"?: ["critical"], "state"?: ["open"]}}` | Replaces the current filters. Same meaning as the `GET /alerts` query. No `subscribe` = everything the user may see, default states `open` and `acknowledged`. |
| `{"type": "pong"}` | Answer to `ping`. |

Unknown or malformed messages are ignored (the server logs them); they do not close the connection.

## Server → client

| Message | When |
| --- | --- |
| `{"type": "alert.upsert", "alert": AlertSummary}` | An alert matching the filters was created or changed (state, count, last seen, dead notification, runbook run). Also sent when an alert **stops** matching the filters (e.g. resolved while filtering `state: open`), so the client can drop it. |
| `{"type": "ping"}` | Every 20 s. A client that does not answer twice in a row is disconnected. |
| `{"type": "resync", "reason": "listener_reconnected" \| "slow_consumer"}` | The server may have missed changes. The client reloads the list with `GET /alerts`. |

`alert.title` has the live fields (`{{$count}}`, `{{$state}}`, `{{$alertUrl}}`) already rendered.

## Client rules

1. On connect and on every reconnect: send `subscribe`, then `GET /alerts`, then apply messages that arrive.
2. Apply `alert.upsert` only when `alert.version` is newer than the version already held for that id
   (compare as strings; the format is sortable). This makes reordering and the REST/WS race harmless.
3. Reconnect with backoff 1 s, 2 s, 4 s, … capped at 30 s; reset after a connection that lasted 30 s.
4. Detail pages refetch `GET /alerts/{id}` (and the timeline) when an `alert.upsert` for that id arrives.
