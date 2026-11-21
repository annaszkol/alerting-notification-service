-- A's tables the processor writes, in the spec §8 shape (TEST FIXTURE ONLY, like services/api/tests/sql).
-- alerts / rules / config_version come from services/api/tests/sql/assumed_tables.sql (the API's assumption);
-- when A's migrations land, both files go and the tests run on the real migrations.
CREATE TABLE processed_events (
    event_id     text PRIMARY KEY,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schema_mappings (
    id          text PRIMARY KEY,
    priority    integer NOT NULL DEFAULT 100,
    source_ids  text[],
    detect      jsonb,
    schema_from text,
    split_on    text,
    fields      jsonb   NOT NULL DEFAULT '{}',
    enabled     boolean NOT NULL DEFAULT true
);

CREATE TABLE sources (
    id               text PRIMARY KEY,
    type             text    NOT NULL,
    config           jsonb   NOT NULL DEFAULT '{}',
    parser           text    NOT NULL DEFAULT 'json',
    default_severity text    NOT NULL DEFAULT 'info',
    enabled          boolean NOT NULL DEFAULT true
);

CREATE TABLE unmapped_samples (
    shape_hash text        NOT NULL,
    source_id  text        NOT NULL,
    sample     jsonb,
    count      integer     NOT NULL DEFAULT 1,
    last_seen  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shape_hash, source_id)
);
