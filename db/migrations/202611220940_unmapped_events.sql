-- Unmapped event samples (owner A, spec §2.1). When no schema in `schemas` matches
-- an incoming payload, the processor stores a small sample here so C can show a
-- "new schema detected" panel (spec §2.1) and an operator can add the mapping.
--
-- Retention: 7 days, cleaned up by the processor scheduler (spec §2.1 default).
-- The sample is stored raw so it can be replayed once a schema exists.

CREATE TABLE unmapped_events (
    id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   text        NOT NULL,
    event_id    text        NOT NULL,
    received_at timestamptz NOT NULL,
    -- 'no_schema_matched' | 'schema_error'. Kept as text, not enum, so new reasons
    -- do not need a migration.
    reason      text        NOT NULL,
    sample      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX unmapped_events_source_idx ON unmapped_events (source_id, created_at DESC);
CREATE INDEX unmapped_events_created_at_idx ON unmapped_events (created_at);

COMMENT ON TABLE unmapped_events IS 'Samples of events no schema matched; drives the "new schema" UI (spec §2.1).';