# Executor + scheduler (owner B, spec §5, §7)

`python -m services.executor`: one process runs both the runbook executor loop and the scheduler jobs. Scale by
running more instances (step runs are claimed with `SKIP LOCKED` + lease; scheduler jobs are idempotent SQL).

| Module | What |
| --- | --- |
| `engine.py` | run state machine; API entry points `start_run`, `run_step`, `mark_done`, `approve`, `reject`, `cancel_run` |
| `scheduler.py` | every 10 s: resolve silent alerts (`autoResolveAfter`), delete `processed_events` older than 24 h |
| `alerts.py` | what the executor reads / writes on `alerts` (A's table, spec §8 columns) |
| `templating.py` | stand-in renderer for step params until the shared templater lands |
| `actions/` | action registry; built-in actions arrive on `feat/actions-approvals` |

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
