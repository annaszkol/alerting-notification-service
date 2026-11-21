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

## Log tailer (`tailer.py`), compose service `tailer`

Follows every enabled `logfile` source (`config.path`). Sends complete lines only; event id `<source>:<inode>:<byte
offset>` (stable, so a re-read after a crash is dropped by `processed_events`); offset saved in `tailer_offsets` only
after Kafka acknowledged the lines; rotation (new inode) or truncation starts from 0. Demo lines:

```sh
docker compose -f deploy/docker-compose.yml exec tailer python -m mocks.generator pricing --lines 2
```

The pricing-engine log format is ours (it is a mock): `ts=… level=WARN service=pricing-engine-2 instrument=EURUSD msg="…"`.

## Kafka adapter (`kafka_adapter.py`), compose service `kafka-adapter`

Reads the topics of enabled `kafka` sources (`config.topic`; demo: `workflow-events` → source `workflows-kafka`),
event id `<source>:<topic>:<partition>:<offset>`, commits the external offsets only after `events.raw` acknowledged.

## Demo events (`mocks/generator`)

`python -m mocks.generator workflow | trade [--notional N] | high-error-rate [--resolved] | burst --count 500 |
pricing --lines 2 | unknown`: the spec's sample events, sent to the HTTP adapter (`INGEST_URL`, `SEED_INGEST_TOKEN`).
