-- Alerts (owner A, spec §5, §13). One row per open fingerprint; closed alerts are kept
-- for history. Every update path is owned by the processor (services/processor/).

CREATE TABLE alerts (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fingerprint     text        NOT NULL,
    rule_id         text        NOT NULL,

    -- Exactly one of the two is set. Team rules populate team_id; personal rules
    -- (owner = {"user": "..."}) populate owner_user_id.
    team_id         text,
    owner_user_id   text,

    state           text        NOT NULL DEFAULT 'open'
                                CHECK (state IN ('open', 'acknowledged', 'resolved')),
    severity        text        NOT NULL DEFAULT 'warning'
                                CHECK (severity IN ('info', 'warning', 'error', 'critical')),

    -- `title` and `body` are the rule's presentation templates rendered with the
    -- first event's static fields. Live fields ({{$count}}, {{$state}},
    -- {{$alert_url}}) are preserved as markers and filled in by delivery / UI at
    -- display and send time (contracts/templates.md).
    title           text        NOT NULL,
    body            text        NOT NULL,

    -- First event's Letter.view(): fields + payload + $ reserved fields.
    -- Used by delivery (email/UI) and the runbook executor (contracts/actions.py).
    letter          jsonb       NOT NULL DEFAULT '{}'::jsonb,

    -- Envelope event_id of the event that created this alert. Used as the
    -- dedup_key for notifications of kind = 'alert' (contracts/delivery.md step 3).
    first_event_id  text        NOT NULL,

    -- $correlationKey of the event, if present. Auto-resolve closes every open alert
    -- with the same value when a resolved event arrives (spec §5).
    correlation_key text,

    count           integer     NOT NULL DEFAULT 1 CHECK (count > 0),

    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),

    acknowledged_at timestamptz,
    acknowledged_by text,
    resolved_at     timestamptz,
    resolved_by     text,
    resolved_how    text        CHECK (resolved_how IN ('auto', 'manual', 'runbook')),

    CHECK (team_id IS NOT NULL OR owner_user_id IS NOT NULL),
    -- A row is either resolved (with a timestamp) or it is not.
    CHECK ((state = 'resolved') = (resolved_at IS NOT NULL))
);

-- AGENTS.md invariant: one open alert per fingerprint. The processor's alert store
-- relies on this exact predicate for INSERT ... ON CONFLICT ... DO NOTHING RETURNING id.
CREATE UNIQUE INDEX alerts_fingerprint_open_uq
    ON alerts (fingerprint) WHERE state <> 'resolved';

-- UI feed: alerts for a team, newest first (spec §2.4).
CREATE INDEX alerts_team_last_seen_idx ON alerts (team_id, last_seen DESC);

-- Personal rules feed.
CREATE INDEX alerts_owner_last_seen_idx ON alerts (owner_user_id, last_seen DESC)
    WHERE owner_user_id IS NOT NULL;

-- Auto-resolve by correlationKey (spec §5).
CREATE INDEX alerts_correlation_key_open_idx ON alerts (correlation_key)
    WHERE correlation_key IS NOT NULL AND state <> 'resolved';

-- autoResolveAfter sweep: alerts whose last_seen is past the rule's window.
CREATE INDEX alerts_state_last_seen_idx ON alerts (state, last_seen)
    WHERE state <> 'resolved';

COMMENT ON TABLE  alerts                 IS 'One row per alert; kept after resolution for history (spec §5, §13).';
COMMENT ON COLUMN alerts.fingerprint     IS 'Stable hash of (rule_id, dedup fields). Unique among rows with state <> ''resolved''.';
COMMENT ON COLUMN alerts.first_event_id  IS 'Envelope event_id of the first event; dedup_key for kind = ''alert'' notifications.';
COMMENT ON COLUMN alerts.letter          IS 'First event Letter.view() - fields + payload + $ reserved.';
COMMENT ON COLUMN alerts.title           IS 'Rule presentation rendered with the first event (static fields); $live markers preserved.';