-- Log tailer offsets (owner A, spec §2.1). One row per (source, path). The tailer
-- updates `offset` after writing the batch to events.raw; `inode` catches file
-- rotation (same path, new inode -> reset offset to 0).

CREATE TABLE tailer_offsets (
    source_id   text        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path        text        NOT NULL,
    inode       bigint,
    offset      bigint      NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, path)
);

COMMENT ON COLUMN tailer_offsets.inode IS 'File inode; a change means the file was rotated and offset resets.';