# Processor (owner A; written by B with the team's OK)

`python -m services.processor`: reads envelopes from Kafka `events.raw`, turns them into alerts and outbox
notifications in **one Postgres transaction per batch**, then commits the Kafka offsets (spec §5, AGENTS.md).

| Module | What |
| --- | --- |
| `config.py` | rules, mappings and sources from Postgres; reloaded when `config_version` changes (checked every 5 s) |
| `batch.py` | `process_batch`: the spec §5 algorithm |
| `consumer.py` | envelope decoding, transaction with retry on deadlock / serialization failure, offset commit after it |
| `__main__.py` | Kafka consumer loop (`kafka-python`, auto commit off, batches up to `PROCESSOR_BATCH`) |

## One batch

1. `INSERT INTO processed_events ... ON CONFLICT DO NOTHING RETURNING`: replays and retries are no-ops.
2. Mapping (`libs/conditions`), rule matching, fingerprints. Firing letters grouped per fingerprint (count, first
   letter, latest event).
3. Per fingerprint, sorted: `INSERT ... ON CONFLICT (fingerprint) WHERE state <> 'resolved' DO NOTHING RETURNING
   id`; new alert → `plan_notifications`, `alert.created` audit entry, approved `mode: auto` runbooks started;
   otherwise `UPDATE ... count = count + n` (back to the INSERT if the alert was resolved meanwhile, max 3 times).
4. Resolved letters close open alerts with the same `$correlationKey` whose `last_event_time` is not newer.
5. Unmapped events → `unmapped_samples` (one row per key-set shape and source, with a count).
6. `NOTIFY alerts_changed`; the caller commits, then the Kafka offsets.

Fingerprint = hash(rule id, `dedup.key` values), default key `$correlationKey`, else `$service` + `$summary`;
`mode: window` adds the clock-aligned window start. The title and body are rendered once from the first event, with
`{{$count}}`, `{{$state}}`, `{{$alertUrl}}` kept for display / send time.

Until A's tables (`processed_events`, `alerts`, …) are migrated, the process waits and does not read Kafka, so
no offsets move past unprocessed events. Tests create the spec §8 shape from `tests/sql/` and the API's fixture.
