-- Approvals of runbook steps, escalations, free-text notifications (owner B, spec §6, §7, §8).

-- One row per approval request. The code is what people send by SMS (APPROVE <code> / REJECT <code>); it identifies
-- exactly one request, so a code can never approve another step (spec §13 test 12).
CREATE TABLE approvals (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    step_run_id   bigint      NOT NULL REFERENCES step_runs (id) ON DELETE CASCADE,
    team_id       text        NOT NULL,      -- whose role is required (runbook owner team, else alert owner team)
    required_role text        NOT NULL CHECK (required_role IN ('manager', 'member')),
    code          text        NOT NULL,
    decision      text        CHECK (decision IN ('approved', 'rejected')),
    decided_by    text REFERENCES users (id) ON DELETE SET NULL,
    via           text        CHECK (via IN ('ui', 'sms', 'email')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz
);

-- Codes are unique among open requests; one open request per step run.
CREATE UNIQUE INDEX approvals_open_code_key ON approvals (code) WHERE decision IS NULL;
CREATE UNIQUE INDEX approvals_open_step_key ON approvals (step_run_id) WHERE decision IS NULL;

-- `escalate` action: a team added to an alert (spec §7). Kept here rather than as a column on A's `alerts`.
CREATE TABLE alert_escalations (
    alert_id    bigint      NOT NULL,
    team_id     text        NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    step_run_id bigint,                      -- the escalate step that added it (informational)
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (alert_id, team_id)
);

-- `notify` action: a custom text instead of the alert title.
ALTER TABLE notifications ADD COLUMN message text;
