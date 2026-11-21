-- On-call rotations and channel preferences (owner B, spec §6 "Dyżury", §8).

-- One rotation per team. rotation[1] is on call from starts_on at handoff_time (in `timezone`), then the
-- next person every shift_length, cycling.
CREATE TABLE oncall_schedules (
    team_id      text PRIMARY KEY REFERENCES teams (id) ON DELETE CASCADE,
    rotation     text[]      NOT NULL CHECK (cardinality(rotation) > 0),
    shift_length interval    NOT NULL DEFAULT interval '1 day' CHECK (shift_length > interval '0'),
    handoff_time time        NOT NULL DEFAULT '09:00',
    timezone     text        NOT NULL DEFAULT 'UTC',
    starts_on    date        NOT NULL DEFAULT current_date,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Channel preferences of a person, or the defaults of a team. The person's own row wins over team defaults.
-- by_severity: {"info": [...], "warning": ..., "error": ..., "critical": ...}; each value is a channel list or
--   a schedule {"schedule": [{"between": "09:00-17:00", "days": [1,2,3,4,5], "use": ["email"]}], "otherwise": ["ui"]}
--   (same format as rule channels, contracts/route.schema.json), evaluated in the person's time zone.
--   A missing level uses the nearest lower level that is set.
-- limits: throttling per channel, e.g. {"sms": {"max": 5, "window": "10m"}} (used by feat/throttling).
CREATE TABLE channel_preferences (
    owner_type  text        NOT NULL CHECK (owner_type IN ('user', 'team')),
    owner_id    text        NOT NULL,
    by_severity jsonb       NOT NULL DEFAULT '{}',
    limits      jsonb       NOT NULL DEFAULT '{}',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_type, owner_id)
);
