-- Work time start / stop (spec §6 "Czas pracy"). Owner: B (delivery reads it in plan_notifications), C (API writes it).

-- Opt-in: only people who switched it on get notifications only while at work.
CREATE TABLE work_tracking (
    user_id    text        PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    enabled    boolean     NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE work_sessions (
    id         bigserial   PRIMARY KEY,
    user_id    text        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at   timestamptz,
    ended_how  text        CHECK (ended_how IN ('user', 'auto')),
    CHECK ((ended_at IS NULL) = (ended_how IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- at most one open session per person
CREATE UNIQUE INDEX work_sessions_one_open ON work_sessions (user_id) WHERE ended_at IS NULL;
CREATE INDEX work_sessions_user_started ON work_sessions (user_id, started_at DESC);
