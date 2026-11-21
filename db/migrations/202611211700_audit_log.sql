-- Audit log (owner C, spec §5, §7, §8). Append-only record of alert state changes, runbook steps and approvals.
-- Written by the API (ack, resolve, manual steps, approvals), the processor (A) and the executor / scheduler /
-- inbound SMS handler (B), always in the same transaction as the change it describes.

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    -- user id, or a process: 'processor', 'executor', 'scheduler', 'system'
    actor       text        NOT NULL,
    -- e.g. 'alert.created', 'alert.acknowledged', 'alert.resolved', 'step.done', 'approval.approved'
    action      text        NOT NULL,
    -- 'alert', 'runbook_run', 'step_run', 'approval', 'rule', ...
    target_type text        NOT NULL,
    target_id   text        NOT NULL,
    -- free-form: {"via": "sms"}, {"note": "..."}, {"how": "event"}, ...
    details     jsonb       NOT NULL DEFAULT '{}'
);

CREATE INDEX audit_log_target_idx ON audit_log (target_type, target_id, ts);

-- Append-only: rows can be inserted, never changed or deleted.
CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
