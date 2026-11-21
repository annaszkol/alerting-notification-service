-- Notification outbox (owner B, spec §6, §8). Rows are inserted by plan_notifications inside the
-- processor's batch transaction and claimed by delivery workers with FOR UPDATE SKIP LOCKED.
--
-- alert_id / user_id have no foreign keys yet: `alerts` (A) and `users` (seed) do not exist on main.
-- FKs are added in a later migration once those tables land.

CREATE TABLE notifications (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_id        bigint      NOT NULL,
    user_id         text        NOT NULL,
    channel         text        NOT NULL CHECK (channel IN ('ui', 'email', 'sms', 'phone')),
    kind            text        NOT NULL DEFAULT 'alert' CHECK (kind IN ('alert', 'limit', 'approval')),
    -- first event id of the alert (kind=alert), window start (kind=limit), approval id (kind=approval)
    dedup_key       text        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'sending', 'sent', 'dead', 'held')),
    attempts        integer     NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_until     timestamptz,
    last_error      text,
    reply_code      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    -- one person, one channel, one event (spec §6 step 3)
    CONSTRAINT notifications_user_channel_dedup_key UNIQUE (user_id, channel, dedup_key)
);

-- Delivery claim query: pending rows due now, and sending rows with an expired lease.
CREATE INDEX notifications_claim_idx ON notifications (status, next_attempt_at);
CREATE INDEX notifications_alert_idx ON notifications (alert_id);
