# Executor + scheduler (owner B, spec §5, §7)

`python -m services.executor`: one process runs both the runbook executor loop and the scheduler jobs. Scale by
running more instances (step runs are claimed with `SKIP LOCKED` + lease; scheduler jobs are idempotent SQL).

| Module | What |
| --- | --- |
| `engine.py` | run state machine; API entry points `start_run`, `run_step`, `mark_done`, `approve`, `reject`, `cancel_run` |
| `scheduler.py` | every 10 s: resolve silent alerts (`autoResolveAfter`), delete `processed_events` older than 24 h |
| `runbooks.py` | runbooks / runs / approvals as the API shows them: validation against the contract, rendered steps, run and approval views |
| `history.py` | alert history: runbook runs, steps, approvals, automatic resolves → `audit_log` + `NOTIFY` |
| `alerts.py` | what the executor reads / writes on `alerts` (A's table, spec §8 columns) |
| `templating.py` | stand-in renderer for step params until the shared templater lands |
| `actions/builtin.py` | built-in actions (`build_registry`) |
| `approvals.py` | approval requests, decisions (UI / SMS code / email link), signed links |

## How a run works

- `start_run(conn, alert_id, runbook, now, started_by, mode)` stores a **snapshot** of the canonical runbook
  (contracts/runbook.schema.json), so later edits never change a run in progress. The caller (API) checks that the
  runbook is manager-approved and the user may start it.
- Steps are visited in order; each visit is a `step_runs` row (`seq`):
  - `text`: manual, `pending` until someone ticks it (`mark_done` stores who and when). Does not block the run.
  - `wait`: `scheduled` until `now + duration`.
  - `action`: mode `auto` → due at once; mode `manual` → waits for the "run" button (`run_step`). If its effective
    approval is `required` (max of the step's and the action's minimum) it goes `waiting_approval` and the approval
    request hook fires; `approve` lets it run, `reject` ends the run.
  - up to `attempts` executions, 5 s apart; final failure → `on_failure` step, or record it and continue.
  - `resolves: true` + success → alert resolved (`resolved_how = runbook`) and the run ends.
  - `only_if_open: true` → skipped when the alert is already resolved.
- A run finishes once it walked past its last step and no step is open (manual checklist items included). It is
  `failed` if its last automatic step failed. At most 100 step runs per run (protects against `on_failure` loops).
- The executor runs the action outside any transaction and stores the result only while its lease token is still
  on the row, so an executor that crashed or stalled never overwrites the one that took over. A restart continues
  where it stopped.

Entry points take the caller's connection and run inside its transaction; the executor's own connection must be in
autocommit mode.

## Actions (`actions/builtin.py`, spec §7)

| Action | Params | Min approval | Calls |
| --- | --- | --- | --- |
| `http_get` | `url` | none | GET; host must be on `ACTION_HTTP_ALLOWLIST` (checked after templating) |
| `webhook` | `url`, `body` | required | POST to an allowlisted host, `Idempotency-Key: step-run-<id>-<attempt>` |
| `retry_workflow` | `workflowId` | none | `POST $WORKFLOW_ENGINE_URL/workflows/<id>/retry` |
| `restart_service` | `service` | required | `POST $ORCHESTRATOR_URL/services/<name>/restart` |
| `open_ticket` | `title`, `description` | none | `POST $TICKETS_URL/tickets` (same `externalId` → same ticket) → `output.ticketId` |
| `escalate` | `team` | none | adds the team to the alert (`alert_escalations`) and notifies its on-call person |
| `notify` | `to`, `message`, `channels?` | none | notifications with a custom text (`notifications.message`) |

Values from events are URL-encoded in paths, so `../` in an event cannot reach another endpoint. Internal actions key
their notifications on the step run, so repeating them after a crash sends nothing new. New action: a class with
`name`, `params_schema`, `min_approval`, `execute(params, ctx)`, added to `build_registry`.

Demo targets: `mocks/action_targets` (compose `action-targets`, http://localhost:8091, host names
`counterparty-service`, `workflow-engine`, `orchestrator`, `tickets`, `webhook-receiver`). The page / `POST /api/fail`
makes a target fail the next N calls or always (spec §13 test 12: "mock set to fail").

## Approvals (`approvals.py`, spec §6, §7, §11)

- A step whose effective approval is `required` creates one `approvals` row with its own 6-character code and sends
  `kind = approval` notifications (SMS + email) to the people with the required role (`approval_role`, default
  `manager`) in the runbook owner's team (else the alert owner's team).
- `decide_by_code(conn, code, phone, decision, now)`: `APPROVE <code>` / `REJECT <code>` by SMS; only from the phone
  of someone with that role; a code decides exactly one request. The inbound SMS endpoint (`feat/inbound-sms-codes`)
  calls it.
- `decide_approval(conn, approval_id, actor, decision, via)` (agreement 4 in docs/plans/c-api-web.md): UI decisions;
  `decision` is `approve` / `reject`; checks the role; a decided request cannot be decided again. Results map to
  HTTP: `not_allowed` → 403, `unknown_code` → 404, `already_decided` → 409.
- Email: one link per approver, `PUBLIC_BASE_URL/approvals/<token>`. `sign_approval_token` / `verify_approval_token`
  (agreement 5): HMAC-SHA256 with `APPROVAL_LINK_SECRET`, names the approval **and the person it was emailed to**,
  24 h. `decide_by_token(conn, token, decision)` backs `POST /approvals/by-token/{token}` (no login): the addressee's
  role is still checked, `invalid_token` for a bad or expired token.
- The approval SMS starts with the codes, so cutting it to 160 characters never loses them.

## History ("who did what", spec §7)

Everything the executor, scheduler and approvals do is written to C's `audit_log` as `target_type = 'alert'` with the
alert id, in the same transaction as the change, plus `NOTIFY alerts_changed`. The alert timeline
(`GET /api/alerts/{id}/timeline`) shows them as `kind = step` / `approval`.

| Action | Actor | Details |
| --- | --- | --- |
| `run.started`, `run.cancelled` | user, or `executor` (mode auto) / `system` | run, runbook, mode |
| `step.started` ("run" button), `step.done` (manual step ticked) | user | run, step, stepRun |
| `step.succeeded`, `step.attempt_failed`, `step.failed`, `step.skipped` | `executor` | run, step, stepRun, attempt, error |
| `approval.requested` | `executor` | step, approval, team, role |
| `approval.approved`, `approval.rejected` | user | step, approval, via (ui / sms / email) |
| `alert.resolved` | user who started the run / `executor` (how: runbook), `scheduler` (how: silence) | how |
