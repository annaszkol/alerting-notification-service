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

## Results (2026-11-21, run 44873402292, commit 9bb3c57)

**Hardware:** GitHub-hosted runner `ubuntu-latest`, 2 vCPU (AMD EPYC 9V74), 7.7 GiB RAM. The whole stack (Redpanda,
Postgres, all services, web) and the generator share these 2 vCPUs. `events.raw` has 12 partitions. One instance of
each service unless stated. Raw samples: `results/<scenario>/samples.csv`, environment: `results/env.txt`.

| Scenario | Events sent | Duration | Alerts expected / delivered | SMS latency p50 / p95 / p99 (max) |
| --- | --- | --- | --- | --- |
| `unique`, 100 events/s offered (89/s achieved) | 6 000 | 67 s | 6 000 / 6 000 | 23.8 s / 38.5 s / 42.3 s (46.1 s) |
| `burst`, 20 copies of each event (129 events/s achieved) | 9 999 | 78 s | 500 / 500 | 0.56 s / 0.93 s / 1.06 s (1.17 s) |
| `outage`, SMS gateway down from s 15 to s 35 | 5 998 | 61 s | 5 998 / 5 998, 0 lost | 34.7 s / 54.9 s / 63.4 s (69.8 s) |
| `kill`, 1 of 2 processors and 1 of 2 delivery workers killed | 5 996 | 70 s | 5 996 / 5 996, 0 lost | 37.6 s / 103.2 s / 114.3 s (118.9 s) |

Processor throughput, draining 20 000 unique events from `events.raw` (delivery and executor stopped):

| Processors | Seconds | Events/s |
| --- | --- | --- |
| 1 | 296.0 | 68 |
| 2 | 281.0 | 71 |
| 4 | 453.4 | 44 |

**What this shows:**

- **Nothing is lost:** every scenario delivered exactly the expected alerts, including the SMS outage and killed instances
  (spec §13 tests 10 and 17 under load).
- **Deduplication works under load:** 10 000 events in bursts of duplicates became 500 alerts with sub-second latency.
- **The bottleneck is the processor on unique events: ~70 events/s** on this machine. Each unique event creates an alert,
  plans notifications (phone + SMS), starts a runbook run and sends `NOTIFY`. At 100 events/s offered the backlog grows,
  so the latency of the `unique` rows is queueing time, not steady-state latency.
- **More processors do not help on 2 vCPUs**: every container shares the same 2 cores, so the test measures CPU
  contention, not horizontal scaling. The scaling scenario has to be repeated on a machine with more cores.
- 1–4 requests per scenario were not accepted (non-202 answer or client timeout; not investigated yet). The generator
  counts them as not sent and leaves them out of the expected alerts; a real sender retries (spec §2).
