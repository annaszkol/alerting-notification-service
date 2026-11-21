# Load test (owner A)

Scripts and raw results for spec §10. Numbers in this file are measured, never estimated.

## How it measures

- **Generator** [`run.py`](run.py) sends `WORKFLOW_FAILED` events for the seeded rule `settlements-prod-failures`
  (dedup by `workflowId`, SMS to the settlements on-call). Every unique event has `workflowId = LT-<run>-<seq>` and
  carries `sentAt`. Events go through the HTTP ingest (`--via http`, waits for the Kafka ack like a real sender) or
  straight into `events.raw` (`--via kafka`). We use a Python asyncio generator instead of k6 so that the same script
  both sends the events and matches them to SMS, with no extra tool to install.
- **End of measurement:** the fake SMS gateway records `receivedAt` for every message. Latency = `receivedAt` of the first
  SMS for a `workflowId` − its `sentAt`. Generator and gateway run on one machine, so they share a clock. Percentiles are
  computed from the raw samples in `results/<scenario>/samples.csv`.
- **Correctness:** every run checks delivered alerts = expected alerts (exit code 1 otherwise).
- **Throttling** (5 SMS / 10 min per person) is lifted by `scenarios.sh`, otherwise almost every SMS would be `held`.
  Restore the demo limits with `docker compose -f deploy/docker-compose.yml run --rm seed`.
- **Prometheus snapshots** of the processor and delivery (`results/<scenario>/metrics-*.txt`, see README "Metrics").

## Scenarios ([`scenarios.sh`](scenarios.sh))

| Scenario | What happens | Pass condition |
| --- | --- | --- |
| `unique` | `RATE` (default 100) unique events/s for 60 s over HTTP | every alert gets its SMS; p50/p95/p99 |
| `burst` | 5 × `RATE` events/s for 20 s, every event 20 times (duplicate burst) | one alert and one SMS per unique event |
| `outage` | like `unique`, SMS gateway returns 503 from second 15 to 35 | everything delivered after retries |
| `kill` | 2 processors + 2 delivery workers, one of each `docker kill`ed at second 20/22, restarted at 40 | everything delivered |
| `scale` | backlog of 20 000 events in `events.raw`, drained by 1 → 2 → 4 processors (delivery and executor stopped to isolate the processor) | `results/scale.csv`: events/s per instance count |

## Running it

```bash
docker compose -f deploy/docker-compose.yml up -d --build
loadtest/scenarios.sh                  # all scenarios, ~10 min
RATE=200 loadtest/scenarios.sh unique  # one scenario, other rate
uv run python loadtest/run.py --help   # single runs, fault injection with --at SECONDS COMMAND
```

In CI: the workflow **Load test** (`.github/workflows/loadtest.yml`) runs on pushes that touch `loadtest/` and on
manual dispatch, then uploads `loadtest/results/` as an artifact.

## Results

Not measured yet. After a run, copy the artifact into `results/` and fill this section (hardware from `results/env.txt`).
