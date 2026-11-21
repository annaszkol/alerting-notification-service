-- People and teams (spec §8). Ids are short slugs (`anna`, `rates`) because rules reference them
-- directly (`user: anna`, `team: rates`). Created by B (seed owner); the API (C) reads and edits them.

CREATE TABLE users (
    id         text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_.-]*$'),
    name       text        NOT NULL,
    email      text,
    phone      text,       -- E.164, e.g. +48500100200; inbound SMS codes are matched against it
    timezone   text        NOT NULL DEFAULT 'UTC',  -- IANA name, used for schedules and rotations
    is_admin   boolean     NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX users_phone_key ON users (phone) WHERE phone IS NOT NULL;

CREATE TABLE teams (
    id         text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_.-]*$'),
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE team_members (
    team_id text NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role    text NOT NULL DEFAULT 'member' CHECK (role IN ('manager', 'member')),
    PRIMARY KEY (team_id, user_id)
);

CREATE INDEX team_members_user_idx ON team_members (user_id);

-- notifications.user_id now has a table to point at (alert_id waits for `alerts`, owner A).
ALTER TABLE notifications
    ADD CONSTRAINT notifications_user_fk FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE;
