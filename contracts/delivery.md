# Delivery contract (owner B, spec §6)

Code: [`delivery.py`](delivery.py). Route schema: [`route.schema.json`](route.schema.json).

## `plan_notifications(conn, alert, rule, now) -> PlanResult`

Called by the processor **inside its batch transaction**, only for a newly inserted alert (spec §5, step 4.1).
It never commits and never sends anything; it only inserts rows into `notifications` (outbox).
Updates of an already open alert (`count = count + n`) do **not** call it.

1. **Recipients** from each `rule.route` entry:
   `user` → that user; `team` → all members; `{team, role}` → members with that role;
   `oncall` → the person currently on duty in the team's rotation (at `now`).
2. **Channels** per recipient: a list → as is; a schedule → first `between` window that contains `now` in the
   **recipient's** time zone, else `otherwise`; `"preferences"` (or omitted) → the recipient's
   `channel_preferences` for the alert severity, falling back to the team defaults. A channel from the rule wins
   over a preference.
3. **One person, one channel, one event:** `INSERT ... ON CONFLICT (user_id, channel, dedup_key) DO NOTHING`.
   `dedup_key` = `alert.first_event_id` for `kind = alert`, the window start for `kind = limit`,
   the approval id for `kind = approval`. A person matched by two rules for the same event gets one message.
4. **Throttling** per (user, channel): `pg_advisory_xact_lock(hashtext(user_id || ':' || channel))`, then count
   that user's notifications on that channel in the window. Under the limit → `pending`. First time over →
   one `kind = limit` row (`pending`) and the current one `held`. After that → `held`.
   Defaults: SMS 5 / 10 min, phone 5 / 10 min, email 20 / 10 min, UI unlimited. `critical` on `phone` bypasses
   the limit (spec §14).
5. Each `pending` row gets `next_attempt_at = now` and a `reply_code` (see below).

The UI feed does not depend on notifications: an alert is always visible in its owner's feed.

**Stub for the processor until `feat/notifications-outbox` lands:** all members of the owner team (or the owner
user), channel `ui`.

## Channel adapter

```python
class ChannelAdapter(Protocol):
    adapter: str

    def __init__(self, config: dict) -> None: ...
    async def send(self, message: Message) -> SendResult: ...  # sent | retryable_error | permanent_error
```

- One class per provider, chosen by `channels.adapter`, configured by `channels.config`. A real provider
  (e.g. Twilio) is a new class plus a row in `channels`; nothing else changes.
- `send` never raises for provider errors. Timeouts, connection errors and 5xx → `retryable_error`;
  invalid address and other 4xx → `permanent_error`.
- `message.notification_id` is passed to the provider as the idempotency key (delivery is at-least-once).

Worker side (for reference): claim batches with `FOR UPDATE SKIP LOCKED` and a 60 s lease; `retryable_error`
→ `pending`, backoff 5 s, 10 s, 20 s, … capped at 5 min, max 10 attempts; `permanent_error` or exhausted
attempts → `dead`.

## Messages per channel

Rendered at send time from the alert's stored title/body (live fields filled in now).

| Channel | Content |
| --- | --- |
| `ui` | full: title, body, fields, runbook, buttons (WebSocket format: contract owned by C) |
| `email` | subject = title; body = full text + fields + runbook steps + link to the alert; approvals: Approve / Reject links |
| `sms` | max 160 chars: `[CRITICAL] <title> <base>/a/<id> ACK <code>`; title is truncated with `…` to fit |
| `phone` | short text to be read out: `<severity> alert. <title>. Press 1 to acknowledge.` |

## Reply codes and inbound replies

- `reply_code`: 6 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (no 0/O/1/I), unique among open codes.
- `kind = alert`: one code per (alert, user), reused across that user's channels. `ACK <code>` acknowledges.
- `kind = approval`: the approval's own code. `APPROVE <code>` / `REJECT <code>` decides that one step only.
- A code is accepted only from the phone number of the user it was issued to (and, for approvals, only if that
  user still has the required role). Commands are case-insensitive; extra whitespace is ignored.
- Inbound SMS from the fake gateway: `POST /api/inbound/sms` with
  `{"from": "+48...", "text": "ACK K7Q2MX", "receivedAt": "<ISO 8601>", "gatewayMessageId": "..."}` → `200`
  with `{"result": "acknowledged" | "approved" | "rejected" | "unknown_code" | "not_allowed"}`.
  The gateway shows the result as the reply. Handler implemented by B on `feat/inbound-sms-codes`.
- Phone: the fake gateway's "press 1" button calls the same endpoint with `"text": "ACK <code>"`.
- Email approve / reject links: `<base>/approvals/<token>`, the token is signed, single-use and expires (24 h).
- `<base>/a/<id>` redirects to the alert page after login.
