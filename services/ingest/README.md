# Ingest (owner A; written by B)

## HTTP adapter (`app.py`), http://localhost:8080

`POST /ingest/{sourceId}` with the source token (`X-Source-Token: <token>` or `Authorization: Bearer <token>`):

```sh
curl -X POST localhost:8080/ingest/workflows -H 'X-Source-Token: dev-ingest-token' -H 'Content-Type: application/json' \
  -d '{"type":"WORKFLOW_FAILED","workflowId":"WF-1","workflowName":"order-settlement","step":"validate-counterparty",
       "severity":"ERROR","environment":"prod","error":{"code":"TIMEOUT","message":"no answer in 5000 ms"}}'
# -> 202 {"eventId": "..."}; a few seconds later: alert in the UI, phone call + SMS to the settlements on-call
```

- Sources are rows in `sources` (type `http`); the token is stored as SHA-256 (seed: `SEED_INGEST_TOKEN`, default
  `dev-ingest-token`). Unknown source and wrong token both answer `401`.
- Body: JSON (`application/json`) or text (any other content type, e.g. log lines). Limit `INGEST_MAX_BYTES`
  (256 KiB) → `413`; invalid JSON → `422`.
- Envelope `{eventId, sourceId, receivedAt, contentType, payload}` to Kafka `events.raw`, key = event id. `202` only
  after Kafka acknowledged the write; broker down → `503` so the sender retries.
- `Idempotency-Key: <key>` → event id `<sourceId>:<key>`: a retried POST is processed once (`processed_events`).
