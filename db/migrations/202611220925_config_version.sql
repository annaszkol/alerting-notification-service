-- Config version (owner A, spec §5, AGENTS.md "One transaction per processor batch").
-- Single row, monotonic counter. Every change to rules / schemas / sources bumps it
-- and sends a NOTIFY on `config_changed`.
--
-- The processor LISTENs on `config_changed` AND polls this counter every 5s as a
-- safety net (a missed NOTIFY from a crashed listener must not leave stale rules).

CREATE TABLE config_version (
    id      integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version bigint  NOT NULL DEFAULT 0
);

INSERT INTO config_version (id, version) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;


-- One function, statement-level trigger on each config table. Statement level so
-- one bulk INSERT of 100 rules bumps the version once, not 100 times.
CREATE OR REPLACE FUNCTION bump_config_version() RETURNS trigger AS $$
DECLARE
    v bigint;
BEGIN
    UPDATE config_version SET version = version + 1 WHERE id = 1
    RETURNING version INTO v;
    PERFORM pg_notify('config_changed', v::text);
    RETURN NULL;  -- result ignored for statement-level triggers
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION bump_config_version() IS
    'Bump config_version and NOTIFY config_changed. Fires once per statement on the config tables.';


CREATE TRIGGER rules_config_version
    AFTER INSERT OR UPDATE OR DELETE ON rules
    FOR EACH STATEMENT EXECUTE FUNCTION bump_config_version();

CREATE TRIGGER schemas_config_version
    AFTER INSERT OR UPDATE OR DELETE ON schemas
    FOR EACH STATEMENT EXECUTE FUNCTION bump_config_version();

CREATE TRIGGER sources_config_version
    AFTER INSERT OR UPDATE OR DELETE ON sources
    FOR EACH STATEMENT EXECUTE FUNCTION bump_config_version();

COMMENT ON TABLE config_version IS 'Monotonic config counter. Bumped by triggers on rules/schemas/sources.';