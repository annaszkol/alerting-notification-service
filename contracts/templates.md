
# Template syntax contract (owner A, spec §3)

Code: [`template.py`](template.py). Rendering lives in `libs/conditions/template.py`.

Templates are the values of `rule.presentation.title`, `rule.presentation.body`,
`runbook.step.text`, and every `runbook.step.params` field. They are also rendered by
the delivery layer at send time (`contracts/delivery.md`). They never execute code.

## Syntax

```
{{ path }}
{{ $live }}
```

- Whitespace inside the braces is ignored: `{{  workflowId  }}` == `{{workflowId}}`.
- Paths use the same syntax as conditions (see `letter.md`).
- Missing path -> empty string. `None` -> empty string. Lists -> comma-separated.
  Anything else -> `str(value)`.
- Nested braces (`{{a{{b}}}}`) are not supported; `PLACEHOLDER` matches the shortest
  non-empty content between `{{` and `}}` that contains no braces.

## Live vs static fields

| Rendered | When                                                         | Which fields                                                                              |
| -------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Static   | Once, when the alert is created (first event).               | Everything from `Letter.view()`: `workflowId`, `payload.notional`, `$schema`, ... |
| Live     | Every time the alert is displayed or a notification is sent. | `$count`, `$state`, `$alert_url` (delivery layer adds them to the view).            |

Static rendering happens inside the processor's transaction, and the result is stored
on the alert. Live rendering happens on the delivery worker, so `{{$count}}` in a
message body reflects the current deduplicated count, not the count at first event.

Convention:

- Rule presentation templates may use both static and live fields.
- Runbook step `params` and `text` use **static** fields only. A runbook is rendered
  when the step runs, so live delivery fields are already available via
  `AlertContext` and must be referenced there (`ctx.alert.state`), not through
  `{{$state}}`. This keeps runbook templates deterministic across replays of the same
  step.
- `MAX_RENDERED_LENGTH` caps a single rendered template at 64 KiB. Over the cap, the
  renderer truncates and logs; it does not raise.

## Examples

Rule presentation (static):

```json
{
  "title": "Rejected {{payload.currency}} {{payload.notional}} - {{payload.reason}}",
  "body": "Trade {{tradeld}} on desk {{payload.desk}} rejected: {{payload.reason}}"
}
```

For a Rates team alert, this renders at first event to:

```
Rejected EUR 25000000 - LIMIT_BREACH
```

Rule presentation (static + live):

```json
{
  "title": "[{{$count}}] {{workflowName}} failed at {{step}}",
  "body": "Error {{error_code}}: {{error_message}}. Current state: {{$state}}."
}
```

At display time with `count = 5` and `state = "acknowledged"`:

```
[5] order-settlement failed at validate-counterparty
Error TIMEOUT: Counterparty service did not respond in 5000 ms. Current state: acknowledged.
```

Runbook step (static, runbook executes at step time so `{{$count}}` is not allowed
here - use `AlertContext` instead):

```json
{
  "action": "retry_workflow",
  "params": { "workflowId": "{{workflowId}}" }
}
```
