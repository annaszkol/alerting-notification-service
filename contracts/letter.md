# Event envelope, Letter and path syntax (owner A, spec §3, §5)

Code: [`event.py`](event.py), [`letter.py`](letter.py).

## Event envelope

Every adapter produces one `EventEnvelope` per incoming message and writes it to
Kafka topic `events.raw` (12 partitions, key = `event_id`). The processor never reads
provider-specific payloads - only envelopes.

| Field            | Type         | Meaning                                                                              |
| ---------------- | ------------ | ------------------------------------------------------------------------------------ |
| `event_id`     | `str`      | Assigned at ingest, stable across replays. Idempotency key for `processed_events`. |
| `source_id`    | `str`      | The adapter's source id (a row in `sources`).                                      |
| `received_at`  | `datetime` | Ingest timestamp (UTC).                                                              |
| `content_type` | `str`      | Hint for the parser: `application/json`, `text/logfmt`, `text/plain`.          |
| `payload`      | `Any`      | Decoded JSON when applicable, otherwise the raw string.                              |

Replay of the same `event_id` must be a no-op at the processor: the pipeline inserts
into `processed_events` first and skips already-processed rows. This is what makes
`INSERT ... ON CONFLICT DO NOTHING` in the alert store correct in the presence of
Kafka replays.

## Letter

A `Letter` is one event after schema mapping. One envelope can produce several Letters
when `schema.splitOn` names a list in the payload (one Letter per element, with
`$split_index` distinguishing them).

| Field           | Type               | Meaning                                                                                          |
| --------------- | ------------------ | ------------------------------------------------------------------------------------------------ |
| `fields`      | `dict[str, Any]` | Everything a condition or template can address with `$<name>`. Filled from `schema.mapping`. |
| `raw`         | `Any`            | The parsed payload, addressable as `payload.<path>`.                                           |
| `source_id`   | `str`            | From the envelope.                                                                               |
| `event_id`    | `str`            | From the envelope.                                                                               |
| `received_at` | `str`            | ISO-8601 UTC.                                                                                    |
| `schema`      | `str`            | The id of the schema that produced this Letter.                                                  |
| `split_index` | `int \| None`     | Zero-based index when `schema.splitOn` produced multiple Letters.                              |

`Letter.view()` returns the flat mapping conditions and templates evaluate against:

```python
{
    **fields,
    "payload": raw,
    "$schema": schema,
    "$source_id": source_id,
    "$event_id": event_id,
    "$received_at": received_at,
    "$split_index": split_index,  # only when the event was split
}
```

This is the *only* structure a condition or a template sees. A new source, schema or
channel produces a new `fields` dict; the view and the evaluation rules never change.

## Path syntax

Used identically in conditions (`{"field": <path>, ...}`) and templates (`{{path}}`).

| Syntax                  | Meaning                                                                                                                |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `a.b`                 | Nested access. `a` and `b` are dict keys.                                                                          |
| `a.0` or `a[0]`     | Numeric segment indexes an array. Both forms are accepted.                                                             |
| `labels["a.b"]`       | Brackets for a key that contains `.` (or any character not allowed in a bare segment). Quotes may be `"` or `'`. |
| `payload.meta.schema` | `payload` is `Letter.raw`. The rest is the usual nested access.                                                    |
| `$schema`             | Reserved field on the view (see above).                                                                                |
| `` (empty)              | Invalid. Evaluator returns `None` and the comparison is `false`.                                                   |

Rules:

- Reading a path that does not exist returns `None` (not `KeyError`). The comparison
  is then `false`, except for `exists: false` and `not`.
- Reading a path that hits a scalar before the end returns `None`.
- Both `a.0` and `a[0]` are accepted; `a.0` is preferred in the spec examples.
- The path is validated once when the rule is saved; the processor never re-parses it
  at event time.

## Examples from Appendix A

Workflow failure, as a Letter (`schema = "workflow.failed.v1"`):

```python
Letter(
    fields={
        "type": "WORKFLOW_FAILED",
        "workflowId": "WF-20261023-00042",
        "workflowName": "order-settlement",
        "step": "validate-counterparty",
        "severity": "ERROR",
        "environment": "prod",
        "error_code": "TIMEOUT",
        "error_message": "Counterparty service did not respond in 5000 ms",
        "timestamp": "2026-10-23T09:14:02.331Z",
    },
    raw={
        "type": "WORKFLOW_FAILED",
        "workflowid": "WF-20261023-00042",
        "workflowName": "order-settlement",
        "step": "validate-counterparty",
        "severity": "ERROR",
        "environment": "prod",
        "timestamp": "2026-10-23T09:14:02.331Z",
        "error": {
            "code": "TIMEOUT",
            "message": "Counterparty service did not respond in 5000 ms",
        },
    },
    source_id="workflows.kafka",
    event_id="01H8...",
    received_at="2026-10-23T09:14:02.400Z",
    schema="workflow.failed.v1",
)
```

The `schema.mapping` decides whether `error.code` is exposed as `error_code` (flat) or
kept nested; both are valid. Conditions and templates see whatever the mapping
produces, plus `payload` with the original.
