# Mocks (owner B)

## Fake SMS / phone gateway (`fake_gateway/`), http://localhost:8090

- Page: outbound SMS and calls, a reply form (`ACK` / `APPROVE` / `REJECT <code>` from any number),
  a "Press 1" button on calls, and an **outage** toggle per channel (demo: spec §6, §13 test 10).
- `POST /sms`, `POST /calls` `{to, text, idempotencyKey, replyCode?}`: `202`; `200` for a repeated
  `idempotencyKey` (recorded once, counted in `duplicates`); `503` during an outage.
- `GET /api/messages?channel=sms|phone&direction=out|in`: everything received, with `receivedAt` (ms), used as the
  end of the latency measurement (spec §10). `DELETE /api/messages` clears.
- `POST /api/outage {"sms": true}`: toggle from scripts.
- Replies and "press 1" are forwarded to `INBOUND_SMS_URL` (`POST /api/inbound/sms`, contracts/delivery.md); the
  API's answer is shown next to the reply. Until `feat/inbound-sms-codes` lands, replies show `http_404`.
- State is in memory: restarting the container clears it.

Email goes to Mailpit (compose, http://localhost:8025).

## Action targets (`action_targets/`), http://localhost:8091

Mock counterparty-service (`GET /health`), workflow engine (`POST /workflows/<id>/retry`), orchestrator
(`POST /services/<name>/restart`), tickets (`POST /tickets`) and webhook receiver (`POST /webhook`), one container
with those compose host names. The page lists calls and toggles failures; scripts use
`POST /api/fail {"target": "workflow", "times": 2}` (`-1` = always, `0` = never).
