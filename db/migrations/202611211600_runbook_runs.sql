-- Runbook execution state (owner B, spec §7 Executor, §8). Statuses: contracts/runbooks.md.
--
-- A run stores a snapshot of the canonical runbook (contracts/runbook.schema.json) taken at start, so editing or
-- re-approving the runbook never changes a run in progress. alert_id has no FK yet: `alerts` belongs to A.

CREATE TABLE runbook_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_id    bigint      NOT NULL,
    runbook_id  text        NOT NULL,
    runbook     jsonb       NOT NULL,
    mode        text        NOT NULL DEFAULT 'manual' CHECK (mode IN ('manual', 'auto')),
    status      text        NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'waiting_approval', 'succeeded', 'failed', 'rejected', 'cancelled')),
    started_by  text REFERENCES users (id) ON DELETE SET NULL,  -- NULL: started automatically (mode auto)
    reached_end boolean     NOT NULL DEFAULT false,  -- walked past the last step; finishes once manual steps are done
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE INDEX runbook_runs_alert_idx ON runbook_runs (alert_id);

CREATE TABLE step_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       bigint      NOT NULL REFERENCES runbook_runs (id) ON DELETE CASCADE,
    seq          integer     NOT NULL,          -- execution order within the run (a step can run twice via on_failure)
    step_id      text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('text', 'action', 'wait')),
    status       text        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'scheduled', 'running', 'waiting_approval',
                                               'succeeded', 'failed', 'skipped', 'rejected', 'done')),
    attempt      integer     NOT NULL DEFAULT 0,  -- executions of an action step so far
    -- Executor picks up rows with next_run_at <= now(). NULL = waits for a person (manual step, "run" button).
    next_run_at  timestamptz,
    lease_until  timestamptz,
    lease_token  uuid,
    params       jsonb,                         -- rendered parameters of the last attempt
    result       jsonb,                         -- action output of the last attempt
    error        text,
    started_by   text REFERENCES users (id) ON DELETE SET NULL,  -- who pressed "run" (manual mode)
    approved_by  text REFERENCES users (id) ON DELETE SET NULL,
    executed_by  text REFERENCES users (id) ON DELETE SET NULL,  -- who ticked a manual (text) step
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    UNIQUE (run_id, seq)
);

-- Executor claim: due rows and rows whose lease expired.
CREATE INDEX step_runs_due_idx ON step_runs (next_run_at) WHERE status IN ('pending', 'scheduled', 'running');
CREATE INDEX step_runs_run_idx ON step_runs (run_id);
