-- Runbooks (owner B, spec §7, §8; docs/plans/c-api-web.md lists the table as B's).
-- `original` keeps the imported text; `canonical` is the document valid against contracts/runbook.schema.json.
-- Actions run only from approved runbooks: any edit clears approved_by / approved_at (contracts/openapi.yaml).
CREATE TABLE runbooks (
    id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9_.-]*$'),
    owner_team  text        NOT NULL REFERENCES teams (id),
    title       text        NOT NULL,
    format      text        NOT NULL CHECK (format IN ('yaml', 'markdown', 'checklist', 'text', 'link')),
    original    text        NOT NULL DEFAULT '',
    canonical   jsonb       NOT NULL,
    mode        text        NOT NULL DEFAULT 'manual' CHECK (mode IN ('manual', 'auto')),
    approved_by text REFERENCES users (id) ON DELETE SET NULL,
    approved_at timestamptz,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX runbooks_owner_idx ON runbooks (owner_team);

-- Optional note when a manual step is ticked off (`POST /runbook-runs/{id}/steps/{stepId}/done`).
ALTER TABLE step_runs ADD COLUMN note text;
