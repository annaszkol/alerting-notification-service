-- Event idempotency (owner A, AGENTS.md "One transaction per processor batch").
-- The processor inserts every event_id from a batch here first, with
-- INSERT ... ON CONFLICT DO NOTHING, and skips any row that already existed.
-- This is what makes Kafka replays and HTTP retries harmless.

CREATE TABLE processed_events (
    event_id     text        PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

-- Cleanup: the processor (or a maintenance task) deletes rows older than the
-- Kafka retention window (30 days is plenty).
CREATE INDEX processed_events_processed_at_idx ON processed_events (processed_at);

COMMENT ON TABLE processed_events IS 'Event ids the processor has already handled. Replays are no-ops (AGENTS.md invariant).';