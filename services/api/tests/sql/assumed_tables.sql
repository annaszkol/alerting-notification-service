-- Tables the API reads before their owners' migrations are merged (spec §8). TEST FIXTURE ONLY.
-- Column names are the API's assumption; when A's migrations for `rules`, `config_version` and `alerts` land,
-- this file is deleted and the tests run on the real migrations. Differences found then are fixed in the API.

-- A: rules (one JSON document per rule, spec §4). "when" is quoted because WHEN is an SQL keyword.
CREATE TABLE rules (
    id                 text PRIMARY KEY,
    owner              jsonb       NOT NULL,          -- {"team": "rates"} | {"user": "anna"}
    enabled            boolean     NOT NULL DEFAULT true,
    "when"             jsonb       NOT NULL,
    route              jsonb       NOT NULL DEFAULT '[]',
    present            jsonb       NOT NULL DEFAULT '{}',
    dedup              jsonb       NOT NULL DEFAULT '{}',
    auto_resolve_after interval,
    runbook_ids        text[]      NOT NULL DEFAULT '{}',
    updated_by         text        NOT NULL,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- A: one number, bumped on every config change (spec §4, §8).
CREATE TABLE config_version (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version   bigint  NOT NULL
);
INSERT INTO config_version (version) VALUES (1);

-- A: alerts (spec §5, §8).
CREATE TABLE alerts (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id            text,
    owner              jsonb       NOT NULL,
    extra_teams        text[]      NOT NULL DEFAULT '{}',   -- added by the `escalate` action (plan, agreement 8)
    fingerprint        text        NOT NULL,
    correlation_key    text,
    state              text        NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'acknowledged', 'resolved')),
    severity           text        NOT NULL,
    title              text        NOT NULL,
    body               text,
    color              text,
    fields             jsonb       NOT NULL DEFAULT '[]',   -- present.fields paths at creation
    count              integer     NOT NULL DEFAULT 1,
    first_seen         timestamptz NOT NULL DEFAULT now(),
    last_seen          timestamptz NOT NULL DEFAULT now(),
    last_event_time    timestamptz NOT NULL DEFAULT now(),
    first_event        jsonb       NOT NULL,
    last_event         jsonb       NOT NULL,
    auto_resolve_after interval,
    acked_by           text,
    acked_at           timestamptz,
    resolved_by        text,
    resolved_at        timestamptz,
    resolved_how       text CHECK (resolved_how IN ('manual', 'event', 'runbook', 'silence')),
    previous_alert_id  bigint
);
CREATE UNIQUE INDEX alerts_open_fingerprint ON alerts (fingerprint) WHERE state <> 'resolved';
