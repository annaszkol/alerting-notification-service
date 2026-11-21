# Runbooks and actions contract (owner B, spec §7)

Canonical model: [`runbook.schema.json`](runbook.schema.json). Action interface: [`actions.py`](actions.py).
The importer (A, `feat/runbook-import`) produces documents valid against the schema; the executor (B) runs them;
the UI (C) displays and edits them.

## Canonical runbook

```json
{
  "id": "settlement-timeout",
  "format": "yaml",
  "match": { "all": [ { "type": "WORKFLOW_FAILED" }, { "error.code": "TIMEOUT" } ] },
  "mode": "manual",
  "steps": [
    { "id": "check-counterparty", "kind": "action", "title": "Is counterparty-service healthy?",
      "action": "http_get", "params": { "url": "http://counterparty-service/health" }, "approval": "none" },
    { "id": "retry", "kind": "action", "action": "retry_workflow", "params": { "workflowId": "{{workflowId}}" },
      "approval": "none", "attempts": 2, "resolves": true, "on_failure": "escalate" },
    { "id": "escalate", "kind": "action", "action": "escalate", "params": { "team": "counterparty-support" },
      "approval": "required" }
  ]
}
```

- Step kinds: `text` (manual, ticked off in the UI, records who and when), `action`, `wait` (`duration`: `30s`, `2m`, `1h`).
- `params` and `text` are templates (spec §3), rendered with the alert's first event when the step runs.
- `approval`: `none` | `required`, plus `approval_role` (default `manager`).
  **Effective approval = max(step.approval, action.min_approval).** A runbook can raise it, never lower it.
- `attempts` (default 1), `on_failure` (step id; omitted = record the failure and continue),
  `resolves` (success resolves the alert), `only_if_open` (skip when the alert is resolved).
- `needs_review: true` marks a step the importer could not fully understand (unknown field or phrase).
- Actions run only when the runbook has been approved by a manager (`runbooks.approved_by` is set).
  Before that the runbook is text to read.

## Action interface

```python
class Action(Protocol):
    name: str
    params_schema: dict  # JSON Schema; the UI builds the form from it
    min_approval: Approval  # none | required

    async def execute(self, params: dict, ctx: ActionContext) -> ActionResult: ...
```

- `execute` gets params after template substitution and schema validation. It runs one attempt; the executor
  handles `attempts`, `on_failure`, `resolves`, `only_if_open`, approvals and recording.
- Expected failures return `ActionResult(ok=False, error=...)`; an exception counts as a failed attempt.
- Must be safe to repeat: after a crash the executor may run the same attempt again.
- HTTP-based actions call only hosts in `ctx.http_allowlist` (`ACTION_HTTP_ALLOWLIST` env), checked after
  substitution. A host outside the list is a failed attempt, not a request.

## Action registry (v1)

| Action | Params | Min approval | Success |
| --- | --- | --- | --- |
| `http_get` | `url` | none | 2xx |
| `retry_workflow` | `workflowId` | none | mock workflow engine accepts the retry |
| `restart_service` | `service` | required | mock orchestrator returns 2xx |
| `webhook` | `url`, `body` (object) | required | 2xx from a POST |
| `open_ticket` | `title`, `description` | none | ticket id returned (in `output.ticketId`) |
| `escalate` | `team` | none | team added to the alert, its on-call person notified |
| `notify` | `to` (recipient, as in `route.schema.json`), `message` | none | notifications created |

## Execution state (for the UI and the timeline)

- `runbook_runs.status`: `running`, `waiting_approval`, `succeeded`, `failed`, `rejected`, `cancelled`.
- `step_runs.status`: `pending`, `scheduled` (waiting until `next_run_at`), `running`, `waiting_approval`,
  `succeeded`, `failed`, `skipped`, `rejected`, `done` (manual step ticked off).
- Every step run records: who started it, who approved it, rendered params, result (`output` / `error`),
  attempt number and timestamps.
- A step needing approval creates one `approvals` row with its own code and sends `kind = approval`
  notifications to the people with the required role. Rejection ends the run (`rejected`).
