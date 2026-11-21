-- Runtime configuration for the hot path (owner A reads, C writes via its REST API).
-- Every table here is watched by the config_version triggers (next migration), so
-- any INSERT / UPDATE / DELETE bumps the version and the processor reloads.
--
-- Shapes come from contracts/condition.py (AST), contracts/rule.py (rule shape,
-- to be added in a later contracts PR), and contracts/delivery.RuleForPlanning.

CREATE TABLE rules (
    id           text        PRIMARY KEY,                     -- slug from contracts
    name         text        NOT NULL,
    team_id      text        NOT NULL,                        -- owning team (spec §4)
    enabled      boolean     NOT NULL DEFAULT true,
    -- Condition AST (contracts/condition.py, contracts/conditions.md).
    condition    jsonb       NOT NULL,
    -- Per-rule presentation (spec §2.2): {title, body, color, severity, fields?}.
    presentation jsonb       NOT NULL,
    -- Recipients and channels (contracts/route.schema.json). Empty = UI feed only.
    route        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- Runbooks attached to this rule (contracts/runbook.schema.json can also
    -- attach a runbook via runbook.match).
    runbook_ids  text[]      NOT NULL DEFAULT ARRAY[]::text[],
    -- {correlationFields: [...], default: bool}. Empty = use processor default.
    dedup        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- {window: "5m", max: 1} or null. Throttling is per (user, channel) in delivery.
    throttle     jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX rules_team_idx ON rules (team_id) WHERE enabled;


CREATE TABLE schemas (
    id           text        PRIMARY KEY,                     -- e.g. "workflow.failed.v1"
    source_id    text,                                        -- null = any source
    priority     integer     NOT NULL DEFAULT 0,              -- higher first (spec §2.1)
    -- Schema detection AST (same grammar as rule conditions).
    detect       jsonb       NOT NULL,
    parser       text        NOT NULL CHECK (parser IN ('json', 'logfmt', 'regex')),
    -- {"pattern": "..."} for regex, {} for json/logfmt.
    parser_config jsonb      NOT NULL DEFAULT '{}'::jsonb,
    -- {"field": "path"} or {"field": {"const": value}}. Reserved fields added by
    -- the processor: $schema, $source_id, $event_id, $received_at.
    mapping      jsonb       NOT NULL,
    -- Payload path to a list; each element becomes a separate Letter with $split_index.
    split_on     text,
    enabled      boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- detect order: highest priority first, then id for determinism.
CREATE INDEX schemas_detect_idx ON schemas (priority DESC, id) WHERE enabled;


CREATE TABLE sources (
    id           text        PRIMARY KEY,                     -- e.g. "workflows.kafka"
    kind         text        NOT NULL CHECK (kind IN ('http', 'kafka', 'tailer')),
    -- Adapter-specific config. Example:
    --   http:    {}
    --   kafka:   {"topic": "...", "group": "..."}
    --   tailer:  {"path": "/var/log/x.log"}
    config       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    enabled      boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);